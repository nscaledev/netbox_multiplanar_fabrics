#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" = "--volumes" ]; then
    docker compose down --volumes
else
    docker compose down
fi

