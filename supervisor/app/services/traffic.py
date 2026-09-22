"""Idempotent, aggregate-only telemetry ingestion. Never stores request logs."""

from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, BigInteger, text
from sqlalchemy.dialects.postgresql import insert
from app.models import entities as m
from app.core.settings import settings

FIELDS = ("requests", "bytes_sent", "bytes_received", "cache_hits", "errors")


async def collection_mode(db):
    setting = await db.scalar(select(m.SystemSetting).where(m.SystemSetting.key == "analytics"))
    return setting.value.get("mode", "full") if setting else settings.analytics_mode


async def ingest(db, node_id, snapshot):
    mode = await collection_mode(db)
    if mode == "off":
        return
    # Same node can be polled by only one collector transaction across all replicas.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(node_id.bytes[-8:], "big", signed=True)},
    )
    epoch = snapshot["epoch"]
    from uuid import UUID

    incoming_ids = set()
    for item in snapshot.get("items", []):
        try:
            incoming_ids.add(UUID(item["vhost_id"]))
        except (ValueError, TypeError, KeyError):
            continue
    previous = {
        (row.vhost_id, row.country): row
        for row in (
            await db.scalars(
                select(m.TrafficCounter).where(
                    m.TrafficCounter.agent_id == node_id,
                    m.TrafficCounter.vhost_id.in_(incoming_ids),
                )
            )
        ).all()
    }
    valid = set(
        (
            await db.scalars(
                select(m.Vhost.id).where(
                    m.Vhost.id.in_(incoming_ids), m.Vhost.deleted_at.is_(None)
                )
            )
        ).all()
    )

    now = datetime.now(timezone.utc)
    hour = now.replace(minute=0, second=0, microsecond=0)
    counters = []
    buckets = []
    for item in snapshot.get("items", []):
        vhost_id = UUID(item["vhost_id"])
        if vhost_id not in valid:
            continue
        country = item.get("country", "ZZ")
        if len(country) != 2 or not country.isalpha():
            country = "ZZ"
        old = previous.get((vhost_id, country))
        observed = {field: max(0, int(item.get(field, 0))) for field in FIELDS}
        baseline = old.observed if old and old.epoch == epoch else dict.fromkeys(FIELDS, 0)
        delta = {field: max(0, observed[field] - baseline.get(field, 0)) for field in FIELDS}
        if old and old.epoch == epoch and old.observed == observed:
            continue
        totals = {field: (old.totals.get(field, 0) if old else 0) + delta[field] for field in FIELDS}
        counters.append(
            dict(
                agent_id=node_id,
                vhost_id=vhost_id,
                country=country,
                epoch=epoch,
                observed=observed,
                totals=totals,
                updated_at=now,
            )
        )
        if mode == "full" and item.get("mode") == "full" and delta["requests"]:
            buckets.append(dict(agent_id=node_id, vhost_id=vhost_id, country=country, hour=hour, **delta))
    for start in range(0, len(counters), 500):
        stmt = insert(m.TrafficCounter).values(counters[start : start + 500])
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["agent_id", "vhost_id", "country"],
                set_={
                    name: getattr(stmt.excluded, name)
                    for name in ["epoch", "observed", "totals", "updated_at"]
                },
            )
        )
    for start in range(0, len(buckets), 500):
        stmt = insert(m.TrafficBucket).values(buckets[start : start + 500])
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["agent_id", "vhost_id", "country", "hour"],
                set_={name: getattr(m.TrafficBucket, name) + getattr(stmt.excluded, name) for name in FIELDS},
            )
        )


async def usage(db, vhost_id=None, customer_id=None, hours=24):
    window = datetime.now(timezone.utc) - timedelta(hours=hours)
    filters = []
    if vhost_id:
        filters.append(m.TrafficBucket.vhost_id == vhost_id)
    if customer_id:
        filters.append(
            m.TrafficBucket.vhost_id.in_(select(m.Vhost.id).where(m.Vhost.customer_id == customer_id))
        )
    sums = [func.coalesce(func.sum(getattr(m.TrafficBucket, f)), 0).label(f) for f in FIELDS]
    base = select(*sums).where(m.TrafficBucket.hour >= window, *filters)
    totals = dict((await db.execute(base)).mappings().one())
    series = [
        dict(row)
        for row in (
            await db.execute(
                select(m.TrafficBucket.hour, *sums)
                .where(m.TrafficBucket.hour >= window, *filters)
                .group_by(m.TrafficBucket.hour)
                .order_by(m.TrafficBucket.hour)
            )
        ).mappings()
    ]
    countries = [
        dict(row)
        for row in (
            await db.execute(
                select(m.TrafficBucket.country, *sums)
                .where(m.TrafficBucket.hour >= window, *filters)
                .group_by(m.TrafficBucket.country)
                .order_by(func.sum(m.TrafficBucket.bytes_sent).desc())
                .limit(250)
            )
        ).mappings()
    ]
    agents = [
        dict(row)
        for row in (
            await db.execute(
                select(m.TrafficBucket.agent_id, m.TrafficBucket.country, *sums)
                .where(m.TrafficBucket.hour >= window, *filters)
                .group_by(m.TrafficBucket.agent_id, m.TrafficBucket.country)
                .order_by(func.sum(m.TrafficBucket.bytes_sent).desc())
                .limit(1000)
            )
        ).mappings()
    ]
    lifetime_filters = []
    if vhost_id:
        lifetime_filters.append(m.TrafficCounter.vhost_id == vhost_id)
    if customer_id:
        lifetime_filters.append(
            m.TrafficCounter.vhost_id.in_(select(m.Vhost.id).where(m.Vhost.customer_id == customer_id))
        )
    lifetime = dict(
        (
            await db.execute(
                select(
                    *[
                        func.coalesce(func.sum(m.TrafficCounter.totals[f].astext.cast(BigInteger)), 0).label(
                            f
                        )
                        for f in FIELDS
                    ]
                ).where(*lifetime_filters)
            )
        )
        .mappings()
        .one()
    )
    return {
        "window_hours": hours,
        "mode": await collection_mode(db),
        "totals": totals,
        "lifetime": lifetime,
        "series": series,
        "countries": countries,
        "agents": agents,
        "note": "Observed aggregate usage; hours represent collection time. ZZ means unavailable or disabled geography.",
    }
