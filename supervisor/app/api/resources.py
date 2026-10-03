import ipaddress
import asyncio
import asyncssh
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Literal
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select, delete
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from app.api.auth import current_user, role, section_access, SECTIONS
from app.db.session import session
from app.models import entities as m
from app.schemas import config as c
from app.services.config import POLICIES, configuration_lock, revision, latest, enqueue, restore_revision, queue_revision, revision_pending
from app.services.agents import request_agent
from app.core.security import decrypt, encrypt, redact, passwords
from app.core.settings import settings
from app.services.tls_status import tls_summary

router = APIRouter(dependencies=[Depends(section_access)])
writer = role("ADMIN", "OPERATOR")
admin = role("ADMIN")


def output(row):
    return redact({column.name: getattr(row, column.name) for column in row.__table__.columns})


def audit(db, user, action, resource, request):
    db.add(
        m.AuditLog(
            created_by=user.id,
            action=action,
            resource=str(resource),
            source_ip=request.client.host if request.client else "",
        )
    )


def node_activity(db, node, stage, status, message, user=None):
    db.add(m.AgentActivity(agent_id=node.id, stage=stage, status=status, message=message, created_by=user.id if user else None))


async def get(db, model, id):
    row = await db.get(model, id)
    if not row:
        raise HTTPException(404, "Resource not found")
    return row


class NodeInput(c.Model):
    name: str = Field(min_length=1, max_length=100)
    hostname: str
    management_url: str
    public_ipv4: str | None = None
    public_ipv6: str | None = None
    city: str = ""
    country: str = ""
    provider: str = ""
    notes: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    _hostname = field_validator("hostname")(c.hostname)

    @field_validator("public_ipv4", "public_ipv6")
    @classmethod
    def ip(cls, value):
        return str(ipaddress.ip_address(value)) if value else None

    @field_validator("management_url")
    @classmethod
    def url(cls, value):
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected management base URL")
        if parsed.scheme == "http" and settings.environment != "development":
            raise ValueError("Production requires HTTPS/mTLS")
        return value.rstrip("/")


class CredentialInput(c.Model):
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    port: int = Field(default=22, ge=1, le=65535)
    auth_mode: Literal["password", "private_key", "key_and_password", "supervisor_key"] | None = None
    password: str | None = Field(default=None, repr=False)
    private_key: str | None = Field(default=None, repr=False)
    sudo_password: str | None = Field(default=None, repr=False)
    sudo_password_required: bool = False
    management_cidrs: list[str] = Field(default_factory=list)
    firewall: bool = False

    @field_validator("management_cidrs")
    @classmethod
    def cidrs(cls, value):
        return [str(ipaddress.ip_network(v, strict=False)) for v in value]

    @__import__("pydantic").model_validator(mode="after")
    def sudo_secret(self):
        if self.auth_mode is None:
            self.auth_mode = (
                "key_and_password" if self.private_key and self.password else
                "private_key" if self.private_key else
                "password" if self.password else
                "private_key"
            )
        if self.auth_mode in {"password", "key_and_password"} and not self.password:
            raise ValueError("SSH login password is required for this authentication mode")
        if self.sudo_password_required and self.username != "root" and not self.sudo_password:
            raise ValueError("Sudo password is required when the checkbox is enabled")
        return self


class HostApproval(c.Model):
    host_key: str


class Toggle(c.Model):
    enabled: bool


@router.get("/agents")
async def nodes(
    db=Depends(session), q: str = "", offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)
):
    rows = (
        await db.scalars(
            select(m.AgentNode)
            .where(m.AgentNode.name.ilike("%" + q + "%"))
            .order_by(m.AgentNode.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    result = []
    desired = await latest(db)
    building_revision = await revision_pending(db)
    ids = [row.id for row in rows]
    states = {
        s.agent_id: s
        for s in (
            await db.scalars(select(m.AgentConfigState).where(m.AgentConfigState.agent_id.in_(ids)))
        ).all()
    }
    desired_bundle = c.Bundle.model_validate_json(decrypt(desired.encrypted_bundle)) if desired else None
    desired_hashes = {str(v.id): v.digest() for v in desired_bundle.vhosts if v.enabled} if desired_bundle else {}
    histories = list((await db.scalars(select(m.AgentHealthHistory).where(m.AgentHealthHistory.agent_id.in_(ids)).distinct(m.AgentHealthHistory.agent_id).order_by(m.AgentHealthHistory.agent_id, m.AgentHealthHistory.created_at.desc()))).all()) if ids else []
    observed_by_node = {}
    for history in histories:
        observed_by_node.setdefault(history.agent_id, history.observed)
    labels_by_node = {}
    for label in (await db.scalars(select(m.AgentLabel).where(m.AgentLabel.agent_id.in_(ids)))).all():
        labels_by_node.setdefault(label.agent_id, {})[label.key] = label.value
    for row in rows:
        state = states.get(row.id)
        observed = observed_by_node.get(row.id, {})
        applied_hashes = (observed.get("vhost_hashes") or {})
        synced = sum(applied_hashes.get(vhost_id) == digest for vhost_id, digest in desired_hashes.items())
        desired_services = {"nginx", "varnish", "cdn-agent", "prometheus", "prometheus-node-exporter", "prometheus-nginx-exporter", "prometheus-varnish-exporter"}
        current_services = observed.get("services") or {}
        healthy_services = sum(bool(current_services.get(name, {}).get("active")) for name in desired_services)
        result.append(
            {
                **output(row),
                "applied_revision": state.revision if state else 0,
                "desired_revision": desired.id if desired else 0,
                "vhosts_synced": synced,
                "vhosts_desired": len(desired_hashes),
                "vhost_state": "QUEUED" if building_revision else "SYNCED" if len(applied_hashes) == len(desired_hashes) and synced == len(desired_hashes) else "OUT OF SYNC",
                "services_healthy": healthy_services,
                "services_desired": len(desired_services),
                "service_state": "HEALTHY" if healthy_services == len(desired_services) else "DEGRADED",
                "labels": labels_by_node.get(row.id, {}),
            }
        )
    return result


@router.post("/agents")
async def add_node(body: NodeInput, request: Request, user=Depends(admin), db=Depends(session)):
    existing = await db.scalar(select(m.AgentNode).where(m.AgentNode.name == body.name))
    if existing and existing.status in {"PROVISIONING", "CREDENTIALS_SAVED", "SSH_FAILED", "HOST_KEY_PENDING", "SSH_READY", "HOST_KEY_APPROVED", "FAILED"}:
        for key, value in body.model_dump(exclude={"labels"}).items():
            setattr(existing, key, value)
        node_activity(db, existing, "node", "RESUMED", "Operator resumed incomplete node onboarding", user)
        await db.commit()
        return output(existing)
    node = m.AgentNode(**body.model_dump(exclude={"labels"}), created_by=user.id)
    db.add(node)
    await db.flush()
    db.add(m.AgentConfigState(agent_id=node.id))
    for k, v in body.labels.items():
        db.add(m.AgentLabel(agent_id=node.id, key=k, value=v))
    audit(db, user, "CREATE_NODE", node.id, request)
    node_activity(db, node, "node", "SUCCESS", "Node record created; waiting for SSH credentials", user)
    await db.commit()
    return output(node)


@router.get("/agents/{id}")
async def node(id: UUID, db=Depends(session)):
    row = await get(db, m.AgentNode, id)
    health = await db.scalar(
        select(m.AgentHealthHistory)
        .where(m.AgentHealthHistory.agent_id == id)
        .order_by(m.AgentHealthHistory.created_at.desc())
        .limit(1)
    )
    return {**output(row), "observed": health.observed if health else {}}


@router.get("/agents/{id}/vhosts")
async def node_vhosts(id: UUID, user=Depends(current_user), db=Depends(session)):
    """Inspect the exact vhost files in a node's currently applied release."""
    row = await get(db, m.AgentNode, id)
    try:
        applied = await request_agent(db, row, "GET", "/api/v1/config/vhosts")
    except Exception as exc:
        raise HTTPException(502, "The node Agent could not return its applied vhost configuration.") from exc
    desired_revision = await latest(db)
    building_revision = await revision_pending(db)
    bundle = c.Bundle.model_validate_json(decrypt(desired_revision.encrypted_bundle)) if desired_revision else None
    desired = {str(v.id): v for v in bundle.vhosts if v.enabled} if bundle else {}
    current = {str(v.get("id")): v for v in applied}
    result = []
    for vhost_id in sorted(set(desired) | set(current)):
        wanted, actual = desired.get(vhost_id), current.get(vhost_id)
        desired_hash = wanted.digest() if wanted else None
        applied_hash = actual.get("applied_hash") if actual else None
        status = "SYNCED" if wanted and actual and desired_hash == applied_hash else "MISSING" if wanted and not actual else "EXTRA" if actual and not wanted else "DRIFTED"
        if building_revision:
            status = "QUEUED"
        result.append({
            **(actual or {}),
            "id": vhost_id,
            "name": wanted.name if wanted else actual.get("name", vhost_id),
            "domains": wanted.domains if wanted else actual.get("domains", []),
            "desired_hash": desired_hash,
            "applied_hash": applied_hash,
            "status": status,
        })
    return result


@router.put("/agents/{id}/credentials")
async def credentials(
    id: UUID, body: CredentialInput, request: Request, user=Depends(admin), db=Depends(session)
):
    await get(db, m.AgentNode, id)
    if body.auth_mode in {"private_key", "key_and_password", "supervisor_key"}:
        try:
            from app.services.provisioning import client_keys
            client_keys(body.model_dump())
        except (__import__("asyncssh").Error, OSError, ValueError) as exc:
            detail = (
                "Pasted SSH private key is invalid. Check its format and passphrase."
                if body.private_key else
                "Dedicated Supervisor SSH key is not readable by this container. Correct the key mount permissions or paste a private key."
            )
            raise HTTPException(422, detail) from exc
    row = await db.scalar(select(m.AgentCredential).where(m.AgentCredential.agent_id == id))
    if not row:
        row = m.AgentCredential(agent_id=id, encrypted="")
        db.add(row)
    row.encrypted = encrypt(body.model_dump_json())
    node = await get(db, m.AgentNode, id)
    node.status = "CREDENTIALS_SAVED"
    node_activity(db, node, "credentials", "SUCCESS", "Encrypted SSH and sudo credentials saved", user)
    audit(db, user, "SET_CREDENTIAL", id, request)
    await db.commit()
    return {"success": True}


@router.get("/agents/{id}/credentials/summary")
async def credential_summary(id: UUID, user=Depends(admin), db=Depends(session)):
    from app.services.provisioning import auth_mode, credential

    node = await get(db, m.AgentNode, id)
    try:
        row, data = await credential(db, node)
    except ValueError:
        return {"configured": False}
    return {
        "configured": True,
        "username": data.get("username"),
        "port": data.get("port"),
        "auth_mode": auth_mode(data),
        "sudo_password_required": bool(data.get("sudo_password_required")),
        "host_key_approved": bool(row.host_key),
    }


@router.get("/agents/{id}/fingerprint")
async def fingerprint(id: UUID, user=Depends(admin), db=Depends(session)):
    from app.services.provisioning import discover

    node = await get(db, m.AgentNode, id)
    try:
        result = await discover(db, node)
        node.status = "HOST_KEY_PENDING"
        node_activity(db, node, "host-key", "SUCCESS", "SSH host key discovered; operator approval required", user)
        return result
    except Exception as exc:
        node.status = "SSH_FAILED"
        node_activity(db, node, "host-key", "FAILED", "Could not retrieve SSH host key: " + type(exc).__name__, user)
        await db.commit()
        raise HTTPException(
            422,
            "Cannot discover the SSH identity. Check the SSH hostname/public IP, port, credentials, routing, and firewall.",
        ) from exc


@router.post("/agents/{id}/test-ssh")
async def test_agent_ssh(
    id: UUID, body: HostApproval, request: Request, user=Depends(admin), db=Depends(session)
):
    from app.services.provisioning import test_ssh

    try:
        result = await test_ssh(db, await get(db, m.AgentNode, id), body.host_key)
    except ValueError as exc:
        result = {"success": False, "stage": "validation", "message": str(exc)}
    node = await get(db, m.AgentNode, id)
    node.status = "SSH_READY" if result["success"] else "SSH_FAILED"
    node_activity(db, node, "ssh-test", "SUCCESS" if result["success"] else "FAILED", result.get("message") or f"SSH connected; sudo mode: {result.get('sudo')}", user)
    audit(db, user, "TEST_NODE_SSH", id, request)
    return result


@router.post("/agents/{id}/test-approved-ssh")
async def test_approved_agent_ssh(
    id: UUID, request: Request, user=Depends(admin), db=Depends(session)
):
    """Test an existing node without trusting a newly discovered host key."""
    from app.services.provisioning import credential, test_ssh

    node = await get(db, m.AgentNode, id)
    credential_row, _ = await credential(db, node)
    if not credential_row.host_key:
        raise HTTPException(
            409,
            "SSH host identity is not approved. Edit the node, discover its fingerprint, and approve it first.",
        )
    result = await test_ssh(db, node, credential_row.host_key)
    node.status = "SSH_READY" if result["success"] else "SSH_FAILED"
    node_activity(
        db,
        node,
        "ssh-test",
        "SUCCESS" if result["success"] else "FAILED",
        result.get("message") or f"SSH connected; sudo mode: {result.get('sudo')}",
        user,
    )
    audit(db, user, "TEST_NODE_SSH", id, request)
    await db.commit()
    return result


@router.get("/agents/{id}/activities")
async def node_activities(id: UUID, db=Depends(session)):
    await get(db, m.AgentNode, id)
    rows = await db.scalars(select(m.AgentActivity).where(m.AgentActivity.agent_id == id).order_by(m.AgentActivity.created_at.desc()).limit(200))
    return [output(row) for row in rows]


@router.post("/agents/{id}/approve-host-key")
async def approve(id: UUID, body: HostApproval, request: Request, user=Depends(admin), db=Depends(session)):
    import asyncssh

    try:
        key = asyncssh.import_public_key(body.host_key)
    except Exception:
        raise HTTPException(422, "Invalid SSH public host key") from None
    credential = await db.scalar(select(m.AgentCredential).where(m.AgentCredential.agent_id == id))
    if not credential:
        raise HTTPException(400, "Save SSH credentials first")
    credential.host_key = key.export_public_key().decode().strip()
    node = await get(db, m.AgentNode, id)
    node.status = "HOST_KEY_APPROVED"
    node_activity(db, node, "host-key", "SUCCESS", "Operator approved SSH host fingerprint", user)
    audit(db, user, "APPROVE_HOST_KEY", id, request)
    return {"fingerprint": key.get_fingerprint()}


@router.post("/agents/{id}/maintenance")
async def maintenance(id: UUID, body: Toggle, request: Request, user=Depends(writer), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    node.maintenance = body.enabled
    audit(db, user, "MAINTENANCE", id, request)
    return output(node)


@router.get("/agents/{id}/services/{service}/logs")
async def node_service_logs(id: UUID, service: str, request: Request, user=Depends(current_user), db=Depends(session)):
    from app.services.provisioning import service_command, MANAGED_SERVICES

    if service not in MANAGED_SERVICES:
        raise HTTPException(404, "Unknown managed service")
    node = await get(db, m.AgentNode, id)
    try:
        result = await service_command(db, node, service, "logs")
    except ValueError:
        raise HTTPException(409, "Approved SSH credentials are required to read node service logs.") from None
    except (asyncssh.Error, OSError, asyncio.TimeoutError):
        raise HTTPException(502, "Cannot reach node over approved SSH. Test SSH and sudo first.") from None
    audit(db, user, "READ_NODE_SERVICE_LOGS", f"{id}/{service}", request)
    return result


@router.post("/agents/{id}/services/{service}/restart")
async def restart_node_service(id: UUID, service: str, request: Request, user=Depends(admin), db=Depends(session)):
    from app.services.provisioning import service_command, MANAGED_SERVICES

    if service not in MANAGED_SERVICES:
        raise HTTPException(404, "Unknown managed service")
    node = await get(db, m.AgentNode, id)
    try:
        result = await service_command(db, node, service, "restart")
    except ValueError:
        raise HTTPException(409, "Approved SSH credentials are required to control node services.") from None
    except (asyncssh.Error, OSError, asyncio.TimeoutError):
        raise HTTPException(502, "Cannot reach node over approved SSH. Test SSH and sudo first.") from None
    operation = "Validated NGINX and sent graceful reload" if service == "nginx" else f"Restarted {service}"
    node_activity(db, node, "service", "SUCCESS" if result["success"] else "ERROR", operation if result["success"] else f"{service} restart failed; inspect service logs", user)
    audit(db, user, "RELOAD_NGINX" if service == "nginx" else "RESTART_NODE_SERVICE", f"{id}/{service}", request)
    return result


@router.post("/agents/{id}/{action}")
async def node_action(id: UUID, action: str, request: Request, user=Depends(writer), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    if action in {"disable", "activate"}:
        if user.role != "ADMIN":
            raise HTTPException(403, "Administrator required to change node activation")
        enabled = action == "activate"
        node.active = enabled
        node.dns_eligible = False
        node.status = "OFFLINE" if enabled else "DISABLED"
        node.failures = 0
        node.successes = 0
        node_activity(
            db,
            node,
            "node",
            "ACTIVATED" if enabled else "DISABLED",
            "Node activated; awaiting a successful health check" if enabled else "Node disabled and removed from DNS eligibility",
            user,
        )
        audit(db, user, action.upper() + "_NODE", id, request)
        return output(node)
    mapping = {
        "sync": "VHOST_SYNC",
        "sync-vhosts": "VHOST_SYNC",
        "sync-services": "SERVICE_SYNC",
        "reprovision-services": "SERVICE_SYNC",
        "provision": "PROVISION",
        "reprovision": "AGENT_UPGRADE",
        "reprovision-auto-approve": "AGENT_UPGRADE",
        "upgrade": "AGENT_UPGRADE",
        "rollback-agent": "AGENT_ROLLBACK",
        "validate": "VALIDATE",
        "reload": "RELOAD",
        "test-origin": "TEST_ORIGIN",
    }
    if action not in mapping:
        raise HTTPException(404, "Unknown action")
    if action == "reload" and node.status not in {"READY", "DEGRADED"}:
        raise HTTPException(409, "Provision and connect the agent before reloading NGINX")
    if action in {"provision", "reprovision", "reprovision-auto-approve", "upgrade", "rollback-agent", "sync-services", "reprovision-services"} and user.role != "ADMIN":
        raise HTTPException(403, "Administrator required for SSH provisioning")
    if action == "reprovision-auto-approve":
        from app.services.provisioning import credential as ssh_credential, discover

        credential, _ = await ssh_credential(db, node)
        if not credential.host_key:
            try:
                identity = await discover(db, node)
            except (ConnectionError, ValueError):
                node_activity(
                    db,
                    node,
                    "host-key",
                    "FAILED",
                    "SSH identity discovery failed. Check the SSH hostname/public IP, port, credentials, routing, and firewall.",
                    user,
                )
                raise HTTPException(
                    422,
                    "Cannot reach SSH using the configured hostname or public IPv4. Edit the node and verify its SSH port, routing, and firewall.",
                )
            credential.host_key = identity["host_key"]
            if node.hostname != identity["host"]:
                old_hostname = node.hostname
                node.hostname = identity["host"]
                node_activity(
                    db,
                    node,
                    "ssh-endpoint",
                    "UPDATED",
                    f"SSH hostname {old_hostname} was not reachable; using public IPv4 {identity['host']}",
                    user,
                )
            node_activity(
                db,
                node,
                "host-key",
                "AUTO_APPROVED",
                f"SSH host identity automatically approved: {identity['fingerprint']}",
                user,
            )
    job = await enqueue(
        db,
        mapping[action],
        node_ids=[id],
        user_id=user.id,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    node.status = "PROVISIONING" if mapping[action] == "PROVISION" else node.status
    node_activity(db, node, action, "QUEUED", f"{mapping[action]} job queued as {job.id}", user)
    audit(db, user, action, id, request)
    return output(job)


class VhostInput(c.Model):
    name: str = Field(min_length=1, max_length=100)
    domains: list[str]
    origins: list[c.Origin]
    enabled: bool = True
    cache_policy_id: UUID | None = None
    rate_policy_id: UUID | None = None
    real_ip_policy_id: UUID | None = None
    header_policy_id: UUID | None = None
    certificate_id: UUID | None = None
    customer_id: UUID | None = None
    options: dict = Field(default_factory=dict)
    deploy: bool = True

    @field_validator("options")
    @classmethod
    def controlled_options(cls, value):
        allowed = {
            "websocket",
            "logging",
            "max_body_mb",
            "client_timeout",
            "allowed_methods",
            "blocked_paths",
            "ip_allow",
            "ip_deny",
            "path_rules",
            "redirect_rules",
            "origin_routes",
            "geo",
            "analytics",
            "redirect_https",
            "tls_mode",
            "waf",
        }
        if set(value) - allowed:
            raise ValueError("Options may only contain typed traffic/security settings")
        return value

    @field_validator("domains")
    @classmethod
    def domains_valid(cls, value):
        return c.Vhost.domains_valid(value)

    @model_validator(mode="after")
    def valid_tls_controls(self):
        waf = c.WAFPolicy.model_validate(self.options.get("waf", {}))
        if waf.mode != "off" and not self.certificate_id:
            raise ValueError("WAF_REQUIRES_POP_CERTIFICATE")
        mode = self.options.get("tls_mode", "auto")
        if mode not in {"auto", "http_only", "passthrough", "terminate"}:
            raise ValueError("Invalid TLS mode")
        if mode == "terminate" and not self.certificate_id:
            raise ValueError("POP_TLS_CERTIFICATE_REQUIRED")
        if mode in {"passthrough", "http_only"} and self.certificate_id:
            raise ValueError("Remove the POP certificate or select POP TLS termination")
        if mode == "passthrough" and any(o.scheme != "https" for o in self.origins):
            raise ValueError("TLS passthrough requires HTTPS origins")
        if self.options.get("redirect_https") and not self.certificate_id:
            raise ValueError("HTTP_TO_HTTPS_REDIRECT_REQUIRES_EDGE_CERTIFICATE")
        return self


class VhostYaml(c.Model):
    yaml: str = Field(min_length=1, max_length=2_000_000)
    deploy: bool = True


async def write_vhost(db, body, v):
    c.Vhost(id=v.id, name=body.name, domains=body.domains, origins=body.origins, **body.options)
    for field, value in body.model_dump(exclude={"domains", "origins", "deploy"}).items():
        setattr(v, field, value)
    for field, model in [
        ("cache_policy_id", m.CachePolicy),
        ("rate_policy_id", m.RateLimitPolicy),
        ("real_ip_policy_id", m.RealIPPolicy),
        ("header_policy_id", m.HeaderPolicy),
        ("certificate_id", m.Certificate),
    ]:
        if getattr(v, field):
            await get(db, model, getattr(v, field))
    if v.certificate_id:
        cert = await get(db, m.Certificate, v.certificate_id)
        for domain in body.domains:
            covered = any(
                domain == san
                or (san.startswith("*.") and domain.endswith(san[1:]) and domain.count(".") == san.count("."))
                for san in cert.domains
            )
            if not covered:
                raise HTTPException(422, "CERTIFICATE_DOMAIN_MISMATCH")
    await db.execute(delete(m.VhostDomain).where(m.VhostDomain.vhost_id == v.id))
    await db.execute(delete(m.Origin).where(m.Origin.vhost_id == v.id))
    for domain in body.domains:
        db.add(m.VhostDomain(vhost_id=v.id, domain=domain))
    for index, origin in enumerate(body.origins):
        db.add(m.Origin(vhost_id=v.id, position=index, config=origin.model_dump()))


async def vhost_output(db, v):
    result = {
        **output(v),
        "domains": list(
            (await db.scalars(select(m.VhostDomain.domain).where(m.VhostDomain.vhost_id == v.id))).all()
        ),
        "origins": list(
            (
                await db.scalars(
                    select(m.Origin.config).where(m.Origin.vhost_id == v.id).order_by(m.Origin.position)
                )
            ).all()
        ),
    }
    effective = {}
    fields = {"cache": "cache_policy_id", "rate": "rate_policy_id", "real_ip": "real_ip_policy_id", "headers": "header_policy_id"}
    for _resource, (model, _schema, key) in POLICIES.items():
        selected_id = getattr(v, fields[key])
        policy = await db.get(model, selected_id) if selected_id else await db.scalar(select(model).where(model.is_default.is_(True)))
        effective[key] = {
            "source": "vhost" if selected_id else "global",
            "policy_id": str(policy.id) if policy else None,
            "policy_name": policy.name if policy else None,
            "config": policy.config if policy else None,
        }
    result["effective_policies"] = effective
    result.update(tls_summary(v.certificate_id, v.options, result["origins"]))
    return result


@router.get("/vhosts")
async def vhosts(
    db=Depends(session), q: str = "", offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)
):
    rows = (
        await db.scalars(
            select(m.Vhost)
            .where(m.Vhost.deleted_at.is_(None), m.Vhost.name.ilike("%" + q + "%"))
            .order_by(m.Vhost.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    ids = [v.id for v in rows]
    domains, origins = {}, {}
    for row in (await db.scalars(select(m.VhostDomain).where(m.VhostDomain.vhost_id.in_(ids)))).all():
        domains.setdefault(row.vhost_id, []).append(row.domain)
    for row in (
        await db.scalars(select(m.Origin).where(m.Origin.vhost_id.in_(ids)).order_by(m.Origin.position))
    ).all():
        origins.setdefault(row.vhost_id, []).append(row.config)
    return [{**output(v), "domains": domains.get(v.id, []), "origins": origins.get(v.id, []),
             **tls_summary(v.certificate_id, v.options, origins.get(v.id, []))} for v in rows]


@router.get("/vhosts/{id}/deployment")
async def vhost_deployment(id: UUID, user=Depends(current_user), db=Depends(session)):
    """Compare one desired vhost with the last state reported by every active node."""
    await get(db, m.Vhost, id)
    building_revision = await revision_pending(db)
    desired_revision = await latest(db)
    bundle = c.Bundle.model_validate_json(decrypt(desired_revision.encrypted_bundle)) if desired_revision else None
    wanted = next((v for v in bundle.vhosts if v.id == id and v.enabled), None) if bundle else None
    desired_hash = wanted.digest() if wanted else None
    nodes = list((await db.scalars(select(m.AgentNode).where(m.AgentNode.active.is_(True), m.AgentNode.demo.is_(False)).order_by(m.AgentNode.name))).all())
    histories = list((await db.scalars(select(m.AgentHealthHistory).where(m.AgentHealthHistory.agent_id.in_([n.id for n in nodes])).distinct(m.AgentHealthHistory.agent_id).order_by(m.AgentHealthHistory.agent_id, m.AgentHealthHistory.created_at.desc()))).all()) if nodes else []
    latest_by_node = {}
    for history in histories:
        latest_by_node.setdefault(history.agent_id, history)
    result = []
    for node in nodes:
        observed = latest_by_node.get(node.id).observed if latest_by_node.get(node.id) else {}
        applied_hash = (observed.get("vhost_hashes") or {}).get(str(id))
        applied_ids = set(observed.get("vhosts") or [])
        status = "SYNCED" if desired_hash and applied_hash == desired_hash else "MISSING" if desired_hash and str(id) not in applied_ids else "EXTRA" if not desired_hash and str(id) in applied_ids else "DRIFTED" if applied_hash else "UNKNOWN"
        if building_revision:
            status = "QUEUED"
        result.append({"node_id": node.id, "node_name": node.name, "city": node.city, "status": status, "desired_hash": desired_hash, "applied_hash": applied_hash, "last_seen": node.last_seen})
    return result


@router.get("/vhosts/{id}/yaml")
async def vhost_yaml(id: UUID, user=Depends(current_user), db=Depends(session)):
    """Export the editable, typed vhost source without certificate secrets."""
    import yaml

    row = await get(db, m.Vhost, id)
    data = await vhost_output(db, row)
    editable = {key: data.get(key) for key in VhostInput.model_fields if key != "deploy"}
    editable["deploy"] = True
    normalized = VhostInput.model_validate(editable).model_dump(mode="json")
    return {"yaml": yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True)}


@router.put("/vhosts/{id}/yaml")
async def update_vhost_yaml(id: UUID, body: VhostYaml, request: Request, user=Depends(writer), db=Depends(session)):
    """Validate YAML through the same strict schema, then fan out to all active nodes."""
    import yaml

    await configuration_lock(db)
    try:
        raw = yaml.safe_load(body.yaml)
        if not isinstance(raw, dict):
            raise ValueError("Vhost YAML must contain one mapping")
        raw["deploy"] = True
        parsed = VhostInput.model_validate(raw)
    except (yaml.YAMLError, ValueError) as exc:
        raise HTTPException(422, f"Invalid vhost YAML: {exc}") from exc
    row = await get(db, m.Vhost, id)
    await write_vhost(db, parsed, row)
    deployed = await queue_revision(db, user.id)
    audit(db, user, "EDIT_VHOST_YAML", id, request)
    return {**await vhost_output(db, row), "deployment_job_id": deployed.id, "deployment_status": "QUEUED"}


@router.post("/vhosts")
async def add_vhost(body: VhostInput, request: Request, user=Depends(writer), db=Depends(session)):
    await configuration_lock(db)
    from app.services.dns import dns_config

    dns_settings = await dns_config(db)
    id = uuid4()
    v = m.Vhost(
        id=id, name=body.name, cdn_hostname=f"{id.hex[:12]}.{dns_settings['zone']}", created_by=user.id
    )
    db.add(v)
    await db.flush()
    await write_vhost(db, body, v)
    rev = await queue_revision(db, user.id)
    audit(db, user, "CREATE_VHOST", id, request)
    return {**await vhost_output(db, v), "deployment_job_id": rev.id, "deployment_status": "QUEUED"}


@router.put("/vhosts/{id}")
async def edit_vhost(id: UUID, body: VhostInput, request: Request, user=Depends(writer), db=Depends(session)):
    await configuration_lock(db)
    v = await get(db, m.Vhost, id)
    await write_vhost(db, body, v)
    rev = await queue_revision(db, user.id)
    audit(db, user, "EDIT_VHOST", id, request)
    return {**await vhost_output(db, v), "deployment_job_id": rev.id, "deployment_status": "QUEUED"}


@router.delete("/vhosts/{id}")
async def delete_vhost(id: UUID, request: Request, user=Depends(writer), db=Depends(session)):
    await configuration_lock(db)
    v = await get(db, m.Vhost, id)
    v.deleted_at = datetime.now(timezone.utc)
    v.enabled = False
    await db.execute(delete(m.VhostDomain).where(m.VhostDomain.vhost_id == id))
    rev = await queue_revision(db, user.id)
    audit(db, user, "DELETE_VHOST", id, request)
    return {"deployment_job_id": rev.id, "deployment_status": "QUEUED"}


class Purge(c.Model):
    paths: list[str] = Field(default_factory=list, max_length=100)
    prefix: bool = False


@router.post("/vhosts/{id}/purge")
async def purge(id: UUID, body: Purge, request: Request, user=Depends(writer), db=Depends(session)):
    await get(db, m.Vhost, id)
    job = await enqueue(
        db,
        "PURGE",
        {"vhost_id": str(id), **body.model_dump()},
        user_id=user.id,
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    audit(db, user, "PURGE", id, request)
    return output(job)


@router.get("/vhosts/{id}/logs")
async def logs(
    id: UUID, agent_id: UUID, limit: int = Query(100, ge=1, le=500), search: str = "", db=Depends(session)
):
    from urllib.parse import urlencode

    await get(db, m.Vhost, id)
    node = await get(db, m.AgentNode, agent_id)
    return await request_agent(
        db, node, "GET", "/api/v1/logs?" + urlencode({"vhost_id": str(id), "limit": limit, "search": search})
    )


class PolicyInput(c.Model):
    name: str = Field(min_length=1, max_length=100)
    config: dict
    is_default: bool = False
    deploy: bool = True


def policy_routes(resource, model, schema):
    def policy_output(row):
        value = output(row)
        value["enabled"] = bool(row.config.get("enabled", True))
        return value

    async def listing(db=Depends(session)):
        return [policy_output(v) for v in (await db.scalars(select(model).order_by(model.name))).all()]

    async def create(body: PolicyInput, request: Request, user=Depends(admin), db=Depends(session)):
        await configuration_lock(db)
        config = schema.model_validate(body.config)
        if body.is_default:
            for row in (await db.scalars(select(model))).all():
                row.is_default = False
        row = model(
            name=body.name, config=config.model_dump(), is_default=body.is_default, created_by=user.id
        )
        db.add(row)
        await revision(db, user.id, body.deploy)
        audit(db, user, "CREATE_POLICY", resource, request)
        return policy_output(row)

    async def update(id: UUID, body: PolicyInput, request: Request, user=Depends(admin), db=Depends(session)):
        await configuration_lock(db)
        row = await get(db, model, id)
        row.config = schema.model_validate(body.config).model_dump()
        row.name = body.name
        if body.is_default:
            for other in (await db.scalars(select(model))).all():
                other.is_default = False
        row.is_default = body.is_default
        await revision(db, user.id, body.deploy)
        audit(db, user, "EDIT_POLICY", id, request)
        return policy_output(row)

    async def remove(id: UUID, request: Request, user=Depends(admin), db=Depends(session)):
        await configuration_lock(db)
        row = await get(db, model, id)
        if row.is_default:
            raise HTTPException(409, "Select another default before deletion")
        await db.delete(row)
        await revision(db, user.id)
        audit(db, user, "DELETE_POLICY", id, request)
        return {"success": True}

    router.add_api_route("/" + resource, listing, methods=["GET"], name="list_" + resource)
    router.add_api_route("/" + resource, create, methods=["POST"], name="create_" + resource)
    router.add_api_route("/" + resource + "/{id}", update, methods=["PUT"], name="update_" + resource)
    router.add_api_route("/" + resource + "/{id}", remove, methods=["DELETE"], name="delete_" + resource)


for resource, (model, schema, key) in POLICIES.items():
    policy_routes(resource, model, schema)


@router.get("/jobs")
async def jobs(status: str | None = None, offset: int = Query(0, ge=0), db=Depends(session)):
    query = select(m.Job).order_by(m.Job.created_at.desc()).offset(offset).limit(100)
    if status:
        query = query.where(m.Job.status == status)
    return [output(v) for v in (await db.scalars(query)).all()]


@router.get("/jobs/{id}")
async def job(id: UUID, db=Depends(session)):
    row = await get(db, m.Job, id)
    return {
        **output(row),
        "targets": [
            output(t) for t in (await db.scalars(select(m.JobTarget).where(m.JobTarget.job_id == id))).all()
        ],
    }


@router.get("/jobs/{id}/events")
async def events(id: UUID, db=Depends(session)):
    return [
        output(v)
        for v in (
            await db.scalars(
                select(m.JobEvent).where(m.JobEvent.job_id == id).order_by(m.JobEvent.created_at).limit(1000)
            )
        ).all()
    ]


@router.post("/jobs/{id}/retry")
async def retry(id: UUID, user=Depends(writer), db=Depends(session)):
    old = await get(db, m.Job, id)
    if old.kind == "PROVISION" and user.role != "ADMIN":
        raise HTTPException(403, "Administrator required")
    targets = list(
        (
            await db.scalars(
                select(m.JobTarget.agent_id).where(m.JobTarget.job_id == id, m.JobTarget.status == "FAILED")
            )
        ).all()
    )
    return output(await enqueue(db, old.kind, old.payload, targets, user.id))


@router.get("/config-revisions")
async def revisions(db=Depends(session)):
    return [
        {"id": v.id, "hash": v.config_hash, "created_at": v.created_at}
        for v in (
            await db.scalars(
                select(m.ConfigurationRevision).order_by(m.ConfigurationRevision.id.desc()).limit(100)
            )
        ).all()
    ]


@router.get("/config-revisions/{id}")
async def revision_detail(id: int, db=Depends(session)):
    row = await get(db, m.ConfigurationRevision, id)
    return {
        "id": id,
        "hash": row.config_hash,
        "items": [
            v.config
            for v in (
                await db.scalars(
                    select(m.ConfigurationRevisionItem).where(m.ConfigurationRevisionItem.revision_id == id)
                )
            ).all()
        ],
    }


@router.post("/config-revisions/{id}/rollback")
async def rollback(id: int, request: Request, user=Depends(admin), db=Depends(session)):
    await configuration_lock(db)
    row = await restore_revision(db, await get(db, m.ConfigurationRevision, id), user.id)
    audit(db, user, "ROLLBACK", id, request)
    return {"revision": row.id}


@router.post("/sync")
async def sync(user=Depends(writer), db=Depends(session)):
    return output(await enqueue(db, "VHOST_SYNC", user_id=user.id))


@router.post("/sync-services")
async def sync_services(user=Depends(admin), db=Depends(session)):
    """Reconcile runtime services separately from the vhost desired state."""
    return output(await enqueue(db, "SERVICE_SYNC", user_id=user.id))


@router.get("/audit")
async def audit_log(offset: int = Query(0, ge=0), db=Depends(session)):
    return [
        output(v)
        for v in (
            await db.scalars(
                select(m.AuditLog).order_by(m.AuditLog.created_at.desc()).offset(offset).limit(100)
            )
        ).all()
    ]


class CertificateInput(c.Model):
    name: str = Field(min_length=1, max_length=100)
    source: Literal["manual", "certbot"] = "manual"
    challenge: Literal["dns-01", "http-01"] = "dns-01"
    certificate: str = ""
    private_key: str = Field(default="", repr=False)
    domains: list[str] = Field(default_factory=list, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    auto_renew: bool = True
    renew_before_days: int = Field(default=30, ge=7, le=60)

    @field_validator("domains")
    @classmethod
    def valid_domains(cls, values):
        return c.Vhost.domains_valid(values) if values else values

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("Enter a valid ACME account email")
        return value

    @model_validator(mode="after")
    def required_material(self):
        if self.source == "manual" and (not self.certificate or not self.private_key):
            raise ValueError("Manual certificates require certificate and private-key PEM")
        if self.source == "certbot" and (not self.domains or not self.email):
            raise ValueError("Certbot certificates require at least one domain and an account email")
        if self.source == "certbot" and self.challenge == "http-01" and any(
            domain.startswith("*.") for domain in self.domains
        ):
            raise ValueError("Wildcard certificates require DNS-01 validation")
        return self


@router.get("/certificates")
async def certificates(db=Depends(session)):
    return [output(v) for v in (await db.scalars(select(m.Certificate))).all()]


@router.post("/certificates")
async def certificate(body: CertificateInput, request: Request, user=Depends(admin), db=Depends(session)):
    try:
        certificate_pem, private_key = body.certificate, body.private_key
        if body.source == "certbot":
            if not body.email:
                raise ValueError("Email is required for Certbot")
            from app.services.certificates import issue
            from app.services.dns import dns_config

            dns = await dns_config(db) if body.challenge == "dns-01" else None
            certificate_pem, private_key = await issue(body.domains, body.email, dns, body.challenge)
        from app.services.certificates import validate_pair

        cert, cert_domains = validate_pair(certificate_pem, private_key)
        if cert.not_valid_after_utc <= datetime.now(timezone.utc):
            raise ValueError("Expired")
        if body.source == "certbot" and set(body.domains) - set(cert_domains):
            raise ValueError("Issued certificate does not cover every requested domain")
    except Exception as exc:
        detail = str(exc) if str(exc).startswith("CERTBOT_FAILED:") else "CERTIFICATE_INVALID"
        raise HTTPException(422, detail) from None
    row = m.Certificate(
        name=body.name,
        certificate=certificate_pem,
        encrypted_key=encrypt(private_key),
        expires_at=cert.not_valid_after_utc,
        domains=cert_domains,
        source=body.source,
        challenge=body.challenge,
        auto_renew=body.source == "certbot" and body.auto_renew,
        renew_before_days=body.renew_before_days,
        email=body.email if body.source == "certbot" else None,
        status="READY",
        created_by=user.id,
    )
    db.add(row)
    await db.flush()
    audit(db, user, "UPLOAD_CERTIFICATE", row.id, request)
    return output(row)


@router.post("/certificates/{id}/renew")
async def renew_certificate(id: UUID, request: Request, user=Depends(admin), db=Depends(session)):
    row = await get(db, m.Certificate, id)
    if row.source != "certbot" or not row.email:
        raise HTTPException(409, "Only Certbot-managed certificates can be renewed")
    from app.services.certificates import issue, validate_pair
    from app.services.dns import dns_config

    try:
        dns = await dns_config(db) if row.challenge == "dns-01" else None
        certificate_pem, private_key = await issue(row.domains, row.email, dns, row.challenge)
        cert, domains = validate_pair(certificate_pem, private_key)
    except Exception as exc:
        row.status = "FAILED"
        raise HTTPException(422, str(exc)) from None
    row.certificate = certificate_pem
    row.encrypted_key = encrypt(private_key)
    row.expires_at = cert.not_valid_after_utc
    row.domains = domains
    row.status = "READY"
    await revision(db, user.id, True)
    audit(db, user, "RENEW_CERTIFICATE", row.id, request)
    return output(row)


class UserInput(c.Model):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(pattern="^(ADMIN|OPERATOR|VIEWER)$")
    section_permissions: list[str] = Field(default_factory=list)

    @field_validator("section_permissions")
    @classmethod
    def sections_valid(cls, values):
        if set(values) - SECTIONS:
            raise ValueError("Unknown application section")
        return sorted(set(values))


@router.get("/users")
async def users(user=Depends(admin), db=Depends(session)):
    return [output(v) for v in (await db.scalars(select(m.User))).all()]


@router.post("/users")
async def add_user(body: UserInput, request: Request, user=Depends(admin), db=Depends(session)):
    row = m.User(
        username=body.username,
        password_hash=passwords.hash(body.password),
        role=body.role,
        section_permissions=body.section_permissions,
        created_by=user.id,
    )
    db.add(row)
    await db.flush()
    audit(db, user, "CREATE_USER", row.id, request)
    return output(row)


@router.get("/settings")
async def settings_view(user=Depends(admin), db=Depends(session)):
    from app.services.runtime_settings import get_group
    from app.services.dns import dns_config
    country = Path(settings.maxmind_country_db)
    city = Path(settings.maxmind_city_db)
    geoip = await get_group(db, "geoip")
    monitoring = await get_group(db, "analytics")
    dns = await dns_config(db)
    return {
        "environment": settings.environment,
        "cdn_zone": dns.get("zone"),
        "dns_ttl": dns.get("ttl"),
        "dns_configured": bool(dns.get("api_url")),
        "health_failures": settings.health_failures,
        "health_successes": settings.health_successes,
        "bgp_enabled": settings.bgp_enabled,
        "monitoring": monitoring,
        "maxmind": {
            **geoip,
            "country_available": country.is_file(),
            "city_available": city.is_file(),
            "country_bytes": country.stat().st_size if country.is_file() else 0,
            "city_bytes": city.stat().st_size if city.is_file() else 0,
        },
    }


class OperationalSettingsInput(c.Model):
    country_enabled: bool = True
    city_enabled: bool = True
    monitoring_mode: Literal["full", "metrics_only", "off"] = "full"


@router.put("/settings")
async def update_settings(body: OperationalSettingsInput, request: Request, user=Depends(admin), db=Depends(session)):
    from app.services.runtime_settings import put_group
    await configuration_lock(db)
    await put_group(db, "geoip", {"country_enabled": body.country_enabled, "city_enabled": body.city_enabled})
    await put_group(db, "analytics", {"mode": body.monitoring_mode})
    audit(db, user, "UPDATE_SYSTEM_SETTINGS", "runtime", request)
    return {"success": True}


@router.get("/dns")
async def dns_records(db=Depends(session)):
    return [output(v) for v in (await db.scalars(select(m.DNSRecord))).all()]


@router.get("/dns/pop-sites")
async def dns_pop_sites(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=500),
    db=Depends(session),
):
    rows = (
        await db.scalars(
            select(m.AgentNode)
            .where(m.AgentNode.demo.is_(False))
            .order_by(m.AgentNode.country, m.AgentNode.city, m.AgentNode.name)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [
        {
            key: getattr(row, key)
            for key in (
                "id",
                "name",
                "city",
                "country",
                "public_ipv4",
                "public_ipv6",
                "status",
                "active",
                "maintenance",
                "dns_eligible",
            )
        }
        for row in rows
    ]


@router.get("/dns/records")
async def provider_dns_records(user=Depends(admin), db=Depends(session)):
    from app.services.dns import list_records

    return await list_records(db)


class DNSRecordInput(c.Model):
    name: str = Field(min_length=1, max_length=253)
    type: Literal["A", "AAAA", "CNAME", "TXT", "CAA", "MX", "NS", "SRV"]
    ttl: int = Field(default=60, ge=30, le=86400)
    values: list[str] = Field(min_length=1, max_length=100)


@router.put("/dns/records")
async def put_dns_record(
    body: DNSRecordInput, request: Request, user=Depends(admin), db=Depends(session)
):
    from app.services.dns import change_record

    try:
        result = await change_record(db, body.name, body.type, body.ttl, body.values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, user, "UPSERT_DNS_RECORD", f"{body.name}/{body.type}", request)
    return result


@router.delete("/dns/records")
async def delete_dns_record(
    name: str, type: str, request: Request, user=Depends(admin), db=Depends(session)
):
    from app.services.dns import change_record

    try:
        result = await change_record(db, name, type, settings.dns_ttl, delete=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, user, "DELETE_DNS_RECORD", f"{name}/{type}", request)
    return result


@router.post("/dns/reconcile")
async def dns_reconcile(user=Depends(admin), db=Depends(session)):
    return output(await enqueue(db, "DNS", node_ids=[None], user_id=user.id))


@router.get("/vhosts/{id}/dns-verify")
async def dns_verify(id: UUID, db=Depends(session)):
    import dns.asyncresolver

    v = await get(db, m.Vhost, id)
    domains = list((await db.scalars(select(m.VhostDomain.domain).where(m.VhostDomain.vhost_id == id))).all())
    result = []
    for domain in domains:
        detected = False
        try:
            answers = await dns.asyncresolver.resolve(domain, "CNAME", lifetime=3)
            detected = any(str(a.target).rstrip(".") == v.cdn_hostname for a in answers)
        except Exception:
            pass
        result.append(
            {
                "domain": domain,
                "status": "DNS detected" if detected else "DNS pending",
                "target": v.cdn_hostname,
                "instructions": "Use CNAME for a subdomain; at the zone apex use A/AAAA or provider ALIAS/ANAME.",
            }
        )
    return result


@router.post("/agents/{id}/origin/test")
async def origin_test(id: UUID, body: c.Origin, request: Request, user=Depends(writer), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    audit(db, user, "TEST_ORIGIN", id, request)
    return await request_agent(db, node, "POST", "/api/v1/origin/test", body.model_dump())


@router.get("/config-revisions/{id}/preview")
async def preview(id: int, agent_id: UUID, user=Depends(admin), db=Depends(session)):
    from app.core.security import decrypt
    import json

    rev = await get(db, m.ConfigurationRevision, id)
    node = await get(db, m.AgentNode, agent_id)
    result = await request_agent(
        db, node, "POST", "/api/v1/config/validate", json.loads(decrypt(rev.encrypted_bundle))
    )
    return redact(result)


@router.post("/vhosts/{id}/clone")
async def clone(id: UUID, body: VhostInput, request: Request, user=Depends(writer), db=Depends(session)):
    await get(db, m.Vhost, id)
    return await add_vhost(body, request, user, db)


@router.post("/vhosts/{id}/sync")
async def sync_vhost(id: UUID, user=Depends(writer), db=Depends(session)):
    await get(db, m.Vhost, id)
    return output(await enqueue(db, "SYNC", user_id=user.id))


class DNSConfigInput(c.Model):
    api_url: str
    api_key: str = Field(default="", repr=False)
    auth_mode: Literal["api_key", "basic"] = "api_key"
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", repr=False)
    server_id: str = Field(default="localhost", pattern=r"^[A-Za-z0-9_-]+$")
    zone: str
    ttl: int = Field(default=60, ge=30, le=86400)
    anycast_ipv4: list[str] = Field(default_factory=list, max_length=32)
    anycast_ipv6: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("zone")
    @classmethod
    def zone_valid(cls, v):
        return c.hostname(v)

    @field_validator("api_url")
    @classmethod
    def api_url_valid(cls, v):
        parsed = urlparse(v)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected an HTTP(S) PowerDNS API URL")
        return v.rstrip("/")

    @field_validator("anycast_ipv4", "anycast_ipv6")
    @classmethod
    def anycast_valid(cls, values, info):
        version = 4 if info.field_name == "anycast_ipv4" else 6
        result = [str(ipaddress.ip_address(value)) for value in values]
        if any(ipaddress.ip_address(value).version != version for value in result):
            raise ValueError(f"Expected IPv{version} addresses")
        return sorted(set(result))


@router.put("/settings/dns")
async def configure_dns(body: DNSConfigInput, request: Request, user=Depends(admin), db=Depends(session)):
    await configuration_lock(db)
    row = await db.scalar(select(m.DNSSetting).limit(1))
    if not row:
        row = m.DNSSetting(config={})
        db.add(row)
    from app.core.security import decrypt
    import json

    secrets = {}
    if row.encrypted_key:
        raw = decrypt(row.encrypted_key)
        try:
            secrets = json.loads(raw)
        except ValueError:
            secrets = {"api_key": raw}
    secrets.update(
        {
            key: value
            for key, value in body.model_dump(include={"api_key", "username", "password"}).items()
            if value
        }
    )
    if body.auth_mode == "api_key" and not secrets.get("api_key"):
        raise HTTPException(422, "PowerDNS API key required")
    if body.auth_mode == "basic" and not secrets.get("password"):
        raise HTTPException(422, "Basic authentication password required")
    row.config = body.model_dump(exclude={"api_key", "username", "password"})
    row.encrypted_key = encrypt(json.dumps(secrets))
    audit(db, user, "CONFIGURE_DNS", "dns_settings", request)
    return {"success": True}


@router.get("/vhosts/{id}")
async def get_vhost(id: UUID, db=Depends(session)):
    return await vhost_output(db, await get(db, m.Vhost, id))


@router.put("/agents/{id}")
async def edit_node(id: UUID, body: NodeInput, request: Request, user=Depends(admin), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    if node.hostname != body.hostname:
        credential = await db.scalar(select(m.AgentCredential).where(m.AgentCredential.agent_id == id))
        if credential:
            credential.host_key = None
    for key, value in body.model_dump(exclude={"labels"}).items():
        setattr(node, key, value)
    await db.execute(delete(m.AgentLabel).where(m.AgentLabel.agent_id == id))
    for key, value in body.labels.items():
        db.add(m.AgentLabel(agent_id=id, key=key, value=value))
    audit(db, user, "EDIT_NODE", id, request)
    # The bulk label delete and server-managed updated_at can expire attributes.
    # Resolve those values through the async session before synchronous output()
    # walks the mapped columns; otherwise SQLAlchemy attempts implicit async IO
    # and raises MissingGreenlet.
    await db.flush()
    await db.refresh(node)
    return output(node)


@router.delete("/agents/{id}")
async def delete_node(id: UUID, request: Request, user=Depends(admin), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    target_ids = select(m.JobTarget.id).where(m.JobTarget.agent_id == id)
    await db.execute(delete(m.JobEvent).where(m.JobEvent.target_id.in_(target_ids)))
    await db.execute(delete(m.JobTarget).where(m.JobTarget.agent_id == id))
    for model in (
        m.TrafficBucket,
        m.TrafficCounter,
        m.AgentHealthHistory,
        m.AgentActivity,
        m.AgentLabel,
        m.AgentCredential,
        m.AgentConfigState,
    ):
        await db.execute(delete(model).where(model.agent_id == id))
    audit(db, user, "DELETE_NODE", id, request)
    await db.delete(node)
    return {"success": True, "deleted": True}


@router.post("/agents/{id}/disable")
async def disable_node(id: UUID, request: Request, user=Depends(admin), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    node.active = False
    node.dns_eligible = False
    node.status = "DISABLED"
    audit(db, user, "DISABLE_NODE", id, request)
    return {"success": True, "history_retained": True}


@router.post("/agents/{id}/activate")
async def activate_node(id: UUID, request: Request, user=Depends(admin), db=Depends(session)):
    node = await get(db, m.AgentNode, id)
    node.active = True
    node.dns_eligible = False
    node.status = "OFFLINE"
    node.failures = 0
    node.successes = 0
    node_activity(db, node, "node", "ACTIVATED", "Node activated; awaiting a successful health check", user)
    audit(db, user, "ACTIVATE_NODE", id, request)
    return output(node)


class UserUpdate(c.Model):
    role: str = Field(pattern="^(ADMIN|OPERATOR|VIEWER)$")
    active: bool = True
    section_permissions: list[str] = Field(default_factory=list)

    @field_validator("section_permissions")
    @classmethod
    def sections_valid(cls, values):
        if set(values) - SECTIONS:
            raise ValueError("Unknown application section")
        return sorted(set(values))


@router.put("/users/{id}")
async def update_user(id: UUID, body: UserUpdate, request: Request, user=Depends(admin), db=Depends(session)):
    if id == user.id and (
        not body.active
        or body.role != "ADMIN"
        or (body.section_permissions and "users" not in body.section_permissions)
    ):
        raise HTTPException(409, "Cannot remove your own administrator access")
    row = await get(db, m.User, id)
    row.role = body.role
    row.active = body.active
    row.section_permissions = body.section_permissions
    await db.execute(delete(m.LoginSession).where(m.LoginSession.user_id == id))
    audit(db, user, "UPDATE_USER", id, request)
    return output(row)
