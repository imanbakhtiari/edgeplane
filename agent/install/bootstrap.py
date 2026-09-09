#!/usr/bin/env python3
"""Root bootstrap for approved Ubuntu 24.04 nodes. No arbitrary API inputs."""

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


def main():
    if os.geteuid() != 0:
        raise SystemExit("Root required for host bootstrap")
    source = Path(sys.argv[1])
    identity = json.loads((source / "identity.json").read_text())
    step("Checking required ports and existing NGINX configuration")
    if shutil.which("nginx"):
        run("nginx", "-t")
    step("Installing missing NGINX, Varnish, Node Exporter, NGINX Exporter and Python packages")
    run("apt-get", "update", "-qq")
    run(
        "apt-get",
        "install",
        "-y",
        "nginx",
        "varnish",
        "prometheus-node-exporter",
        "prometheus-nginx-exporter",
        "python3-venv",
        "libnginx-mod-http-headers-more-filter",
        "libnginx-mod-http-geoip2",
        "sudo",
    )
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
    # Templates are a package resource included explicitly by packaging configuration.
    shutil.copytree(
        source / "agent/templates",
        "/opt/cdn-agent/venv/lib/python3.12/site-packages/templates",
        dirs_exist_ok=True,
    )
    if (source / "GeoLite2-City.mmdb").exists():
        step("Installing supplied MaxMind City database")
        shutil.copy2(source / "GeoLite2-City.mmdb", "/etc/cdn-agent/GeoLite2-City.mmdb")
        Path("/etc/cdn-agent/GeoLite2-City.mmdb").chmod(0o644)
    step("Installing unique mTLS identity")
    for name, key in [
        ("ca.pem", "management_ca"),
        ("server.pem", "server_cert"),
        ("server.key", "server_key"),
    ]:
        write("/etc/cdn-agent/" + name, identity[key], 0o600)
    write(
        "/etc/cdn-agent/agent.env",
        f"AGENT_MODE=host\nAGENT_ID={identity['agent_id']}\nAGENT_NAME={identity['agent_id']}\n",
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
    write(
        "/etc/default/prometheus-nginx-exporter",
        'ARGS="-nginx.scrape-uri=http://127.0.0.1:8081/stub_status -web.listen-address=127.0.0.1:9113"\n',
    )
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
        config.write_text(modified)
        check = subprocess.run(["nginx", "-t"], capture_output=True)
        if check.returncode:
            config.write_text(original)
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
    for service in ["varnish", "prometheus-node-exporter", "prometheus-nginx-exporter", "cdn-agent"]:
        run("systemctl", "enable", service)
        run("systemctl", "restart", service)
    # Mandatory independent validation before this bootstrap reload too.
    run("nginx", "-t")
    run("systemctl", "enable", "--now", "nginx")
    run("nginx", "-t")
    run("nginx", "-s", "reload")
    step("NGINX validated and reloaded; verifying Varnish and exporters")
    for service in ["nginx", "varnish", "prometheus-node-exporter", "prometheus-nginx-exporter", "cdn-agent"]:
        run("systemctl", "is-active", service)
    step("Host bootstrap complete")


if __name__ == "__main__":
    main()
