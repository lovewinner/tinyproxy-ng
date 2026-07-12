#!/bin/bash
# Build Docker image for tinyproxy-ng

IMAGE_NAME="tinyproxy-ng"
IMAGE_TAG="latest"

echo "Building Docker image: ${IMAGE_NAME}:${IMAGE_TAG}"
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

if [ $? -eq 0 ]; then
    echo ""
    echo "Build successful!"
    echo ""
    echo "To run the container:"
    echo "  docker run -d -p 26128:26128 --name tinyproxy-ng ${IMAGE_NAME}:${IMAGE_TAG}"
    echo ""
    echo "Or use docker-compose:"
    echo "  docker-compose up -d"
else
    echo ""
    echo "Build failed! Check the error messages above."
    exit 1
fi
