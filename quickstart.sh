#!/bin/bash
# Quick start script for tinyproxy-ng

set -e

echo "================================================"
echo "  Tinyproxy-ng Quick Start"
echo "================================================"
echo ""

# Check if Docker is installed
if command -v docker &> /dev/null; then
    echo "✓ Docker detected"
    USE_DOCKER=true
else
    echo "✗ Docker not found, will use native installation"
    USE_DOCKER=false
fi

if [ "$USE_DOCKER" = true ]; then
    echo ""
    echo "Starting with Docker..."
    echo ""
    
    # Check if .env exists
    if [ ! -f .env ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env
        echo ""
        echo "⚠️  Please edit .env file with your settings:"
        echo "   nano .env"
        echo ""
        read -p "Press Enter after editing .env to continue..."
    fi
    
    # Build and run
    echo "Building Docker image..."
    docker-compose build
    
    echo "Starting container..."
    docker-compose up -d
    
    echo ""
    echo "✓ Container started!"
    echo ""
    echo "Useful commands:"
    echo "  View logs:     docker-compose logs -f"
    echo "  Stop service:  docker-compose down"
    echo "  Restart:       docker-compose restart"
    echo ""
    echo "Proxy is now running on port 26128"
    echo "Test with: curl -x http://username:password@localhost:26128 https://example.com"
    
else
    echo ""
    echo "Starting native installation..."
    echo ""
    
    # Check Python version
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo "✓ Python version: $PYTHON_VERSION"
    
    # Install dependencies
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
    
    # Check config
    if [ ! -f config.yaml ]; then
        echo "Creating config.yaml from config.example.yaml..."
        cp config.example.yaml config.yaml
        echo ""
        echo "⚠️  Please edit config.yaml with your settings:"
        echo "   nano config.yaml"
        echo ""
        read -p "Press Enter after editing config.yaml to continue..."
    fi
    
    echo ""
    echo "Starting proxy server..."
    echo "Press Ctrl+C to stop"
    echo ""
    python3 proxy_server.py
fi
