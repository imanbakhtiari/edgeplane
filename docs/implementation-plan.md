# Implementation and acceptance plan
1. Typed, versioned wire contract; PostgreSQL models and migrations; session security.
2. Deterministic NGINX/VCL templates, host/sandbox adapters, atomic activation and failure tests.
3. Supervisor CRUD, immutable snapshots, durable jobs, reconciliation and mTLS.
4. Pinned SSH host identity, idempotent bootstrap, encrypted credentials, DNS.
5. Graphite React console with resource forms, progress, drift and operational actions.
6. Compose, systemd, Kubernetes, documentation, tests and acceptance evidence.

Only `supervisor/` and `agent/` are application projects. Wire schemas are copied
with a parity test to keep independent packages without a third application.
