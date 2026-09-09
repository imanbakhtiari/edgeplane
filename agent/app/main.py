from app.services.telemetry import TrafficCollector
import json
import asyncio
from contextlib import suppress
import os
import ssl
import time
from pathlib import Path
from uuid import UUID, uuid4
from contextlib import asynccontextmanager
import psutil
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import Field
from app.core.settings import Settings
from app.core.logging import configure
import logging

from app.schemas.config import Bundle, Model, Origin
from app.services.deploy import Deployment
from app.services.origins import test_origin
from app.system.adapter import HostSystemAdapter, SandboxSystemAdapter

configure()

s = Settings()
system = SandboxSystemAdapter() if s.agent_mode == "sandbox" else HostSystemAdapter()
if os.getenv("CDN_DATA_PLANE_LAB") == "true":
    if s.agent_mode != "sandbox" or not Path("/.dockerenv").exists():
        raise RuntimeError("Real sandbox data plane is restricted to a lab container")
    from app.system.lab import LabSystemAdapter

    system = LabSystemAdapter()
deploy = Deployment(s, system)
collector = TrafficCollector(s, deploy)


@asynccontextmanager
async def lifespan(application):
    if s.agent_mode == "host" and not getattr(application.state, "mtls_listener", False):
        raise RuntimeError("Host Agent must start with python -m app.main to enforce mTLS")
    with deploy.lock():
        await deploy.recover()

    async def collect_loop():
        while True:
            try:
                await asyncio.to_thread(collector.collect)
            except Exception as error:
                logging.getLogger("cdn.agent").error("Traffic collector failure: %s", type(error).__name__)
            await asyncio.sleep(s.telemetry_interval)

    task = asyncio.create_task(collect_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="CDN Agent", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def correlation(request: Request, call_next):
    response = await call_next(request)
    request_id = str(uuid4())
    response.headers["X-Request-ID"] = request_id
    logging.getLogger("cdn.agent").info(
        json.dumps(
            {
                "request_id": request_id,
                "agent_id": s.agent_id,
                "path": request.url.path,
                "status": response.status_code,
            }
        )
    )
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "mode": s.agent_mode}


@app.get("/ready")
async def ready():
    if s.agent_mode == "host" and not all(
        Path(p).exists() for p in [s.nginx_binary, s.varnish_binary, s.varnishadm_binary]
    ):
        raise HTTPException(503, "Required management binaries missing")
    return {"ready": True, "mode": s.agent_mode}


@app.get("/api/v1/capabilities")
async def capabilities():
    return {
        "agent_version": "0.1.0",
        "schema_versions": [1, 2],
        "features": [
            "tls",
            "origin-pools",
            "purge",
            "logs",
            "rate-limit",
            "real-ip",
            "traffic-metrics",
            "path-acl",
            "geoip2",
        ],
        "mode": s.agent_mode,
    }


@app.get("/api/v1/services")
async def services():
    result = {}
    for service in ["nginx", "varnish", "prometheus-node-exporter", "prometheus-nginx-exporter"]:
        state = await system.run("/usr/bin/systemctl", "is-active", service)
        result[service] = {"active": state.code == 0, "observed": state.stdout.strip()}
    return result


@app.get("/api/v1/status")
async def status():
    return {
        "agent_id": s.agent_id,
        "hostname": s.agent_name,
        "agent_version": "0.1.0",
        "schema_versions": [1, 2],
        "mode": s.agent_mode,
        **deploy.current(),
        "uptime": time.time() - psutil.boot_time(),
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage(s.state_root).percent,
        "load": os.getloadavg(),
        "services": await services(),
    }


@app.get("/api/v1/config/current")
async def current():
    return deploy.current()


@app.post("/api/v1/config/apply")
async def apply(bundle: Bundle):
    return await deploy.apply(bundle)


@app.post("/api/v1/config/validate")
async def validate(bundle: Bundle):
    return await deploy.apply(bundle, validate_only=True)


@app.post("/api/v1/services/nginx/validate")
async def validate_nginx():
    with deploy.lock():
        result = await system.run(s.nginx_binary, "-t")
        return {"success": result.code == 0, **result.dict()}


@app.post("/api/v1/services/nginx/reload")
async def reload_nginx():
    with deploy.lock():
        return await deploy.guarded_reload()


class Purge(Model):
    vhost_id: UUID
    paths: list[str] = Field(default_factory=list, max_length=100)
    prefix: bool = False


@app.post("/api/v1/cache/purge")
@app.post("/api/v1/cache/purge-host")
async def purge(body: Purge):
    if str(body.vhost_id) not in deploy.current()["vhosts"]:
        raise HTTPException(404, "Unknown active vhost")
    import re

    expressions = []
    for path in body.paths:
        if not path.startswith("/") or len(path) > 2048 or any(c in path for c in '\r\n"\\'):
            raise HTTPException(422, "Invalid purge path")
        expressions.append(
            "obj.http.X-CDN-Vhost == "
            + str(body.vhost_id)
            + ' && obj.http.X-CDN-URL ~ "^'
            + re.escape(path)
            + ("" if body.prefix else "$")
            + '"'
        )
    if not expressions:
        expressions = ["obj.http.X-CDN-Vhost == " + str(body.vhost_id)]
    for expression in expressions:
        result = await system.run(s.varnishadm_binary, "ban", expression)
        if result.code:
            raise HTTPException(502, detail=result.dict())
    return {"success": True, "bans": len(expressions)}


@app.post("/api/v1/origin/test")
async def origin_test(origin: Origin):
    return await test_origin(origin, s.allow_private_origins)


@app.get("/api/v1/logs")
async def logs(
    vhost_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    search: str = "",
    status: int | None = None,
    method: str | None = None,
    request_id: str | None = None,
):
    if str(vhost_id) not in deploy.current()["vhosts"]:
        raise HTTPException(404, "Unknown active vhost")
    path = s.log_root / str(vhost_id) / "access.json.log"
    if not path.exists():
        return {"items": []}
    if path.is_symlink() or not path.resolve().is_relative_to(s.log_root.resolve()):
        raise HTTPException(403, "Invalid log path")
    with path.open("rb") as file:
        file.seek(max(0, path.stat().st_size - 1048576))
        raw = file.read(1048576)
    entries = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if search and search not in row.get("uri", ""):
            continue
        if status and row.get("status") != status:
            continue
        if method and row.get("method") != method:
            continue
        if request_id and row.get("request_id") != request_id:
            continue
        entries.append(row)
    return {"items": entries[-limit:]}


@app.get("/metrics")
async def metrics():
    current = deploy.current()
    return Response(
        f"cdn_agent_up 1\ncdn_agent_config_revision {current['revision']}\ncdn_agent_vhosts {len(current['vhosts'])}\n"
        + collector.metrics(),
        media_type="text/plain",
    )


@app.get("/api/v1/traffic")
async def traffic():
    return collector.snapshot()


def run():
    import uvicorn

    tls = (
        {}
        if s.agent_mode == "sandbox"
        else dict(
            ssl_keyfile=s.server_key,
            ssl_certfile=s.server_cert,
            ssl_ca_certs=s.management_ca,
            ssl_cert_reqs=ssl.CERT_REQUIRED,
        )
    )
    app.state.mtls_listener = s.agent_mode == "host"
    uvicorn.run(app, host=s.agent_listen_host, port=s.agent_listen_port, **tls)


if __name__ == "__main__":
    run()
