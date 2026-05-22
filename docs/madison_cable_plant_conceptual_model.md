# Madison Cable Plant Conceptual Model

> **Historical note:** This document predates the current V2 data model and
> still refers to V1 graph objects in later sections. Use it for Madison design
> context only. The current implementation contract is
> `docs/data_model.md`, especially the `CableAssembly`, `TransportChannel`,
> `OpticalLane`, `StrandTermination`, and `TransferMap` sections.

This document translates the Madison RoCE shuffle architecture and fiber BOM into
a NetBox-oriented conceptual model. It is intended to guide the next data-model
and seed-script work for the local Madison NetBox instance.

Sources reviewed:

- Notion: [ROCE 4-plane Shuffle Cabling Patterns](https://www.notion.so/35ecaf6bfadc80c1a77ac54a8e8de19f), last edited 2026-05-15.
- Google Drive: [Nscale NC 18k Fiber BOM v1.2.xlsx](https://docs.google.com/spreadsheets/d/1W_5uZuxB7r9aYZ2fOfbDZeOa6kbZtofN/edit?usp=drivesdk&ouid=111979992452804372953&rtpof=true&sd=true), modified 2026-04-22.

## 1. Source Architecture Summary

The Madison backside RoCE fabric should be modeled as a four-plane folded-Clos
fabric. Each GPU-facing 800G OSFP is broken into four 200G logical Ethernet
interfaces, one per plane. The physical trick is that a GPU-side OSFP has two
MPO12 connectors, while the network design needs to land that OSFP's logical
200G channels across four distinct leaf switches. The design solves this with
2x2 shuffle cassettes and a deterministic stagger pattern between GPU MPOs,
shuffle cassette MPOs, and leaf MPOs.

The Notion architecture defines these important hierarchy and naming concepts:

| Concept | Source model |
| --- | --- |
| Fabric planes | `PLANE-1` through `PLANE-4` |
| GPU rackscale unit | `NVL72-n` |
| Compute tray | `NVL72-n.GB300-m`, 18 GB300 trays per NVL72 |
| GPU-side optical port | Four OSFPs per GB300, each with two MPO connectors |
| Leaf switch | One leaf per plane per leaf index |
| Leaf OSFP | 64 OSFP cages per leaf switch |
| Shuffle box | Contains 3 trays |
| Shuffle tray | Contains 6 cassettes |
| Shuffle cassette | Has 4 front MPOs and 4 rear MPOs; internally two 2x2 shuffles |

The BOM establishes cable-plant material categories and quantities. The main
East/West summary is:

| Category | Cable count | Notes |
| --- | ---: | --- |
| Node -> Shuffle | 36,864 | Host/GB300 to leaf-side shuffle plant |
| Shuffle -> Leaf | 36,864 | Shuffle plant to leaf switches |
| Shuffle -> Spine | 37,296 | Leaf/spine shuffle plant to spines |
| Total | 111,024 | Consolidated East/West total |

The same workbook also includes a North/South network summary:

| Category | Cable count | Notes |
| --- | ---: | --- |
| Node -> Leaf | 9,216 | N/S GPU tray connectivity |
| Leaf -> Spine | 1,024 | Includes in-rack and cross-rack cases |
| Total | 10,240 | N/S total |

The materials seen in the workbook are primarily single-mode MPO8/MPO-terminated
cable assemblies such as:

- `96f MPO8 SM APC Unpinned/Unpinned`
- `96f SM MPO8 Unpinned/Unpinned`
- `72f SM MPO8 Unpinned/Unpinned`
- `48f MPO8 SM APC Unpinned/Unpinned` for some spare/core cases

Lengths must be modeled as purchased assembly lengths, not simply Euclidean
floorplan distances. The BOM distinguishes bracket-to-bracket length, source
bracket leg length, destination bracket leg length, and end-to-end length.

## 2. Modeling Boundary

NetBox core should remain the system of record for physical inventory and actual
cable instances:

- sites, locations, racks
- devices and passive assemblies
- front ports, rear ports, interfaces
- physical `Cable` records
- cable type/status/labels

`netbox_plant_graph` should own the normalized fabric graph and the lane-aware
interpretation of that physical plant:

- `Fabric` and `FabricPlane`
- `PlantNode`
- `TerminationPoint`
- `AttachmentUnit`
- `SignalLane`
- `CoarseEdge`
- `FineEdge`
- `TransferMap`
- `LaneMap`
- `PlaneMembership`

That gives us two views of the same plant:

1. The NetBox physical view: "what device/port/cable is installed where?"
2. The graph view: "which logical plane, 200G channel, lane, or path does this
   physical thing participate in?"

## 3. Physical Inventory Model

### 3.1 Fabric Scope

Create one backside Madison RoCE fabric:

| Model | Proposed value |
| --- | --- |
| `Fabric.name` | `MAD-1-ROCE-BACKSIDE` |
| `Fabric.expected_plane_count` | `4` |
| `Fabric.tier_depth` | `2` for leaf/spine; `3` only if core is included |
| `Fabric.scope_site` | `MAD-1` |
| `Fabric.disjointness_policy` | `full` |

Create four `FabricPlane` rows numbered 1 through 4.

### 3.2 Active Endpoints

Model active switch and compute endpoints as NetBox `Device` records. Mirror them
into `PlantNode` records through the graph transformer.

For GB300/NVL72 endpoints, the key question is granularity. The cable pattern is
defined at the GB300 tray and OSFP level, so the useful modeling unit is:

```text
Rack
  NVL72 rackscale appliance or rack grouping
    GB300 tray device
      OSFP-1
        MPO-1
        MPO-2
      OSFP-2
      OSFP-3
      OSFP-4
```

If we need a lighter-weight first pass, represent each GB300 tray as a `Device`
in the rack and do not model an NVL72 parent device. The rack name and GB300
device name can carry the appliance index.

For leaf and spine switches:

- each leaf switch belongs to exactly one `FabricPlane`
- each leaf switch exposes 64 OSFP cages
- each OSFP has two MPO attachment units
- each 800G OSFP is modeled as four 200G `AttachmentUnit` rows for plane/path
  reasoning

### 3.3 Passive Shuffle Plant

Model shuffle boxes as passive NetBox `Device` records using templates:

```text
SHUFFLEBOX-n
  TRAY-1
    CASSETTE-1
      FRONT-MPO-1..4
      REAR-MPO-1..4
    CASSETTE-2
    ...
  TRAY-2
  TRAY-3
```

Implementation options:

| Option | Fit |
| --- | --- |
| One NetBox device per shuffle box, ports named by tray/cassette/MPO | Best first pass; simple cabling and labels |
| One NetBox device per cassette | More explicit, but many devices |
| NetBox modules for trays/cassettes | Better long-term if module support is mature in the target NetBox line |

Recommended first pass: one passive `Device` per shuffle box with generated
front/rear ports. Use `AssemblyTemplate`, `AssemblyConnectorTemplate`, and
`AssemblyMappingTemplate` to stamp the ports and their internal transfer maps.

## 4. Connector and Lane Model

### 4.1 OSFP and MPO

At the NetBox physical layer:

- an OSFP cage is an 800G parent interface or port-like termination
- each OSFP has two MPO-side attachment units
- an MPO12 connector is represented as a `TerminationPoint` with connector type
  `MPO12/MPO8 SM APC`

At the graph layer:

- an OSFP becomes a `TerminationPoint`
- MPO-1 and MPO-2 become `AttachmentUnit` rows
- each 200G logical Ethernet channel becomes an `AttachmentUnit` with
  `speed_gbps=200`
- strand/pin/lane detail becomes `SignalLane`

The architecture assumes 800G OSFP optics using 100G unidirectional optical lanes.
Each 200G logical channel consumes two optical lanes, with separate transmit and
receive strands. Therefore, the graph should support both:

- channel-level reasoning: four 200G units per OSFP
- lane-level validation: MPO positions 1-4 and 9-12 mapped to TX/RX lane pairs

### 4.2 Plane Membership

Plane membership should be assigned at the smallest unit that carries forwarding
semantics:

| Object | Plane membership |
| --- | --- |
| Leaf switch `PlantNode` | Native member of one plane |
| Leaf OSFP 200G child channel | Native member of that plane |
| GPU 200G child channel | Native member of exactly one plane |
| Shuffle cassette `PlantNode` | Shared/passive member, with per-edge/fine-edge plane memberships |
| Physical trunk cable | Often shared; derived fine edges carry exact plane membership |

This avoids lying about passive multi-plane assemblies. A 96-fiber trunk or
shuffle cassette can physically carry multiple planes, while its derived fine
edges and logical channels are plane-specific.

## 5. Cable Assembly Model

### 5.1 Cable Profiles

Create a cable-profile catalog, either as plugin metadata or as a NetBox custom
field-backed convention on cable types. A profile should include:

| Field | Example |
| --- | --- |
| `profile_name` | `96f-mpo8-sm-apc-unpinned-unpinned` |
| `fiber_count` | `96` |
| `connector_family` | `MPO8` or `MPO12 harnessed as MPO8` |
| `fiber_mode` | `single-mode` |
| `polish` | `APC` |
| `side_a_pinning` | `unpinned` |
| `side_b_pinning` | `unpinned` |
| `bracket_to_bracket_m` | BOM value |
| `source_leg_m` | BOM value |
| `destination_leg_m` | BOM value |
| `end_to_end_m` | BOM value |
| `source_bom_section` | e.g. `EW HOST TO SHUFFLE (LEAF)` |
| `spare_policy` | primary, spare, reference-only |

The workbook has a warning that the East/West host-to-shuffle leaf trunk option
needs design-team review and should defer to the NVIS BOM for ordering. Model
those rows with `source_status=reference_only` until the design team confirms the
final material list.

### 5.2 Cable Instances

Every installed cable assembly should become a NetBox `Cable`. Use cable labels
that encode both allocation index and endpoints.

Recommended cable naming shape:

```text
MAD1-EW-HOST-SHUFFLE-SU{su}-NVL72{n}-GB300{g}-OSFP{o}-MPO{m}
MAD1-EW-SHUFFLE-LEAF-SB{box}-T{tray}-C{cassette}-MPO{m}-P{plane}
MAD1-EW-SHUFFLE-SPINE-SB{box}-T{tray}-C{cassette}-MPO{m}-P{plane}
```

Store BOM-derived values on the cable:

- cable profile
- length bucket
- source/destination bracket leg lengths
- BOM row or allocation source
- spare/reference status
- route class: `node_to_shuffle`, `shuffle_to_leaf`, `shuffle_to_spine`,
  `node_to_leaf_ns`, `leaf_to_spine_ns`

### 5.3 Coarse and Fine Edges

Use `CoarseEdge` for physical cable assemblies:

```text
GB300.OSFP-1.MPO-1 -- Cable -- SHUFFLEBOX-1.TRAY-1.CASSETTE-1.FRONT-MPO-1
```

Use `FineEdge` for derived channel/lane connections:

```text
GB300.OSFP-1.channel-1 -> PLANE-1.LEAF-1.OSFP-1.channel-1
GB300.OSFP-1.channel-2 -> PLANE-2.LEAF-1.OSFP-1.channel-1
GB300.OSFP-1.channel-3 -> PLANE-3.LEAF-1.OSFP-1.channel-1
GB300.OSFP-1.channel-4 -> PLANE-4.LEAF-1.OSFP-1.channel-1
```

This lets physical cable count and logical fabric correctness be validated
independently.

## 6. Shuffle and Stagger Rules

The Notion page defines two separate behaviors:

1. Shuffle: the internal cassette maps strands from one MPO pair across two MPOs
   on the other side.
2. Stagger: the external GPU-to-shuffle MPO allocation swaps the second and
   third MPO connections in every four-MPO group.

Represent these as distinct model concepts:

| Behavior | Model |
| --- | --- |
| Internal cassette shuffle | `TransferMap` and `LaneMap` owned by the shuffle cassette/box `PlantNode` |
| External staggered cabling | allocation algorithm that creates `Cable` and `CoarseEdge` rows |

The four-MPO stagger for GPU-side connections is:

```text
GPU MPO 1 -> Shuffle MPO 1
GPU MPO 2 -> Shuffle MPO 3
GPU MPO 3 -> Shuffle MPO 2
GPU MPO 4 -> Shuffle MPO 4
```

For an eight-MPO allocation group, apply that pattern twice:

```text
1 -> 1
2 -> 3
3 -> 2
4 -> 4
5 -> 5
6 -> 7
7 -> 6
8 -> 8
```

The generator should maintain three allocation streams:

- GPU MPO stream: ordered by scalability unit, NVL72, GB300, OSFP, MPO
- shuffle MPO stream: ordered by shuffle box, tray, cassette, MPO
- leaf MPO stream: striped across planes, then by leaf OSFP/MPO

The shuffle stream itself is not staggered; the GPU stream is connected to it
using the staggered index mapping.

## 7. Capacity Math

Use the Notion hierarchy to derive capacity:

| Unit | Capacity |
| --- | ---: |
| GB300 tray | 4 OSFP x 2 MPO = 8 GPU-side MPOs |
| One 2x2 shuffle cassette pair | 8 front MPOs and 8 rear MPOs across two cassettes |
| One shuffle tray | 6 cassettes = 24 front MPOs + 24 rear MPOs |
| One shuffle box | 3 trays = 72 front MPOs + 72 rear MPOs |

One full GPU-to-leaf shuffle iteration consumes:

- one GB300 tray
- two 2x2 shuffle cassettes
- one OSFP cage on one leaf in each of four planes

This is the natural unit for generated cabling, audit reporting, and visual
debugging.

## 8. Validation Rules

The modeled cable plant should support these checks:

| Check | Description |
| --- | --- |
| Plane completeness | Every modeled GB300 OSFP exposes four 200G channels, one per plane |
| Plane disjointness | Each logical GPU channel lands on exactly one plane-native leaf path |
| Stagger correctness | Every group of four GPU MPOs follows 1->1, 2->3, 3->2, 4->4 |
| Shuffle internal mapping | Cassette `TransferMap`/`LaneMap` matches the 2x2 shuffle profile |
| Cable profile completeness | Every physical NetBox cable has a known profile and length source |
| BOM reconciliation | Generated cable counts by category and length equal the BOM totals |
| Spare segregation | Spare/reference-only BOM rows are not treated as installed production cables |
| Blast radius | Given a cable, cassette, tray, box, leaf, or plane, derive affected GB300 channels |
| Floorplan congruence | Cable endpoints live in racks whose floorplan coordinates are known |

## 9. Implementation Path

### Phase 1: Catalogs

Create machine-readable catalogs for:

- cable profiles from the workbook
- shuffle cassette profile: 2x2, four MPO front, four MPO rear
- OSFP profile: two MPOs, four 200G logical channels, lane map
- leaf/spine switch OSFP profile

### Phase 2: Physical Inventory

Generate or reconcile NetBox objects for:

- GB300 tray devices in Madison racks
- leaf and spine switch devices
- shuffle boxes as passive devices
- front/rear/MPO ports
- OSFP/child interfaces where active switching semantics are needed

### Phase 3: Fabric Graph

Generate:

- one `Fabric`
- four `FabricPlane` rows
- `PlantNode` mirrors for active and passive devices
- `TerminationPoint`, `AttachmentUnit`, and `SignalLane` rows
- `PlaneMembership` rows for plane-native active endpoints and derived fine edges

### Phase 4: Cabling

Generate NetBox `Cable` rows and plugin `CoarseEdge` rows from allocation rules:

- Node -> Shuffle
- Shuffle -> Leaf
- Shuffle -> Spine
- N/S Node -> Leaf, if modeled in the same NetBox instance
- N/S Leaf -> Spine, if modeled in the same NetBox instance

### Phase 5: Derived Path Expansion

Expand each physical cable and passive shuffle mapping into `FineEdge` and
`LaneMap` rows. The result should support path tracing from:

```text
GB300 OSFP child channel -> shuffle cassette -> leaf OSFP child channel -> spine path
```

### Phase 6: Reconciliation Reports

Produce reports comparing generated state against the workbook:

- cables by category
- cables by length
- cable-meters by category
- primary vs spare/reference counts
- missing or unmatched cable profile rows

## 10. Open Decisions

| Decision | Recommendation |
| --- | --- |
| Represent NVL72 as a device or just group GB300 trays by rack/metadata? | Start with GB300 tray devices; add NVL72 parent later only if needed |
| Represent shuffle cassettes as child devices/modules or ports on one shuffle-box device? | Start with one shuffle-box device and fully qualified port names |
| Store cable profiles in plugin model or NetBox custom fields? | Start with metadata/custom fields; promote to plugin model if reconciliation needs more structure |
| Model every strand immediately? | Generate channel-level first, then add lane-level maps for validation-critical profiles |
| Include N/S fabric in same conceptual fabric? | Keep separate `Fabric` unless operators need a unified blast-radius view |
| Treat reference-only host-to-shuffle rows as installable? | No; mark `reference_only` until confirmed |

## 11. Proposed First Seed Target

For a narrow but useful first implementation, seed one full GPU-to-leaf pattern:

```text
one GB300 tray
two shuffle cassettes
four leaf switches, one per plane
eight GPU-side MPO cables
eight shuffle-to-leaf MPO cables
derived four-plane 200G channel paths
```

Once this validates cleanly, scale the same allocator across the Madison rack
layout and reconcile aggregate counts against the BOM.
