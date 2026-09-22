from pathlib import Path
from ipaddress import ip_network
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    agent_mode: str = "host"
    agent_id: str = "unconfigured"
    agent_name: str = "edge"
    agent_city: str = ""
    agent_country: str = ""
    agent_provider: str = ""
    agent_listen_host: str = "0.0.0.0"
    agent_listen_port: int = 9443
    management_ca: str = "/etc/cdn-agent/ca.pem"
    server_cert: str = "/etc/cdn-agent/server.pem"
    server_key: str = "/etc/cdn-agent/server.key"
    nginx_config_root: Path = Path("/etc/nginx/cdn-managed")
    varnish_config_root: Path = Path("/etc/varnish/cdn-managed")
    state_root: Path = Path("/var/lib/cdn-agent")
    log_root: Path = Path("/var/log/nginx/cdn")
    nginx_binary: str = "/usr/sbin/nginx"
    varnish_binary: str = "/usr/sbin/varnishd"
    varnishadm_binary: str = "/usr/bin/varnishadm"
    allow_private_origins: bool = False
    public_port: int = 80
    tls_port: int = 443
    origin_port: int = 8080
    varnish_port: int = 6081
    retention: int = 5
    maxmind_country_db: Path = Path("/etc/cdn-agent/GeoLite2-Country.mmdb")
    maxmind_city_db: Path = Path("/etc/cdn-agent/GeoLite2-City.mmdb")
    telemetry_interval: float = 5
    telemetry_max_bytes: int = 16777216
    bgp_enabled: bool = False
    birdc_binary: str = "/usr/sbin/birdc"
    varnishstat_binary: str = "/usr/bin/varnishstat"
    management_allowed_cidrs: list[str] = Field(default_factory=lambda: ["127.0.0.0/8", "::1/128"])

    @field_validator("management_allowed_cidrs")
    @classmethod
    def validate_management_networks(cls, value):
        if not value:
            raise ValueError("At least one management CIDR is required")
        return [str(ip_network(cidr, strict=False)) for cidr in value]

    def model_post_init(self, context):
        if self.agent_mode not in {"host", "container", "sandbox"}:
            raise ValueError("Invalid Agent mode")
        if self.agent_mode in {"container", "sandbox"}:
            self.nginx_config_root = Path("/tmp/cdn-sandbox/nginx")
            self.varnish_config_root = Path("/tmp/cdn-sandbox/varnish")
            self.state_root = Path("/tmp/cdn-sandbox/state")
            self.log_root = Path("/tmp/cdn-sandbox/logs")
