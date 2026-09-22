import ipaddress
from pathlib import Path
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Literal
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import select, delete
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from app.api.auth import current_user, role, section_access, SECTIONS
from app.db.session import session
from app.models import entities as m
from app.schemas import config as c
from app.services.config import POLICIES, configuration_lock, revision, latest, enqueue, restore_revision
from app.services.agents import request_agent
from app.core.security import encrypt, redact, passwords
from app.core.settings import settings

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
    ids = [row.id for row in rows]
    states = {
        s.agent_id: s
        for s in (
            await db.scalars(select(m.AgentConfigState).where(m.AgentConfigState.agent_id.in_(ids)))
        ).all()
    }
    labels_by_node = {}
    for label in (await db.scalars(select(m.AgentLabel).where(m.AgentLabel.agent_id.in_(ids)))).all():
        labels_by_node.setdefault(label.agent_id, {})[label.key] = label.value
    for row in rows:
        state = states.get(row.id)
        result.append(
            {
                **output(row),
                "applied_revision": state.revision if state else 0,
                "desired_revision": desired.id if desired else 0,
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
        "sync": "SYNC",
        "provision": "PROVISION",
        "reprovision": "PROVISION",
        "reprovision-auto-approve": "PROVISION",
        "upgrade": "PROVISION",
        "validate": "VALIDATE",
        "reload": "RELOAD",
        "test-origin": "TEST_ORIGIN",
    }
    if action not in mapping:
        raise HTTPException(404, "Unknown action")
    if action == "reload" and node.status not in {"READY", "DEGRADED"}:
        raise HTTPException(409, "Provision and connect the agent before reloading NGINX")
    if action in {"provision", "reprovision", "reprovision-auto-approve", "upgrade"} and user.role != "ADMIN":
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
            "geo",
            "analytics",
        }
        if set(value) - allowed:
            raise ValueError("Options may only contain typed traffic/security settings")
        return value

    @field_validator("domains")
    @classmethod
    def domains_valid(cls, value):
        return c.Vhost.domains_valid(value)


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
    return {
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
    return [{**output(v), "domains": domains.get(v.id, []), "origins": origins.get(v.id, [])} for v in rows]


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
    rev = await revision(db, user.id, body.deploy)
    audit(db, user, "CREATE_VHOST", id, request)
    return {**await vhost_output(db, v), "revision": rev.id}


@router.put("/vhosts/{id}")
async def edit_vhost(id: UUID, body: VhostInput, request: Request, user=Depends(writer), db=Depends(session)):
    await configuration_lock(db)
    v = await get(db, m.Vhost, id)
    await write_vhost(db, body, v)
    rev = await revision(db, user.id, body.deploy)
    audit(db, user, "EDIT_VHOST", id, request)
    return {**await vhost_output(db, v), "revision": rev.id}


@router.delete("/vhosts/{id}")
async def delete_vhost(id: UUID, request: Request, user=Depends(writer), db=Depends(session)):
    await configuration_lock(db)
    v = await get(db, m.Vhost, id)
    v.deleted_at = datetime.now(timezone.utc)
    v.enabled = False
    await db.execute(delete(m.VhostDomain).where(m.VhostDomain.vhost_id == id))
    rev = await revision(db, user.id)
    audit(db, user, "DELETE_VHOST", id, request)
    return {"revision": rev.id}


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
    return output(await enqueue(db, "SYNC", user_id=user.id))


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
    name: str
    certificate: str
    private_key: str = Field(repr=False)


@router.get("/certificates")
async def certificates(db=Depends(session)):
    return [output(v) for v in (await db.scalars(select(m.Certificate))).all()]


@router.post("/certificates")
async def certificate(body: CertificateInput, request: Request, user=Depends(admin), db=Depends(session)):
    try:
        cert = x509.load_pem_x509_certificate(body.certificate.encode())
        key = serialization.load_pem_private_key(body.private_key.encode(), None)
        if key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) != cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ):
            raise ValueError("Key mismatch")
        if cert.not_valid_after_utc <= datetime.now(timezone.utc):
            raise ValueError("Expired")
        domains = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
    except Exception:
        raise HTTPException(422, "CERTIFICATE_INVALID") from None
    row = m.Certificate(
        name=body.name,
        certificate=body.certificate,
        encrypted_key=encrypt(body.private_key),
        expires_at=cert.not_valid_after_utc,
        domains=domains,
        created_by=user.id,
    )
    db.add(row)
    await db.flush()
    audit(db, user, "UPLOAD_CERTIFICATE", row.id, request)
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
async def settings_view(user=Depends(admin)):
    maxmind = Path(settings.maxmind_country_db)
    return {
        "environment": settings.environment,
        "cdn_zone": settings.powerdns_cdn_zone,
        "dns_ttl": settings.dns_ttl,
        "dns_configured": bool(settings.powerdns_api_url),
        "health_failures": settings.health_failures,
        "health_successes": settings.health_successes,
        "bgp_enabled": settings.bgp_enabled,
        "maxmind": {
            "available": maxmind.is_file(),
            "database": "GeoLite2-Country",
            "bytes": maxmind.stat().st_size if maxmind.is_file() else 0,
            "provisioned_to_real_agents": maxmind.is_file(),
        },
    }


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
