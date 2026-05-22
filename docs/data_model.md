# netbox_plant_graph V2 Data Model

This document describes the **current V2 architecture** implemented in
`netbox_plant_graph`, including:

- the persistent model schema,
- how paths are represented and resolved,
- what is plugin-native vs. NetBox-anchored,
- and the key invariants the code enforces.

It intentionally replaces V1-era terminology (`PlantNode`, `AttachmentUnit`,
`LaneMap`, etc.) with the live V2 model surface.

---

## 1) V2 Architecture Summary

V2 models multiplanar optical fabrics with plugin-native topology objects,
registry-backed architecture blueprints, and NetBox object anchors:

1. **Architecture definitions and blueprints** describe reusable topology
   semantics, stamp-time parameter contracts, and transfer geometry.
2. **Fabric instances** materialize those semantics for a concrete deployment.
3. **Endpoint graph primitives** model ports, MPO positions, strands, and maps.
4. **Optical lanes** are transceiver-local signaling constructs.
5. **Transport channels** represent 200gbps child/sub-interface groupings on
   physical OSFP endpoints and map those channels onto MPO positions.
6. **Path resolution** traverses connector-position connectivity to produce
   end-to-end lane paths across any number of hops.
7. **Control-plane objects** track stamping, architecture workspaces,
   onboarding workspaces, suppressions/exceptions, audit lifecycle events,
   import reports, and operational impact reports.

V2 is plugin-native for modeled fabric connectivity and does not require
NetBox-native cable/path objects as modeled-fabric source of truth.

---

## 2) Persistent Model Inventory

### 2.1 Architecture definition layer

1. `FabricArchitecture`
2. `ArchitectureRole`
3. `TransferPattern`
4. `AllocationRuleSet`
5. `StampTemplate`
6. `ArchitectureWorkspace`
7. `ArchitectureSourceArtifact`
8. `ArchitectureDesignComponent`
9. `ArchitectureValidationRun`
10. `ArchitecturePublishPlan`

Purpose:

- define reusable architecture semantics, fabric class, roles, transfer
  behavior, allocation rules, and stamping templates independently from a
  concrete fabric instance.
- persist architecture blueprints keyed by `slug` + `version`, with
  `FabricArchitecture.fabric_class` classifying the intended fabric family
  (`roce_backend`, `ethernet_frontend`, `management`, or `storage`).
- provide a registry-backed source for built-in blueprints and imported
  blueprint bundles. The active built-ins are
  `roce-4-plane-gb300-2x2-shuffle`, `roce-4-plane-h100-direct-attach`, and
  `roce-8-plane-gb300-2x2-shuffle`.
- provide a first-class architecture workspace for collecting blueprint/source
  artifacts, normalizing architecture components, validating schema/import
  compatibility, generating publish plans, approving changes, and publishing
  into `FabricArchitecture` plus child architecture rows.

Architecture workspace rows:

1. `ArchitectureWorkspace`: durable design workspace for a new or revised
   architecture blueprint, keyed by unique `slug`, target blueprint
   `target_slug`/`target_version`, `fabric_class`, optional base architecture,
   optional published architecture, current publish plan, source summary,
   validation summary, and metadata.
2. `ArchitectureSourceArtifact`: referenced or pasted source data for the
   workspace. Current artifact types include blueprint bundles, architecture
   schema JSON, stamp-template JSON, manual/API payloads, notes, diagrams,
   spreadsheets, and other design artifacts.
3. `ArchitectureDesignComponent`: normalized workspace component with
   `kind`, `natural_key`, `desired_state`, provenance, validation status, and
   messages. Important component kinds are
   `fabric_architecture_blueprint`, `architecture_role`,
   `transfer_pattern`, `allocation_rule_set`, `parameter_schema`,
   `required_device_types`, and `stamp_template`.
4. `ArchitectureValidationRun`: durable preflight result for schema
   validation, blueprint parameter validation, device-type compatibility
   warnings, and import/reconcile dry-run output.
5. `ArchitecturePublishPlan`: stable-hash publish plan containing the exact
   import payload to apply, validation summary, import plan, approval metadata,
   result JSON, and lifecycle status.

Architecture workspace publish uses the existing import/reconciliation engine
for `fabric_architecture_blueprint` items, so successful publication creates or
updates `FabricArchitecture`, `ArchitectureRole`, `TransferPattern`,
`AllocationRuleSet`, and associated `StampTemplate` rows through the same path
used by import preview/apply.

### 2.2 Fabric topology layer

1. `Fabric`
2. `Plane`
3. `FabricNode`
4. `Endpoint`
5. `ConnectorPosition`
6. `TransportChannel`
7. `TransportChannelPositionMap`
8. `CableAssembly`
9. `FiberSegment`
10. `FiberStrand`
11. `StrandTermination`
12. `OpticalLane`
13. `TransferMap`
14. `PathIntent`

Purpose:

- represent the physical/logical fabric graph used for lane-aware path
  traversal and multiplanar policy evaluation.

### 2.3 Operational/control-plane layer

1. `StampRun`
2. `SuppressionRule`
3. `AuditEvent`
4. `OperationRun`
5. `ArchitectureWorkspace`
6. `ArchitectureSourceArtifact`
7. `ArchitectureDesignComponent`
8. `ArchitectureValidationRun`
9. `ArchitecturePublishPlan`
10. `OnboardingWorkspace`
11. `OnboardingSourceArtifact`
12. `OnboardingDesignItem`
13. `OnboardingPrerequisite`
14. `OnboardingPlan`
15. `OnboardingExecutionStage`
16. `OnboardingObjectLink`

Purpose:

- provide execution provenance, policy suppression lifecycle, audit workflow
  event history, operation run tracking, a persistent first-class architecture
  workspace for blueprint publication, and a persistent first-class onboarding
  workspace for source artifacts, staged design inventory, prerequisite
  resolution, unified plans, staged execution, readiness, publish, and handoff
  records.

---

## 3) NetBox Anchors (What is joined to core objects)

V2 uses NetBox object references where appropriate:

1. `Fabric.tenant` -> `tenancy.Tenant` (optional)
2. `Fabric.scope_site` -> `dcim.Site` (optional)
3. `Fabric.scope_location` -> `dcim.Location` (optional)
4. `FabricNode.source` -> Generic FK (`source_type` + `source_id`)
5. `Endpoint.source` -> Generic FK (`source_type` + `source_id`)
6. `TransportChannel.source_subinterface` -> `dcim.Interface` (optional)
7. `CableAssembly.site` and `FiberStrand.cable_site` -> `dcim.Site`
8. `AuditEvent.actor`, `SuppressionRule.created_by/approved_by`,
   `OperationRun.initiated_by` -> auth user model
9. `ArchitectureWorkspace.created_by/owner`,
   `ArchitectureValidationRun.executed_by`, and
   `ArchitecturePublishPlan.generated_by/approved_by` -> auth user model
10. `ArchitectureWorkspace.base_architecture/published_architecture` ->
   `FabricArchitecture`
11. `OnboardingWorkspace.site/location/tenant` -> NetBox `Site`, `Location`,
   and `Tenant` scope
12. `OnboardingWorkspace.created_by/owner`,
   `OnboardingPlan.generated_by/approved_by` -> auth user model
13. `OnboardingDesignItem.planned_object_type`,
   `OnboardingPrerequisite.resolved_object_type`, and
   `OnboardingObjectLink.object_type` -> content type anchors for staged,
   resolved, and applied objects

The plugin owns the topology graph itself (nodes/endpoints/strands/maps/lanes),
while anchoring select objects back to NetBox inventory as needed.

---

## 4) Core Topology Semantics

### 4.1 Node and endpoint hierarchy

1. `FabricNode` is a topology-bearing element within one `Fabric`.
2. `Endpoint` belongs to a `FabricNode`, with optional `parent` to support
   endpoint trees (for example, transceiver port -> child MPO endpoint).
3. `ConnectorPosition` belongs to an `Endpoint` and models discrete connector
   positions (for example, MPO position 1..12).

### 4.2 Fiber graph primitives

1. `CableAssembly` is the first-class physical cable row with:
   - `site` + `cable_id` (unique per site),
   - manufacturer / serial / model / description,
   - optional `parent_cable` for trunk -> child jumper hierarchy.
2. `FiberSegment` connects two endpoint containers (`a_endpoint`, `b_endpoint`)
   inside one fabric.
3. `FiberStrand` indexes individual strands inside a segment, and references a
   cable assembly identity via `cable_site` + `cable_id`.
   In the current phase this pair is intentionally nullable to support
   topology-first modeling before final cable plant assignment.
4. `StrandTermination` normalizes strand-to-MPO termination with:
   - `strand`
   - `mpo_endpoint`
   - `mpo_position`

This is the normalized strand/MPO-position join table used by resolver logic.

### 4.3 Transport channel semantics

`TransportChannel` represents a logical 200gbps transport channel on a
fabric endpoint. In the GB300/OSFP architecture this is the plugin-side
counterpart to a NetBox child/sub-interface such as `osfp1/1`.

Key fields:

1. `endpoint`: the plugin endpoint for the physical port/cage.
2. `plane`: optional plane identity for plane-aware policy and reporting.
3. `source_subinterface`: optional NetBox child interface anchor.
4. `channel_index`: local channel number within the endpoint.
5. `speed_gbps`: nominal channel rate.

`TransportChannelPositionMap` is the normalized mapping between a transport
channel and the MPO positions that implement it:

1. `channel`
2. `mpo_endpoint`
3. `mpo_position`
4. `metadata`

For the built-in GB300 2x2 shuffle architecture, each OSFP owns two MPO12
children and four 200gbps transport channels:

| Channel | MPO | Positions |
| --- | --- | --- |
| 1 | 1 | 1, 12, 2, 11 |
| 2 | 1 | 3, 10, 4, 9 |
| 3 | 2 | 1, 12, 2, 11 |
| 4 | 2 | 3, 10, 4, 9 |

MPO positions 5-8 are dark in this model.

### 4.4 Optical lane semantics (endpoint-local)

`OpticalLane` is a transceiver-local signaling construct, not an end-to-end
path row. It carries:

1. owning `endpoint` (transceiver-side endpoint),
2. local breakout identity (`lane_index`, `local_mpo_index`),
3. direction (`send` or `receive`, local to that endpoint),
4. wavelength (`wavelength_nm`),
5. local termination anchor (`local_mpo_endpoint`, `local_mpo_position`),
6. optional grouping (`pair_key`) and optional `TransportChannel`/`Plane`.

This allows distinct wavelengths to coexist on the same local MPO position.

### 4.5 Passive/internal remap semantics

`TransferMap` maps one connector position to another within a fabric graph
context and supports both:

1. node-owned transfer behavior (`owner_node`), and
2. segment-owned transfer behavior (`owner_segment`),

with exactly one owner required by model validation.

`TransferMap` is the explicit mechanism for shuffle/polarity/breakout behavior
at connector-position resolution.

The seeded `shuffle_2x2` transfer pattern models a true cassette transform:
each front MPO fans out across both rear MPOs and applies the key-down roll on
the rear side (`dst_position = 13 - base_dst_position` for MPO12).

### 4.6 Path intent semantics

`PathIntent` captures desired source/destination channel or endpoint intent plus
a `selector` JSON for intent metadata and targeting.

---

## 5) End-to-End Path Representation in V2

V2 does **not** persist a dedicated end-to-end path table per optical lane.
Instead:

1. optical endpoints are represented by `OpticalLane` rows,
2. physical/transfer connectivity is represented by:
   - `StrandTermination` (strand hops),
   - `TransferMap` (position remaps),
3. end-to-end lane paths are resolved on demand by traversal.

Resolver entrypoint:

- `services/resolver.py::resolve_optical_lane_path()`

Traversal behavior:

1. start at source lane local MPO position,
2. walk graph neighbors:
   - strand peers via `StrandTermination`,
   - transfer neighbors via `TransferMap`,
3. match candidate destination lane(s) by:
   - opposite direction,
   - same wavelength,
   - compatible plane constraints (when present),
4. return ordered path steps (`PathStep`) with object references and metadata.

Because traversal is graph-based, arbitrary additional hops are supported
without a schema change.

---

## 6) Important Invariants and Constraints

The V2 model enforces several critical invariants:

1. `FabricArchitecture`: unique (`slug`, `version`)
2. `ArchitectureRole`: unique (`architecture`, `slug`)
3. `TransferPattern`: unique (`architecture`, `slug`)
4. `AllocationRuleSet`: unique (`architecture`, `slug`)
5. `Plane`: unique (`fabric`, `plane_number`)
6. `FabricNode`: unique (`fabric`, `address`)
7. `Endpoint`: unique (`fabric`, `address`)
8. `ConnectorPosition`: unique (`endpoint`, `position_number`)
9. `TransportChannel`: unique (`endpoint`, `channel_index`)
10. `TransportChannelPositionMap`: unique (`channel`, `mpo_position`)
11. `CableAssembly`: unique (`site`, `cable_id`)
12. `FiberStrand`: unique (`segment`, `strand_index`)
13. `StrandTermination`: unique (`strand`, `mpo_position`) and unique
    (`mpo_position`) for single occupancy
14. `OpticalLane`: unique (`endpoint`, `lane_index`, `direction`)

Additional model-level validation includes:

1. `FiberSegment` endpoints must be distinct and in the same fabric.
2. `StrandTermination.mpo_position` must belong to `mpo_endpoint`.
3. `StrandTermination` endpoint fabric must match strand fabric.
4. `OpticalLane` endpoint/MPO anchors/plane/channel must be fabric-consistent.
5. `OpticalLane.local_mpo_endpoint` (when parented) must be child of
   `OpticalLane.endpoint`.
6. `TransportChannelPositionMap` endpoint/MPO anchors must be
   fabric-consistent, and parented MPO endpoints must belong to the channel
   endpoint.
7. `TransferMap` requires exactly one owner (`owner_node` xor `owner_segment`)
   and distinct source/destination positions.
8. `FiberStrand` `cable_site` and `cable_id` must be provided together, and the
   referenced cable assembly must exist.
9. `SuppressionRule` plane/lane scope must align to the same fabric.

Indexes optimized for lane lookup:

1. (`fabric`, `lane_index`)
2. (`fabric`, `pair_key`)
3. (`fabric`, `wavelength_nm`)

---

## 7) Control-Plane / Workflow Data Semantics

### 7.1 Stamping lifecycle

1. `StampTemplate` stores executable template JSON.
2. `StampRun` records execution status, parameters, result, errors, metadata.

The operator-facing stamp path is V2.5:

1. `preview_stamp_template_v25()` resolves the template, selects a registered or
   persisted blueprint, validates stamp parameters/device-type compatibility,
   and returns a dry-run change plan.
2. `apply_stamp_template_v25()` reuses the preview gate, blocks on error
   severity issues, applies through the registry-aware stamp executor, and
   persists a `StampRun`.
3. Saved `StampRun` pages expose retry classification and rollback
   preview/apply actions through the V2.5 service layer.

### 7.2 Policy suppressions and disjointness exceptions

`SuppressionRule` is the persistent suppression/exception substrate and can
scope to:

1. fabric-wide,
2. plane-specific,
3. optical-lane-specific,
4. path-hop-specific (`path_hop_object_type` + `path_hop_object_id`),
5. policy-key-specific.

### 7.3 Audit/event lifecycle

`AuditEvent` is the persistent event log for stamping, path resolution, policy
evaluation, suppression changes, and operation workflow events.

Important implementation detail:

- v2 workflow findings are represented through `AuditEvent` records with
  `event_type='policy_eval'` and lifecycle metadata, rather than a dedicated
  `AuditFinding` table in the v2 schema.

### 7.4 Operations lifecycle

`OperationRun` tracks execution profile, status, dedupe key, parameters, result,
error detail, metadata, and timing fields.

### 7.5 First-class architecture workspace lifecycle

`ArchitectureWorkspace` is the durable container for designing a new
architecture blueprint, revising a blueprint version, or preparing a blueprint
bundle for publication. It is separate from fabric onboarding: architecture
workspaces publish reusable architecture semantics; onboarding workspaces
instantiate those semantics for a specific site/fabric.

The architecture workspace flow is:

1. attach source artifacts (`ArchitectureSourceArtifact`) from blueprint
   bundles, schema JSON, stamp-template JSON, diagrams, spreadsheets, manual
   entries, notes, or API payloads,
2. normalize those artifacts into architecture components
   (`ArchitectureDesignComponent`) with desired state and provenance,
3. validate the workspace (`ArchitectureValidationRun`) against the executable
   architecture schema, blueprint parameter schema, optional device-type
   compatibility, and an import/reconcile dry-run,
4. generate a stable-hash `ArchitecturePublishPlan` carrying the exact
   `fabric_architecture_blueprint` import payload to apply,
5. approve the plan after warnings are acknowledged,
6. publish the plan through the import/reconciliation engine, creating or
   updating `FabricArchitecture`, `ArchitectureRole`, `TransferPattern`,
   `AllocationRuleSet`, and `StampTemplate` rows,
7. fetch handoff JSON for review, automation, or audit records.

This workspace is the intended UI/API path for turning site design documents or
directly entered architecture semantics into persistent blueprint rows without
bypassing validation or provenance.

### 7.6 First-class onboarding lifecycle

`OnboardingWorkspace` is the durable container for a net-new fabric onboarding
effort or a major staged fabric change. It links the intended fabric identity,
fabric class, selected architecture, optional NetBox site/location/tenant scope,
source summary, current plan, readiness summary, and owner/creator.

The workspace flow is:

1. attach source artifacts (`OnboardingSourceArtifact`) from blueprints,
   spreadsheets, diagram metadata, cable schedules, manual entries, or API
   payloads,
2. normalize those artifacts into staged design rows
   (`OnboardingDesignItem`) with desired state and provenance,
3. discover and resolve prerequisites (`OnboardingPrerequisite`) through
   bind/create/defer/not-required decisions,
4. generate a stable-hash `OnboardingPlan` that composes V2.5 stamp preview,
   import dry-run, prerequisite plan, readiness projection, retry preview, and
   rollback preview,
5. approve and apply the plan through ordered `OnboardingExecutionStage` rows,
   which call V2.5 stamping, import reconciliation, readiness evaluation, and
   handoff generation,
6. persist `OnboardingObjectLink` rows from the workspace/plan/stages/source
   artifacts/design items to created objects, bound objects, reports, exports,
   or external references,
7. publish only after readiness is acceptable or explicitly acknowledged.

The first implemented slice intentionally supports JSON/source-reference
artifacts and staged manual/API rows. Rich spreadsheet and diagram parsers are
expected follow-on additions, not separate topology source-of-truth paths.

---

## 8) V2 Choice Domains

Key constrained enums used by model fields:

1. `ArchitectureStatusChoices`: `draft`, `active`, `retired`
2. `FabricStatusChoices`: `draft`, `planned`, `active`, `retired`
3. `NodeKindChoices`: `active_device`, `passive_assembly`, `logical_container`
4. `EndpointKindChoices`: `netbox_port`, `plugin_port`, `connector`,
   `subconnector`
5. `ConnectorKindChoices`: `osfp`, `qsfp-dd`, `mpo-8`, `mpo-12`, `mpo-16`,
   `mpo-24`, `lc`, `virtual`, `other`
6. `SegmentKindChoices`: `jumper`, `trunk`, `internal`, `external_plant`
7. `LaneDirectionChoices`: `send`, `receive`
8. `TransferMapKindChoices`: `identity`, `polarity_swap`, `shuffle_2x2`,
   `shuffle_1x4`, `shuffle_2x2_mpo24`, `shuffle_4x4`, `direct_attach`,
   `polarity_type_b`, `polarity_type_c`, `shuffle_nxm`, `stagger`, `breakout`,
   `custom`
9. `StampRunStatusChoices`: `pending`, `running`, `completed`, `failed`
10. `SuppressionStatusChoices`: `pending`, `active`, `revoked`, `expired`
11. `AuditEventTypeChoices`: `stamp`, `path_resolve`, `suppression_change`,
    `operation_run`, `policy_eval`
12. `OperationProfileChoices`: `generic_roce`, `madison_default`,
    `topology_integrity`, `operational_impact`, `import_reconciliation`
13. `OperationRunStatusChoices`: `pending`, `running`, `completed`, `failed`
14. `ArchitectureWorkspaceStatusChoices`: `draft`, `collecting_sources`,
    `normalizing`, `validating`, `blocked`, `ready`, `awaiting_approval`,
    `approved`, `publishing`, `published`, `archived`, `cancelled`
15. `ArchitectureWorkspaceKindChoices`: `new_blueprint`, `new_version`,
    `revision`, `comparison`
16. `ArchitectureSourceArtifactTypeChoices`: `blueprint_bundle`,
    `schema_json`, `stamp_template`, `diagram`, `spreadsheet`, `api_payload`,
    `manual_entry`, `note`, `other`
17. `ArchitectureSourceArtifactStatusChoices`: `received`, `normalized`,
    `failed`, `superseded`, `ignored`
18. `ArchitectureComponentStatusChoices`: `pending`, `valid`, `warning`,
    `conflict`, `ignored`
19. `ArchitectureValidationStatusChoices`: `pending`, `passed`, `warning`,
    `failed`
20. `ArchitecturePublishPlanStatusChoices`: `generated`, `blocked`,
    `approved`, `publishing`, `published`, `failed`, `superseded`,
    `cancelled`
21. `OnboardingWorkspaceStatusChoices`: `draft`, `collecting_sources`,
    `normalizing`, `planning`, `blocked`, `awaiting_approval`, `approved`,
    `applying`, `applied`, `readiness_failed`, `published`, `archived`,
    `cancelled`
22. `OnboardingSourceArtifactTypeChoices`: `blueprint_bundle`, `spreadsheet`,
    `diagram`, `cable_schedule`, `rack_plan`, `bom`, `manual_entry`,
    `api_payload`, `note`, `other`
23. `OnboardingSourceArtifactStatusChoices`: `received`, `normalized`,
    `failed`, `superseded`, `ignored`
24. `OnboardingDesignItemStatusChoices`: `pending`, `valid`, `warning`,
    `conflict`, `ignored`
25. `OnboardingPrerequisiteResolutionModeChoices`: `unresolved`, `bind`,
    `create`, `defer`, `not_required`
26. `OnboardingPrerequisiteStatusChoices`: `open`, `resolved`, `deferred`,
    `blocked`
27. `OnboardingPlanStatusChoices`: `draft`, `generated`, `blocked`,
    `awaiting_approval`, `approved`, `applying`, `applied`, `failed`,
    `superseded`, `cancelled`
28. `OnboardingStageStatusChoices`: `pending`, `running`, `completed`,
    `failed`, `skipped`, `rolled_back`

---

## 9) Operator and API Surfaces

### 9.1 Menu layout

The current NetBox menu exposes four groups:

1. `Operate`: Fabric Overview, Interface Fanout Trace, Path Query, Physical
   Cable Blast Radius
2. `Build & Run`: Architecture Workspaces, Onboarding Workspaces, Onboard
   Fabric, Operations Center, Import Preview, Impact Reports
3. `Audit`: Audit Dashboard, Audit Triage, Exception Requests
4. `Model Inventory`: Fabrics, Architectures, Cable Assemblies, Lane Inventory,
   Model Catalog

### 9.2 Visual path workflows

`Path Query` and `Interface Fanout Trace` both expose graphical path traces.
Those views render:

1. 200gbps transport channel groups,
2. optical lanes,
3. MPO12 connectors and fiber positions,
4. shuffle-cassette transforms,
5. cable-assembly cylinders for grouped strands,
6. per-lane color-coded path lines,
7. SVG export and collapsible detail sections.

`Interface Fanout Trace` starts from a NetBox device/interface pair and can
render either expanded per-lane paths or consolidated 200gbps channel groups.
`Path Query` starts from selected optical lanes and renders the resolved path in
the same visual language.

### 9.3 Physical cable blast radius

`Physical Cable Blast Radius` supports operator drill-down by site, device type,
role, rack label, rack row, rack elevation, and partial device name. It can model
these failure scenarios:

1. cable assembly cut/disconnect,
2. connector unplug,
3. OSFP transceiver unseat.

Results are grouped around impacted endpoints/devices and include follow-on
links into path traces where possible. Operators can save modeled results as
immutable `OperationRun` snapshots; `Impact Reports` lists those snapshots,
exports JSON, and compares two saved reports.

### 9.4 Import preview and saved reports

`Import Preview` supports paste/upload JSON dry-runs for plugin-native imports,
including cable assemblies, strand-to-cable assignment, endpoints, transport
channels, channel-position maps, strand terminations, and architecture
blueprints. Applied and dry-run imports can be saved as `OperationRun`
snapshots, exported as JSON, replayed, or applied from the exact saved plan.

### 9.5 Architecture workspaces

`Architecture Workspaces` are the preferred entry point for new/revised
architecture blueprints. The detail page exposes source attachment, source
normalization, architecture component inventory, validation runs, publish plan
generation, plan approval/publish, and handoff JSON actions in one persistent
workspace.

The same workflow is exposed through REST action endpoints so external systems
can attach blueprint bundles or direct-entry schema JSON, normalize them into
components, validate them, generate a stable publish plan, approve it, publish
it, and fetch handoff artifacts without bypassing schema/import validation.

### 9.6 Onboarding workspaces

`Onboarding Workspaces` are the preferred entry point for net-new fabric
onboarding. The detail page exposes source attachment, source normalization,
prerequisite discovery/resolution, plan generation, plan approval/apply,
readiness evaluation, publish, and handoff JSON actions in one persistent
workspace.

The same workflow is exposed through REST action endpoints so external systems
can attach design-document payloads or direct-entry JSON, normalize them into
staged design items, resolve prerequisites, generate a stable plan, approve it,
apply it, and fetch handoff artifacts without bypassing the plugin’s provenance
and readiness loop.

### 9.7 REST and GraphQL boundary

The REST API exposes generated CRUD endpoints for registry-backed objects plus
workflow endpoints for path query, stamp preview/execute/rollback, audit
lifecycle actions, operation runs, operational impact previews, onboarding
workspace actions, architecture workspace actions, and disjointness exception
lifecycle.

GraphQL V2 is query-oriented and JSON-forward. See
`docs/v2_graphql_contract_v2.md` for the versioned field contract.

## 10) Registry Coverage

The v2 registry (`netbox_plant_graph/v2_registry.py`) currently covers standard
CRUD/API object surfaces for:

1. all architecture-definition models,
2. all topology models listed above,
3. all control-plane models listed above.

Workflow pages remain hand-wired and are intentionally not fully registry-driven.

---

## 11) What changed from V1 (high level)

1. V1 hierarchical classes (`PlantNode`, `TerminationPoint`, `AttachmentUnit`,
   `SignalLane`, `CoarseEdge`, `FineEdge`, `LaneMap`, `PlaneMembership`) are
   not the active V2 schema.
2. V2 replaces those with normalized endpoint/position/strand/lane/transfer-map
   primitives.
3. End-to-end lane paths are resolved dynamically from graph primitives instead
   of being persisted as first-class path rows.

---

## 12) Source of Truth

This document is aligned to:

1. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/models.py`
2. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0001_initial.py`
3. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0002_post_mvp_control_plane.py`
4. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0003_cable_assembly.py`
5. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0004_transport_channel_subinterfaces.py`
6. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0005_fabricarchitecture_fabric_class.py`
7. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0006_onboarding_workspace.py`
8. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/resolver.py`
9. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/v2_registry.py`
10. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/architecture.py`
11. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/architecture_schema.py`
12. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/blueprint_registry.py`
13. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/stamping.py`
14. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/stamping_v25.py`
15. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/imports/reconciliation.py`
16. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/impact_modeling.py`
17. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/topology_integrity.py`
18. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/onboarding/`

If model code and this document diverge, treat code as authoritative and update
this file in the same change set.
