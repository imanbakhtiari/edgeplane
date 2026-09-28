# End-to-end local edge flow

```bash
python3 tools/dev_env.py  # skip if .env already exists
cd supervisor
docker compose -f docker-compose.yml -f docker-compose.lab.yml up --build -d
```

Open http://localhost:8000, use the locally generated admin password, and replace it
on first login. Register `local-edge` with management URL `http://edge-agent:9443`
from the Compose network. Create a real customer and vhost in the UI, then wait for
the target to report SUCCESS. No demo data is seeded.

```bash
curl -si -H 'Host: demo.example.com' http://localhost:8080/
curl -si -H 'Host: demo.example.com' http://localhost:8080/
```

Expect MISS then HIT for a cacheable response. In Vhosts select Demo website and
Purge vhost cache; wait for the target SUCCESS, then repeat curl for MISS.

In Rate Limits edit the global default (for example rate=2, burst=2), wait for sync:

```bash
seq 1 20 | xargs -P 10 -I{} curl -s -o /dev/null -w '%{http_code}\n'   -H 'Host: demo.example.com' http://localhost:8080/
```

Some responses should be 429. Restore a comfortable rate afterwards.
Logs → select sandbox-edge and Demo website → live polling shows per-request JSON.
Use the Real IP policy to explicitly trust your test proxy range; with no trusted
range, a client-supplied X-Forwarded-For must not replace the socket peer IP.

The opt-in automated `agent/tests/test_real_data_plane.py` runs actual NGINX and
Varnish on temporary high-port listeners and proves cache, purge, headers and rate
behavior without changing the host's production services. Additional TLS/Range/
WebSocket and real-host acceptance items are tracked in `acceptance.md`.

The local edge runs real services inside its own container, with no privileged Docker
flag, host mounts or host network. Internet-facing POPs use the systemd host installer
and mTLS described in `agent-provisioning.md`.

## Port 443 regression tests

`agent/tests/test_tls_data_plane.py` runs real NGINX and Varnish in an isolated
container. It binds ports 80/443 only inside that container; do not use host
networking. The browser-side test client verifies a generated test CA and checks
which certificate it receives. Cases cover:

- Automatic/no POP certificate: encrypted passthrough and balancing to HTTPS
  origins, preserving the public SNI and origin certificate.
- POP TLS termination: HTTP and HTTPS origins, cache MISS/HIT, origin SNI,
  403 path blocking and 303 redirects on both HTTP and HTTPS.
- Strict origin verification failure versus explicitly unchecked origin TLS.
- Rejection of unknown SNI, disabled HTTPS, and HTTP-only origins without a POP
  certificate. Plain HTTP is not secure, regardless of origin encryption.
- With `CDN_REAL_COUNTRY_DB` pointing to a GeoLite2 Country MMDB: France blocking
  after restoring the actual client address through the trusted TLS router.

Build `agent/Dockerfile.edge` and run the agent tests with
`CDN_REAL_VARNISHD=/usr/sbin/varnishd`; mount the repository at `/repo` and use
`/repo/agent` as the working directory. Install pytest and pytest-asyncio in that
disposable container. The stream and headers-more modules must be installed.

Existing hosts need service reprovisioning to install the stream module and
master stream include. An agent-only upgrade is insufficient; applying TLS
configuration fails explicitly if the include is missing. Passthrough deliberately
bypasses HTTP rules and cache; use a valid POP certificate for HTTPS rule enforcement.

Vhost saves now queue a durable, coalesced revision-building job. A successful
save means queued, not deployed. The worker validates and activates the revision,
then publishes its acknowledgement to deployment status. Throughput at 100 saves
per second and 100,000 vhosts has not been benchmarked.
