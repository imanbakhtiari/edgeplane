# PowerDNS Lua routing

DNS → IP addresses includes a bounded Lua preview generator. The same operation
is available through authenticated `POST /api/v1/dns/lua-preview`.
It does not publish records, change customer origins, or
enable Lua/GeoIP on PowerDNS.

Rules are ordered, first match wins. Country groups, continents and network
ACLs all produce an A answer; an explicit fallback handles unknown locations.
The API validates addresses/selectors and does not accept arbitrary Lua.

Example record content for Iran versus the rest of the world:

```lua
A ";if country('IR') then return '185.79.98.207' else return '185.79.97.93' end"
```

Iran, then Europe, then the rest of the world (replace the documentation-only
fallback address before publishing):

```lua
A ";if country('IR') then return '185.79.98.207' elseif continent('EU') then return '185.79.97.93' else return '203.0.113.10' end"
```

For a network rule, use `netmask({'192.0.2.0/24','198.51.100.0/24'})` before the
geographic conditions. For a country group, use `country({'IR','IQ'})`.

Enable Lua records and configure the GeoIP backend with a suitable MaxMind
database on the authoritative server before using geographic functions. Test
against the authoritative server with different ECS inputs and without ECS.
Geographic DNS uses the resolver/ECS location, not necessarily the browser's
IP, and is not a firewall. See the [PowerDNS reference](https://doc.powerdns.com/authoritative/lua-records/functions.html).

## Customer-record safety boundary

Do not replace a customer's A/AAAA records with these examples. Safe CDN
activation still needs a separate stored origin, versioned snapshots of
original/published RRsets, conflict checks for external edits, and controlled
restoration. A CNAME cannot coexist with an A record at the same owner. While
proxied, a new origin address must be stored separately and used for upstream
routing; it must not overwrite the public CDN target. Apex flattening and this
customer-record lifecycle are not implemented by the preview generator.
