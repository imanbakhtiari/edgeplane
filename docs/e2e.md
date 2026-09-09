# End-to-end development demo

```bash
python3 tools/dev_env.py  # skip if .env already exists
cd supervisor
docker compose -f docker-compose.yml -f docker-compose.lab.yml up --build -d
docker compose exec supervisor python -m app.seed_demo
```

Open http://localhost:8000, use the locally generated admin password, replace it on
first login. Jobs show the complete snapshot sent to `sandbox-edge`. The five city
nodes are DEMO records. Wait for the real sandbox target to report SUCCESS.

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

The lab runs services only inside its own container, with no privileged Docker flag,
no host mounts and no host network. It is not a production edge deployment method.
