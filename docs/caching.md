# Cache behavior

Traffic: public NGINX → loopback Varnish → loopback NGINX origin pool → origin.
TLS/SNI, timeouts, Host override and passive OSS upstream failover live in the origin
proxy. No NGINX Plus active health checks are claimed.

GET/HEAD can cache; other methods pass. Authorization, any Cookie, Upgrade, request
no-cache/no-store and Pragma no-cache bypass. Set-Cookie/private/no-store/no-cache
responses are uncacheable. All cookies bypass intentionally, even when a preset
lists specific known session cookies. Dynamic fallback TTL is 120s, static fallback
3600s, grace 300s, keep 60s, maximum TTL 86400s in the generic policy. Origin TTL
wins unless explicit override is enabled. Allowed response codes are policy-controlled.

Preset policies: Generic Website, Static Assets, WordPress, WooCommerce, API No Cache,
Aggressive Static CDN. WordPress/WooCommerce path prefixes apply only to their policies.
Query stripping and ignored parameter controls are opt-in because they can merge keys.
Host and vhost UUID are included in cache identity. WebSocket traffic uses the direct
internal-origin path rather than Varnish.

X-Cache and X-Cache-Hits originate in Varnish. X-Request-ID and X-Served-By originate
in NGINX. Internal X-CDN-* headers are removed before delivery. Logging omits cookies,
authorization and raw query strings by default.

Purge uses local authenticated management commands to create exact-path/prefix/vhost
bans. Public PURGE is rejected. A vhost purge is distributed to all active nodes with
per-target results. OSS ban invalidation is asynchronous in cache cleanup, but future
lookups match the ban immediately.

Rate policies support IP or validated header keys, missing-header fallback to IP,
burst, nodelay, dry-run, rejection status, CIDR exemptions and path-prefix limits.
Real-IP accepts only explicitly trusted peers. Custom request headers are literal
configured values; arbitrary client headers are not transparently forwarded unless
listed in the controlled forwarding template.

Origin DNS is pinned when a new content hash is applied; periodic origin-address
refresh independent of config changes is a remaining operational limitation.
