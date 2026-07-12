.PHONY: help build run stop logs clean test docker-build docker-run docker-stop k8s-deploy k8s-undeploy

help:
	@echo "Tinyproxy-ng - Available commands:"
	@echo ""
	@echo "Local Development:"
	@echo "  make run           - Run proxy server locally"
	@echo "  make test          - Run tests"
	@echo "  make clean         - Clean generated files"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build  - Build Docker image"
	@echo "  make docker-run    - Run container in background"
	@echo "  make docker-stop   - Stop and remove container"
	@echo "  make docker-logs   - View container logs"
	@echo "  make docker-shell  - Open shell in container"
	@echo ""
	@echo "Docker Compose:"
	@echo "  make compose-up    - Start with docker-compose"
	@echo "  make compose-down  - Stop docker-compose"
	@echo "  make compose-logs  - View compose logs"
	@echo ""
	@echo "Kubernetes:"
	@echo "  make k8s-deploy    - Deploy to Kubernetes"
	@echo "  make k8s-undeploy  - Remove from Kubernetes"
	@echo "  make k8s-logs      - View Kubernetes logs"
	@echo ""

# Local development
run:
	python proxy_server.py

test:
	python -m pytest tests/ -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache

# Docker
docker-build:
	docker build -t tinyproxy-ng:latest .

docker-run:
	docker run -d \
		--name tinyproxy-ng \
		-p 26128:26128 \
		-e AUTH_ENABLED=true \
		-e USERNAME=lovewinner \
		-e PASSWORD=ncepu@6868 \
		tinyproxy-ng:latest

docker-stop:
	docker stop tinyproxy-ng || true
	docker rm tinyproxy-ng || true

docker-logs:
	docker logs -f tinyproxy-ng

docker-shell:
	docker exec -it tinyproxy-ng /bin/bash

# Docker Compose
compose-up:
	docker-compose up -d

compose-down:
	docker-compose down

compose-logs:
	docker-compose logs -f

compose-prod-up:
	docker-compose -f docker-compose.prod.yml --profile monitoring up -d

compose-prod-down:
	docker-compose -f docker-compose.prod.yml --profile monitoring down

# Kubernetes
k8s-deploy:
	kubectl apply -f k8s/deployment.yaml

k8s-undeploy:
	kubectl delete -f k8s/deployment.yaml

k8s-logs:
	kubectl logs -f deployment/tinyproxy-ng -n tinyproxy

k8s-status:
	kubectl get pods -n tinyproxy
	kubectl get svc -n tinyproxy
