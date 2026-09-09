from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    agent_mode: str = "host"
    agent_id: str = "unconfigured"
    agent_name: str = "edge"
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
    maxmind_city_db: Path = Path("/etc/cdn-agent/GeoLite2-City.mmdb")
    telemetry_interval: float = 5
    telemetry_max_bytes: int = 16777216

    def model_post_init(self, context):
        if self.agent_mode not in {"host", "sandbox"}:
            raise ValueError("Invalid Agent mode")
        if self.agent_mode == "sandbox":
            self.nginx_config_root = Path("/tmp/cdn-sandbox/nginx")
            self.varnish_config_root = Path("/tmp/cdn-sandbox/varnish")
            self.state_root = Path("/tmp/cdn-sandbox/state")
            self.log_root = Path("/tmp/cdn-sandbox/logs")
