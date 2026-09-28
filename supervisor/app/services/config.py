from sqlalchemy import select, text
from app.models import entities as m
from app.schemas import config as c
from app.core.security import encrypt, decrypt

POLICIES = {
    "cache-policies": (m.CachePolicy, c.CachePolicy, "cache"),
    "rate-limit-policies": (m.RateLimitPolicy, c.RatePolicy, "rate"),
    "real-ip-policies": (m.RealIPPolicy, c.RealIPPolicy, "real_ip"),
    "header-policies": (m.HeaderPolicy, c.HeaderPolicy, "headers"),
}


async def configuration_lock(db):
    # Transaction-wide lock serializes ALL desired state mutations and their snapshots.
    await db.execute(text("SELECT pg_advisory_xact_lock(7391042)"))


async def latest(db):
    return await db.scalar(
        select(m.ConfigurationRevision).order_by(m.ConfigurationRevision.id.desc()).limit(1)
    )


async def snapshot(db):
    # A bounded number of SQL queries, independent of vhost count.
    from collections import defaultdict

    rows = (
        await db.scalars(
            select(m.Vhost)
            .where(m.Vhost.deleted_at.is_(None), m.Vhost.enabled.is_(True))
            .order_by(m.Vhost.id)
        )
    ).all()
    active = select(m.Vhost.id).where(m.Vhost.deleted_at.is_(None), m.Vhost.enabled.is_(True))
    domains = defaultdict(list)
    origins = defaultdict(list)
    for row in (
        await db.scalars(
            select(m.VhostDomain).where(m.VhostDomain.vhost_id.in_(active)).order_by(m.VhostDomain.domain)
        )
    ).all():
        domains[row.vhost_id].append(row.domain)
    for row in (
        await db.scalars(select(m.Origin).where(m.Origin.vhost_id.in_(active)).order_by(m.Origin.position))
    ).all():
        origins[row.vhost_id].append(row.config)
    policies = {}
    for model, schema, key in POLICIES.values():
        values = (await db.scalars(select(model))).all()
        policies[key] = ({p.id: p for p in values}, next((p for p in values if p.is_default), None))
    certs = {p.id: p for p in (await db.scalars(select(m.Certificate))).all()}
    hosts = []
    for v in rows:
        data = dict(
            id=str(v.id),
            name=v.name,
            domains=domains[v.id],
            origins=origins[v.id],
            customer_id=v.customer_id,
            **v.options,
        )
        for key, field in {
            "cache": "cache_policy_id",
            "rate": "rate_policy_id",
            "real_ip": "real_ip_policy_id",
            "headers": "header_policy_id",
        }.items():
            index, default = policies[key]
            policy = index.get(getattr(v, field)) if getattr(v, field) else default
            if policy:
                data[key] = policy.config
        if v.certificate_id:
            cert = certs[v.certificate_id]
            data["tls"] = {"certificate": cert.certificate, "private_key": decrypt(cert.encrypted_key)}
        hosts.append(c.Vhost.model_validate(data))
    return c.Bundle(revision=1, vhosts=hosts)


async def enqueue(db, kind, payload=None, node_ids=None, user_id=None, idempotency_key=None):
    if idempotency_key:
        existing = await db.scalar(select(m.Job).where(m.Job.idempotency_key == idempotency_key))
        if existing:
            if existing.kind != kind or existing.payload != (payload or {}):
                from fastapi import HTTPException

                raise HTTPException(409, "Idempotency key reused for a different operation")
            return existing
    job = m.Job(kind=kind, payload=payload or {}, created_by=user_id, idempotency_key=idempotency_key)
    db.add(job)
    await db.flush()
    if node_ids is None:
        node_ids = list(
            (
                await db.scalars(
                    select(m.AgentNode.id).where(m.AgentNode.active.is_(True), m.AgentNode.demo.is_(False))
                )
            ).all()
        )
    # For state reconciliation, retain at most one pending successor per node.
    # A running target may already have captured an older desired snapshot, so
    # it is intentionally not coalesced with the pending newest-state target.
    if kind in {"SYNC", "VHOST_SYNC", "SERVICE_SYNC"} and node_ids:
        already_pending = set(
            (
                await db.scalars(
                    select(m.JobTarget.agent_id)
                    .join(m.Job, m.Job.id == m.JobTarget.job_id)
                    .where(
                        m.Job.kind.in_(
                            ["SYNC", "VHOST_SYNC"]
                            if kind in {"SYNC", "VHOST_SYNC"}
                            else ["SERVICE_SYNC"]
                        ),
                        m.JobTarget.status == "PENDING",
                        m.JobTarget.agent_id.in_(node_ids),
                    )
                )
            ).all()
        )
        node_ids = [node_id for node_id in node_ids if node_id not in already_pending]
    for node_id in node_ids:
        db.add(m.JobTarget(job_id=job.id, agent_id=node_id))
    if not node_ids:
        job.status = "SUCCESS"
    db.add(m.JobEvent(job_id=job.id, message=f"{kind}: queued for {len(node_ids)} target(s)"))
    return job


async def revision(db, user_id=None, deploy=True, bundle=None):
    await configuration_lock(db)
    await db.flush()
    bundle = bundle or await snapshot(db)
    revision_id = await db.scalar(
        text("SELECT nextval(pg_get_serial_sequence('configuration_revisions','id'))")
    )
    bundle.revision = revision_id
    row = m.ConfigurationRevision(
        id=revision_id,
        created_by=user_id,
        config_hash=bundle.digest(),
        encrypted_bundle=encrypt(bundle.model_dump_json()),
    )
    db.add(row)
    await db.flush()
    for v in bundle.vhosts:
        safe = v.model_dump(mode="json", exclude={"tls"})
        db.add(m.ConfigurationRevisionItem(revision_id=row.id, vhost_id=v.id, config=safe))
    await db.flush()
    if deploy:
        await enqueue(db, "VHOST_SYNC", {"revision": row.id}, user_id=user_id)
    return row


async def queue_revision(db, user_id=None):
    """Durably coalesce saves; expensive fleet snapshots are worker work.

    A RUNNING snapshot is never reused: it may already have read old data.
    The configuration transaction lock makes the pending successor race-free.
    """
    await configuration_lock(db)
    pending = await db.scalar(select(m.Job).join(m.JobTarget, m.JobTarget.job_id == m.Job.id)
                             .where(m.Job.kind == "BUILD_REVISION", m.JobTarget.status == "PENDING")
                             .limit(1))
    if pending:
        return pending
    job = m.Job(kind="BUILD_REVISION", created_by=user_id, payload={})
    db.add(job)
    await db.flush()
    db.add(m.JobTarget(job_id=job.id, agent_id=None))
    db.add(m.JobEvent(job_id=job.id, message="Vhost changes saved; configuration build queued"))
    return job


async def revision_pending(db):
    return bool(await db.scalar(select(m.JobTarget.id).join(m.Job, m.Job.id == m.JobTarget.job_id)
                                .where(m.Job.kind == "BUILD_REVISION",
                                       m.JobTarget.status.in_(["PENDING", "RUNNING"])).limit(1)))


async def restore_revision(db, old, user_id):
    bundle = c.Bundle.model_validate_json(decrypt(old.encrypted_bundle))
    # Restore normalized desired state as well as the immutable snapshot.
    from datetime import datetime, timezone
    from sqlalchemy import delete

    current = list((await db.scalars(select(m.Vhost))).all())
    for v in current:
        v.deleted_at = datetime.now(timezone.utc)
        v.enabled = False
    await db.execute(delete(m.VhostDomain))
    await db.execute(delete(m.Origin))
    for state in bundle.vhosts:
        v = await db.get(m.Vhost, state.id)
        v.enabled = True
        v.deleted_at = None
        v.name = state.name
        v.customer_id = state.customer_id
        for field in ["cache_policy_id", "rate_policy_id", "real_ip_policy_id", "header_policy_id"]:
            setattr(v, field, None)
        # Materialize snapshot policies as dedicated rows so future edits preserve rollback state.
        for model, schema, key in POLICIES.values():
            policy = model(
                name=f"rollback-{old.id}-{state.id.hex[:8]}-{key}-{__import__('uuid').uuid4().hex[:8]}",
                config=getattr(state, key).model_dump(),
            )
            db.add(policy)
            await db.flush()
            setattr(
                v,
                {
                    "cache": "cache_policy_id",
                    "rate": "rate_policy_id",
                    "real_ip": "real_ip_policy_id",
                    "headers": "header_policy_id",
                }[key],
                policy.id,
            )
        v.options = state.model_dump(
            mode="json",
            exclude={
                "id",
                "customer_id",
                "name",
                "domains",
                "origins",
                "enabled",
                "cache",
                "rate",
                "real_ip",
                "headers",
                "tls",
            },
        )
        for d in state.domains:
            db.add(m.VhostDomain(vhost_id=v.id, domain=d))
        for i, o in enumerate(state.origins):
            db.add(m.Origin(vhost_id=v.id, position=i, config=o.model_dump()))
    return await revision(db, user_id, bundle=bundle)
