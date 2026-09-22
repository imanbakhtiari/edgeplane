# Edgeplane CDN

A two-application DNS/unicast CDN control plane. **Supervisor** owns desired state
in PostgreSQL. The database-free **Agent** manages revisioned NGINX and Varnish
configuration on each edge. No BGP, Anycast, Redis, or arbitrary shell API.

This repository contains the deployable control plane, durable worker, and edge
agent. Production operation still requires environment-specific capacity testing,
security review, backups, monitoring and a staged rollout; a README cannot replace
an acceptance sign-off.

```mermaid
flowchart TB
  UI[React console] --> S[Supervisor · FastAPI]
  S --> P[(PostgreSQL)]
  W[Durable workers] --> P
  W -->|mTLS desired-state API| A[Stateless Agent]
  W -. approved SSH bootstrap / repair .-> A
  A --> N[Public NGINX :80 / :443]
  N --> V[Varnish · loopback :6081]
  V --> I[Origin NGINX · loopback :8080]
  I --> O[Customer origin pool]
  S --> D[PowerDNS authoritative API]
```

## Start the control plane (no demo data)

```bash
python3 tools/dev_env.py     # once; refuses to overwrite existing .env
cd supervisor
docker compose up --build -d
```

Open **http://localhost:8000**. The normal Compose stack starts only PostgreSQL,
the Supervisor, and its worker; it does not start a sandbox agent or demo origin.
Username is `admin`; retrieve `ADMIN_PASSWORD`
from your local `supervisor/.env`. No shared default password exists. The first
login requires replacing the bootstrap password. Generated secrets are ignored
by Git and Docker build contexts.

For one complete local Docker edge using real NGINX and Varnish processes:

```bash
cd supervisor
docker compose -f docker-compose.yml -f docker-compose.lab.yml up --build -d
curl http://localhost:9443/ready
curl http://localhost:9091/-/ready
```

No demo records are inserted. The local edge runs the production Agent activation
path in `container` mode: candidate VCL compilation, `nginx -t`, atomic revision
activation, Varnish VCL load/use, and guarded NGINX reload. It is isolated from host
services and is intended for local flow verification, not as an Internet-facing POP.

For a real edge on this localhost or at a POP, use **Nodes → Add CDN node** and
enter that Ubuntu 22.04/24.04 host's SSH address plus its HTTPS management URL (normally
`https://<management-ip>:9443`). Verify the displayed SSH fingerprint, then choose
**Approve identity & provision**. The Supervisor installs the host-mode systemd
agent, NGINX, Varnish, exporters, and a unique mTLS identity. `localhost` means the
machine running the Supervisor process; from a Supervisor container, use a host
address reachable from that container instead of `127.0.0.1`.

`supervisor/maxmind/GeoLite2-City.mmdb` is included in the Supervisor image. During
real-agent provisioning it is copied to `/etc/cdn-agent/GeoLite2-City.mmdb`. Country
allow/deny and city rules configured on each vhost are rendered into NGINX, while
country traffic aggregation remains opt-in per vhost.

## Projects

- [`supervisor/`](supervisor/README.md): async FastAPI, normalized SQLAlchemy models,
  Alembic, PostgreSQL workers, SSH provisioning, mTLS PKI, PowerDNS, React console.
- [`agent/`](agent/README.md): FastAPI, versioned wire contract, deterministic Jinja
  templates, host/sandbox adapters, atomic activation, purge, status and bounded logs.
- [`docs/`](docs/architecture.md): architecture, security, operations and test evidence.
- [`tools/`](tools/dev_env.py): development utilities, not a third application.

## Configuration lifecycle

Every vhost/policy mutation acquires a PostgreSQL transaction advisory lock and
creates a monotonically numbered immutable snapshot. Configuration and TLS key
material in snapshots are encrypted. Workers claim durable targets using
`FOR UPDATE SKIP LOCKED` and hold a per-node PostgreSQL session advisory lock
through the operation. A disconnected worker releases its lock, making unfinished
work reclaimable. The Agent adds an inter-process file lock and rejects stale
revisions. Equal content hashes are NOOPs without a reload.

Candidates are rendered into fresh release directories. Varnish compilation and
NGINX candidate validation precede activation. Every NGINX reload has its own
immediately preceding successful `nginx -t`. Validation failure restores the old
pointer and returns failure without reload. Historical rollback creates a new
revision and restores normalized desired vhost data, rather than editing history.

## Operations and deployment

See [Supervisor setup](supervisor/README.md), [Agent setup](agent/README.md),
[real Ubuntu provisioning](docs/agent-provisioning.md),
[security](docs/security.md), [configuration lifecycle](docs/config-lifecycle.md),
[caching](docs/caching.md), [DNS](docs/dns.md), and
[troubleshooting](docs/troubleshooting.md).
[Prometheus metrics and remote write](docs/metrics.md) documents the per-vhost,
country, and POP labels and the safe Grafana connection options.

Production requires external PostgreSQL, HTTPS for the console, unique environment
secrets, an approved SSH host identity, and Agent mTLS. Kubernetes examples show
2 Supervisor and 2 worker replicas. Run the migration Job before rolling out pods.
The primary edge deployment is systemd; the Kubernetes Agent manifest is lab-only.

### Container inventory

The default Supervisor Compose project runs exactly one application server and one
durable worker. Supporting infrastructure is separate by design:

| Container | Purpose | Replica guidance |
|---|---|---|
| `cdn-supervisor-1` | UI, REST API and WebSocket status stream | 1 locally; 2+ behind a load balancer in production |
| `cdn-worker-1` | Claims provisioning/sync/reload jobs from PostgreSQL | exactly 1 locally; scale horizontally in production |
| `cdn-postgres-1` | durable desired state, audit, jobs and node activity | local only; managed HA PostgreSQL in production |
| `cdn-prometheus-1` | central/remote-write metrics aggregation | optional when POP Prometheus is queried directly |
| `cdn-migrate-1` | one-shot schema migration and bootstrap admin | exits with status 0; it is not a running replica |
| `edgeplane-edge-agent-1` | one full local NGINX/Varnish edge | exactly 1 for the localhost flow |

An exited `cdn-migrate-1` with code 0 is expected. It completed successfully and
does not consume a running worker slot.

### Production checklist

- Put the Supervisor behind an HTTPS reverse proxy and set `SUPERVISOR_PUBLIC_URL`
  and exact `ALLOWED_ORIGINS` values.
- Store PostgreSQL, encryption, SSH and CA secrets in a secrets manager; never commit
  `.env`, private keys, certificates, database volumes, or MaxMind license material.
- Use managed PostgreSQL with tested point-in-time recovery, connection pooling and
  alerts for job backlog, replication lag and storage.
- Provision POPs on supported Ubuntu hosts through verified SSH fingerprints. The
  remote installer uses systemd, mTLS, loopback-only exporters, NGINX and Varnish.
- Place customer traffic behind your tested unicast/Anycast and BGP design. Keep the
  management listener private and allow only Supervisor addresses to TCP/9443.
- Stage configuration, monitor failed targets, and test rollback before customer
  migration. Run load/capacity tests using your vhost count and traffic distribution.
- Configure Prometheus retention/remote-write and Grafana independently at each POP;
  alert on Agent, NGINX, Varnish, exporter, disk, certificate and BGP health.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e './supervisor[dev]'
(cd agent && ../.venv/bin/python -m pytest -q)
(cd supervisor && ../.venv/bin/python -m pytest -q)
.venv/bin/ruff check agent supervisor tools
npm --prefix supervisor/frontend ci
npm --prefix supervisor/frontend run build
```

PostgreSQL integration and real data-plane tests are explicitly opt-in, documented
in [test evidence](docs/acceptance.md). A skipped integration test is not evidence
of a working external system.
