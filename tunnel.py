import asyncio
import fnmatch
import logging
import socket
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


async def handle_connect_client(server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                            rid: int, target: str, headers: dict):
    await server._semaphore.acquire()
    server.stats.tunnel_opened()
    tunnel_start = time.perf_counter()
    try:
        connect_tunnel = await handle_connect(server, reader, writer, target, headers)
        if connect_tunnel is not None:
            remote_reader, remote_writer = connect_tunnel

            ct = server._active_connections.get(rid)
            if ct:
                ct.mode = 'tunnel'
                ct.target = target
                ct.tunnel_start = time.perf_counter()

            connect_host = (target.split(']')[0][1:] if target.startswith('[')
                           else target.split(':')[0] if ':' in target
                           else target)
            effective_lifetime = server.max_tunnel_lifetime
            matched = next((p for p in server.download_hosts if fnmatch.fnmatch(connect_host, p)), None)
            if matched:
                effective_lifetime = server.max_tunnel_lifetime_download
                logger.debug(f"{server.rid_prefix()}Matched download host {connect_host} ({matched}), tunnel timeout {effective_lifetime}s")

            try:
                if effective_lifetime > 0:
                    await asyncio.wait_for(
                        tunnel_traffic(server, reader, writer, remote_writer, remote_reader),
                        timeout=effective_lifetime,
                    )
                else:
                    await tunnel_traffic(server, reader, writer, remote_writer, remote_reader)
            except asyncio.TimeoutError:
                logger.info(f"{server.rid_prefix()}CONNECT {target} reached max lifetime {effective_lifetime}s, closing tunnel")
            finally:
                try:
                    remote_writer.close()
                except Exception:
                    pass
        else:
            server.stats.connect_failed()
        server.stats.tunnel_closed()
        tunnel_elapsed = time.perf_counter() - tunnel_start
        if tunnel_elapsed > server.slow_request_threshold:
            logger.warning(f"{server.rid_prefix()}CONNECT {target} tunnel ended | {tunnel_elapsed:.1f}s (slow, >{server.slow_request_threshold}s)")
    finally:
        server._semaphore.release()

def tune_socket(server, sock, keepalive=False):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, server.socket_sndbuf)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, server.socket_rcvbuf)
        if keepalive:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'TCP_KEEPIDLE'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            if hasattr(socket, 'TCP_KEEPINTVL'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            if hasattr(socket, 'TCP_KEEPCNT'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
    except Exception:
        pass

async def connect_upstream(server, host: str, port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    # Select upstream: prefer https upstream for HTTPS requests, fallback to http
    upstream_https = server.upstream_proxies.get('https')
    upstream_http = server.upstream_proxies.get('http')
    proxy_url = upstream_https or upstream_http

    if not proxy_url:
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        seen = set()
        last_error = None
        for info in infos:
            addr_key = (info[0], info[4][0])
            if addr_key in seen:
                continue
            seen.add(addr_key)
            try:
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(info[4][0], info[4][1]),
                    timeout=10
                )
                sock = remote_writer.get_extra_info('socket')
                if sock:
                    tune_socket(server, sock, keepalive=True)
                # Warm DNS cache asynchronously
                server._dns_cache[host] = {'time': time.monotonic(), 'addr': info[4][0]}
                return remote_reader, remote_writer
            except asyncio.TimeoutError:
                last_error = last_error or asyncio.TimeoutError(f"Timed out after {len(seen)} address(es)")
            except Exception as e:
                last_error = e
        raise last_error or OSError(f"Cannot connect to {host}:{port}")

    if proxy_url.startswith('socks5'):
        raise Exception(
            "SOCKS5 upstream for CONNECT tunnels is not supported. "
            "Use an HTTP/HTTPS upstream proxy or remove upstream_proxies to connect directly."
        )

    parsed = urlparse(proxy_url)
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 8080

    # HTTP upstream proxy: use CONNECT method to establish tunnel
    logger.debug(f"Proxying CONNECT {host}:{port} -> {proxy_host}:{proxy_port}")
    remote_reader, remote_writer = await asyncio.wait_for(
        asyncio.open_connection(proxy_host, proxy_port), timeout=10
    )

    sock = remote_writer.get_extra_info('socket')
    if sock:
        tune_socket(server, sock, keepalive=True)

    # Send CONNECT request to upstream proxy
    connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
    req_bytes = connect_req.encode()
    server.stats.add_bytes(sent=len(req_bytes))
    remote_writer.write(req_bytes)
    await remote_writer.drain()

    # Wait for upstream proxy confirmation (200 Connection Established)
    response = await asyncio.wait_for(remote_reader.readline(), timeout=15)
    server.stats.add_bytes(received=len(response))
    if not response.startswith(b'HTTP/1.1 200'):
        remote_writer.close()
        try:
            await remote_writer.wait_closed()
        except Exception:
            pass
        raise Exception(f"Upstream proxy CONNECT failed: {response.decode().strip()}")

    # Read and discard remaining response headers (with timeout, prevent upstream hang)
    while True:
        line = await asyncio.wait_for(remote_reader.readline(), timeout=10)
        server.stats.add_bytes(received=len(line))
        if line == b'\r\n' or not line:
            break

    return remote_reader, remote_writer

async def handle_connect(server, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                     target: str, headers: dict) -> Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    try:
        # Parse target address (supports IPv6: [::1]:443)
        if target.startswith('['):
            host_end = target.find(']')
            if host_end == -1:
                raise ValueError(f"Malformed IPv6 target: {target}")
            host = target[1:host_end]
            port = int(target[host_end + 2:]) if target[host_end + 1:].startswith(':') else 443
        elif ':' in target:
            host, port_str = target.split(':', 1)
            port = int(port_str)
        else:
            host = target
            port = 443  # HTTPS default port

        logger.info(f"{server.rid_prefix()}CONNECT tunnel: {host}:{port}")

        # Connect to target (possibly via upstream proxy)
        try:
            remote_reader, remote_writer = await connect_upstream(server, host, port)
        except Exception as e:
            logger.error(f"{server.rid_prefix()}Failed to connect to target {host}:{port}: {e}")
            resp = b'HTTP/1.1 502 Bad Gateway\r\n\r\nCannot connect to target'
            server.stats.add_bytes(sent=len(resp))
            # Use ContextVar RID (not server._request_counter) — this method runs in the client's task
            ct = server._active_connections.get(server.current_rid())
            if ct:
                ct.bytes_sent += len(resp)
            writer.write(resp)
            await server._safe_drain(writer)
            return None

        # Notify client that tunnel is established
        resp = b'HTTP/1.1 200 Connection Established\r\n\r\n'
        server.stats.add_bytes(sent=len(resp))
        writer.write(resp)
        await server._safe_drain(writer)

        # Return tunnel connection, handle_client will forward traffic after releasing semaphore
        return (remote_reader, remote_writer)

    except Exception as e:
        logger.error(f"Error handling CONNECT: {e}")
        try:
            resp = b'HTTP/1.1 500 Internal Server Error\r\n\r\n'
            server.stats.add_bytes(sent=len(resp))
            writer.write(resp)
            await server._safe_drain(writer)
        except Exception:
            pass
        return None

async def tunnel_traffic(server, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                     remote_writer: asyncio.StreamWriter, remote_reader: asyncio.StreamReader):
    # Tunnel idle timeout in seconds
    TUNNEL_IDLE_TIMEOUT = server.tunnel_idle_timeout
    tunnel_start = time.perf_counter()

    async def forward(src_reader: asyncio.StreamReader, dst_writer: asyncio.StreamWriter, name: str):
        # ContextVar returns the per-task RID (isolated from other concurrent tasks)
        rid = server.current_rid()
        try:
            while True:
                data = await asyncio.wait_for(src_reader.read(server.io_buffer_size), timeout=TUNNEL_IDLE_TIMEOUT)
                if not data:
                    break
                if name.startswith("client"):
                    server.stats.add_bytes(sent=len(data))
                else:
                    server.stats.add_bytes(received=len(data))
                # Track bytes for the correct connection via per-task RID
                ct = server._active_connections.get(rid)
                if ct:
                    if name.startswith("client"):
                        ct.bytes_sent += len(data)
                    else:
                        ct.bytes_received += len(data)
                dst_writer.write(data)
                await dst_writer.drain()
        except asyncio.TimeoutError:
            logger.debug(f"Tunnel {name} idle timeout closed")
        except asyncio.CancelledError:
            try:
                await asyncio.shield(dst_writer.drain())
            except Exception:
                pass
            raise
        except Exception as e:
            logger.debug(f"Tunnel {name} forward ended: {e}")
        finally:
            try:
                dst_writer.close()
            except Exception:
                pass

    # Create bidirectional forwarding tasks
    tasks = [
        asyncio.create_task(forward(client_reader, remote_writer, "client->remote")),
        asyncio.create_task(forward(remote_reader, client_writer, "remote->client")),
    ]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except Exception as e:
        logger.error(f"Tunnel forward error: {e}")
    finally:
        for task in tasks:
            task.cancel()
        # Ensure all connections are closed
        try:
            remote_writer.close()
        except Exception:
            pass
        try:
            client_writer.close()
        except Exception:
            pass
    server.stats.record_tunnel_duration(time.perf_counter() - tunnel_start)
