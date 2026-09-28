"""Certbot manual HTTP-01 hook distributed to every active Edgeplane POP."""

import asyncio
import os
import socket
import sys

import httpx
from sqlalchemy import select

from app.db.session import Session
from app.models import entities as m
from app.services.agents import request_agent


def public_url(address: str, token: str) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"http://{host}/.well-known/acme-challenge/{token}"


async def resolved_addresses(domain: str) -> set[str]:
    rows = await asyncio.to_thread(socket.getaddrinfo, domain, 80, 0, socket.SOCK_STREAM)
    return {row[4][0] for row in rows}


async def active_nodes(db):
    return list(
        (
            await db.scalars(
                select(m.AgentNode).where(
                    m.AgentNode.active.is_(True),
                    m.AgentNode.demo.is_(False),
                )
            )
        ).all()
    )


async def present(domain: str, token: str, validation: str):
    async with Session.begin() as db:
        nodes = await active_nodes(db)
        if not nodes:
            raise RuntimeError("ACME_HTTP_NO_ACTIVE_POPS")
        await asyncio.gather(
            *(request_agent(db, node, "POST", "/api/v1/acme/challenge", {
                "token": token, "validation": validation,
            }) for node in nodes)
        )

    addresses = await resolved_addresses(domain)
    pop_addresses = {
        value for node in nodes for value in (node.public_ipv4, node.public_ipv6) if value
    }
    if not addresses or not addresses.issubset(pop_addresses):
        raise RuntimeError("ACME_HTTP_DOMAIN_NOT_EXCLUSIVELY_ROUTED_TO_ACTIVE_POPS")
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        for address in sorted(addresses):
            response = await client.get(public_url(address, token), headers={"Host": domain})
            if response.status_code != 200 or response.text.strip() != validation:
                raise RuntimeError(f"ACME_HTTP_CHALLENGE_UNREACHABLE:{address}")


async def cleanup(token: str):
    async with Session.begin() as db:
        nodes = await active_nodes(db)
        await asyncio.gather(
            *(request_agent(db, node, "POST", "/api/v1/acme/challenge/cleanup", {
                "token": token, "validation": "",
            }) for node in nodes),
            return_exceptions=True,
        )


async def run(action: str):
    domain = os.environ["CERTBOT_DOMAIN"]
    token = os.environ["CERTBOT_TOKEN"]
    if action == "present":
        await present(domain, token, os.environ["CERTBOT_VALIDATION"])
    elif action == "cleanup":
        await cleanup(token)
    else:
        raise ValueError("Expected present or cleanup")


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))
