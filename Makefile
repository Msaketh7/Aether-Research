# Aether Research - developer entry points.
# `make help` lists everything. Targets fail loudly rather than silently.

SHELL := /bin/bash
.DEFAULT_GOAL := help

WEB := @aether/web
API := apps/api

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------
.PHONY: install
install: ## Install all JS workspace dependencies
	npm install

.PHONY: env
env: ## Create .env from .env.example if absent
	@test -f .env || (cp .env.example .env && echo "created .env")

# --- backend ---------------------------------------------------------------
.PHONY: api-install
api-install: ## Create the API virtualenv and install locked dependencies
	cd $(API) && uv sync

.PHONY: api
api: ## Run the FastAPI service (needs `make up` for Postgres and Redis)
	cd $(API) && uv run uvicorn app.main:app --reload --port 8000

.PHONY: api-lint
api-lint: ## Format check, lint and typecheck the API
	cd $(API) && uv run ruff format --check .
	cd $(API) && uv run ruff check .
	cd $(API) && uv run mypy app

.PHONY: api-test
api-test: ## Run the API test suite
	cd $(API) && uv run pytest

.PHONY: migrate
migrate: ## Apply database migrations
	cd $(API) && uv run alembic upgrade head

.PHONY: migration
migration: ## Autogenerate a migration from the models: make migration m="add x"
	cd $(API) && uv run alembic revision --autogenerate -m "$(m)"

.PHONY: migrate-check
migrate-check: ## Verify migrations are reversible against a throwaway database
	cd $(API) && uv run python scripts/check_migrations.py

# --- frontend --------------------------------------------------------------
.PHONY: dev
dev: ## Run the Next.js app in development (mock API mode)
	npm run dev --workspace $(WEB)

.PHONY: build
build: ## Production build of the frontend
	npm run build --workspace $(WEB)

.PHONY: lint
lint: ## Lint every workspace
	npm run lint --workspaces --if-present

.PHONY: typecheck
typecheck: ## Typecheck every workspace
	npm run typecheck --workspaces --if-present

.PHONY: format
format: ## Apply Prettier formatting
	npm run format

# --- tests -----------------------------------------------------------------
.PHONY: test
test: ## Unit tests (all workspaces)
	npm run test --workspaces --if-present

.PHONY: test-e2e
test-e2e: ## Playwright end-to-end smoke tests
	npm run test:e2e --workspace $(WEB)

.PHONY: ci
ci: format-check lint typecheck test api-lint api-test migrate-check ## Everything a pull request must pass

.PHONY: format-check
format-check:
	npm run format:check

# --- infrastructure (local) ------------------------------------------------
.PHONY: up
up: ## Start local infrastructure (Postgres, Redis, MinIO, Prometheus, Grafana)
	docker compose up -d

.PHONY: down
down: ## Stop local infrastructure
	docker compose down

.PHONY: logs
logs: ## Tail local infrastructure logs
	docker compose logs -f

.PHONY: clean
clean: ## Remove build output and caches
	rm -rf apps/web/.next apps/web/coverage apps/web/playwright-report apps/web/test-results
