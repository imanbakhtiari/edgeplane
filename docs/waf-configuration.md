# Coraza WAF configuration — integration status

Vhost GUI and API accept typed `options.waf`: mode `off`, `detection`, or
`blocking`; profile `low`, `standard`, `high`, or `custom`; an exact CRS v4
version; custom paranoia/threshold values; and CRS rule-ID exclusions.
Enabled WAF requires a POP certificate. HTTP and POP-terminated HTTPS use the
same per-vhost rules file. TLS passthrough cannot inspect HTTP requests.

This is a configuration integration, **not an automatically provisioned or
production-verified WAF installation**. Connector installation, real attack
tests, event ingestion/redaction, rule categories and event dashboards remain
outstanding. Do not label these policies as active protection based on a save.

## Installation contract

An operator-provisioned installation uses `/etc/cdn-agent/waf/manifest.json`
with `crs_version`, `engine_version`, and `connector_version`. It must have
`coraza-base.conf` and `crs-<version>/crs-setup.conf` plus `rules/*.conf`.
The base file must come from the pinned engine release and include its body
parsers and malformed-body protections. Review its engine/logging defaults:
the generated policy overrides engine mode, enables request inspection,
disables response-body inspection and audit logging, and configures profiles.
Keep CRS setup profile actions commented to avoid duplicate 900000/900110 IDs.
Do not use mutable `main` branches as production installation artifacts.

Presence checks are not runtime attestations. The loaded NGINX connector must
match the installed NGINX ABI; its library and engine must be compatible.
Every release still goes through NGINX validation and guarded activation.
Missing installation/version causes deployment failure, retaining old state.
Install and test in staging before rolling out to production POPs.

`GET /api/v1/capabilities` reports the configuration contract and installation
metadata. Supervisor checks capabilities before sending an enabled policy.
Disabled WAF is omitted from wire serialization to preserve existing hashes
and compatibility. Upgrade agents before enabling WAF.

Configuration tests cover modes/profiles/exclusions and both listeners, not
actual Coraza attack blocking. NGINX error logging may contain rule details;
review sensitive-data exposure before enabling detection mode.
