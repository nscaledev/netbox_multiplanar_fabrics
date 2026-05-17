# NetBox Plant Graph Plugin

Lane-aware, plane-aware topology extension for multi-plane RoCE fabrics.

## Overview

This NetBox plugin layers a **plant-graph model** on top of native NetBox inventory and cabling. It is intended for environments with GPU clusters using multi-plane RoCEv2 fabrics, shuffle cables/modules, and 800G ports subdivided into 200G child transport units.

The plugin maintains a derived, normalized, graph-oriented topology layer that advanced consumers can query for automation, troubleshooting, validation, and visualization.

## Current Coverage

The current implementation is centered on **attachment-unit resolution**, with an initial **signal-lane** slice:

- rebuilds derive topology from NetBox `CablePath` objects
- cable profile expansion uses NetBox cable profile position mapping at sync time
- channelized parent interfaces are mapped onto child-interface attachment units
- plane memberships sourced from child interfaces are propagated across passive attachment hops
- signal lanes, signal-lane `FineEdge`s, and `LaneMap`s are materialized for channelized topologies
- resolver support includes both attachment-unit and signal-lane path resolution
- operational pages expose graph overview, health, audit dashboard, path resolution, plane audit, lane drilldown, lane compare, lane workspace, and blast-radius results in the plugin UI
- ambiguous blank-profile fanout cables are left unresolved in sync and surfaced by plane audit as missing-profile findings
- profile-derived breakout mappings now require explicit child interfaces; when those are missing, sync leaves the path unresolved and plane audit reports a missing-child-interface finding
- profile-derived breakouts with only a partial child-interface set now materialize only the positions that exist, and plane audit reports an incomplete-child-interface-set finding
- profile-derived breakouts with missing peer termination positions are now surfaced by plane audit as partial-profile-mapping findings
- explicit child-interface attachment units that never participate in any derived path are now surfaced by plane audit as orphaned-attachment-unit findings
- cabled passive front/rear ports without `PortMapping` coverage are surfaced by plane audit as missing-port-mapping findings
- operational resolver/blast-radius flows now accept core NetBox `Interface`, `FrontPort`, and `RearPort` objects directly, and object-page badges provide shortcuts into those workflows
- lane drilldown is available from object badges, operational pages, detail cards, and GraphQL for lane-first inspection of materialized `SignalLane` objects
- fabric health is available from operational UI, detail cards, and GraphQL as an on-demand summary over audit and graph state
- typed lane-first GraphQL now exists alongside the legacy JSON operational fields, including `lanePath`, `laneDrilldownTyped`, and `fabricHealthTyped`
- grouped lane-set and lane-allocation summaries now exist as reusable graph services, with typed GraphQL exposure via `laneSet` and `laneAllocationSummary`
- a dedicated lane workspace page now exposes grouped lane views by attachment, node/passive artifact, and plane, and detail pages for `CoarseEdge`, `PlantNode`, `TerminationPoint`, and `FabricPlane` now surface lane coverage cards
- the lane workspace now has a typed composition service, normalized `group_by` query handling, smarter node/plane deep links, and a primary-grouping UI that promotes the selected grouping while keeping supporting views in context
- the richer lane workspace Phase 0/1/2 slice is now in place: the workspace has a normalized query/state contract for `mode`, `group_by`, `focus`, `group_key`, `lane_index`, and `plane_id`, renders as a composed shell with query/summary/main/context regions, supports exact-lane mode inside the page, preserves selected-group state, and accepts `SignalLane`/drilldown handoffs directly into lane mode
- the richer lane workspace Phase 3 slice is now in place: `group_by=path` now collapses repeated representative path shapes into deterministic grouped rows, `path_lane_index` pins representative-lane selection when needed, the workspace renders server-side textual representative-path detail without requiring JavaScript, and `CoarseEdge`/`FabricPlane` workspace deep links are now narrower by default
- the richer lane workspace Phase 4 slice is now in place: the workspace query contract now carries optional compare/backlink/export state, audit findings and detail cards can deep-link into narrowed workspace states with `source_finding_id`, the workspace now renders scoped related-findings, next-actions, compact compare context, and server-side CSV/JSON exports for the visible scope
- the richer lane workspace Phase 5 slice is now in place: representative path detail now has an additive SVG path-canvas enhancement built from the existing textual stage model, workspace navigation/focus targets are keyboard-friendly, grouped/lane/path tables use mobile-safe overflow wrappers, and the shell now degrades cleanly without JavaScript because the canvas carries no unique semantics
- plane-audit findings now render lane-aware impact summaries, remediation hints, and guided next-action links for the highest-value unresolved and cross-plane cases
- compare-mode lane review now exists as both an operational page and a typed GraphQL query via `laneCompare`, with regression summaries for completeness, mapping symmetry, and plane isolation plus policy contamination-domain deltas and policy regression summaries
- persistent audit foundations now exist via durable `AuditRun` records and fingerprinted `AuditFinding` upsert/resolution lifecycle support for full-fabric audits, while the existing live audit page remains on-demand
- durable finding workflow state now includes acknowledged and in-progress statuses, event history via `AuditFindingEvent`, and explicit audit-finding detail actions for acknowledge, start-remediation, resolve, and reopen
- durable finding suppressions now exist via `AuditSuppression`, including detail-page suppress/unsuppress actions, expiration handling, and an audit-retention job that can expire suppressions and prune stale inactive runs/events
- the plugin API now exposes explicit audit-finding workflow action endpoints for acknowledge, start-remediation, resolve, reopen, suppress, and unsuppress on top of the generated read-only durable object APIs
- durable audit reporting now exists via an audit dashboard plus typed GraphQL `auditWorkflowSummary`, with counts, recent runs/events, stale findings, and expiring suppressions
- explicit durable GraphQL query surfaces now exist for audit finding search/detail and audit run timelines via `auditFindingSearch`, `auditFindingDetail`, and `auditRunTimeline`
- durable finding list filtering now includes suppression-state and minimum-age filters, and the audit dashboard plus audit run/suppression detail views now deep-link operators into the relevant durable workflow surfaces
- recent-churn reporting is now explicit in both the audit dashboard and typed GraphQL workflow summary, with 7-day and 30-day opened/reopened/resolved/auto-resolved/suppressed counts
- explicit REST durability reporting endpoints now exist for workflow summary, filtered finding search, finding detail, and run timelines under the plugin API, instead of relying only on generated CRUD plus workflow actions
- the live plane-audit page now bridges into durable workflow state when a matching persisted finding exists, including current status, suppression context, detail links, and workflow actions inline with the live result
- audit workflow history is now easier to drill into from the dashboard via filtered event-history links from recent churn metrics and a fabric-scoped recent-events shortcut
- graph rebuilds now persist durable `GraphBuildRun` rows with scope, trigger mode, completion status, graph stats, revision metadata, generated list/detail/API/GraphQL surfaces, and retention pruning alongside the audit-retention flow
- optional unresolved-topology durability now has its Phase B foundation: full-fabric rebuilds can persist canonical `UnresolvedStateSummary` and append-only `UnresolvedStateObservation` rows for missing cable profiles, missing/incomplete child-interface sets, missing passive `PortMapping` coverage, normalized profile-mapping failures (`profile_error`, `profile_returned_none`, `missing_peer_position`), and orphaned attachment units, with reopen/resolve lifecycle driven by rebuild fingerprints instead of audit cadence
- unresolved-topology durability now also has its Phase C read surfaces: generated read-only list/detail/API/GraphQL surfaces exist for unresolved summaries and observations, health and lane workspace now surface durable unresolved state independently from audit-finding counts, and fabric/plane/unresolved-summary detail pages now link operators into the rebuild-driven unresolved backlog without forcing an audit-dashboard-first workflow
- unresolved-topology durability now also has its Phase D audit-linkage/reporting slice: persistent audit findings can carry explicit related-summary fingerprints, live plane-audit rows and audit-finding detail pages now link back to matching durable unresolved summaries when the mapping is clear, and the audit dashboard now exposes separate unresolved-topology widgets for active backlog, aging summaries, multi-build recurrence, reopen counts, top causes, and oldest active unresolved cohorts
- unresolved-topology durability now also has its Phase E hardening slice: incremental/site-scoped refreshes explicitly skip unresolved-summary resolution for non-comparable partial builds, and optional unresolved overview/dashboard caching now exists behind plugin settings keyed by fabric graph revision plus latest summary mutation timestamp so cache remains deterministic and non-authoritative
- policy/disjointness work now includes extracted policy-evidence services for passive artifact sharing and cross-plane edge bridges, deterministic contamination-domain construction, enriched audit metadata (`rule_id`, plane-pair evidence, contamination-domain keys), a Policy Review page with exception coverage/drift reporting, and explicit `DisjointnessException` lifecycle support for approved topology exceptions
- typed policy summary, policy dashboard, and contamination-domain GraphQL queries now exist, the audit dashboard now surfaces policy widgets for highest-risk domains, plane-pair rollups, exception coverage, and oldest active durable policy findings, and `Fabric`, `FabricPlane`, and passive `PlantNode` detail pages now surface policy-oriented summary cards alongside the existing lane-first cards
- optional policy-reporting caching now exists behind plugin settings, keyed by fabric, policy mode, rule catalog version, and a rebuild-stamped graph revision token on `Fabric.metadata`, so cached policy evaluation remains a performance optimization rather than a semantic dependency
- operational path, audit, and blast-radius pages now render direct object links, contextual metadata, and guided next-action links for follow-on investigation
- the plugin menu now exposes the operational pages directly instead of leaving them as URL-only utilities

The test suite includes a multiplane shuffle fixture with one 800G host interface, four 200G child interfaces, one shuffle module with `PortMapping` rows, and one leaf switch.

## Requirements

- NetBox 4.2.3+ (supported lines: 4.2.x and 4.5.x; 4.3.x and 4.4.x are not validated)
- Python 3.12+
- `netbox-floorplan-plugin` 0.9.x for the floorplan/layout integration work

## Installation

```bash
pip install netbox_plant_graph
```

Install and enable both plugins in your NetBox `configuration.py`:

```python
PLUGINS = ['netbox_floorplan', 'netbox_plant_graph']
```

The floorplan plugin owns the operator-facing 2D site/location layout
experience. `netbox_plant_graph` retains ownership of template authoring,
spatial/rack/assembly stamping, connection templates, deployment-plan
workflow, and read-only `SpatialPlacement` planning metadata that is not
represented by floorplan canvas state.

When a spatial stamp creates or updates racks, the plugin can push managed rack
positions into the corresponding floorplan automatically. After an operator
edits those managed rack objects in the floorplan UI, use the dedicated
reconciliation endpoint to pull the floorplan's `x`/`y`/rotation values back
into `SpatialPlacement` metadata without reopening generic placement CRUD:

```text
POST /api/plugins/netbox_plant_graph/floorplan/reconcile/
```

Payload:

```json
{"scope_type": "site"|"location", "scope_id": 123, "create_missing": false}
```

This reconciliation path updates managed rack placements only. Existing
`position_z`, reference-frame fields, and arbitrary placement metadata remain
owned by `netbox_plant_graph`.

## Development

See [LOCAL_DEV_SETUP.md](LOCAL_DEV_SETUP.md) for local development environment setup.
The `devrun` wrapper uses a repo-specific Docker Compose project name so its PostgreSQL and Redis volumes stay isolated from other NetBox plugin repos.
The compose stack intentionally avoids fixed `container_name` values so multiple local NetBox plugin repos do not collide on global Docker container names.

## License

Apache License 2.0
