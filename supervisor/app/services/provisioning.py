"""SSH is exclusively bootstrap/repair transport. Host key approval is explicit."""

import asyncio
import ipaddress
import json
import shlex
from pathlib import Path
from urllib.parse import urlparse
import asyncssh
from sqlalchemy import select
from app.db.session import Session
from app.models.entities import AgentCredential
from app.core.security import decrypt
from app.core.settings import settings
from app.services.pki import authority, issue


async def credential(db, node):
    row = await db.scalar(select(AgentCredential).where(AgentCredential.agent_id == node.id))
    if not row:
        raise ValueError("SSH_CREDENTIAL_MISSING")
    return row, json.loads(decrypt(row.encrypted))


async def discover(db, node):
    row, data = await credential(db, node)
    # Discovery fetches a PUBLIC key; no credentials are sent and no trust is granted.
    candidates = list(dict.fromkeys(value for value in (node.hostname, node.public_ipv4) if value))
    last_error = None
    for host in candidates:
        try:
            key = await asyncio.wait_for(
                asyncssh.get_server_host_key(host, port=data["port"]), timeout=15
            )
            exported = key.export_public_key().decode().strip()
            return {
                "fingerprint": key.get_fingerprint(),
                "host_key": exported,
                "approved": row.host_key == exported,
                "host": host,
                "port": data["port"],
            }
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            last_error = exc
    raise ConnectionError("SSH_DISCOVERY_FAILED") from last_error


def auth_mode(data):
    # Records written before explicit authentication selection retain their
    # original password/key behavior without silently changing credentials.
    return data.get("auth_mode") or ("key_and_password" if data.get("private_key") and data.get("password") else "private_key" if data.get("private_key") else "password" if data.get("password") else "private_key")


def client_keys(data):
    mode = auth_mode(data)
    if mode == "password":
        return []
    if mode in {"private_key", "key_and_password", "supervisor_key"}:
        if data.get("private_key"):
            return [asyncssh.import_private_key(data["private_key"])]
        return [asyncssh.read_private_key(settings.supervisor_ssh_private_key)]
    raise ValueError("UNSUPPORTED_SSH_AUTH_MODE")


def ssh_options(data):
    mode = auth_mode(data)
    return {"password": data.get("password") if mode in {"password", "key_and_password"} else None,
            "client_keys": client_keys(data)}


def ubuntu_release(os_release):
    fields = {}
    for line in os_release.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            fields[key] = value.strip().strip('"')
    if fields.get("ID") != "ubuntu":
        raise ValueError("UNSUPPORTED_NODE_OS")
    version = fields.get("VERSION_ID", "")
    if version == "20.04":
        raise ValueError("UBUNTU_20_HOST_BOOTSTRAP_UNAVAILABLE")
    if version not in {"22.04", "24.04"}:
        raise ValueError("UNSUPPORTED_UBUNTU_RELEASE")
    return version


async def test_ssh(db, node, host_key):
    """Test the exact stored SSH identity and sudo mode without changing the remote host."""
    _row, data = await credential(db, node)
    try:
        public_key = asyncssh.import_public_key(host_key)
        async with asyncssh.connect(
            node.hostname,
            port=data["port"],
            username=data["username"],
            **ssh_options(data),
            known_hosts=([public_key], [], []),
            connect_timeout=15,
        ) as connection:
            identity = (await connection.run("id -u", check=True)).stdout.strip()
            if identity == "0":
                return {"success": True, "stage": "complete", "ssh": "connected", "auth_mode": auth_mode(data), "sudo": "root account"}
            password = data.get("sudo_password") if data.get("sudo_password_required") else None
            if not password:
                result = await connection.run("sudo -n -p '' -- true", check=False)
                if result.exit_status == 0:
                    return {"success": True, "stage": "complete", "ssh": "connected", "auth_mode": auth_mode(data), "sudo": "passwordless"}
                return {"success": False, "stage": "sudo", "message": "SSH connected, but passwordless sudo was rejected. Enable sudo password required and enter the sudo password."}
            process = await connection.create_process("sudo -S -p '' -- true")
            process.stdin.write(password + "\n")
            await process.stdin.drain()
            await process.wait_closed()
            if process.exit_status:
                return {"success": False, "stage": "sudo", "message": "SSH connected, but the sudo password was rejected or this account cannot run sudo."}
            return {"success": True, "stage": "complete", "ssh": "connected", "auth_mode": auth_mode(data), "sudo": "password accepted"}
    except asyncssh.PermissionDenied:
        return {"success": False, "stage": "authentication", "auth_mode": auth_mode(data), "message": f"The SSH server rejected {auth_mode(data).replace('_', ' ')} authentication for this username. Check that the matching public key is in authorized_keys, or choose the authentication method actually enabled by sshd. The sudo password is separate."}
    except asyncssh.HostKeyNotVerifiable:
        return {"success": False, "stage": "host identity", "message": "The SSH host key no longer matches the approved identity. Discover and approve the current fingerprint again."}
    except (OSError, asyncio.TimeoutError):
        return {"success": False, "stage": "connection", "message": "The SSH endpoint could not be reached. Check the host, port, routing, and firewall."}
    except (asyncssh.Error, ValueError):
        return {"success": False, "stage": "ssh", "message": "SSH authentication, connection, or host-key verification failed. Check hostname, port, credentials, firewall, and the displayed fingerprint."}


async def provision(node, emit):
    async with Session.begin() as db:
        row, data = await credential(db, node)
        if not row.host_key:
            raise ValueError("SSH_HOST_KEY_APPROVAL_REQUIRED")
        public_key = asyncssh.import_public_key(row.host_key)
        pki = await authority(db)
        identity = issue(pki, str(node.id), urlparse(node.management_url).hostname)
        metrics_identity = issue(pki, "prometheus-" + str(node.id), client=True)
    await emit("SSH connectivity and approved host fingerprint verification")
    options = ssh_options(data)
    async with asyncssh.connect(
        node.hostname,
        port=data["port"],
        username=data["username"],
        **options,
        known_hosts=([public_key], [], []),
        connect_timeout=15,
    ) as connection:
        await emit("SSH host fingerprint verified")
        # sshd reports the peer address after any NAT. This is the address the
        # same Supervisor host normally uses to reach the Agent management API.
        peer_result = await connection.run("printf '%s' \"$SSH_CONNECTION\"", check=False)
        peer_address = peer_result.stdout.split()[0] if peer_result.exit_status == 0 and peer_result.stdout.split() else ""
        try:
            supervisor_peer_cidr = str(ipaddress.ip_network(peer_address, strict=False))
        except ValueError:
            raise ValueError("SUPERVISOR_EGRESS_IP_UNAVAILABLE") from None
        allowed_cidrs = list(dict.fromkeys([
            "127.0.0.0/8", "::1/128", supervisor_peer_cidr,
            *settings.agent_management_allowed_cidrs, *data["management_cidrs"],
        ]))
        await emit(f"Supervisor management source detected: {supervisor_peer_cidr}")
        os_result = await connection.run("cat /etc/os-release", check=True)
        release = ubuntu_release(os_result.stdout)
        await emit(f"Ubuntu {release} detected; checking Python, CPU, RAM and disk")
        python_result = await connection.run(
            "python3 -c 'import sys; print(sys.version_info.major, sys.version_info.minor)'",
            check=False,
        )
        if python_result.exit_status != 0 or tuple(int(v) for v in python_result.stdout.split()) < (3, 10):
            raise ValueError("NODE_PYTHON_TOO_OLD")
        result = await connection.run("getconf _NPROCESSORS_ONLN && free -m && df -Pm /var", check=True)
        await emit(result.stdout[:2000])
        package = Path(settings.agent_package_path)
        if not (package / "install/bootstrap.py").exists():
            raise ValueError("AGENT_PACKAGE_MISSING")
        # Upload to an unpredictable user-owned directory; no secret in shell arguments.
        remote = (await connection.run("mktemp -d /tmp/cdn-bootstrap.XXXXXXXX", check=True)).stdout.strip()
        try:
            async with connection.start_sftp_client() as sftp:
                await sftp.put(str(package), remote + "/agent", recurse=True)
                if Path(settings.maxmind_country_db).is_file():
                    await emit("Uploading supplied MaxMind Country database")
                    await sftp.put(settings.maxmind_country_db, remote + "/GeoLite2-Country.mmdb")
                if Path(settings.maxmind_city_db).is_file():
                    await sftp.put(settings.maxmind_city_db, remote + "/GeoLite2-City.mmdb")
                config = {
                    "agent_id": str(node.id),
                    "agent_name": node.name,
                    "agent_city": node.city,
                    "agent_country": node.country,
                    "agent_provider": node.provider,
                    "management_hostname": urlparse(node.management_url).hostname,
                    "management_ca": pki["ca"],
                    **identity,
                    "metrics_client_cert": metrics_identity["client_cert"],
                    "metrics_client_key": metrics_identity["client_key"],
                    "prometheus_remote_write_url": settings.prometheus_remote_write_url,
                    "prometheus_remote_write_token": settings.prometheus_remote_write_token,
                    "bgp_enabled": settings.bgp_enabled,
                    "firewall": data["firewall"],
                    "management_cidrs": allowed_cidrs,
                    "management_allowed_cidrs": allowed_cidrs,
                    "ssh_port": data["port"],
                }
                async with sftp.open(remote + "/identity.json", "w") as file:
                    await file.write(json.dumps(config))
                await sftp.chmod(remote + "/identity.json", 0o600)
            await emit("Installing packages, services, management identity and controlled NGINX include")
            # Fixed repository installer only; never an Agent shell API. Requires root or NOPASSWD sudo.
            command = (
                ("" if data["username"] == "root" else "sudo -S -p '' -- ")
                + "python3 "
                + shlex.quote(remote + "/agent/install/bootstrap.py")
                + " "
                + shlex.quote(remote)
            )
            process = await connection.create_process(command)
            if data["username"] != "root":
                sudo_password = data.get("sudo_password") if data.get("sudo_password_required") else None
                if sudo_password:
                    process.stdin.write(sudo_password + "\n")
                    await process.stdin.drain()
            async for line in process.stdout:
                # Installer emits only predefined progress lines, never identity contents.
                if line.startswith("CDN_STEP "):
                    await emit(line.strip()[9:])
            await process.wait_closed()
            if process.exit_status:
                raise ValueError("BOOTSTRAP_FAILED")
            await emit("Agent service installed; verifying management API and full desired state next")
        finally:
            await connection.run("rm -rf -- " + shlex.quote(remote), check=False)
