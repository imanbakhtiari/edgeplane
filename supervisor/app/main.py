from app.api.operations import router as operations
from app.api.analytics import router as analytics, prometheus
from fastapi import Depends
from app.api.auth import current_user
from app.db.session import session
import logging
import json
import time
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from app.db.session import engine, Session
from app.core.logging import configure

from app.api.auth import router as auth
from app.api.resources import router as resources


configure()


@asynccontextmanager
async def lifespan(app):
    # Environment values are bootstrap inputs only. Persist them once so later
    # container recreation cannot discard settings changed through the UI/API.
    from app.services.dns import import_environment_defaults
    async with Session.begin() as db:
        await import_environment_defaults(db)
    yield
    await engine.dispose()


app = FastAPI(title="CDN Supervisor", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth, prefix="/api/v1")
app.include_router(resources, prefix="/api/v1")
app.include_router(operations, prefix="/api/v1")
app.include_router(analytics, prefix="/api/v1")


@app.get("/openapi.json", include_in_schema=False)
async def protected_openapi(user=Depends(current_user)):
    return app.openapi()


@app.get("/docs", include_in_schema=False)
async def protected_docs(user=Depends(current_user)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Edgeplane API")


@app.get("/redoc", include_in_schema=False)
async def protected_redoc(user=Depends(current_user)):
    return get_redoc_html(openapi_url="/openapi.json", title="Edgeplane API")


@app.websocket("/api/v1/ws/status")
async def status_stream(websocket: WebSocket):
    """Authenticated live invalidation stream; PostgreSQL remains the durable source."""
    from datetime import datetime, timezone
    from sqlalchemy import select, func
    from app.core.security import token_hash
    from app.models.entities import LoginSession, User, AgentNode, JobEvent

    if websocket.headers.get("origin") and not __import__("app.api.auth", fromlist=["origin_allowed"]).origin_allowed(websocket.headers["origin"]):
        await websocket.close(code=1008)
        return
    raw = websocket.cookies.get("cdn_session", "")
    async with Session() as db:
        login = await db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(raw), LoginSession.expires_at > datetime.now(timezone.utc)))
        user = await db.get(User, login.user_id) if login else None
    if not user or not user.active:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            async with Session() as db:
                node_change = await db.scalar(select(func.max(AgentNode.updated_at)))
                event_change = await db.scalar(select(func.max(JobEvent.created_at)))
            await websocket.send_json({"type": "status", "nodes_updated_at": node_change.isoformat() if node_change else None, "events_updated_at": event_change.isoformat() if event_change else None})
            await __import__("asyncio").sleep(2)
    except WebSocketDisconnect:
        return


@app.websocket("/api/v1/ws/ssh/{node_id}")
async def ssh_terminal(websocket: WebSocket, node_id: str):
    """Authenticated interactive PTY using only the node's approved SSH identity."""
    import asyncio
    import secrets
    from datetime import datetime, timezone
    from uuid import UUID
    import asyncssh
    from sqlalchemy import select
    from app.api.auth import origin_allowed
    from app.core.security import token_hash
    from app.models.entities import AgentNode, AuditLog, LoginSession, User
    from app.services.provisioning import credential, ssh_options

    if websocket.headers.get("origin") and not origin_allowed(websocket.headers["origin"]):
        await websocket.close(code=1008, reason="Origin rejected")
        return
    raw = websocket.cookies.get("cdn_session", "")
    async with Session() as db:
        login = await db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == token_hash(raw),
                LoginSession.expires_at > datetime.now(timezone.utc),
            )
        )
        user = await db.get(User, login.user_id) if login else None
        if (
            not user
            or not user.active
            or user.must_change_password
            or user.role not in {"ADMIN", "OPERATOR"}
            or not login
            or not secrets.compare_digest(websocket.query_params.get("csrf", ""), login.csrf)
        ):
            await websocket.close(code=1008, reason="Authentication rejected")
            return
        try:
            node = await db.get(AgentNode, UUID(node_id))
        except ValueError:
            node = None
        if not node:
            await websocket.close(code=1008, reason="Node not found")
            return
        ssh_row, ssh_data = await credential(db, node)
        if not ssh_row.host_key:
            await websocket.close(code=1008, reason="Approve the SSH host identity first")
            return
        host_key = asyncssh.import_public_key(ssh_row.host_key)
        hostname, username, port = node.hostname, ssh_data["username"], ssh_data["port"]
        options = ssh_options(ssh_data)
        db.add(
            AuditLog(
                created_by=user.id,
                action="OPEN_SSH_TERMINAL",
                resource=str(node.id),
                source_ip=websocket.client.host if websocket.client else "",
            )
        )
        await db.commit()

    await websocket.accept()
    try:
        async with asyncssh.connect(
            hostname,
            port=port,
            username=username,
            **options,
            known_hosts=([host_key], [], []),
            connect_timeout=15,
        ) as connection:
            process = await connection.create_process(
                term_type="xterm-256color", term_size=(120, 32)
            )

            async def remote_output():
                while True:
                    data = await process.stdout.read(4096)
                    if not data:
                        break
                    await websocket.send_json({"type": "output", "data": data})

            async def browser_input():
                while True:
                    message = await websocket.receive_json()
                    if message.get("type") == "input":
                        process.stdin.write(str(message.get("data", "")))
                        await process.stdin.drain()
                    elif message.get("type") == "resize":
                        cols = max(20, min(400, int(message.get("cols", 120))))
                        rows = max(5, min(200, int(message.get("rows", 32))))
                        process.change_terminal_size(cols, rows)

            output_task = asyncio.create_task(remote_output())
            input_task = asyncio.create_task(browser_input())
            done, pending = await asyncio.wait(
                {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if process.exit_status is None:
                process.stdin.write_eof()
    except WebSocketDisconnect:
        return
    except (asyncssh.Error, OSError, ValueError, asyncio.TimeoutError):
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "SSH connection failed. Verify the approved host key, endpoint, and configured credentials.",
                }
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/metrics", tags=["Monitoring"])
async def metrics(user=Depends(current_user), db=Depends(session)):
    return await prometheus(db)


@app.middleware("http")
async def correlation(request: Request, call_next):
    request_id = str(uuid4())
    start = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    level = logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO
    if request.method != "GET" or response.status_code >= 400:
        logging.getLogger("cdn").log(
            level, json.dumps(
            {
                "component": "supervisor",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
            )
        )
    return response


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse(
        status_code=409,
        content={"code": "RESOURCE_CONFLICT", "message": "Duplicate value or resource still referenced"},
    )


@app.exception_handler(ValidationError)
async def validation(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_FAILED",
            "message": "Invalid configuration fields",
            "fields": [{"location": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
        },
    )


@app.exception_handler(RequestValidationError)
async def request_validation(request, exc):
    # FastAPI's default 422 includes each rejected input. Credential requests
    # contain SSH and sudo secrets, so return field names and messages only.
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_FAILED",
            "message": "Invalid request fields",
            "fields": [{"location": list(error["loc"]), "message": error["msg"]} for error in exc.errors()],
        },
    )


@app.exception_handler(httpx.HTTPError)
async def offline(request, exc):
    return JSONResponse(
        status_code=502,
        content={
            "code": "AGENT_OFFLINE",
            "message": "Management API could not be reached or rejected the request",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"ready": False})
    return {"ready": True}


frontend = Path(__file__).parents[1] / "frontend/dist"
if (frontend / "assets").exists():
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    if (frontend / "index.html").exists():
        return FileResponse(frontend / "index.html")
    return JSONResponse(status_code=503, content={"message": "Build supervisor/frontend with npm run build"})
