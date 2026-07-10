import asyncio
import json

from conftest import basic_auth, raw_proxy_request, read_http_response


async def test_http_forward_get(proxy_server, origin_server):
    _, proxy_port = await proxy_server()
    origin_port = origin_server["port"]
    request = (
        f"GET http://127.0.0.1:{origin_port}/hello?x=1 HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        f"Proxy-Authorization: {basic_auth()}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    status, _, body = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 200")
    assert body == b"GET /hello?x=1"


async def test_header_names_are_case_insensitive(proxy_server, origin_server):
    _, proxy_port = await proxy_server()
    origin_port = origin_server["port"]
    request = (
        f"GET /lowercase HTTP/1.1\r\n"
        f"host: 127.0.0.1:{origin_port}\r\n"
        f"proxy-authorization: {basic_auth()}\r\n"
        "connection: close\r\n\r\n"
    ).encode()

    status, _, body = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 200")
    assert body == b"GET /lowercase"


async def test_stats_endpoint_requires_auth_and_returns_json(proxy_server):
    proxy, proxy_port = await proxy_server()
    request = (
        "GET / HTTP/1.1\r\n"
        f"Host: {proxy.stats_host}\r\n"
        f"Proxy-Authorization: {basic_auth()}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    status, headers, body = await raw_proxy_request(proxy_port, request)
    payload = json.loads(body.decode())

    assert status.startswith("HTTP/1.1 200")
    assert headers["content-type"] == "application/json"
    assert payload["server"] == "running"
    assert "total" in payload
    assert "active_connections" in payload


async def test_body_limit_returns_413(proxy_server, origin_server):
    _, proxy_port = await proxy_server(max_body_size=4)
    origin_port = origin_server["port"]
    body = b"too large"
    request = (
        f"POST http://127.0.0.1:{origin_port}/echo HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        f"Proxy-Authorization: {basic_auth()}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body

    status, _, _ = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 413")


async def test_chunked_post_is_forwarded(proxy_server, origin_server):
    _, proxy_port = await proxy_server()
    origin_port = origin_server["port"]
    request = (
        f"POST http://127.0.0.1:{origin_port}/echo HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        f"Proxy-Authorization: {basic_auth()}\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n"
        "5;foo=bar\r\nhello\r\n"
        "6\r\n world\r\n"
        "0\r\n\r\n"
    ).encode()

    status, _, body = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 200")
    assert body == b"hello world"
    assert origin_server["state"]["requests"][-1]["body"] == b"hello world"


async def test_connect_tunnel_relays_tcp(proxy_server, echo_server):
    _, proxy_port = await proxy_server()
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(
        (
            f"CONNECT 127.0.0.1:{echo_server} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{echo_server}\r\n"
            f"Proxy-Authorization: {basic_auth()}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    status, _, _ = await read_http_response(reader)
    assert status.startswith("HTTP/1.1 200")

    writer.write(b"ping")
    await writer.drain()
    echoed = await asyncio.wait_for(reader.readexactly(4), timeout=5)
    writer.close()
    await writer.wait_closed()

    assert echoed == b"ping"


async def test_upstream_proxy_connection_failure_returns_502(proxy_server, origin_server, unused_port):
    _, proxy_port = await proxy_server(upstream_proxies={"http": f"http://127.0.0.1:{unused_port}"})
    origin_port = origin_server["port"]
    request = (
        f"GET http://127.0.0.1:{origin_port}/via-upstream HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        f"Proxy-Authorization: {basic_auth()}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    status, _, _ = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 502")
