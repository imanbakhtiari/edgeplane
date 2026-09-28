"""Start a complete container-local edge data plane and management agent."""

import os
from pathlib import Path
import subprocess
import sys


root = Path("/tmp/cdn-sandbox")
for name in ["nginx/releases/bootstrap/http", "varnish", "state", "logs", "prometheus"]:
    (root / name).mkdir(parents=True, exist_ok=True)

release = root / "nginx/releases/bootstrap"
(release / "http/empty.conf").write_text("# managed vhosts are installed atomically\n")
current = root / "nginx/current"
if not current.exists():
    current.symlink_to(release)

Path("/etc/nginx/nginx.conf").write_text(
    "include /etc/nginx/modules-enabled/*.conf;\n"
    "worker_processes auto;\n"
    "worker_rlimit_nofile 262144;\n"
    "pid /run/nginx.pid;\n"
    "events { worker_connections 16384; multi_accept on; use epoll; }\n"
    "stream { include /tmp/cdn-sandbox/nginx/current/stream/*.conf; }\n"
    "http { sendfile on; tcp_nopush on; tcp_nodelay on; keepalive_timeout 30; "
    "keepalive_requests 10000; reset_timedout_connection on; "
    "include /tmp/cdn-sandbox/nginx/current/http/*.conf; }\n"
)
bootstrap = root / "varnish/bootstrap.vcl"
bootstrap.write_text('vcl 4.1; backend default { .host="127.0.0.1"; .port="8080"; }\n')
active = root / "varnish/current/default.vcl"


def start(args):
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


subprocess.run(
    ["varnishd", "-a", "127.0.0.1:6081", "-T", "127.0.0.1:6082", "-S", "/etc/varnish/secret",
     "-f", str(active if active.exists() else bootstrap),
     "-s", "malloc," + os.getenv("VARNISH_CACHE_SIZE", "1g"),
     "-p", "thread_pools=2", "-p", "thread_pool_min=100", "-p", "thread_pool_max=5000"],
    check=True,
)
subprocess.run(["nginx", "-t"], check=True)
subprocess.run(["nginx"], check=True)
start(["prometheus-node-exporter", "--web.listen-address=127.0.0.1:9100"])
start(["prometheus-nginx-exporter", "-nginx.scrape-uri=http://127.0.0.1:8081/stub_status",
       "-web.listen-address=127.0.0.1:9113"])
start(["prometheus-varnish-exporter", "-web.listen-address=127.0.0.1:9131"])

prometheus_config = root / "prometheus.yml"
prometheus_config.write_text(
    "global:\n  scrape_interval: 15s\n"
    "scrape_configs:\n"
    "  - job_name: cdn-agent\n    static_configs:\n      - targets: ['127.0.0.1:9443']\n"
    "  - job_name: node\n    static_configs:\n      - targets: ['127.0.0.1:9100']\n"
    "  - job_name: nginx\n    static_configs:\n      - targets: ['127.0.0.1:9113']\n"
    "  - job_name: varnish\n    static_configs:\n      - targets: ['127.0.0.1:9131']\n"
)
start(["prometheus", "--config.file=" + str(prometheus_config),
       "--storage.tsdb.path=" + str(root / "prometheus"),
       "--web.listen-address=127.0.0.1:" + os.getenv("POP_PROMETHEUS_PORT", "9091")])
os.execv(sys.executable, [sys.executable, "-m", "app.main"])
