from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, func, BigInteger
from fastapi.responses import Response
from app.api.auth import current_user, role
from app.api.resources import get, audit
from app.db.session import session
from app.models import entities as m
from app.schemas.config import Model
from app.services.traffic import usage, collection_mode

router = APIRouter(tags=["Traffic analytics"], dependencies=[Depends(current_user)])


@router.get("/traffic")
async def traffic(
    hours: int = Query(24, ge=1, le=2160),
    vhost_id: UUID | None = None,
    customer_id: UUID | None = None,
    db=Depends(session),
):
    return await usage(db, vhost_id, customer_id, hours)


@router.get("/vhosts/{id}/traffic")
async def vhost_traffic(id: UUID, hours: int = Query(24, ge=1, le=2160), db=Depends(session)):
    await get(db, m.Vhost, id)
    return await usage(db, vhost_id=id, hours=hours)


@router.get("/customers/{id}/usage")
async def customer_usage(
    id: UUID,
    hours: int = Query(24, ge=1, le=2160),
    format: Literal["json", "csv", "prometheus"] = "json",
    db=Depends(session),
):
    await get(db, m.Customer, id)
    report = await usage(db, customer_id=id, hours=hours)
    if format == "csv":
        import csv
        import io
        from app.services.traffic import FIELDS

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["hour", *FIELDS])
        writer.writeheader()
        writer.writerows(report["series"])
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="customer-{id}-usage.csv"'},
        )
    if format == "prometheus":
        rows = []
        for field, value in report["lifetime"].items():
            rows.extend(
                [
                    f"# TYPE cdn_customer_{field}_total counter",
                    f'cdn_customer_{field}_total{{customer_id="{id}"}} {value}',
                ]
            )
        return Response("\n".join(rows) + "\n", media_type="text/plain; version=0.0.4")
    return report


class AnalyticsSettings(Model):
    mode: Literal["full", "metrics_only", "off"] = "full"


@router.get("/settings/analytics")
async def analytics_settings(db=Depends(session)):
    return {"mode": await collection_mode(db)}


@router.put("/settings/analytics")
async def update_analytics(
    body: AnalyticsSettings, request: Request, user=Depends(role("ADMIN")), db=Depends(session)
):
    from app.services.config import configuration_lock

    await configuration_lock(db)
    row = await db.scalar(select(m.SystemSetting).where(m.SystemSetting.key == "analytics"))
    if not row:
        row = m.SystemSetting(key="analytics", value={})
        db.add(row)
    row.value = body.model_dump()
    audit(db, user, "ANALYTICS_MODE", "analytics", request)
    return body


async def prometheus(db):
    from app.services.config import latest

    desired = await latest(db)
    total = await db.scalar(
        select(func.count())
        .select_from(m.AgentNode)
        .where(m.AgentNode.active.is_(True), m.AgentNode.demo.is_(False))
    )
    drift = await db.scalar(
        select(func.count())
        .select_from(m.AgentConfigState)
        .join(m.AgentNode, m.AgentConfigState.agent_id == m.AgentNode.id)
        .where(
            m.AgentNode.active.is_(True),
            m.AgentNode.demo.is_(False),
            m.AgentConfigState.revision != (desired.id if desired else 0),
        )
    )
    rows = [
        "cdn_supervisor_up 1",
        f"cdn_supervisor_desired_revision {desired.id if desired else 0}",
        f"cdn_supervisor_agents {total}",
        f"cdn_supervisor_agents_out_of_sync {drift}",
        f"cdn_supervisor_analytics_collection_enabled {int(await collection_mode(db) != 'off')}",
    ]
    for field in ["requests", "bytes_sent", "bytes_received", "cache_hits", "errors"]:
        query = (
            select(m.Vhost.customer_id, func.sum(m.TrafficCounter.totals[field].astext.cast(BigInteger)))
            .join(m.Vhost, m.Vhost.id == m.TrafficCounter.vhost_id)
            .where(m.Vhost.customer_id.is_not(None))
            .group_by(m.Vhost.customer_id)
        )
        for customer, value in (await db.execute(query)).all():
            rows.append(f'cdn_customer_{field}_total{{customer_id="{customer}"}} {value}')
    return Response("\n".join(rows) + "\n", media_type="text/plain; version=0.0.4")
