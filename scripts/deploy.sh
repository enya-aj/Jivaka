#!/usr/bin/env bash
# Build (if needed) and bring up the full Jivaka stack. Safe to re-run:
# only images whose source changed get rebuilt, and containers are
# recreated in place - existing volumes/data are untouched.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and set OLLAMA_MODEL first." >&2
  exit 1
fi

docker compose build
docker compose up -d
docker compose ps
