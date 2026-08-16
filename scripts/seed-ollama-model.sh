#!/usr/bin/env bash
# Convenience wrapper for manually (re-)pulling the configured Ollama model
# into an already-running `ollama` service, without waiting on the one-shot
# `ollama-pull` init container (e.g. after changing OLLAMA_MODEL in .env).
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${OLLAMA_MODEL:-}" ]; then
  echo "OLLAMA_MODEL is not set (check .env)." >&2
  exit 1
fi

echo "Pulling model '${OLLAMA_MODEL}' into the ollama service..."
docker compose exec ollama ollama pull "${OLLAMA_MODEL}"
