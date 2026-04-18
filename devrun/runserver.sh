#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

require_command python3

HOST="${NETBOX_RUN_HOST:-0.0.0.0}"
PORT="${NETBOX_RUN_PORT:-8000}"
ENABLE_PLUGIN="${NETBOX_PLANT_GRAPH_ENABLE:-1}"

cd "$NETBOX_PROJECT_DIR"
source "$VENV_DIR/bin/activate"

NETBOX_PLANT_GRAPH_ENABLE="$ENABLE_PLUGIN" exec python manage.py runserver "$HOST:$PORT"
