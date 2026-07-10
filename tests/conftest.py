import asyncio
import base64
import contextlib
import socket

import pytest
import pytest_asyncio
from aiohttp import web

from proxy_server import ProxyServer


def basic_auth(username="admin", password="password123"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


async def read_http_response(reader):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = await asyncio.wait_for(reader.read(1), timeout=5)
        if not chunk:
            break
        data.extend(chunk)
    header_bytes, _, rest = bytes(data).partition(b"\r\n\r\n")
    header_text = header_bytes.decode("iso-8859-1")
    lines = header_text.split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
    body = bytearray(rest)
    if "content-length" in headers:
        target_len = int(headers["content-length"])
        while len(body) < target_len:
            body.extend(await asyncio.wait_for(reader.read(target_len - len(body)), timeout=5))
    elif headers.get("transfer-encoding", "").lower() == "chunked":
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            body.extend(line)
            size = int(line.strip().split(b";", 1)[0], 16)
            if size == 0:
                trailer = await asyncio.wait_for(reader.readline(), timeout=5)
                body.extend(trailer)
                break
            body.extend(await asyncio.wait_for(reader.readexactly(size + 2), timeout=5))
    else:
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            body.extend(chunk)
    return status_line, headers, bytes(body)


async def raw_proxy_request(proxy_port, request):
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(request)
    await writer.drain()
    response = await read_http_response(reader)
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return response


@pytest.fixture
def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest_asyncio.fixture
async def origin_server():
    state = {"requests": []}

    async def handle(request):
        body = await request.read()
        state["requests"].append(
            {
                "method": request.method,
                "path": request.path_qs,
                "body": body,
                "headers": dict(request.headers),
            }
        )
        if request.path == "/echo":
            return web.Response(body=body)
        if request.path == "/json":
            return web.json_response({"ok": True, "path": request.path_qs})
        return web.Response(text=f"{request.method} {request.path_qs}")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield {"port": port, "state": state}
    finally:
        await runner.cleanup()


@pytest_asyncio.fixture
async def echo_server():
    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


@pytest_asyncio.fixture
async def proxy_server(tmp_path):
    servers = []

    async def factory(**overrides):
        config = {
            "host": "127.0.0.1",
            "port": 0,
            "username": "admin",
            "password": "password123",
            "auth_enabled": True,
            "display_interval": 0,
            "stats_interval": 0,
            "stats_file": str(tmp_path / f"stats-{len(servers)}.json"),
            "max_body_size": 1024,
            "tunnel_idle_timeout": 2,
            "max_tunnel_lifetime": 5,
            "drain_timeout": 2,
        }
        config.update(overrides)
        proxy = ProxyServer(config)
        server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        servers.append((server, proxy))
        return proxy, port

    yield factory

    for server, proxy in reversed(servers):
        proxy._shutting_down = True
        server.close()
        await server.wait_closed()
        for writer in list(proxy._active_writers):
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        await proxy.close_session()
