include .env

.PHONY: help \
        restart-setup setup check-python create-venv install-deps install install-dev hooks-install sync-hooks \
        lint format typecheck audit sast check \
        test test-cov \
        run dev \
        docker-build docker-build-dev docker-up docker-down docker-logs \
        migrate migrate-create migrate-rollback \
        sonar \
        ci-validate \
        clean

PYTHON_MIN_VERSION=3.12

# Detects if 'docker-compose' (v1) or 'docker compose' (v2) exists
DOCKER_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

# Default target
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Deletes the virtual environment and reruns the setup process
.PHONY: restart-setup
restart-setup:
	@rm -rf venv
	@make setup

# Sets up the project locally - credits: @cnsfeir
.PHONY: setup
setup:
	@echo "\n🚀 Setting up the project!"
	@make check-python
	@make create-venv
	@make install-deps
	@echo "\n🎉 All set! Happy coding\n"

# Check if the required version of Python is installed
.PHONY: check-python
check-python:
	@echo "\n🔍 Checking Python version..."
	@python --version >/dev/null 2>&1 || (echo "❌ ERROR: Python not found\n" && exit 1)
	@python -c "\
import sys; \
req = tuple(int(x) for x in '$(PYTHON_MIN_VERSION)'.split('.')); \
actual = sys.version_info[:2]; \
sys.exit(0) if actual >= req else sys.exit(1)" \
		|| (echo "❌ ERROR: Python $(PYTHON_MIN_VERSION)+ is required (found $$(python --version))\n" && exit 1)
	@echo "✅ Using $$(python --version)"

.PHONY: create-venv
create-venv:
	@echo "\n🐍 Creating virtual environment..."
	@python -m venv venv

# Sets up the virtual environment and installs the required packages
.PHONY: install-deps
install-deps:
	@echo "\n📦 Installing dependencies...\n"
	@if [ -d "venv" ]; then \
		venv/bin/python -m pip install --upgrade pip setuptools; \
		venv/bin/python -m pip install -r requirements-dev.txt; \
		venv/bin/python -m pre_commit install; \
		venv/bin/python -m pre_commit install --hook-type commit-msg; \
	else \
		pip install --upgrade pip setuptools; \
		pip install -r requirements.txt; \
	fi

install: ## Install production dependencies
	pip install -r requirements.txt

install-dev: ## Install all dependencies (incl. dev)
	venv/bin/pip install -r requirements-dev.txt
	venv/bin/pre-commit install

hooks-install: ## Install pre-commit hooks
	venv/bin/pre-commit install
	venv/bin/pre-commit install --hook-type commit-msg

sync-hooks: ## Sync .pre-commit-config.yaml revisions with versions in requirements-dev.txt
	@venv/bin/python -c "\
import re; \
txt = open('requirements-dev.txt').read(); \
cfg = open('.pre-commit-config.yaml').read(); \
get = lambda p: re.search(rf'^{p}==([\S]+)', txt, re.M).group(1); \
ruff=get('ruff'); mypy=get('mypy'); bandit=get('bandit'); \
cfg = re.sub(r'(astral-sh/ruff-pre-commit\n\s+rev: )v[\d.]+', r'\g<1>v' + ruff, cfg); \
cfg = re.sub(r'(mirrors-mypy\n\s+rev: )v[\d.]+', r'\g<1>v' + mypy, cfg); \
cfg = re.sub(r'(PyCQA/bandit\n\s+rev: )[\d.]+', r'\g<1>' + bandit, cfg); \
open('.pre-commit-config.yaml', 'w').write(cfg); \
print(f'Synced: ruff=v{ruff}  mypy=v{mypy}  bandit={bandit}'); \
print('Run: pre-commit clean && pre-commit install') \
"

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint: ## Run ruff linter
	venv/bin/ruff check .

format: ## Run ruff formatter
	venv/bin/ruff format .

typecheck: ## Run mypy type checker
	venv/bin/mypy .

audit: ## Run pip-audit (dependency vulnerability scan)
	venv/bin/pip-audit -r requirements.txt

sast: ## Run bandit (SAST security analysis)
	venv/bin/bandit -r . -c pyproject.toml -ll

check: lint typecheck ## Run all code quality checks

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test: ## Run tests
	venv/bin/pytest

test-cov: ## Run tests with coverage report
	venv/bin/pytest --cov=. --cov-report=html --cov-report=term-missing

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
run: ## Run the app in production mode
	venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

dev: ## Run the app in development mode (hot-reload)
	venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload --log-level debug

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
docker-build: ## Build production image (Dockerfile)
	docker build -t python-service:latest .

docker-build-dev: ## Build development image (Dockerfile.dev)
	docker build -f Dockerfile.dev -t python-service:dev .

docker-up: ## Start all services (API + DB + Redis + LocalStack + NATS)
	$(DOCKER_COMPOSE) up -d

docker-down: ## Stop all services
	$(DOCKER_COMPOSE) down

docker-logs: ## Follow logs from all services
	$(DOCKER_COMPOSE) logs -f

# ---------------------------------------------------------------------------
# Database — requires PostgreSQL running via `make docker-up`
# ---------------------------------------------------------------------------
migrate: ## Run Alembic migrations
	PYTHONPATH=$(PWD) venv/bin/alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create MSG="description")
	PYTHONPATH=$(PWD) venv/bin/alembic revision --autogenerate -m "$(MSG)"

migrate-rollback: ## Rollback last migration
	PYTHONPATH=$(PWD) venv/bin/alembic downgrade -1

# ---------------------------------------------------------------------------
# SonarQube
# ---------------------------------------------------------------------------
SONAR_PROJECT_KEY  := ##REPOSITORY_NAME##

sonar: ## Run SonarQube analysis against the remote server (requires SONAR_HOST_URL and SONAR_TOKEN)
	@[ -n "$(SONAR_HOST_URL)" ] || (echo "❌ SONAR_HOST_URL is not set" && exit 1)
	@[ -n "$(SONAR_TOKEN)" ]    || (echo "❌ SONAR_TOKEN is not set" && exit 1)
	@echo "📊 Generating coverage report..."
	@mkdir -p coverage
	@venv/bin/pytest --cov=. --cov-report=xml:coverage/coverage.xml -q 2>/dev/null || true
	@echo "🔬 Running sonar-scanner against $(SONAR_HOST_URL)..."
	@docker run --rm \
		-v "$(PWD):/usr/src" \
		-e SONAR_USER_HOME=/tmp/.sonar \
		sonarsource/sonar-scanner-cli:latest \
		-Dsonar.projectKey=$(SONAR_PROJECT_KEY) \
		-Dsonar.host.url=$(SONAR_HOST_URL) \
		-Dsonar.token=$(SONAR_TOKEN)
	@echo "\n✅ Done. View results at: $(SONAR_HOST_URL)/dashboard?id=$(SONAR_PROJECT_KEY)"

# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------
ci-validate: install-dev check test-cov audit sast docker-build ## Run full CI validation pipeline locally
	@echo "✅ All CI validation steps completed!"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean: ## Remove Python cache files and coverage reports
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml coverage/
