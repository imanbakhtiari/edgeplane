# Configuration and rollback

1. Validate structured mutation and acquire transaction-level configuration lock.
2. Update normalized desired state and build a complete active-vhost snapshot.
3. Allocate PostgreSQL sequence revision; deterministic hash excludes revision ID.
4. Encrypt snapshot, append immutable item/history records, queue deployment targets.
5. Worker holds per-node session lock and sends newest supported desired state.
6. Agent flock rejects simultaneous activation. Lower revisions conflict; equal hash
   advances observed revision metadata without rendering/reloading.
7. Render into a fresh release directory; validate VCL compilation and candidate NGINX.
8. Load VCL without using it, record recovery metadata, atomically switch NGINX include
   pointer, validate the complete active NGINX config, then activate VCL.
9. Immediately before reload, run another `nginx -t`. Failure restores the prior
   pointer/VCL and returns detailed failure. **No failed validation path reloads.**
10. Persist applied hash/revision only after successful reload; retain recent releases.

NGINX and Varnish are separate daemons, so their activation cannot be one database-like
atomic operation. The durable activation journal supports recovery after an interrupted
switch. Run staging power-loss/fault-injection testing before production. Direct host
administrators must not concurrently edit the managed includes or reload NGINX outside
this control path.

Rollback reads an immutable historical bundle, restores normalized vhost configuration,
and creates a higher-numbered revision. The Agent never accepts revision regression.
The GUI shows desired versus applied revision, per-node job results, and revision history.
Save Only creates desired state without an immediate job; periodic reconciliation can
still deploy drift. It is not a permanent staging branch.
