# V2 Ground-Up Rewrite Plan

Status: active move-fast implementation plan on
`codex-mencken/v2-ground-up-rewrite`.

Last updated: 2026-05-20.

## Intent

V2 is not a cleanup of the current `netbox_plant_graph` plugin. It is a new,
plugin-native topology kernel for multi-planar fabrics.

NetBox remains the source of truth for ordinary inventory objects such as
devices, device types, roles, sites, racks, and physical device ports. V2 owns
all modeled-fabric connection semantics:

- fiber segments
- connector position semantics
- strand identity
- wavelength/lambda identity
- optical lane identity
- passive internal transfer maps
- fabric plane membership
- end-to-end path resolution
- stamping rules and stamp provenance

For modeled fabrics, V2 must not depend on NetBox `Cable`, `CableTermination`,
`CablePath`, cable profiles, or `PortMapping` as source-of-truth constructs.
Modeled-fabric endpoints should explicitly reject plugin-managed connections
that try to use those NetBox-native cable/path constructs.

## Product Shape

The first usable V2 should do one thing very well:

> Stamp a small instance of the four-plane GB300/NVL72 shuffle architecture,
> bind active endpoints to real NetBox devices and ports, represent passive
> shuffle/fiber plant natively, then resolve per-lambda optical paths from a
> GPU-side physical port to the appropriate leaf-switch physical ports.

Everything else waits.

## Current State

The V2 rewrite is now past the pure skeleton phase. The branch has a working
plugin boot path, greenfield V2 models, a bounded graph resolver, a RoCE
four-plane mini fixture, registry-backed standard object/API pages, and a
hand-wired stamp execution workflow.

Completed:

- Greenfield `0001_initial.py` for the V2 model set.
- Core models for architecture, roles, transfer patterns, allocation rules,
  fabrics, planes, nodes, endpoints, connector positions, transport channels,
  fiber segments, strands, strand terminations, optical lanes, transfer maps,
  path intents, stamp templates, and stamp runs.
- Resolver over connector-position adjacency, with arbitrary-hop BFS and
  wavelength/plane/direction matching.
- Executable RoCE four-plane mini architecture fixture.
- Hybrid stamp executor registry and a `roce_4plane_mini_proof` primitive.
- Idempotent stamping of one GB300 tray, two shuffle cassettes, and four leaf
  ports.
- Standard object UI/API generated from the V2 registry.
- Hand-wired V2 home, path query, seed proof, and stamp-template execution
  workflow pages.
- Template-driven source binding fields for stamp execution.
- NetBox source anchoring for stamped `FabricNode` and `Endpoint` objects via
  generic source pointers.
- Validation that an interface source binding belongs to its selected device
  binding.
- Stamp-run result provenance for the managed V2 object inventory.

In progress:

- The stamp runner still uses a mini-proof Python primitive. It is intentionally
  hybrid, but the next iterations should move more role/endpoint/allocation
  shape into validated template data.
- NetBox device creation from template rules is not implemented yet; current
  source binding supports existing devices/ports.

Deferred:

- Full-suite testing. Focused V2 tests are being run slice-by-slice until
  several more implementation slices land.

## Hard Cuts From V1

The following V1 surfaces are out of the first V2 slice:

- NetBox-native cable/path sync and rebuild.
- Cable custom fields for breakout profiles.
- `PortMapping` compatibility.
- Persistent audit workflow, suppressions, unresolved-state lifecycles.
- Policy dashboards and contamination-domain reporting.
- Floorplan integration.
- Madison seed/report scripts as core plugin behavior.
- V1 generated CRUD registry patterns. V2 now uses a deliberately small
  registry for standard table/object/API pages only; workflow pages remain
  hand-wired.
- Lane workspace UI.
- GraphQL beyond minimal object/path query proof.

Some ideas may come back later, but only after the kernel proves itself.

## Proposed Package Strategy

Decision: replace the existing `netbox_plant_graph` plugin in-place.

Working package name remains:

```text
netbox_plant_graph
```

Working Django app label remains:

```text
netbox_plant_graph
```

Why: the old plugin was a discovery prototype. V2 should not carry two
semantically different apps in one repo. This is a greenfield break of the
existing app surface.

## Core Data Model

The V2 model should start with a small number of high-signal tables.

### Architecture Definition

`FabricArchitecture`

- Names and versions an architecture family.
- Example: `roce-4-plane-gb300-2x2-shuffle`.
- Stores high-level capabilities: plane count, endpoint role taxonomy,
  default lane model, default connector model.

`ArchitectureRole`

- Defines semantic roles used by the architecture.
- Examples: `gpu_tray`, `gpu_osfp`, `leaf_switch`, `shuffle_box`,
  `shuffle_tray`, `shuffle_cassette`, `shuffle_mpo`.
- Roles are not NetBox device roles. They are fabric semantics.

`TransferPattern`

- Defines reusable internal mapping primitives.
- Examples: identity, polarity inversion, 2x2 shuffle, staged 2x2 shuffle.
- Stores position-to-position and optional lane/lambda transform rules.

`AllocationRuleSet`

- Defines deterministic endpoint allocation order.
- Examples: GB300 OSFP/MPO sequence, shuffle cassette fill order, leaf
  cross-plane striping order, stagger transform.

### Fabric Instance

`Fabric`

- One deployed/staged fabric instance.
- Points to `FabricArchitecture`.
- Has lifecycle state: draft, planned, active, retired.

`Plane`

- Numbered plane inside a fabric.
- Minimal fields: fabric, plane number, label.

`FabricNode`

- One topology-bearing object.
- May bind to a NetBox `dcim.Device`, or may be plugin-native passive plant.
- Carries architecture role, parent node, local index, and path-like address.
- Examples: `SU1.NVL72-1.GB300-1`, `SHUFFLEBOX-1.TRAY-1.CASSETTE-1`,
  `PLANE-1.LEAF-1`.

`Endpoint`

- A connector-bearing point on a node.
- May bind to a NetBox interface/front port/rear port for active or anchored
  physical endpoints.
- May be plugin-native for passive MPOs and internal fabric plant.
- Examples: `OSFP-1`, `OSFP-1.MPO-1`, `CASSETTE-1.FRONT.MPO-1`,
  `Ethernet1/1.MPO-1`.

`ConnectorPosition`

- A numbered position inside an endpoint.
- Example: MPO-12 position 1, position 12.
- This is explicit because position identity matters.

`FiberStrand`

- A physical strand of glass inside a `FiberSegment`.
- Represents glass, not traffic.
- Carries polarity/orientation metadata when needed.

`StrandTermination`

- Normalized join from one `FiberStrand` to one MPO endpoint position.
- Fields: strand, MPO endpoint, MPO position, optional termination index.
- This lets a strand have one or more modeled terminations without baking a
  fixed "A/B end" assumption into the core model.

`OpticalLane`

- A transceiver-local signaling lane, not an end-to-end path object.
- Belongs to an active/plugin transceiver endpoint such as an OSFP.
- Carries lane index, local MPO index, local MPO position, send/receive
  direction from the transceiver's point of view, wavelength/lambda, nominal
  rate, plane, and optional channel.
- Multiple optical lanes can reference the same fiber strand indirectly by
  terminating on positions attached to that strand. WDM-style cases are
  represented by distinct lanes with distinct wavelengths on the same
  position/strand.
- Bidirectional operation is represented as two local lanes, usually sharing a
  `pair_key`: one `send`, one `receive`.

`TransportChannel`

- Logical channel grouping used for user-facing path semantics.
- Example: 200G child channel inside an 800G OSFP.
- Groups optical lanes and binds the fabric plane.

### Connectivity

`FiberSegment`

- Plugin-native replacement for NetBox cable/path segments.
- Connects two endpoints and optionally one or more position pairs.
- Can represent a jumper, trunk, internal assembly hop, or external plant hop.

`TransferMap`

- Internal transform inside a `FabricNode` or `FiberSegment`.
- Maps connector positions across a passive artifact.
- Passive maps are bidirectional by default; future active/directional
  transforms can set `bidirectional=false`.

### Arbitrary-Hop Path Extraction

The database stores canonical graph facts; the resolver walks them.

- Vertices: `ConnectorPosition` rows.
- Fiber edges: positions joined through `StrandTermination` rows on the same
  `FiberStrand`.
- Passive transfer edges: `TransferMap.src_position` to
  `TransferMap.dst_position`.
- Signaling anchors: `OpticalLane.local_mpo_position` at the active endpoints.

Path extraction is a bounded breadth-first graph traversal with cycle
detection, not a fixed-depth SQL join. This is the escape hatch for arbitrary
patch/shuffle hops: the schema can represent any number of segments, and the
resolver follows adjacency until it finds a compatible destination lane or
exhausts `max_depth`.

`PathIntent`

- The intended logical path target.
- Example: GPU OSFP channel 2 should reach plane 2 leaf port X.
- Useful for validation and stamping idempotency.

`ResolvedPath`

- Optional cached path snapshot for speed and UI.
- Rebuildable from canonical fiber/lane data.
- Not source of truth.

### Stamping

`StampTemplate`

- Declarative template for devices, passive nodes, endpoints, channels, and
  fiber segments.
- JSON-backed initially, with strict validation in Python.
- Architecture/rule storage is deliberately flexible for now. Start with DB
  rows containing JSON payloads, but keep import/export services ready for
  versioned JSON/YAML fixtures if the workflow wants repository-owned
  architecture definitions.

`StampRun`

- One execution record.
- Stores input parameters, created/updated object references, status, and
  error details.

## First Architecture Fixture

The first architecture fixture should encode the Notion four-plane shuffle
design as data, not as scattered ad hoc Python.

Minimum fixture content:

- Four planes.
- GB300 tray role:
  - four OSFP endpoints
  - two MPO endpoints per OSFP
  - four 200G transport channels per OSFP
  - transceiver-local lane-to-MPO position mapping for 100G unidirectional
    optical lanes
- Leaf switch role:
  - OSFP endpoints
  - two MPO endpoints per OSFP
  - plane membership on transport channels
- Shuffle cassette role:
  - front/rear sides
  - four MPOs per side
  - two independent 2x2 shuffle assemblies per cassette
- Fill/allocation rules:
  - GB300 MPO order
  - shuffle cassette order
  - leaf endpoint plane striping
  - second/third MPO stagger within each group of four

## Implementation Slices

### Slice 0: In-Place Skeleton

Status: completed.

Goal: replace the V1 plugin boot path with a minimal V2 kernel.

- Strip V1 custom-field, CablePath, audit, floorplan, and generated-registry
  boot behavior from plugin initialization.
- Replace plugin config metadata/default settings with V2 settings.
- Add minimal URL/API placeholders.
- Replace migrations with a new greenfield `0001_initial.py`.
- Add tests proving the V2 plugin imports and model metadata is coherent.

Exit criteria:

- `python manage.py test netbox_plant_graph.tests.test_v2_*` can discover
  and run V2 tests.
- No V2 boot path imports V1 sync/audit/floorplan/cabling code.

### Slice 1: Kernel Models

Status: completed.

Goal: persist the V2 graph vocabulary.

- Implement architecture, fabric, node, endpoint, position, strand
  termination, optical lane, channel, segment, transfer map, and stamp-run
  models.
- Keep UI minimal: admin/list/detail can wait.
- Add pure model tests for constraints and string/address behavior.

Exit criteria:

- Can create a fabric with four planes.
- Can create NetBox-bound active endpoints.
- Can create plugin-native passive endpoints.
- Can create multiple send/receive optical lanes on positions attached to one
  strand, including distinct wavelengths on the same strand.

### Slice 2: In-Memory Resolver

Status: completed for the MVP graph semantics.

Goal: prove semantics before building a big UI.

- Build resolver over persisted V2 objects.
- Resolve from active NetBox-bound physical endpoint to active endpoint.
- Return ordered path stages:
  - source endpoint
  - connector positions
  - fiber-strand hops
  - transfer maps
  - destination endpoint
- Support filtering by plane, channel, lane index, wavelength, direction.

Exit criteria:

- Unit tests resolve one direct fiber segment.
- Unit tests resolve one passive shuffle hop.
- Unit tests detect no-path cases clearly.

### Slice 3: Four-Plane Shuffle Fixture

Status: completed for the mini proof fixture; full NVL72-scale template shape
is still deferred.

Goal: make the Notion architecture executable.

- Add fixture/builder for one GB300 tray, two shuffle cassettes, and four leaf
  ports across four planes.
- Encode the stagger and 2x2 shuffle as reusable transfer/allocation rules.
- Stamp enough data to prove end-to-end paths per optical lane.

Exit criteria:

- For one GB300 tray, all four OSFPs and eight MPOs are represented.
- The expected leaf plane striping appears in the stamped topology.
- Per-lane path tests prove the stagger+shuffle mapping.

### Slice 4: Stamping Engine MVP

Status: in progress.

Goal: turn architecture data into repeatable instances.

- Implement idempotent stamp runner.
- Support:
  - selecting existing NetBox device types by role
  - creating NetBox devices for active roles when requested
  - binding to existing NetBox devices/ports when requested
  - generating plugin-native passive nodes/endpoints
  - name/address patterns with variables and counters
  - allocation cursors
- Keep templates JSON-backed initially.

Exit criteria:

- Re-running a stamp updates/keeps managed objects without duplication.
- A small fabric instance can be stamped from one parameter payload.
- Stamp run records all created object IDs.

Completed:

- Hybrid executor dispatch.
- Idempotent mini-proof stamp runner.
- Existing NetBox device/interface source bindings.
- Template-driven source binding fields on the workflow form.
- Managed V2 object IDs and counts in `StampRun.result`.

Next:

- Add clearer template validation at the boundary.
- Add NetBox device creation support behind explicit template rules.

### Slice 5: Minimal Operator Surface

Status: partially completed.

Goal: usable, not beautiful.

- Add one fabric detail page.
- Add one "stamp V2 fabric" action endpoint/form.
- Add one path query page/API endpoint.
- Expose JSON path output first; human polish later.

Exit criteria:

- Operator can stamp a small test fabric.
- Operator can query a GPU endpoint and see its per-lane paths to leaves.

Completed:

- V2 home and seed proof action.
- Stamp-template detail action and execute workflow.
- Path query page/API endpoint.
- Registry-backed standard object list/detail/add/edit/delete/changelog/journal
  routes.

### Slice 6: Compatibility And Cutover Decisions

Status: not started.

Goal: decide what survives from V1.

- Decide whether to migrate any V1 data.
- Decide whether V1 remains installable in parallel.
- Decide whether V2 replaces the published package name.
- Add deprecation docs if needed.

## Coding Rules For The Rewrite

- Prefer canonical persisted objects over derived overlays.
- Do not read NetBox `CablePath` for modeled fabric path semantics.
- Do not use NetBox `PortMapping` for modeled fabric passive semantics.
- Keep JSON templates, but validate them with typed Python classes at the
  boundary.
- Keep resolver pure enough to test without UI.
- Add UI only after model+resolver tests prove the core.
- Use the V2 registry for standard object table/detail/form/filter/API routes.
  Keep workflow pages hand-wired.
- Prefer deleting a feature to preserving V1 compatibility in V2.

## Immediate File Plan

First files to replace/add:

```text
netbox_plant_graph/
  __init__.py
  choices.py
  models.py
  urls.py
  api/
    __init__.py
    serializers.py
    urls.py
    views.py
  services/
    __init__.py
    architecture.py
    resolver.py
    stamping.py
  migrations/
    __init__.py
    0001_initial.py
  tests/
    __init__.py
    test_v2_models.py
    test_v2_resolver.py
    test_v2_shuffle_architecture.py
    test_v2_stamping.py
```

First docs to add:

```text
docs/v2_model_contract.md
docs/v2_roce_4_plane_shuffle_fixture.md
```

## Resolved Decisions

1. Plugin/package boundary: replace `netbox_plant_graph` in-place.
2. NetBox cable relationship: forbid NetBox-native cables for modeled fabric
   connections.
3. Active endpoint anchoring: join to NetBox physical ports, but model port
   definitions, OSFP semantics, and MPO children in the plugin.
4. Optical lane semantics: `OpticalLane` is transceiver-local. `FiberStrand`
   is glass. `StrandTermination` joins strands to MPO positions. Lambda lives
   on the local optical lane.
5. Directionality: send/receive are separate local lanes from the endpoint's
   point of view, with a shared pair/group key when paired.
6. Architecture definitions: start with DB JSON payloads validated by Python;
   be ready to pivot to repo fixtures or dual DB/file definitions.
7. Stamping NetBox devices: support both creating NetBox devices and binding to
   existing devices/ports.
8. Migration from V1: greenfield; no V1 migration in the first working slice.
9. Resolver persistence: compute on demand from canonical graph objects in the
   MVP. Add persisted/cached `ResolvedPath` rows later only if performance or
   UI requirements justify them.
10. First proof target: one GB300 tray -> two shuffle cassettes -> four leaf
    ports.

## First Acceptance Test

The first meaningful V2 acceptance test should read like this:

1. Create or bind one GB300 tray node with four OSFPs.
2. Create two plugin-native shuffle cassettes with front/rear MPO endpoints.
3. Bind four leaf switch endpoints, one per plane.
4. Stamp the GPU-to-leaf shuffle pattern.
5. Resolve every optical lane from each GB300 OSFP to its destination leaf
   endpoint.
6. Assert:
   - every expected plane is reached
   - every expected lane has one path
   - the second/third MPO stagger is reflected in the path
   - the 2x2 shuffle transfer is reflected in the path
   - no NetBox `Cable` rows are created

That is the point where V2 becomes real.
