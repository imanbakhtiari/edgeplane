import os
from uuid import uuid4
import pytest
from httpx import AsyncClient, ASGITransport
from app.api.auth import origin_allowed
from app.core.settings import settings


def test_origin_aliases_and_rejection(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "supervisor_public_url", "http://localhost:8000")
    assert origin_allowed("http://127.0.0.1:8000")
    assert origin_allowed("http://localhost:8000")
    assert not origin_allowed("http://evil.example:8000")
    assert not origin_allowed("http://localhost:9000")
    assert not origin_allowed("http://localhost:invalid")
    assert not origin_allowed("http://[invalid")
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "supervisor_public_url", "https://console.example.com")
    monkeypatch.setattr(settings, "allowed_origins", [])
    assert not origin_allowed("http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "allowed_origins", ["https://console.example.com"])
    assert origin_allowed("https://console.example.com")


@pytest.mark.skipif(not os.environ.get("CDN_TEST_DATABASE"), reason="Requires migrated disposable PostgreSQL")
async def test_preferences_tokens_customer_isolation_and_ingestion():
    from app.main import app
    from app.db.session import Session, engine
    from app.models import entities as m
    from app.core.security import passwords
    from app.services.traffic import ingest, usage

    suffix = uuid4().hex[:8]
    async with Session.begin() as db:
        user = m.User(
            username="ops-" + suffix,
            password_hash=passwords.hash("test-password-123"),
            role="ADMIN",
            must_change_password=False,
        )
        db.add(user)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"username": "ops-" + suffix, "password": "test-password-123"},
                headers={"Origin": "http://127.0.0.1:8000"},
            )
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf"]
            preferences = {
                "theme": "dark",
                "sidebar_collapsed": True,
                "compact_tables": True,
                "timezone": "Asia/Tehran",
            }
            assert (await client.put("/api/v1/auth/preferences", json=preferences)).status_code == 200
            assert (await client.get("/api/v1/auth/preferences")).json() == preferences
            customer = (await client.post("/api/v1/customers", json={"name": "Customer " + suffix})).json()
            node = (
                await client.post(
                    "/api/v1/agents",
                    json={
                        "name": "traffic-" + suffix,
                        "hostname": "127.0.0.1",
                        "management_url": "http://127.0.0.1:9443",
                    },
                )
            ).json()
            vhost = (
                await client.post(
                    "/api/v1/vhosts",
                    json={
                        "name": "traffic-" + suffix,
                        "domains": [suffix + ".example.com"],
                        "origins": [{"host": "93.184.216.34"}],
                        "customer_id": customer["id"],
                        "deploy": False,
                    },
                )
            ).json()
            assert "id" in vhost, vhost
            api_token = (
                await client.post(
                    "/api/v1/auth/api-tokens", json={"name": "Customer export", "customer_id": customer["id"]}
                )
            ).json()
            assert "token" in api_token, api_token
            from uuid import UUID

            snapshot = {
                "epoch": str(uuid4()),
                "items": [
                    {
                        "vhost_id": vhost["id"],
                        "country": "DE",
                        "requests": 5,
                        "bytes_sent": 1000,
                        "bytes_received": 100,
                        "cache_hits": 3,
                        "errors": 0,
                        "mode": "full",
                    }
                ],
            }
            async with Session.begin() as db:
                await ingest(db, UUID(node["id"]), snapshot)
            async with Session.begin() as db:
                await ingest(db, UUID(node["id"]), snapshot)
                result = await usage(db, customer_id=UUID(customer["id"]))
                assert result["totals"]["bytes_sent"] == 1000
                assert result["lifetime"]["requests"] == 5
                assert result["countries"][0]["country"] == "DE"
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer " + api_token["token"]},
            ) as customer_client:
                assert (
                    await customer_client.get("/api/v1/customers/" + customer["id"] + "/usage")
                ).status_code == 200
                assert (
                    await customer_client.get("/api/v1/customers/" + str(uuid4()) + "/usage")
                ).status_code == 403
                assert (await customer_client.get("/api/v1/vhosts")).status_code == 403
                csv = await customer_client.get("/api/v1/customers/" + customer["id"] + "/usage?format=csv")
                assert csv.status_code == 200 and "bytes_sent" in csv.text and "1000" in csv.text
                metrics = await customer_client.get(
                    "/api/v1/customers/" + customer["id"] + "/usage?format=prometheus"
                )
                assert (
                    metrics.status_code == 200
                    and 'cdn_customer_requests_total{customer_id="' + customer["id"] + '"} 5' in metrics.text
                )
                assert (await customer_client.post("/api/v1/sync")).status_code == 403
            await client.delete("/api/v1/auth/api-tokens/" + api_token["id"])
            denied = await client.get(
                "/api/v1/customers/" + customer["id"] + "/usage",
                headers={"Authorization": "Bearer " + api_token["token"]},
            )
            assert denied.status_code == 401
    finally:
        await engine.dispose()
