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
