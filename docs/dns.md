# DNS / PowerDNS

Supervisor creates a unique `<vhost-id-prefix>.<managed-zone>` CDN hostname.
Its A/AAAA RRsets contain eligible active edges with public IP addresses. The managed
zone must already exist in your authoritative PowerDNS installation. Customer zones
are never edited by this integration.

The DNS page reads live RRsets from PowerDNS and permits validated A, AAAA, CNAME,
TXT, CAA, MX, NS and SRV replacement or deletion inside that managed zone.
Edgeplane-generated vhost RRsets are shown separately because automatic POP
reconciliation may replace manual edits to those names.

With no anycast setting, generated vhost names publish all healthy POP unicast
addresses. With `DNS_ANYCAST_IPV4` or `DNS_ANYCAST_IPV6`, they publish shared service
addresses which must be announced from each POP by the operator's BGP system.

Configure API URL/key, server ID, managed zone and TTL using deployment environment
or `PUT /api/v1/settings/dns`. API-configured secrets are encrypted in PostgreSQL.
The database configuration takes precedence. Reconciliation uses RRset REPLACE/DELETE,
not record appends, so retries are idempotent. Desired/last-successful records are
stored in `dns_records`. Failed provider calls roll back the database transaction.

Three failed polls remove an edge; five successful healthy polls restore eligibility.
Maintenance, disabled, DEMO or ineligible nodes are excluded. Thresholds are deployment
configuration. DNS propagation still depends on recursive resolver TTL behavior.

Customer subdomain: `www.customer.com CNAME <generated-hostname>`.
Customer zone apex: use public A/AAAA or provider ALIAS/ANAME. Ordinary apex CNAME
is not presented as universally valid. The verification API checks observed CNAMEs;
full provider-specific apex verification is not yet implemented.

PowerDNS tests mock the provider boundary. Live PowerDNS credentials/zone were not
available for external integration acceptance. If changing managed zones, migrate
existing CDN hostnames explicitly; the reconciler will not edit another zone implicitly.
