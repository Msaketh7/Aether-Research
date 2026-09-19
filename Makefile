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

# Load testing (Phase 21). Two halves, because there are two ceilings.
#
# `loadtest` drives the research pipeline: the declared profiles from
# app/loadtest/profiles.py against a real worker, a real graph and real
# Postgres, with the model and the socket scripted. It provisions its own
# throwaway database and needs no credentials, so it runs anywhere.
#
# `loadtest-api` drives the HTTP surface with Locust, against an API that is
# already running (`make api`). Locust is fetched into a throwaway environment
# rather than added to the lockfile, the same way `make audit` fetches
# pip-audit: neither belongs in every developer's virtualenv.
.PHONY: loadtest
loadtest: ## Load test the research pipeline (provisions its own database)
	cd $(API) && uv run python scripts/with_test_db.py uv run python scripts/loadtest.py $(ARGS)

USERS ?= 20
SPAWN ?= 2
RUNTIME ?= 60s
HOST ?= http://localhost:8000

.PHONY: loadtest-api
loadtest-api: ## Load test the HTTP surface with Locust (needs `make api` running)
	mkdir -p data/loadtest
	cd $(API) && uv run --with locust locust -f loadtest/locustfile.py --headless --host $(HOST) --users $(USERS) --spawn-rate $(SPAWN) --run-time $(RUNTIME) --csv ../../data/loadtest/api $(ARGS)

# The same load against a stack this target stands up and throws away: a
# throwaway Postgres, a migrated schema and a uvicorn on a spare port. What to
# run when there is no deployment to point at, which is the usual case here.
.PHONY: loadtest-api-local
loadtest-api-local: ## Load test the HTTP surface against a disposable local stack
	cd $(API) && uv run python scripts/with_test_db.py bash scripts/loadtest_api.sh

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

# --- security --------------------------------------------------------------
# Advisory scanning of what is actually installed, on both sides. Neither is a
# guarantee - a database only contains what someone has reported - so this is
# "known vulnerable dependencies", not "no vulnerable dependencies".
#
# `npm audit` is capped at high on purpose: the transitive dev graph of a
# Next.js toolchain produces a steady trickle of low and moderate advisories
# that do not reach a production bundle, and a gate that fails every week is a
# gate people learn to skip. Container image scanning arrives with the images
# themselves, in Phase 23.
.PHONY: audit
audit: ## Scan both dependency trees for known vulnerabilities
	cd $(API) && uv run --with pip-audit pip-audit
	npm audit --audit-level=high

# The secret scan CI runs, for running before pushing rather than after.
# Needs gitleaks on the PATH; configuration, including the one allowance for
# .env.example, is in .gitleaks.toml.
.PHONY: secrets
secrets: ## Scan the working tree and its history for committed secrets
	gitleaks git --no-banner --redact --exit-code 1 .

.PHONY: format-check
format-check:
	npm run format:check

# --- infrastructure (local) ------------------------------------------------
# Two different questions, two targets. `up` brings up the dependencies and
# leaves the application on the host, which is how it is developed. `up-app`
# adds web, api and worker from the images in infra/docker/, which is how the
# images themselves get tested.
.PHONY: up
up: ## Start local infrastructure (Postgres, Redis, MinIO, Prometheus, Grafana)
	docker compose up -d

.PHONY: up-app
up-app: ## Start infrastructure plus web, api and worker from their images
	docker compose --profile app up -d --build

.PHONY: down
down: ## Stop local infrastructure
	docker compose --profile app down

.PHONY: logs
logs: ## Tail local infrastructure logs
	docker compose --profile app logs -f

# --- container images ------------------------------------------------------
# Built from the repository root: the web build needs the npm workspace and the
# API image must keep the apps/api path depth (see infra/docker/api.Dockerfile).
IMAGE_TAG ?= local

.PHONY: images
images: image-api image-web ## Build both container images

.PHONY: image-api
image-api: ## Build the API/worker image
	docker build -f infra/docker/api.Dockerfile -t aether-api:$(IMAGE_TAG) .

.PHONY: image-web
image-web: ## Build the web image
	docker build -f infra/docker/web.Dockerfile -t aether-web:$(IMAGE_TAG) .

# --- infrastructure (cloud) ------------------------------------------------
# Terraform is modular and environment-selected: one root, one .tfvars per
# environment, one state key per environment. ENV picks which.
ENV ?= staging
TF := terraform -chdir=infra/terraform

.PHONY: tf-fmt
tf-fmt: ## Format the Terraform sources
	terraform fmt -recursive infra/terraform

.PHONY: tf-validate
tf-validate: ## Validate the Terraform configuration (no cloud credentials needed)
	$(TF) init -backend=false -input=false
	$(TF) validate

.PHONY: tf-plan
tf-plan: ## Plan the named environment: make tf-plan ENV=staging
	$(TF) init -input=false -backend-config=environments/$(ENV).backend.hcl
	$(TF) plan -input=false -var-file=environments/$(ENV).tfvars

.PHONY: tf-apply
tf-apply: ## Apply the named environment: make tf-apply ENV=staging
	$(TF) init -input=false -backend-config=environments/$(ENV).backend.hcl
	$(TF) apply -input=false -var-file=environments/$(ENV).tfvars

.PHONY: clean
clean: ## Remove build output and caches
	rm -rf apps/web/.next apps/web/coverage apps/web/playwright-report apps/web/test-results
