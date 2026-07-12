# Docker 部署指南

本文档详细介绍如何使用 Docker 部署 tinyproxy-ng 代理服务器。

## 目录

- [快速开始](#快速开始)
- [Docker 基础操作](#docker-基础操作)
- [Docker Compose 部署](#docker-compose-部署)
- [Kubernetes 部署](#kubernetes-部署)
- [配置说明](#配置说明)
- [故障排查](#故障排查)

## 快速开始

### 使用快速启动脚本

**Linux/macOS:**
```bash
chmod +x quickstart.sh
./quickstart.sh
```

**Windows:**
```cmd
quickstart.bat
```

脚本会自动检测是否安装 Docker，并引导你完成配置和启动。

### 手动快速启动

1. **构建镜像：**
```bash
docker build -t tinyproxy-ng:latest .
```

2. **运行容器：**
```bash
docker run -d \
  --name tinyproxy-ng \
  -p 26128:26128 \
  -e USERNAME=your_username \
  -e PASSWORD=your_password \
  tinyproxy-ng:latest
```

3. **测试连接：**
```bash
curl -x http://your_username:your_password@localhost:26128 https://www.google.com
```

## Docker 基础操作

### 构建镜像

```bash
# 基础构建
docker build -t tinyproxy-ng:latest .

# 带标签构建
docker build -t tinyproxy-ng:v1.0.0 .

# 使用代理构建（国内网络）
docker build --build-arg HTTP_PROXY=http://proxy:port -t tinyproxy-ng:latest .
```

### 运行容器

```bash
# 最小配置
docker run -d --name tinyproxy-ng -p 26128:26128 tinyproxy-ng:latest

# 完整配置
docker run -d \
  --name tinyproxy-ng \
  -p 26128:26128 \
  -e AUTH_ENABLED=true \
  -e USERNAME=your_username \
  -e PASSWORD=your_password \
  -e LOG_LEVEL=INFO \
  -e MAX_CONNECTIONS=500 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/stats.json:/app/stats.json \
  -v $(pwd)/logs:/app/logs \
  --restart unless-stopped \
  tinyproxy-ng:latest
```

### 管理容器

```bash
# 查看日志
docker logs -f tinyproxy-ng

# 进入容器
docker exec -it tinyproxy-ng /bin/bash

# 重启容器
docker restart tinyproxy-ng

# 停止容器
docker stop tinyproxy-ng

# 删除容器
docker rm tinyproxy-ng

# 查看容器状态
docker ps -a | grep tinyproxy-ng

# 查看容器资源使用
docker stats tinyproxy-ng
```

### 健康检查

容器内置健康检查机制，每 30 秒检查一次：

```bash
# 查看健康状态
docker inspect --format='{{.State.Health.Status}}' tinyproxy-ng

# 查看健康检查历史
docker inspect --format='{{json .State.Health}}' tinyproxy-ng | jq
```

## Docker Compose 部署

### 开发环境

使用 `docker-compose.yml`：

```bash
# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down

# 重启
docker-compose restart
```

### 生产环境

使用 `docker-compose.prod.yml`（包含监控）：

```bash
# 启动所有服务（包括监控）
docker-compose -f docker-compose.prod.yml --profile monitoring up -d

# 只启动代理服务
docker-compose -f docker-compose.prod.yml up -d

# 查看服务状态
docker-compose -f docker-compose.prod.yml ps

# 停止所有服务
docker-compose -f docker-compose.prod.yml --profile monitoring down
```

### 访问监控服务

启用监控后，可以访问：

- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)

## Kubernetes 部署

详细说明请参考 [k8s/README.md](k8s/README.md)

### 快速部署

```bash
# 部署到 Kubernetes
kubectl apply -f k8s/deployment.yaml

# 查看状态
kubectl get pods -n tinyproxy
kubectl get svc -n tinyproxy

# 查看日志
kubectl logs -f deployment/tinyproxy-ng -n tinyproxy

# 删除部署
kubectl delete -f k8s/deployment.yaml
```

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `PROXY_HOST` | `0.0.0.0` | 监听地址 |
| `PROXY_PORT` | `26128` | 监听端口 |
| `AUTH_ENABLED` | `true` | 是否启用认证 |
| `USERNAME` | `lovewinner` | 认证用户名 |
| `PASSWORD` | `ncepu@6868` | 认证密码 |
| `LOG_LEVEL` | `INFO` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `MAX_CONNECTIONS` | `500` | 最大并发连接数 |
| `UPSTREAM_PROXY_HTTP` | (无) | HTTP 上游代理 URL |
| `UPSTREAM_PROXY_HTTPS` | (无) | HTTPS 上游代理 URL |

### 配置文件

可以挂载自定义配置文件：

```bash
docker run -d \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  tinyproxy-ng:latest
```

配置文件优先级：环境变量 > 配置文件 > 默认值

### 数据持久化

推荐持久化的数据：

```yaml
volumes:
  - ./config.yaml:/app/config.yaml:ro    # 配置文件（只读）
  - ./stats.json:/app/stats.json          # 统计数据
  - ./logs:/app/logs                      # 日志目录
```

## 故障排查

### 容器无法启动

```bash
# 查看详细日志
docker logs tinyproxy-ng

# 检查容器状态
docker inspect tinyproxy-ng

# 检查端口占用
netstat -tlnp | grep 26128
```

### 连接失败

```bash
# 检查容器是否运行
docker ps | grep tinyproxy-ng

# 检查端口映射
docker port tinyproxy-ng

# 测试端口连通性
telnet localhost 26128

# 检查防火墙
sudo ufw status  # Linux
netsh advfirewall show allprofiles  # Windows
```

### 认证失败

```bash
# 检查环境变量
docker exec tinyproxy-ng env | grep -E 'USERNAME|PASSWORD'

# 检查配置文件
docker exec tinyproxy-ng cat /app/config.yaml

# 查看认证日志
docker logs tinyproxy-ng | grep -i auth
```

### 性能问题

```bash
# 查看容器资源使用
docker stats tinyproxy-ng

# 调整资源限制
docker update --memory=512m --cpus=1.0 tinyproxy-ng

# 查看连接数
docker exec tinyproxy-ng netstat -an | grep ESTABLISHED | wc -l
```

### 日志问题

```bash
# 实时查看日志
docker logs -f --tail 100 tinyproxy-ng

# 导出日志
docker logs tinyproxy-ng > proxy.log 2>&1

# 查看日志文件
docker exec tinyproxy-ng ls -lh /app/logs/
docker exec tinyproxy-ng cat /app/logs/proxy.log
```

## 最佳实践

### 安全建议

1. **修改默认密码**：不要使用示例中的默认密码
2. **使用密钥管理**：生产环境建议使用 Docker Secrets 或 Kubernetes Secrets
3. **网络隔离**：使用 Docker 网络或防火墙限制访问
4. **定期更新**：及时更新基础镜像和依赖包

### 性能优化

1. **调整连接数**：根据服务器配置调整 `MAX_CONNECTIONS`
2. **启用缓存**：配置 DNS 缓存和连接池
3. **资源限制**：设置合理的 CPU 和内存限制

### 监控告警

1. **健康检查**：利用 Docker 内置健康检查
2. **日志收集**：使用 ELK 或其他日志系统
3. **指标监控**：集成 Prometheus/Grafana

## 常用命令速查

```bash
# 构建
docker build -t tinyproxy-ng:latest .

# 运行
docker run -d --name tinyproxy-ng -p 26128:26128 tinyproxy-ng:latest

# 停止
docker stop tinyproxy-ng

# 删除
docker rm tinyproxy-ng

# 日志
docker logs -f tinyproxy-ng

# 进入容器
docker exec -it tinyproxy-ng bash

# Compose 启动
docker-compose up -d

# Compose 停止
docker-compose down

# 查看状态
docker ps -a | grep tinyproxy-ng

# 资源监控
docker stats tinyproxy-ng
```

## 相关文档

- [README.md](README.md) - 项目主文档
- [k8s/README.md](k8s/README.md) - Kubernetes 部署文档
- [config.example.yaml](config.example.yaml) - 配置文件示例
