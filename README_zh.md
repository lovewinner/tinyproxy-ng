# tinyproxy-ng

> **中文** | [English](README.md)

基于 Python asyncio + aiohttp 的高性能 HTTP/HTTPS 代理服务器，支持认证、CONNECT 隧道、上游代理链、统计监控与自动恢复。

## Feature

- HTTP/HTTPS 代理（CONNECT 隧道）
- HTTP Basic 认证（Proxy-Authorization）
- 上游代理链（HTTP 上游：转发 + CONNECT 均支持；SOCKS5 上游：仅 HTTP 转发支持，需安装 aiohttp-socks）
- 全局连接池复用 + DNS 缓存 + TCP 参数调优
- 隧道闲置超时（180s）+ 最长存活（可配置）
- 下载主机自动匹配延长超时（download_hosts）
- 慢请求检测 + 请求级追踪 ID
- 日志轮转（RotatingFileHandler）
- 内置统计系统（JSON + HTTP 端点）
- 指数退避重试 + 信号量并发控制
- 优雅关闭（5s 等待 + 强制清理）
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

浏览器配置：HTTP 代理 → 服务器 IP → 端口（默认 26128）→ 启用认证。

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
| `port` | 26128 | 监听端口 |
| `auth_enabled` | true | 启用 Basic 认证 |
| `max_connections` | 500 | 最大并发连接数 |
| `max_body_size` | 10MB | 请求体上限 |
| `tunnel_idle_timeout` | 180s | 隧道闲置关闭 |
| `max_tunnel_lifetime` | 300s | 隧道最长存活 |
| `max_tunnel_lifetime_download` | 7200s | 下载隧道超时 |
| `download_hosts` | `*.github.com` 等 | 自动匹配下载主机 |
| `slow_request_threshold` | 5.0s | 慢请求告警阈值 |
| `stats_interval` | 3600s | 统计日志/快照间隔 |
| `display_interval` | 2s | 终端 Dashboard 刷新间隔（0=传统日志） |

## Dashboard

当 `display_interval > 0` 时，终端进入实时 Dashboard 模式，定时刷新显示代理状态：

```
+========================================================================================================================+
| Proxy 0.0.0.0:26128  |  Active:18  TUN:9  DONE:5  UP:3h49m  |  Total↑2.1 MB ↓88.6 MB                                 |
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

统计信息每 `stats_interval`（默认 3600s）输出到日志并保存到 `stats.json`（持久化跨重启）。可通过 HTTP 访问（需认证）：

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

- **HTTP 请求**：代理解析 URL，通过 aiohttp 转发，流式传输响应体
- **CONNECT 隧道**：建立 TCP 隧道双向透传，支持闲置超时 + 最长存活双层保护
- **认证**：HTTP Basic，拦截 407 后保活循环退出
- **统计**：StatsCollector 累计 + 滑动窗口 + JSON 持久化
- **Dashboard**：`_active_connections` 字典追踪每连接状态，`_active_display` 定时渲染

## License

MIT
