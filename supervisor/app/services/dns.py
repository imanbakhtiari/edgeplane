import httpx
from sqlalchemy import select
from app.core.settings import settings
from app.models import entities as m


async def dns_config(db):
    from app.core.security import decrypt

    row = await db.scalar(select(m.DNSSetting).limit(1))
    if row:
        import json

        raw = decrypt(row.encrypted_key) if row.encrypted_key else ""
        try:
            secret = json.loads(raw)
        except ValueError:
            secret = {"api_key": raw}
        return {"auth_mode": "api_key", **row.config, **secret}
    return {
        "api_url": settings.powerdns_api_url,
        "api_key": settings.powerdns_api_key,
        "auth_mode": settings.powerdns_auth_mode,
        "username": settings.powerdns_username,
        "password": settings.powerdns_password,
        "server_id": settings.powerdns_server_id,
        "zone": settings.powerdns_cdn_zone,
        "ttl": settings.dns_ttl,
    }


async def reconcile(db):
    config = await dns_config(db)
    if not config["api_url"]:
        return {"configured": False}
    nodes = (
        await db.scalars(
            select(m.AgentNode).where(
                m.AgentNode.active.is_(True),
                m.AgentNode.demo.is_(False),
                m.AgentNode.maintenance.is_(False),
                m.AgentNode.dns_eligible.is_(True),
            )
        )
    ).all()
    vhosts = (await db.scalars(select(m.Vhost))).all()
    rrsets = []
    for v in vhosts:
        if not v.cdn_hostname.endswith("." + config["zone"].rstrip(".")):
            continue
        for kind, field in [("A", "public_ipv4"), ("AAAA", "public_ipv6")]:
            addresses = (
                sorted({getattr(n, field) for n in nodes if getattr(n, field)})
                if v.enabled and not v.deleted_at
                else []
            )
            name = v.cdn_hostname.rstrip(".") + "."
            row = await db.scalar(
                select(m.DNSRecord).where(m.DNSRecord.name == name, m.DNSRecord.type == kind)
            )
            if row and row.values == addresses and row.status == "SUCCESS":
                continue
            rrsets.append(
                {
                    "name": name,
                    "type": kind,
                    "ttl": config["ttl"],
                    "changetype": "REPLACE" if addresses else "DELETE",
                    "records": [{"content": a, "disabled": False} for a in addresses],
                }
            )
            if not row:
                row = m.DNSRecord(name=name, type=kind, values=[])
                db.add(row)
            row.values = addresses
            row.status = "SUCCESS"
    if rrsets:
        zone = config["zone"].rstrip(".") + "."
        url = f"{config['api_url'].rstrip('/')}/api/v1/servers/{config['server_id']}/zones/{zone}"
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.patch(url, **authentication(config), json={"rrsets": rrsets})
            response.raise_for_status()
    return {"configured": True, "changed": len(rrsets)}


def authentication(config):
    if config.get("auth_mode") == "basic":
        return {"auth": httpx.BasicAuth(config.get("username", ""), config.get("password", ""))}
    return {"headers": {"X-API-Key": config.get("api_key", "")}}


async def test_connection(db):
    config = await dns_config(db)
    if not config["api_url"]:
        return {"success": False, "message": "Configure PowerDNS first"}
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        try:
            response = await client.get(
                f"{config['api_url']}/api/v1/servers/{config['server_id']}", **authentication(config)
            )
            if response.status_code in {401, 403}:
                return {
                    "success": False,
                    "status": response.status_code,
                    "message": "Authentication rejected. Native PowerDNS REST requires an API key; Basic mode requires a compatible authenticating API gateway.",
                }
            response.raise_for_status()
            return {
                "success": True,
                "auth_mode": config["auth_mode"],
                "server": response.json().get("id", config["server_id"]),
            }
        except (httpx.HTTPError, ValueError):
            return {
                "success": False,
                "message": "PowerDNS API is unreachable or did not return a valid server response",
            }
