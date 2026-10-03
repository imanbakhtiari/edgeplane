# Varnish storage and retention

An administrator can use Nodes → Varnish storage & log retention, or the
authenticated Supervisor API:

```
GET /api/v1/agents/{node_id}/varnish
PUT /api/v1/agents/{node_id}/varnish
```

PUT body:

```json
{
  "storage": "file",
  "size_mb": 1024,
  "log_retention_days": 7,
  "acknowledge_restart": true
}
```

Storage is `file` or `malloc`; size is MiB (256–1048576). The file path is fixed
to `/var/lib/cdn-agent/cache`; arbitrary filesystem paths and daemon arguments
are not accepted. Capacity checks run on the target host before activation.

The API saves desired settings and queues `VARNISH_CONFIG`. The approved SSH
provisioning transport invokes `bootstrap.py --varnish-only`, not an arbitrary
shell command. An already provisioned and synchronized Ubuntu node is required.
The action validates VCL and logrotate, changes the managed systemd override,
restarts Varnish only when its startup configuration changed, and restores the
old override if activation fails. Restarting discards the cache and may briefly
interrupt requests. It is not a graceful VCL-only reload.

Unchanged storage and retention-only updates do not restart Varnish. Normal
service reprovisioning preserves saved storage settings. The Agent reports the
last successfully applied settings after upgrade/poll; `desired` and `reported`
are separate, and a queued job is not proof of activation.

Retention applies to rotated files matching `/var/log/nginx/cdn/*/*.log` through
daily logrotate, bounded by rotation count and maximum age. NGINX reopens its
logs after rotation. Retention does not delete live files or the Varnish cache
backing file. Varnish object expiry is managed by the vhost Cache policy's TTL,
grace and cacheability controls, not by deleting filesystem objects.

Storage semantics: https://www.varnish.org/docs/reference/varnishd/
