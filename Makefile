# Algotrader — developer convenience targets.
# Conventions: all targets are idempotent; long-running targets print what they do first.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON ?= python3.11
POETRY ?= poetry
COMPOSE ?= docker compose

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------- Install / env ----------------

.PHONY: install
install: ## Install Python deps via Poetry
	$(POETRY) install --with dev

.PHONY: lock
lock: ## Refresh poetry.lock
	$(POETRY) lock --no-update

# ---------------- Local stack ----------------

.PHONY: up
up: ## Bring up local stack (redis, timescaledb)
	$(COMPOSE) -f deploy/docker/docker-compose.yml up -d
	@echo "Stack up. Redis: localhost:6379  TimescaleDB: localhost:5432"

.PHONY: down
down: ## Tear down local stack
	$(COMPOSE) -f deploy/docker/docker-compose.yml down

.PHONY: logs
logs: ## Tail local stack logs
	$(COMPOSE) -f deploy/docker/docker-compose.yml logs -f --tail=200

.PHONY: db-shell
db-shell: ## Open psql against the local TimescaleDB
	$(COMPOSE) -f deploy/docker/docker-compose.yml exec timescaledb psql -U algotrader -d algotrader

.PHONY: redis-shell
redis-shell: ## Open redis-cli against local redis
	$(COMPOSE) -f deploy/docker/docker-compose.yml exec redis redis-cli

.PHONY: migrate
migrate: ## Apply database migrations
	$(POETRY) run alembic -c persistence/alembic.ini upgrade head

# ---------------- Quality gates ----------------

.PHONY: lint
lint: ## Run ruff
	$(POETRY) run ruff check .

.PHONY: lint-fix
lint-fix: ## Auto-fix lint issues
	$(POETRY) run ruff check --fix .

.PHONY: format
format: ## Format code with black
	$(POETRY) run black .

.PHONY: format-check
format-check: ## Check formatting without changing files
	$(POETRY) run black --check .

.PHONY: typecheck
typecheck: ## Run mypy
	$(POETRY) run mypy core data features strategies risk execution monitoring persistence backtest

.PHONY: test
test: ## Run unit tests
	$(POETRY) run pytest tests/unit -m "not integration"

.PHONY: test-integration
test-integration: ## Run integration tests (requires `make up`)
	$(POETRY) run pytest tests/integration -m integration

.PHONY: test-all
test-all: ## Run full test suite
	$(POETRY) run pytest

.PHONY: coverage
coverage: ## Run tests with coverage
	$(POETRY) run pytest --cov --cov-report=term-missing --cov-report=html

.PHONY: check
check: lint format-check typecheck test ## Run all quality gates (fast)

# ---------------- House keeping ----------------

.PHONY: clean
clean: ## Remove caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
