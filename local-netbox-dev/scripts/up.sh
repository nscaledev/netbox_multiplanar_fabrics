#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROM_SD_SRC="${PROM_SD_SRC:-/Users/mencken/github-repos/netbox-plugin-prometheus-sd}"
MPF_SRC="${MPF_SRC:-/Users/mencken/github-repos/netbox_multiplanar_fabrics}"
POWER_PLANT_SRC="${POWER_PLANT_SRC:-/Users/mencken/github-repos/netbox_power_plant}"
PROM_SD_VENDOR="$ROOT/vendor/netbox-plugin-prometheus-sd"
MPF_VENDOR="$ROOT/vendor/netbox_multiplanar_fabrics"
POWER_PLANT_VENDOR="$ROOT/vendor/netbox_power_plant"

if ! command -v docker >/dev/null 2>&1; then
    printf 'docker is not installed or not on PATH. Install Docker Desktop, OrbStack, or Colima plus the Docker CLI first.\n' >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    printf 'Docker is installed but the daemon is not reachable. Start your container runtime and retry.\n' >&2
    exit 1
fi

if [ ! -d "$PROM_SD_SRC/netbox_prometheus_sd" ]; then
    printf 'Expected netbox-plugin-prometheus-sd at %s; set PROM_SD_SRC to override.\n' "$PROM_SD_SRC" >&2
    exit 1
fi
if [ ! -d "$MPF_SRC/netbox_plant_graph" ]; then
    printf 'Expected netbox_multiplanar_fabrics at %s; set MPF_SRC to override.\n' "$MPF_SRC" >&2
    exit 1
fi
if [ ! -d "$POWER_PLANT_SRC/netbox_power_plant" ]; then
    printf 'Expected netbox_power_plant at %s; set POWER_PLANT_SRC to override.\n' "$POWER_PLANT_SRC" >&2
    exit 1
fi

mkdir -p "$ROOT/vendor"
rm -rf "$PROM_SD_VENDOR"
rm -rf "$MPF_VENDOR"
rm -rf "$POWER_PLANT_VENDOR"
rsync -a --delete --exclude .git "$PROM_SD_SRC/" "$PROM_SD_VENDOR/"
rsync -a --delete \
    --exclude .git \
    --exclude .venv \
    --exclude local-netbox-dev \
    "$MPF_SRC/" "$MPF_VENDOR/"
rsync -a --delete \
    --exclude .git \
    --exclude .venv \
    "$POWER_PLANT_SRC/" "$POWER_PLANT_VENDOR/"

cd "$ROOT"
docker compose build --no-cache
if ! docker compose up -d; then
    printf '\nNetBox failed during compose startup. Recent logs:\n' >&2
    docker compose logs --tail=120 netbox >&2 || true
    exit 1
fi

printf 'Waiting for NetBox health check'
for _ in $(seq 1 120); do
    status_json="$(docker compose ps --format json netbox 2>/dev/null || true)"
    if printf '%s' "$status_json" | grep -q '"Health":"healthy"'; then
        printf '\n'
        break
    fi
    if printf '%s' "$status_json" | grep -Eq '"State":"(dead|exited)"|"ExitCode":[1-9]'; then
        printf '\nNetBox exited while waiting for health. Recent logs:\n' >&2
        docker compose logs --tail=120 netbox >&2 || true
        exit 1
    fi
    printf '.'
    sleep 2
done

if ! docker compose ps --format json netbox 2>/dev/null | grep -q '"Health":"healthy"'; then
    printf '\nNetBox did not become healthy in time. Recent logs:\n' >&2
    docker compose logs --tail=100 netbox >&2
    exit 1
fi

docker compose exec -T netbox \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py migrate --noinput

docker compose restart netbox netbox-worker >/dev/null

docker compose exec -T netbox \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell <<'PY'
from django.contrib.auth import get_user_model

User = get_user_model()
user, _ = User.objects.get_or_create(username="admin")
user.email = "admin@example.local"
user.is_staff = True
user.is_superuser = True
user.is_active = True
user.set_password("admin")
user.save()
PY

curl -fsS http://localhost:8000/login/ >/dev/null
printf 'NetBox is up at http://localhost:8000 (admin/admin)\n'
