import json
import logging
import os
import time

logger = logging.getLogger(__name__)


def format_bytes(size: int) -> str:
    """Format bytes as human-readable string"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"



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
        self.last_period: dict[str, object] = {}

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
            f"  Bytes:   {format_bytes(t['bytes_sent'])} sent / {format_bytes(t['bytes_received'])} received",
            f"  Errors:  {t['auth_failures']} auth  {t['upstream_errors']} upstream  {t['timeout_errors']} timeout",
            f"  HTTP:    {t['http_requests']} reqs  avg {t['http_avg_ms']}ms  max {t['http_max_ms']}ms  fail {t['http_failed']}",
            f"  Tunnel:  {t['connect_tunnels']} CONNECT  avg {t['tunnel_avg_ms']}ms  fail {t['connect_failed']}",
        ]
        lp = s.get("last_period")
        if lp:
            lines += [
                f"  ── Last period ({lp['duration_seconds']}s) ──",
                f"  Reqs:   {lp['connections']} conns  {lp['http_requests']} HTTP  {lp['connect_tunnels']} CONNECT",
                f"  Bytes:  {format_bytes(lp['bytes_sent'])} sent / {format_bytes(lp['bytes_received'])} received",
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
                with open(self.persist_file, encoding="utf-8") as f:
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
            tmp_file = self.persist_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.persist_file)
        except Exception as e:
            logger.debug(f"Stats save failed: {e}")
