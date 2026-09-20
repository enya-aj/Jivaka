#!/usr/bin/env bash
# Stop and remove the stack's containers/network. Volumes and bind-mounted
# data (FalkorDB's graph, uploaded files) are left untouched.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose down
