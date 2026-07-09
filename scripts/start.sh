#!/bin/bash
# HTTP/HTTPS Proxy Server startup script (Linux/macOS)

echo "========================================"
echo "   HTTP/HTTPS Proxy Server"
echo "========================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found! Please install Python 3.8+"
    exit 1
fi

echo "[1/3] Python check: $(python3 --version)"

# Install dependencies (if needed)
echo "[2/3] Checking dependencies..."
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate 2>/dev/null || true
pip install -r requirements.txt > /dev/null 2>&1
echo "Dependency check complete"

# Check configuration
echo "[3/3] Checking configuration..."
if [ ! -f "config.yaml" ]; then
    echo "[WARN] config.yaml not found, using default configuration"
    echo "Copy config.example.yaml to config.yaml and edit it"
fi

echo ""
echo "========================================"
echo "Starting proxy server..."
echo "Press Ctrl+C to stop"
echo "========================================"
echo ""

python3 proxy_server.py