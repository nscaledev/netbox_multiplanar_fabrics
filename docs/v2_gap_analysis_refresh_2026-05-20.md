# Fresh Gap Analysis: Original Baseline vs Current V2

Date: 2026-05-20
Baseline (original plugin): commit `8d3a4ea`
Current target (v2): local working tree on branch `codex-mencken/v2-ground-up-rewrite` (HEAD `ccb5a9d`)

> **Status update, 2026-05-21:** This document is a point-in-time gap
> analysis. The highest-priority gaps identified here have since been addressed:
> REST mutation endpoints were reintroduced for workflow/finding/exception and
> stamp actions, GraphQL V2 was formally versioned as contract `2.0.0`, and
> stamp-preview naming compatibility was added. Menu discoverability was
> intentionally consolidated rather than restored to the V1 table-menu layout.

## Scope and Method

This pass compares baseline vs current across:

1. UI workflow routes/pages
2. Navigation and workflow discoverability
3. Registry/model table surfaces
4. REST API surface
5. GraphQL operational query surface

Floorplan integration is treated as intentionally excised unless noted as an explicit compatibility gap.

## Executive Summary

The major workflow/UI route gaps identified earlier are now effectively closed.
The remaining deltas are primarily contract and discoverability gaps:

1. REST mutation/action parity is still incomplete (baseline had API action endpoints that v2 does not expose).
2. GraphQL parity is still partial (several baseline operational fields remain absent/renamed, and the contract is now mostly JSON payloads).
3. Table-page navigation discoverability regressed (baseline had dynamic table menu groups for the full registry; current menu exposes a narrower subset directly).
4. Floorplan reconcile API is removed by design.

## 1) UI Pages and Workflow Routes

### Current Status

Workflow route parity is strong. Baseline workflow routes checked in `urls.py` are present in current routing (including:
`graph_overview`, `health`, `fabric_onboard`, `fabric_operations`, `fabric_assign_planes`, `audit_dashboard`, `audit_triage`, `policy_review`, `plane_audit`, `lane_drilldown`, `lane_compare`, `blast_radius`, `path_resolver`, stamping/deployment workflow routes, and finding/exception lifecycle routes).

### Remaining UI Gaps

1. **Menu/table discoverability gap**
   Baseline navigation supported workflow menus plus registry-driven table groups (`Plant Graph Tables` / grouped table menus).
   Current navigation is consolidated and does not expose the same breadth of table pages directly in menu structure.

2. **Legacy menu configurability gap**
   Baseline had plugin setting-driven top-level menu behavior (`top_level_menu` split/combined modes).
   Current menu is static and does not preserve that toggle behavior.

## 2) Registry / Model Surface Delta

Registry key count changed materially:

- Baseline object registry keys: `33`
- Current v2 object registry keys: `21`

### Baseline-only keys (replaced or removed in v2)

`assemblyconnectortemplate`, `assemblymappingtemplate`, `assemblytemplate`, `attachmentunit`, `auditfinding`, `auditfindingevent`, `auditrun`, `auditsuppression`, `breakoutprofile`, `coarseedge`, `connectiontemplate`, `deploymentplan`, `devicebreakouttemplate`, `devicechildinterfacespec`, `disjointnessexception`, `fabricplane`, `fineedge`, `graphbuildrun`, `lanemap`, `planemembership`, `plantnode`, `rackpopulationslot`, `rackpopulationtemplate`, `signallane`, `spatialplacement`, `spatialtemplate`, `spatialtemplatenode`, `stamprecord`, `terminationpoint`, `unresolvedstateobservation`, `unresolvedstatesummary`

### V2-only keys

`allocationruleset`, `architecturerole`, `auditevent`, `connectorposition`, `endpoint`, `fabricarchitecture`, `fabricnode`, `fibersegment`, `fiberstrand`, `operationrun`, `opticallane`, `pathintent`, `plane`, `stamprun`, `stamptemplate`, `strandtermination`, `suppressionrule`, `transferpattern`, `transportchannel`

Interpretation: this is a true data-model replacement (not 1:1 legacy model parity), which is expected but still a compatibility delta.

## 3) REST API Gap Analysis

### Baseline API endpoints missing in current

1. `floorplan/reconcile/` (`spatial-placement-reconcile`) — intentional removal.
2. Route-name compatibility mismatch for stamp preview:
   - Baseline route name: `stamp-preview`
   - Current route name: `stamps-preview`
   (path remains `/stamps/preview/`).

### Baseline API mutation/action surface not present in current v2 API

Baseline exposed API actions (via DRF `@action`) for:

1. Audit finding lifecycle mutations (`acknowledge`, `start-remediation`, `suppress`, `unsuppress`, `resolve`, `reopen`)
2. Disjointness exception lifecycle mutations (`approve`, `expire`, `reactivate`)
3. Deployment plan mutations (`execute`, `rollback`)
4. Template stamping mutations (assembly/spatial/rack-population stamp actions)

Current v2 restores workflow behavior primarily through UI views and summary/query APIs, but does not currently expose this same mutation/action API contract.

## 4) GraphQL Gap Analysis

### Baseline GraphQL fields missing in current

`assembly_template_detail`, `deployment_plan_detail`, `fabric_health`, `fabric_health_typed`, `lane_allocation_summary`, `lane_drilldown_typed`, `lane_path`, `lane_set`, `plane_audit`, `resolve_path`, `spatial_placements_by_scope`, `spatial_template_detail`, `stamp_preview`

### Current-only GraphQL fields

`audit_events`, `deployment_workflow_summary`, `fabrics`, `operation_runs`, `optical_lane_path`, `optical_lanes`, `stamp_runs`, `stamp_template_preview`, `suppression_rules`

### Contract-level delta

Baseline GraphQL had broader typed operational coverage in key areas; current GraphQL is improved but still centered on JSON-shaped operational payloads and renamed fields. If downstream consumers expect baseline field names/types, compatibility work remains.

## 5) Priority Recommendations (Point-in-Time)

1. **P0 — Decide API mutation parity target** - closed 2026-05-21.
   - If external automation should match baseline capability, reintroduce API mutation endpoints for lifecycle/stamp/execute/rollback actions.
   - Current state: REST mutation endpoints exist for workflow finding
     lifecycle, disjointness exception lifecycle, stamp execution/rollback, and
     operation-run summaries.

2. **P1 — Restore menu discoverability parity for registry tables** - closed by
   product decision.
   - Add grouped navigation for the full v2 registry table surfaces (or a discoverable index page with equivalent reachability).
   - Current state: V2 intentionally uses a consolidated operator menu plus
     `Model Catalog` rather than restoring the full V1 registry table menu.

3. **P1 — GraphQL compatibility policy** - closed 2026-05-21.
   - Either:
     - add baseline-compatible aliases/fields (`stamp_preview`, `resolve_path`, etc.), or
     - formally version and document v2 GraphQL contract breaks.
   - Current state: GraphQL V2 is formally versioned as `2.0.0`; baseline
     breaks are documented in `v2_graphql_contract_v2.md`.

4. **P2 — Optional route-name compatibility alias** - closed 2026-05-21.
   - Add `stamp-preview` name alias to remove reverse-name drift.
   - Current state: stamp-preview compatibility naming exists alongside the
     plural route name.

## Bottom Line

Compared to the original plugin baseline, v2 now has near-complete workflow page/route parity and solid operational test coverage.
As of the 2026-05-21 refresh, the action items above are no longer open gaps.
The remaining baseline deltas are intentional data-model/API-shape differences
and intentionally removed floorplan-related surfaces.
