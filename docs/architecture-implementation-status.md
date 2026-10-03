# Architecture implementation status

The supplied DNS/TLS/Coraza document is a target, not a claim that all features
are implemented. Do not advertise an inactive WAF or a merely queued policy as
protection.

## Implemented paths

- HTTP proxying and POP TLS termination to independently configured HTTP/HTTPS
  origins; shared HTTP access, geography, headers, cache and redirect policy.
- Explicit TLS passthrough, labelled as bypassing HTTP inspection. It is not
  the Layer-7 CDN described in the target architecture.
- Certificate assignment, managed certificate workflows, async configuration
  reconciliation and per-node deployment acknowledgements.
- Vhost API/UI desired TLS-mode and policy-scope indicators. These do not assert
  live DNS routing or certificate validity.
- Per-node Varnish storage/log-retention desired state, authenticated API,
  provisioning worker and operator controls; see varnish-operations.md.
- Bounded PowerDNS Lua routing preview in GUI/API; see powerdns-lua-routing.md.
- Typed per-vhost WAF configuration, GUI, capability guard and renderer tests;
  this is not a verified WAF installation. See waf-configuration.md.

## Not yet delivered

- Coraza connector provisioning, verified pinned engine/CRS releases, live attack
  regression tests, redacted security events and WAF dashboards.
- Delegated customer-zone record ownership and separate origin/proxied/effective
  DNS state; atomic origin updates while remaining proxied; apex flattening.
- BGP prefix/peer provisioning and announcements. Entering anycast IPs only
  configures DNS publication; it does not establish routing.
- Automatic ACME as the default onboarding mode for every new vhost, with
  verified ownership and issuance readiness before directing HTTPS traffic.
- Load validation at 100 writes/second and 100,000 vhosts.

Live deployment also requires actual domain delegation/API credentials, ACME
contact/ownership validation or uploaded certificates, and authorized BGP
prefixes/ASN/peer details. Do not infer them from examples in the design document.

## Diagnosis of the supplied September 28 curl results

Both HTTP and HTTPS connected to `185.79.97.142`, the stated origin. Those
requests did not traverse the POP IPs `185.79.98.207` or `185.79.97.93`. Test each
POP with curl `--resolve` while preserving the public Host/SNI before modifying
DNS. Do not change the configured origin address to a POP address.
