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

.PHONY: worker
worker: ## Run the research worker (needs `make up` for Postgres and Redis)
	cd $(API) && uv run python -m app.workers.runner

.PHONY: api-lint
api-lint: ## Format check, lint and typecheck the API
	cd $(API) && uv run ruff format --check .
	cd $(API) && uv run ruff check .
	cd $(API) && uv run mypy app

# Test workers. The suite is a mix of CPU-bound imports and Postgres round
# trips, and each worker provisions a database of its own, so this trades memory
# for wall clock. Four measured best on a 12-thread laptop; `make api-test
# WORKERS=0` runs it serially, which is what to do when a failure needs reading.
WORKERS ?= 4

.PHONY: api-test
api-test: ## Run the API test suite in parallel (WORKERS=0 for serial)
	cd $(API) && uv run pytest $(if $(filter 0,$(WORKERS)),,-n $(WORKERS))

# The fifteen situations Phase 19 requires the system to survive, each driven
# through the whole stack. Part of `api-test`; named separately because it is
# the suite to run when a change touches the graph, the worker or the tools,
# and the one to read when asking what this system is claimed to withstand.
.PHONY: test-scenarios
test-scenarios: ## Run the end-to-end research scenarios
	cd $(API) && uv run pytest -m scenario $(if $(filter 0,$(WORKERS)),,-n $(WORKERS))

# Migrations form two branches: `core` (relational) and `vector` (needs
# pgvector). See apps/api/migrations/versions/0003_document_ingestion.py.
HEAD ?= core@head

.PHONY: migrate
migrate: ## Apply database migrations (both branches)
	cd $(API) && uv run alembic upgrade heads

.PHONY: migration
migration: ## Autogenerate a migration: make migration m="add x" [HEAD=vector@head]
	cd $(API) && uv run alembic revision --autogenerate --head $(HEAD) -m "$(m)"

.PHONY: migrate-check
migrate-check: ## Verify migrations are reversible against a throwaway database
	cd $(API) && uv run python scripts/check_migrations.py

.PHONY: benchmark-retrieval
benchmark-retrieval: ## Measure the retrieval strategies and the chunk size (ADR 0013)
	cd $(API) && uv run python scripts/with_test_db.py uv run python scripts/benchmark_retrieval.py

# Every case is a real research run against real providers, so this spends
# real money and needs Postgres and credentials. It exits non-zero when a
# gated metric regresses, which is what CI gates on (Phase 18).
.PHONY: evaluate
evaluate: ## Run the evaluation dataset against this build
	cd $(API) && uv run python -m app.evaluations $(ARGS)

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
