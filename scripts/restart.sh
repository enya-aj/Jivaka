#!/usr/bin/env bash
# Restart the running containers in place - no rebuild, no image changes.
# Use deploy.sh instead if source has changed since the last build.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose restart
docker compose ps
