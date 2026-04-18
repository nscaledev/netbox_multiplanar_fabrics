#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

usage() {
    cat <<'EOF'
Usage: ./dev.sh <start|stop|status|test>

    start   Ensure containers, config, migrations, worker, and run the NetBox dev server
    stop    Stop the NetBox dev server, worker, and container stack
    status  Show the current NetBox dev environment and process status
    test    Run Python test lanes through the dedicated test wrapper (fast|contract|full)
EOF
}

start_dev() {
    if runserver_is_running; then
        printf 'NetBox development server is already running for %s\n' "$NETBOX_PROJECT_DIR"
        exec "$DEVRUN_DIR/status.sh"
    fi

    "$DEVRUN_DIR/bootstrap-netbox.sh"
    ensure_state_dir
    if ! worker_is_running; then
        nohup "$DEVRUN_DIR/worker.sh" >"$STATE_DIR/worker.log" 2>&1 &
    fi
    exec "$DEVRUN_DIR/runserver.sh"
}

stop_dev() {
    "$DEVRUN_DIR/stop.sh"
}

status_dev() {
    "$DEVRUN_DIR/status.sh"
}

test_dev() {
    exec "$DEVRUN_DIR/test.sh" "$@"
}

case "${1:-}" in
    start)
        start_dev
        ;;
    stop)
        stop_dev
        ;;
    status)
        status_dev
        ;;
    test)
        shift
        test_dev "$@"
        ;;
    -h|--help|help|'')
        usage
        ;;
    *)
        printf 'Unknown command: %s\n\n' "$1" >&2
        usage >&2
        exit 1
        ;;
esac
