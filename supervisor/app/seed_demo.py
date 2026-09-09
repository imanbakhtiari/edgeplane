"""Explicit development-only demo records and a connected sandbox node."""

import asyncio
from sqlalchemy import select
from app.core.settings import settings
from app.db.session import Session
from app.models import entities as m
from app.services.config import configuration_lock, revision, enqueue


async def seed():
    if settings.environment != "development":
        raise SystemExit("Demo seeding requires ENVIRONMENT=development")
    async with Session.begin() as db:
        await configuration_lock(db)
        for city in ["tehran", "shiraz", "tabriz", "mashhad", "isfahan"]:
            name = city + "-edge-01"
            if not await db.scalar(select(m.AgentNode).where(m.AgentNode.name == name)):
                node = m.AgentNode(
                    name=name,
                    hostname="192.0.2.10",
                    management_url="https://192.0.2.10:9443",
                    city=city.title(),
                    country="IR",
                    provider="Simulated infrastructure",
                    status="DEMO",
                    demo=True,
                    active=False,
                )
                db.add(node)
                await db.flush()
                db.add(m.AgentConfigState(agent_id=node.id))
        node = await db.scalar(select(m.AgentNode).where(m.AgentNode.name == "sandbox-edge"))
        if not node:
            node = m.AgentNode(
                name="sandbox-edge",
                hostname="sandbox-agent",
                management_url="http://sandbox-agent:9443",
                city="Local lab",
                status="SYNCING",
            )
            db.add(node)
            await db.flush()
            db.add(m.AgentConfigState(agent_id=node.id))
        if not await db.scalar(select(m.Vhost).where(m.Vhost.name == "Demo website")):
            from uuid import uuid4

            id = uuid4()
            v = m.Vhost(
                id=id, name="Demo website", cdn_hostname=id.hex[:12] + "." + settings.powerdns_cdn_zone
            )
            db.add(v)
            await db.flush()
            db.add(m.VhostDomain(vhost_id=id, domain="demo.example.com"))
            db.add(
                m.Origin(
                    vhost_id=id, position=0, config={"host": "demo-origin", "port": 8088, "scheme": "http"}
                )
            )
            await revision(db)
        else:
            await enqueue(db, "SYNC", node_ids=[node.id])


if __name__ == "__main__":
    asyncio.run(seed())
