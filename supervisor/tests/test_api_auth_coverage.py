import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.api.auth import current_user
from app.main import app


def has_auth_dependency(dependant):
    return dependant.call is current_user or any(has_auth_dependency(child) for child in dependant.dependencies)


def test_every_supervisor_api_route_requires_a_token_except_login():
    missing = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and (route.path.startswith("/api/v1/") or route.path in {"/metrics", "/docs", "/redoc", "/openapi.json"})
        and route.path != "/api/v1/auth/login"
        and not has_auth_dependency(route.dependant)
    ]
    assert missing == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/agents", "/api/v1/traffic", "/api/v1/auth/me", "/metrics", "/docs", "/openapi.json"])
async def test_anonymous_requests_are_rejected(path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://supervisor") as client:
        response = await client.get(path)
    assert response.status_code == 401
