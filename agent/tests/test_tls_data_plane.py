"""Real port 80/443 routing in an isolated container, never on production nodes.

Run with CDN_REAL_VARNISHD=/usr/sbin/varnishd inside Dockerfile.edge's image.
The private test CA is trusted by the client, so HTTPS tests verify certificates.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from app.core.settings import Settings
from app.schemas.config import Bundle, Origin, TLS, Vhost, PathAccessRule, RedirectRule, GeographicPolicy
from app.services.deploy import Deployment
from app.system.adapter import SandboxSystemAdapter

pytestmark = pytest.mark.skipif(not os.getenv("CDN_REAL_VARNISHD"), reason="Requires isolated real data-plane container")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def certificate(tmp_path, name, domains, issuer=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer[0].subject if issuer else subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
               .not_valid_after(now + timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True))
    if domains:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]), False)
    cert = builder.sign(issuer[1] if issuer else key, hashes.SHA256())
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    cert_path, key_path = tmp_path / (name + ".pem"), tmp_path / (name + ".key")
    cert_path.write_text(pem)
    key_path.write_text(private)
    return cert, key, cert_path, key_path


@contextmanager
def origin_server(label, cert=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"origin": label, "host": self.headers.get("Host"),
                               "sni": getattr(self.connection, "test_sni", None),
                               "client": self.headers.get("X-Real-IP"),
                               "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Cache-Control", "public, max-age=120")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert[2], cert[3])
        context.set_servername_callback(lambda sock, name, _: setattr(sock, "test_sni", name))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(host, ca, https=True, path="/asset", proxy_source=None, port=None):
    sock = socket.create_connection(("127.0.0.1", port or (443 if https else 80)), timeout=3)
    peer = None
    try:
        if https:
            if proxy_source:
                sock.sendall(f"PROXY TCP4 {proxy_source} 127.0.0.1 50000 443\r\n".encode())
            sock = ssl.create_default_context(cafile=str(ca)).wrap_socket(sock, server_hostname=host)
            peer = sock.getpeercert(binary_form=True)
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        response = HTTPResponse(sock)
        response.begin()
        return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read(), peer
    finally:
        sock.close()


async def test_real_443_passthrough_termination_and_origin_protocols(tmp_path):
    traversal = tmp_path
    while traversal != Path("/tmp"):
        traversal.chmod(0o755)
        traversal = traversal.parent
    ca = certificate(tmp_path, "test-ca", [])
    origin_cert = certificate(tmp_path, "origin", ["pass.example", "origin.example"], ca)
    pop_cert = certificate(tmp_path, "pop", ["http-pop.example", "https-pop.example", "strict.example", "geo.example"], ca)
    tls = TLS(certificate=pop_cert[2].read_text(), private_key=pop_cert[3].read_text())
    with origin_server("http") as http_port, origin_server("https-a", origin_cert) as https_a, origin_server("https-b", origin_cert) as https_b:
        def host(domain, scheme, port, **options):
            value = Vhost(id=uuid4(), name=domain, domains=[domain],
                          origins=[Origin(host="127.0.0.1", port=port, scheme=scheme)], **options)
            value.rate.enabled = False
            value.real_ip.forward_to_origin = True
            return value
        passthrough = host("pass.example", "https", https_a)
        passthrough.origins.append(Origin(host="127.0.0.1", port=https_b, scheme="https"))
        http_pop = host("http-pop.example", "http", http_port, tls=tls, tls_mode="terminate")
        http_pop.path_rules = [PathAccessRule(path="/admin/", action="deny")]
        http_pop.redirect_rules = [RedirectRule(path="/mamad/", match="prefix", target="https://other.example/")]
        https_pop = host("https-pop.example", "https", https_a, tls=tls)
        https_pop.origins[0].sni = "origin.example"
        plain = host("plain.example", "http", http_port)
        disabled = host("disabled.example", "https", https_a, tls_mode="http_only")
        strict = host("strict.example", "https", https_a, tls=tls)
        strict.origins[0].sni = "origin.example"
        strict.origins[0].tls_verify = True  # Test CA is deliberately not in the POP trust store.
        settings = Settings(_env_file=None, agent_mode="host", state_root=tmp_path / "state",
                            nginx_config_root=tmp_path / "nginx", varnish_config_root=tmp_path / "varnish",
                            log_root=tmp_path / "logs", public_port=80, tls_port=443,
                            origin_port=free_port(), varnish_port=free_port(),
                            tls_termination_port=free_port(), tls_passthrough_port=free_port(),
                            allow_private_origins=True)
        deployment = Deployment(settings, SandboxSystemAdapter())
        hosts = [passthrough, http_pop, https_pop, plain, disabled, strict]
        if os.getenv("CDN_REAL_COUNTRY_DB"):
            settings.maxmind_country_db = Path(os.environ["CDN_REAL_COUNTRY_DB"])
            geo = host("geo.example", "http", http_port, tls=tls)
            geo.geo = GeographicPolicy(mode="deny", countries=["FR"])
            hosts.append(geo)
        release, _ = await deployment.prepare(Bundle(revision=1, vhosts=hosts))
        config = release / "candidate.conf"
        checked = subprocess.run(["nginx", "-t", "-c", str(config)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
        varnish_log = (tmp_path / "varnish.log").open("w")
        nginx_log = (tmp_path / "nginx.log").open("w")
        varnish = subprocess.Popen([os.environ["CDN_REAL_VARNISHD"], "-F", "-n", str(tmp_path / "varnish-runtime"),
                                    "-a", f"127.0.0.1:{settings.varnish_port}", "-T", "none",
                                    "-f", str(release / "default.vcl"), "-s", "malloc,32m"],
                                   stdout=varnish_log, stderr=varnish_log)
        nginx = subprocess.Popen(["nginx", "-c", str(config), "-g", "daemon off;"],
                                 stdout=nginx_log, stderr=nginx_log)
        try:
            for _ in range(60):
                try:
                    ready = await asyncio.to_thread(request, "plain.example", ca[2], False)
                    if ready[0] == 200:
                        break
                except (OSError, ssl.SSLError):
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail((tmp_path / "nginx.log").read_text() + (tmp_path / "varnish.log").read_text())

            # No POP certificate: browser validates the ORIGIN certificate; SNI and bytes unchanged.
            seen = set()
            for _ in range(6):
                status, headers, body, peer = await asyncio.to_thread(request, "pass.example", ca[2])
                assert status == 200
                assert peer == origin_cert[0].public_bytes(serialization.Encoding.DER)
                assert "x-cache" not in headers
                payload = json.loads(body)
                assert payload["sni"] == "pass.example"
                assert payload["host"] == "pass.example"
                seen.add(payload["origin"])
            assert seen == {"https-a", "https-b"}, "Passthrough must load balance TLS connections"

            # POP termination independently supports HTTP and HTTPS origin transports.
            for domain, expected in [("http-pop.example", "http"), ("https-pop.example", "https-a")]:
                status, headers, body, peer = await asyncio.to_thread(request, domain, ca[2])
                assert status == 200
                assert peer == pop_cert[0].public_bytes(serialization.Encoding.DER)
                assert json.loads(body)["origin"] == expected
                assert json.loads(body)["client"] == "127.0.0.1"
                assert headers["x-cache"] == "MISS"
                again = await asyncio.to_thread(request, domain, ca[2])
                assert again[1]["x-cache"] == "HIT"
            assert json.loads((await asyncio.to_thread(request, "https-pop.example", ca[2]))[2])["sni"] == "origin.example"
            for https in [False, True]:
                assert (await asyncio.to_thread(request, "http-pop.example", ca[2], https, "/admin/"))[0] == 403
                redirected = await asyncio.to_thread(request, "http-pop.example", ca[2], https, "/mamad/hello")
                assert redirected[0] == 303
                assert redirected[1]["location"] == "https://other.example/"
            if os.getenv("CDN_REAL_COUNTRY_DB"):
                # Exercise the same loopback PROXY-protocol hop used by the TLS router.
                france = await asyncio.to_thread(request, "geo.example", ca[2], True, "/",
                                                 "213.136.80.38", settings.tls_termination_port)
                assert france[0] == 403, "MaxMind FR denial must run after restoring the TLS client's address"
                allowed = await asyncio.to_thread(request, "geo.example", ca[2], True, "/",
                                                  "8.8.8.8", settings.tls_termination_port)
                assert allowed[0] == 200

            # Port 80 remains a cached HTTP proxy, including when its origin uses HTTPS.
            for domain in ["plain.example", "pass.example", "http-pop.example", "https-pop.example"]:
                status, _, body, peer = await asyncio.to_thread(request, domain, ca[2], False, "/http")
                assert status == 200 and peer is None
                assert json.loads(body)["host"] == domain

            # Verification is honored when enabled; it is not a browser-certificate bypass.
            assert (await asyncio.to_thread(request, "strict.example", ca[2]))[0] == 502
            for domain in ["unknown.example", "plain.example", "disabled.example"]:
                with pytest.raises((ssl.SSLError, OSError)):
                    await asyncio.to_thread(request, domain, ca[2])
            # Unknown/unserved SNI must never fall through to another customer's certificate/origin.
        finally:
            nginx.terminate()
            varnish.terminate()
            nginx.wait(timeout=10)
            varnish.wait(timeout=10)
            nginx_log.close()
            varnish_log.close()
