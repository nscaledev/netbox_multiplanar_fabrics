# Multi-planar V2 GraphQL Contract

Contract Version: `2.0.0`
Effective Date: `2026-05-20`
Baseline for break comparison: `8d3a4ea`

## Purpose

This document formally versions the GraphQL API contract for V2 and records known contract breaks from the original baseline.

## Versioning Policy

1. Major version (`X.0.0`) changes indicate breaking contract differences.
2. Minor version (`2.Y.0`) changes add backward-compatible fields.
3. Patch version (`2.0.Z`) changes are non-contractual fixes/documentation only.

The current schema exposes `graphql_contract_version` for machine-readable contract identification.

## Current V2 Query Surface (2.0.0)

Primary operational fields:

1. `graphql_contract_version`
2. `v2_status`
3. `optical_lane_path`
4. `fabrics`
5. `optical_lanes`
6. `stamp_runs`
7. `suppression_rules`
8. `audit_events`
9. `operation_runs`
10. `lane_drilldown`
11. `lane_compare`
12. `blast_radius`
13. `audit_workflow_summary`
14. `audit_finding_search`
15. `audit_finding_detail`
16. `audit_run_timeline`
17. `policy_summary`
18. `policy_dashboard`
19. `contamination_domains`
20. `deployment_workflow_summary`
21. `stamp_template_preview`

## Breaking Changes from Baseline

The following baseline fields are not present under the same names in V2 `2.0.0`:

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

## Field Migration Guidance (Baseline -> V2)

1. `stamp_preview` -> `stamp_template_preview`
2. `resolve_path` / `lane_path` -> `optical_lane_path` (or `lane_drilldown` for richer context)
3. `deployment_plan_detail` -> `deployment_workflow_summary`
4. `plane_audit` + portions of `fabric_health` -> `audit_workflow_summary`, `policy_dashboard`, `contamination_domains`
5. `lane_set` / `lane_allocation_summary` -> `lane_drilldown` and `lane_compare`

## Intentional Contract Decisions

1. V2 operational outputs are JSON-forward and model-agnostic where possible.
2. Floorplan-coupled GraphQL surfaces are excluded from V2 runtime contract.
3. Backward-compat aliases are only added when explicitly approved.

## Notes for Integrators

1. Treat `2.0.0` as a major cut from baseline GraphQL consumers.
2. Gate client behavior on `graphql_contract_version`.
3. Avoid assumptions about baseline typed payload structure unless a compatibility alias is added in a future minor release.
