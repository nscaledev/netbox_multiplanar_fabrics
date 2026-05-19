# V2 Ground-Up Rewrite Plan

Status: move-fast implementation plan, intended to become executable work on
`codex-mencken/v2-ground-up-rewrite`.

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

## Product Shape

The first usable V2 should do one thing very well:

> Stamp a small instance of the four-plane GB300/NVL72 shuffle architecture,
> bind active endpoints to real NetBox devices and ports, represent passive
> shuffle/fiber plant natively, then resolve per-lambda optical paths from a
> GPU-side physical port to the appropriate leaf-switch physical ports.

Everything else waits.

## Hard Cuts From V1

The following V1 surfaces are out of the first V2 slice:

- NetBox-native cable/path sync and rebuild.
- Cable custom fields for breakout profiles.
- `PortMapping` compatibility.
- Persistent audit workflow, suppressions, unresolved-state lifecycles.
- Policy dashboards and contamination-domain reporting.
- Floorplan integration.
- Madison seed/report scripts as core plugin behavior.
- Large generic generated CRUD registry.
- Lane workspace UI.
- GraphQL beyond minimal object/path query proof.

Some ideas may come back later, but only after the kernel proves itself.

## Proposed Package Strategy

Default: create a parallel V2 plugin package in this repo, leaving
`netbox_plant_graph` intact as the V1 reference.

Working package name:

```text
netbox_multiplanar_fabrics
```

Working Django app label:

```text
netbox_mpf
```

Why: clean migrations, clean model names, no compatibility drag, and no need to
untangle V1's derived-overlay assumptions while building the V2 kernel.

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

- A physical strand between two connector positions.
- Represents glass, not traffic.
- Carries polarity/orientation metadata when needed.

`OpticalCarrier`

- A wavelength/lambda on one strand.
- This makes WDM-like cases natural: multiple carriers can share one strand.

`OpticalLane`

- A directed or paired transport lane used in a fabric path.
- References one or more carriers depending on optic mode.
- Carries plane, lane index, nominal rate, direction, and endpoint/channel
  semantics.

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
- Maps connector positions, strands, carriers, lanes, or channels across a
  passive artifact.

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
  - lane-to-MPO position mapping for 100G unidirectional optical lanes
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

### Slice 0: Branch And Skeleton

Goal: establish V2 as a separate kernel without damaging V1.

- Add new package skeleton.
- Add NetBox plugin config for V2.
- Add minimal URL/API placeholders.
- Add migration `0001_initial.py` for kernel models.
- Add tests proving V2 can load alongside V1 in the local test settings.

Exit criteria:

- `python manage.py test netbox_multiplanar_fabrics` can discover tests.
- V1 tests are not made worse by V2 package presence.

### Slice 1: Kernel Models

Goal: persist the V2 graph vocabulary.

- Implement architecture, fabric, node, endpoint, position, strand, carrier,
  optical lane, channel, segment, transfer map, and stamp-run models.
- Keep UI minimal: admin/list/detail can wait.
- Add pure model tests for constraints and string/address behavior.

Exit criteria:

- Can create a fabric with four planes.
- Can create NetBox-bound active endpoints.
- Can create plugin-native passive endpoints.
- Can create multiple optical carriers on one strand.

### Slice 2: In-Memory Resolver

Goal: prove semantics before building a big UI.

- Build resolver over persisted V2 objects.
- Resolve from active NetBox-bound physical endpoint to active endpoint.
- Return ordered path stages:
  - source endpoint
  - connector positions
  - strand/carrier/lane hops
  - transfer maps
  - destination endpoint
- Support filtering by plane, channel, lane index, wavelength, direction.

Exit criteria:

- Unit tests resolve one direct fiber segment.
- Unit tests resolve one passive shuffle hop.
- Unit tests detect no-path cases clearly.

### Slice 3: Four-Plane Shuffle Fixture

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

Goal: turn architecture data into repeatable instances.

- Implement idempotent stamp runner.
- Support:
  - selecting existing NetBox device types by role
  - creating NetBox devices for active roles when requested
  - binding to existing NetBox devices/ports
  - generating plugin-native passive nodes/endpoints
  - name/address patterns with variables and counters
  - allocation cursors
- Keep templates JSON-backed initially.

Exit criteria:

- Re-running a stamp updates/keeps managed objects without duplication.
- A small fabric instance can be stamped from one parameter payload.
- Stamp run records all created object IDs.

### Slice 5: Minimal Operator Surface

Goal: usable, not beautiful.

- Add one fabric detail page.
- Add one "stamp V2 fabric" action endpoint/form.
- Add one path query page/API endpoint.
- Expose JSON path output first; human polish later.

Exit criteria:

- Operator can stamp a small test fabric.
- Operator can query a GPU endpoint and see its per-lane paths to leaves.

### Slice 6: Compatibility And Cutover Decisions

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
- Avoid registry-driven surface generation until there is a stable object set.
- Prefer deleting a feature to preserving V1 compatibility in V2.

## Immediate File Plan

First files to add:

```text
netbox_multiplanar_fabrics/
  __init__.py
  plugin_config.py
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
    test_models.py
    test_resolver.py
    test_shuffle_architecture.py
    test_stamping.py
```

First docs to add:

```text
docs/v2_model_contract.md
docs/v2_roce_4_plane_shuffle_fixture.md
```

## Explicit Decisions Needed

These are the questions that materially affect the implementation. My
recommended default is marked with `Default`.

1. Plugin/package boundary

Default: build V2 as a parallel package named `netbox_multiplanar_fabrics`
inside this repo and leave `netbox_plant_graph` intact.

Question: do you want a parallel V2 plugin package, or should V2 replace the
existing `netbox_plant_graph` app in-place?

2. NetBox cable relationship

Default: V2 never creates or depends on NetBox `Cable` rows for modeled
fabric connections.

Question: should V2 forbid NetBox-native cables for modeled fabric endpoints,
or merely ignore them?

3. Active endpoint anchoring

Default: anchor only at NetBox physical parent interfaces/front/rear ports in
V2 MVP; represent OSFP/MPO substructure as plugin-native endpoints under that
anchor.

Question: should MPO connectors under an OSFP be modeled as plugin-native
sub-endpoints, or should we force NetBox component objects for each MPO when
possible?

4. Optical lane semantics

Default: model strand, carrier/lambda, and optical lane separately:
`FiberStrand` -> `OpticalCarrier` -> `OpticalLane`.

Question: do you agree that a lane is not the same object as a fiber strand,
and not the same object as a wavelength?

5. Directionality

Default: optical lanes are directed, and bidirectional optics are represented
by paired directed lanes/carriers.

Question: should bidirectional lanes be first-class paired records, or should
we always model TX/RX as separate directed lanes with a shared pair/group key?

6. Architecture definitions

Default: architecture templates are database rows with JSON rule payloads,
validated by Python dataclasses/services.

Question: should architecture definitions live in the database, in versioned
YAML/JSON files in the repo, or both?

7. Stamping NetBox devices

Default: V2 stamping can create NetBox devices from selected device types, but
does not create NetBox cables.

Question: should the first stamping MVP create NetBox devices, or only bind to
pre-existing NetBox devices while creating plugin-native fabric plant?

8. Migration from V1

Default: no V1-to-V2 migration in the first working slice.

Question: do you need a migration/import path from current V1 data before we
call V2 useful, or can V2 initially be greenfield?

9. Resolver persistence

Default: resolved paths are computed on demand first; cached `ResolvedPath`
records come only if performance requires them.

Question: do you want resolved path rows persisted as part of stamping, or
computed on demand from canonical graph objects?

10. First proof target

Default: the first proof target is one GB300 tray -> two shuffle cassettes ->
four leaf ports, not a whole NVL72 or full Madison SU.

Question: is that small topology the right first acceptance test, or should
the first acceptance target be larger?

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
