# V2 Post-MVP Expansion Plan

Status: implementation-complete
Last updated: 2026-05-20

## Objective

Move V2 from MVP into production-grade operations by implementing all deferred scope:

1. Audit/suppression lifecycle systems.
2. Remove floorplan-plugin reliance and restore plugin-native spatial ownership.
3. Lane workspace and policy dashboards.
4. Madison-specific operational scripts as plugin core behavior.
5. Expanded GraphQL beyond minimal path query needs.

## Execution Rules

- Implement slices in order.
- Keep each slice shippable behind feature flags where practical.
- Run focused tests per slice; run full suite at end of Slice 5.
- Maintain V2 ownership boundary: no NetBox `Cable*` as modeled-fabric truth.
- Floorplan plugin (`netbox_floorplan`) is no longer an allowed runtime dependency.

## Slice 0: Foundation Alignment (2-3 days)

Goal: lock semantics and avoid rework.

Checklist:

- [x] Define suppression object model and state machine in docs.
- [x] Confirm plugin-native spatial ownership boundary for V2 objects.
- [x] Confirm GraphQL schema style and versioning approach.
- [x] Confirm Madison behavior as first-class module, not ad hoc script copy.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_cutover_decisions.md`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_post_mvp_expansion_plan.md`

Exit criteria:

- All decision gates in this doc are explicitly resolved.

## Slice 1: Audit and Suppression Lifecycle (5-7 days)

Goal: make path/policy operations safe, traceable, and reversible.

Implementation:

- [x] Add `SuppressionRule` model with scoped targets (`fabric`, `plane`, `lane`, `path_hop`, `policy`).
- [x] Add lifecycle fields (`status`, `reason`, `created_by`, `approved_by`, `expires_at`, `revoked_at`).
- [x] Add `AuditEvent` model for write-path provenance on stamp, policy evaluation, suppression transitions, and reconciliation.
- [x] Add suppression-aware service hooks in resolver and policy engines.
- [x] Add UI pages for suppression create/review/revoke and audit timeline browse.
- [x] Add API + GraphQL surfaces for suppression and audit reads.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/models.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/api/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/graphql/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/`

Exit criteria:

- Suppression can be created, enforced, expired, and revoked.
- Every mutating workflow emits auditable events.

## Slice 2: Floorplan Dependency Excision (4-6 days)

Goal: remove all runtime reliance on `netbox_floorplan` and preserve spatial workflows natively.

Implementation:

- [x] Remove floorplan compatibility checks, bridge services, and plugin URL handoffs.
- [x] Remove floorplan sync/reconcile execution paths from spatial stamping and plan execution.
- [x] Restore plugin-native coordinate/spatial workflows and templates as first-class operator surfaces.
- [x] Remove floorplan-specific reporting fields from stamp metadata and UI.
- [x] Remove floorplan-specific plugin test setup and replace with plugin-native spatial tests.
- [x] Remove floorplan package/config references from docs and local-dev setup.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/floorplan_compat.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/floorplan_bridge.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/spatial_stamp.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/plan_execution.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/netbox_configuration.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/`

Exit criteria:

- Plugin runs cleanly with `netbox_floorplan` absent from NetBox `PLUGINS`.
- Spatial stamp and plan workflows remain functional using plugin-native pages only.

## Slice 3: Expanded GraphQL Surface (4-6 days)

Goal: expose V2 objects and computed signals for UI, automation, and external consumers.

Implementation:

- [x] Add GraphQL types and filters for architecture, fabric, lane, strand, termination, transfer map, stamp run, suppression, and audit event.
- [x] Add computed resolvers for lane health/status, effective suppression, and path summary.
- [x] Add pagination/filter contracts for high-cardinality entities.
- [x] Add resolver tests for correctness and query-cost guardrails.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/graphql/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/`

Exit criteria:

- Dashboard and automation use-cases can be fulfilled without direct SQL or ad hoc service imports.

## Slice 4: Lane Workspace + Policy Dashboards (6-8 days)

Goal: provide operator-native visibility and policy control at lane/path level.

Implementation:

- [x] Add lane workspace page with scoped filters (fabric/plane/role/status/suppression).
- [x] Add path drill-down panel showing hop chain, lambdas, endpoint devices/interfaces, and active suppressions.
- [x] Add policy dashboard with rule inventory, effective scope, last-evaluated state, and violations.
- [x] Add bulk actions for policy re-evaluation and suppression propose/revoke.
- [x] Add stamp-run provenance links from workspace entities.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/forms.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/navigation.py`

Exit criteria:

- Operator can investigate lane/path state and manage policy workflow end-to-end in UI.

## Slice 5: Madison Operationalization as Plugin Core (5-7 days)

Goal: make Madison operations reproducible and supported as core behavior.

Implementation:

- [x] Convert Madison scripts into plugin-native services/management commands with typed inputs.
- [x] Introduce operation profiles (`madison_default`, `generic_roce`) so Madison logic is core but selectable.
- [x] Add idempotent execution and provenance recording in `StampRun` or dedicated operation run model.
- [x] Add operator workflow pages for launching and reviewing operations.
- [x] Add rollback/safe-retry semantics for partially completed runs.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/management/commands/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/models.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/`

Exit criteria:

- Madison operational workflows execute from plugin UI/API/commands with repeatable outcomes.

## Slice 6: Plugin-Native Spatial UX Completion (4-6 days)

Goal: finish spatial operator UX without external floorplan dependencies.

Checklist:

- [x] Deliver coordinate layout workspace with site/location/rack scoping.
- [x] Add passive/active endpoint overlays relevant to lane/path workflows.
- [x] Add spatial-to-lane and lane-to-spatial deep links.
- [x] Add reconciliation/validation tools for inconsistent spatial metadata.
- [x] Add focused UI tests for operator navigation and update flows.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/views.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/forms.py`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/templates/netbox_plant_graph/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/`

Exit criteria:

- Spatial operations are fully available in plugin-native UI with no external plugin handoff.

## Slice 7: Hardening and Release Gate (3-4 days)

Goal: ship safely.

Checklist:

- [x] Full plugin test suite and targeted performance runs.
- [x] Migration rehearsal on a copy of production-like data.
- [x] Documentation refresh: operator runbook + API/GraphQL usage.
- [x] Cutover checklist signoff.

Primary files:

- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/v2_cutover_runbook.md`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/docs/`
- `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/tests/`

Exit criteria:

- Release candidate tagged with green full-suite + validated migration rehearsal.

## Decision Gates (Requires Explicit Signoff)

1. Suppression scope semantics:
   - Allow suppression at `lane`, `path_hop`, and `policy` concurrently, with deterministic precedence (most-specific wins).
2. Suppression expiry behavior:
   - Auto-expire by `expires_at` and retain historical audit trail; no hard delete.
3. GraphQL contract:
   - Add first-class GraphQL for all V2 entities now, keep REST for workflow actions.
4. Spatial ownership mode:
   - Plugin-native spatial editor/workflows are the only supported spatial interface.
5. Madison packaging:
   - Ship as core module with profile toggles (default `generic_roce`, optional `madison_default`).

## Suggested Implementation Order

1. Slice 0
2. Slice 1
3. Slice 2
4. Slice 3
5. Slice 4
6. Slice 5
7. Slice 6
8. Slice 7

## Success Metrics

- Suppression and audit coverage for 100% of mutating V2 workflows.
- Lane workspace load under 2 seconds for 10k+ lanes with scoped filters.
- Plugin-native spatial workflows run with no external plugin dependency.
- Madison operation runs are idempotent and replay-safe.
- GraphQL queries satisfy dashboard/workflow needs with no direct SQL consumers.
