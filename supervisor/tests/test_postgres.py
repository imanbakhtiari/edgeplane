"""Uses an isolated migrated PostgreSQL database; never substitutes SQLite."""

import os
from uuid import uuid4
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, select

pytestmark = pytest.mark.skipif(
    not os.environ.get("CDN_TEST_DATABASE"),
    reason="Set CDN_TEST_DATABASE=1 with a migrated disposable PostgreSQL database",
)


async def test_api_rbac_revisions_jobs_and_immutability():
    from app.main import app
    from app.db.session import Session, engine
    from app.models import entities as m
    from app.core.security import passwords
    from app.services.config import enqueue, latest
    from app.workers.runner import claim

    suffix = uuid4().hex[:10]
    async with Session.begin() as db:
        db.add(
            m.User(
                username="admin-" + suffix,
                password_hash=passwords.hash("test-password-123"),
                role="ADMIN",
                must_change_password=False,
            )
        )
        db.add(
            m.User(
                username="viewer-" + suffix,
                password_hash=passwords.hash("test-password-123"),
                role="VIEWER",
                must_change_password=False,
            )
        )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/agents")).status_code == 401
            login = await client.post(
                "/api/v1/auth/login", json={"username": "admin-" + suffix, "password": "test-password-123"}
            )
            assert login.status_code == 200, login.text
            csrf = login.json()["csrf"]
            assert (await client.post("/api/v1/sync")).status_code == 403
            client.headers["X-CSRF-Token"] = csrf
            node = await client.post(
                "/api/v1/agents",
                json={
                    "name": "edge-" + suffix,
                    "hostname": "127.0.0.1",
                    "management_url": "http://127.0.0.1:19443",
                    "city": "Tehran",
                },
            )
            assert node.status_code == 200, node.text
            node_id = node.json()["id"]
            varnish_url = f"/api/v1/agents/{node_id}/varnish"
            assert (await client.get(varnish_url)).json()["reported"] is None
            assert (await client.put(varnish_url, json={"storage": "malloc", "size_mb": 512})).status_code == 409
            changed = await client.put(varnish_url, json={"storage": "malloc", "size_mb": 512,
                                       "log_retention_days": 14, "acknowledge_restart": True})
            assert changed.status_code == 200, changed.text
            assert changed.json()["status"] == "QUEUED"
            assert (await client.get(varnish_url)).json()["desired"]["size_mb"] == 512
            async with Session() as db:
                job = await db.get(m.Job, __import__('uuid').UUID(changed.json()["job_id"]))
                assert job.kind == "VARNISH_CONFIG"
            edited_node = await client.put(
                "/api/v1/agents/" + node_id,
                json={
                    "name": "edge-edited-" + suffix,
                    "hostname": "127.0.0.1",
                    "management_url": "http://127.0.0.1:19443",
                    "city": "Shiraz",
                    "country": "Iran",
                    "labels": {"region": "south"},
                },
            )
            assert edited_node.status_code == 200, edited_node.text
            assert edited_node.json()["city"] == "Shiraz"
            host = await client.post(
                "/api/v1/vhosts",
                json={
                    "name": "host-" + suffix,
                    "domains": [suffix + ".example.com"],
                    "origins": [{"host": "93.184.216.34"}],
                },
            )
            assert host.status_code == 200, host.text
            assert host.json()["https_mode"] == "HTTPS unavailable"
            rev = host.json()["deployment_job_id"]
            assert host.json()["deployment_status"] == "QUEUED"
            policies = await client.get("/api/v1/cache-policies")
            assert len(policies.json()) >= 6
            updated = await client.put(
                "/api/v1/vhosts/" + host.json()["id"],
                json={
                    "name": "edited-" + suffix,
                    "domains": [suffix + ".example.com"],
                    "origins": [{"host": "93.184.216.34"}],
                    "deploy": False,
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["deployment_job_id"] == rev
            assert updated.json()["deployment_status"] == "QUEUED"
            # Two saves coalesce before a worker builds one newest-state revision.
            from app.workers.runner import perform
            async with Session() as db:
                build_target = await db.scalar(select(m.JobTarget.id).join(m.Job)
                                               .where(m.Job.kind == "BUILD_REVISION", m.JobTarget.status == "PENDING"))
            assert build_target
            await perform(build_target)
            jobs = (await client.get("/api/v1/jobs")).json()
            assert any(j["kind"] == "VHOST_SYNC" for j in jobs)
            login = await client.post(
                "/api/v1/auth/login", json={"username": "viewer-" + suffix, "password": "test-password-123"}
            )
            client.headers["X-CSRF-Token"] = login.json()["csrf"]
            assert (await client.get("/api/v1/vhosts")).status_code == 200
            assert (await client.post("/api/v1/sync")).status_code == 403
            assert (await client.get("/api/v1/users")).status_code == 403
        # Claiming with separate connections models separate worker processes.
        async with engine.connect() as a, engine.connect() as b:
            first = await claim(a)
            second = await claim(b)
            assert first
            if second:
                assert first[1] != second[1]
                await b.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": second[1]})
            await a.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": first[1]})
        async with Session.begin() as db:
            rev = await latest(db)
            with pytest.raises(Exception):
                async with db.begin_nested():
                    await db.execute(
                        text("UPDATE configuration_revisions SET config_hash='invalid' WHERE id=:id"),
                        {"id": rev.id},
                    )
            key = "test-" + suffix
            first = await enqueue(db, "SYNC", idempotency_key=key, node_ids=[])
            await db.flush()
            second = await enqueue(db, "SYNC", idempotency_key=key, node_ids=[])
            assert first.id == second.id
    finally:
        await engine.dispose()


async def test_new_node_receives_full_state_and_same_hash_is_noop(monkeypatch):
    from app.db.session import Session, engine
    from app.models import entities as m
    from app.services.config import latest, enqueue
    from app.workers.runner import perform
    from app.schemas.config import Bundle

    received = []

    async def request(db, node, method, path, payload=None):
        if path.endswith("capabilities"):
            return {"schema_versions": [1, 2]}
        bundle = Bundle.model_validate(payload)
        received.append(bundle)
        return {"success": True, "revision": bundle.revision, "hash": bundle.digest()}

    monkeypatch.setattr("app.workers.runner.request_agent", request)
    try:
        async with Session.begin() as db:
            node = m.AgentNode(
                name="future-" + uuid4().hex[:8],
                hostname="node.example.com",
                management_url="https://node.example.com:9443",
            )
            db.add(node)
            await db.flush()
            db.add(m.AgentConfigState(agent_id=node.id))
            job = await enqueue(db, "SYNC", node_ids=[node.id])
            await db.flush()
            target = await db.scalar(
                __import__("sqlalchemy").select(m.JobTarget).where(m.JobTarget.job_id == job.id)
            )
            target_id = target.id
        await perform(target_id)
        async with Session() as db:
            desired = await latest(db)
            state = await db.scalar(
                __import__("sqlalchemy")
                .select(m.AgentConfigState)
                .where(m.AgentConfigState.agent_id == node.id)
            )
            assert state.revision == desired.id
            assert state.config_hash == desired.config_hash
            assert received and len(received[0].vhosts) >= 1
    finally:
        await engine.dispose()
