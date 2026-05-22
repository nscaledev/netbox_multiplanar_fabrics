#!/usr/bin/env bash
set -euo pipefail

: "${NETBOX_URL:?Set NETBOX_URL, for example https://netbox.example.com}"
: "${NETBOX_TOKEN:?Set NETBOX_TOKEN to a NetBox API token}"

AUTH_HEADER=(-H "Authorization: Token ${NETBOX_TOKEN}")
JSON_HEADER=(-H "Content-Type: application/json")
STAMP_TEMPLATE_ID="${STAMP_TEMPLATE_ID:-10}"
STAMP_RUN_ID="${STAMP_RUN_ID:-55}"
FABRIC_SLUG="${FABRIC_SLUG:-example-fabric}"
FABRIC_ID="${FABRIC_ID:-1}"
SOURCE_LANE_ID="${SOURCE_LANE_ID:-101}"
DESTINATION_LANE_ID="${DESTINATION_LANE_ID:-202}"

# Stable: preview a stamp before mutation.
curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/stamps/preview/" \
    -d @docs/examples/stamp_preview_request.json

# Stable: execute a known-good stamp template.
curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/stamp-templates/${STAMP_TEMPLATE_ID}/execute/" \
    -d @docs/examples/stamp_execute_request.json

# Stable: rollback is explicit; keep your own confirmation gate before this call.
curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/stamp-runs/${STAMP_RUN_ID}/rollback/" \
    -d '{}'

# Stable: confirm the fabric exists through the registry endpoint.
curl -sS \
    "${AUTH_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/fabrics/?slug=${FABRIC_SLUG}"

# Stable: resolve a plugin-native path without scraping the visual UI.
curl -sS \
    "${AUTH_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/path-query/?source_lane=${SOURCE_LANE_ID}&destination_lane=${DESTINATION_LANE_ID}"

# Stable: inspect operation runs after stamp/audit activity.
curl -sS \
    "${AUTH_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/workflow/runs/?fabric=${FABRIC_ID}&limit=10"

# Stable command examples:
python manage.py mpf_import_reconcile docs/examples/sample_import_reconcile.json --json
python manage.py mpf_import_reconcile docs/examples/sample_import_reconcile.json --apply --json --fail-on-conflict
python manage.py mpf_audit_integrity --fabric "${FABRIC_SLUG}" --format json --fail-on error

# Stable: non-mutating impact preview through REST.
curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/impact/cable-assembly-cut/" \
    -d @docs/examples/impact_cable_assembly_cut_request.json

curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/impact/mpo-connector-unplug/" \
    -d @docs/examples/impact_mpo_connector_unplug_request.json

curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/api/plugins/plant-graph/impact/osfp-transceiver-unseat/" \
    -d @docs/examples/impact_osfp_transceiver_unseat_request.json

# Experimental: GraphQL blast-radius payload shape may change inside the V2 line.
curl -sS -X POST \
    "${AUTH_HEADER[@]}" \
    "${JSON_HEADER[@]}" \
    "${NETBOX_URL}/graphql/" \
    -d @docs/examples/impact_blast_radius_graphql.json
