from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.core.settings import Settings
from app.schemas.config import Bundle, Vhost, Origin, RatePolicy, HeaderPolicy, RealIPPolicy, TLS
from app.schemas.config import RedirectRule, OriginRoute
from app.services.deploy import Deployment
from app.system.adapter import SandboxSystemAdapter, Result
from app.renderers.config import vhost_filename


@pytest.fixture
def settings(tmp_path):
    return Settings(
        agent_mode="host",
        nginx_config_root=tmp_path / "nginx",
        varnish_config_root=tmp_path / "varnish",
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
        allow_private_origins=True,
        public_port=80,
        tls_port=443,
    )


@pytest.fixture
def bundle():
    return Bundle(
        revision=1,
        vhosts=[
            Vhost(id=uuid4(), name="Demo", domains=["demo.example.com"], origins=[Origin(host="127.0.0.1")])
        ],
    )


class Runner(SandboxSystemAdapter):
    def __init__(self, fail_at=None):
        super().__init__()
        self.tests = 0
        self.fail_at = fail_at

    async def run(self, *args):
        result = await super().run(*args)
        if "-t" in args:
            self.tests += 1
            if self.tests == self.fail_at:
                return Result(1, stderr="invalid number of arguments: candidate.conf:28")
        return result


@pytest.mark.parametrize("failure", [1, 2, 3])
async def test_reload_never_called_after_failed_nginx_validation(settings, bundle, failure):
    runner = Runner()
    deploy = Deployment(settings, runner)
    assert (await deploy.apply(bundle))["success"]
    old = deploy.current()
    old_link = (settings.nginx_config_root / "current").resolve()
    runner.calls.clear()
    runner.tests = 0
    runner.fail_at = failure
    candidate = bundle.model_copy(deep=True)
    candidate.revision = 2
    candidate.vhosts[0].name = "Changed"
    result = await deploy.apply(candidate)
    assert result["success"] is False
    assert result["validation_failed"] is True
    assert not any("-s" in call and "reload" in call for call in runner.calls)
    assert deploy.current() == old
    assert (settings.nginx_config_root / "current").resolve() == old_link


async def test_manual_reload_guard(settings):
    runner = Runner(fail_at=1)
    result = await Deployment(settings, runner).guarded_reload()
    assert not result["success"]
    assert len(runner.calls) == 1
    assert "-t" in runner.calls[0]


async def test_missing_installed_tls_router_never_claims_applied(settings, bundle):
    from app.system.adapter import HostSystemAdapter

    class MissingRouter(HostSystemAdapter):
        def __init__(self):
            self.calls = []

        async def run(self, *args):
            self.calls.append(args)
            return Result(stdout="http { include current/http/*.conf; }")

    bundle.vhosts[0].origins[0].scheme = "https"
    bundle.vhosts[0].origins[0].port = 443
    runner = MissingRouter()
    deploy = Deployment(settings, runner)
    before = deploy.current()
    result = await deploy.apply(bundle)
    assert result["error"] == "TLS_ROUTER_NOT_INSTALLED"
    assert not result["success"]
    assert deploy.current() == before
    assert not any("reload" in call for call in runner.calls)


async def test_same_hash_noop_and_older_rejected(settings, bundle):
    runner = Runner()
    deploy = Deployment(settings, runner)
    await deploy.apply(bundle)
    runner.calls.clear()
    legacy = deploy.current()
    legacy.pop("vhost_hashes")
    legacy.pop("telemetry")
    deploy.save(legacy)
    bundle.revision = 2
    assert (await deploy.apply(bundle))["noop"]
    assert not runner.calls
    assert deploy.current()["revision"] == 2
    assert deploy.current()["vhost_hashes"] == {
        str(bundle.vhosts[0].id): bundle.vhosts[0].digest()
    }
    assert str(bundle.vhosts[0].id) in deploy.current()["telemetry"]
    bundle.revision = 1
    with pytest.raises(HTTPException) as caught:
        await deploy.apply(bundle)
    assert caught.value.status_code == 409


def test_renderer_version_changes_bundle_digest(bundle):
    original = bundle.digest()
    bundle.renderer_version += 1
    assert bundle.digest() != original


async def test_varnish_failure_preserves_old(settings, bundle):
    class VarnishFailure(Runner):
        async def run(self, *args):
            result = await super().run(*args)
            return Result(1, stderr="VCL syntax error") if "-C" in args else result

    runner = VarnishFailure()
    deploy = Deployment(settings, runner)
    result = await deploy.apply(bundle)
    assert not result["success"]
    assert deploy.current()["revision"] == 0
    assert not any("vcl.use" in c or "reload" in c for c in runner.calls)


async def test_process_lock(settings):
    one = Deployment(settings, Runner())
    two = Deployment(settings, Runner())
    with one.lock():
        with pytest.raises(HTTPException) as caught:
            with two.lock():
                pass
    assert caught.value.status_code == 409


async def test_deterministic_render_and_preview(settings, bundle):
    deploy = Deployment(settings, Runner())
    result = await deploy.apply(bundle, validate_only=True)
    assert result["success"]
    assert deploy.current()["revision"] == 0
    files = result["preview"]
    nginx = files[f"http/{vhost_filename(bundle.vhosts[0])}"]
    assert "proxy_pass_request_headers off" in nginx
    assert "listen 127.0.0.1:8080" in nginx
    assert "limit_req_status 429" in nginx
    assert "req.http.Authorization || req.http.Cookie" in files["default.vcl"]
    assert "hash_data(req.http.host)" in files["default.vcl"]
    assert "beresp.http.Set-Cookie" in files["default.vcl"]
    assert bundle.digest() == bundle.model_copy(update={"revision": 99}).digest()


async def test_applied_vhosts_lists_named_active_configuration(settings, bundle):
    deploy = Deployment(settings, Runner())
    result = await deploy.apply(bundle)
    assert result["success"]
    applied = deploy.applied_vhosts()
    assert len(applied) == 1
    assert applied[0]["id"] == str(bundle.vhosts[0].id)
    assert applied[0]["name"] == "Demo"
    assert applied[0]["domains"] == ["demo.example.com"]
    assert applied[0]["filename"].startswith("demo--")
    assert "server_name demo.example.com;" in applied[0]["content"]


async def test_candidate_validation_uses_writable_agent_temp_directories(settings, bundle):
    deploy = Deployment(settings, Runner())
    result = await deploy.apply(bundle, validate_only=True)
    candidate = result["preview"]["candidate.conf"]
    candidate_temp = settings.state_root / "nginx-candidate"

    assert "/var/lib/nginx" not in candidate
    for name, directive in (
        ("body", "client_body_temp_path"),
        ("proxy", "proxy_temp_path"),
        ("fastcgi", "fastcgi_temp_path"),
        ("uwsgi", "uwsgi_temp_path"),
        ("scgi", "scgi_temp_path"),
    ):
        assert f"{directive} {candidate_temp / name};" in candidate
        assert (candidate_temp / name).is_dir()


async def test_varnish_can_read_candidate_vcl_with_restrictive_umask(settings, bundle):
    import os

    previous = os.umask(0o027)
    try:
        deploy = Deployment(settings, Runner())
        release, _files = await deploy.prepare(bundle)
    finally:
        os.umask(previous)
    for directory in (settings.nginx_config_root, release.parent, release):
        assert directory.stat().st_mode & 0o001, directory
    assert (release / "default.vcl").stat().st_mode & 0o004
    assert not (release / "tls").exists()


@pytest.mark.parametrize("value", ["example.com; root /tmp", "evil\n.com", 'a".com', "../nginx.conf"])
def test_domain_injection_rejected(value, bundle):
    from pydantic import ValidationError

    data = bundle.vhosts[0].model_dump()
    data["domains"] = [value]
    with pytest.raises(ValidationError):
        Vhost(**data)


@pytest.mark.parametrize("value", ["X-CDN-Test", "Host", "Authorization", "bad\nHeader"])
def test_reserved_headers(value):
    with pytest.raises(ValueError):
        HeaderPolicy(request={value: "test"})


@pytest.mark.parametrize("value", ["a\nb", 'a"b', "${host}", "a;b"])
def test_header_value_injection(value):
    with pytest.raises(ValueError):
        HeaderPolicy(response={"X-Test": value})


def test_cidr_and_rate_validation():
    with pytest.raises(ValueError):
        RealIPPolicy(trusted_cidrs=["0.0.0.0/0"])
    with pytest.raises(ValueError):
        RatePolicy(rate=0)


def test_wire_schema_parity():
    root = Path(__file__).parents[2]
    assert (root / "agent/app/schemas/config.py").read_text() == (
        root / "supervisor/app/schemas/config.py"
    ).read_text()


async def test_ssrf_metadata_blocked():
    from app.services.origins import resolve

    with pytest.raises(ValueError):
        await resolve(Origin(host="169.254.169.254"), True)
    with pytest.raises(ValueError):
        await resolve(Origin(host="127.0.0.1"), False)


async def test_interrupted_activation_recovery(settings, bundle):
    import json

    runner = Runner()
    deployment = Deployment(settings, runner)
    await deployment.apply(bundle)
    previous = deployment.current()
    fake = settings.nginx_config_root / "releases" / "interrupted"
    fake.mkdir()
    deployment.link(fake, settings.nginx_config_root / "current")
    deployment.journal.write_text(json.dumps({"previous": previous, "candidate": str(fake)}))
    await deployment.recover()
    assert (settings.nginx_config_root / "current").resolve() == Path(previous["release"])
    assert not deployment.journal.exists()
    assert runner.calls[-2:] == [(settings.nginx_binary, "-t"), (settings.nginx_binary, "-s", "reload")]


async def test_log_and_purge_endpoints_reject_unknown_or_invalid_vhost(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "sandbox")
    from app.main import app
    from httpx import AsyncClient, ASGITransport

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://agent") as client:
        assert (await client.get("/api/v1/logs", params={"vhost_id": "../../etc/passwd"})).status_code == 422
        assert (await client.get("/api/v1/logs", params={"vhost_id": str(uuid4())})).status_code == 404
        assert (await client.post("/api/v1/cache/purge", json={"vhost_id": str(uuid4())})).status_code == 404
        assert (await client.post("/api/v1/command", json={"command": "id"})).status_code == 404


async def test_renderer_golden_snapshot():
    from app.renderers.config import render

    s = Settings(
        agent_mode="host",
        agent_id="edge-golden",
        allow_private_origins=True,
        public_port=80,
        tls_port=443,
    )
    v = Vhost(
        id="00000000-0000-0000-0000-000000000001",
        name="Golden",
        domains=["golden.example.com"],
        origins=[Origin(host="127.0.0.1")],
    )
    files = await render(Bundle(revision=1, vhosts=[v]), s, Path("/etc/nginx/cdn-managed/releases/golden"))
    snapshots = Path(__file__).parent / "snapshots"
    assert files[f"http/{vhost_filename(v)}"] == (snapshots / "vhost.conf").read_text()
    assert files["default.vcl"] == (snapshots / "default.vcl").read_text()


@pytest.mark.parametrize("target", ["https://example.com/ok", "http://example.com:8080/new?from=old"])
def test_redirect_rule_accepts_fixed_url(target):
    assert RedirectRule(path="/old", target=target).status == 303


@pytest.mark.parametrize("target", ["javascript:alert(1)", "https://example.com/\nreturn 200", "https://example.com/$host", "https://example.com/;deny all"])
def test_redirect_rule_rejects_unsafe_url(target):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RedirectRule(path="/old", target=target)


async def test_redirect_and_path_access_render_in_order(settings):
    from app.renderers.config import render
    from app.schemas.config import PathAccessRule

    vhost = Vhost(
        id=uuid4(), name="Redirect", domains=["redirect.example.com"],
        origins=[Origin(host="127.0.0.1")],
        path_rules=[PathAccessRule(path="/private", action="deny")],
        redirect_rules=[RedirectRule(path="/old", target="https://example.com/new")],
    )
    files = await render(Bundle(revision=1, vhosts=[vhost]), settings, Path("/tmp/redirect-release"))
    conf = files[f"http/{vhost_filename(vhost)}"]
    assert "return 403;" in conf
    assert "if ($uri = /old) { return 303 https://example.com/new; }" in conf
    assert conf.index("return 403;") < conf.index("return 303 https://example.com/new;")


async def test_path_origin_route_renders_dedicated_upstream(settings):
    from app.renderers.config import render

    vhost = Vhost(
        id=uuid4(), name="Routes", domains=["routes.example.com"],
        origins=[Origin(host="127.0.0.1", port=18088), Origin(host="127.0.0.1", port=18089)],
        origin_routes=[OriginRoute(path="/api/", origin_index=1)],
    )
    files = await render(Bundle(revision=1, vhosts=[vhost]), settings, Path("/tmp/route-release"))
    conf = files[f"http/{vhost_filename(vhost)}"]
    assert f"upstream origin_{vhost.id.hex}_1" in conf
    assert "server 127.0.0.1:18089" in conf
    assert "location ^~ /api/" in conf
    assert f"proxy_pass http://origin_{vhost.id.hex}_1;" in conf


async def test_http_client_can_cache_https_origin_with_host_sni(settings):
    from app.renderers.config import render

    vhost = Vhost(
        id=uuid4(), name="Re-encrypted origin", domains=["www.example.com"],
        origins=[Origin(host="192.0.2.20", port=443, scheme="https", tls_verify=False)],
    )
    files = await render(Bundle(revision=1, vhosts=[vhost]), settings, Path("/tmp/https-origin-release"))
    conf = files[f"http/{vhost_filename(vhost)}"]
    assert f"proxy_pass https://origin_{vhost.id.hex};" in conf
    assert "proxy_ssl_server_name on;" in conf
    assert "proxy_ssl_name $host;" in conf
    assert "proxy_ssl_verify off;" in conf
    stream = files["stream/00-tls.conf"]
    assert "listen 443;" in stream
    assert "ssl_preread on;" in stream
    assert "www.example.com 127.0.0.1:8445;" in stream
    assert "server 192.0.2.20:443" in stream
    assert "proxy_protocol off;" in stream


def test_passthrough_cannot_turn_plain_http_into_https():
    with pytest.raises(ValueError, match="TLS passthrough requires HTTPS origins"):
        Vhost(id=uuid4(), name="invalid", domains=["demo.example.com"],
              origins=[Origin(host="192.0.2.20")], tls_mode="passthrough")


async def test_http_only_does_not_route_https_and_termination_requires_certificate(settings):
    from app.renderers.config import render
    vhost = Vhost(id=uuid4(), name="HTTP only", domains=["demo.example.com"],
                  origins=[Origin(host="192.0.2.20", scheme="https", port=443)], tls_mode="http_only")
    files = await render(Bundle(revision=1, vhosts=[vhost]), settings, Path("/tmp/no-tls"))
    assert "listen 443" not in files["stream/00-tls.conf"]
    vhost.tls_mode = "terminate"
    with pytest.raises(ValueError, match="POP_TLS_CERTIFICATE_REQUIRED"):
        await render(Bundle(revision=1, vhosts=[vhost]), settings, Path("/tmp/no-tls"))


async def test_edge_tls_termination_and_http_redirect_require_tls_material(settings):
    from app.renderers.config import render

    vhost = Vhost(
        id=uuid4(), name="Edge TLS", domains=["secure.example.com"],
        origins=[Origin(host="192.0.2.21", port=80, scheme="http")],
        tls=TLS(certificate="certificate", private_key="private-key"), redirect_https=True,
    )
    files = await render(Bundle(revision=1, vhosts=[vhost]), settings, Path("/tmp/edge-tls-release"))
    conf = files[f"http/{vhost_filename(vhost)}"]
    assert "listen 127.0.0.1:8444 ssl proxy_protocol;" in conf
    assert "listen 443;" in files["stream/00-tls.conf"]
    assert "secure.example.com 127.0.0.1:8444;" in files["stream/00-tls.conf"]
    assert "if ($scheme = http) { return 301 https://$host$request_uri; }" in conf
    assert f"proxy_pass http://origin_{vhost.id.hex};" in conf


def test_path_origin_route_must_reference_configured_origin():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Vhost(id=uuid4(), name="Bad route", domains=["bad.example.com"],
              origins=[Origin(host="127.0.0.1")],
              origin_routes=[OriginRoute(path="/api/", origin_index=1)])


def test_component_versions_report_is_bounded(monkeypatch):
    from app import main
    from subprocess import CompletedProcess

    main.component_versions.cache_clear()
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/" + name if name == "nginx" else None)
    monkeypatch.setattr(main.subprocess, "run", lambda args, **kwargs: CompletedProcess(args, 0, "", "nginx version: nginx/1.24.0\n"))
    assert main.component_versions() == {"nginx": "nginx version: nginx/1.24.0"}
    main.component_versions.cache_clear()
