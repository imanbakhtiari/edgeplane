"""Durable jobs: PostgreSQL session advisory lock fences each active node.

Claim locks are kept on a dedicated connection during I/O, but transactions are
short so progress events are visible. Crashed workers lose the connection/lock;
another worker can reclaim RUNNING targets without overlapping activation.
"""

import asyncio
import json
import logging
import httpx
from datetime import datetime, timezone
from sqlalchemy import select, text
from app.db.session import Session, engine
from app.models import entities as m
from app.core.settings import settings
from app.core.security import decrypt, redact
from app.services.config import latest, enqueue, revision
from app.services.agents import request_agent

log = logging.getLogger("cdn.worker")

SAFE_FAILURES = {
    "SSH_CREDENTIAL_MISSING": "SSH credentials are missing. Edit the node and save bootstrap credentials.",
    "SSH_HOST_KEY_APPROVAL_REQUIRED": "SSH host identity is not approved. Discover and approve the fingerprint before provisioning.",
    "UBUNTU_20_HOST_BOOTSTRAP_UNAVAILABLE": "Ubuntu 20.04 detected. Host provisioning needs NGINX GeoIP2 and NGINX exporter packages unavailable in its standard repositories; use Ubuntu 22.04 or 24.04. No packages were changed.",
    "UNSUPPORTED_NODE_OS": "Only Ubuntu nodes are supported by this host provisioner. No packages were changed.",
    "UNSUPPORTED_UBUNTU_RELEASE": "Only Ubuntu 22.04 and 24.04 are supported by this host provisioner. No packages were changed.",
    "NODE_PYTHON_TOO_OLD": "Node Python must be 3.10 or newer. Install a supported Python runtime before reprovisioning. No packages were changed.",
    "AGENT_PACKAGE_MISSING": "The production Agent package is missing on the Supervisor worker.",
    "BOOTSTRAP_FAILED": "The remote production-Agent bootstrap command failed.",
    "SUPERVISOR_EGRESS_IP_UNAVAILABLE": "The remote SSH session did not report the Supervisor source IP. Check SSH_CONNECTION on the POP, then configure AGENT_MANAGEMENT_ALLOWED_CIDRS.",
    "AGENT_SCHEMA_UNSUPPORTED": "The Agent does not support this Supervisor configuration schema.",
    "AGENT_REVISION_HASH_MISMATCH": "The Agent returned a different configuration revision or hash.",
}


def safe_failure(exc):
    value = str(exc)
    if value in SAFE_FAILURES:
        return value, SAFE_FAILURES[value]
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 403:
            return "AGENT_MANAGEMENT_DENIED", "Agent rejected the Supervisor source IP (HTTP 403). Reprovision to refresh its management allowlist, or add the Supervisor egress CIDR to AGENT_MANAGEMENT_ALLOWED_CIDRS."
        return "AGENT_HTTP_ERROR", f"Agent management API returned HTTP {status}. Check the Agent service journal."
    name = type(exc).__name__
    if name in {"ConnectError", "ConnectTimeout"}:
        return "AGENT_API_UNREACHABLE", "Cannot connect to the Agent management API. Provision it first, then check port 9443, TLS, and the firewall allowlist."
    if name == "TimeoutError":
        return "SSH_CONNECTION_TIMEOUT", "SSH connection timed out. Allow the Supervisor host to reach this POP on TCP 22, then retry the Agent-only upgrade. No NGINX or Varnish process was changed."
    if name in {"ConnectionLost", "ConnectionRefusedError", "HostKeyNotVerifiable", "PermissionDenied"}:
        return "SSH_CONNECTION_FAILED", "SSH connection or authentication failed. Run Test SSH & sudo and check the SSH host, port, credentials, approved fingerprint, and firewall."
    return name, "Operation failed. Run the relevant connectivity test and inspect the provisioning steps below."


def lock_key(id):
    return int.from_bytes(id.bytes[:8], "big", signed=True) if id else 7391044


async def event(job_id, target_id, message, level="INFO"):
    async with Session.begin() as db:
        db.add(m.JobEvent(job_id=job_id, target_id=target_id, message=message[:8192], level=level))
        if target_id:
            target = await db.get(m.JobTarget, target_id)
            if target and target.agent_id:
                db.add(m.AgentActivity(agent_id=target.agent_id, stage="job", status=level, message=message[:8192]))


async def claim(connection):
    async with Session.begin() as db:
        candidates = (
            await db.scalars(
                select(m.JobTarget)
                .where(m.JobTarget.status.in_(["PENDING", "RUNNING"]))
                .order_by(m.JobTarget.created_at)
                .with_for_update(skip_locked=True)
                .limit(32)
            )
        ).all()
        for target in candidates:
            key = lock_key(target.agent_id)
            locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            if not locked:
                continue
            target.status = "RUNNING"
            target.attempts += 1
            job = await db.get(m.Job, target.job_id)
            job.status = "RUNNING"
            return target.id, key
    return None


async def perform(target_id):
    async with Session() as db:
        target = await db.get(m.JobTarget, target_id)
        job = await db.get(m.Job, target.job_id)
        node = await db.get(m.AgentNode, target.agent_id) if target.agent_id else None
        job_id = job.id
        kind = job.kind
        payload = job.payload
        created_by = job.created_by
    await event(job_id, target_id, f"{kind}: starting")
    bootstrap_result = None
    if kind in {"PROVISION", "SERVICE_SYNC", "AGENT_UPGRADE", "AGENT_ROLLBACK", "VARNISH_CONFIG"}:
        from app.services.provisioning import provision

        agent_only = kind in {"AGENT_UPGRADE", "AGENT_ROLLBACK"}
        await provision(
            node,
            lambda message: event(job_id, target_id, message),
            agent_only=agent_only,
            rollback=kind == "AGENT_ROLLBACK",
            varnish_only=kind == "VARNISH_CONFIG",
        )
        bootstrap_result = {"success": True, "agent_only": agent_only}
        if kind == "PROVISION":
            await event(job_id, target_id, "Initial host bootstrap complete; synchronizing vhost desired state")
            kind = "VHOST_SYNC"
    completed_message = "Completed"
    if kind in {"SYNC", "VHOST_SYNC", "BUILD_REVISION"}:
        # Allow rapid API changes to settle before opening a DB transaction,
        # then read and apply only the newest desired state.
        await asyncio.sleep(settings.vhost_reconcile_debounce_seconds)
    async with Session.begin() as db:
        node = await db.get(m.AgentNode, node.id) if node else None
        if kind == "BUILD_REVISION":
            rev = await revision(db, user_id=created_by, deploy=True)
            result = {"success": True, "revision": rev.id}
            completed_message = f"Built desired revision {rev.id}; POP deployment queued"
        elif kind == "POLL":
            await poll_nodes()
            result = {"success": True}
        elif kind == "DNS":
            from app.services.dns import reconcile

            result = await reconcile(db)
        elif kind in {"SERVICE_SYNC", "AGENT_UPGRADE", "AGENT_ROLLBACK", "VARNISH_CONFIG"}:
            result = bootstrap_result
            completed_message = (
                "Varnish storage and managed log retention applied" if kind == "VARNISH_CONFIG" else
                "Agent release changed without changing or signalling NGINX or Varnish"
                if kind in {"AGENT_UPGRADE", "AGENT_ROLLBACK"}
                else "Service desired state reconciled independently of vhost configuration"
            )
        elif kind in {"SYNC", "VHOST_SYNC", "VALIDATE"}:
            # Always reconcile newest desired state; old queued work cannot regress a node.
            rev = await latest(db)
            bundle = json.loads(decrypt(rev.encrypted_bundle))
            capabilities = await request_agent(db, node, "GET", "/api/v1/capabilities")
            if bundle["schema_version"] not in capabilities["schema_versions"]:
                raise ValueError("AGENT_SCHEMA_UNSUPPORTED")
            if any("waf" in v for v in bundle.get("vhosts", [])) and not capabilities.get("waf_configuration_supported"):
                raise ValueError("AGENT_UPGRADE_REQUIRED: this revision includes the WAF configuration schema")
            for vhost in bundle.get("vhosts", []):
                waf = vhost.get("waf", {})
                if vhost.get("enabled", True) and waf.get("mode", "off") != "off":
                    installation = capabilities.get("waf_installation", {})
                    if not installation.get("configured") or installation.get("crs_version") != waf.get("crs_version"):
                        raise ValueError("WAF_CRS_VERSION_NOT_INSTALLED: provision the pinned WAF installation first")
            await event(job_id, target_id, f"Validating and applying revision {rev.id}")
            result = await request_agent(
                db,
                node,
                "POST",
                "/api/v1/config/" + ("validate" if kind == "VALIDATE" else "apply"),
                bundle,
            )
            if result.get("success") and kind in {"SYNC", "VHOST_SYNC"}:
                if result.get("revision") != rev.id or result.get("hash") != rev.config_hash:
                    raise ValueError("AGENT_REVISION_HASH_MISMATCH")
                state = await db.scalar(
                    select(m.AgentConfigState).where(m.AgentConfigState.agent_id == node.id)
                )
                state.revision = rev.id
                state.config_hash = rev.config_hash
                # Commit the acknowledged hashes with the revision. Otherwise list
                # views keep using the pre-deployment five-minute health sample.
                health = await db.scalar(select(m.AgentHealthHistory)
                                         .where(m.AgentHealthHistory.agent_id == node.id)
                                         .order_by(m.AgentHealthHistory.created_at.desc()).limit(1))
                observed = dict(health.observed) if health else {}
                observed.update({key: result[key] for key in ("revision", "hash", "vhosts", "vhost_hashes") if key in result})
                db.add(m.AgentHealthHistory(agent_id=node.id, observed=observed))
                node.status = "READY"
                completed_message = (
                    f"Desired revision {rev.id} already active; configuration unchanged"
                    if result.get("noop") else f"Applied desired revision {rev.id} to node"
                )
        elif kind == "PURGE":
            result = await request_agent(db, node, "POST", "/api/v1/cache/purge", payload)
        elif kind == "RELOAD":
            result = await request_agent(db, node, "POST", "/api/v1/services/nginx/reload")
        else:
            raise ValueError("UNSUPPORTED_JOB_TYPE")
    success = result.get("success", True)
    async with Session.begin() as db:
        target = await db.get(m.JobTarget, target_id)
        target.status = "SUCCESS" if success else "FAILED"
        target.result = redact(result)
        if not success and target.agent_id:
            failed_node = await db.get(m.AgentNode, target.agent_id)
            failed_node.status = "FAILED"
    failure_detail = " ".join(str(result.get("stderr", "")).split())[:500]
    await event(
        job_id,
        target_id,
        completed_message
        if success
        else "CONFIG NOT APPLIED; PREVIOUS CONFIG STILL ACTIVE: " + str(result.get("error") or result.get("code") or "VALIDATION_FAILED") + (": " + failure_detail if failure_detail else ""),
        "INFO" if success else "ERROR",
    )


async def finish_job(target_id):
    async with Session.begin() as db:
        target = await db.get(m.JobTarget, target_id)
        job = await db.scalar(select(m.Job).where(m.Job.id == target.job_id).with_for_update())
        states = list(
            (await db.scalars(select(m.JobTarget.status).where(m.JobTarget.job_id == job.id))).all()
        )
        job.status = (
            "RUNNING"
            if any(s in {"PENDING", "RUNNING"} for s in states)
            else (
                "SUCCESS"
                if all(s == "SUCCESS" for s in states)
                else "PARTIAL"
                if "SUCCESS" in states
                else "FAILED"
            )
        )


async def work_once():
    async with engine.connect() as connection:
        claimed = await claim(connection)
        if not claimed:
            return False
        target_id, key = claimed
        try:
            try:
                await perform(target_id)
            except Exception as exc:
                # SSH/HTTP exception strings may include credentials; persist typed error only.
                code, message = safe_failure(exc)
                async with Session.begin() as db:
                    target = await db.get(m.JobTarget, target_id)
                    target.status = "FAILED"
                    target.result = {
                        "error": code,
                        "message": message,
                    }
                    node = await db.get(m.AgentNode, target.agent_id) if target.agent_id else None
                    if node:
                        node.status = "FAILED"
                    job_id = target.job_id
                log.error("job failed job_id=%s target_id=%s error=%s", job_id, target_id, code)
                await event(job_id, target_id, message, "ERROR")
            await finish_job(target_id)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await connection.commit()
        return True


async def poll_one(id, semaphore):
    async with semaphore, engine.connect() as connection:
        key = lock_key(id)
        if not await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
            return
        try:
            async with Session.begin() as db:
                node = await db.get(m.AgentNode, id)
                try:
                    observed = await request_agent(db, node, "GET", "/api/v1/status")
                    if observed["agent_id"] != str(id) and settings.environment != "development":
                        raise ValueError("AGENT_IDENTITY_MISMATCH")
                    healthy = all(service["active"] for service in observed["services"].values())
                    node.last_seen = datetime.now(timezone.utc)
                    node.failures = 0 if healthy else node.failures + 1
                    node.successes = node.successes + 1 if healthy else 0
                    node.status = "READY" if healthy else "DEGRADED"
                    if node.successes >= settings.health_successes:
                        node.dns_eligible = True
                    if node.failures >= settings.health_failures:
                        node.dns_eligible = False
                    state = await db.scalar(
                        select(m.AgentConfigState).where(m.AgentConfigState.agent_id == id)
                    )
                    state.revision = observed["revision"]
                    state.config_hash = observed["hash"]
                    db.add(m.AgentHealthHistory(agent_id=id, observed=observed))
                    desired = await latest(db)
                    desired_vhost_hashes = {}
                    if desired:
                        from app.schemas.config import Bundle

                        desired_bundle = Bundle.model_validate_json(decrypt(desired.encrypted_bundle))
                        desired_vhost_hashes = {
                            str(v.id): v.digest() for v in desired_bundle.vhosts if v.enabled
                        }
                    observed_vhost_hashes = observed.get("vhost_hashes") or {}
                    vhosts_drifted = observed_vhost_hashes != desired_vhost_hashes
                    pending = await db.scalar(
                        select(m.JobTarget.id)
                        .where(m.JobTarget.agent_id == id, m.JobTarget.status.in_(["PENDING", "RUNNING"]))
                        .limit(1)
                    )
                    if (
                        desired
                        and (
                            not healthy
                            or state.revision != desired.id
                            or state.config_hash != desired.config_hash
                            or vhosts_drifted
                        )
                        and not pending
                    ):
                        await enqueue(
                            db,
                            "SERVICE_SYNC" if not healthy else "VHOST_SYNC",
                            {
                                "reason": (
                                    "service-drift"
                                    if not healthy
                                    else "vhost-drift"
                                    if vhosts_drifted
                                    else "revision-drift"
                                )
                            },
                            node_ids=[id],
                        )
                except Exception:
                    node.successes = 0
                    node.failures += 1
                    if node.failures >= settings.health_failures:
                        node.status = "OFFLINE"
                        node.dns_eligible = False
            # Telemetry failures must not mark a healthy data plane offline.
            try:
                async with Session.begin() as db:
                    from app.services.traffic import ingest, collection_mode

                    if await collection_mode(db) != "off":
                        node = await db.get(m.AgentNode, id)
                        snapshot = await request_agent(db, node, "GET", "/api/v1/traffic")
                        await ingest(db, id, snapshot)
            except Exception as exc:
                log.warning("telemetry collection failed for %s: %s", id, type(exc).__name__)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await connection.commit()


async def poll_nodes():
    async with engine.connect() as connection:
        if not await connection.scalar(text("SELECT pg_try_advisory_lock(7391045)")):
            return
        try:
            async with Session() as db:
                ids = list(
                    (
                        await db.scalars(
                            select(m.AgentNode.id).where(
                                m.AgentNode.active.is_(True), m.AgentNode.demo.is_(False)
                            )
                        )
                    ).all()
                )
            semaphore = asyncio.Semaphore(8)
            # Bounded batches avoid allocating an unbounded task list for large fleets.
            for start in range(0, len(ids), 64):
                await asyncio.gather(*(poll_one(id, semaphore) for id in ids[start : start + 64]))
            async with Session.begin() as db:
                from app.services.dns import reconcile

                await reconcile(db)
            await renew_certificates()
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(7391045)"))
            await connection.commit()


async def renew_certificates():
    """Reissue near-expiry Certbot certificates and deploy one new revision."""
    from datetime import timedelta
    from app.services.certificates import issue, validate_pair
    from app.core.security import encrypt
    from app.services.dns import dns_config

    async with Session.begin() as db:
        rows = list(
            (
                await db.scalars(
                    select(m.Certificate).where(
                        m.Certificate.source == "certbot",
                        m.Certificate.auto_renew.is_(True),
                        m.Certificate.status == "READY",
                    )
                )
            ).all()
        )
        due = [row for row in rows if row.expires_at <= datetime.now(timezone.utc) + timedelta(days=row.renew_before_days)]
        dns = await dns_config(db)
        identities = [(row.id, list(row.domains), row.email, row.challenge) for row in due]
        for row in due:
            row.status = "RENEWING"
    changed = False
    for certificate_id, domains, email, challenge in identities:
        try:
            pem, key = await issue(domains, email, dns if challenge == "dns-01" else None, challenge)
            cert, issued_domains = validate_pair(pem, key)
            async with Session.begin() as db:
                row = await db.get(m.Certificate, certificate_id)
                row.certificate, row.encrypted_key = pem, encrypt(key)
                row.expires_at, row.domains, row.status = cert.not_valid_after_utc, issued_domains, "READY"
                changed = True
        except Exception:
            async with Session.begin() as db:
                row = await db.get(m.Certificate, certificate_id)
                row.status = "FAILED"
            log.error("automatic certificate renewal failed certificate_id=%s", certificate_id)
    if changed:
        async with Session.begin() as db:
            await revision(db, deploy=True)


async def reconciliation_loop():
    while True:
        try:
            await poll_nodes()
        except Exception as exc:
            log.error("reconciliation failed: %s", type(exc).__name__)
        await asyncio.sleep(settings.agent_poll_interval)


async def main():
    reconcile_task = asyncio.create_task(reconciliation_loop())
    try:
        while True:
            try:
                await asyncio.gather(*(work_once() for _ in range(4)))
            except Exception as exc:
                log.error("worker cycle failed: %s", type(exc).__name__)
            await asyncio.sleep(settings.job_poll_interval)
    finally:
        reconcile_task.cancel()
        from contextlib import suppress

        with suppress(asyncio.CancelledError):
            await reconcile_task
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
