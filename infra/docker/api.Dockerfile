# The API and the worker, in one image.
#
# ADR 0001 makes them one codebase and two process types, and that is the shape
# of this file: one build, two entrypoints. The image's CMD starts the API; the
# worker is the same image with its command overridden (see docker-compose.yml
# and infra/terraform/modules/ecs-service). Two images built from one tree would
# be two things to keep in step and two things to scan, for no difference in
# content.
#
# Build context is the repository root, not apps/api - see the note on WORKDIR.
#
#   docker build -f infra/docker/api.Dockerfile -t aether-api .

# --- builder -----------------------------------------------------------------
# uv resolves from the committed lockfile and never touches the network for a
# version decision. `--frozen` fails the build if uv.lock disagrees with
# pyproject.toml, which is the property that makes the image reproducible.
FROM python:3.12-slim-bookworm AS builder

# Pinned rather than :latest, because an image that resolves its own build tool
# at build time is not reproducible either.
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# The path depth is load-bearing, not cosmetic. `app/core/config.py` computes
# REPO_ROOT as `Path(__file__).resolve().parents[4]`, which needs four
# directories above `app/` - at `/app/app/core/config.py` that index raises
# IndexError and the process dies on import, before any logging exists. Keeping
# `apps/api` in the image path is what makes the repository layout and the
# container layout the same layout. tests/test_infrastructure.py fails if this
# WORKDIR gets shortened.
WORKDIR /srv/aether/apps/api

# Dependencies first, as their own layer: they change when uv.lock changes,
# which is rarely, while app/ changes every commit.
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY apps/api/app ./app
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/migrations ./migrations

# Installs the project itself into the same virtualenv. Deliberately *not*
# `--no-editable`: a non-editable install moves the code into site-packages,
# which changes `__file__` and therefore REPO_ROOT. The source tree is the
# installed tree here, at a path that is fixed for the life of the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# libpq is not needed - asyncpg speaks the wire protocol itself and psycopg is
# installed as psycopg[binary] - so the runtime carries no build toolchain and
# no compiler. tini reaps the parse worker's children: document parsing forks a
# process per document (app/retrieval/parse_worker.py), and PID 1 in a container
# does not reap by default, so without an init the image accumulates zombies
# under load.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# A fixed uid/gid rather than whatever the base image's next free number is, so
# a volume written by this container has predictable ownership on the host and
# in a Kubernetes securityContext.
RUN groupadd --gid 10001 aether \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/aether aether

# APP_ENV is deliberately baked to `production` rather than left at the
# Settings default of `local`. The difference is not cosmetic: under `local` the
# development identity is allowed, so a deployment that simply forgot to set the
# variable would serve every request as an authenticated developer. The safe
# value is the one that needs no action; compose sets APP_ENV=local explicitly
# to get the other behaviour, which is a thing you can see in a diff.
ENV PATH="/srv/aether/apps/api/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    API_HOST=0.0.0.0 \
    API_PORT=8000

WORKDIR /srv/aether/apps/api

COPY --from=builder --chown=10001:10001 /srv/aether/apps/api /srv/aether/apps/api

# The filesystem storage backend's default location, created here so that a
# deployment that chooses it does not fail on first write. Production selects
# S3 (app/core/config.py refuses the filesystem backend there), so this is the
# local and staging path only.
RUN install -d -o 10001 -g 10001 /srv/aether/.data/object-storage

USER 10001:10001

EXPOSE 8000

# Liveness only - /health answers without touching Postgres, Redis or S3, which
# is the distinction that keeps a database blip from making every replica
# unhealthy at once. Readiness (/ready) is the load balancer's question and is
# asked by the target group, not by the container runtime. Written against
# urllib because the runtime has no curl and adding one to answer a health
# check is 5 MB of shell utilities in an image that has no shell user.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('API_PORT','8000')+'/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]

# One worker process per container. Concurrency is horizontal here (ADR 0008):
# ECS runs N tasks, each with one uvicorn, so a task's CPU reservation means
# something and the autoscaler has one number to act on. `--workers 4` inside a
# task would hide three quarters of the load from every metric the deployment
# scales on.
#
# `--no-access-log` because the application already writes one:
# RequestContextMiddleware emits a structured `app.access` record per request,
# with the request id, the matched route template and the duration. Uvicorn's
# own line has none of those and would double the log volume to say less.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
