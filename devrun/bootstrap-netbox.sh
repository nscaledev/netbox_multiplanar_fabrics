#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/common.sh"

require_command docker
require_command python3
require_command pg_isready
require_command redis-cli

SOURCE_CREDENTIALS_FILE="${SOURCE_CREDENTIALS_FILE:-$HOME/.config/netbox-rpki-dev/credentials.env}"

generate_password() {
    python3 - <<'PY'
import secrets
import string
alphabet = string.ascii_letters + string.digits + '._-'
print(''.join(secrets.choice(alphabet) for _ in range(40)))
PY
}

generate_secret() {
    python3 - <<'PY'
import secrets
import string
alphabet = string.ascii_letters + string.digits + '._-'
print(''.join(secrets.choice(alphabet) for _ in range(64)))
PY
}

escape_sql_literal() {
    SQL_LITERAL_VALUE="$1" python3 - <<'PY'
import os

print(os.environ['SQL_LITERAL_VALUE'].replace("'", "''"))
PY
}

is_safe_token() {
    local value="$1"
    local expected_length="$2"
    [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
    [ "${#value}" -eq "$expected_length" ]
}

read_source_credential() {
    local name="$1"

    if [ ! -f "$SOURCE_CREDENTIALS_FILE" ]; then
        return 1
    fi

    (
        set -a
        # shellcheck disable=SC1090
        . "$SOURCE_CREDENTIALS_FILE"
        set +a
        eval "printf '%s' \"\${$name-}\""
    )
}

ensure_credentials() {
    local source_database_password=""
    local source_admin_password=""
    local source_secret_key=""
    local source_api_token_pepper=""

    ensure_state_dir
    load_credentials

    source_database_password="$(read_source_credential NETBOX_DATABASE_PASSWORD || true)"
    source_admin_password="$(read_source_credential NETBOX_ADMIN_PASSWORD || true)"
    source_secret_key="$(read_source_credential NETBOX_SECRET_KEY || true)"
    source_api_token_pepper="$(read_source_credential NETBOX_API_TOKEN_PEPPER || true)"

    if [ -z "${NETBOX_DATABASE_PASSWORD:-}" ]; then
        NETBOX_DATABASE_PASSWORD="${source_database_password:-$(generate_password)}"
    fi
    if [ -z "${NETBOX_ADMIN_PASSWORD:-}" ]; then
        NETBOX_ADMIN_PASSWORD="${source_admin_password:-$(python3 - <<'PY'
import secrets
import string
alphabet = string.ascii_letters + string.digits + '._-'
print(''.join(secrets.choice(alphabet) for _ in range(24)))
PY
)}"
    fi
    if ! is_safe_token "${NETBOX_SECRET_KEY:-}" 64; then
        if is_safe_token "$source_secret_key" 64; then
            NETBOX_SECRET_KEY="$source_secret_key"
        else
            NETBOX_SECRET_KEY="$(generate_secret)"
        fi
    fi
    if ! is_safe_token "${NETBOX_API_TOKEN_PEPPER:-}" 64; then
        if is_safe_token "$source_api_token_pepper" 64; then
            NETBOX_API_TOKEN_PEPPER="$source_api_token_pepper"
        else
            NETBOX_API_TOKEN_PEPPER="$(generate_secret)"
        fi
    fi

    printf 'NETBOX_DATABASE_PASSWORD=%q\n' "$NETBOX_DATABASE_PASSWORD" > "$CREDENTIALS_FILE"
    printf 'NETBOX_ADMIN_PASSWORD=%q\n' "$NETBOX_ADMIN_PASSWORD" >> "$CREDENTIALS_FILE"
    printf 'NETBOX_SECRET_KEY=%q\n' "$NETBOX_SECRET_KEY" >> "$CREDENTIALS_FILE"
    printf 'NETBOX_API_TOKEN_PEPPER=%q\n' "$NETBOX_API_TOKEN_PEPPER" >> "$CREDENTIALS_FILE"
    chmod 600 "$CREDENTIALS_FILE"
}

ensure_compose_env() {
    POSTGRES_DB="netbox"
    POSTGRES_USER="netbox"
    POSTGRES_PASSWORD="$NETBOX_DATABASE_PASSWORD"

    cat > "$ENV_FILE" <<EOF
POSTGRES_DB=$POSTGRES_DB
POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
EOF
    chmod 600 "$ENV_FILE"
}

ensure_database_password() {
    local sql_password

    sql_password="$(escape_sql_literal "$POSTGRES_PASSWORD")"

    docker_compose exec -T postgres \
        psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
        -c "ALTER ROLE $POSTGRES_USER WITH PASSWORD '$sql_password';" >/dev/null

    PGPASSWORD="$POSTGRES_PASSWORD" \
        psql "host=127.0.0.1 port=5432 dbname=postgres user=$POSTGRES_USER" \
        -Atqc 'select 1' >/dev/null
}

ensure_netbox_source_link() {
    local site_packages
    site_packages="$($VENV_DIR/bin/python -c 'import site; print(site.getsitepackages()[0])')"
    printf '%s\n' "$NETBOX_PROJECT_DIR" > "$site_packages/netbox.pth"
}

write_configuration() {
    cat > "$CONFIG_FILE" <<EOF
import os

ALLOWED_HOSTS = ['*']
DEBUG = True
DEVELOPER = True

REDIS = {
    'tasks': {
        'HOST': '127.0.0.1',
        'PORT': 6379,
        'PASSWORD': '',
        'DATABASE': 0,
        'SSL': False,
    },
    'caching': {
        'HOST': '127.0.0.1',
        'PORT': 6379,
        'PASSWORD': '',
        'DATABASE': 1,
        'SSL': False,
    },
}

SECRET_KEY = '$NETBOX_SECRET_KEY'
EOF

    # NetBox 4.2.x expects DATABASE (singular) while newer releases use
    # DATABASES. Emit the format expected by the selected release line.
    case "$NETBOX_RELEASE" in
        4.2.*)
            cat >> "$CONFIG_FILE" <<EOF
DATABASE = {
    'NAME': 'netbox',
    'USER': 'netbox',
    'PASSWORD': '$NETBOX_DATABASE_PASSWORD',
    'HOST': '127.0.0.1',
    'PORT': '5432',
    'CONN_MAX_AGE': 300,
}
EOF
            ;;
        *)
            cat >> "$CONFIG_FILE" <<EOF
DATABASES = {
    'default': {
        'NAME': 'netbox',
        'USER': 'netbox',
        'PASSWORD': '$NETBOX_DATABASE_PASSWORD',
        'HOST': '127.0.0.1',
        'PORT': '5432',
        'CONN_MAX_AGE': 300,
    }
}
EOF
            ;;
    esac

    # API_TOKEN_PEPPERS was introduced in NetBox 4.5.0.  Including it on
    # older releases (e.g. 4.2.x) causes an unrecognised-setting error, so
    # we only emit the block when the configured release starts with "4.5."
    # or is from a later major or minor line.
    case "$NETBOX_RELEASE" in
        4.5.*|4.[6-9].*|[5-9].*)
            cat >> "$CONFIG_FILE" <<EOF
API_TOKEN_PEPPERS = {
    1: '$NETBOX_API_TOKEN_PEPPER',
}
EOF
            ;;
    esac

    cat >> "$CONFIG_FILE" <<EOF
PLUGINS = ['netbox_floorplan', 'netbox_plant_graph'] if os.getenv('NETBOX_PLANT_GRAPH_ENABLE') == '1' else []

PLUGINS_CONFIG = {
    'netbox_plant_graph': {
        'top_level_menu': True,
    },
}
EOF
    chmod 600 "$CONFIG_FILE"
}

run_manage_tasks() {
    (
        cd "$NETBOX_PROJECT_DIR"
        source "$VENV_DIR/bin/activate"
        NETBOX_PLANT_GRAPH_ENABLE=1 python manage.py migrate --noinput
        NETBOX_PLANT_GRAPH_ENABLE=1 python manage.py collectstatic --noinput
        python manage.py check
        NETBOX_ADMIN_PASSWORD="$NETBOX_ADMIN_PASSWORD" python manage.py shell -c "import os; from django.contrib.auth import get_user_model; User = get_user_model(); user, created = User.objects.get_or_create(username='admin', defaults={'email': 'admin@example.com', 'is_superuser': True, 'is_active': True}); user.email = 'admin@example.com'; user.is_superuser = True; user.is_active = True; user.set_password(os.environ['NETBOX_ADMIN_PASSWORD']); user.save()"
        NETBOX_PLANT_GRAPH_ENABLE=1 python manage.py check || true
    )
}

ensure_credentials
ensure_compose_env
docker_compose up -d
wait_for_postgres
wait_for_redis
ensure_database_password
ensure_netbox_source_link
write_configuration
run_manage_tasks
