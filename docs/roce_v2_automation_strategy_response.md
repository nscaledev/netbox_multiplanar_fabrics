# Response: Plugin Changes Implied by the RoCEv2 Automation Strategy

> **Historical note:** This response was written before the V2 data-model
> rewrite. Its plugin/platform ownership boundary remains directionally useful,
> but the concrete model examples use V1 names. Current V2 objects are
> documented in `docs/data_model.md`.

This document responds to [docs/roce_v2_automation_strategy.md](./roce_v2_automation_strategy.md)
from the perspective of the current `netbox_multiplanar_fabrics` repository and
the `netbox_plant_graph` plugin.

## Executive conclusion

If the strategy paper is adopted, the advisable changes inside this plugin are
deliberately limited.

The strategy is right that extremely large RoCEv2 GPU fabrics need an
AI-fabric operations platform. The plugin should be a dependency of that
platform, not the whole platform. Its correct role is the NetBox-side physical
topology, planning-intent, audit, and query layer. The policy compiler,
firmware compatibility engine, wave planner, execution system, telemetry
pipeline, workload correlation layer, artifact store, and health gates should
remain external.

The exact advisable plugin changes are:

| Priority | Change | Recommendation |
|---|---|---|
| 1 | Reframe or rename `DeploymentPlan` semantics | Advisable. Avoid collision with the strategy paper's rollout/wave-planning meaning. |
| 2 | Document the plugin/platform ownership boundary | Advisable. Make clear that this plugin is a topology and provisioning-intent component. |
| 3 | Publish a stable external topology-query contract | Advisable. External planners and validators need deliberate read-only contracts. |
| 4 | Add a planner-oriented impact-summary query | Advisable if an external wave planner will depend on this plugin. Keep it read-only. |
| 5 | Define lightweight external metadata conventions | Optional. Use references to adjacent systems, not first-class engines. |

No broad model expansion is advisable inside this plugin solely because the
strategy is adopted.

## Current fit

The plugin already fits the strategy paper's source-of-truth and topology-service
layers:

- `Fabric` and `FabricPlane` model the fabric and independent plane structure.
- `PlantNode`, `TerminationPoint`, `AttachmentUnit`, and `SignalLane` normalize
  NetBox inventory and cabling into a plane-aware, lane-aware graph.
- `CoarseEdge`, `FineEdge`, `TransferMap`, and `LaneMap` represent cable,
  breakout, shuffle, and internal mapping behavior.
- `PlaneMembership`, disjointness policy, contamination-domain reporting, blast
  radius, lane workspace, lane compare, plane audit, durable audit findings, and
  unresolved-topology summaries already provide topology validation inputs.
- Spatial, rack-population, assembly, breakout, and connection templates already
  support physical-fabric inventory provisioning and stamp provenance.
- GraphQL already exposes many machine-consumable operational queries, including
  typed lane/path, lane-set, health, policy, audit, and contamination-domain
  surfaces.

That is enough to make the plugin a useful topology dependency. It is not a
reason to make the plugin the orchestrator.

## Advisable plugin changes

### 1. Reframe or rename `DeploymentPlan`

This is the most important concrete change.

In this repository, `DeploymentPlan` currently means an inventory/provisioning
plan: a set of pending or completed `StampRecord` rows for spatial stamps, rack
population stamps, cable assembly stamps, passive assembly stamps, breakout
application, and rollback of objects created by those stamps.

In the strategy paper, a deployment or rollout plan means topology-aware live
production change sequencing with waves, gates, telemetry, workload impact,
firmware policy, pause, quarantine, and rollback.

Those are different concepts. If the strategy is adopted, keeping both concepts
under the same "deployment plan" name will create bad operator and API
semantics.

Recommended exact change:

- Rename `DeploymentPlan` to `ProvisioningPlan`.
- Rename `StampRecord` to `ProvisioningActionRecord` or `StampActionRecord`.
- Update related registry keys, routes, UI labels, API help text, GraphQL field
  names, and docs.
- If external clients already consume the existing names, keep compatibility
  aliases for at least one release and document the deprecation.

Lower-risk fallback if the model rename is too disruptive:

- Keep the model names for now.
- Change all visible labels and docs to "provisioning plan" and "stamp record".
- Add explicit help text that these are not live rollout or wave-planning
  constructs.

Acceptance criterion:

- A reader cannot confuse plugin stamp execution with strategy-level production
  rollout orchestration.

### 2. Document the ownership boundary

The strategy requires a larger platform. This repo should state exactly which
parts it owns.

Recommended exact change:

- Update `README.md`, `docs/data_model.md`, and this response document to state
  that `netbox_plant_graph` owns NetBox-side topology normalization, physical
  fabric planning metadata, graph/audit query surfaces, and provisioning-intent
  stamps.
- State that adjacent services own RoCE policy compilation, firmware tuple
  policy, scheduler/job correlation, telemetry ingestion, topology-aware wave
  planning, execution workers, health gates, artifact retention, and
  pause/quarantine/rollback control.
- When future docs mention operations workflows, distinguish "topology input for
  an external planner" from "the planner itself".

Acceptance criterion:

- The repo docs describe the plugin as a component of an AI-fabric operations
  platform, not as the complete platform.

### 3. Publish a stable external topology-query contract

The plugin already has useful topology functions, but an adopted strategy makes
them platform dependencies. That requires an explicit contract.

Recommended exact change:

- Add documentation for the supported external query surface.
- Prefer typed GraphQL fields for stable automation contracts.
- Keep older JSON GraphQL fields as convenience or compatibility surfaces, but
  do not make external automation depend on ambiguous JSON shapes.
- Add REST mirrors only where external systems cannot consume GraphQL cleanly.

The documented contract should cover:

- Path resolution: source, optional destination, optional plane, resolution
  level, returned path steps, and planes touched.
- Lane-set and lane-allocation summaries: expected lanes, present lanes, mapped
  lanes, missing lanes, plane consistency, and profile/mapping gaps.
- Fabric health: per-plane health, finding counts, and graph completeness.
- Plane audit and durable audit findings: active graph-quality issues relevant
  to preflight validation.
- Policy/contamination domains: domains, artifacts, plane pairs, exceptions, and
  representative targets.
- Unresolved-topology summaries: active unresolved state by fabric, owner,
  cause, impact, and representative object.
- Blast radius or impact summary: the candidate target's graph reachability and
  affected scopes.

Implementation detail:

- `lane_path`, `lane_set`, `lane_allocation_summary`, `fabric_health_typed`,
  `policy_summary`, `policy_dashboard`, `contamination_domains`,
  `audit_finding_search`, and `audit_workflow_summary` are already close to the
  desired contract style.
- `resolve_path`, `plane_audit`, and `blast_radius` are still JSON-oriented. If
  external automation will rely on them, add typed equivalents before declaring
  them stable.

Acceptance criterion:

- An external planner or validator can use documented read-only calls without
  scraping HTML views or reverse-engineering service internals.

### 4. Add a planner-oriented impact-summary query

The strategy's wave planner depends on impact and failure-domain summaries. The
plugin already computes pieces of this, but the pieces are not currently
packaged as a single planner-facing answer.

Recommended exact change:

- Add a read-only `impact_summary` service function.
- Expose it as a typed GraphQL query, for example `planner_impact_summary`.
- Optionally add a REST endpoint only if the external planner needs REST.

Minimum useful input:

- `target_registry_key`
- `target_id`
- `resolution` with values such as `attachment_unit` and `signal_lane`
- optional `fabric_id`
- optional `plane_id`

Minimum useful output:

- target object reference
- resolved fabric
- affected planes
- affected plant nodes
- affected termination points and attachment units
- affected sites, locations, racks, or rack groups when resolvable from NetBox
- tenant or ownership context already present on plugin or NetBox objects
- policy contamination domains touched
- active audit findings in the impacted scope
- active unresolved-topology summaries in the impacted scope
- graph completeness warnings that should prevent blind automation
- links or object references for drill-down into existing path, blast-radius,
  lane workspace, policy, and audit surfaces

Important boundary:

- This query should not know whether a live job is protected.
- It should not decide wave order.
- It should not read high-frequency telemetry.
- It should not approve, pause, roll back, or quarantine anything.

It should answer only: "What topology and graph-quality impact does this
candidate target have?"

Acceptance criterion:

- An external wave planner can call one plugin query to get topology impact
  context, then join that context with scheduler, telemetry, firmware, and
  policy systems outside the plugin.

### 5. Define lightweight metadata conventions for adjacent systems

The existing plugin models already have `metadata` JSON fields. If adopted
platform integrations need stable references, prefer conventions over new
engines.

Recommended optional change:

- Document reserved metadata keys for references to adjacent systems.
- Add helper functions or validators only if the conventions become widely used.
- Avoid new plugin-owned models unless the referenced data is genuinely
  topology data.

Reasonable metadata conventions:

```json
{
  "external": {
    "roce_profile_id": "roce-profile-spectrumx-800g-h100-v3.2",
    "compatibility_tuple_id": "tuple-h100-cx7-spectrumx-2026q2",
    "scheduler_partition_id": "slurm-partition-a",
    "telemetry_scope_id": "telemetry-scope-pod17-plane2"
  }
}
```

Where to allow these references:

- `Fabric` for fabric-wide profile, scheduler, or telemetry scope references.
- `FabricPlane` for plane-specific profile or telemetry references.
- `PlantNode` for device or host external references.
- `TerminationPoint` or `AttachmentUnit` for NIC/switch-port/rail-specific
  references.

Acceptance criterion:

- Adjacent systems can correlate plugin topology objects to their own records
  without making this plugin the owner of those records.

## Changes that are not advisable inside this plugin

### Do not add a RoCE policy compiler

The paper is right that RoCE policy should be compiled from structured intent
and promoted through lab, synthetic, canary, pilot, and production stages. That
compiler should live in the external operations platform.

At most, this plugin should reference an externally managed profile ID.

### Do not add a firmware compatibility engine

Firmware compatibility tuples are real operational objects, but they combine
GPU generation, NIC model and firmware, OFED/driver, CUDA/NCCL, switch ASIC,
NOS, SDK, optics, RoCE profile, promotion state, upgrade order, and rollback
order. That is not topology data.

At most, this plugin should reference an externally managed tuple ID.

### Do not add topology-aware wave planning

The plugin can answer topology-impact questions. It should not own live rollout
sequencing, canary expansion, protected-job exclusion, maintenance-window
policy, or pause criteria.

The current stamp/provisioning workflow is not an appropriate substrate for
production change waves.

### Do not add execution workers

CloudVision, NetQ, gNMI, NETCONF, Nornir, NAPALM, SSH, host agents, and similar
execution mechanisms belong in bounded external workers controlled by an
orchestrator.

The plugin should not execute live network or host changes.

### Do not add high-frequency telemetry ingestion

Switch/NIC/GPU/job telemetry requires ingestion pipelines, baselines,
correlation, anomaly detection, storage, and near-real-time health decisions.
That is an adjacent platform concern.

The plugin can expose topology identities that telemetry systems map onto.

### Do not add scheduler or job-control ownership

The strategy repeatedly asks which jobs are impacted or protected. That is a
valid platform requirement, but the scheduler integration should join external
job state to plugin topology identities.

The plugin should not become a Slurm, Kubernetes, or custom scheduler control
plane.

### Do not add an artifact store

Rendered configs, diffs, firmware manifests, telemetry snapshots, validation
reports, rollback bundles, execution logs, and rollout reports should be stored
by the operations platform.

Plugin stamp provenance remains useful, but it is not the operational black-box
recorder described in the strategy.

## Strategy requirement mapping

| Strategy position | Plugin response |
|---|---|
| Model the fabric beyond devices | Already aligned. Continue investing in graph, plane, lane, audit, and unresolved-topology quality. |
| Separate frontend and backend operational models | No major code change. Model backend RoCE fabrics as explicit `Fabric` scopes; keep frontend automation external or separate. |
| Treat RoCE policy as a compiled artifact | Do not implement compiler here. Reference external profile IDs only if needed. |
| Make firmware compatibility first-class | Do not implement tuple engine here. Reference external tuple IDs only if needed. |
| Use topology-aware wave planning | Support with stable read-only topology and impact queries. Do not build the planner here. |
| Use high-frequency cross-layer telemetry | Keep external. Provide topology identities for correlation. |
| Gate every rollout stage | Keep external. Let gates consume plugin topology/audit outputs as preflight inputs. |
| Preserve artifacts | Keep external. Plugin stamp provenance is not a rollout artifact store. |
| Integrate scheduler/job state | Keep external. Join job state to plugin topology references outside this plugin. |

## Recommended implementation order

1. Update docs and UI/API labels so current `DeploymentPlan` semantics are
   clearly provisioning/stamp semantics.
2. Document the plugin/platform ownership boundary in `README.md` and
   `docs/data_model.md`.
3. Add a topology-query contract document that names supported GraphQL fields,
   inputs, outputs, compatibility expectations, and non-goals.
4. Add typed GraphQL replacements for any JSON-only query that external
   automation will depend on, especially blast radius and plane audit.
5. Add the planner-oriented impact-summary service and typed GraphQL query.
6. Decide whether to perform the full model rename from `DeploymentPlan` to
   `ProvisioningPlan`; do it with compatibility aliases if external clients
   exist.
7. Add metadata conventions for external profile, tuple, scheduler, and
   telemetry references only when an adjacent system is ready to consume them.

## Final recommendation

Adopting the strategy paper should not make `netbox_plant_graph` larger in
scope. It should make the plugin's boundary sharper.

The plugin should become a reliable NetBox-side topology and provisioning-intent
component with stable read-only contracts for external automation. The rest of
the AI-fabric operations platform should be built beside it, not inside it.
