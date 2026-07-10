#!/usr/bin/env python3
"""
HTTP/HTTPS Proxy Server - Enhanced Stable Version

High-performance async proxy server based on asyncio, supporting:
  - HTTP/HTTPS proxy (CONNECT tunnels)
  - HTTP Basic auth (Proxy-Authorization)
  - Connection pool reuse, TCP tuning, DNS cache
  - Tunnel idle timeout auto-close
  - Upstream proxy chain (HTTP/SOCKS5)
  - Exponential backoff retry, concurrency limiting
"""

import asyncio
import base64
import collections
import contextvars
import gc
import hmac
import json
import sys
import os
import socket
import time
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse
import fnmatch

import aiohttp

# SOCKS5 upstream proxy support (optional dependency)
try:
    from aiohttp_socks import ProxyConnector, ProxyType
    AIOHTTP_SOCKS_AVAILABLE = True
except ImportError:
    AIOHTTP_SOCKS_AVAILABLE = False

# Log config: time - level - message
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Request tracing ID (shared across the same coroutine chain for log correlation)
_request_id = contextvars.ContextVar('request_id', default=0)


def _rid_prefix() -> str:
    """Return log prefix for current request, e.g. '[R=123] '"""
    rid = _request_id.get()
    return f"[R={rid}] " if rid else ""


def _format_bytes(size: int) -> str:
    """Format bytes as human-readable string"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _display_width(s: str) -> int:
    """Calculate display width (CJK chars count as 2)"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def _ljust_display(s: str, width: int) -> str:
    """Left-align pad by display width"""
    pad = max(0, width - _display_width(s))
    return s + ' ' * pad


def _truncate_to_width(s: str, max_w: int) -> str:
    """Truncate string by display width"""
    w = 0
    for i, c in enumerate(s):
        cw = 2 if ord(c) > 127 else 1
        if w + cw > max_w:
            return s[:i] + "..."
        w += cw
    return s


class AlertHandler(logging.Handler):
    """Log WARNING+ messages to ProxyServer's deque for Dashboard display"""
    def __init__(self, target_deque):
        super().__init__(level=logging.WARNING)
        self._target = target_deque

    def emit(self, record):
        try:
            msg = self.format(record)
            self._target.append((record.created, record.levelname, msg))
        except:
            pass


class _ConnTrack:
    """Dashboard live connection tracker"""
    __slots__ = ('rid', 'peer', 'connected_at', 'mode', 'target',
                 'bytes_sent', 'bytes_received', 'request_count', 'tunnel_start')
    def __init__(self, rid: int, peer: str):
        self.rid = rid                       # Request ID (unique per connection)
        self.peer = peer                     # Client address
        self.connected_at = time.perf_counter()  # Connection time
        self.mode = 'idle'                   # 'idle' | 'http' | 'tunnel'
        self.target = ''                     # Target host:port
        self.bytes_sent = 0                  # Upload bytes
        self.bytes_received = 0              # Download bytes
        self.request_count = 0               # HTTP requests on keep-alive connection
        self.tunnel_start = 0.0              # Tunnel start timestamp


class StatsCollector:
    """Stats collection: total (historical) + last_period (period snapshot), dual-layer"""

    def __init__(self, persist_file: str = "stats.json"):
        self.persist_file = persist_file
        self.started_at = time.time()
        self._monotonic_start = time.monotonic()

        # ── Totals (persistent) ──
        self.total_connections = 0
        self.total_http_requests = 0
        self.total_connect_tunnels = 0
        self.total_bytes_sent = 0
        self.total_bytes_received = 0
        self.total_auth_failures = 0
        self.total_upstream_errors = 0
        self.total_timeout_errors = 0
        self.total_http_failed = 0
        self.total_connect_failed = 0
        self.total_disconnected = 0
        self.session_bytes_sent = 0
        self.session_bytes_received = 0
        self._total_http_elapsed = 0.0
        self._total_http_count = 0
        self._total_http_max = 0.0
        self._total_tunnel_elapsed = 0.0
        self._total_tunnel_count = 0

        # ── Period accumulators (non-persistent) ──
        self._period_connections = 0
        self._period_http_requests = 0
        self._period_connect_tunnels = 0
        self._period_bytes_sent = 0
        self._period_bytes_received = 0
        self._period_auth_failures = 0
        self._period_upstream_errors = 0
        self._period_timeout_errors = 0
        self._period_http_failed = 0
        self._period_connect_failed = 0
        self._period_http_elapsed = 0.0
        self._period_http_count = 0
        self._period_http_max = 0.0
        self._period_tunnel_elapsed = 0.0
        self._period_tunnel_count = 0

        # ── Last period snapshot ──
        self.last_period = {}

        # ── Live ──
        self.active_connections = 0
        self.active_tunnels = 0

        self._load()

    # ── Cumulative + period sync update ──

    def conn_opened(self):
        self.total_connections += 1
        self._period_connections += 1
        self.active_connections += 1

    def conn_closed(self):
        self.active_connections = max(0, self.active_connections - 1)

    def http_request(self):
        self.total_http_requests += 1
        self._period_http_requests += 1

    def http_failed(self):
        self.total_http_failed += 1
        self._period_http_failed += 1

    def tunnel_opened(self):
        self.total_connect_tunnels += 1
        self._period_connect_tunnels += 1
        self.active_tunnels += 1

    def tunnel_closed(self):
        self.active_tunnels = max(0, self.active_tunnels - 1)

    def connect_failed(self):
        self.total_connect_failed += 1
        self._period_connect_failed += 1

    def auth_failed(self):
        self.total_auth_failures += 1
        self._period_auth_failures += 1

    def upstream_error(self):
        self.total_upstream_errors += 1
        self._period_upstream_errors += 1

    def timeout_error(self):
        self.total_timeout_errors += 1
        self._period_timeout_errors += 1

    def add_bytes(self, sent: int = 0, received: int = 0):
        # Persistent total (saved to stats.json)
        self.total_bytes_sent += sent
        self.total_bytes_received += received
        # Session-only counters (reset on restart)
        self.session_bytes_sent += sent
        self.session_bytes_received += received
        self._period_bytes_sent += sent
        self._period_bytes_received += received

    def record_http_elapsed(self, elapsed_sec: float):
        self._total_http_elapsed += elapsed_sec
        self._total_http_count += 1
        if elapsed_sec > self._total_http_max:
            self._total_http_max = elapsed_sec
        self._period_http_elapsed += elapsed_sec
        self._period_http_count += 1
        if elapsed_sec > self._period_http_max:
            self._period_http_max = elapsed_sec

    def record_tunnel_duration(self, elapsed_sec: float):
        self._total_tunnel_elapsed += elapsed_sec
        self._total_tunnel_count += 1
        self._period_tunnel_elapsed += elapsed_sec
        self._period_tunnel_count += 1

    # ── Period snapshot ──

    def snapshot_period(self):
        """Snapshot current period to last_period, reset period accumulators"""
        self.last_period = dict(
            duration_seconds=round(time.time() - self.started_at),
            connections=self._period_connections,
            http_requests=self._period_http_requests,
            connect_tunnels=self._period_connect_tunnels,
            bytes_sent=self._period_bytes_sent,
            bytes_received=self._period_bytes_received,
            auth_failures=self._period_auth_failures,
            upstream_errors=self._period_upstream_errors,
            timeout_errors=self._period_timeout_errors,
            http_failed=self._period_http_failed,
            connect_failed=self._period_connect_failed,
            http_avg_ms=round((self._period_http_elapsed / self._period_http_count * 1000)
                              if self._period_http_count else 0),
            http_max_ms=round(self._period_http_max * 1000),
            tunnel_avg_ms=round((self._period_tunnel_elapsed / self._period_tunnel_count * 1000)
                                if self._period_tunnel_count else 0),
        )
        # Reset
        self._period_connections = 0
        self._period_http_requests = 0
        self._period_connect_tunnels = 0
        self._period_bytes_sent = 0
        self._period_bytes_received = 0
        self._period_auth_failures = 0
        self._period_upstream_errors = 0
        self._period_timeout_errors = 0
        self._period_http_failed = 0
        self._period_connect_failed = 0
        self._period_http_elapsed = 0.0
        self._period_http_count = 0
        self._period_http_max = 0.0
        self._period_tunnel_elapsed = 0.0
        self._period_tunnel_count = 0

    # ── Output ──

    def get_stats(self) -> dict:
        uptime = time.monotonic() - self._monotonic_start
        return {
            "server": "running",
            "started_at": int(self.started_at),
            "uptime_seconds": round(uptime),
            "active_connections": self.active_connections,
            "active_tunnels": self.active_tunnels,
            "total": {
                "connections": self.total_connections,
                "http_requests": self.total_http_requests,
                "connect_tunnels": self.total_connect_tunnels,
                "http_failed": self.total_http_failed,
                "connect_failed": self.total_connect_failed,
                "http_avg_ms": round((self._total_http_elapsed / self._total_http_count * 1000)
                                     if self._total_http_count else 0),
                "http_max_ms": round(self._total_http_max * 1000),
                "tunnel_avg_ms": round((self._total_tunnel_elapsed / self._total_tunnel_count * 1000)
                                       if self._total_tunnel_count else 0),
                "bytes_sent": self.total_bytes_sent,
                "bytes_received": self.total_bytes_received,
                "auth_failures": self.total_auth_failures,
                "upstream_errors": self.total_upstream_errors,
                "timeout_errors": self.total_timeout_errors,
            },
            "last_period": dict(self.last_period) if self.last_period else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.get_stats(), ensure_ascii=False, indent=2)

    def format_text(self) -> str:
        s = self.get_stats()
        u = s["uptime_seconds"]
        h, m = divmod(u, 3600)
        m, sec = divmod(m, 60)
        uptime_str = f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"
        t = s["total"]

        lines = [
            f"\n{'─' * 55}",
            f"  Proxy Stats (Uptime {uptime_str}) — {s['active_connections']} conns / {s['active_tunnels']} tunnels",
            f"{'─' * 55}",
            f"  Total:   {t['connections']} conns  {t['http_requests']} HTTP  {t['connect_tunnels']} CONNECT",
            f"  Bytes:   {_format_bytes(t['bytes_sent'])} sent / {_format_bytes(t['bytes_received'])} received",
            f"  Errors:  {t['auth_failures']} auth  {t['upstream_errors']} upstream  {t['timeout_errors']} timeout",
            f"  HTTP:    {t['http_requests']} reqs  avg {t['http_avg_ms']}ms  max {t['http_max_ms']}ms  fail {t['http_failed']}",
            f"  Tunnel:  {t['connect_tunnels']} CONNECT  avg {t['tunnel_avg_ms']}ms  fail {t['connect_failed']}",
        ]
        lp = s.get("last_period")
        if lp:
            lines += [
                f"  ── Last period ({lp['duration_seconds']}s) ──",
                f"  Reqs:   {lp['connections']} conns  {lp['http_requests']} HTTP  {lp['connect_tunnels']} CONNECT",
                f"  Bytes:  {_format_bytes(lp['bytes_sent'])} sent / {_format_bytes(lp['bytes_received'])} received",
                f"  Errors: {lp['auth_failures']} auth  {lp['upstream_errors']} upstream  {lp['timeout_errors']} timeout",
                f"  HTTP:   avg {lp['http_avg_ms']}ms  max {lp['http_max_ms']}ms  fail {lp['http_failed']}",
                f"  Tunnel: avg {lp['tunnel_avg_ms']}ms  fail {lp['connect_failed']}",
            ]
        lines.append(f"{'─' * 55}")
        return "\n".join(lines)

    # ── Persistence ──

    _PERSIST_FIELDS = [
        'total_connections', 'total_http_requests', 'total_connect_tunnels',
        'total_bytes_sent', 'total_bytes_received',
        'total_auth_failures', 'total_upstream_errors', 'total_timeout_errors',
        'total_http_failed', 'total_connect_failed',
        '_total_http_elapsed', '_total_http_count', '_total_http_max',
        '_total_tunnel_elapsed', '_total_tunnel_count',
    ]

    def _load(self):
        if os.path.exists(self.persist_file):
            try:
                with open(self.persist_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.started_at = data.get("started_at", self.started_at)
                for k in self._PERSIST_FIELDS:
                    setattr(self, k, data.get(k, getattr(self, k)))
                logger.info(f"Loaded historical stats: {self.persist_file}")
            except Exception as e:
                logger.warning(f"Stats file load failed: {e}, starting from zero")

    def save(self):
        data = {"started_at": self.started_at}
        data.update({k: getattr(self, k) for k in self._PERSIST_FIELDS})
        try:
            with open(self.persist_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Stats save failed: {e}")


class ProxyServer:
    """HTTP/HTTPS Proxy Server core class"""

    def __init__(self, config: dict):
        """
        Initialize proxy server

        Args:
            config: Config dict with host/port/username/password etc.
        """
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
        # SSL cert (not used yet)
        self.ssl_cert = config.get('ssl_cert')
        self.ssl_key = config.get('ssl_key')

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

        # Dashboard terminal refresh mode
        self.display_interval = config.get('display_interval', 5)
        self._active_connections: Dict[int, _ConnTrack] = {}
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

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=self.max_connections,  # Match max_connections config
                    limit_per_host=max(10, self.max_connections // 10),  # Scale with total
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
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                )
        return self._session

    async def close_session(self):
        """Close global HTTP client session (clean up connection pool)"""
        if self._session and not self._session.closed:
            await self._session.close()

    def check_auth(self, headers: dict) -> Tuple[bool, Optional[str]]:
        if not self.auth_enabled:
            return True, None

        # Prefer Proxy-Authorization (proxy standard header), fallback to Authorization
        auth_header = headers.get('Proxy-Authorization') or headers.get('Authorization')
        if not auth_header:
            return False, "Authentication required"
        try:
            # Format: "Basic base64(username:password)"
            auth_type, auth_info = auth_header.split(' ', 1)
            if auth_type.lower() != 'basic':
                return False, "Unsupported auth type"
            decoded = base64.b64decode(auth_info).decode('utf-8')
            username, password = decoded.split(':', 1)
            if hmac.compare_digest(username, self.username) and hmac.compare_digest(password, self.password):
                return True, None
            else:
                return False, "Authentication failed"
        except Exception as e:
            logger.error(f"Auth parse error: {e}")
            return False, "Auth format error"

    async def _read_headers(self, reader: asyncio.StreamReader) -> Tuple[dict, int]:
        headers = {}
        total_bytes = 0
        while True:
            line = await reader.readline()
            total_bytes += len(line)
            if not line or line == b'\r\n':
                break
            try:
                key, value = line.decode('utf-8').strip().split(': ', 1)
                headers[key] = value
                logger.debug(f"Header: {key}: {value}")
            except ValueError:
                continue
        return headers, total_bytes

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # ── Assign a unique RID for this connection ──
        # Each connection gets a monotonically increasing request ID (rid).
        # The rid is captured into a LOCAL variable and used throughout;
        # NEVER read self._request_counter after an await point, because
        # other concurrent tasks may have incremented it.
        self._request_counter += 1
        rid = self._request_counter
        _request_id.set(rid)
        peer = writer.get_extra_info('peername')
        if peer:
            logger.info(f"{_rid_prefix()}New connection: {peer[0]}:{peer[1]}")
        else:
            logger.info(f"{_rid_prefix()}New connection: (unknown address)")

        # Dashboard tracking
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        self._active_connections[rid] = _ConnTrack(rid, peer_str)

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
                    await writer.drain()
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
                # Track upload bytes via LOCAL rid, NOT self._request_counter
                ct = self._active_connections.get(rid)
                if ct:
                    ct.bytes_sent += hdr_bytes

                auth_ok, error_msg = self.check_auth(headers)
                if not auth_ok:
                    self.stats.auth_failed()
                    resp = f'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Proxy"\r\nContent-Type: text/plain\r\n\r\n{error_msg}'
                    self.stats.add_bytes(sent=len(resp))
                    writer.write(resp.encode('utf-8'))
                    await writer.drain()
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
                    await writer.drain()
                    break

                if method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'):
                    self.stats.http_request()
                    # Mark connection as HTTP mode for dashboard display
                    ct = self._active_connections.get(rid)
                    if ct:
                        ct.mode = 'http'
                        ct.target = f'{method} {target}'
                        ct.request_count += 1

                if method == 'CONNECT':
                    self.stats.tunnel_opened()
                    tunnel_start = time.perf_counter()
                    connect_tunnel = await self.handle_connect(reader, writer, target, headers)
                    if connect_tunnel is not None:
                        remote_reader, remote_writer = connect_tunnel

                        ct = self._active_connections.get(rid)
                        if ct:
                            ct.mode = 'tunnel'
                            ct.target = target
                            ct.tunnel_start = time.perf_counter()

                        connect_host = (target.split(']')[0][1:] if target.startswith('[')
                                       else target.split(':')[0] if ':' in target
                                       else target)
                        effective_lifetime = self.max_tunnel_lifetime
                        matched = next((p for p in self.download_hosts if fnmatch.fnmatch(connect_host, p)), None)
                        if matched:
                            effective_lifetime = self.max_tunnel_lifetime_download
                            logger.debug(f"{_rid_prefix()}Matched download host {connect_host} ({matched}), tunnel timeout {effective_lifetime}s")

                        try:
                            if effective_lifetime > 0:
                                await asyncio.wait_for(
                                    self.tunnel_traffic(reader, writer, remote_writer, remote_reader),
                                    timeout=effective_lifetime,
                                )
                            else:
                                await self.tunnel_traffic(reader, writer, remote_writer, remote_reader)
                        except asyncio.TimeoutError:
                            logger.info(f"{_rid_prefix()}CONNECT {target} reached max lifetime {effective_lifetime}s, closing tunnel")
                        finally:
                            try:
                                remote_writer.close()
                            except:
                                pass
                    else:
                        self.stats.connect_failed()
                    self.stats.tunnel_closed()
                    tunnel_elapsed = time.perf_counter() - tunnel_start
                    if tunnel_elapsed > self.slow_request_threshold:
                        logger.warning(f"{_rid_prefix()}CONNECT {target} tunnel ended | {tunnel_elapsed:.1f}s (slow, >{self.slow_request_threshold}s)")
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
                    await writer.drain()
                    break

            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                break
            except Exception:
                logger.exception(f"{_rid_prefix()}Unexpected error, cleaning up connection")
                break

        try:
            writer.close()
        except:
            pass
        self._active_writers.discard(writer)
        self.stats.conn_closed()
        # Remove from dashboard tracking; count idle connections for stats
        ct = self._active_connections.pop(rid, None)
        if ct:
            self.stats.total_disconnected += 1

    def _tune_socket(self, sock, keepalive=False):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.socket_sndbuf)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_rcvbuf)
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

    async def _connect_upstream(self, host: str, port: int) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # Select upstream: prefer https upstream for HTTPS requests, fallback to http
        upstream_https = self.upstream_proxies.get('https')
        upstream_http = self.upstream_proxies.get('http')
        proxy_url = upstream_https or upstream_http

        if not proxy_url or proxy_url.startswith('socks5'):
            if proxy_url and proxy_url.startswith('socks5'):
                logger.warning("SOCKS5 upstream for CONNECT not supported yet, connecting directly")
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
            sock = remote_writer.get_extra_info('socket')
            if sock:
                self._tune_socket(sock, keepalive=True)
            return remote_reader, remote_writer

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
            self._tune_socket(sock, keepalive=True)

        # Send CONNECT request to upstream proxy
        connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
        req_bytes = connect_req.encode()
        self.stats.add_bytes(sent=len(req_bytes))
        remote_writer.write(req_bytes)
        await remote_writer.drain()

        # Wait for upstream proxy confirmation (200 Connection Established)
        response = await asyncio.wait_for(remote_reader.readline(), timeout=15)
        self.stats.add_bytes(received=len(response))
        if not response.startswith(b'HTTP/1.1 200'):
            remote_writer.close()
            raise Exception(f"Upstream proxy CONNECT failed: {response.decode().strip()}")

        # Read and discard remaining response headers (with timeout, prevent upstream hang)
        while True:
            line = await asyncio.wait_for(remote_reader.readline(), timeout=10)
            self.stats.add_bytes(received=len(line))
            if line == b'\r\n' or not line:
                break

        return remote_reader, remote_writer

    async def handle_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
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

            logger.info(f"{_rid_prefix()}CONNECT tunnel: {host}:{port}")

            # Connect to target (possibly via upstream proxy)
            try:
                remote_reader, remote_writer = await self._connect_upstream(host, port)
            except Exception as e:
                logger.error(f"{_rid_prefix()}Failed to connect to target {host}:{port}: {e}")
                resp = b'HTTP/1.1 502 Bad Gateway\r\n\r\nCannot connect to target'
                self.stats.add_bytes(sent=len(resp))
                # Use ContextVar RID (not self._request_counter) — this method runs in the client's task
                ct = self._active_connections.get(_request_id.get())
                if ct:
                    ct.bytes_sent += len(resp)
                writer.write(resp)
                await writer.drain()
                return None

            # Notify client that tunnel is established
            resp = b'HTTP/1.1 200 Connection Established\r\n\r\n'
            self.stats.add_bytes(sent=len(resp))
            writer.write(resp)
            await writer.drain()

            # Return tunnel connection, handle_client will forward traffic after releasing semaphore
            return (remote_reader, remote_writer)

        except Exception as e:
            logger.error(f"Error handling CONNECT: {e}")
            try:
                resp = b'HTTP/1.1 500 Internal Server Error\r\n\r\n'
                self.stats.add_bytes(sent=len(resp))
                writer.write(resp)
                await writer.drain()
            except:
                pass
            return None

    async def handle_http_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                  method: str, target: str, version: str, headers: dict):
        try:
            req_start = time.perf_counter()
            # Read request body (supports Content-Length and Transfer-Encoding: chunked)
            body = b''
            if method in ('POST', 'PUT', 'PATCH'):
                content_length = headers.get('Content-Length')
                if content_length is not None:
                    cl = int(content_length)
                    if cl > self.max_body_size:
                        self.stats.add_bytes(sent=len(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n'))
                        writer.write(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n')
                        await writer.drain()
                        return False
                    body = await reader.readexactly(cl)
                    self.stats.add_bytes(sent=len(body))
                    ct_body = self._active_connections.get(_request_id.get())
                    if ct_body:
                        ct_body.bytes_sent += len(body)
                elif headers.get('Transfer-Encoding', '').lower() == 'chunked':
                    while True:
                        size_line = await reader.readline()
                        if not size_line:
                            break
                        self.stats.add_bytes(sent=len(size_line))
                        size_line = size_line.strip()
                        if not size_line:
                            continue
                        chunk_size = int(size_line, 16)
                        if chunk_size == 0:
                            await reader.readline()
                            break
                        if len(body) + chunk_size > self.max_body_size:
                            self.stats.add_bytes(sent=len(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n'))
                            writer.write(b'HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n')
                            await writer.drain()
                            return False
                        body += await reader.readexactly(chunk_size)
                        self.stats.add_bytes(sent=chunk_size)
                        await reader.readline()

            # Parse full URL
            if target.startswith('http://') or target.startswith('https://'):
                url = target
            else:
                # Relative path: build full URL from Host header
                host = headers.get('Host')
                if not host:
                    self.stats.add_bytes(sent=len(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nMissing Host header'))
                    writer.write(b'HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nMissing Host header')
                    await writer.drain()
                    return False
                url = f'http://{host}{target}' if target.startswith('/') else f'http://{host}/{target}'

            logger.info(f"{_rid_prefix()}HTTP forward: {method} {url}")

            # Filter forward headers: remove proxy-specific and hop-by-hop headers
            forward_headers = {}
            skip_headers = ['proxy-connection', 'proxy-authorization', 'connection', 'host']
            for k, v in headers.items():
                if k.lower() not in skip_headers:
                    forward_headers[k] = v

            # Determine upstream proxy to use (HTTPS requests use https upstream, HTTP uses http upstream)
            upstream_http = self.upstream_proxies.get('http')
            upstream_https = self.upstream_proxies.get('https')
            proxy_url = None
            if url.startswith('https://') and upstream_https:
                proxy_url = upstream_https
            elif url.startswith('http://') and upstream_http:
                proxy_url = upstream_http

            # Get global connection pool session
            session = await self._get_session()

            # Exponential backoff retry: transient failures (e.g. DNS timeout) auto-retry once
            max_retries = 1
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    if proxy_url and proxy_url.startswith('socks5'):
                        # SOCKS5 upstream proxy: use ProxyConnector from aiohttp-socks
                        if not AIOHTTP_SOCKS_AVAILABLE:
                            raise Exception("aiohttp-socks not installed, cannot use SOCKS5 upstream")

                        if attempt == 0:
                            # First attempt: via SOCKS5 proxy
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
                                    await self._write_response(writer, resp)
                        else:
                            # Retry: fallback to direct connection (bypass problematic upstream)
                            async with session.request(
                                method, url,
                                headers=forward_headers,
                                data=body if body else None,
                                allow_redirects=False,
                            ) as resp:
                                await self._write_response(writer, resp)
                    else:
                        # HTTP upstream or direct connection
                        kwargs = dict(headers=forward_headers, data=body if body else None, allow_redirects=False)
                        if proxy_url and attempt == 0:
                            kwargs['proxy'] = proxy_url  # aiohttp native HTTP proxy
                        async with session.request(method, url, **kwargs) as resp:
                            await self._write_response(writer, resp)
                    req_elapsed = time.perf_counter() - req_start
                    self.stats.record_http_elapsed(req_elapsed)
                    if req_elapsed > self.slow_request_threshold:
                        logger.warning(f"Slow request: {method} {url} | {req_elapsed:.1f}s (>{self.slow_request_threshold}s)")
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
            raise last_error

        except aiohttp.ClientError as e:
            self.stats.http_failed()
            self.stats.upstream_error()
            logger.error(f"{_rid_prefix()}HTTP forward failed: {e}")
            try:
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
                await writer.drain()
            except:
                pass
            return False
        except asyncio.TimeoutError:
            self.stats.http_failed()
            self.stats.timeout_error()
            logger.error(f"HTTP forward timeout")
            try:
                writer.write(b'HTTP/1.1 504 Gateway Timeout\r\nConnection: close\r\n\r\n')
                await writer.drain()
            except:
                pass
            return False
        except Exception as e:
            self.stats.http_failed()
            self.stats.upstream_error()
            logger.error(f"Error handling HTTP request: {e}")
            return False

    async def _write_response(self, writer: asyncio.StreamWriter, resp: aiohttp.ClientResponse):
        # Use ContextVar to get the correct RID for this task (per-task isolated)
        rid = _request_id.get()
        try:
            # Write status line
            writer.write(f'HTTP/1.1 {resp.status} {resp.reason}\r\n'.encode('utf-8'))
            # Write response headers (filter out hop-by-hop headers)
            for key, value in resp.headers.items():
                if key.lower() not in ['transfer-encoding', 'connection', 'content-encoding',
                                        'keep-alive', 'proxy-authenticate', 'proxy-connection',
                                        'upgrade', 'trailer']:
                    writer.write(f'{key}: {value}\r\n'.encode('utf-8'))
            writer.write(b'\r\n')
            # Stream response body in chunks; track DOWN bytes for this connection
            async for chunk in resp.content.iter_chunked(self.io_buffer_size):
                self.stats.add_bytes(received=len(chunk))
                ct = self._active_connections.get(rid)
                if ct:
                    ct.bytes_received += len(chunk)
                writer.write(chunk)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            logger.debug("Client disconnected during response transfer")
        except Exception as e:
            logger.debug(f"Response write error: {e}")

    async def tunnel_traffic(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter,
                            remote_writer: asyncio.StreamWriter, remote_reader: asyncio.StreamReader):
        # Tunnel idle timeout in seconds
        TUNNEL_IDLE_TIMEOUT = self.tunnel_idle_timeout
        tunnel_start = time.perf_counter()

        async def forward(src_reader: asyncio.StreamReader, dst_writer: asyncio.StreamWriter, name: str):
            # ContextVar returns the per-task RID (isolated from other concurrent tasks)
            rid = _request_id.get()
            try:
                while True:
                    data = await asyncio.wait_for(src_reader.read(self.io_buffer_size), timeout=TUNNEL_IDLE_TIMEOUT)
                    if not data:
                        break
                    if name.startswith("client"):
                        self.stats.add_bytes(sent=len(data))
                    else:
                        self.stats.add_bytes(received=len(data))
                    # Track bytes for the correct connection via per-task RID
                    ct = self._active_connections.get(rid)
                    if ct:
                        if name.startswith("client"):
                            ct.bytes_sent += len(data)
                        else:
                            ct.bytes_received += len(data)
                    dst_writer.write(data)
                    await dst_writer.drain()
            except asyncio.TimeoutError:
                logger.debug(f"Tunnel {name} idle timeout closed")
            except Exception as e:
                logger.debug(f"Tunnel {name} forward ended: {e}")
            finally:
                try:
                    dst_writer.close()
                except:
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
            except:
                pass
            try:
                client_writer.close()
            except:
                pass
        self.stats.record_tunnel_duration(time.perf_counter() - tunnel_start)

    async def _stats_logger(self):
        while not self._shutting_down:
            await asyncio.sleep(self.stats_interval)
            if self._shutting_down:
                break
            logger.info(self.stats.format_text())
            self.stats.save()
            self.stats.snapshot_period()

    def _render_dashboard(self):
        """Render the live Dashboard (pure ASCII, no CJK width issues)"""
        if os.name == 'nt':
            os.system('cls')
        else:
            sys.stdout.write('\033[2J\033[H')
            sys.stdout.flush()
        now = time.perf_counter()
        uptime = now - self._server_start
        h, m = divmod(int(uptime), 3600)
        m, s = divmod(m, 60)
        uptime_str = f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
        active_conn = len(self._active_connections)
        active_tun = sum(1 for ct in self._active_connections.values() if ct.mode == 'tunnel')
        total_idle = self.stats.total_disconnected
        # Use session bytes (reset on restart), not total (persistent across restarts)
        total_str = f"Total U {_format_bytes(self.stats.session_bytes_sent)} D {_format_bytes(self.stats.session_bytes_received)}"

        W = 120
        sep = '=' * W
        lines = []
        lines.append('+' + sep + '+')
        header = f" Proxy {self.host}:{self.port}  |  Active:{active_conn}  TUN:{active_tun}  DONE:{total_idle}  UP:{uptime_str}  |  {total_str} "
        lines.append('|' + _ljust_display(header, W) + '|')
        lines.append('+' + sep + '+')

        for ct in list(self._active_connections.values()):
            duration = now - ct.connected_at
            total_sec = int(duration)
            d_hours, d_rem = divmod(total_sec, 3600)
            d_mins, d_secs = divmod(d_rem, 60)
            if d_hours:
                dur_str = f"{d_hours}h{d_mins:02d}m"
            else:
                dur_str = f"{d_mins}m{d_secs:02d}s"

            if ct.mode == 'tunnel':
                t_dur = now - ct.tunnel_start
                total_sec = int(t_dur)
                t_hours, rem = divmod(total_sec, 3600)
                t_mins, t_secs = divmod(rem, 60)
                if t_hours:
                    ts = f"{t_hours}h{t_mins:02d}m"
                else:
                    ts = f"{t_mins}m{t_secs:02d}s"
                mode_str = f"TUN {ts}"
            elif ct.mode == 'http':
                mode_str = f"HTTP x{ct.request_count}"
            else:
                mode_str = "IDLE"

            ip = ct.peer.split(':')[0]
            line = f" {ip:<15} {mode_str:<16} UP:{_format_bytes(ct.bytes_sent):>8}  DOWN:{_format_bytes(ct.bytes_received):>8}"
            if ct.mode != 'tunnel':
                line += f"  {dur_str}"
            line = _truncate_to_width(line, W)
            lines.append('|' + _ljust_display(line, W) + '|')

        lines.append('+' + sep + '+')
        sys.stdout.write(os.linesep.join(lines))
        sys.stdout.flush()

    async def _active_display(self):
        """Periodically refresh the Dashboard"""
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
                        except:
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


def load_config(config_file: str = 'config.yaml') -> dict:
    import yaml
    default_config = {
        'host': '0.0.0.0',
        'port': 8080,
        'username': 'admin',
        'password': 'password123',
        'auth_enabled': True,
        'log_level': 'INFO',
        'upstream_proxies': {},
        'max_connections': 500,
    }
    if os.path.exists(config_file):
        user_config = None
        for enc in ('utf-8', 'gbk'):
            try:
                with open(config_file, 'r', encoding=enc) as f:
                    user_config = yaml.safe_load(f)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        try:
            if user_config:
                default_config.update(user_config)
            print(f"[OK] Loaded config: {config_file}")
            print(f"   Auth: {'Enabled' if default_config['auth_enabled'] else 'Disabled'}")
            print(f"   Username: {default_config['username']}")
            print(f"   Password: {'*' * len(default_config['password']) if default_config['password'] else '(empty)'}")
        except Exception as e:
            logger.error(f"Failed to load config file: {e}, using defaults")
    else:
        print(f"[!] Config file {config_file} not found, using default configuration")
        print(f"[*] Copy {config_file}.example and edit it")
    return default_config


def main():
    import argparse
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

    # Configure log rotation
    log_file = config.get('log_file', '')
    if log_file:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=config.get('log_max_size', 10 * 1024 * 1024),
            backupCount=config.get('log_backup_count', 5),
            encoding='utf-8'
        )
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(handler)
        logger.info(f"Log file: {log_file}")

    # Set log level
    if args.debug:
        config['log_level'] = 'DEBUG'
        logging.getLogger().setLevel(logging.DEBUG)
    elif 'log_level' in config:
        log_level = getattr(logging, config['log_level'].upper(), logging.INFO)
        logging.getLogger().setLevel(log_level)

    try:
        server = ProxyServer(config)
        asyncio.run(server.start_server())
    except KeyboardInterrupt:
        print("\nServer stopped")
    except Exception as e:
        logger.error(f"Server start failed: {e}")
        sys.exit(1)
    finally:
        gc.collect()


if __name__ == '__main__':
    main()
