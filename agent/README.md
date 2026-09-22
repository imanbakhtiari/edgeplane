# CDN Agent

Stateless FastAPI node management. No SQLAlchemy, PostgreSQL, SQLite or Redis
runtime dependency. Persistent local files are generated configuration releases,
logs, and small applied-state / recovery metadata files.

## Complete local Docker edge

```bash
cd agent
docker compose up --build -d
curl http://localhost:9443/health
curl http://localhost:9443/api/v1/status
curl http://localhost:9091/-/ready
```

This container runs the same Agent configuration and activation code against real
container-local NGINX and Varnish processes. It also runs Prometheus, Node Exporter,
NGINX Exporter and Varnish Exporter. Docker uses host networking, removing bridge
NAT from the traffic path. The local defaults are 8080 for customer HTTP, 8443 for
customer TLS, 9443 for management and loopback-only 9091 for POP Prometheus. Real
POPs normally use 80/443. Persistent configuration, cache metadata and metrics are
kept in the `edge-data` volume.

`MANAGEMENT_ALLOWED_CIDRS` is a JSON list in `agent/.env`. Keep only loopback, the
Supervisor Docker subnet, and explicitly approved operations addresses. The Agent
checks the socket peer and ignores forwarded headers for this decision. Container
mode does not receive `NET_ADMIN` and therefore cannot rewrite the host's global
firewall. Real host-mode provisioning can configure UFW with `firewall=true` and
the same explicit management CIDRs while preserving SSH and public 80/443.

The `sandbox` mode remains available only for isolated unit development:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
AGENT_MODE=sandbox ALLOW_PRIVATE_ORIGINS=true .venv/bin/python -m app.main
```

Sandbox service results are explicitly labeled simulated and never invoke NGINX,
Varnish or systemctl. Do not register a sandbox process as a POP.

## Host mode

Use Supervisor's approved SSH bootstrap against Ubuntu 22.04 or 24.04. The installer
creates a constrained systemd service, installs NGINX/Varnish and exporters,
generates controlled includes with backup/validation, and installs a unique mTLS
identity issued by Supervisor. Start with `python -m app.main`; loading the ASGI
application with an unconfigured HTTP listener is rejected in host mode.

Agent listens on 9443 by default. TLS requires a verified Supervisor client
certificate. NGINX/Varnish internal listeners and exporters bind loopback. Firewall
changes are opt-in and preserve SSH. See [provisioning](../docs/agent-provisioning.md).

## Management API

- `GET /health`, `/ready`, `/metrics`
- `GET /api/v1/status`, `/capabilities`, `/services`, `/config/current`
- `POST /api/v1/config/validate`, `/config/apply`
- `POST /api/v1/services/nginx/validate`, `/services/nginx/reload`
- `POST /api/v1/cache/purge`, `/cache/purge-host`
- `POST /api/v1/origin/test`
- `GET /api/v1/logs?vhost_id=<UUID>&limit=100`

Purge accepts a vhost UUID and optional literal paths, with an optional prefix flag.
It builds bounded `varnishadm ban` expressions, not public PURGE traffic. Log lookup
maps a UUID to an active managed vhost path; arbitrary filenames are not accepted.
The GUI polls logs every 3 seconds as its live-tail equivalent.

## Rendering and safety

The versioned Pydantic contract rejects directive injection, unsafe headers,
invalid domains, out-of-range policy numbers and untrusted CIDRs. Origin DNS is
resolved and checked before numeric addresses are rendered into upstreams;
metadata/link-local addresses are blocked. Private origins require explicit opt-in.
Templates need the headers-more module to remove arbitrary `X-CDN-*` headers;
the Ubuntu installer installs that module.

Candidate `varnishd -C` and candidate `nginx -t -c` run before pointer switching.
The active full NGINX config is tested again. `guarded_reload()` independently
runs `nginx -t` and **returns immediately on any nonzero result**. The reload command
exists only after that successful check. Tests force failure at every validation
stage and assert zero reload calls and an unchanged applied revision/current link.

Run `python -m pytest -q`. Optional real data-plane test environment is described
in [acceptance evidence](../docs/acceptance.md).
