import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from app.core.settings import settings


def validate_pair(certificate: str, private_key: str):
    cert = x509.load_pem_x509_certificate(certificate.encode())
    key = serialization.load_pem_private_key(private_key.encode(), None)
    public = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(serialization.Encoding.DER, public) != cert.public_key().public_bytes(serialization.Encoding.DER, public):
        raise ValueError("Key mismatch")
    domains = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    return cert, domains


async def issue(domains: list[str], email: str, dns: dict | None = None, challenge: str = "dns-01"):
    dns = dns or {
        "api_url": settings.powerdns_api_url,
        "api_key": settings.powerdns_api_key,
        "auth_mode": settings.powerdns_auth_mode,
        "username": settings.powerdns_username,
        "password": settings.powerdns_password,
        "server_id": settings.powerdns_server_id,
    }
    if challenge not in {"dns-01", "http-01"}:
        raise ValueError("Unsupported ACME challenge")
    if challenge == "dns-01" and not dns.get("api_url"):
        raise ValueError("POWERDNS_API_URL is required for Certbot DNS-01")
    def run():
        with tempfile.TemporaryDirectory(prefix="edgeplane-acme-") as root:
            hook = "acme_http_hook" if challenge == "http-01" else "acme_dns_hook"
            preferred = "http" if challenge == "http-01" else "dns"
            args = [
                "certbot", "certonly", "--manual", "--preferred-challenges", preferred,
                "--manual-auth-hook", f"python -m app.services.{hook} present",
                "--manual-cleanup-hook", f"python -m app.services.{hook} cleanup",
                "--non-interactive", "--agree-tos", "--email", email,
                "--server", settings.acme_directory_url,
                "--config-dir", root + "/config", "--work-dir", root + "/work", "--logs-dir", root + "/logs",
                "--cert-name", "edgeplane", "--force-renewal",
            ]
            for domain in domains:
                args.extend(["-d", domain])
            environment = os.environ.copy()
            environment.update({
                "ACME_POWERDNS_API_URL": dns.get("api_url", ""),
                "ACME_POWERDNS_API_KEY": dns.get("api_key", ""),
                "ACME_POWERDNS_AUTH_MODE": dns.get("auth_mode", "api_key"),
                "ACME_POWERDNS_USERNAME": dns.get("username", ""),
                "ACME_POWERDNS_PASSWORD": dns.get("password", ""),
                "ACME_POWERDNS_SERVER_ID": dns.get("server_id", "localhost"),
            })
            result = subprocess.run(args, capture_output=True, text=True, timeout=600, env=environment)
            if result.returncode:
                raise RuntimeError("CERTBOT_FAILED: " + " ".join(result.stderr.split())[-1000:])
            live = Path(root) / "config/live/edgeplane"
            return (live / "fullchain.pem").read_text(), (live / "privkey.pem").read_text()
    return await asyncio.to_thread(run)
