# Troubleshooting

- **Bootstrap password change required**: sign in, replace the bootstrap password,
  and sign in again. Infrastructure access remains blocked until then.
- **CSRF check failed**: fetch `/api/v1/auth/me` and send its csrf value in
  `X-CSRF-Token` with the HttpOnly session cookie. Match the configured public origin.
- **AGENT_OFFLINE**: inspect management routing, TLS CA/hostname, Agent systemd logs,
  firewall and certificate expiry. One poll failure does not remove DNS membership.
- **NGINX VALIDATION FAILED / CONFIG NOT APPLIED**: inspect the target result and
  generated line reference. The previous applied revision remains in service. Fix
  the structured policy and create a new revision; do not manually force reload.
- **unknown log format cdn**: managed base config must sort before vhost files
  (`00-base.conf`). Do not rename generated files.
- **unknown directive more_clear_headers**: install Ubuntu's headers-more module
  and ensure the main config loads `/etc/nginx/modules-enabled/*.conf`.
- **VARNISH_VALIDATION_FAILED**: check compiler availability, VCL feature support,
  filesystem access for the Varnish compiler user and candidate VCL result.
- **SSH provisioning failed**: check approved host key, supported Ubuntu version,
  credentials, sudo availability and the last durable step event. Private output
  from SSH exceptions is deliberately not persisted.
- **Jobs remain RUNNING**: verify a worker is running. A replacement can reclaim
  after the old PostgreSQL connection closes. Never force two workers through a lock.
- **Origin blocked**: metadata/link-local is always rejected. Enable private origins
  explicitly for an intended private network, rather than weakening URL validation.
- **Sandbox reports simulated success**: expected in default Compose. Select the
  explicit data-plane lab overlay for real caching.

Useful commands: `docker compose ps`, `docker compose logs --tail=100 worker`,
`systemctl status cdn-agent`, `journalctl -u cdn-agent`, `nginx -t`,
`varnishadm vcl.list`. Manual commands are for diagnosis; normal changes go through
Supervisor. Keep secrets out of copied diagnostics.
