# Prometheus metrics

Every provisioned host agent runs a local Prometheus. It scrapes:

- the Agent on `https://127.0.0.1:9443/metrics` using a dedicated mTLS client certificate;
- Node Exporter on `127.0.0.1:9100`;
- NGINX Exporter on `127.0.0.1:9113`.

The local Prometheus binds `127.0.0.1:9090`. Query it through a protected tunnel,
or configure `PROMETHEUS_REMOTE_WRITE_URL` before provisioning/reprovisioning an
agent. For the bundled Supervisor receiver, the URL path is `/api/v1/write`.
Production remote write must traverse authenticated HTTPS; never publish an
unauthenticated Prometheus receiver on the Internet.

Agent traffic series use bounded labels: `agent_id`, `agent_name`, `pop_city`,
`pop_country`, `provider`, `vhost_id`, `customer_id`, and the two-letter request
`country`. Metrics include requests, bytes sent/received, cache hits, and errors.

Client IP addresses are deliberately not metric labels. A public CDN can receive
millions of unique addresses, and labeling by IP would exhaust Prometheus memory.
Use bounded per-vhost request logs for individual source-IP investigation and
country-labeled metrics for dashboards and alerting.

The Supervisor Prometheus is available locally at `http://127.0.0.1:9090` and
retains 30 days by default. Point Grafana at it for remotely written POP series.
Grafana may also connect to a POP Prometheus over a VPN or SSH tunnel.
