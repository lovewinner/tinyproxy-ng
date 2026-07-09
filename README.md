# tinyproxy-ng

> **[Chinese Doc](README_zh.md)** | **English**

A high-performance HTTP/HTTPS proxy server built on Python asyncio + aiohttp with authentication, CONNECT tunneling, upstream proxy chaining, stats monitoring, and auto-recovery.

## Features

- HTTP/HTTPS proxy (CONNECT tunneling)
- HTTP Basic authentication (Proxy-Authorization)
- Upstream proxy chaining (HTTP upstream: supported for both forward proxy and CONNECT tunnels; SOCKS5 upstream: forward proxy only, requires aiohttp-socks)
- Global connection pool reuse + DNS cache + TCP tuning
- Tunnel idle timeout (180s) + max lifetime (configurable)
- Auto-extended timeout for download hosts (`download_hosts`)
- Slow request detection + per-request tracing ID
- Log rotation (RotatingFileHandler)
- Built-in stats system (JSON + HTTP endpoint)
- Exponential backoff retry + semaphore concurrency control
- Graceful shutdown (5s wait + force cleanup)
- **Live terminal Dashboard** — active connections, tunnels, HTTP requests, bytes, auto-refreshed
- **Session-only stats** — Dashboard Total↑↓ resets on restart; `stats.json` persists historical totals

## Requirements

- Python 3.8+
- `pip install aiohttp pyyaml`

## Quick Start

```bash
# 1. Install dependencies
pip install aiohttp pyyaml

# 2. Copy and edit config
cp config.example.yaml config.yaml
# Edit config.yaml, set username and password

# 3. Start
python proxy_server.py
```

Browser setup: HTTP proxy → server IP → port (default 26128) → enable authentication.

## CLI

```bash
python proxy_server.py --host 127.0.0.1 --port 8888 --user admin --passwd secret --debug
```

| Flag | Description |
|------|-------------|
| `-c, --config` | Config file path |
| `--host` | Bind address |
| `--port` | Bind port |
| `--user` | Auth username |
| `--passwd` | Auth password |
| `--no-auth` | Disable authentication |
| `--debug` | DEBUG logging |

## Config

See `config.example.yaml` for the full configuration. Key parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `port` | 26128 | Listen port |
| `auth_enabled` | true | Enable Basic auth |
| `max_connections` | 500 | Max concurrent connections |
| `max_body_size` | 10MB | Max request body size |
| `tunnel_idle_timeout` | 180s | Tunnel idle close timeout |
| `max_tunnel_lifetime` | 300s | Max tunnel lifetime |
| `max_tunnel_lifetime_download` | 7200s | Extended tunnel timeout for downloads |
| `download_hosts` | `*.github.com` etc. | Auto-match download hosts |
| `slow_request_threshold` | 5.0s | Slow request warning threshold |
| `stats_interval` | 3600s | Stats log/snapshot interval |
| `display_interval` | 2s | Dashboard refresh interval (0 = traditional log mode) |

## Dashboard

When `display_interval > 0`, the terminal enters live Dashboard mode and refreshes periodically:

```
+========================================================================================================================+
| Proxy 0.0.0.0:26128  |  Active:18  TUN:9  DONE:5  UP:3h49m  |  Total↑2.1 MB ↓88.6 MB                                 |
+========================================================================================================================+
| 192.168.1.100   TUN 36m51s       UP:  4.7 KB  DOWN:  4.4 KB                                                            |
| 192.168.1.100   TUN 12m46s       UP:  3.5 KB  DOWN:  5.5 KB                                                            |
| 192.168.1.100   HTTP x2          UP:  3.2 KB  DOWN:  2.6 KB  0m51s                                                     |
+========================================================================================================================+
```

Row fields:

| Column | Description |
|--------|-------------|
| **IP** | Client address |
| **Mode** | `TUN {duration}` / `HTTP x{N}` / `IDLE` |
| **UP/DOWN** | Upload/download bytes for this connection |
| **Duration** | Connection lifetime (tunnel duration shown for tunnels) |

Header fields:

| Metric | Description |
|--------|-------------|
| **Active** | Current active connections |
| **TUN** | Tunnel count |
| **DONE** | Total disconnected connections |
| **UP** | Server uptime |
| **Total↑↓** | Total upload/download bytes for **the current session** (resets on restart) |

## Stats

Stats are logged every `stats_interval` (default 3600s) and saved to `stats.json` (persisted across restarts). Accessible via HTTP (requires auth):

```
curl -u user:pass http://proxy-stats/
```

Structure:

```
total:       Cumulative values since first start (persistent)
last_period: Snapshot of the last period (in-memory only)
```

The Dashboard's `Total↑↓` displays **session-only** byte counts (`session_bytes_sent/received`), separate from the persistent `total`.

JSON example:

```json
{
  "server": "running",
  "started_at": 1720435200,
  "uptime_seconds": 3600,
  "active_connections": 5,
  "active_tunnels": 3,
  "total": {
    "connections": 10000,
    ...
  },
  "last_period": { ... }
}
```

## Structure

```
proxy-server/
├── proxy_server.py          # Main program
├── config.example.yaml      # Example config
├── requirements.txt
├── LICENSE
├── README.md                # English README
├── README_zh.md             # Chinese documentation
├── scripts/                 # Startup / install scripts
└── tests/                   # Test tools
    ├── stress_test.py       # Load test
    ├── test_proxy.py        # Functional test
    └── test_auth.py         # Auth test
```

## Architecture

```
Client ──TCP──> asyncio Server ──aiohttp──> Target (HTTP)
                (handle_client)  ──Tunnel──> Target (HTTPS/CONNECT)
```

- **HTTP requests**: The proxy parses the URL, forwards via aiohttp, and streams the response body
- **CONNECT tunnels**: Creates a bidirectional TCP tunnel with idle timeout + max lifetime protection
- **Auth**: HTTP Basic authentication; breaks keep-alive loop after 407
- **Stats**: `StatsCollector` with cumulative counters + sliding window + JSON persistence
- **Dashboard**: `_active_connections` dict tracks per-connection state; `_active_display` renders periodically

## License

MIT
