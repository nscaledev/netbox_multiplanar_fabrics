#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

require_command python3

ENABLE_PLUGIN="${NETBOX_PLANT_GRAPH_ENABLE:-1}"
QUEUE="${NETBOX_RQ_QUEUE:-default}"

cd "$NETBOX_PROJECT_DIR"
source "$VENV_DIR/bin/activate"

NETBOX_PLANT_GRAPH_ENABLE="$ENABLE_PLUGIN" exec python manage.py rqworker "$QUEUE"
