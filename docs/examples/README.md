# V2 External Automation Examples

These examples are starter kit fragments for scripts that call the documented
V2 external contract. Replace IDs, slugs, and file paths before running them
against a real NetBox instance.

## Contract Labels

| Example | Label | Notes |
| --- | --- | --- |
| `stamp_preview_request.json` | Stable | Request body for `POST /api/plugins/plant-graph/stamps/preview/`. |
| `stamp_execute_request.json` | Stable | Request body for `POST /api/plugins/plant-graph/stamp-templates/<id>/execute/`. |
| `sample_import_reconcile.json` | Stable nucleus | Compact example using `cable_assembly` and `fiber_strand_cable`; the full documented kind list is in `v2_import_reconciliation.md`. |
| `impact_cable_assembly_cut_request.json` | Stable | Request body for `POST /api/plugins/plant-graph/impact/cable-assembly-cut/`. |
| `impact_mpo_connector_unplug_request.json` | Stable | Request body for `POST /api/plugins/plant-graph/impact/mpo-connector-unplug/`. |
| `impact_osfp_transceiver_unseat_request.json` | Stable | Request body for `POST /api/plugins/plant-graph/impact/osfp-transceiver-unseat/`. |
| `curl_workflows.sh` stamp, audit, import, path sections | Stable | Uses documented REST routes and command options. |
| `impact_blast_radius_graphql.json` | Experimental | Uses GraphQL `blast_radius`; keep local snapshot/smoke tests around this flow. |

## Stamp Flow

Stable automation should preview before it mutates:

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/stamps/preview/" \
  -d @docs/examples/stamp_preview_request.json
```

Execute only after the preview is clean:

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/stamp-templates/$STAMP_TEMPLATE_ID/execute/" \
  -d @docs/examples/stamp_execute_request.json
```

Rollback is stable as an explicit operator/API action, but automation should
record the `StampRun` ID returned by execute and require its own confirmation
gate before calling rollback.

## Audit Flow

Topology integrity audit is the stable preflight before trusting path tracing,
visual review, or impact modeling:

```bash
python manage.py mpf_audit_integrity --fabric example-fabric --format json --fail-on error
```

Stable JSON envelope keys are documented in `docs/v2_external_contracts.md`.
Finding codes and nested remediation details may expand.

## Import Flow

Use import/reconcile for repeatable script-fed topology updates:

```bash
python manage.py mpf_import_reconcile docs/examples/sample_import_reconcile.json --json
python manage.py mpf_import_reconcile docs/examples/sample_import_reconcile.json --apply --json --fail-on-conflict
```

The stable outcomes are `create`, `update`, `skip`, and `conflict`. Stop on
any conflict unless the consuming runbook explicitly knows how to remediate it.

## Impact Flow

Stable automation should use the REST impact preview endpoints. They are
non-mutating and return the documented operational-impact envelope.

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/api/plugins/plant-graph/impact/cable-assembly-cut/" \
  -d @docs/examples/impact_cable_assembly_cut_request.json
```

Operator review remains available through `/plugins/plant-graph/blast-radius/`.
The GraphQL `blast_radius` example is experimental and should be treated as a
snapshot-tested lab contract.

```bash
curl -sS -X POST \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  "$NETBOX_URL/graphql/" \
  -d @docs/examples/impact_blast_radius_graphql.json
```
