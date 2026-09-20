#!/usr/bin/env bash
# Start the stack using whatever images already exist - no build step.
# Use deploy.sh instead if source has changed since the last build.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose up -d
docker compose ps
