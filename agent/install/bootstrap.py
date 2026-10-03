#!/usr/bin/env python3
"""Root bootstrap for approved Ubuntu 22.04/24.04 nodes. No arbitrary API inputs."""

import datetime
import filecmp
import hashlib
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
import uuid


def step(message):
    print("CDN_STEP " + message, flush=True)


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def write(path, value, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text() == value and (path.stat().st_mode & 0o777) == mode:
        return False
    path.touch(mode=mode)
    path.chmod(mode)
    path.write_text(value)
    return True


def copy_if_changed(source, target, mode=None):
    source, target = Path(source), Path(target)
    if target.is_file() and filecmp.cmp(source, target, shallow=False):
        return False
    shutil.copy2(source, target)
    if mode is not None:
        target.chmod(mode)
    return True


def missing_packages(packages):
    missing = []
    for name in packages:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", name],
            capture_output=True, text=True, check=False,
        )
        if result.returncode or result.stdout.strip() != "install ok installed":
            missing.append(name)
    return missing


def source_digest(root):
    root = Path(root)
    digest = hashlib.sha256()
    ignored = {"__pycache__", ".pytest_cache", "build", "cdn_agent.egg-info"}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in ignored for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(relative.as_posix().encode() + b"\0")
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


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


def hostname_hosts(content, name):
    """Replace only the self-hostname address; preserve unrelated hosts and aliases."""
    if len(name) > 253 or not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) for label in name.split(".")):
        raise ValueError("Node name must be a valid DNS hostname")
    lines = []
    aliases = []
    for line in content.splitlines():
        fields = line.split("#", 1)[0].split()
        if fields and fields[0] == "127.0.1.1":
            aliases.extend(fields[2:])
        else:
            lines.append(line)
    names = list(dict.fromkeys([name, name.split('.')[0], *aliases]))
    lines.append("127.0.1.1\t" + " ".join(names) + " # Edgeplane node hostname")
    return "\n".join(lines) + "\n"


def configure_hostname(name):
    hosts = Path("/etc/hosts")
    updated = hostname_hosts(hosts.read_text(), name)
    current = subprocess.run(["hostnamectl", "--static"], capture_output=True, text=True, check=True).stdout.strip()
    if current != name:
        run("hostnamectl", "set-hostname", name)
    write(hosts, updated)


AGENT_RELEASE_STATE = Path('/var/lib/cdn-agent/agent-releases.json')
AGENT_RELEASE_OVERRIDE = Path('/etc/systemd/system/cdn-agent.service.d/release.conf')


def release_state():
    if AGENT_RELEASE_STATE.is_file():
        return json.loads(AGENT_RELEASE_STATE.read_text())
    marker = Path('/opt/cdn-agent/source/.bootstrap-source-sha256')
    current = None
    if Path('/opt/cdn-agent/venv/bin/python').is_file():
        current = {'version': marker.read_text().strip() if marker.is_file() else 'legacy',
                   'source': '/opt/cdn-agent/source', 'python': '/opt/cdn-agent/venv/bin/python'}
    return {'current': current, 'previous': None}


def prepare_agent_release(source):
    digest = source_digest(source)
    state = release_state()
    if state['current'] and state['current']['version'] == digest:
        step('Agent package unchanged; installation skipped')
        return state['current']
    # Unique immutable directories keep the active Python environment intact.
    root = Path('/opt/cdn-agent/releases') / (digest[:12] + '-' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    shutil.copytree(source, root / 'source', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.env'))
    run('python3', '-m', 'venv', str(root / 'venv'))
    python = str(root / 'venv/bin/python')
    run(python, '-m', 'pip', 'install', str(root / 'source'))
    run(python, '-c', 'import fastapi, psutil, app.schemas.config, app.services.deploy')
    write(root / 'source/.bootstrap-source-sha256', digest + '\n')
    return {'version': digest, 'source': str(root / 'source'), 'python': python}


def activate_agent_release(target):
    state = release_state()
    previous_override = AGENT_RELEASE_OVERRIDE.read_text() if AGENT_RELEASE_OVERRIDE.is_file() else None
    write(AGENT_RELEASE_OVERRIDE, '[Service]\nWorkingDirectory=' + target['source'] + '\nExecStart=\nExecStart=' + target['python'] + ' -m app.main\n')
    try:
        run('systemctl', 'daemon-reload')
        run('systemctl', 'restart', 'cdn-agent')
        time.sleep(3)
        require_active_service('cdn-agent')
    except Exception:
        if previous_override is None:
            AGENT_RELEASE_OVERRIDE.unlink(missing_ok=True)
        else:
            write(AGENT_RELEASE_OVERRIDE, previous_override)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'restart', 'cdn-agent')
        raise RuntimeError('Agent activation failed; previous release restored') from None
    if state['current'] != target:
        state = {'current': target, 'previous': state['current']}
    AGENT_RELEASE_STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = AGENT_RELEASE_STATE.with_suffix('.tmp')
    write(temp, json.dumps(state), 0o600)
    temp.replace(AGENT_RELEASE_STATE)
    step('Agent release active: ' + target['version'][:12])


def rollback_agent_release():
    previous = release_state().get('previous')
    if not previous or not Path(previous['python']).is_file():
        raise RuntimeError('No previous Agent release is available')
    activate_agent_release(previous)
    step('Previous Agent restored; NGINX and Varnish were not signalled')


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
    if '--rollback-agent' in sys.argv[2:]:
        rollback_agent_release()
        return
    identity = json.loads((source / "identity.json").read_text())
    from varnish_config import validate as validate_varnish, logrotate as varnish_logrotate
    varnish_settings = identity.get("varnish_settings")
    saved_varnish = Path("/var/lib/cdn-agent/varnish-settings.json")
    if not varnish_settings and saved_varnish.is_file():
        varnish_settings = json.loads(saved_varnish.read_text())
    if varnish_settings:
        varnish_settings = validate_varnish(varnish_settings)
    if '--varnish-only' in sys.argv[2:]:
        from varnish_config import apply
        if not identity.get("varnish_settings"):
            raise ValueError("Varnish desired settings missing")
        result = apply(identity["varnish_settings"])
        step("Varnish storage and log retention applied; restarted=" + str(result["restarted"]))
        return
    # Node names are the managed operating-system identity. This is safe during
    # an Agent-only upgrade and does not signal NGINX or Varnish.
    configure_hostname(identity["agent_name"])
    agent_only = "--agent-only" in sys.argv[2:]
    if agent_only:
        step("Agent-only upgrade: data-plane services and configuration are excluded")
        if missing_packages(["python3-venv"]):
            run("apt-get", "update", "-qq")
            run("apt-get", "install", "-y", "python3-venv")
        Path("/opt/cdn-agent").mkdir(parents=True, exist_ok=True)
        Path("/etc/cdn-agent").mkdir(parents=True, exist_ok=True)
        Path("/var/lib/cdn-agent").mkdir(parents=True, exist_ok=True)
        agent_release = prepare_agent_release(source / 'agent')
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
        copy_if_changed(
            source / "agent/systemd/cdn-agent.service",
            "/etc/systemd/system/cdn-agent.service",
        )
        if varnish_settings:
            write("/etc/logrotate.d/cdn-agent", varnish_logrotate(varnish_settings))
        else:
            copy_if_changed(source / "agent/install/logrotate.conf", "/etc/logrotate.d/cdn-agent")
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "cdn-agent")
        activate_agent_release(agent_release)
        step("Agent-only upgrade complete; NGINX and Varnish were not changed or signalled")
        return
    step("Checking required ports and existing NGINX configuration")
    if shutil.which("nginx"):
        run("nginx", "-t")
    packages = [
        "nginx",
        "varnish",
        "logrotate",
        "prometheus-node-exporter",
        "prometheus-nginx-exporter",
        "prometheus-varnish-exporter",
        "prometheus",
        "python3-venv",
        "libnginx-mod-http-headers-more-filter",
        "libnginx-mod-http-geoip2",
        "libnginx-mod-stream",
        "sudo",
    ]
    if identity.get("bgp_enabled"):
        packages.append("bird2")
    to_install = missing_packages(packages)
    if to_install:
        step("Installing missing host packages: " + ", ".join(to_install))
        run("apt-get", "update", "-qq")
    else:
        step("Required host packages already installed; skipping apt update and install")
    missing = [
        name for name in to_install
        if subprocess.run(["apt-cache", "show", name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode != 0
    ]
    if missing:
        step("Required Ubuntu packages unavailable: " + ", ".join(missing) + "; check enabled apt components")
        raise RuntimeError("Required Ubuntu packages unavailable")
    if to_install:
        run("apt-get", "install", "-y", *to_install)
    if identity.get("bgp_enabled"):
        step("Optional BIRD routing daemon available")
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
    # Ubuntu enables its own catch-all server by default. Edgeplane provides a
    # managed catch-all, so retaining both produces a conflicting server_name
    # warning and makes default-host routing depend on include order.
    ubuntu_default = Path("/etc/nginx/sites-enabled/default")
    if ubuntu_default.exists() or ubuntu_default.is_symlink():
        if ubuntu_default.exists():
            shutil.copy2(ubuntu_default, stamp / "nginx-default-site.conf")
        ubuntu_default.unlink()
        step("Disabled Ubuntu default NGINX site; backup retained")
    for old in sorted(backup.iterdir())[:-5]:
        shutil.rmtree(old)
    agent_release = prepare_agent_release(source / 'agent')
    # Templates are installed as venv data files; the source tree is also kept
    # for the systemd WorkingDirectory. Avoid a Python-minor-version path here.
    if (source / "GeoLite2-City.mmdb").exists():
        changed = copy_if_changed(source / "GeoLite2-City.mmdb", "/etc/cdn-agent/GeoLite2-City.mmdb", 0o644)
        step("MaxMind City database " + ("updated" if changed else "unchanged; skipped"))
    if (source / "GeoLite2-Country.mmdb").exists():
        changed = copy_if_changed(source / "GeoLite2-Country.mmdb", "/etc/cdn-agent/GeoLite2-Country.mmdb", 0o644)
        step("MaxMind Country database " + ("updated" if changed else "unchanged; skipped"))
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
    existing_varnish_unit = Path("/etc/systemd/system/varnish.service.d/cdn.conf")
    size_match = re.search(
        r"-s file,/var/lib/cdn-agent/cache,(\d+)",
        existing_varnish_unit.read_text() if existing_varnish_unit.is_file() else "",
    )
    free = shutil.disk_usage("/var/lib/cdn-agent").free
    size = int(size_match.group(1)) if size_match else min(1024 * 1024 * 1024, free // 10)
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
    varnish_service_changed = write(
        "/etc/systemd/system/varnish.service.d/cdn.conf",
        existing_varnish_unit.read_text() if varnish_settings and existing_varnish_unit.is_file() else
        f"[Service]\nExecStart=\nExecStart=/usr/sbin/varnishd -F -a 127.0.0.1:6081 -T 127.0.0.1:6082 -S /etc/varnish/secret -f {active_vcl} -s file,/var/lib/cdn-agent/cache,{size}\n",
    )
    node_exporter_changed = write("/etc/default/prometheus-node-exporter", 'ARGS="--web.listen-address=127.0.0.1:9100"\n')
    prometheus_args_changed = write("/etc/default/prometheus", 'ARGS="--web.listen-address=127.0.0.1:9090"\n')
    nginx_exporter_changed = write(
        "/etc/default/prometheus-nginx-exporter",
        'ARGS="-nginx.scrape-uri=http://127.0.0.1:8081/stub_status -web.listen-address=127.0.0.1:9113"\n',
    )
    varnish_exporter_changed = write(
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
    prometheus_config_changed = write(
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
    stream_include = "include /etc/nginx/cdn-managed/current/stream/*.conf;"
    modified = original
    if include not in original:
        modified, count = re.subn(
            r"(?m)^\s*http\s*\{", lambda m: m.group(0) + "\n    " + include, original, count=1
        )
        if count != 1:
            raise RuntimeError("Cannot safely locate HTTP context")
    if stream_include not in modified:
        if re.search(r"(?m)^\s*stream\s*\{", modified):
            modified = re.sub(r"(?m)^\s*stream\s*\{",
                              lambda m: m.group(0) + "\n    " + stream_include, modified, count=1)
        else:
            modified += "\nstream { " + stream_include + " }\n"
    created_bootstrap_release = ensure_nginx_status_configuration("/etc/nginx/cdn-managed")
    if modified != original:
        config.write_text(modified)
    check = subprocess.run(["nginx", "-t"], capture_output=True)
    if check.returncode:
        if modified != original:
            config.write_text(original)
        if created_bootstrap_release:
            (Path("/etc/nginx/cdn-managed") / "current").unlink()
        raise RuntimeError("NGINX VALIDATION FAILED: original configuration restored; no reload")
    step("Installing constrained systemd service")
    copy_if_changed(source / "agent/systemd/cdn-agent.service", "/etc/systemd/system/cdn-agent.service")
    if varnish_settings:
        write("/etc/logrotate.d/cdn-agent", varnish_logrotate(varnish_settings))
    else:
        copy_if_changed(source / "agent/install/logrotate.conf", "/etc/logrotate.d/cdn-agent")
    if identity["firewall"]:
        step("Applying opted-in firewall policy, preserving SSH access")
        if not identity["management_cidrs"]:
            raise RuntimeError("Explicit management CIDRs required for firewall provisioning")
        if missing_packages(["ufw"]):
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
        if service == 'cdn-agent':
            activate_agent_release(agent_release)
            continue
        changed = {
            "varnish": varnish_service_changed,
            "prometheus-node-exporter": node_exporter_changed,
            "prometheus-varnish-exporter": varnish_exporter_changed,
            "prometheus": prometheus_args_changed or prometheus_config_changed,
            "cdn-agent": True,  # management identity is rotated during reprovision
        }.get(service, False)
        if changed or subprocess.run(["systemctl", "is-active", "--quiet", service], check=False).returncode:
            run("systemctl", "restart", service)
        else:
            step(service + " already active with unchanged configuration; restart skipped")
    # Mandatory independent validation before this bootstrap reload too.
    if varnish_settings:
        from varnish_config import apply as apply_varnish
        apply_varnish(varnish_settings)
    run("nginx", "-t")
    run("systemctl", "enable", "--now", "nginx")
    run("nginx", "-t")
    if created_bootstrap_release or modified != original:
        run("nginx", "-s", "reload")
    else:
        step("NGINX managed configuration unchanged; reload skipped")
    step("NGINX validated; checking loopback stub_status before checking exporter")
    wait_for_nginx_status()
    run("systemctl", "enable", "prometheus-nginx-exporter")
    run("systemctl", "reset-failed", "prometheus-nginx-exporter")
    if nginx_exporter_changed or subprocess.run(["systemctl", "is-active", "--quiet", "prometheus-nginx-exporter"], check=False).returncode:
        run("systemctl", "restart", "prometheus-nginx-exporter")
    else:
        step("NGINX exporter already active with unchanged configuration; restart skipped")
    wait_for_nginx_exporter()
    step("NGINX exporter healthy; verifying managed services")
    for service in ["nginx", *managed_services, "prometheus-nginx-exporter"]:
        require_active_service(service)
    step("Host bootstrap complete")


if __name__ == "__main__":
    main()
