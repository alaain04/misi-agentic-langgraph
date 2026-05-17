.PHONY: help setup lint format test \
        deps-up deps-down deps-logs \
        dev-backend dev-workers dev-frontend \
        build

DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
setup: ## Install all dependencies
	$(MAKE) -C backend setup
	$(MAKE) -C workers setup
	$(MAKE) -C frontend install

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint: ## Lint all components
	$(MAKE) -C backend lint
	$(MAKE) -C workers lint
	$(MAKE) -C frontend lint

format: ## Format all components
	$(MAKE) -C backend format
	$(MAKE) -C workers format
	$(MAKE) -C frontend format

test: ## Test all components
	$(MAKE) -C backend test
	$(MAKE) -C workers test

# ---------------------------------------------------------------------------
# Infrastructure — shared deps for full-stack runs
# ---------------------------------------------------------------------------
deps-up: ## Start all infrastructure (mongo, redis, nats)
	$(DOCKER_COMPOSE) up -d

deps-down: ## Stop all infrastructure
	$(DOCKER_COMPOSE) down

deps-logs: ## Follow infrastructure logs
	$(DOCKER_COMPOSE) logs -f

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------
dev-backend: ## Start backend dev server
	$(MAKE) -C backend dev

dev-workers: ## Start workers dev server
	$(MAKE) -C workers dev

dev-frontend: ## Start frontend dev server
	$(MAKE) -C frontend dev

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
build: ## Build all components for production
	$(MAKE) -C frontend build
