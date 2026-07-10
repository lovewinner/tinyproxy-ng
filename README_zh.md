# tinyproxy-ng

> **中文** | [English](README.md)

轻量 Python asyncio HTTP/HTTPS 代理服务器，支持认证、CONNECT 隧道、上游代理链、速率限制和实时终端面板。模块化实现，依赖保持精简。

---

## 1. 能做什么

- 接受浏览器、CLI 工具 (`curl -x`) 或系统级代理设置的 HTTP 代理请求。
- HTTP 流量转发到目标服务器；HTTPS 建立 CONNECT 隧道后透传原始 TCP。
- 服务器自身无法直连外网时，可通过上游代理（HTTP 或 SOCKS5）链式转发。
- 可选每 IP 速率限制 + Basic 认证防止滥用。
- 终端实时面板展示活跃连接、隧道和流量吞吐。
- 累计统计（JSON API + 本地持久化）用于长期监控。

---

## 2. 快速上手

### 安装依赖

```bash
pip install aiohttp pyyaml
```

需 Python 3.8+。如需 SOCKS5 上游，追加 `pip install aiohttp-socks`。

### 配置

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，至少改一下用户名和密码：

```yaml
auth_enabled: true
username: myuser
password: mypass
port: 8080     # 可按需修改
```

如果不需要上游代理，`upstream_proxies` 保持注释状态即可。

### 启动

```bash
python proxy_server.py
```

也可用 CLI 参数直接覆盖配置：

```bash
python proxy_server.py --host 127.0.0.1 --port 8888 --user admin --passwd secret --debug
```

| 参数 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径（默认 `config.yaml`） |
| `--host` | 监听地址 |
| `--port` | 监听端口 |
| `--user` | 认证用户名 |
| `--passwd` | 认证密码 |
| `--no-auth` | 禁用认证 |
| `--debug` | 启用 DEBUG 日志 |

### 测试

```bash
python -m pytest
```

### 客户端设置

浏览器或系统代理指向 `服务器IP:端口`，填入配置中的用户名密码。

- **Chrome/Edge**：设置 → 系统 → 打开代理设置 → 手动代理 → HTTP 代理
- **Firefox**：设置 → 网络设置 → 手动代理配置 → HTTP 代理
- **curl**：`curl -x http://user:pass@server:26128 https://example.com`
- **环境变量**：`export http_proxy=http://user:pass@server:26128`

---

## 3. 特性与配置详解

### 完整配置参数表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `host` | `0.0.0.0` | 监听地址；`127.0.0.1` = 仅本机 |
| `port` | `8080` | 监听端口 |
| `auth_enabled` | `true` | 启用 HTTP Basic 认证 |
| `username` | — | 认证用户名 |
| `password` | — | 认证密码 |
| `upstream_proxies` | (无) | 按协议指定上游 HTTP/SOCKS5 代理；详见 `config.example.yaml` |
| `max_connections` | `500` | 最大并发连接数（信号量） |
| `max_body_size` | `10 MB` | 请求体上限；超限 → 413 |
| `max_request_line_size` | `16384` | 请求 URL 最大长度；超限 → 414 |
| `tunnel_idle_timeout` | `180 s` | CONNECT 隧道闲置超时 |
| `max_tunnel_lifetime` | `300 s` | CONNECT 隧道最长存活 |
| `max_tunnel_lifetime_download` | `7200 s` | 下载类主机延长存活 |
| `download_hosts` | `*.github.com` … | glob 匹配列表；命中则应用延长存活 |
| `header_timeout` | `15 s` | 请求头读取超时（防 Slowloris） |
| `drain_timeout` | `30 s` | 单次客户端 drain 超时；防慢客户端永久挂起 |
| `io_buffer_size` | `65536` | I/O 缓冲区大小（字节） |
| `socket_sndbuf` | `262144` | 套接字发送缓冲区 |
| `socket_rcvbuf` | `262144` | 套接字接收缓冲区 |
| `max_keepalive_requests` | `100` | 每条 Keep-Alive 连接最大请求数 |
| `keepalive_timeout` | `30 s` | Keep-Alive 闲置超时 |
| `rate_limit_enabled` | `false` | 启用每 IP 速率限制 |
| `rate_limit_per_minute` | `300` | 每 IP 每分钟最大请求数（60 s 滑动窗口） |
| `dns_cache_ttl` | `300 s` | 直连 CONNECT 隧道 DNS 缓存 TTL |
| `slow_request_threshold` | `5.0 s` | 超过此阈值打印 WARNING |
| `stats_interval` | `60 s` | 定期统计日志间隔（0 = 禁用） |
| `stats_file` | `stats.json` | 统计数据持久化文件 |
| `stats_host` | `proxy-stats` | 通过 HTTP 访问统计所需的 Host 头 |
| `display_interval` | `5 s` | 终端面板刷新间隔（0 = 传统日志模式） |
| `log_file` | (stdout) | 可选日志文件路径 |
| `log_max_size` | `10 MB` | 日志文件最大尺寸 |
| `log_backup_count` | `5` | 保留的历史日志文件数量 |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### 实时面板

`display_interval > 0` 时，终端进入实时面板模式（完整日志仍写入文件）：

```
+========================================================================================================================+
| Proxy 0.0.0.0:8080  |  Active:18  TUN:9  DONE:5  UP:3h49m  |  Total U 2.1 MB D 88.6 MB                                  |
+========================================================================================================================+
| 192.168.1.100   TUN 36m51s       UP:  4.7 KB  DOWN:  4.4 KB                                                            |
| 192.168.1.100   HTTP x2          UP:  3.2 KB  DOWN:  2.6 KB  0m51s                                                     |
+========================================================================================================================+
```

- **表头**：活跃连接 / 隧道数 / 已断开总数 / 运行时长 / 本次会话字节总量（重启归零）。
- **行**：客户端 IP → `TUN`（隧道）或 `HTTP x{N}` → 上/下行字节 → 持续时长。
- 已断开连接自动清理；`DONE` 递增。

### 统计

统计每 `stats_interval` 输出日志，原子写入 `stats.json`。可通过 HTTP 访问：

```bash
curl -u user:pass http://proxy-stats/
```

响应包含 `total`（跨重启累计）、`last_period`（上一周期快照）、实时连接/隧道数和运行时长。

### 目录结构

```
proxy-server/
├── proxy_server.py          # 入口与服务编排
├── config.py                # 配置加载与日志设置
├── stats.py                 # 统计收集与持久化
├── auth.py                  # Basic 代理认证
├── http_forward.py          # HTTP 请求转发
├── tunnel.py                # CONNECT 隧道处理
├── dashboard.py             # 终端实时面板
├── config.example.yaml      # 带注释的配置模板
├── requirements.txt
├── LICENSE                  # MIT
├── README.md / README_zh.md
├── scripts/                 # 启动/安装脚本
└── tests/                   # stress_test.py, test_proxy.py, test_auth.py
```

### 核心模块

| 模块 | 说明 |
|------|------|
| **HTTP 转发** | 解析 URL，通过 aiohttp 转发并重试/指数退避；上游缺 Content-Length 时自动重新分块。 |
| **CONNECT 隧道** | 解析全部地址逐尝试（每地址 10 s 连接超时）；双向透传 + 闲置 + 存活双重超时。 |
| **认证** | HTTP Basic，`hmac.compare_digest` 时序安全比对；407 后保活循环退出。 |
| **速率限制** | 每 IP 60 s 滑动窗口；超限返回 429。 |
| **drain 保护** | 每次客户端写入均包裹 `_safe_drain(writer)`，有超时上限，防止慢客户端永久反压挂起。 |
| **统计** | `StatsCollector` — 双模式（持久化 `total` + 临时 `last_period`），原子 JSON 写入。 |
| **面板** | `_active_connections` 字典追踪每连接状态；`_active_display` 定时刷新；连接关闭时自动清理死条目。 |
| **退出** | 5 s 宽限期 → 强制关闭所有已追踪 writer + 取消任务。 |

## License

MIT
