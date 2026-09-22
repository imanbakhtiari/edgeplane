"""Operations, integration credentials and per-user preferences."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select, delete
from app.api.auth import current_user, role, section_access
from app.api.resources import get, output, audit
from app.core.security import token_hash, passwords
from app.db.session import session
from app.models import entities as m
from app.schemas.config import Model

router = APIRouter(tags=["Operations"], dependencies=[Depends(section_access)])
admin = role("ADMIN")
writer = role("ADMIN", "OPERATOR")


class Preferences(Model):
    theme: Literal["light", "dark", "system"] = "system"
    sidebar_collapsed: bool = False
    compact_tables: bool = False
    timezone: str = Field(default="UTC", max_length=100)

    @__import__("pydantic").field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown timezone") from None
        return value


@router.get("/auth/preferences", response_model=Preferences)
async def preferences(user=Depends(current_user)):
    return Preferences.model_validate(user.preferences)


@router.put("/auth/preferences", response_model=Preferences)
async def update_preferences(body: Preferences, user=Depends(current_user), db=Depends(session)):
    user.preferences = body.model_dump()
    return body


class ResetPassword(Model):
    password: str = Field(min_length=12, max_length=128, repr=False)


@router.post("/users/{id}/reset-password")
async def reset_password(
    id: UUID, body: ResetPassword, request: Request, user=Depends(admin), db=Depends(session)
):
    target = await get(db, m.User, id)
    target.password_hash = passwords.hash(body.password)
    target.must_change_password = True
    await db.execute(delete(m.LoginSession).where(m.LoginSession.user_id == id))
    audit(db, user, "RESET_PASSWORD", id, request)
    return {"success": True, "must_change_password": True}


class TokenInput(Model):
    name: str = Field(min_length=1, max_length=100)
    customer_id: UUID | None = None
    expires_days: int = Field(default=90, ge=1, le=365)


@router.get("/auth/api-tokens")
async def api_tokens(user=Depends(current_user), db=Depends(session)):
    return [
        {k: v for k, v in output(row).items() if k != "token_hash"}
        for row in (await db.scalars(select(m.APIToken).where(m.APIToken.user_id == user.id))).all()
    ]


@router.post("/auth/api-tokens")
async def create_token(body: TokenInput, request: Request, user=Depends(current_user), db=Depends(session)):
    if body.customer_id:
        await get(db, m.Customer, body.customer_id)
    token = "cdn_" + secrets.token_urlsafe(40)
    row = m.APIToken(
        user_id=user.id,
        customer_id=body.customer_id,
        name=body.name,
        token_hash=token_hash(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_days),
        created_by=user.id,
    )
    db.add(row)
    await db.flush()
    audit(db, user, "CREATE_API_TOKEN", row.id, request)
    return {
        "id": row.id,
        "token": token,
        "expires_at": row.expires_at,
        "scope": "customer-usage" if body.customer_id else user.role,
        "message": "Displayed once. Store securely.",
    }


@router.delete("/auth/api-tokens/{id}")
async def revoke_token(id: UUID, request: Request, user=Depends(current_user), db=Depends(session)):
    token = await get(db, m.APIToken, id)
    if token.user_id != user.id and user.role != "ADMIN":
        raise HTTPException(403, "Cannot revoke another user token")
    token.revoked = True
    audit(db, user, "REVOKE_API_TOKEN", id, request)
    return {"success": True}


class CustomerInput(Model):
    name: str = Field(min_length=1, max_length=150)
    external_id: str | None = Field(default=None, max_length=150)
    active: bool = True


@router.get("/customers")
async def customers(
    q: str = "", offset: int = 0, limit: int = 50, user=Depends(current_user), db=Depends(session)
):
    rows = (
        await db.scalars(
            select(m.Customer)
            .where(m.Customer.name.ilike("%" + q + "%"))
            .order_by(m.Customer.name)
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 200))
        )
    ).all()
    return [output(row) for row in rows]


@router.post("/customers")
async def create_customer(body: CustomerInput, request: Request, user=Depends(writer), db=Depends(session)):
    row = m.Customer(**body.model_dump(), created_by=user.id)
    db.add(row)
    await db.flush()
    audit(db, user, "CREATE_CUSTOMER", row.id, request)
    return output(row)


@router.put("/customers/{id}")
async def edit_customer(
    id: UUID, body: CustomerInput, request: Request, user=Depends(writer), db=Depends(session)
):
    row = await get(db, m.Customer, id)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    audit(db, user, "EDIT_CUSTOMER", id, request)
    return output(row)


@router.get("/monitoring")
async def monitoring(db=Depends(session)):
    from app.services.config import latest

    desired = await latest(db)
    rev = desired.id if desired else 0
    hashed = desired.config_hash if desired else ""
    rows = (
        await db.execute(
            select(m.AgentNode, m.AgentConfigState)
            .outerjoin(m.AgentConfigState, m.AgentNode.id == m.AgentConfigState.agent_id)
            .where(m.AgentNode.active.is_(True), m.AgentNode.demo.is_(False))
        )
    ).all()
    agents = [
        {
            "id": n.id,
            "name": n.name,
            "city": n.city,
            "status": "MAINTENANCE" if n.maintenance else n.status,
            "last_seen": n.last_seen,
            "applied_revision": state.revision if state else 0,
            "desired_revision": rev,
            "out_of_sync": not state or state.revision != rev or state.config_hash != hashed,
        }
        for n, state in rows
    ]
    events = (await db.scalars(select(m.JobEvent).order_by(m.JobEvent.created_at.desc()).limit(30))).all()
    from app.core.settings import settings

    return {
        "agents": agents,
        "out_of_sync": sum(a["out_of_sync"] for a in agents),
        "healthy": sum(a["status"] == "READY" for a in agents),
        "desired_revision": rev,
        "poll_interval_seconds": settings.agent_poll_interval,
        "events": [output(e) for e in events],
    }


@router.post("/monitoring/refresh")
async def refresh(user=Depends(writer), db=Depends(session)):
    from app.services.config import enqueue

    return output(await enqueue(db, "POLL", node_ids=[None], user_id=user.id))


@router.get("/routing-health")
async def routing_health(db=Depends(session)):
    nodes = (
        await db.scalars(
            select(m.AgentNode).where(m.AgentNode.active.is_(True), m.AgentNode.demo.is_(False))
        )
    ).all()
    latest_health = {
        row.agent_id: row
        for row in (
            await db.scalars(
                select(m.AgentHealthHistory)
                .distinct(m.AgentHealthHistory.agent_id)
                .order_by(m.AgentHealthHistory.agent_id, m.AgentHealthHistory.created_at.desc())
            )
        ).all()
    }
    result = []
    for node in nodes:
        health = latest_health.get(node.id)
        observed = health.observed if health else {}
        routing = observed.get("routing", {})
        services = observed.get("services", {})
        result.append(
            {
                "id": node.id,
                "name": node.name,
                "is_default": "BGP enabled" if routing.get("enabled") else "BGP disabled",
                "config": {
                    "city": node.city,
                    "agent": services.get("cdn-agent", {}).get("active"),
                    "nginx": services.get("nginx", {}).get("active"),
                    "varnish": services.get("varnish", {}).get("active"),
                    "node_exporter": services.get("prometheus-node-exporter", {}).get("active"),
                    "nginx_exporter": services.get("prometheus-nginx-exporter", {}).get("active"),
                    "prometheus": services.get("prometheus", {}).get("active"),
                    "bird": routing.get("service_active"),
                    "bgp_sessions_established": routing.get("established", 0),
                    "protocols": routing.get("protocols", []),
                    "last_seen": node.last_seen,
                },
            }
        )
    return result


@router.get("/settings/dns")
async def dns_settings(user=Depends(admin), db=Depends(session)):
    from app.services.dns import dns_config

    config = await dns_config(db)
    return {
        **{k: v for k, v in config.items() if k not in {"api_key", "password", "username"}},
        "username": config.get("username", ""),
        "has_api_key": bool(config.get("api_key")),
        "has_password": bool(config.get("password")),
    }


@router.post("/dns/test-connection")
async def test_dns(user=Depends(admin), db=Depends(session)):
    from app.services.dns import test_connection

    return await test_connection(db)


@router.get("/geo/status")
async def geo_status():
    from pathlib import Path
    from app.core.settings import settings

    path = Path(settings.maxmind_country_db)
    return {
        "available": path.is_file(),
        "database": "GeoLite2-Country",
        "bytes": path.stat().st_size if path.is_file() else 0,
    }


@router.get("/geo/lookup")
async def geo_lookup(ip: str):
    import ipaddress
    from app.services.geo import lookup

    try:
        address = str(ipaddress.ip_address(ip))
    except ValueError:
        raise HTTPException(422, "Invalid IP address") from None
    return lookup(address)
