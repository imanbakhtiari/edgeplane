from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.core.settings import Settings
from app.schemas.config import Bundle, Vhost, Origin, RatePolicy, HeaderPolicy, RealIPPolicy
from app.services.deploy import Deployment
from app.system.adapter import SandboxSystemAdapter, Result


@pytest.fixture
def settings(tmp_path):
    return Settings(
        agent_mode="host",
        nginx_config_root=tmp_path / "nginx",
        varnish_config_root=tmp_path / "varnish",
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
        allow_private_origins=True,
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


async def test_same_hash_noop_and_older_rejected(settings, bundle):
    runner = Runner()
    deploy = Deployment(settings, runner)
    await deploy.apply(bundle)
    runner.calls.clear()
    bundle.revision = 2
    assert (await deploy.apply(bundle))["noop"]
    assert not runner.calls
    assert deploy.current()["revision"] == 2
    bundle.revision = 1
    with pytest.raises(HTTPException) as caught:
        await deploy.apply(bundle)
    assert caught.value.status_code == 409


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
    nginx = files[f"http/{bundle.vhosts[0].id}.conf"]
    assert "proxy_pass_request_headers off" in nginx
    assert "listen 127.0.0.1:8080" in nginx
    assert "limit_req_status 429" in nginx
    assert "req.http.Authorization || req.http.Cookie" in files["default.vcl"]
    assert "hash_data(req.http.host)" in files["default.vcl"]
    assert "beresp.http.Set-Cookie" in files["default.vcl"]
    assert bundle.digest() == bundle.model_copy(update={"revision": 99}).digest()


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

    s = Settings(agent_mode="host", agent_id="edge-golden", allow_private_origins=True)
    v = Vhost(
        id="00000000-0000-0000-0000-000000000001",
        name="Golden",
        domains=["golden.example.com"],
        origins=[Origin(host="127.0.0.1")],
    )
    files = await render(Bundle(revision=1, vhosts=[v]), s, Path("/etc/nginx/cdn-managed/releases/golden"))
    snapshots = Path(__file__).parent / "snapshots"
    assert files[f"http/{v.id}.conf"] == (snapshots / "vhost.conf").read_text()
    assert files["default.vcl"] == (snapshots / "default.vcl").read_text()
