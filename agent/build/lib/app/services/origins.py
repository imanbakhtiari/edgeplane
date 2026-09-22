import asyncio
import ipaddress
import socket
import ssl
import time
from app.schemas.config import Origin


async def resolve(origin: Origin, allow_private: bool):
    records = await asyncio.get_running_loop().getaddrinfo(origin.host, origin.port, type=socket.SOCK_STREAM)
    ips = sorted({r[4][0] for r in records})
    if not ips:
        raise ValueError("Origin DNS returned no addresses")
    for value in ips:
        ip = ipaddress.ip_address(value)
        if (
            ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or (not allow_private and not ip.is_global)
        ):
            raise ValueError("Origin destination is blocked by network policy")
    return ips


async def test_origin(origin, allow_private):
    start = time.monotonic()
    result = {"dns": False, "tcp": False, "tls": None, "http_status": None}
    try:
        ips = await resolve(origin, allow_private)
        result.update(dns=True, addresses=ips)
        context = None
        if origin.scheme == "https":
            context = ssl.create_default_context()
            if not origin.tls_verify:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                ips[0],
                origin.port,
                ssl=context,
                server_hostname=(origin.sni or origin.host) if context else None,
            ),
            10,
        )
        result.update(tcp=True, tls=True if context else None)
        try:
            writer.write(
                f"HEAD / HTTP/1.1\r\nHost: {origin.host_header or origin.host}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), 10)
            result["http_status"] = int(line.split()[1])
        finally:
            writer.close()
            await writer.wait_closed()
    except (OSError, ValueError, TimeoutError, IndexError):
        result["error"] = "ORIGIN_UNREACHABLE"
    result["latency_ms"] = round((time.monotonic() - start) * 1000, 2)
    return result
