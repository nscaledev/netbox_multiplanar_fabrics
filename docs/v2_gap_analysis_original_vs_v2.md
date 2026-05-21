# V2 Gap Analysis: Original Plugin vs Current V2

## Scope & Method
- Baseline ("original"): commit `8d3a4ea`.
- Target ("current v2"): branch `codex-mencken/v2-ground-up-rewrite` at `ccb5a9d`.
- Focus: functionality present in baseline and missing (or materially reduced) in V2, with emphasis on UI pages and workflows.

## Executive Summary
- V2 has a stronger normalized core model and registry-driven CRUD surface, but it is still missing a large portion of the original operator workflow UI.
- Biggest gaps are in:
  1. Audit lifecycle workflows (triage, finding state transitions, exception lifecycle),
  2. Multi-step stamping/deployment workflows,
  3. Advanced lane analysis surfaces (drilldown/compare/blast radius),
  4. Fabric onboarding/operations workflows.

## UI Page & Workflow Gaps

### A) Baseline Pages/Routes Not Present in V2 Routing
Missing named routes from baseline `urls.py` (present in `8d3a4ea`, absent in current V2 route map):

- Visibility/health/onboarding:
  - `graph_overview`, `health`, `fabric_onboard`, `fabric_operations`, `fabric_assign_planes`
- Lane analysis:
  - `path_resolver` (replaced by reduced `path_query`)
  - `lane_drilldown`, `lane_compare`, `blast_radius`
- Policy/audit:
  - `policy_review` (replaced by reduced `policy_dashboard`)
  - `plane_audit`, `audit_dashboard`, `audit_triage`
  - finding lifecycle actions:
    - `audit_finding_acknowledge`
    - `audit_finding_start_remediation`
    - `audit_finding_suppress`
    - `audit_finding_unsuppress`
    - `audit_finding_resolve`
    - `audit_finding_reopen`
  - exception lifecycle:
    - `disjointness_exception_request`
    - `disjointness_exception_approve`
    - `disjointness_exception_expire`
    - `disjointness_exception_reactivate`
- Stamping/templates/deployment:
  - `assembly_stamp_wizard`
  - `assembly_graph_stamp_wizard`
  - `assembly_template_build`
  - `breakout_stamp_wizard`
  - `spatial_stamp_wizard`
  - `spatial_template_compose`
  - `connection_template_builder`
  - `rack_population_stamp_wizard`
  - `deployment_plan_execute`
  - `deployment_plan_rollback`
  - `deployment_plan_workflow`
  - `template_library`

### B) Menu Navigation Regression
- Baseline had workflow menu groups:
  - `Fabric Visibility`
  - `Lane Analysis`
  - `Policy & Audit`
- Current V2 menu group (`Multi-planar v2`) includes only:
  - `Overview`, `Fabrics`, `Architectures`, `Path Query`, `Lane Workspace`, `Policy Dashboard`, `Coordinate Layout`, `Operations Center`
- Result: baseline workflow discovery/navigation is significantly reduced.

### C) Pages Present in V2 But Functionally Reduced
- `Path Query` vs baseline `Path Resolver`:
  - current is optical-lane-centric and narrower in target/resolution controls.
- `Lane Workspace`:
  - current is a filter+table workspace; baseline had richer composed workspace panels and workflow context rails.
- `Policy Dashboard` vs baseline `Policy Review` + `Audit Dashboard` + `Audit Triage`:
  - current summarizes suppression rules but does not provide durable finding workflow operations.
- `Operations Center`:
  - current executes operation profiles and shows recent runs; baseline also had deployment workflow/rollback pages.
- `Coordinate Layout`:
  - current is endpoint spatial metadata editing (floorplan-independent), but baseline had broader spatial template/stamping workflow pages.

### D) Templates Still in Repo but Not Wired to V2 URLs
Legacy templates still exist (examples): `graph_overview.html`, `health.html`, `audit_dashboard.html`, `audit_triage.html`, `lane_drilldown.html`, `lane_compare.html`, `plane_audit.html`, `policy_review.html`, stamping/deployment/template-library pages.

This indicates a route/view wiring gap, not just missing static assets.

## API Gaps (Baseline -> V2)

Baseline API endpoints absent in current V2 API routing:
- `workflow/summary/`
- `workflow/findings/`
- `workflow/findings/<int:pk>/`
- `workflow/runs/`
- `stamps/preview/`
- (`floorplan/reconcile/` intentionally removed due floorplan excision)

Current V2 API surface is narrower (`path-query`, `suppression-summary`, `audit-timeline`, `operation-runs`).

## GraphQL Gaps (Baseline -> V2)

Baseline exposed a broad typed operational query set (including path/plane-audit/blast-radius/lane-drilldown/lane-compare/fabric-health/policy/audit workflow/detail/timeline/stamp preview).

Current V2 GraphQL includes a smaller JSON-forward set:
- `v2_status`, `optical_lane_path`, `fabrics`, `optical_lanes`, `stamp_runs`, `suppression_rules`, `audit_events`, `operation_runs`

Key missing capabilities:
- typed lane analysis and compare surfaces,
- durable audit workflow query model,
- policy analytics model parity,
- template/deployment planning detail queries.

## Priority Gap Ranking (UI/Workflow First)

1. **P0**: Restore audit/finding lifecycle workflows and triage pages (high operational risk if absent).
2. **P0**: Restore advanced lane analysis workflows (`lane_drilldown`, `lane_compare`, `blast_radius`) for incident/debug workflows.
3. **P1**: Restore stamping/deployment workflow pages (wizard + plan execution/rollback) for repeatable rollout operations.
4. **P1**: Restore onboarding/plane-assignment/ops pages to close day-1/day-2 operational loop.
5. **P2**: Re-expand GraphQL/API operational surfaces where needed by restored UI workflows.

## Bottom Line
Yes: there is still a meaningful functionality delta between original and V2, concentrated in workflow UI and operational lifecycle handling. V2 currently behaves like a strong schema/kernel + basic control plane, but not yet a full parity replacement for the original operator experience.
