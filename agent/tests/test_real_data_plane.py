"""Opt-in unprivileged, isolated real NGINX + Varnish + HTTP origin E2E."""

import asyncio
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4
import httpx
import pytest
from app.core.settings import Settings
from app.schemas.config import Bundle, Vhost, Origin, OriginRoute, RedirectRule, PathAccessRule
from app.services.deploy import Deployment
from app.system.adapter import SandboxSystemAdapter

pytestmark = pytest.mark.skipif(
    not os.getenv("CDN_REAL_VARNISHD"),
    reason="Set CDN_REAL_VARNISHD and CDN_REAL_VMOD_PATH for real isolated data-plane tests",
)


async def test_real_miss_hit_purge_rate_headers_and_real_ip(tmp_path):
    # varnishd deliberately drops privileges while compiling VCL. pytest creates
    # its per-test directory as 0700, so permit traversal just as production's
    # managed release roots do.
    traversal = tmp_path
    while traversal != Path("/tmp"):
        traversal.chmod(0o755)
        traversal = traversal.parent
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"cached origin asset"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "public, max-age=120")
            self.send_header("ETag", '"demo-v1"')
            self.send_header("X-CDN-Evil", "should-be-stripped")
            self.send_header("X-Origin-Client", self.headers.get("X-Real-IP", ""))
            self.send_header("X-Origin-Custom", self.headers.get("X-Custom", ""))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 18088), Handler)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    class RoutedHandler(Handler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"routed origin")

    routed_origin = ThreadingHTTPServer(("127.0.0.1", 18089), RoutedHandler)
    routed_thread = threading.Thread(target=routed_origin.serve_forever, daemon=True)
    routed_thread.start()
    settings = Settings(
        agent_mode="host",
        state_root=tmp_path / "state",
        nginx_config_root=tmp_path / "nginx",
        varnish_config_root=tmp_path / "varnish",
        log_root=tmp_path / "logs",
        public_port=18080,
        origin_port=18081,
        varnish_port=16081,
        allow_private_origins=True,
    )
    v = Vhost(
        id=uuid4(), name="E2E", domains=["demo.example.com"], origins=[Origin(host="127.0.0.1", port=18088)]
    )
    v.headers.request = {"X-Custom": "works"}
    v.real_ip.forward_to_origin = True
    v.rate.rate = 2
    v.rate.burst = 2
    keyed = v.model_copy(deep=True)
    keyed.id = uuid4()
    keyed.domains = ["keyed.example.com"]
    keyed.rate.key = "header"
    keyed.rate.header = "X-API-Key"
    keyed.rate.rate = 1
    keyed.rate.burst = 1
    keyed.real_ip.trusted_cidrs = ["127.0.0.0/8"]
    routed = Vhost(
        id=uuid4(), name="Routing", domains=["routing.example.com"],
        origins=[Origin(host="127.0.0.1", port=18088), Origin(host="127.0.0.1", port=18089, backup=True)],
        origin_routes=[OriginRoute(path="/api/", origin_index=1)],
        redirect_rules=[RedirectRule(path="/old", target="https://example.net/new")],
        path_rules=[PathAccessRule(path="/private", action="deny")],
    )
    routed.rate.enabled = False
    hosts=[v,keyed,routed]
    geo_module=os.getenv("CDN_REAL_GEO_MODULE")
    if geo_module:
        settings.maxmind_country_db=Path(os.environ["CDN_REAL_MAXMIND"])
        settings.maxmind_city_db=Path(os.environ["CDN_REAL_MAXMIND"])
        from app.schemas.config import GeographicPolicy
        geo=keyed.model_copy(deep=True)
        geo.id=uuid4()
        geo.domains=["geo.example.com"]
        geo.rate.enabled=False
        geo.geo=GeographicPolicy(mode="deny",city_ids=[2861650])
        geo.analytics.geography=True
        geo.path_rules=[PathAccessRule(path="/blocked",action="deny"),PathAccessRule(path="/internal",action="allow_ips",cidrs=["203.0.113.0/24"])]
        country=geo.model_copy(deep=True)
        country.id=uuid4()
        country.domains=["country.example.com"]
        country.geo=GeographicPolicy(mode="deny",countries=["US"])
        hosts.extend([geo,country])
    bundle = Bundle(revision=1, vhosts=hosts)
    deployment = Deployment(settings, SandboxSystemAdapter())
    release, files = await deployment.prepare(bundle)
    # Use a fully isolated master config and unprivileged process-owned PID/log/temp paths.
    module = os.environ["CDN_REAL_HEADERS_MODULE"]
    config = tmp_path / "nginx.conf"
    config.write_text(f"""{('load_module '+geo_module+';') if geo_module else ''}
    load_module {module};
    pid {tmp_path}/nginx.pid;
    error_log {tmp_path}/error.log;
    events {{}}
    http {{
      access_log off;
      client_body_temp_path {tmp_path}/body;
      proxy_temp_path {tmp_path}/proxy;
      fastcgi_temp_path {tmp_path}/fastcgi;
      uwsgi_temp_path {tmp_path}/uwsgi;
      scgi_temp_path {tmp_path}/scgi;
      include {release}/http/*.conf;
    }}""")
    varnish = os.environ["CDN_REAL_VARNISHD"]
    vmods = os.environ["CDN_REAL_VMOD_PATH"]
    compile_command = [
        varnish,
        "-C",
        "-n",
        str(tmp_path / "compile"),
        "-p",
        "vmod_path=" + vmods,
        "-f",
        str(release / "default.vcl"),
    ]
    compiled = subprocess.run(compile_command, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    validated = subprocess.run(["nginx", "-t", "-c", str(config)], capture_output=True, text=True)
    assert validated.returncode == 0, validated.stderr
    proc = subprocess.Popen(
        [
            varnish,
            "-F",
            "-n",
            str(tmp_path / "varnish-runtime"),
            "-a",
            "127.0.0.1:16081",
            "-T",
            "127.0.0.1:16082",
            "-S",
            "none",
            "-p",
            "vmod_path=" + vmods,
            "-f",
            str(release / "default.vcl"),
            "-s",
            "malloc,32m",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    nginx = None
    try:
        nginx = subprocess.Popen(
            ["nginx", "-c", str(config), "-g", "daemon off;"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:18080", headers={"Host": "demo.example.com"}, trust_env=False
        ) as client:
            first = None
            for _ in range(40):
                try:
                    first = await client.get("/asset.txt")
                    if first.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            assert first is not None and first.status_code == 200, (proc.poll(), first)
            assert first.headers["x-cache"] == "MISS"
            await asyncio.sleep(1.1)
            second = await client.get("/asset.txt")
            assert second.headers["x-cache"] == "HIT"
            assert "x-cdn-evil" not in second.headers
            assert second.headers["x-origin-custom"] == "works"
            assert "x-request-id" in second.headers
            await asyncio.sleep(1.1)
            partial = await client.get("/asset.txt", headers={"Range": "bytes=0-5"})
            assert partial.status_code == 206
            assert partial.content == b"cached"
            conditional = await client.get("/asset.txt", headers={"If-None-Match": '"demo-v1"'})
            assert conditional.status_code == 304
            # Give the deliberately low rate limiter time to refill.
            await asyncio.sleep(1.1)
            spoof = await client.get("/other", headers={"X-Forwarded-For": "8.8.8.8", "X-CDN-Vhost": "spoof"})
            assert spoof.headers["x-origin-client"] == "127.0.0.1"
            assert (await client.request("PURGE", "/asset.txt")).status_code == 405
            admin = str(Path(varnish).with_name("varnishadm"))
            if not Path(admin).exists():
                admin = str(Path(varnish).parents[1] / "bin/varnishadm")
            ban = subprocess.run(
                [admin, "-T", "127.0.0.1:16082", "ban", f"obj.http.X-CDN-Vhost == {v.id}"],
                capture_output=True,
                text=True,
            )
            assert ban.returncode == 0, ban.stderr
            await asyncio.sleep(1.1)
            after = await client.get("/asset.txt")
            assert after.headers["x-cache"] == "MISS"
            responses = await asyncio.gather(*[client.get("/asset.txt") for _ in range(12)])
            assert any(r.status_code == 429 for r in responses)
            keyed_headers = {
                "Host": "keyed.example.com",
                "X-API-Key": "client-a",
                "X-Forwarded-For": "8.8.8.8",
            }
            trusted = await client.get("/trusted", headers=keyed_headers)
            assert trusted.headers["x-origin-client"] == "8.8.8.8"
            throttled = await asyncio.gather(
                *[client.get("/trusted", headers=keyed_headers) for _ in range(5)]
            )
            assert any(r.status_code == 429 for r in throttled)
            isolated = await client.get("/trusted", headers={**keyed_headers, "X-API-Key": "client-b"})
            assert isolated.status_code == 200
            if geo_module:
                assert (await client.get("/",headers={"Host":"geo.example.com","X-Forwarded-For":"91.99.101.1"})).status_code==403
                assert (await client.get("/",headers={"Host":"country.example.com","X-Forwarded-For":"8.8.8.8"})).status_code==403
                assert (await client.get("/blocked/test",headers={"Host":"geo.example.com"})).status_code==403
                assert (await client.get("/internal",headers={"Host":"geo.example.com","X-Forwarded-For":"8.8.8.8"})).status_code==403
                assert (await client.get("/internal",headers={"Host":"geo.example.com","X-Forwarded-For":"203.0.113.5"})).status_code==200
            routed_headers = {"Host": "routing.example.com"}
            assert (await client.get("/api/item", headers=routed_headers)).content == b"routed origin"
            assert (await client.get("/normal", headers=routed_headers)).content == b"cached origin asset"
            redirect = await client.get("/old", headers=routed_headers, follow_redirects=False)
            assert redirect.status_code == 303
            assert redirect.headers["location"] == "https://example.net/new"
            assert (await client.get("/private/file", headers=routed_headers)).status_code == 403

        assert (settings.log_root / str(v.id) / "access.json.log").exists()
    finally:
        if nginx:
            nginx.terminate()
            nginx.wait(timeout=10)
        proc.terminate()
        proc.wait(timeout=10)
        origin.shutdown()
        routed_origin.shutdown()
        origin.shutdown()
        origin.server_close()
