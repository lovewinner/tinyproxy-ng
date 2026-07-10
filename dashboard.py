import logging
import os
import sys
import time

from stats import format_bytes


def display_width(s: str) -> int:
    """Calculate display width (CJK chars count as 2)"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def ljust_display(s: str, width: int) -> str:
    """Left-align pad by display width"""
    pad = max(0, width - display_width(s))
    return s + ' ' * pad


def truncate_to_width(s: str, max_w: int) -> str:
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
        except Exception:
            pass


class ConnTrack:
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



def render_dashboard(server):
    """Render the live Dashboard (pure ASCII, no CJK width issues)"""
    if os.name == 'nt':
        os.system('cls')
    elif sys.stdout.isatty():
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()
    now = time.perf_counter()
    uptime = now - server._server_start
    h, m = divmod(int(uptime), 3600)
    m, s = divmod(m, 60)
    uptime_str = f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
    active_conn = len(server._active_connections)
    active_tun = sum(1 for ct in server._active_connections.values() if ct.mode == 'tunnel')
    total_idle = server.stats.total_disconnected
    # Use session bytes (reset on restart), not total (persistent across restarts)
    total_str = f"Total U {format_bytes(server.stats.session_bytes_sent)} D {format_bytes(server.stats.session_bytes_received)}"

    W = 120
    sep = '=' * W
    lines = []
    lines.append('+' + sep + '+')
    header = f" Proxy {server.host}:{server.port}  |  Active:{active_conn}  TUN:{active_tun}  DONE:{total_idle}  UP:{uptime_str}  |  {total_str} "
    lines.append('|' + ljust_display(header, W) + '|')
    lines.append('+' + sep + '+')

    for ct in list(server._active_connections.values()):
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
        line = f" {ip:<15} {mode_str:<16} UP:{format_bytes(ct.bytes_sent):>8}  DOWN:{format_bytes(ct.bytes_received):>8}"
        if ct.mode != 'tunnel':
            line += f"  {dur_str}"
        line = truncate_to_width(line, W)
        lines.append('|' + ljust_display(line, W) + '|')

    lines.append('+' + sep + '+')
    sys.stdout.write(os.linesep.join(lines))
    sys.stdout.flush()
