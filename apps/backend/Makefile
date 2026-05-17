include .env

.PHONY: help setup lint format typecheck test test-cov run dev \
        docker-build docker-build-dev docker-up docker-down docker-logs clean

DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
setup: ## Install all dependencies (including dev)
	uv sync

install: ## Install production dependencies only
	uv sync --no-dev

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint: ## Run ruff linter
	uv run ruff check .

format: ## Run ruff formatter
	uv run ruff format .

typecheck: ## Run mypy type checker
	uv run mypy .

check: lint typecheck ## Run all code quality checks

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage report
	uv run pytest --cov=. --cov-report=html --cov-report=term-missing

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
run: ## Run the app in production mode
	uv run uvicorn src.main:app --host 0.0.0.0 --port 8001

dev: ## Run the app in development mode (hot-reload)
	uv run uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload --log-level debug

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
docker-build: ## Build production image
	docker build -t entity-worker:latest .

docker-build-dev: ## Build development image
	docker build -f Dockerfile.dev -t entity-worker:dev .

docker-up: ## Start all services
	$(DOCKER_COMPOSE) up -d

docker-down: ## Stop all services
	$(DOCKER_COMPOSE) down

docker-logs: ## Follow logs from all services
	$(DOCKER_COMPOSE) logs -f

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean: ## Remove Python cache files and coverage reports
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml coverage/
