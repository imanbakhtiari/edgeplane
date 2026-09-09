import httpx
from app.core.settings import settings
from app.services.pki import tls_context, authority


async def request_agent(db, node, method, path, payload=None):
    if node.demo:
        raise ValueError("DEMO nodes cannot receive infrastructure operations")
    if node.management_url.startswith("http://"):
        if settings.environment != "development":
            raise ValueError("Agent mTLS is mandatory in production")
        async with httpx.AsyncClient(timeout=settings.agent_request_timeout, trust_env=False) as client:
            response = await client.request(method, node.management_url + path, json=payload)
    else:
        pki = await authority(db)
        with tls_context(pki) as ctx:
            async with httpx.AsyncClient(
                verify=ctx,
                timeout=httpx.Timeout(settings.agent_request_timeout, connect=settings.agent_connect_timeout),
                trust_env=False,
            ) as client:
                response = await client.request(method, node.management_url + path, json=payload)
    response.raise_for_status()
    return response.json()
