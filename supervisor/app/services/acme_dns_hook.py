"""Certbot manual DNS-01 hook backed by the configured PowerDNS API."""
import os
import sys
import time
from urllib.parse import quote
import httpx
from app.core.settings import settings


def headers():
    if os.getenv("ACME_POWERDNS_AUTH_MODE", settings.powerdns_auth_mode) == "basic":
        return {}, (os.getenv("ACME_POWERDNS_USERNAME", settings.powerdns_username), os.getenv("ACME_POWERDNS_PASSWORD", settings.powerdns_password))
    return {"X-API-Key": os.getenv("ACME_POWERDNS_API_KEY", settings.powerdns_api_key)}, None


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    domain = os.environ["CERTBOT_DOMAIN"].rstrip(".").lower()
    validation = os.environ.get("CERTBOT_VALIDATION", "")
    base = os.getenv("ACME_POWERDNS_API_URL", settings.powerdns_api_url).rstrip("/")
    server_id = os.getenv("ACME_POWERDNS_SERVER_ID", settings.powerdns_server_id)
    hdrs, auth = headers()
    with httpx.Client(headers=hdrs, auth=auth, timeout=20) as client:
        zones = client.get(f"{base}/api/v1/servers/{quote(server_id, safe='')}/zones").raise_for_status().json()
        zone = max((z["name"].rstrip(".") for z in zones if domain == z["name"].rstrip(".") or domain.endswith("." + z["name"].rstrip("."))), key=len, default=None)
        if not zone:
            raise RuntimeError(f"No authoritative PowerDNS zone contains {domain}")
        name = f"_acme-challenge.{domain}."
        rrset = {"name": name, "type": "TXT", "changetype": "REPLACE" if action == "present" else "DELETE"}
        if action == "present":
            rrset.update({"ttl": 60, "records": [{"content": f'"{validation}"', "disabled": False}]})
        response = client.patch(
            f"{base}/api/v1/servers/{quote(server_id, safe='')}/zones/{quote(zone + '.', safe='')}",
            json={"rrsets": [rrset]},
        )
        response.raise_for_status()
    if action == "present":
        time.sleep(settings.acme_dns_propagation_seconds)


if __name__ == "__main__":
    main()
