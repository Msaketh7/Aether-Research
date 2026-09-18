#!/usr/bin/env bash
# Stand up a disposable API and load it with Locust (Phase 21).
#
#   uv run python scripts/with_test_db.py bash scripts/loadtest_api.sh
#
# `make loadtest-api` points Locust at an API that is already running, which is
# what to do against a deployment. This is the local form: it expects
# DATABASE_URL to name a database it may own - which `with_test_db.py` provides
# - migrates it, starts uvicorn, waits for the readiness probe, runs the load,
# and stops the server whatever happened.
#
# The load registers its own accounts and creates its own runs, because every
# read on this surface is scoped by user_id: rows seeded under some other
# account are rows these users cannot see, and would measure an index lookup
# that returns nothing.
#
# No worker is started. The runs stay queued, which is the correct state for
# this measurement: the question is what the read surface costs while research
# is outstanding, and starting a worker here would mean measuring the pipeline
# a second time with a less precise instrument.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set - run under scripts/with_test_db.py}"
export APP_ENV=test
export AETHER_LOAD_START_RUNS=1
# Close the development identity. In `test` it answers any request that
# arrives without a session, so a load whose sign-in is broken keeps
# returning 200 - as one shared user, against one shared rate-limit
# bucket. The first run of this script measured exactly that.
export DEV_IDENTITY_ENABLED=false

PORT="${PORT:-8123}"
USERS="${USERS:-20}"
SPAWN="${SPAWN:-2}"
RUNTIME="${RUNTIME:-60s}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${OUT:-$HERE/../../data/loadtest}"
PYTHON="${PYTHON:-$HERE/.venv/Scripts/python.exe}"
[[ -x "$PYTHON" ]] || PYTHON="$HERE/.venv/bin/python"

mkdir -p "$OUT"
cd "$HERE"

echo "[loadtest-api] migrating"
"$PYTHON" -c "
from tests.support.postgres import apply_migrations
import os
apply_migrations(os.environ['DATABASE_URL'], with_vector=False)
"

echo "[loadtest-api] starting uvicorn on :$PORT"
"$PYTHON" -m uvicorn app.main:app --port "$PORT" --log-level warning &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/ready" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/ready" >/dev/null || {
  echo "[loadtest-api] the API never became ready" >&2
  exit 1
}

echo "[loadtest-api] $USERS users for $RUNTIME"
uv run --with locust locust -f loadtest/locustfile.py \
  --headless --host "http://127.0.0.1:$PORT" \
  --users "$USERS" --spawn-rate "$SPAWN" --run-time "$RUNTIME" \
  --csv "$OUT/api"
