from conftest import basic_auth, raw_proxy_request


async def test_auth_required_without_credentials(proxy_server, origin_server):
    _, proxy_port = await proxy_server()
    origin_port = origin_server["port"]
    request = (
        f"GET http://127.0.0.1:{origin_port}/auth HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    status, _, body = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 407")
    assert b"Authentication required" in body


async def test_auth_accepts_valid_proxy_authorization(proxy_server, origin_server):
    _, proxy_port = await proxy_server()
    origin_port = origin_server["port"]
    request = (
        f"GET http://127.0.0.1:{origin_port}/auth-ok HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        f"Proxy-Authorization: {basic_auth()}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    status, _, body = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 200")
    assert body == b"GET /auth-ok"


async def test_auth_rejects_invalid_credentials(proxy_server, origin_server):
    _, proxy_port = await proxy_server()
    origin_port = origin_server["port"]
    request = (
        f"GET http://127.0.0.1:{origin_port}/bad-auth HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{origin_port}\r\n"
        f"Proxy-Authorization: {basic_auth(password='wrong')}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()

    status, _, body = await raw_proxy_request(proxy_port, request)

    assert status.startswith("HTTP/1.1 407")
    assert b"Authentication failed" in body
