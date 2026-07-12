# Multi-stage build for smaller final image
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Create non-root user for security
RUN useradd -m -u 1000 proxyuser && \
    mkdir -p /app/logs && \
    chown -R proxyuser:proxyuser /app

# Copy application code
COPY --chown=proxyuser:proxyuser . .

# Switch to non-root user
USER proxyuser

# Expose default port
EXPOSE 26128

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(5); \
    result=s.connect_ex(('127.0.0.1', 26128)); s.close(); exit(0 if result==0 else 1)"

# Default configuration
ENV PROXY_HOST=0.0.0.0 \
    PROXY_PORT=26128 \
    AUTH_ENABLED=true \
    LOG_LEVEL=INFO

# Run the proxy server
CMD ["python", "proxy_server.py", "-c", "config.yaml"]
