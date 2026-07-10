# tinyproxy-ng

> **[中文文档](README_zh.md)** | **English**

A lightweight Python asyncio HTTP/HTTPS proxy server with authentication, CONNECT tunneling, upstream proxy chaining, rate limiting, and a live terminal dashboard. Modular implementation with a small dependency set.

---

## 1. What It Does

- Accepts HTTP proxy requests from browsers, CLI tools (`curl -x`), or OS-level proxy settings.
- Forwards HTTP traffic to target servers; for HTTPS, establishes CONNECT tunnels and relays raw TCP.
- Supports chaining through an upstream proxy (HTTP or SOCKS5) when the server itself lacks direct internet access.
- Optional per-IP rate limiting and Basic authentication protect against abuse.
- Live terminal dashboard shows real-time connections, tunnels, and throughput.
- Collects cumulative stats (JSON endpoint + local persistence) for long-term monitoring.

---

## 2. How to Use

### Install

```bash
pip install aiohttp pyyaml
```

Python 3.8+ required.  If you need SOCKS5 upstream, also `pip install aiohttp-socks`.

### Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml` — at minimum set `username` and `password`:

```yaml
auth_enabled: true
username: myuser
password: mypass
port: 8080     # change if needed
```

Leave `upstream_proxies` commented out unless you need an outbound proxy.

### Run

```bash
python proxy_server.py
```

Or override settings via CLI:

```bash
python proxy_server.py --host 127.0.0.1 --port 8888 --user admin --passwd secret --debug
```

| Flag | Description |
|------|-------------|
| `-c, --config` | Config file path (default: `config.yaml`) |
| `--host` | Listen address |
| `--port` | Listen port |
| `--user` | Auth username |
| `--passwd` | Auth password |
| `--no-auth` | Disable authentication |
| `--debug` | Enable DEBUG logging |

### Test

```bash
python -m pytest
```

### Client Setup

Configure your browser or OS to use an HTTP proxy at `server-ip:port` with the username/password from config.

- **Chrome/Edge**: Settings → System → Open proxy settings → Manual proxy → HTTP proxy
- **Firefox**: Settings → Network Settings → Manual proxy configuration → HTTP proxy
- **curl**: `curl -x http://user:pass@server:26128 https://example.com`
- **Environment**: `export http_proxy=http://user:pass@server:26128`

---

## 3. Features & Configuration Reference

### Complete Config Table

| Parameter | Default | Description |
|-----------|---------|-------------|
| `host` | `0.0.0.0` | Listen address; `127.0.0.1` = local only |
| `port` | `8080` | Listen port |
| `auth_enabled` | `true` | Enable HTTP Basic authentication |
| `username` | — | Auth username |
| `password` | — | Auth password |
| `upstream_proxies` | (none) | Upstream HTTP/SOCKS5 proxy per protocol; see `config.example.yaml` |
| `max_connections` | `500` | Max concurrent connections (semaphore) |
| `max_body_size` | `10 MB` | Max request body; oversize → 413 |
| `max_request_line_size` | `16384` | Max URL length; oversize → 414 |
| `tunnel_idle_timeout` | `180 s` | CONNECT tunnel idle timeout |
| `max_tunnel_lifetime` | `300 s` | Max CONNECT tunnel lifetime |
| `max_tunnel_lifetime_download` | `7200 s` | Extended lifetime for download hosts |
| `download_hosts` | `*.github.com` … | Glob patterns; matched hosts get extended lifetime |
| `header_timeout` | `15 s` | Request header read timeout (Slowloris protection) |
| `drain_timeout` | `30 s` | Per-write drain timeout; prevents hangs on slow clients |
| `io_buffer_size` | `65536` | I/O buffer size (bytes) |
| `socket_sndbuf` | `262144` | Socket send buffer |
| `socket_rcvbuf` | `262144` | Socket receive buffer |
| `max_keepalive_requests` | `100` | Max requests per keep-alive connection |
| `keepalive_timeout` | `30 s` | Keep-alive idle timeout |
| `rate_limit_enabled` | `false` | Enable per-IP rate limiting |
| `rate_limit_per_minute` | `300` | Max requests/min per client IP (60 s sliding window) |
| `dns_cache_ttl` | `300 s` | DNS cache TTL for direct CONNECT tunnels |
| `slow_request_threshold` | `5.0 s` | Log WARNING if request exceeds this |
| `stats_interval` | `60 s` | Periodic stats logging interval (0 = disable) |
| `stats_file` | `stats.json` | Stats persistence file |
| `stats_host` | `proxy-stats` | Host header to access stats over HTTP |
| `display_interval` | `5 s` | Dashboard refresh interval (0 = plain log mode) |
| `log_file` | (stdout) | Optional log file path |
| `log_max_size` | `10 MB` | Max log file size before rotation |
| `log_backup_count` | `5` | Number of rotated log files kept |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### Dashboard

When `display_interval > 0`, the terminal shows a live dashboard (full logs still go to file):

```
+========================================================================================================================+
| Proxy 0.0.0.0:8080  |  Active:18  TUN:9  DONE:5  UP:3h49m  |  Total U 2.1 MB D 88.6 MB                                  |
+========================================================================================================================+
| 192.168.1.100   TUN 36m51s       UP:  4.7 KB  DOWN:  4.4 KB                                                            |
| 192.168.1.100   HTTP x2          UP:  3.2 KB  DOWN:  2.6 KB  0m51s                                                     |
+========================================================================================================================+
```

- **Header**: Active connections / tunnel count / disconnected total / uptime / session byte totals (reset on restart).
- **Rows**: Client IP → `TUN` (tunnel) or `HTTP x{N}` → up/down bytes → connection duration.
- Disconnected connections are purged automatically; `DONE` increments.

### Stats

Stats are logged every `stats_interval` and atomically persisted to `stats.json`.  Access via HTTP:

```bash
curl -u user:pass http://proxy-stats/
```

Response includes a `total` section (cumulative across restarts), a `last_period` snapshot, live connection/tunnel counts, and uptime.

### Structure

```
proxy-server/
├── proxy_server.py          # Entrypoint + server orchestration
├── config.py                # Config loading and logging setup
├── stats.py                 # Stats collection and persistence
├── auth.py                  # Basic proxy authentication
├── http_forward.py          # HTTP request forwarding
├── tunnel.py                # CONNECT tunnel handling
├── dashboard.py             # Live terminal dashboard
├── config.example.yaml      # Annotated config template
├── requirements.txt
├── LICENSE                  # MIT
├── README.md / README_zh.md
├── scripts/                 # Startup / install helpers
└── tests/                   # stress_test.py, test_proxy.py, test_auth.py
```

### Key Internals

| Module | Description |
|--------|-------------|
| **HTTP forward** | Parses URL, forwards via aiohttp with retry + exponential backoff; re-chunks response when upstream omits Content-Length. |
| **CONNECT tunnel** | Resolves all addresses, tries each with per-address 10 s connect timeout; bidirectional relay with idle + lifetime timeouts. |
| **Auth** | HTTP Basic via `hmac.compare_digest` (timing-safe). 407 breaks keep-alive loop. |
| **Rate limit** | Per-IP 60 s sliding window; 429 when exceeded. |
| **Drain protection** | Every client write is wrapped in `_safe_drain(writer)` with configurable timeout; prevents indefinite backpressure hangs. |
| **Stats** | `StatsCollector` — dual-layer (persistent `total` + ephemeral `last_period`), atomic JSON writes. |
| **Dashboard** | `_active_connections` dict per connection; `_active_display` periodic render; dead entries purged on close. |
| **Shutdown** | 5 s grace period then force-close all tracked writers + cancel tasks. |

## License

MIT
