"""SSH is exclusively bootstrap/repair transport. Host key approval is explicit."""

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
    key = await asyncssh.get_server_host_key(node.hostname, port=data["port"])
    return {
        "fingerprint": key.get_fingerprint(),
        "host_key": key.export_public_key().decode().strip(),
        "approved": row.host_key == key.export_public_key().decode().strip(),
    }


async def provision(node, emit):
    async with Session.begin() as db:
        row, data = await credential(db, node)
        if not row.host_key:
            raise ValueError("SSH_HOST_KEY_APPROVAL_REQUIRED")
        public_key = asyncssh.import_public_key(row.host_key)
        pki = await authority(db)
        identity = issue(pki, str(node.id), urlparse(node.management_url).hostname)
    await emit("SSH connectivity and approved host fingerprint verification")
    keys = [asyncssh.import_private_key(data["private_key"])] if data.get("private_key") else []
    async with asyncssh.connect(
        node.hostname,
        port=data["port"],
        username=data["username"],
        password=data.get("password"),
        client_keys=keys,
        known_hosts=([public_key], [], []),
        connect_timeout=15,
    ) as connection:
        await emit("SSH host fingerprint verified")
        os_result = await connection.run("cat /etc/os-release", check=True)
        if "ID=ubuntu" not in os_result.stdout or 'VERSION_ID="24.04"' not in os_result.stdout:
            raise ValueError("Only Ubuntu 24.04 is currently supported")
        await emit("Ubuntu 24.04 detected; checking CPU, RAM and disk")
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
                if Path(settings.maxmind_city_db).is_file():
                    await emit("Uploading supplied MaxMind City database")
                    await sftp.put(settings.maxmind_city_db, remote + "/GeoLite2-City.mmdb")
                config = {
                    "agent_id": str(node.id),
                    "agent_name": node.name,
                    "management_ca": pki["ca"],
                    **identity,
                    "firewall": data["firewall"],
                    "management_cidrs": data["management_cidrs"],
                    "ssh_port": data["port"],
                }
                async with sftp.open(remote + "/identity.json", "w") as file:
                    await file.write(json.dumps(config))
                await sftp.chmod(remote + "/identity.json", 0o600)
            await emit("Installing packages, services, management identity and controlled NGINX include")
            # Fixed repository installer only; never an Agent shell API. Requires root or NOPASSWD sudo.
            command = (
                ("" if data["username"] == "root" else "sudo -n ")
                + "python3 "
                + shlex.quote(remote + "/agent/install/bootstrap.py")
                + " "
                + shlex.quote(remote)
            )
            process = await connection.create_process(command)
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
