# Kubernetes Deployment Guide

This directory contains Kubernetes manifests for deploying tinyproxy-ng.

## Prerequisites

- Kubernetes cluster (v1.19+)
- kubectl configured to access your cluster
- Docker image built and pushed to a registry (or loaded locally)

## Quick Deploy

1. **Build and push Docker image:**
```bash
# Build image
docker build -t tinyproxy-ng:latest ..

# Tag for your registry (example)
docker tag tinyproxy-ng:latest your-registry.com/tinyproxy-ng:latest

# Push to registry
docker push your-registry.com/tinyproxy-ng:latest
```

2. **Update credentials:**
```bash
# Edit the secret in deployment.yaml or create from command line:
kubectl create secret generic tinyproxy-credentials \
  --from-literal=username=your_username \
  --from-literal=password=your_password \
  -n tinyproxy
```

3. **Deploy:**
```bash
kubectl apply -f deployment.yaml
```

4. **Verify deployment:**
```bash
# Check pods
kubectl get pods -n tinyproxy

# Check services
kubectl get svc -n tinyproxy

# View logs
kubectl logs -f deployment/tinyproxy-ng -n tinyproxy
```

## Configuration

### Environment Variables

The deployment uses environment variables for configuration. You can modify the `ConfigMap` or override with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_HOST` | `0.0.0.0` | Listen address |
| `PROXY_PORT` | `26128` | Listen port |
| `AUTH_ENABLED` | `true` | Enable authentication |
| `USERNAME` | (from secret) | Auth username |
| `PASSWORD` | (from secret) | Auth password |
| `LOG_LEVEL` | `INFO` | Log level |
| `MAX_CONNECTIONS` | `500` | Max concurrent connections |

### Scaling

The deployment is configured with 2 replicas by default. Scale up/down:

```bash
# Scale to 3 replicas
kubectl scale deployment tinyproxy-ng --replicas=3 -n tinyproxy

# Or use Horizontal Pod Autoscaler (HPA)
kubectl autoscale deployment tinyproxy-ng --min=2 --max=10 --cpu-percent=70 -n tinyproxy
```

### Access the Service

The service is exposed as `LoadBalancer` by default. For local testing, you can use:

```bash
# Port forward
kubectl port-forward svc/tinyproxy-ng 26128:26128 -n tinyproxy

# Then access via localhost:26128
```

### Monitoring

Check the health of your deployment:

```bash
# Get pod details
kubectl describe pods -n tinyproxy

# Check resource usage
kubectl top pods -n tinyproxy

# View events
kubectl get events -n tinyproxy --sort-by='.lastTimestamp'
```

## Security Considerations

1. **Secrets Management**: The credentials are stored in Kubernetes Secrets. Consider using external secret management (HashiCorp Vault, AWS Secrets Manager, etc.) for production.

2. **Network Policy**: A basic NetworkPolicy is included that:
   - Allows ingress on port 26128 from any source
   - Allows egress to ports 80 and 443 only
   - Customize as needed for your environment

3. **RBAC**: Consider adding Role-Based Access Control for production deployments.

4. **TLS/SSL**: For production, consider adding TLS termination at the ingress level.

## Troubleshooting

### Pod won't start
```bash
# Check pod events
kubectl describe pod <pod-name> -n tinyproxy

# Check logs
kubectl logs <pod-name> -n tinyproxy
```

### Authentication issues
```bash
# Verify secret exists
kubectl get secret tinyproxy-credentials -n tinyproxy -o yaml

# Recreate secret if needed
kubectl delete secret tinyproxy-credentials -n tinyproxy
kubectl create secret generic tinyproxy-credentials \
  --from-literal=username=your_username \
  --from-literal=password=your_password \
  -n tinyproxy

# Restart pods
kubectl rollout restart deployment tinyproxy-ng -n tinyproxy
```

### Connection issues
```bash
# Check service
kubectl get svc tinyproxy-ng -n tinyproxy

# Check endpoints
kubectl get endpoints tinyproxy-ng -n tinyproxy

# Test connectivity
kubectl run -it --rm debug --image=busybox --restart=Never -- nc -zv tinyproxy-ng.tinyproxy.svc.cluster.local 26128
```

## Cleanup

```bash
# Delete all resources
kubectl delete -f deployment.yaml

# Delete namespace (removes all resources in the namespace)
kubectl delete namespace tinyproxy
```
