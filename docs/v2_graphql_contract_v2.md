# Multi-planar V2 GraphQL Contract

Contract Version: `2.0.0`
Effective Date: `2026-05-20`
Documentation Refresh: `2026-05-22`
Baseline for break comparison: `8d3a4ea`

## Purpose

This document versions the V2 GraphQL API contract and records known breaks
from the earlier baseline. GraphQL V2 is read-oriented. Operational mutations
such as stamp execution, rollback, workflow finding lifecycle actions, and
exception approval are REST contracts, not GraphQL mutations.

For the cross-surface REST, import/reconcile, topology audit, and visual trace
contract, see `docs/v2_external_contracts.md`.

## Versioning Policy

1. Major version (`X.0.0`) changes indicate breaking contract differences.
2. Minor version (`2.Y.0`) changes add backward-compatible stable fields.
3. Patch version (`2.0.Z`) changes are non-contractual fixes or documentation.

The schema exposes `graphql_contract_version` for machine-readable contract
identification. NetBox configures GraphQL with snake_case field and argument
names.

## Stability Labels

| Label | Meaning |
| --- | --- |
| `Stable` | External consumers may depend on the field name, arguments, and documented top-level JSON keys. |
| `Experimental` | The field is available and tested, but nested JSON payload details may change. |
| `Removed/Renamed` | Present in the baseline but not available under the same name in V2 `2.0.0`. |

## Stable Minimal Query Contract

The following fields are the supported V2 `2.0.0` GraphQL contract:

| Field | Arguments | Stable top-level JSON keys |
| --- | --- | --- |
| `graphql_contract_version` | none | String value, currently `2.0.0`. |
| `v2_status` | none | `status`, `architecture_count`, `fabric_count`. |
| `fabrics` | none | List entries with `id`, `name`, `slug`, `status`. |
| `optical_lanes` | `fabric_id`, `plane_id`, `direction` | List entries with `id`, `fabric_id`, `endpoint_id`, `plane_id`, `lane_index`, `direction`, `wavelength_nm`, `pair_key`. |
| `optical_lane_path` | `source_id`, optional `destination_id` | `path_found`, `source_lane_id`, `destination_lane_id`, `error`, `steps`. |
| `stamp_runs` | optional `fabric_id` | List entries with `id`, `fabric_id`, `template_id`, `status`, `created`. |
| `suppression_rules` | optional `fabric_id`, `active_only` | List entries with `id`, `fabric_id`, `plane_id`, `optical_lane_id`, `policy_key`, `status`, `expires_at`, `revoked_at`. |
| `audit_events` | optional `fabric_id`, `limit` | List entries with `id`, `fabric_id`, `event_type`, `outcome`, `actor_id`, `subject_type_id`, `subject_id`, `created`, `message`. |
| `operation_runs` | optional `fabric_id` | List entries with `id`, `fabric_id`, `profile`, `status`, `initiated_by_id`, `started_at`, `completed_at`. |

Stable path step keys are:

- `step_type`
- `object_type`
- `object_id`
- `label`
- `metadata`

Example version gate:

```graphql
query ContractVersion {
  graphql_contract_version
}
```

Example lane inventory:

```graphql
query LaneInventory($fabric_id: ID!) {
  graphql_contract_version
  optical_lanes(fabric_id: $fabric_id, direction: "send")
}
```

Example path resolution:

```graphql
query ResolveLanePath($source_id: ID!, $destination_id: ID) {
  optical_lane_path(source_id: $source_id, destination_id: $destination_id)
}
```

## Experimental Query Fields

These fields are available for dashboards, operators, and lab automation, but
their nested JSON shape is not yet a stable external contract:

1. `lane_drilldown`
2. `lane_compare`
3. `blast_radius`
4. `audit_workflow_summary`
5. `audit_finding_search`
6. `audit_finding_detail`
7. `audit_run_timeline`
8. `policy_summary`
9. `policy_dashboard`
10. `contamination_domains`
11. `deployment_workflow_summary`
12. `stamp_template_preview`

External automation may use these behind `graphql_contract_version` checks and
local snapshot tests, but should expect future minor releases to add or reshape
nested keys.

## Known Breaking Changes From Baseline

The following baseline fields are not present under the same names in V2
`2.0.0`:

1. `resolve_path`
2. `plane_audit`
3. `fabric_health`
4. `fabric_health_typed`
5. `lane_drilldown_typed`
6. `lane_path`
7. `lane_set`
8. `lane_allocation_summary`
9. `assembly_template_detail`
10. `spatial_template_detail`
11. `spatial_placements_by_scope`
12. `deployment_plan_detail`
13. `stamp_preview`

## Field Migration Guidance

| Baseline field | V2 guidance |
| --- | --- |
| `stamp_preview` | Use REST `POST /api/plugins/plant-graph/stamps/preview/`. GraphQL `stamp_template_preview` is experimental. |
| `resolve_path` / `lane_path` | Use stable GraphQL `optical_lane_path` or REST `GET /api/plugins/plant-graph/path-query/`. |
| `deployment_plan_detail` | Use REST stamp execute/rollback plus `workflow/runs/`. GraphQL `deployment_workflow_summary` is experimental. |
| `plane_audit` / `fabric_health` | Use `mpf_audit_integrity` for preflight checks and REST workflow findings for persisted audit lifecycle. |
| `lane_set` / `lane_allocation_summary` | Use stable `optical_lanes` for inventory. Treat `lane_drilldown` and `lane_compare` as experimental. |
| `fabric_health_typed` / `lane_drilldown_typed` | No typed V2 replacement exists in GraphQL `2.0.0`; current operational payloads are JSON-forward. |

## Intentional Contract Decisions

1. V2 GraphQL remains read-only.
2. Stable GraphQL fields return JSON payloads with documented top-level keys.
3. REST remains the mutation surface for V2 workflow state.
4. Import/reconcile and topology audit are CLI/service contracts, not GraphQL
   contracts.
5. Floorplan-coupled GraphQL surfaces are excluded from the V2 runtime
   contract.
6. Visual path schematics in `Path Query`, `Interface Fanout Trace`, and
   `Physical Cable Blast Radius` are UI contracts for operators, not GraphQL
   field contracts.

## Notes For Integrators

1. Treat `2.0.0` as a major cut from baseline GraphQL consumers.
2. Gate client behavior on `graphql_contract_version`.
3. Avoid assumptions about baseline typed payload structure unless a
   compatibility alias is added in a future minor release.
4. Prefer REST endpoints for automation that creates, mutates, suppresses,
   approves, rolls back, or otherwise changes workflow state.
5. Do not depend on private Python resolver functions; only the GraphQL field
   names above are contract surfaces.
