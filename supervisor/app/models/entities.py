import uuid
from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Record:
    # Fetch server-generated update timestamps during the awaited flush. Async
    # response serialization must never trigger an implicit database query.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="VIEWER")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    section_permissions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")


class LoginSession(Record, Base):
    __tablename__ = "login_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentNode(Record, Base):
    __tablename__ = "agent_nodes"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    hostname: Mapped[str] = mapped_column(String(253))
    management_url: Mapped[str] = mapped_column(String(500))
    public_ipv4: Mapped[str | None] = mapped_column(String(45), nullable=True)
    public_ipv6: Mapped[str | None] = mapped_column(String(45), nullable=True)
    city: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(100), default="")
    provider: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="PROVISIONING")
    failures: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    dns_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentLabel(Record, Base):
    __tablename__ = "agent_labels"
    __table_args__ = (UniqueConstraint("agent_id", "key"),)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"))
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(200))


class AgentCredential(Record, Base):
    __tablename__ = "agent_credentials"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"), unique=True)
    encrypted: Mapped[str] = mapped_column(Text)
    host_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentHealthHistory(Record, Base):
    __tablename__ = "agent_health_history"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"), index=True)
    observed: Mapped[dict] = mapped_column(JSONB)


class AgentActivity(Record, Base):
    __tablename__ = "agent_activities"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"), index=True)
    stage: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)


class AgentConfigState(Record, Base):
    __tablename__ = "agent_config_state"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"), unique=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PolicyMixin(Record):
    name: Mapped[str] = mapped_column(String(100), unique=True)
    config: Mapped[dict] = mapped_column(JSONB)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class CachePolicy(PolicyMixin, Base):
    __tablename__ = "cache_policies"


class RateLimitPolicy(PolicyMixin, Base):
    __tablename__ = "rate_limit_policies"


class RealIPPolicy(PolicyMixin, Base):
    __tablename__ = "real_ip_policies"


class HeaderPolicy(PolicyMixin, Base):
    __tablename__ = "header_policies"


class Certificate(Record, Base):
    __tablename__ = "certificates"
    name: Mapped[str] = mapped_column(String(100))
    certificate: Mapped[str] = mapped_column(Text)
    encrypted_key: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    domains: Mapped[list] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    challenge: Mapped[str] = mapped_column(String(20), default="dns-01")
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    renew_before_days: Mapped[int] = mapped_column(Integer, default=30)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="READY")


class Vhost(Record, Base):
    __tablename__ = "vhosts"
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cdn_hostname: Mapped[str] = mapped_column(String(253), unique=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    cache_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cache_policies.id"), nullable=True)
    rate_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_limit_policies.id"), nullable=True
    )
    real_ip_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("real_ip_policies.id"), nullable=True
    )
    header_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("header_policies.id"), nullable=True
    )
    certificate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)


class VhostDomain(Record, Base):
    __tablename__ = "vhost_domains"
    vhost_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vhosts.id"))
    domain: Mapped[str] = mapped_column(String(253), unique=True)


class Origin(Record, Base):
    __tablename__ = "origins"
    vhost_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vhosts.id"))
    position: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONB)


class ConfigurationRevision(Base):
    __tablename__ = "configuration_revisions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    encrypted_bundle: Mapped[str] = mapped_column(Text)


class ConfigurationRevisionItem(Record, Base):
    __tablename__ = "configuration_revision_items"
    revision_id: Mapped[int] = mapped_column(ForeignKey("configuration_revisions.id"))
    vhost_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    config: Mapped[dict] = mapped_column(JSONB)


class Job(Record, Base):
    __tablename__ = "jobs"
    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)


class JobTarget(Record, Base):
    __tablename__ = "job_targets"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_nodes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class JobEvent(Record, Base):
    __tablename__ = "job_events"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_targets.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(Text)


class DNSSetting(Record, Base):
    __tablename__ = "dns_settings"
    config: Mapped[dict] = mapped_column(JSONB)
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)


class DNSRecord(Record, Base):
    __tablename__ = "dns_records"
    __table_args__ = (UniqueConstraint("name", "type"),)
    name: Mapped[str] = mapped_column(String(253))
    type: Mapped[str] = mapped_column(String(10))
    values: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")


class AuditLog(Record, Base):
    __tablename__ = "audit_logs"
    action: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(100))
    source_ip: Mapped[str] = mapped_column(String(100), default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class SystemSetting(Record, Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[dict] = mapped_column(JSONB)


class Customer(Record, Base):
    __tablename__ = "customers"
    name: Mapped[str] = mapped_column(String(150))
    external_id: Mapped[str | None] = mapped_column(String(150), unique=True, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class APIToken(Record, Base):
    __tablename__ = "api_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class TrafficCounter(Base):
    __tablename__ = "traffic_counters"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"), primary_key=True)
    vhost_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vhosts.id"), primary_key=True)
    country: Mapped[str] = mapped_column(String(2), primary_key=True)
    epoch: Mapped[str] = mapped_column(String(36))
    observed: Mapped[dict] = mapped_column(JSONB)
    totals: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrafficBucket(Base):
    __tablename__ = "traffic_buckets"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_nodes.id"), primary_key=True)
    vhost_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vhosts.id"), primary_key=True)
    country: Mapped[str] = mapped_column(String(2), primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    requests: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_received: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_hits: Mapped[int] = mapped_column(BigInteger, default=0)
    errors: Mapped[int] = mapped_column(BigInteger, default=0)
