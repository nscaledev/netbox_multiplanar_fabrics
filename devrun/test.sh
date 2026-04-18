#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

FAST_TEST_LABELS=(
    netbox_plant_graph.tests.test_urls
    netbox_plant_graph.tests.test_navigation
    netbox_plant_graph.tests.test_graphql.GraphQLSchemaRegistrationTestCase
    netbox_plant_graph.tests.test_api.ObjectRegistrySmokeTestCase
    netbox_plant_graph.tests.test_views.ViewRegistrySmokeTestCase
    netbox_plant_graph.tests.test_filtersets.FilterSetRegistrySmokeTestCase
    netbox_plant_graph.tests.test_forms.FormStructureSmokeTestCase
    netbox_plant_graph.tests.test_tables.TableRegistrySmokeTestCase
)

CONTRACT_TEST_LABELS=(
    netbox_plant_graph.tests.test_views
    netbox_plant_graph.tests.test_api
    netbox_plant_graph.tests.test_forms
    netbox_plant_graph.tests.test_filtersets
    netbox_plant_graph.tests.test_tables
    netbox_plant_graph.tests.test_urls
    netbox_plant_graph.tests.test_navigation
    netbox_plant_graph.tests.test_graphql
)

load_compose_env() {
    if [ -f "$ENV_FILE" ]; then
        set -a
        . "$ENV_FILE"
        set +a
    fi
}

prepare_test_environment() {
    load_credentials
    load_compose_env

    export POSTGRES_DB="${POSTGRES_DB:-netbox}"
    export POSTGRES_USER="${POSTGRES_USER:-netbox}"
    export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-${NETBOX_DATABASE_PASSWORD:-netbox}}"
    export NETBOX_CONFIGURATION="${NETBOX_CONFIGURATION:-netbox_plant_graph.tests.netbox_configuration}"
    export NETBOX_PLANT_GRAPH_ENABLE=1
    export NETBOX_TEST_DB_NAME="${NETBOX_TEST_DB_NAME:-$POSTGRES_DB}"
    export NETBOX_TEST_DB_USER="${NETBOX_TEST_DB_USER:-$POSTGRES_USER}"
    export NETBOX_TEST_DB_PASSWORD="${NETBOX_TEST_DB_PASSWORD:-$POSTGRES_PASSWORD}"
    export NETBOX_TEST_DB_HOST="${NETBOX_TEST_DB_HOST:-127.0.0.1}"
    export NETBOX_TEST_DB_PORT="${NETBOX_TEST_DB_PORT:-5432}"
    export NETBOX_TEST_DB_TEST_NAME="${NETBOX_TEST_DB_TEST_NAME:-test_${NETBOX_TEST_DB_NAME}_plant_graph}"
    export NETBOX_TEST_REDIS_HOST="${NETBOX_TEST_REDIS_HOST:-127.0.0.1}"
    export NETBOX_TEST_REDIS_PORT="${NETBOX_TEST_REDIS_PORT:-6379}"
    export NETBOX_TEST_REDIS_PASSWORD="${NETBOX_TEST_REDIS_PASSWORD:-}"
}

run_django_tests() {
    local -a labels=("$@")

    (
        cd "$NETBOX_PROJECT_DIR"
        exec "$VENV_DIR/bin/python" manage.py test --keepdb --noinput "${labels[@]}"
    )
}

main() {
    require_command docker
    require_command pg_isready
    require_command redis-cli

    prepare_test_environment
    docker_compose up -d postgres redis >/dev/null
    wait_for_postgres
    wait_for_redis

    case "${1:-contract}" in
        fast)
            shift || true
            run_django_tests "${FAST_TEST_LABELS[@]}" "$@"
            ;;
        contract)
            shift || true
            run_django_tests "${CONTRACT_TEST_LABELS[@]}" "$@"
            ;;
        full)
            shift || true
            run_django_tests netbox_plant_graph.tests "$@"
            ;;
        *)
            run_django_tests "$@"
            ;;
    esac
}

main "$@"
