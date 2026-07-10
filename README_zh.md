# tinyproxy-ng

> **中文** | [English](README.md)

基于 Python asyncio + aiohttp 的高性能 HTTP/HTTPS 代理服务器，支持认证、CONNECT 隧道、上游代理链、统计监控与自动恢复。

## Feature

- HTTP/HTTPS 代理（CONNECT 隧道）
- HTTP Basic 认证（`hmac.compare_digest` 时序安全）
- 上游代理链（HTTP 上游：转发 + CONNECT 均支持；SOCKS5 上游：仅 HTTP 转发支持，需安装 aiohttp-socks）
- Happy Eyeballs 全地址重试（直连 CONNECT 隧道）
- 每 IP 频率限制（可配置请求/分钟窗口）
- 全局连接池复用 + DNS 缓存（可配 TTL）+ TCP 参数调优
- CONNECT 隧道：闲置超时（180s）+ 最长存活 + 每地址连接超时
- 下载主机自动匹配延长存活时间（`download_hosts`）
- 客户端 drain 超时保护（`drain_timeout`）；防止慢客户端导致永久挂起
- 请求 URL 长度限制（`max_request_line_size`）；超长返回 414
- 请求体大小限制（`max_body_size`）；超限返回 413 并清空剩余 chunk
- 慢请求检测 + 请求级追踪 ID
- 日志轮转（RotatingFileHandler）
- 内置统计系统（JSON + HTTP 端点，原子写入）
- 指数退避重试 + 信号量并发控制
- 优雅关闭（5s 等待 + 强制清理）
- 响应重组 chunk：剥离上游 hop-by-hop 头，缺失 Content-Length 时自动重新分块
- **终端实时 Dashboard**：显示活跃连接、隧道、HTTP 请求、字节量，每 N 秒刷新
- **会话统计**：Dashboard Total↑↓ 重启归零；`stats.json` 保存历史累计

## Requirements

- Python 3.8+
- `pip install aiohttp pyyaml`

## Quick Start

```bash
# 1. 安装依赖
pip install aiohttp pyyaml

# 2. 复制配置并修改
cp config.example.yaml config.yaml
# 编辑 config.yaml，修改 username 和 password

# 3. 启动
python proxy_server.py
```

浏览器配置：HTTP 代理 → 服务器 IP → 端口（默认 8080）→ 启用认证。

## CLI

```bash
python proxy_server.py --host 127.0.0.1 --port 8888 --user admin --passwd secret --debug
```

| 参数 | 说明 |
|------|------|
| `-c, --config` | 配置文件路径 |
| `--host` | 监听地址 |
| `--port` | 监听端口 |
| `--user` | 认证用户名 |
| `--passwd` | 认证密码 |
| `--no-auth` | 禁用认证 |
| `--debug` | DEBUG 日志 |

## Config

完整配置见 `config.example.yaml`。关键参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `port` | 8080 | 监听端口 |
| `auth_enabled` | true | 启用 Basic 认证 |
| `max_connections` | 500 | 最大并发连接数 |
| `max_body_size` | 10MB | 请求体上限 |
| `tunnel_idle_timeout` | 180s | 隧道闲置关闭 |
| `max_tunnel_lifetime` | 300s | 隧道最长存活 |
| `max_tunnel_lifetime_download` | 7200s | 下载隧道超时 |
| `download_hosts` | `*.github.com` 等 | 自动匹配下载主机 |
| `slow_request_threshold` | 5.0s | 慢请求告警阈值 |
| `stats_interval` | 60s | 统计日志/快照间隔 |
| `display_interval` | 5s | 终端 Dashboard 刷新间隔（0=传统日志） |
| `rate_limit_enabled` | false | 启用每 IP 频率限制 |
| `rate_limit_per_minute` | 300 | 每 IP 每分钟最大请求数 |
| `dns_cache_ttl` | 300s | 直连 CONNECT 隧道 DNS 缓存 TTL |
| `drain_timeout` | 30s | 单次客户端 drain 超时；防止慢客户端导致永久挂起 |
| `max_request_line_size` | 16384 | 请求 URL 最大长度；超长返回 414 URI Too Long |

## Dashboard

当 `display_interval > 0` 时，终端进入实时 Dashboard 模式，定时刷新显示代理状态：

```
+========================================================================================================================+
| Proxy 0.0.0.0:8080  |  Active:18  TUN:9  DONE:5  UP:3h49m  |  Total U 2.1 MB D 88.6 MB                                 |
+========================================================================================================================+
| 192.168.1.100   TUN 36m51s       UP:  4.7 KB  DOWN:  4.4 KB                                                            |
| 192.168.1.100   TUN 12m46s       UP:  3.5 KB  DOWN:  5.5 KB                                                            |
| 192.168.1.100   HTTP x2          UP:  3.2 KB  DOWN:  2.6 KB  0m51s                                                     |
+========================================================================================================================+
```

各行含义：

| 列 | 说明 |
|----|------|
| **IP** | 客户端地址 |
| **Mode** | `TUN {时长}` / `HTTP x{N}` / `IDLE` |
| **UP/DOWN** | 该连接的上下行字节 |
| **Duration** | 连接持续时间（隧道模式显示隧道时长） |

表头：

| 指标 | 说明 |
|------|------|
| **Active** | 当前活跃连接数 |
| **TUN** | 隧道数 |
| **DONE** | 已断开的连接总数 |
| **UP** | 运行时长 |
| **Total↑↓** | 本次会话的上下行总量（**重启归零**） |

## Stats

统计信息每 `stats_interval`（默认 60s）输出到日志并保存到 `stats.json`（持久化跨重启）。可通过 HTTP 访问（需认证）：

```
curl -u user:pass http://proxy-stats/
```

输出结构：

```
total:       自首次启动以来的累计值（持久化）
last_period: 上个统计周期的快照（仅内存）
```

Dashboard 的 `Total↑↓` 显示的是**本次会话**的字节量（`session_bytes_sent/received`），与 `total` 不同。

JSON 示例：

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
├── proxy_server.py          # 主程序
├── config.example.yaml      # 配置示例
├── requirements.txt
├── LICENSE
├── README.md
├── scripts/                 # 启动 / 安装脚本
└── tests/                   # 测试工具
    ├── stress_test.py       # 压力测试
    ├── test_proxy.py        # 功能测试
    └── test_auth.py         # 认证测试
```

## Architecture

```
Client ──TCP──> asyncio Server ──aiohttp──> Target (HTTP)
                (handle_client)  ──Tunnel──> Target (HTTPS/CONNECT)
```

- **HTTP 请求**：解析 URL，通过 aiohttp 转发并重试/退避，流式传输响应体；上游缺 Content-Length 时自动重新分块
- **CONNECT 隧道**：解析全部地址，逐一尝试（每地址 10s 连接超时）；双向 TCP 隧道，闲置超时 + 最长存活 + 每地址连接超时
- **认证**：HTTP Basic，`hmac.compare_digest` 时序安全比对；拦截 407 后保活循环退出
- **频率限制**：每 IP 滑动窗口（60s），可配置最大请求数/分钟；超限返回 429
- **统计**：StatsCollector 累计 + 滑动窗口 + JSON 持久化（原子写入）
- **Dashboard**：`_active_connections` 字典追踪每连接状态，`_active_display` 定时渲染；连接关闭时自动清理死连接

## License

MIT
