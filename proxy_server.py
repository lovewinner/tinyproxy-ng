#!/usr/bin/env python3
"""HTTP/HTTPS Proxy Server entrypoint and orchestration layer."""

import argparse
import asyncio
import collections
import contextvars
import gc
import logging
import os
import socket
import sys
import time
from typing import Dict, Optional, Tuple

from auth import check_auth as check_basic_auth
from config import configure_logging, load_config
from dashboard import AlertHandler, ConnTrack, render_dashboard
from http_forward import close_session, get_session, handle_http_request, upstream_wants_close, write_response
from stats import StatsCollector
from tunnel import connect_upstream, handle_connect, handle_connect_client, tunnel_traffic, tune_socket

# Log config: time - level - message
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Request tracing ID (shared across the same coroutine chain for log correlation)
_request_id = contextvars.ContextVar('request_id', default=0)


class Headers(dict):
    """HTTP headers with case-insensitive lookup and original-case iteration."""

    def __init__(self):
        super().__init__()
        self._lookup = {}

    def __setitem__(self, key, value):
        self._lookup[key.lower()] = key
        super().__setitem__(key, value)

    def get(self, key, default=None):
        actual = self._lookup.get(key.lower())
        if actual is None:
            return default
        return super().get(actual, default)

    def __contains__(self, key):
        return key.lower() in self._lookup


class ProxyServer:
    """HTTP/HTTPS Proxy Server core class."""

    def __init__(self, config: dict):
        self.config = config
        # Listen address and port
        self.host = config.get('host', '0.0.0.0')
        self.port = config.get('port', 8080)
        # Auth config
        self.username = config.get('username', '')
        self.password = config.get('password', '')
        self.auth_enabled = config.get('auth_enabled', True)
        # Upstream proxy config (optional)
        self.upstream_proxies = config.get('upstream_proxies', {})
        # Concurrency semaphore: limits simultaneous connections to prevent resource exhaustion
        self.max_connections = config.get('max_connections', 500)
        self._semaphore = asyncio.Semaphore(self.max_connections)

        # Request body size limit (prevent OOM)
        self.max_body_size = config.get('max_body_size', 10 * 1024 * 1024)  # 10MB

        # Tunnel idle timeout (configurable)
        self.tunnel_idle_timeout = config.get('tunnel_idle_timeout', 180)

        # Header read total timeout (prevent Slowloris attacks)
        self.header_timeout = config.get('header_timeout', 15)

        # I/O buffer size (affects throughput)
        self.io_buffer_size = config.get('io_buffer_size', 65536)  # 64KB

        # Socket send/receive buffer (high bandwidth optimization)
        self.socket_sndbuf = config.get('socket_sndbuf', 262144)  # 256KB
        self.socket_rcvbuf = config.get('socket_rcvbuf', 262144)

        # Keep-Alive config (client connection reuse)
        self.max_keepalive_requests = config.get('max_keepalive_requests', 100)
        self.keepalive_timeout = config.get('keepalive_timeout', 30)

        # Max tunnel lifetime (prevent leaked long connections)
        self.max_tunnel_lifetime = config.get('max_tunnel_lifetime', 300)

        # Download tunnel timeout (overrides when matching download_hosts)
        self.download_hosts = config.get('download_hosts', [])
        self.max_tunnel_lifetime_download = config.get('max_tunnel_lifetime_download', 7200)

        # Slow request detection config
        self.slow_request_threshold = config.get('slow_request_threshold', 5.0)

        # Stats collection
        self.stats_interval = config.get('stats_interval', 60)
        self.stats_host = config.get('stats_host', 'proxy-stats')
        self.stats = StatsCollector(persist_file=config.get('stats_file', 'stats.json'))

        # Rate limiting per client IP
        self.rate_limit_enabled = config.get('rate_limit_enabled', False)
        self.rate_limit_per_minute = config.get('rate_limit_per_minute', 300)
        self._ip_req_times: Dict[str, collections.deque] = {}

        # DNS cache for direct CONNECT tunnels (TTL seconds)
        self._dns_ttl = config.get('dns_cache_ttl', 300)
        self._dns_cache: Dict[str, dict] = {}

        # Max request line / URL length guard (reject pathological requests)
        self.max_request_line_size = config.get('max_request_line_size', 16384)  # 16KB

        # Per-write drain timeout to client (prevent indefinite backpressure hangs)
        self.drain_timeout = config.get('drain_timeout', 30)

        # Dashboard terminal refresh mode
        self.display_interval = config.get('display_interval', 5)
        self._active_connections: Dict[int, ConnTrack] = {}
        self._recent_alerts = collections.deque(maxlen=5)
        self._alert_handler = None
        if self.display_interval > 0:
            self._alert_handler = AlertHandler(self._recent_alerts)
            self._alert_handler.setFormatter(logging.Formatter('%(message)s'))
            logging.getLogger().addHandler(self._alert_handler)
        self._display_task = None
        self._server_start = time.perf_counter()

        # aiohttp client session (lazy init, globally shared)
        self._session = None
        self._session_lock = asyncio.Lock()

        # Active connection tracking (for graceful shutdown)
        self._active_writers = set()
        self._shutting_down = False
        self._request_counter = 0

    def current_rid(self) -> int:
        return _request_id.get()

    def rid_prefix(self) -> str:
        rid = self.current_rid()
        return f"[R={rid}] " if rid else ""

    async def _get_session(self):
        return await get_session(self)

    async def close_session(self):
        await close_session(self)

    def check_auth(self, headers: dict) -> Tuple[bool, Optional[str]]:
        return check_basic_auth(headers, self.auth_enabled, self.username, self.password)

    def _check_rate_limit(self, peer_ip: str) -> bool:
        """Check if peer_ip has exceeded the rate limit. Returns True if allowed."""
        if not self.rate_limit_enabled:
            return True
        now = time.monotonic()
        window = 60.0
        times = self._ip_req_times.get(peer_ip)
        if times is None:
            self._ip_req_times[peer_ip] = collections.deque(maxlen=self.rate_limit_per_minute)
            self._ip_req_times[peer_ip].append(now)
            return True
        while times and times[0] < now - window:
            times.popleft()
        if len(times) >= self.rate_limit_per_minute:
            logger.warning(f"Rate limit exceeded for {peer_ip}: {len(times)} conns in {window}s window")
            return False
        times.append(now)
        return True

    @staticmethod
    def _upstream_wants_close(resp) -> bool:
        return upstream_wants_close(resp)

    async def _safe_drain(self, writer: asyncio.StreamWriter):
        try:
            await asyncio.wait_for(writer.drain(), timeout=self.drain_timeout)
        except asyncio.TimeoutError:
            logger.warning(f"{self.rid_prefix()}Client write stalled > {self.drain_timeout}s, aborting")
            raise

    async def _read_headers(self, reader: asyncio.StreamReader) -> Tuple[dict, int]:
        headers = Headers()
        total_bytes = 0
        while True:
            line = await reader.readline()
            total_bytes += len(line)
            if not line or line == b'\r\n':
                break
            try:
                decoded = line.decode('utf-8').strip()
                key, value = decoded.split(':', 1)
                headers[key.strip()] = value.strip()
                logger.debug(f"Header: {key}: {value}")
            except ValueError:
                continue
        return headers, total_bytes

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Each connection gets a monotonically increasing request ID (rid).
        self._request_counter += 1
        rid = self._request_counter
        _request_id.set(rid)
        peer = writer.get_extra_info('peername')
        if peer:
            logger.info(f"{self.rid_prefix()}New connection: {peer[0]}:{peer[1]}")
        else:
            logger.info(f"{self.rid_prefix()}New connection: (unknown address)")

        # Rate limiting check per client IP
        if peer and not self._check_rate_limit(peer[0]):
            resp = b'HTTP/1.1 429 Too Many Requests\r\nConnection: close\r\n\r\nRate limit exceeded'
            self.stats.add_bytes(sent=len(resp))
            writer.write(resp)
            await self._safe_drain(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self._active_writers.discard(writer)
            return

        # Dashboard tracking
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        self._active_connections[rid] = ConnTrack(rid, peer_str)

        sock = writer.get_extra_info('socket')
        if sock:
            self._tune_socket(sock)

        # Track active connections (for graceful shutdown)
        self._active_writers.add(writer)
        self.stats.conn_opened()

        request_count = 0
        while request_count < self.max_keepalive_requests:
            request_count += 1
            if self._shutting_down:
                break

            try:
                # Read request line (inner try for timeout)
                try:
                    timeout = 10 if request_count == 1 else self.keepalive_timeout
                    request_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                if not request_line:
                    break
                request_line = request_line.decode('utf-8').strip()
                logger.debug(f"Request line [{request_count}]: {request_line}")

                parts = request_line.split(' ', 2)
                if len(parts) != 3:
                    self.stats.add_bytes(sent=len(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n'))
                    writer.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
                    await self._safe_drain(writer)
                    break
                method, target, version = parts

                try:
                    headers, hdr_bytes = await asyncio.wait_for(
                        self._read_headers(reader), timeout=self.header_timeout
                    )
                except asyncio.TimeoutError:
                    break
                hdr_bytes += len(request_line)
                self.stats.add_bytes(sent=hdr_bytes)
                ct = self._active_connections.get(rid)
                if ct:
                    ct.bytes_sent += hdr_bytes

                auth_ok, error_msg = self.check_auth(headers)
                if not auth_ok:
                    self.stats.auth_failed()
                    resp = f'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Proxy"\r\nContent-Type: text/plain\r\n\r\n{error_msg}'
                    self.stats.add_bytes(sent=len(resp))
                    writer.write(resp.encode('utf-8'))
                    await self._safe_drain(writer)
                    break

                stats_host_value = headers.get('Host', '').split(':')[0]
                if method == 'GET' and stats_host_value == self.stats_host:
                    stats_json = self.stats.to_json()
                    resp = (f'HTTP/1.1 200 OK\r\n'
                            f'Content-Type: application/json\r\n'
                            f'Content-Length: {len(stats_json.encode())}\r\n'
                            f'Connection: close\r\n\r\n{stats_json}')
                    self.stats.add_bytes(sent=len(resp))
                    writer.write(resp.encode('utf-8'))
                    await self._safe_drain(writer)
                    break

                if method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'):
                    self.stats.http_request()
                    ct = self._active_connections.get(rid)
                    if ct:
                        ct.mode = 'http'
                        ct.target = f'{method} {target}'
                        ct.request_count += 1

                if method == 'CONNECT':
                    await self._handle_connect_client(reader, writer, rid, target, headers)
                    break

                elif method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'):
                    await self._semaphore.acquire()
                    try:
                        success = await self.handle_http_request(
                            reader, writer, method, target, version, headers
                        )
                    except Exception as e:
                        logger.error(f"HTTP request error: {e}")
                        success = False
                    finally:
                        self._semaphore.release()

                    if not success:
                        break

                    conn = headers.get('Connection', '').lower()
                    proxy_conn = headers.get('Proxy-Connection', '').lower()
                    if conn == 'close' or proxy_conn == 'close':
                        break
                    if version.upper() == 'HTTP/1.0':
                        if 'keep-alive' not in conn and 'keep-alive' not in proxy_conn:
                            break
                else:
                    self.stats.add_bytes(sent=len(b'HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n'))
                    writer.write(b'HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n')
                    await self._safe_drain(writer)
                    break

            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                break
            except Exception:
                logger.exception(f"{self.rid_prefix()}Unexpected error, cleaning up connection")
                break

        try:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=5)
            except Exception:
                pass
        except Exception:
            pass
        self._active_writers.discard(writer)
        self.stats.conn_closed()
        ct = self._active_connections.pop(rid, None)
        if ct:
            self.stats.total_disconnected += 1

    async def _handle_connect_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                     rid: int, target: str, headers: dict):
        await handle_connect_client(self, reader, writer, rid, target, headers)

    def _tune_socket(self, sock, keepalive=False):
        tune_socket(self, sock, keepalive=keepalive)

    async def _connect_upstream(self, host: str, port: int):
        return await connect_upstream(self, host, port)

    async def handle_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                             target: str, headers: dict):
        return await handle_connect(self, reader, writer, target, headers)

    async def handle_http_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                  method: str, target: str, version: str, headers: dict):
        return await handle_http_request(self, reader, writer, method, target, version, headers)

    async def _write_response(self, writer: asyncio.StreamWriter, resp):
        await write_response(self, writer, resp)

    async def tunnel_traffic(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                             remote_writer: asyncio.StreamWriter, remote_reader: asyncio.StreamReader):
        await tunnel_traffic(self, client_reader, client_writer, remote_writer, remote_reader)

    async def _stats_logger(self):
        while not self._shutting_down:
            await asyncio.sleep(self.stats_interval)
            if self._shutting_down:
                break
            logger.info(self.stats.format_text())
            self.stats.save()
            self.stats.snapshot_period()

    def _render_dashboard(self):
        render_dashboard(self)

    async def _active_display(self):
        """Periodically refresh the Dashboard."""
        while not self._shutting_down:
            await asyncio.sleep(self.display_interval)
            if self._shutting_down:
                break
            self._render_dashboard()

    async def start_server(self):
        server_kwargs = {
            'host': self.host,
            'port': self.port,
            'reuse_address': True, # Allow fast restart
        }
        # SO_REUSEPORT: Multiple processes can bind same port (Linux/macOS only)
        if hasattr(socket, 'SO_REUSEPORT'):
            server_kwargs['reuse_port'] = True

        # Create TCP server
        server = await asyncio.start_server(self.handle_client, **server_kwargs)

        for sock in server.sockets:
            self._tune_socket(sock, keepalive=True)

        logger.info("Proxy server started")
        logger.info(f"Address: {self.host}:{self.port}")
        logger.info(f"Auth: {'Enabled' if self.auth_enabled else 'Disabled'}")
        if self.auth_enabled:
            logger.info(f"Username: {self.username}")
        logger.info("Press Ctrl+C to stop")

        # Start stats log task
        stats_task = None
        if self.stats_interval > 0:
            stats_task = asyncio.create_task(self._stats_logger())
        display_task = None
        if self.display_interval > 0:
            display_task = asyncio.create_task(self._active_display())
        # Dashboard mode: terminal shows ERROR only, WARNING goes to AlertHandler + log file
        prev_console_level = None
        if self.display_interval > 0:
            root = logging.getLogger()
            for h in root.handlers:
                if isinstance(h, logging.StreamHandler) and not isinstance(h, AlertHandler):
                    prev_console_level = h.level
                    h.setLevel(logging.ERROR)

        try:
            async with server:
                await server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Stopping server...")
        finally:
            # Restore log level
            if self.display_interval > 0 and prev_console_level is not None:
                root = logging.getLogger()
                for h in root.handlers:
                    if isinstance(h, logging.StreamHandler) and not isinstance(h, AlertHandler):
                        h.setLevel(prev_console_level)
                        break
            # Mark shutting down, reject new requests
            self._shutting_down = True
            # Stop accepting new connections
            server.close()
            await server.wait_closed()
            # Wait for active connections to finish (max 5 seconds)
            if self._active_writers:
                logger.info(f"Waiting for {len(self._active_writers)} active connections...")
                for _ in range(50):
                    if not self._active_writers:
                        break
                    await asyncio.sleep(0.1)
                # Force close remaining connections
                if self._active_writers:
                    logger.info(f"Force closing {len(self._active_writers)} remaining connections")
                    for w in list(self._active_writers):
                        try:
                            w.close()
                        except Exception:
                            pass
            # Clean up connection pool
            await self.close_session()
            # Stop stats logging task and save final stats
            if stats_task:
                stats_task.cancel()
            if display_task:
                display_task.cancel()
            if self._alert_handler:
                logging.getLogger().removeHandler(self._alert_handler)
                self._alert_handler = None
            logger.info("Final stats:\n" + self.stats.format_text())
            self.stats.save()
            # Force cleanup: release transport before event loop closes, prevent RuntimeError
            await asyncio.sleep(0.5)
            gc.collect()


def main():
    parser = argparse.ArgumentParser(description='HTTP/HTTPS Proxy Server')
    parser.add_argument('-c', '--config', default='config.yaml', help='Config file path')
    parser.add_argument('--host', help='Listen address')
    parser.add_argument('--port', type=int, help='Listen port')
    parser.add_argument('--user', help='Auth username')
    parser.add_argument('--passwd', help='Auth password')
    parser.add_argument('--no-auth', action='store_true', help='Disable authentication')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # CLI arguments override config
    if args.host:
        config['host'] = args.host
    if args.port:
        config['port'] = args.port
    if args.user:
        config['username'] = args.user
    if args.passwd:
        config['password'] = args.passwd
    if args.no_auth:
        config['auth_enabled'] = False

    file_handler = configure_logging(config, debug=args.debug)

    try:
        server = ProxyServer(config)
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        print("\nServer stopped")
    except Exception as e:
        logger.error(f"Server start failed: {e}")
        sys.exit(1)
    finally:
        if file_handler:
            try:
                logging.getLogger().removeHandler(file_handler)
                file_handler.close()
            except Exception:
                pass
        gc.collect()


if __name__ == '__main__':
    main()
