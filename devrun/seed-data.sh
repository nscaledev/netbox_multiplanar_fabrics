#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

usage() {
    cat <<'EOF'
Usage: ./seed-data.sh [seed_data.py options]

Populate the local NetBox dev instance with generated sample data for the
netbox_plant_graph plugin. By default this creates both the NVIDIA and Arista
four-plane fabrics at full scale.

Examples:
  ./seed-data.sh --dry-run
  ./seed-data.sh --fabric nvidia --planes 1 --leaves 2 --spines 2 --gpu-facing-ports 1 --spine-facing-ports 1
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
exec "$VENV_DIR/bin/python" "$DEVRUN_DIR/seed_data.py" "$@"