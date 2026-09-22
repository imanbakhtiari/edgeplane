import httpx
import ipaddress
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
        "anycast_ipv4": settings.dns_anycast_ipv4,
        "anycast_ipv6": settings.dns_anycast_ipv6,
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
    existing_records = {
        (row.name, row.type): row for row in (await db.scalars(select(m.DNSRecord))).all()
    }
    rrsets = []
    for v in vhosts:
        if not v.cdn_hostname.endswith("." + config["zone"].rstrip(".")):
            continue
        for kind, field in [("A", "public_ipv4"), ("AAAA", "public_ipv6")]:
            anycast = config.get("anycast_ipv4" if kind == "A" else "anycast_ipv6", [])
            addresses = (
                sorted(set(anycast) or {getattr(n, field) for n in nodes if getattr(n, field)})
                if v.enabled and not v.deleted_at
                else []
            )
            name = v.cdn_hostname.rstrip(".") + "."
            row = existing_records.get((name, kind))
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
                existing_records[(name, kind)] = row
            row.values = addresses
            row.status = "SUCCESS"
    if rrsets:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            # Avoid a single oversized PowerDNS request for large customer fleets.
            for start in range(0, len(rrsets), 500):
                response = await client.patch(
                    zone_url(config),
                    **authentication(config),
                    json={"rrsets": rrsets[start : start + 500]},
                )
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


def zone_url(config):
    zone = config["zone"].rstrip(".") + "."
    return f"{config['api_url'].rstrip('/')}/api/v1/servers/{config['server_id']}/zones/{zone}"


def record_name(config, name):
    zone = config["zone"].rstrip(".").lower()
    candidate = name.rstrip(".").lower()
    if candidate == "@":
        candidate = zone
    elif "." not in candidate:
        candidate += "." + zone
    if candidate != zone and not candidate.endswith("." + zone):
        raise ValueError("Record must be inside the configured managed zone")
    return candidate + "."


def record_values(kind, values):
    kind = kind.upper()
    if kind not in {"A", "AAAA", "CNAME", "TXT", "CAA", "MX", "NS", "SRV"}:
        raise ValueError("Unsupported DNS record type")
    cleaned = []
    for raw in values:
        value = raw.strip()
        if not value or any(c in value for c in "\r\n"):
            raise ValueError("Invalid DNS record content")
        if kind in {"A", "AAAA"}:
            parsed = ipaddress.ip_address(value)
            if (kind == "A") != (parsed.version == 4):
                raise ValueError(f"Expected {kind} address")
            value = str(parsed)
        elif kind in {"CNAME", "NS"}:
            value = value.rstrip(".") + "."
        elif kind == "TXT" and not (value.startswith('"') and value.endswith('"')):
            # PowerDNS accepts TXT values in DNS master-file representation.
            import json

            value = json.dumps(value)
        cleaned.append(value)
    if not cleaned:
        raise ValueError("At least one record value is required")
    if kind == "CNAME" and len(cleaned) != 1:
        raise ValueError("CNAME must contain exactly one target")
    return kind, cleaned


async def list_records(db):
    config = await dns_config(db)
    if not config["api_url"]:
        return []
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.get(zone_url(config), **authentication(config))
        response.raise_for_status()
    rows = []
    for rrset in response.json().get("rrsets", []):
        rows.append(
            {
                "name": rrset.get("name", "").rstrip("."),
                "type": rrset.get("type"),
                "ttl": rrset.get("ttl"),
                "values": [r.get("content", "") for r in rrset.get("records", []) if not r.get("disabled")],
                "disabled_values": [r.get("content", "") for r in rrset.get("records", []) if r.get("disabled")],
            }
        )
    return rows


async def change_record(db, name, kind, ttl, values=None, delete=False):
    config = await dns_config(db)
    if not config["api_url"]:
        raise ValueError("Configure PowerDNS first")
    fqdn = record_name(config, name)
    kind = kind.upper()
    if kind not in {"A", "AAAA", "CNAME", "TXT", "CAA", "MX", "NS", "SRV"}:
        raise ValueError("Unsupported DNS record type")
    records = []
    if not delete:
        kind, values = record_values(kind, values or [])
        records = [{"content": value, "disabled": False} for value in values]
    body = {
        "rrsets": [
            {
                "name": fqdn,
                "type": kind,
                "ttl": ttl,
                "changetype": "DELETE" if delete else "REPLACE",
                "records": records,
            }
        ]
    }
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.patch(zone_url(config), **authentication(config), json=body)
        response.raise_for_status()
    return {"success": True, "name": fqdn.rstrip("."), "type": kind}
