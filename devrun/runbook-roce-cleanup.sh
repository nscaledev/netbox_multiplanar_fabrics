#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

usage() {
    cat <<'EOF'
Usage: ./runbook-roce-cleanup.sh [--execute]

Remove the objects created by docs/runbook-roce-fabric-modeling.md from the
local NetBox dev instance. Defaults to dry-run.

Examples:
  ./runbook-roce-cleanup.sh
  ./runbook-roce-cleanup.sh --execute
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

require_command pg_isready

if [[ ! -f "$CONFIG_FILE" ]] || ! pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    "$DEVRUN_DIR/bootstrap-netbox.sh"
fi

cd "$NETBOX_PROJECT_DIR"
export NETBOX_PLANT_GRAPH_ENABLE=1
"$VENV_DIR/bin/python" manage.py migrate --noinput
exec "$VENV_DIR/bin/python" -u "$DEVRUN_DIR/runbook_roce_cleanup.py" "$@"
