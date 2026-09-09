# Architecture

Exactly two application projects: Supervisor and Agent. Worker, renderer,
provisioner and React frontend are components of those projects.

Supervisor persists users/sessions, normalized nodes/labels/credentials/observations,
vhosts/domains/origins and policy references, certificates, immutable revisions,
job targets/events, DNS state, settings and audit records in PostgreSQL.
JSONB is used for typed policy documents and origin settings, not as a substitute
for resource relationships. UUID primary keys identify business resources;
PostgreSQL bigint sequences identify revisions.

The immutable snapshot is the wire contract sent over Agent HTTPS. Entire snapshots
are AES-GCM encrypted because they can contain certificate private keys. Preview
items omit TLS key material. A database trigger rejects history UPDATE/DELETE.

Management transport after bootstrap is HTTP API over mTLS. SSH only installs or
repairs the host. Agent holds no business records; replacing a node and sending
the latest full snapshot reconstructs all active vhosts.

Job targets use SKIP LOCKED row selection plus per-node session advisory locks.
No time-only lease expiry allows a second worker to race a still-running first
worker. A session loss releases the claim. Agent flock plus monotonically ordered
revisions add an independent guard at the edge.

The frontend uses HttpOnly sessions, CSRF checks and server-enforced roles. It
polls jobs and observed node data, rather than assuming a save is already applied.
Real data and DEMO nodes are distinct. Every active non-DEMO edge receives the
entire desired state; maintenance affects DNS eligibility, not synchronization.

Extension boundaries: versioned config schema, system adapters, DNS reconciliation,
provisioning job handler and policy renderers. GeoDNS, Anycast, WAF, shield nodes,
HTTP/3, external metrics/log backends and tenants are future features, not simulated
capabilities of this release.
