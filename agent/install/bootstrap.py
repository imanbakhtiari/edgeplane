#!/usr/bin/env python3
"""Root bootstrap for approved Ubuntu 22.04/24.04 nodes. No arbitrary API inputs."""

import datetime
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.request


def step(message):
    print("CDN_STEP " + message, flush=True)


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def write(path, value, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=mode)
    path.chmod(mode)
    path.write_text(value)


def ensure_nginx_status_configuration(root):
    """Install only the first-boot status server; never replace an active release."""
    root = Path(root)
    current = root / "current"
    if current.is_symlink():
        if not current.exists():
            raise RuntimeError("Managed NGINX current symlink is broken")
        return False
    if current.exists():
        raise RuntimeError("Managed NGINX current path is not a release symlink")
    release = root / "releases" / "bootstrap"
    write(
        release / "http" / "00-status.conf",
        "server { listen 127.0.0.1:8081; server_name localhost; "
        "location = /stub_status { stub_status; allow 127.0.0.1; deny all; } }\n",
    )
    current.symlink_to(release)
    return True


def wait_for_nginx_status(attempts=5):
    url = "http://127.0.0.1:8081/stub_status"
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200 and b"Active connections:" in response.read(256):
                    return
        except (OSError, ValueError):
            pass
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("NGINX_STUB_STATUS_UNAVAILABLE: 127.0.0.1:8081/stub_status did not respond")


def wait_for_nginx_exporter(attempts=5):
    url = "http://127.0.0.1:9113/metrics"
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200 and b"nginx_up 1" in response.read(65536):
                    return
        except (OSError, ValueError):
            pass
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("NGINX_EXPORTER_UNHEALTHY: exporter did not report nginx_up 1")


def require_active_service(service):
    result = subprocess.run(["systemctl", "is-active", "--quiet", service], check=False)
    if result.returncode:
        step("Service failed health check: " + service)
        raise RuntimeError("SERVICE_INACTIVE:" + service)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Root required for host bootstrap")
    os_release = dict(
        line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    release = os_release.get("VERSION_ID", "").strip('"')
    if os_release.get("ID", "").strip('"') != "ubuntu" or release not in {"22.04", "24.04"}:
        raise SystemExit("Host bootstrap requires Ubuntu 22.04 or 24.04")
    if sys.version_info < (3, 10):
        raise SystemExit("Host bootstrap requires Python 3.10 or newer")
    source = Path(sys.argv[1])
    identity = json.loads((source / "identity.json").read_text())
    step("Checking required ports and existing NGINX configuration")
    if shutil.which("nginx"):
        run("nginx", "-t")
    step("Installing NGINX, Varnish, Prometheus, exporters and Python packages")
    run("apt-get", "update", "-qq")
    packages = [
        "nginx",
        "varnish",
        "prometheus-node-exporter",
        "prometheus-nginx-exporter",
        "prometheus-varnish-exporter",
        "prometheus",
        "python3-venv",
        "libnginx-mod-http-headers-more-filter",
        "libnginx-mod-http-geoip2",
        "sudo",
    ]
    if identity.get("bgp_enabled"):
        packages.append("bird2")
    missing = [
        name for name in packages
        if subprocess.run(["apt-cache", "show", name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode != 0
    ]
    if missing:
        step("Required Ubuntu packages unavailable: " + ", ".join(missing) + "; check enabled apt components")
        raise RuntimeError("Required Ubuntu packages unavailable")
    run("apt-get", "install", "-y", *packages)
    if identity.get("bgp_enabled"):
        step("Optional BIRD routing daemon installed")
    try:
        pwd.getpwnam("cdn-agent")
    except KeyError:
        run(
            "useradd", "--system", "--home", "/var/lib/cdn-agent", "--shell", "/usr/sbin/nologin", "cdn-agent"
        )
    step("Creating controlled directories and bounded service backup")
    for path in [
        "/etc/nginx/cdn-managed",
        "/etc/varnish/cdn-managed",
        "/var/lib/cdn-agent",
        "/var/log/nginx/cdn",
        "/etc/cdn-agent",
    ]:
        Path(path).mkdir(parents=True, exist_ok=True)
    backup = Path("/var/backups/cdn-agent")
    backup.mkdir(parents=True, exist_ok=True)
    stamp = backup / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    stamp.mkdir()
    shutil.copy2("/etc/nginx/nginx.conf", stamp / "nginx.conf")
    for old in sorted(backup.iterdir())[:-5]:
        shutil.rmtree(old)
    step("Installing Agent package into a dedicated Python environment")
    shutil.copytree(source / "agent", "/opt/cdn-agent/source", dirs_exist_ok=True)
    run("python3", "-m", "venv", "/opt/cdn-agent/venv")
    run("/opt/cdn-agent/venv/bin/pip", "install", "/opt/cdn-agent/source")
    # Templates are installed as venv data files; the source tree is also kept
    # for the systemd WorkingDirectory. Avoid a Python-minor-version path here.
    if (source / "GeoLite2-City.mmdb").exists():
        step("Installing supplied MaxMind City database")
        shutil.copy2(source / "GeoLite2-City.mmdb", "/etc/cdn-agent/GeoLite2-City.mmdb")
        Path("/etc/cdn-agent/GeoLite2-City.mmdb").chmod(0o644)
    if (source / "GeoLite2-Country.mmdb").exists():
        step("Installing supplied MaxMind Country database")
        shutil.copy2(source / "GeoLite2-Country.mmdb", "/etc/cdn-agent/GeoLite2-Country.mmdb")
        Path("/etc/cdn-agent/GeoLite2-Country.mmdb").chmod(0o644)
    step("Installing unique mTLS identity")
    for name, key in [
        ("ca.pem", "management_ca"),
        ("server.pem", "server_cert"),
        ("server.key", "server_key"),
        ("metrics-client.pem", "metrics_client_cert"),
        ("metrics-client.key", "metrics_client_key"),
    ]:
        write("/etc/cdn-agent/" + name, identity[key], 0o600)
    write(
        "/etc/cdn-agent/agent.env",
        f"AGENT_MODE=host\nAGENT_ID={identity['agent_id']}\nAGENT_NAME={identity['agent_name']}\n"
        f"AGENT_CITY={identity['agent_city']}\nAGENT_COUNTRY={identity['agent_country']}\n"
        f"AGENT_PROVIDER={identity['agent_provider']}\n"
        f"BGP_ENABLED={'true' if identity.get('bgp_enabled') else 'false'}\n"
        f"MANAGEMENT_ALLOWED_CIDRS='{json.dumps(identity['management_allowed_cidrs'])}'\n",
        0o600,
    )
    step("Configuring loopback Varnish and private exporters")
    free = shutil.disk_usage("/var/lib/cdn-agent").free
    size = min(1024 * 1024 * 1024, free // 10)
    if size < 256 * 1024 * 1024:
        raise RuntimeError("Insufficient free cache disk")
    write(
        "/etc/varnish/cdn-managed/bootstrap.vcl",
        'vcl 4.1; backend default { .host = "127.0.0.1"; .port = "8080"; }\n',
    )
    active_vcl = (
        "/etc/varnish/cdn-managed/current/default.vcl"
        if Path("/etc/varnish/cdn-managed/current/default.vcl").exists()
        else "/etc/varnish/cdn-managed/bootstrap.vcl"
    )
    write(
        "/etc/systemd/system/varnish.service.d/cdn.conf",
        f"[Service]\nExecStart=\nExecStart=/usr/sbin/varnishd -F -a 127.0.0.1:6081 -T 127.0.0.1:6082 -S /etc/varnish/secret -f {active_vcl} -s file,/var/lib/cdn-agent/cache,{size}\n",
    )
    write("/etc/default/prometheus-node-exporter", 'ARGS="--web.listen-address=127.0.0.1:9100"\n')
    write("/etc/default/prometheus", 'ARGS="--web.listen-address=127.0.0.1:9090"\n')
    write(
        "/etc/default/prometheus-nginx-exporter",
        'ARGS="-nginx.scrape-uri=http://127.0.0.1:8081/stub_status -web.listen-address=127.0.0.1:9113"\n',
    )
    write(
        "/etc/default/prometheus-varnish-exporter",
        'ARGS="-web.listen-address=127.0.0.1:9131"\n',
    )
    remote_write = ""
    if identity.get("prometheus_remote_write_url"):
        remote_write = (
            "\nremote_write:\n"
            f"  - url: {json.dumps(identity['prometheus_remote_write_url'])}\n"
        )
        if identity.get("prometheus_remote_write_token"):
            remote_write += (
                "    authorization:\n"
                f"      credentials: {json.dumps(identity['prometheus_remote_write_token'])}\n"
            )
    write(
        "/etc/prometheus/prometheus.yml",
        "global:\n  scrape_interval: 15s\n  external_labels:\n"
        f"    agent_id: {json.dumps(identity['agent_id'])}\n"
        f"    agent_name: {json.dumps(identity['agent_name'])}\n"
        f"    pop_city: {json.dumps(identity['agent_city'])}\n"
        f"    pop_country: {json.dumps(identity['agent_country'])}\n"
        f"    provider: {json.dumps(identity['agent_provider'])}\n"
        "scrape_configs:\n"
        "  - job_name: cdn-agent\n"
        "    scheme: https\n"
        "    static_configs:\n      - targets: [\"127.0.0.1:9443\"]\n"
        "    tls_config:\n"
        "      ca_file: /etc/cdn-agent/ca.pem\n"
        "      cert_file: /etc/cdn-agent/metrics-client.pem\n"
        "      key_file: /etc/cdn-agent/metrics-client.key\n"
        f"      server_name: {json.dumps(identity['management_hostname'])}\n"
        "  - job_name: node\n    static_configs:\n      - targets: [\"127.0.0.1:9100\"]\n"
        "  - job_name: nginx\n    static_configs:\n      - targets: [\"127.0.0.1:9113\"]\n"
        "  - job_name: varnish\n    static_configs:\n      - targets: [\"127.0.0.1:9131\"]\n"
        + remote_write,
        0o640,
    )
    for path in [
        "/etc/prometheus/prometheus.yml",
        "/etc/cdn-agent/ca.pem",
        "/etc/cdn-agent/metrics-client.pem",
        "/etc/cdn-agent/metrics-client.key",
    ]:
        shutil.chown(path, user="prometheus", group="prometheus")
    step("Adding managed include with backup, validation and automatic restoration")
    config = Path("/etc/nginx/nginx.conf")
    original = config.read_text()
    include = "include /etc/nginx/cdn-managed/current/http/*.conf;"
    if include not in original:
        modified, count = re.subn(
            r"(?m)^\s*http\s*\{", lambda m: m.group(0) + "\n    " + include, original, count=1
        )
        if count != 1:
            raise RuntimeError("Cannot safely locate HTTP context")
    created_bootstrap_release = ensure_nginx_status_configuration("/etc/nginx/cdn-managed")
    if include not in original:
        config.write_text(modified)
    check = subprocess.run(["nginx", "-t"], capture_output=True)
    if check.returncode:
        if include not in original:
            config.write_text(original)
        if created_bootstrap_release:
            (Path("/etc/nginx/cdn-managed") / "current").unlink()
        raise RuntimeError("NGINX VALIDATION FAILED: original configuration restored; no reload")
    step("Installing constrained systemd service")
    shutil.copy2(source / "agent/systemd/cdn-agent.service", "/etc/systemd/system/cdn-agent.service")
    shutil.copy2(source / "agent/install/logrotate.conf", "/etc/logrotate.d/cdn-agent")
    if identity["firewall"]:
        step("Applying opted-in firewall policy, preserving SSH access")
        if not identity["management_cidrs"]:
            raise RuntimeError("Explicit management CIDRs required for firewall provisioning")
        run("apt-get", "install", "-y", "ufw")
        run("ufw", "allow", str(int(identity["ssh_port"])) + "/tcp")
        for port in ["80", "443"]:
            run("ufw", "allow", port + "/tcp")
        for cidr in identity["management_cidrs"]:
            network = str(ipaddress.ip_network(cidr, strict=False))
            run("ufw", "allow", "from", network, "to", "any", "port", "9443", "proto", "tcp")
        run("ufw", "deny", "9443/tcp")
        run("ufw", "--force", "enable")
    step("Starting services")
    run("systemctl", "daemon-reload")
    managed_services = ["varnish", "prometheus-node-exporter", "prometheus-varnish-exporter", "prometheus", "cdn-agent"]
    if identity.get("bgp_enabled"):
        managed_services.append("bird")
    for service in managed_services:
        run("systemctl", "enable", service)
        run("systemctl", "restart", service)
    # Mandatory independent validation before this bootstrap reload too.
    run("nginx", "-t")
    run("systemctl", "enable", "--now", "nginx")
    run("nginx", "-t")
    run("nginx", "-s", "reload")
    step("NGINX validated and reloaded; checking loopback stub_status before starting exporter")
    wait_for_nginx_status()
    run("systemctl", "enable", "prometheus-nginx-exporter")
    run("systemctl", "reset-failed", "prometheus-nginx-exporter")
    run("systemctl", "restart", "prometheus-nginx-exporter")
    wait_for_nginx_exporter()
    step("NGINX exporter started; verifying managed services")
    for service in ["nginx", *managed_services, "prometheus-nginx-exporter"]:
        require_active_service(service)
    step("Host bootstrap complete")


if __name__ == "__main__":
    main()
