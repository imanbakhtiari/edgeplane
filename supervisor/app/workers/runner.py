"""Durable jobs: PostgreSQL session advisory lock fences each active node.

Claim locks are kept on a dedicated connection during I/O, but transactions are
short so progress events are visible. Crashed workers lose the connection/lock;
another worker can reclaim RUNNING targets without overlapping activation.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select, text
from app.db.session import Session, engine
from app.models import entities as m
from app.core.settings import settings
from app.core.security import decrypt, redact
from app.services.config import latest, enqueue
from app.services.agents import request_agent

log = logging.getLogger("cdn.worker")


def lock_key(id):
    return int.from_bytes(id.bytes[:8], "big", signed=True) if id else 7391044


async def event(job_id, target_id, message, level="INFO"):
    async with Session.begin() as db:
        db.add(m.JobEvent(job_id=job_id, target_id=target_id, message=message[:8192], level=level))


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
    await event(job_id, target_id, f"{kind}: starting")
    if kind == "PROVISION":
        from app.services.provisioning import provision

        await provision(node, lambda message: event(job_id, target_id, message))
        kind = "SYNC"
    async with Session.begin() as db:
        node = await db.get(m.AgentNode, node.id) if node else None
        if kind == "POLL":
            await poll_nodes()
            result = {"success": True}
        elif kind == "DNS":
            from app.services.dns import reconcile

            result = await reconcile(db)
        elif kind in {"SYNC", "VALIDATE"}:
            # Always reconcile newest desired state; old queued work cannot regress a node.
            rev = await latest(db)
            bundle = json.loads(decrypt(rev.encrypted_bundle))
            capabilities = await request_agent(db, node, "GET", "/api/v1/capabilities")
            if bundle["schema_version"] not in capabilities["schema_versions"]:
                raise ValueError("AGENT_SCHEMA_UNSUPPORTED")
            await event(job_id, target_id, f"Validating and applying revision {rev.id}")
            result = await request_agent(
                db, node, "POST", "/api/v1/config/" + ("apply" if kind == "SYNC" else "validate"), bundle
            )
            if result.get("success") and kind == "SYNC":
                if result.get("revision") != rev.id or result.get("hash") != rev.config_hash:
                    raise ValueError("AGENT_REVISION_HASH_MISMATCH")
                state = await db.scalar(
                    select(m.AgentConfigState).where(m.AgentConfigState.agent_id == node.id)
                )
                state.revision = rev.id
                state.config_hash = rev.config_hash
                node.status = "READY"
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
    await event(
        job_id,
        target_id,
        "Completed"
        if success
        else "CONFIG NOT APPLIED; PREVIOUS CONFIG STILL ACTIVE: " + json.dumps(redact(result)),
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
                code = type(exc).__name__
                async with Session.begin() as db:
                    target = await db.get(m.JobTarget, target_id)
                    target.status = "FAILED"
                    target.result = {
                        "error": code,
                        "message": "Operation failed. See step events and node connectivity.",
                    }
                    job_id = target.job_id
                await event(job_id, target_id, "Operation failed: " + code, "ERROR")
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
                    pending = await db.scalar(
                        select(m.JobTarget.id)
                        .where(m.JobTarget.agent_id == id, m.JobTarget.status.in_(["PENDING", "RUNNING"]))
                        .limit(1)
                    )
                    if (
                        desired
                        and (state.revision != desired.id or state.config_hash != desired.config_hash)
                        and not pending
                    ):
                        await enqueue(db, "SYNC", node_ids=[id])
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
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(7391045)"))
            await connection.commit()


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
