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

V2 models multiplanar optical fabrics with plugin-native topology objects and
NetBox object anchors:

1. **Architecture definitions** describe reusable topology semantics.
2. **Fabric instances** materialize those semantics for a concrete deployment.
3. **Endpoint graph primitives** model ports, MPO positions, strands, and maps.
4. **Optical lanes** are transceiver-local signaling constructs.
5. **Path resolution** traverses connector-position connectivity to produce
   end-to-end lane paths across any number of hops.
6. **Control-plane objects** track stamping, suppressions/exceptions, and audit
   lifecycle events.

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

Purpose:

- define reusable architecture semantics (roles, transfer behavior, allocation
  rules, stamping templates) independently from a concrete fabric instance.

### 2.2 Fabric topology layer

1. `Fabric`
2. `Plane`
3. `FabricNode`
4. `Endpoint`
5. `ConnectorPosition`
6. `TransportChannel`
7. `CableAssembly`
8. `FiberSegment`
9. `FiberStrand`
10. `StrandTermination`
11. `OpticalLane`
12. `TransferMap`
13. `PathIntent`

Purpose:

- represent the physical/logical fabric graph used for lane-aware path
  traversal and multiplanar policy evaluation.

### 2.3 Operational/control-plane layer

1. `StampRun`
2. `SuppressionRule`
3. `AuditEvent`
4. `OperationRun`

Purpose:

- provide execution provenance, policy suppression lifecycle, audit workflow
  event history, and operation run tracking.

---

## 3) NetBox Anchors (What is joined to core objects)

V2 uses NetBox object references where appropriate:

1. `Fabric.tenant` -> `tenancy.Tenant` (optional)
2. `Fabric.scope_site` -> `dcim.Site` (optional)
3. `Fabric.scope_location` -> `dcim.Location` (optional)
4. `FabricNode.source` -> Generic FK (`source_type` + `source_id`)
5. `Endpoint.source` -> Generic FK (`source_type` + `source_id`)
6. `AuditEvent.actor`, `SuppressionRule.created_by/approved_by`,
   `OperationRun.initiated_by` -> auth user model

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

### 4.3 Optical lane semantics (endpoint-local)

`OpticalLane` is a transceiver-local signaling construct, not an end-to-end
path row. It carries:

1. owning `endpoint` (transceiver-side endpoint),
2. local breakout identity (`lane_index`, `local_mpo_index`),
3. direction (`send` or `receive`, local to that endpoint),
4. wavelength (`wavelength_nm`),
5. local termination anchor (`local_mpo_endpoint`, `local_mpo_position`),
6. optional grouping (`pair_key`) and optional `TransportChannel`/`Plane`.

This allows distinct wavelengths to coexist on the same local MPO position.

### 4.4 Passive/internal remap semantics

`TransferMap` maps one connector position to another within a fabric graph
context and supports both:

1. node-owned transfer behavior (`owner_node`), and
2. segment-owned transfer behavior (`owner_segment`),

with exactly one owner required by model validation.

`TransferMap` is the explicit mechanism for shuffle/polarity/breakout behavior
at connector-position resolution.

### 4.5 Path intent semantics

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
10. `CableAssembly`: unique (`site`, `cable_id`)
11. `FiberStrand`: unique (`segment`, `strand_index`)
12. `StrandTermination`: unique (`strand`, `mpo_position`) and unique
    (`mpo_position`) for single occupancy
13. `OpticalLane`: unique (`endpoint`, `lane_index`, `direction`)

Additional model-level validation includes:

1. `FiberSegment` endpoints must be distinct and in the same fabric.
2. `StrandTermination.mpo_position` must belong to `mpo_endpoint`.
3. `StrandTermination` endpoint fabric must match strand fabric.
4. `OpticalLane` endpoint/MPO anchors/plane/channel must be fabric-consistent.
5. `OpticalLane.local_mpo_endpoint` (when parented) must be child of
   `OpticalLane.endpoint`.
6. `TransferMap` requires exactly one owner (`owner_node` xor `owner_segment`)
   and distinct source/destination positions.
7. `FiberStrand` `cable_site` and `cable_id` must be provided together, and the
   referenced cable assembly must exist.
8. `SuppressionRule` plane/lane scope must align to the same fabric.

Indexes optimized for lane lookup:

1. (`fabric`, `lane_index`)
2. (`fabric`, `pair_key`)
3. (`fabric`, `wavelength_nm`)

---

## 7) Control-Plane / Workflow Data Semantics

### 7.1 Stamping lifecycle

1. `StampTemplate` stores executable template JSON.
2. `StampRun` records execution status, parameters, result, errors, metadata.

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
   `stagger`, `breakout`, `custom`
9. `StampRunStatusChoices`: `pending`, `running`, `completed`, `failed`
10. `SuppressionStatusChoices`: `pending`, `active`, `revoked`, `expired`
11. `AuditEventTypeChoices`: `stamp`, `path_resolve`, `suppression_change`,
    `operation_run`, `policy_eval`
12. `OperationProfileChoices`: `generic_roce`, `madison_default`
13. `OperationRunStatusChoices`: `pending`, `running`, `completed`, `failed`

---

## 9) Registry Coverage

The v2 registry (`netbox_plant_graph/v2_registry.py`) currently covers standard
CRUD/API object surfaces for:

1. all architecture-definition models,
2. all topology models listed above,
3. all control-plane models listed above.

Workflow pages remain hand-wired and are intentionally not fully registry-driven.

---

## 10) What changed from V1 (high level)

1. V1 hierarchical classes (`PlantNode`, `TerminationPoint`, `AttachmentUnit`,
   `SignalLane`, `CoarseEdge`, `FineEdge`, `LaneMap`, `PlaneMembership`) are
   not the active V2 schema.
2. V2 replaces those with normalized endpoint/position/strand/lane/transfer-map
   primitives.
3. End-to-end lane paths are resolved dynamically from graph primitives instead
   of being persisted as first-class path rows.

---

## 11) Source of Truth

This document is aligned to:

1. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/models.py`
2. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0001_initial.py`
3. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/migrations/0002_post_mvp_control_plane.py`
4. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/services/resolver.py`
5. `/Users/mencken/github-repos/netbox_multiplanar_fabrics/netbox_plant_graph/v2_registry.py`

If model code and this document diverge, treat code as authoritative and update
this file in the same change set.
