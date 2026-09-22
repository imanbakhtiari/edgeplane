from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
import ipaddress


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "production"
    database_url: str = "postgresql+asyncpg://cdn:cdn@localhost/cdn"
    secret_key: str
    master_encryption_key: str
    admin_username: str = "admin"
    admin_password: str
    supervisor_public_url: str = "https://cdn.example.net"
    allowed_origins: list[str] = []
    agent_poll_interval: int = 300
    analytics_mode: str = "full"
    metrics_token: str = ""
    prometheus_remote_write_url: str = ""
    prometheus_remote_write_token: str = ""
    bgp_enabled: bool = False
    maxmind_country_db: str = "maxmind/GeoLite2-Country.mmdb"
    maxmind_city_db: str = "maxmind/GeoLite2-City.mmdb"
    powerdns_auth_mode: str = "api_key"
    powerdns_username: str = ""
    powerdns_password: str = ""
    job_poll_interval: float = 2
    agent_connect_timeout: int = 10
    agent_request_timeout: int = 120
    powerdns_api_url: str = ""
    powerdns_api_key: str = ""
    powerdns_server_id: str = "localhost"
    powerdns_cdn_zone: str = "edge.example.net"
    dns_ttl: int = 60
    dns_anycast_ipv4: list[str] = []
    dns_anycast_ipv6: list[str] = []
    health_failures: int = 3
    health_successes: int = 5
    agent_package_path: str = "/opt/cdn/agent"
    supervisor_ssh_private_key: str = "/run/secrets/edgeplane_ssh_key"
    agent_management_allowed_cidrs: list[str] = ["127.0.0.0/8", "::1/128"]

    @field_validator("agent_management_allowed_cidrs")
    @classmethod
    def valid_management_cidrs(cls, value):
        return [str(ipaddress.ip_network(cidr, strict=False)) for cidr in value]

    def model_post_init(self, context):
        if len(self.secret_key) < 32 or len(self.admin_password) < 12:
            raise ValueError("SECRET_KEY needs 32 characters; ADMIN_PASSWORD needs 12")


settings = Settings()
