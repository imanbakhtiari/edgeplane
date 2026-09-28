"""Entrypoint for the explicitly selected, self-contained data-plane lab image."""

import os
from pathlib import Path
import subprocess
import sys

root = Path("/tmp/cdn-sandbox")
for name in ["nginx", "varnish", "state", "logs"]:
    (root / name).mkdir(parents=True, exist_ok=True)
Path("/etc/nginx/nginx.conf").write_text(
    "include /etc/nginx/modules-enabled/*.conf;\npid /run/nginx.pid;\nevents {}\nstream { include /tmp/cdn-sandbox/nginx/current/stream/*.conf; }\nhttp { include /tmp/cdn-sandbox/nginx/current/http/*.conf; }\n"
)
bootstrap = root / "varnish/bootstrap.vcl"
bootstrap.write_text('vcl 4.1; backend default { .host="127.0.0.1"; .port="8080"; }\n')
active = root / "varnish/current/default.vcl"
subprocess.run(
    [
        "varnishd",
        "-a",
        "127.0.0.1:6081",
        "-T",
        "127.0.0.1:6082",
        "-S",
        "/etc/varnish/secret",
        "-f",
        str(active if active.exists() else bootstrap),
        "-s",
        "malloc,64m",
    ],
    check=True,
)
subprocess.run(["nginx", "-t"], check=True)
subprocess.run(["nginx"], check=True)
subprocess.Popen(
    ["prometheus-node-exporter", "--web.listen-address=127.0.0.1:9100"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
subprocess.Popen(
    [
        "prometheus-nginx-exporter",
        "-nginx.scrape-uri=http://127.0.0.1:8081/stub_status",
        "-web.listen-address=127.0.0.1:9113",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
os.execv(sys.executable, [sys.executable, "-m", "app.main"])
