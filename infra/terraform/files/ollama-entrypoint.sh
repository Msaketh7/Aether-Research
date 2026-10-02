#!/bin/sh
# The embedding service's entrypoint: start Ollama, give it exactly one model,
# and stay in the foreground for as long as it runs.
#
# Run by `/bin/sh -c` in the stock ollama/ollama image (modules/ecs-service,
# entry_point), with two settings from the task definition:
#
#   OLLAMA_MODEL_PIN  the versioned tag to pull, e.g. nomic-embed-text:v1.5
#   OLLAMA_MODEL      the name the application asks for - the registry entry's
#                     model_id - which the pinned tag is copied to
#
# Why the copy: the application requests `nomic-embed-text`, which Ollama
# resolves as `:latest`, a pointer the registry can move. Pulling `latest`
# directly would mean a task started next month could embed with different
# weights than the index was built with - vectors that still compare, and
# compare meaninglessly. Pulling a version and naming it what the application
# asks for keeps the request unchanged and the weights fixed.
#
# Every failure exits non-zero, so ECS marks the task stopped rather than
# leaving a server with no model registered as healthy. The container health
# check
# (`ollama show $OLLAMA_MODEL`) is what keeps the task out of service discovery
# until the copy has happened.
set -u

: "${OLLAMA_MODEL_PIN:?OLLAMA_MODEL_PIN must name the tag to pull}"
: "${OLLAMA_MODEL:?OLLAMA_MODEL must name the model the application requests}"

/bin/ollama serve &
server=$!

# `sh -c` is PID 1, and PID 1 has no default signal handlers: without this, the
# SIGTERM ECS sends at a deployment would be ignored and the task killed at the
# stop timeout instead of shutting down.
trap 'kill -TERM "$server" 2>/dev/null; wait "$server"; exit 143' TERM INT

# Bounded: a server that never answers is a task that should fail, not one that
# waits forever while the health check's start period runs out.
attempts=0
until /bin/ollama list >/dev/null 2>&1; do
  if ! kill -0 "$server" 2>/dev/null; then
    echo "ollama serve exited before it was ready" >&2
    exit 1
  fi
  attempts=$((attempts + 1))
  if [ "$attempts" -ge 120 ]; then
    echo "ollama serve did not answer within 120 s" >&2
    kill -TERM "$server" 2>/dev/null
    exit 1
  fi
  sleep 1
done

if ! /bin/ollama pull "$OLLAMA_MODEL_PIN"; then
  echo "could not pull $OLLAMA_MODEL_PIN" >&2
  kill -TERM "$server" 2>/dev/null
  exit 1
fi

if ! /bin/ollama cp "$OLLAMA_MODEL_PIN" "$OLLAMA_MODEL"; then
  echo "could not name $OLLAMA_MODEL_PIN as $OLLAMA_MODEL" >&2
  kill -TERM "$server" 2>/dev/null
  exit 1
fi

wait "$server"
