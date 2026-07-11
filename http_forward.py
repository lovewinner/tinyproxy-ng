from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import aiohttp

try:
    from aiohttp_socks import ProxyConnector, ProxyType
    AIOHTTP_SOCKS_AVAILABLE = True
except ImportError:
    AIOHTTP_SOCKS_AVAILABLE = False

logger = logging.getLogger(__name__)


async def get_session(server) -> aiohttp.ClientSession:
    async with server._session_lock:
        if server._session is None or server._session.closed:
            connector = aiohttp.TCPConnector(
                limit=server.max_connections,  # Match max_connections config
                limit_per_host=max(10, server.max_connections // 10),  # Scale with total
                keepalive_timeout=30,      # Keepalive idle time
                ttl_dns_cache=300,         # DNS cache TTL (seconds)
                enable_cleanup_closed=True, # Auto-cleanup abnormally closed connections
            )
            # Layered timeout: connect 10s, read 30s, total 60s
            timeout = aiohttp.ClientTimeout(
                connect=10,
                sock_connect=10,
                sock_read=30,
                total=60,
            )
            server._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )
    return server._session  # type: ignore[no-any-return]

async def close_session(server):
    """Close global HTTP client session (clean up connection pool)"""
    if server._session and not server._session.closed:
        await server._session.close()

def upstream_wants_close(resp: aiohttp.ClientResponse) -> bool:
    conn_header = resp.headers.get('Connection', '').lower()
    return any(p.strip() == 'close' for p in conn_header.split(','))

async def handle_http_request(server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                          method: str, target: str, version: str, headers: dict):
    try:
        req_start = time.perf_counter()
        # Read request body (supports Content-Length and Transfer-Encoding: chunked)
        body = b''
        if method in ('POST', 'PUT', 'PATCH'):
            content_length = headers.get('Content-Length')
            if content_length is not None:
                cl = int(content_length)
                if cl > server.max_body_size:
                    server.stats.add_bytes(sent=len(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n'))
                    writer.write(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n')
                    await server._safe_drain(writer)
                    return False
                body = await reader.readexactly(cl)
                server.stats.add_bytes(sent=len(body))
                ct_body = server._active_connections.get(server.current_rid())
                if ct_body:
                    ct_body.bytes_sent += len(body)
            elif headers.get('Transfer-Encoding', '').lower() == 'chunked':
                while True:
                    size_line = await reader.readline()
                    if not size_line:
                        break
                    server.stats.add_bytes(sent=len(size_line))
                    size_line = size_line.strip()
                    if not size_line:
                        continue
                    chunk_size = int(size_line.split(b';', 1)[0], 16)
                    if chunk_size == 0:
                        await reader.readline()
                        break
                    if len(body) + chunk_size > server.max_body_size:
                        server.stats.add_bytes(sent=len(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n'))
                        writer.write(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n')
                        await server._safe_drain(writer)
                        while True:
                            try:
                                await reader.readexactly(chunk_size)
                                await reader.readline()
                                size_line = await reader.readline()
                                if not size_line:
                                    break
                                chunk_size = int(size_line.strip().split(b';', 1)[0], 16)
                                if chunk_size == 0:
                                    await reader.readline()
                                    break
                            except Exception:
                                break
                        return False
                    body += await reader.readexactly(chunk_size)
                    server.stats.add_bytes(sent=chunk_size)
                    ct_body = server._active_connections.get(server.current_rid())
                    if ct_body:
                        ct_body.bytes_sent += chunk_size
                    await reader.readline()

        # Parse full URL
        if target.startswith('http://') or target.startswith('https://'):
            url = target
        else:
            # Relative path: build full URL from Host header
            host = headers.get('Host')
            if not host:
                server.stats.add_bytes(sent=len(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nMissing Host header'))
                writer.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nMissing Host header')
                await server._safe_drain(writer)
                return False
            url = f'http://{host}{target}' if target.startswith('/') else f'http://{host}/{target}'

        if len(url) > server.max_request_line_size:
            logger.warning(f"{server.rid_prefix()}Request URL too long ({len(url)} bytes), rejecting")
            server.stats.add_bytes(sent=len(b'HTTP/1.1 414 URI Too Long\r\nConnection: close\r\n\r\n'))
            writer.write(b'HTTP/1.1 414 URI Too Long\r\nConnection: close\r\n\r\n')
            await server._safe_drain(writer)
            return False

        logger.info(f"{server.rid_prefix()}HTTP forward: {method} {url}")

        # Filter forward headers: remove proxy-specific and hop-by-hop headers
        forward_headers = {}
        skip_headers = ['proxy-connection', 'proxy-authorization', 'connection', 'host']
        if body:
            skip_headers.append('transfer-encoding')
        for k, v in headers.items():
            if k.lower() not in skip_headers:
                forward_headers[k] = v

        # Determine upstream proxy to use (HTTPS requests use https upstream, HTTP uses http upstream)
        upstream_http = server.upstream_proxies.get('http')
        upstream_https = server.upstream_proxies.get('https')
        proxy_url = None
        if url.startswith('https://') and upstream_https:
            proxy_url = upstream_https
        elif url.startswith('http://') and upstream_http:
            proxy_url = upstream_http

        # Get global connection pool session
        session = await get_session(server)

        # Exponential backoff retry: transient failures (e.g. DNS timeout) auto-retry once
        max_retries = 1
        last_error: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                if proxy_url and proxy_url.startswith('socks5'):
                    # SOCKS5 upstream proxy: use ProxyConnector from aiohttp-socks
                    if not AIOHTTP_SOCKS_AVAILABLE:
                        raise Exception("aiohttp-socks not installed, cannot use SOCKS5 upstream")

                    parsed = urlparse(proxy_url)
                    proxy_host = parsed.hostname
                    proxy_port = parsed.port or 1080
                    kwargs = dict(
                        proxy_type=ProxyType.SOCKS5,
                        host=proxy_host,
                        port=proxy_port,
                        rdns=True  # Remote DNS resolution
                    )
                    if parsed.username and parsed.password:
                        kwargs['username'] = parsed.username
                        kwargs['password'] = parsed.password
                    connector = ProxyConnector(**kwargs)
                    # SOCKS5 needs its own ClientSession
                    async with aiohttp.ClientSession(connector=connector) as socks_session:
                        async with socks_session.request(
                            method, url,
                            headers=forward_headers,
                            data=body if body else None,
                            allow_redirects=False,
                        ) as resp:
                            await asyncio.wait_for(
                                write_response(server, writer, resp),
                                timeout=server.drain_timeout * 4,
                            )
                            if upstream_wants_close(resp):
                                return False
                else:
                    # HTTP upstream or direct connection
                    kwargs = dict(headers=forward_headers, data=body if body else None, allow_redirects=False)
                    if proxy_url:
                        kwargs['proxy'] = proxy_url  # aiohttp native HTTP proxy
                    async with session.request(method, url, **kwargs) as resp:
                        await asyncio.wait_for(
                            write_response(server, writer, resp),
                            timeout=server.drain_timeout * 4,
                        )
                        if upstream_wants_close(resp):
                            return False
                req_elapsed = time.perf_counter() - req_start
                server.stats.record_http_elapsed(req_elapsed)
                if req_elapsed > server.slow_request_threshold:
                    logger.warning(f"Slow request: {method} {url} | {req_elapsed:.1f}s (>{server.slow_request_threshold}s)")
                else:
                    logger.debug(f"HTTP done: {method} {url} | {req_elapsed:.3f}s")
                return True

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                last_error = e
                logger.warning(f"HTTP request failed (attempt {attempt + 1}/{max_retries + 1}): {e}")
                if attempt < max_retries:
                    wait = 0.5 * (2 ** attempt)
                    await asyncio.sleep(wait)

        # All retries failed
        assert last_error is not None
        raise last_error

    except aiohttp.ClientError as e:
        server.stats.http_failed()
        server.stats.upstream_error()
        logger.error(f"{server.rid_prefix()}HTTP forward failed: {e}")
        try:
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
            await server._safe_drain(writer)
        except Exception:
            pass
        return False
    except asyncio.TimeoutError:
        server.stats.http_failed()
        server.stats.timeout_error()
        logger.error("HTTP forward timeout")
        try:
            writer.write(b'HTTP/1.1 504 Gateway Timeout\r\nConnection: close\r\n\r\n')
            await server._safe_drain(writer)
        except Exception:
            pass
        return False
    except Exception as e:
        server.stats.http_failed()
        server.stats.upstream_error()
        logger.error(f"Error handling HTTP request: {e}")
        return False

async def write_response(server, writer: asyncio.StreamWriter, resp: aiohttp.ClientResponse):
    rid = server.current_rid()
    has_content_length = False
    try:
        writer.write(f'HTTP/1.1 {resp.status} {resp.reason or ""}\r\n'.encode())
        writer.write(b'Via: 1.1 tinyproxy-ng\r\n')
        for key, value in resp.headers.items():
            kl = key.lower()
            if kl not in ['transfer-encoding', 'connection', 'content-encoding',
                          'keep-alive', 'proxy-authenticate', 'proxy-connection',
                          'upgrade', 'trailer']:
                writer.write(f'{key}: {value}\r\n'.encode())
                if kl == 'content-length':
                    has_content_length = True
        if not has_content_length:
            writer.write(b'Transfer-Encoding: chunked\r\n')
        writer.write(b'\r\n')
        async for chunk in resp.content.iter_chunked(server.io_buffer_size):
            server.stats.add_bytes(received=len(chunk))
            ct = server._active_connections.get(rid)
            if ct:
                ct.bytes_received += len(chunk)
            if not has_content_length:
                writer.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
            else:
                writer.write(chunk)
            await server._safe_drain(writer)
        if not has_content_length:
            writer.write(b'0\r\n\r\n')
            await server._safe_drain(writer)
    except (ConnectionResetError, BrokenPipeError):
        logger.debug("Client disconnected during response transfer")
        raise Exception("Response write aborted: client disconnected") from None
    except Exception as e:
        logger.debug(f"Response write error: {e}")
        raise Exception(f"Response write aborted: {e}") from e
