# Supervisor

Python 3.12+, FastAPI, Pydantic 2, async SQLAlchemy 2, asyncpg/PostgreSQL, Alembic,
asyncssh, AES-GCM and Argon2. React/TypeScript/Vite/Tailwind/TanStack Query console
is compiled in a multi-stage Docker build and served by FastAPI.

## Compose

From the repository root run `python3 tools/dev_env.py` once, then:

```bash
cd supervisor
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 supervisor worker
```

Console: http://localhost:8000. OpenAPI: `/docs` and `/openapi.json`.
`/health` is a cheap liveness endpoint; `/ready` checks PostgreSQL.

The one-shot migration container waits for PostgreSQL health, runs
`alembic upgrade head`, then `python -m app.seed`. API and worker wait for successful
migration completion; there is no arbitrary database-start sleep.

## Local Python / frontend development

```bash
# repository root
python3 -m venv .venv
.venv/bin/pip install -e './supervisor[dev]'
cd supervisor
# Set DATABASE_URL to a dedicated PostgreSQL database in .env.
../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.seed
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# separate terminal, same directory/environment
../.venv/bin/python -m app.workers.runner
# frontend terminal
cd frontend
npm ci
npm run dev
```

Set `SUPERVISOR_PUBLIC_URL` to the actual browser origin (including the Vite port
when using Vite). HttpOnly session cookies and an in-memory CSRF token are used;
no authentication token is stored in localStorage. ADMIN manages credentials,
users, certificates and shared policies. OPERATOR manages vhosts and operational
actions. VIEWER is read-only. Bootstrap password change is mandatory.

## API areas

`/api/v1/auth`, `/users`, `/agents`, `/vhosts`, `/cache-policies`,
`/rate-limit-policies`, `/real-ip-policies`, `/header-policies`, `/certificates`,
`/jobs`, `/config-revisions`, `/dns`, `/audit`, `/settings`, `/sync`.
See [generated endpoint inventory](api-endpoints.md) for exact methods and routes.

## Worker behavior

Run `python -m app.workers.runner` in one or more separate processes. The same
PostgreSQL database and encryption key must be used by every replica. No Redis or
replica-local durable state is used. A target remains RUNNING if its worker dies;
its session advisory lock disappears, allowing another worker to reclaim it.
The Agent hash/order checks make replay safe. Partial fanout is reported as PARTIAL.

Periodic observations store health and applied revision. Three failures remove
DNS eligibility, five successes restore it. Maintenance removes DNS eligibility
from published records while preserving management and synchronization.

## Production

Use `ENVIRONMENT=production`; terminate trusted HTTPS at your platform edge and
set the public URL. Provision secrets through your secret manager, never a checked-in
file. Persist/back up PostgreSQL and the master encryption key together. Internal
CA material lives encrypted in `system_settings`, shared across all replicas.
Do not use the demonstration encryption key from tests.

Kubernetes: replace image names and Secret placeholders, apply `k8s/config.yaml`,
run and wait for `k8s/migrate.yaml`, then apply API and worker Deployments.
The Service is internal; attach your existing HTTPS gateway. Read-only roots use
`emptyDir` only for transient TLS library files, not persistent application state.

Demo data: `python -m app.seed_demo`, development only. See root docs for limitations.
