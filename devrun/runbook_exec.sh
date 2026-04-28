#!/usr/bin/env bash
# Runbook execution script — phases 1-12 via NetBox API
# Usage: bash runbook_exec.sh
set -euo pipefail

BASE="http://localhost:8000/api"
TOKEN="dboYv2EEWOshJjR1KhaAC5B06iY9NYjQRCuXRj0q"
H_AUTH="Authorization: Token $TOKEN"
H_CT="Content-Type: application/json"

api_post() {
    local endpoint="$1"
    local data="$2"
    curl -s -X POST "$BASE/$endpoint/" -H "$H_AUTH" -H "$H_CT" -d "$data"
}

api_get() {
    local endpoint="$1"
    curl -s "$BASE/$endpoint/" -H "$H_AUTH"
}

echo "=== Phase 1: Tenants ==="
api_post "tenancy/tenants" '{"name":"Tenant Alpha","slug":"tenant-alpha","description":"GPU workload consumer"}'
echo
api_post "tenancy/tenants" '{"name":"Tenant Beta","slug":"tenant-beta","description":"GPU workload consumer"}'
echo

echo "=== Phase 2: Site ==="
api_post "dcim/sites" '{"name":"DC Alpha","slug":"dc-alpha","status":"active"}'
echo

echo "=== Phase 2: Locations ==="
# building-1 (no parent, need site id=1)
api_post "dcim/locations" '{"name":"Building 1","slug":"building-1","site":1}'
echo
# halls under building-1 (location id=1)
api_post "dcim/locations" '{"name":"Hall Compute A","slug":"hall-compute-a","site":1,"parent":1}'
echo
api_post "dcim/locations" '{"name":"Hall Compute B","slug":"hall-compute-b","site":1,"parent":1}'
echo
api_post "dcim/locations" '{"name":"Hall Compute C","slug":"hall-compute-c","site":1,"parent":1}'
echo
api_post "dcim/locations" '{"name":"Hall Network","slug":"hall-network","site":1,"parent":1}'
echo

echo "=== Phase 3: Device Roles ==="
for role_data in \
    '{"name":"GPU Server","slug":"gpu-server","color":"00bcd4","vm_role":false}' \
    '{"name":"RoCE Leaf Switch","slug":"roce-leaf-switch","color":"4caf50","vm_role":false}' \
    '{"name":"RoCE Spine Switch","slug":"roce-spine-switch","color":"8bc34a","vm_role":false}' \
    '{"name":"Frontside Leaf Switch","slug":"frontside-leaf-switch","color":"ff9800","vm_role":false}' \
    '{"name":"Frontside Spine Switch","slug":"frontside-spine-switch","color":"ff5722","vm_role":false}' \
    '{"name":"Management Switch","slug":"management-switch","color":"9e9e9e","vm_role":false}' \
    '{"name":"Edge Router","slug":"edge-router","color":"795548","vm_role":false}'; do
    api_post "dcim/device-roles" "$role_data"
    echo
done

echo "=== Phase 4: Platforms ==="
for plat in \
    '{"name":"GPU Host OS","slug":"gpu-host-os"}' \
    '{"name":"RoCE Switch NOS","slug":"roce-nos"}' \
    '{"name":"Frontside NOS","slug":"frontside-nos"}'; do
    api_post "dcim/platforms" "$plat"
    echo
done

echo "DONE phase 1-4"
