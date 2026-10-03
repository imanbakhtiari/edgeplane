"""Bounded Varnish storage changes; no arbitrary paths or shell arguments."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

DEFAULTS = {"storage": "file", "size_mb": 1024, "log_retention_days": 7}


def validate(config):
    if set(config) - set(DEFAULTS):
        raise ValueError("Unknown Varnish settings")
    value = {**DEFAULTS, **config}
    if value["storage"] not in {"file", "malloc"}:
        raise ValueError("Storage must be file or malloc")
    for key, low, high in [("size_mb", 256, 1048576), ("log_retention_days", 1, 3650)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError("Invalid " + key)
    return value


def unit(config, vcl):
    config = validate(config)
    storage = "file,/var/lib/cdn-agent/cache" if config["storage"] == "file" else "malloc"
    return ("[Service]\nExecStart=\nExecStart=/usr/sbin/varnishd -F -a 127.0.0.1:6081 "
            "-T 127.0.0.1:6082 -S /etc/varnish/secret "
            f"-f {vcl} -s {storage},{config['size_mb'] * 1024 * 1024}\n")


def logrotate(config):
    days = validate(config)["log_retention_days"]
    return ("/var/log/nginx/cdn/*/*.log {\n    daily\n    maxsize 256M\n"
            f"    rotate {days}\n    maxage {days}\n"
            "    missingok\n    notifempty\n    compress\n    delaycompress\n"
            "    sharedscripts\n    postrotate\n        /usr/sbin/nginx -s reopen\n"
            "    endscript\n}\n")


def atomic_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        stream.write(value)
        temporary = stream.name
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def apply(config, root=Path("/"), run=subprocess.run):
    config = validate(config)
    root = Path(root)
    target = root / "etc/systemd/system/varnish.service.d/cdn.conf"
    rotation = root / "etc/logrotate.d/cdn-agent"
    marker = root / "var/lib/cdn-agent/varnish-settings.json"
    vcl = "/etc/varnish/cdn-managed/current/default.vcl"
    if not (root / vcl.lstrip("/")).is_file():
        raise ValueError("Provision and sync this node before changing storage")
    cache = root / "var/lib/cdn-agent/cache"
    if cache.is_symlink():
        raise ValueError("Cache path must not be a symlink")
    previous = target.read_text() if target.exists() else None
    wanted = unit(config, vcl)
    changed = wanted != previous
    if changed:
        requested = config["size_mb"] * 1024 * 1024
        if config["storage"] == "malloc":
            available = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        else:
            available = shutil.disk_usage(cache.parent).free + (cache.stat().st_size if cache.is_file() else 0)
        if requested > available * 0.8:
            raise ValueError("Storage exceeds 80% of available capacity")
        run(["/usr/sbin/varnishd", "-C", "-f", vcl], check=True, stdout=subprocess.DEVNULL)
    # Validate logrotate before changing any service configuration.
    rotation_text = logrotate(config)
    with tempfile.NamedTemporaryFile(mode="w") as check:
        check.write(rotation_text)
        check.flush()
        run(["/usr/sbin/logrotate", "--debug", check.name], check=True, stdout=subprocess.DEVNULL)
    if changed:
        atomic_write(target, wanted)
        try:
            run(["systemctl", "daemon-reload"], check=True)
            run(["systemctl", "restart", "varnish"], check=True)
            run(["systemctl", "is-active", "--quiet", "varnish"], check=True)
        except Exception:
            if previous is None:
                target.unlink()
            else:
                atomic_write(target, previous)
            run(["systemctl", "daemon-reload"], check=True)
            run(["systemctl", "restart", "varnish"], check=True)
            raise
    atomic_write(rotation, rotation_text)
    atomic_write(marker, json.dumps(config, sort_keys=True))
    return {"success": True, "restarted": changed, "config": config}
