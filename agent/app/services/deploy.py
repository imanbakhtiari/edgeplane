"""Serialized revision activation. Every reload passes through guarded_reload."""

import fcntl
import json
import os
import shutil
from contextlib import contextmanager
from uuid import uuid4
from fastapi import HTTPException
from app.renderers.config import render


class Deployment:
    def __init__(self, settings, adapter):
        self.s, self.system = settings, adapter
        for path in (
            settings.nginx_config_root,
            settings.varnish_config_root,
            settings.state_root,
            settings.log_root,
        ):
            path.mkdir(parents=True, exist_ok=True)
        # varnishd drops privileges before compiling/using VCL. Permit traversal
        # of managed roots without exposing directory listings or TLS material.
        settings.nginx_config_root.chmod(0o711)
        settings.varnish_config_root.chmod(0o711)
        (settings.log_root / "metrics").mkdir(parents=True, exist_ok=True)
        candidate_temp = settings.state_root / "nginx-candidate"
        for name in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
            (candidate_temp / name).mkdir(parents=True, exist_ok=True)
        self.metadata = settings.state_root / "applied.json"
        self.journal = settings.state_root / "activation.json"

    def current(self):
        if self.metadata.exists():
            return json.loads(self.metadata.read_text())
        return {"revision": 0, "hash": None, "vhosts": []}

    def applied_vhosts(self):
        """Return the public NGINX files in the active, atomically applied release."""
        current = self.s.nginx_config_root / "current"
        if not current.exists():
            return []
        release = current.resolve()
        releases = (self.s.nginx_config_root / "releases").resolve()
        if release.parent != releases:
            raise RuntimeError("ACTIVE_RELEASE_OUTSIDE_MANAGED_ROOT")
        result = []
        metadata = self.current()
        for path in sorted((release / "http").glob("*.conf")):
            if path.name == "00-base.conf" or path.is_symlink():
                continue
            content = path.read_text()
            comments = {}
            for line in content.splitlines()[:5]:
                if line.startswith("# ") and ": " in line:
                    key, value = line[2:].split(": ", 1)
                    comments[key.lower()] = value
            vhost_id = comments.get("vhost id")
            if not vhost_id:
                # Compatibility with releases created before friendly filenames.
                vhost_id = path.stem.split("--")[-1]
            result.append({
                "id": vhost_id,
                "name": comments.get("edgeplane vhost", path.stem),
                "domains": [v.strip() for v in comments.get("domains", "").split(",") if v.strip()],
                "filename": path.name,
                "content": content,
                "revision": metadata.get("revision", 0),
                "applied_hash": metadata.get("vhost_hashes", {}).get(vhost_id),
            })
        return result

    @contextmanager
    def lock(self):
        with (self.s.state_root / "activation.lock").open("a") as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(409, detail={"code": "AGENT_BUSY"}) from None
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def save(self, data):
        temp = self.metadata.with_suffix(".tmp")
        with temp.open("w") as file:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.metadata)

    @staticmethod
    def link(target, link):
        temporary = link.with_name(link.name + ".next")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, link)

    async def guarded_reload(self):
        validation = await self.system.run(self.s.nginx_binary, "-t")
        if validation.code:
            return {
                "success": False,
                "validation_failed": True,
                "code": "NGINX_VALIDATION_FAILED",
                **validation.dict(),
            }
        result = await self.system.run(self.s.nginx_binary, "-s", "reload")
        return {"success": result.code == 0, "validation_failed": False, **result.dict()}

    async def prepare(self, bundle):
        release = self.s.nginx_config_root / "releases" / f"{bundle.revision}-{uuid4().hex}"
        release.mkdir(parents=True, mode=0o755)
        release.parent.chmod(0o711)
        release.chmod(0o711)
        try:
            files = await render(bundle, self.s, release)
            for name, content in files.items():
                file = release / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
                if name == "default.vcl":
                    # Only rendered VCL is readable by varnish's unprivileged
                    # compiler; TLS keys stay private in release/tls (0700).
                    file.chmod(0o644)
            for v in bundle.vhosts:
                (self.s.log_root / str(v.id)).mkdir(parents=True, exist_ok=True)
                if v.tls:
                    directory = release / "tls"
                    directory.mkdir(exist_ok=True, mode=0o700)
                    for suffix, content in [("pem", v.tls.certificate), ("key", v.tls.private_key)]:
                        path = directory / f"{v.id}.{suffix}"
                        path.touch(mode=0o600)
                        path.write_text(content)
            return release, files
        except BaseException:
            shutil.rmtree(release)
            raise

    async def validate_candidate(self, release):
        for kind, args in [
            ("VARNISH", (self.s.varnish_binary, "-C", "-f", release / "default.vcl")),
            ("NGINX", (self.s.nginx_binary, "-t", "-c", release / "candidate.conf")),
        ]:
            result = await self.system.run(*args)
            if result.code:
                return {
                    "success": False,
                    "validation_failed": True,
                    "error": kind + "_VALIDATION_FAILED",
                    **result.dict(),
                }
        return {"success": True, "validation_failed": False}

    async def recover(self):
        """Recover an interrupted activation before serving further changes."""
        if not self.journal.exists():
            return
        pending = json.loads(self.journal.read_text())
        committed = self.current()
        if committed.get("release") != pending["candidate"]:
            old = pending.get("previous", {})
            current = self.s.nginx_config_root / "current"
            from pathlib import Path

            self.restore(current, Path(old["release"]) if old.get("release") else None)
            if old.get("vcl"):
                restored = await self.system.run(self.s.varnishadm_binary, "vcl.use", old["vcl"])
                if restored.code:
                    raise RuntimeError("VARNISH_RECOVERY_FAILED")
            checked = await self.guarded_reload()
            if not checked["success"]:
                raise RuntimeError("NGINX_RECOVERY_FAILED")
        self.journal.unlink(missing_ok=True)

    async def apply(self, bundle, validate_only=False):
        with self.lock():
            await self.recover()
            previous = self.current()
            digest = bundle.digest()
            if not validate_only:
                if bundle.revision < previous["revision"] or (
                    bundle.revision == previous["revision"] and digest != previous["hash"]
                ):
                    raise HTTPException(409, detail={"code": "REVISION_CONFLICT"})
                if digest == previous["hash"]:
                    previous["revision"] = bundle.revision
                    # Upgrade legacy state even when the rendered bundle is unchanged.
                    previous["vhosts"] = [str(v.id) for v in bundle.vhosts if v.enabled]
                    previous["vhost_hashes"] = {
                        str(v.id): v.digest() for v in bundle.vhosts if v.enabled
                    }
                    previous["telemetry"] = {
                        str(v.id): {
                            "customer_id": str(v.customer_id) if v.customer_id else "",
                            "mode": v.analytics.mode,
                            "geography": v.analytics.geography,
                        }
                        for v in bundle.vhosts
                        if v.enabled
                    }
                    self.save(previous)
                    return {"success": True, "noop": True, **previous}
            release, files = await self.prepare(bundle)
            result = await self.validate_candidate(release)
            # A candidate can validate while the installed master lacks its
            # stream include. Never claim HTTPS applied in that situation.
            from app.system.adapter import HostSystemAdapter
            if result.get("success") and isinstance(self.system, HostSystemAdapter) and "listen " in files.get("stream/00-tls.conf", ""):
                installed = await self.system.run(self.s.nginx_binary, "-T")
                expected = f"include {self.s.nginx_config_root}/current/stream/*.conf;"
                if installed.code or expected not in installed.stdout:
                    result = {"success": False, "validation_failed": True,
                              "error": "TLS_ROUTER_NOT_INSTALLED",
                              "stderr": "Install the NGINX stream module and managed stream include using service provisioning before syncing TLS routes. Previous configuration remains active."}
            if not result["success"] or validate_only:
                shutil.rmtree(release)
                return {
                    **result,
                    "revision": bundle.revision,
                    "previous_revision": previous["revision"],
                    "preview": files if validate_only else None,
                }
            current = self.s.nginx_config_root / "current"
            old = current.resolve() if current.exists() else None
            vcl = "cdn_" + release.name.replace("-", "_")
            # Load compiled VCL first, without changing active VCL.
            loaded = await self.system.run(self.s.varnishadm_binary, "vcl.load", vcl, release / "default.vcl")
            if loaded.code:
                shutil.rmtree(release)
                return {"success": False, "error": "VARNISH_LOAD_FAILED", **loaded.dict()}
            with self.journal.open("w") as journal:
                json.dump({"previous": previous, "candidate": str(release)}, journal)
                journal.flush()
                os.fsync(journal.fileno())
            self.link(release, current)
            validation = await self.system.run(self.s.nginx_binary, "-t")
            if validation.code:
                self.restore(current, old)
                self.journal.unlink(missing_ok=True)
                return {
                    "success": False,
                    "validation_failed": True,
                    "error": "NGINX_VALIDATION_FAILED",
                    "revision": bundle.revision,
                    "previous_revision": previous["revision"],
                    **validation.dict(),
                }
            activated = await self.system.run(self.s.varnishadm_binary, "vcl.use", vcl)
            if activated.code:
                self.restore(current, old)
                self.journal.unlink(missing_ok=True)
                return {"success": False, "error": "VARNISH_ACTIVATION_FAILED", **activated.dict()}
            result = await self.guarded_reload()
            if not result["success"]:
                self.restore(current, old)
                self.journal.unlink(missing_ok=True)
                if previous.get("vcl"):
                    await self.system.run(self.s.varnishadm_binary, "vcl.use", previous["vcl"])
                return {**result, "revision": bundle.revision, "previous_revision": previous["revision"]}
            data = {
                "revision": bundle.revision,
                "hash": digest,
                "vcl": vcl,
                "release": str(release),
                "vhosts": [str(v.id) for v in bundle.vhosts if v.enabled],
                "vhost_hashes": {str(v.id): v.digest() for v in bundle.vhosts if v.enabled},
                "telemetry": {
                    str(v.id): {
                        "customer_id": str(v.customer_id) if v.customer_id else "",
                        "mode": v.analytics.mode,
                        "geography": v.analytics.geography,
                    }
                    for v in bundle.vhosts
                    if v.enabled
                },
            }
            self.save(data)
            self.journal.unlink(missing_ok=True)
            self.link(release, self.s.varnish_config_root / "current")
            # Keep active and the most recent releases. Never follow untrusted paths.
            releases = sorted(
                (self.s.nginx_config_root / "releases").iterdir(),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in releases[max(self.s.retention, 2) :]:
                if stale != release and stale != old and stale.is_dir() and not stale.is_symlink():
                    shutil.rmtree(stale)
            return {"success": True, **data}

    @staticmethod
    def restore(current, old):
        if old:
            Deployment.link(old, current)
        else:
            current.unlink(missing_ok=True)
