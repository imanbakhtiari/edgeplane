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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from app.db.session import engine
from app.core.logging import configure

from app.api.auth import router as auth
from app.api.resources import router as resources


configure()


@asynccontextmanager
async def lifespan(app):
    yield
    await engine.dispose()


app = FastAPI(title="CDN Supervisor", version="0.1.0", lifespan=lifespan)
app.include_router(auth, prefix="/api/v1")
app.include_router(resources, prefix="/api/v1")
app.include_router(operations, prefix="/api/v1")
app.include_router(analytics, prefix="/api/v1")


@app.get("/metrics", tags=["Monitoring"])
async def metrics(user=Depends(current_user), db=Depends(session)):
    return await prometheus(db)


@app.middleware("http")
async def correlation(request: Request, call_next):
    request_id = str(uuid4())
    start = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logging.getLogger("cdn").info(
        json.dumps(
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
