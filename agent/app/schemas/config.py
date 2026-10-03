"""Version 1 desired-state contract. Never accept raw server directives."""

import hashlib
import ipaddress
import json
import re
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer


def hostname(value: str) -> str:
    value = value.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if len(value) > 253 or not re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value
    ):
        raise ValueError("Invalid hostname")
    return value


def header_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", value):
        raise ValueError("Invalid header name")
    if value.lower().startswith("x-cdn-") or value.lower() in {
        "connection",
        "upgrade",
        "transfer-encoding",
        "content-length",
        "host",
        "authorization",
        "cookie",
        "set-cookie",
    }:
        raise ValueError("Reserved header")
    return value


def safe_text(value: str) -> str:
    if len(value) > 1024 or any(c in value for c in '\r\n\x00$\\"{};') or any(ord(c) < 32 for c in value):
        raise ValueError("Unsafe configuration value")
    return value


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Origin(Model):
    host: str
    port: int = Field(default=80, ge=1, le=65535)
    scheme: Literal["http", "https"] = "http"
    host_header: str | None = None
    sni: str | None = None
    # Verification is opt-in because CDN origins are often addressed by IP.
    tls_verify: bool = False
    weight: int = Field(default=1, ge=1, le=1000)
    backup: bool = False
    max_fails: int = Field(default=3, ge=1, le=100)
    fail_timeout: int = Field(default=10, ge=1, le=3600)
    connect_timeout: int = Field(default=5, ge=1, le=120)
    read_timeout: int = Field(default=60, ge=1, le=600)
    send_timeout: int = Field(default=60, ge=1, le=600)
    keepalive: int = Field(default=32, ge=1, le=1024)
    _host = field_validator("host")(hostname)

    @field_validator("host_header", "sni")
    @classmethod
    def optional_host(cls, v):
        return hostname(v) if v else None


class CachePolicy(Model):
    enabled: bool = True
    default_ttl: int = Field(default=120, ge=0, le=31536000)
    static_ttl: int = Field(default=3600, ge=0, le=31536000)
    max_ttl: int = Field(default=86400, ge=0, le=31536000)
    grace: int = Field(default=300, ge=0, le=86400)
    keep: int = Field(default=60, ge=0, le=86400)
    ignore_origin_ttl: bool = False
    query_string: bool = True
    ignored_query_parameters: list[str] = Field(default_factory=list, max_length=32)
    bypass_paths: list[str] = Field(default_factory=list, max_length=64)
    bypass_cookies: list[str] = Field(default_factory=list, max_length=32)
    bypass_headers: list[str] = Field(default_factory=list, max_length=32)
    statuses: list[int] = Field(default_factory=lambda: [200, 203, 301, 404], min_length=1, max_length=16)
    static_extensions: list[str] = Field(
        default_factory=lambda: (
            "css js jpg jpeg png gif webp avif svg ico woff woff2 ttf eot mp4 webm pdf zip".split()
        )
    )

    @field_validator("bypass_paths", "bypass_cookies", "ignored_query_parameters", "static_extensions")
    @classmethod
    def patterns(cls, vs):
        for v in vs:
            if not re.fullmatch(r"[/A-Za-z0-9_.-]{1,128}", v):
                raise ValueError("Use literal path prefixes or simple names, not raw regex")
        return vs

    @field_validator("bypass_headers")
    @classmethod
    def headers(cls, vs):
        return [header_name(v) for v in vs]

    @field_validator("statuses")
    @classmethod
    def status(cls, vs):
        if any(v < 200 or v > 599 for v in vs):
            raise ValueError("Invalid status")
        return vs


class PathRate(Model):
    path: str = Field(pattern=r"^/[A-Za-z0-9_/.-]{0,127}$")
    rate: int = Field(default=10, ge=1, le=100000)
    burst: int = Field(default=20, ge=1, le=100000)


class RatePolicy(Model):
    enabled: bool = True
    rate: int = Field(default=200, ge=1, le=100000)
    burst: int = Field(default=400, ge=1, le=100000)
    nodelay: bool = True
    dry_run: bool = False
    status: int = Field(default=429, ge=400, le=599)
    exemptions: list[str] = Field(default_factory=list, max_length=128)
    paths: list[PathRate] = Field(default_factory=list, max_length=32)

    @field_validator("exemptions")
    @classmethod
    def networks(cls, values):
        return [str(ipaddress.ip_network(v, strict=False)) for v in values]

    key: Literal["ip", "header"] = "ip"
    header: str = "X-API-Key"
    _header = field_validator("header")(header_name)


class RealIPPolicy(Model):
    trusted_cidrs: list[str] = Field(default_factory=list, max_length=128)
    header: str = "X-Forwarded-For"
    recursive: bool = True
    forward_to_origin: bool = False
    _header = field_validator("header")(header_name)

    @field_validator("trusted_cidrs")
    @classmethod
    def cidrs(cls, vs):
        result = []
        for v in vs:
            net = ipaddress.ip_network(v, strict=False)
            if net.prefixlen == 0:
                raise ValueError("Trusting the entire internet is forbidden")
            result.append(str(net))
        return result


class HeaderPolicy(Model):
    request: dict[str, str] = Field(default_factory=dict)
    response: dict[str, str] = Field(
        default_factory=lambda: {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }
    )
    debug: bool = True

    @field_validator("request", "response")
    @classmethod
    def headers(cls, vs):
        if len(vs) > 32:
            raise ValueError("Too many headers")
        return {header_name(k): safe_text(v) for k, v in vs.items()}


class TLS(Model):
    certificate: str
    private_key: str = Field(repr=False)


class GeographicPolicy(Model):
    mode: Literal["off", "allow", "deny"] = "off"
    countries: list[str] = Field(default_factory=list, max_length=250)
    city_ids: list[int] = Field(default_factory=list, max_length=500)
    block_unknown: bool = False

    @field_validator("countries")
    @classmethod
    def valid_countries(cls, values):
        if any(not re.fullmatch(r"[A-Z]{2}", value) for value in values):
            raise ValueError("Use uppercase ISO country codes")
        return sorted(set(values))

    @field_validator("city_ids")
    @classmethod
    def valid_cities(cls, values):
        if any(value < 1 or value > 100000000 for value in values):
            raise ValueError("Use MaxMind GeoNames city IDs")
        return sorted(set(values))

    @model_validator(mode="after")
    def require_rules(self):
        if self.mode != "off" and not self.countries and not self.city_ids and not self.block_unknown:
            raise ValueError("Select at least a country/city or block unknown locations")
        return self


class PathAccessRule(Model):
    path: str = Field(pattern=r"^/[A-Za-z0-9_/.-]{0,255}$")
    match: Literal["exact", "prefix"] = "prefix"
    action: Literal["deny", "allow_ips"] = "deny"
    cidrs: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("cidrs")
    @classmethod
    def valid_cidrs(cls, values):
        return [str(ipaddress.ip_network(value, strict=False)) for value in values]

    @model_validator(mode="after")
    def require_network(self):
        if self.action == "allow_ips" and not self.cidrs:
            raise ValueError("An IP allow rule needs at least one network")
        return self


class RedirectRule(Model):
    path: str = Field(pattern=r"^/[A-Za-z0-9_/-]{0,255}$")
    match: Literal["exact", "prefix"] = "exact"
    target: str = Field(pattern=r"^https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/%?=&+-]*)?$", max_length=1024)
    status: Literal[303] = 303


class OriginRoute(Model):
    path: str = Field(pattern=r"^/[A-Za-z0-9_/.-]{1,255}$")
    match: Literal["exact", "prefix"] = "prefix"
    origin_index: int = Field(ge=0, le=15)


class AnalyticsPolicy(Model):
    mode: Literal["off", "metrics", "full"] = "full"
    geography: bool = False


class WAFPolicy(Model):
    mode: Literal["off", "detection", "blocking"] = "off"
    profile: Literal["low", "standard", "high", "custom"] = "standard"
    crs_version: str = Field(default="", pattern=r"^(|4\.[0-9]+\.[0-9]+)$")
    paranoia_level: int = Field(default=1, ge=1, le=4)
    inbound_threshold: int = Field(default=5, ge=1, le=100)
    excluded_rule_ids: list[int] = Field(default_factory=list, max_length=128)

    @field_validator("excluded_rule_ids")
    @classmethod
    def valid_rule_ids(cls, values):
        if any(value < 900000 or value > 999999 for value in values):
            raise ValueError("Only CRS rule IDs 900000–999999 may be excluded")
        return sorted(set(values))

    @model_validator(mode="after")
    def pinned_rules(self):
        if self.mode != "off" and not self.crs_version:
            raise ValueError("An installed, pinned CRS v4 version is required")
        return self


class Vhost(Model):
    id: UUID
    customer_id: UUID | None = None
    geo: GeographicPolicy = Field(default_factory=GeographicPolicy)
    analytics: AnalyticsPolicy = Field(default_factory=AnalyticsPolicy)
    path_rules: list[PathAccessRule] = Field(default_factory=list, max_length=128)
    redirect_rules: list[RedirectRule] = Field(default_factory=list, max_length=128)
    origin_routes: list[OriginRoute] = Field(default_factory=list, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    domains: list[str] = Field(min_length=1, max_length=100)
    origins: list[Origin] = Field(min_length=1, max_length=16)
    enabled: bool = True
    cache: CachePolicy = Field(default_factory=CachePolicy)
    rate: RatePolicy = Field(default_factory=RatePolicy)
    real_ip: RealIPPolicy = Field(default_factory=RealIPPolicy)
    headers: HeaderPolicy = Field(default_factory=HeaderPolicy)
    tls: TLS | None = None
    tls_mode: Literal["auto", "http_only", "passthrough", "terminate"] = "auto"
    waf: WAFPolicy = Field(default_factory=WAFPolicy)
    redirect_https: bool = False
    websocket: bool = True
    logging: bool = True
    max_body_mb: int = Field(default=32, ge=1, le=10240)
    client_timeout: int = Field(default=30, ge=1, le=300)
    allowed_methods: list[Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]] = Field(
        default_factory=list
    )
    ip_allow: list[str] = Field(default_factory=list, max_length=128)
    ip_deny: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("ip_allow", "ip_deny")
    @classmethod
    def networks(cls, values):
        return [str(ipaddress.ip_network(v, strict=False)) for v in values]

    blocked_paths: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def wire_config(self, handler):
        data = handler(self)
        # Preserve existing revision/vhost hashes and compatibility with agents
        # predating WAF. Enabled policies require explicit capability negotiation.
        if self.waf.mode == "off":
            data.pop("waf", None)
        return data

    def digest(self):
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @field_validator("domains")
    @classmethod
    def domains_valid(cls, vs):
        result = [("*." + hostname(v[2:])) if v.startswith("*.") else hostname(v) for v in vs]
        if len(set(result)) != len(result):
            raise ValueError("Duplicate domains")
        return result

    @field_validator("blocked_paths")
    @classmethod
    def paths(cls, vs):
        return CachePolicy.patterns(vs)

    @model_validator(mode="after")
    def pool(self):
        first = self.origins[0]
        if self.tls_mode == "passthrough" and any(o.scheme != "https" for o in self.origins):
            raise ValueError("TLS passthrough requires HTTPS origins; use POP TLS termination for an HTTP origin")
        if self.tls_mode in {"passthrough", "http_only"} and self.tls:
            raise ValueError("A POP certificate requires auto or terminate TLS mode")
        if all(o.backup for o in self.origins):
            raise ValueError("Origin pool requires a primary")
        for o in self.origins:
            if (o.scheme, o.sni, o.host_header, o.tls_verify) != (
                first.scheme,
                first.sni,
                first.host_header,
                first.tls_verify,
            ):
                raise ValueError("Pool members must share scheme, SNI, Host and TLS verification")
        if any(route.origin_index >= len(self.origins) for route in self.origin_routes):
            raise ValueError("Path origin route references an origin that does not exist")
        routes = [(route.match, route.path) for route in self.origin_routes]
        if len(routes) != len(set(routes)):
            raise ValueError("Duplicate path origin route")
        return self


class Bundle(Model):
    schema_version: Literal[1, 2] = 2
    # Bump when templates or rendering semantics change so data-identical
    # desired state is re-rendered exactly once after an Agent upgrade.
    renderer_version: int = Field(default=3, ge=1)
    revision: int = Field(ge=1)
    vhosts: list[Vhost] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        ids = [v.id for v in self.vhosts]
        domains = [d for v in self.vhosts for d in v.domains]
        if len(ids) != len(set(ids)) or len(domains) != len(set(domains)):
            raise ValueError("Duplicate vhost ID or domain")
        return self

    def digest(self):
        data = self.model_dump(mode="json", exclude={"revision"})
        data["vhosts"] = sorted(data["vhosts"], key=lambda v: v["id"])
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
