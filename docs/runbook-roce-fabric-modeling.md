# Runbook: Modeling a Multi-Tenant Multi-Planar RoCEv2 Fabric in NetBox

> **Historical note:** This runbook was written for the original graph model and
> uses V1-era objects such as `PlantNode`, `AttachmentUnit`, `SignalLane`,
> `CoarseEdge`, `FineEdge`, and NetBox `CablePath` extraction. It is retained as
> a record of earlier modeling work. For current V2 operation, use
> `docs/data_model.md`, `docs/v2_cutover_runbook.md`, and the NetBox
> `Multi-planar v2` menu.

**Purpose:** Step-by-step operator procedure for modeling a shared RoCEv2 backside fabric used by
two tenants, proving out the NetBox core + `netbox_plant_graph` plugin data model end-to-end.

**NetBox version:** 4.5.x  
**Plugin version:** `netbox_plant_graph` (current HEAD)  
**Intended outcome:** A fully populated graph that supports path resolution, plane membership
verification, blast-radius analysis, and plane audit — with all gaps surfaced explicitly.

---

## How to read this document

Callout conventions used throughout:

> ⚠️ **GAP:** Something is missing, ambiguous, or requires a design decision before continuing.
> These are expected and the primary deliverable of this exercise. Do **not** skip gaps.

> 📝 **NOTE:** An important behavioral detail or cross-reference.

> 🔍 **QUESTION:** An open design decision with no blocking consequence for the current step,
> but that must be resolved before moving to production.

---

## Reference Topology

Before touching NetBox, establish the concrete naming conventions used throughout this runbook.

### Site and location hierarchy

```
Site: dc-alpha
└── Location: building-1
    ├── Location: hall-compute-a      (compute hall, tenant workload)
    ├── Location: hall-compute-b      (compute hall, tenant workload)
    ├── Location: hall-compute-c      (compute hall, tenant workload)
    └── Location: hall-network        (network-dedicated, RoCE spine/shuffle + edge)
```

### Tenants

| Slug          | Name         | Role                  |
|---------------|--------------|-----------------------|
| tenant-alpha  | Tenant Alpha | GPU workload consumer |
| tenant-beta   | Tenant Beta  | GPU workload consumer |

### Rack layout

Each compute hall contains tenant GPU racks plus dedicated RoCE shuffle and leaf infrastructure.
The RoCE active switching fabric is physically disjoint per plane.

**hall-compute-a**

| Rack name              | Tenant       | Purpose                                      |
|------------------------|--------------|----------------------------------------------|
| RACK-A-GPU-A1          | tenant-alpha | GPU servers                                  |
| RACK-A-GPU-A2          | tenant-alpha | GPU servers                                  |
| RACK-A-GPU-B1          | tenant-beta  | GPU servers                                  |
| RACK-A-GPU-B2          | tenant-beta  | GPU servers                                  |
| RACK-A-SHUFFLE-1       | (shared)     | GPU→leaf passive shuffle modules / panels    |
| RACK-A-ROCE-LEAF-P1    | (shared)     | RoCE plane-1 leaf switch                     |
| RACK-A-ROCE-LEAF-P2    | (shared)     | RoCE plane-2 leaf switch                     |
| RACK-A-ROCE-LEAF-P3    | (shared)     | RoCE plane-3 leaf switch                     |
| RACK-A-ROCE-LEAF-P4    | (shared)     | RoCE plane-4 leaf switch                     |
| RACK-A-FS-LEAF-1       | (shared)     | Frontside leaf switch                        |

**hall-compute-b** and **hall-compute-c**: identical rack pattern with prefix `B` / `C`.

**hall-network**

| Rack name                 | Tenant   | Purpose                                   |
|---------------------------|----------|-------------------------------------------|
| RACK-NET-ROCE-SHUFFLE-P1  | (shared) | Plane-1 leaf→spine passive shuffle modules |
| RACK-NET-ROCE-SHUFFLE-P2  | (shared) | Plane-2 leaf→spine passive shuffle modules |
| RACK-NET-ROCE-SHUFFLE-P3  | (shared) | Plane-3 leaf→spine passive shuffle modules |
| RACK-NET-ROCE-SHUFFLE-P4  | (shared) | Plane-4 leaf→spine passive shuffle modules |
| RACK-NET-ROCE-SPINE-P1-A  | (shared) | RoCE plane-1 spine A switch               |
| RACK-NET-ROCE-SPINE-P1-B  | (shared) | RoCE plane-1 spine B switch               |
| RACK-NET-ROCE-SPINE-P2-A  | (shared) | RoCE plane-2 spine A switch               |
| RACK-NET-ROCE-SPINE-P2-B  | (shared) | RoCE plane-2 spine B switch               |
| RACK-NET-ROCE-SPINE-P3-A  | (shared) | RoCE plane-3 spine A switch               |
| RACK-NET-ROCE-SPINE-P3-B  | (shared) | RoCE plane-3 spine B switch               |
| RACK-NET-ROCE-SPINE-P4-A  | (shared) | RoCE plane-4 spine A switch               |
| RACK-NET-ROCE-SPINE-P4-B  | (shared) | RoCE plane-4 spine B switch               |
| RACK-NET-FS-SPINE-1       | (shared) | Frontside spine switch                    |
| RACK-NET-FS-SPINE-2       | (shared) | Frontside spine switch                    |
| RACK-NET-MGMT-1           | (shared) | Management switches                       |
| RACK-NET-EDGE-1           | (shared) | Edge / WAN / ISP equipment                |

**Totals:** Tenant Alpha — 6 GPU racks. Tenant Beta — 6 GPU racks. RoCE active fabric — 12 hall
leaves + 8 network spines. Passive RoCE plant — 48 GPU→leaf shuffle modules + 12 leaf→spine
shuffle modules. ✓

### Devices per rack (lightweight build)

| Role                     | Per-rack count | Example names (Hall A / network)                          |
|--------------------------|----------------|-----------------------------------------------------------|
| GPU server               | 2              | `gpu-a-a1-001`, `gpu-a-a1-002`                            |
| RoCE plane leaf sw       | 1              | `roce-leaf-ha-p1` in `RACK-A-ROCE-LEAF-P1`                |
| GPU→leaf shuffle module  | many           | `gpu-leaf-shuffle-a-a1-001-nic0` in `RACK-A-SHUFFLE-1`    |
| Leaf→spine shuffle module| 3 per plane rack | `leaf-spine-shuffle-ha-p1` in `RACK-NET-ROCE-SHUFFLE-P1` |
| Frontside leaf sw        | 1              | `fs-leaf-ha-1`                                            |
| RoCE plane spine sw      | 1              | `roce-spine-net-p1-a` in `RACK-NET-ROCE-SPINE-P1-A`       |
| Frontside spine          | 1              | `fs-spine-net-1`                                          |
| Mgmt switch              | 1              | `mgmt-sw-net-1`                                           |
| Edge router              | 1              | `edge-rtr-net-1`                                          |

### RoCE fabric parameters

| Parameter                | Value                                               |
|--------------------------|-----------------------------------------------------|
| Fabric name              | ROCE-FABRIC-ALPHA                                   |
| Plane count              | 4                                                   |
| Topology                 | Leaf-spine (2-tier), one active switch plane per path |
| Disjointness policy      | full                                                |
| Scope                    | site dc-alpha                                       |
| Speed per plane          | 200 Gbps (child unit)                               |
| Parent port speed        | 800 Gbps                                            |
| GPU child interface count| 4 per 800G GPU parent port                          |
| Leaf uplink child count  | 4 per 800G leaf uplink parent port                  |
| Spine redundancy         | 2 spines per plane (`-a` and `-b`)                  |

### GPU server interface model

Each GPU server has two 800G backside NIC ports used for RoCE, each broken out 4:1:

```
GPU server (e.g. gpu-a-a1-001)
├── NIC0  (800G, parent)
│   ├── NIC0.plane1  (200G, child, fabric_plane=1)
│   ├── NIC0.plane2  (200G, child, fabric_plane=2)
│   ├── NIC0.plane3  (200G, child, fabric_plane=3)
│   └── NIC0.plane4  (200G, child, fabric_plane=4)
└── NIC1  (800G, parent)
    ├── NIC1.plane1  (200G, child, fabric_plane=1)
    ├── NIC1.plane2  (200G, child, fabric_plane=2)
    ├── NIC1.plane3  (200G, child, fabric_plane=3)
    └── NIC1.plane4  (200G, child, fabric_plane=4)
```

The `fabric_plane` field is a NetBox custom field (integer) that the plugin transformer reads to
assign `PlaneMembership` records. Its creation is covered in Phase 10.

### Cabling topology (backside / RoCE only)

Each GPU 800G parent NIC port fans out through a passive shuffle module and lands on four
plane-dedicated leaf switches in the same hall. Each plane leaf then uses a second 800G uplink
parent port to fan out through a plane-local passive shuffle module in the network hall, where the
four 200G channels are split across that plane's two spine switches.

```
gpu-a-a1-001:NIC0
  └── GPU patch cable ──> gpu-leaf-shuffle-a-a1-001-nic0:bp1
        ├── fp1 ──> roce-leaf-ha-p1:Eth1/1
        ├── fp2 ──> roce-leaf-ha-p2:Eth1/1
        ├── fp3 ──> roce-leaf-ha-p3:Eth1/1
        └── fp4 ──> roce-leaf-ha-p4:Eth1/1

roce-leaf-ha-p1:Eth1/49
  └── Leaf uplink cable ──> leaf-spine-shuffle-ha-p1:bp1
        ├── fp1 ──> roce-spine-net-p1-a:Eth1/1
        ├── fp2 ──> roce-spine-net-p1-a:Eth1/2
        ├── fp3 ──> roce-spine-net-p1-b:Eth1/1
        └── fp4 ──> roce-spine-net-p1-b:Eth1/2
```

### GPU→leaf shuffle-board topology

The passive GPU→leaf shuffle is explicit in this runbook. Each GPU parent port lands on one
logical shuffle module with:

- one backside MPO-12 port (`bp1`) facing the GPU NIC
- four frontside MPO-12 ports (`fp1`–`fp4`) facing the four plane-dedicated leaves
- straight MPO-12 patch cables on both sides
- only the four active fiber pairs used by the 4×200G breakout: `(1/12)`, `(2/11)`, `(3/10)`,
  `(4/9)`

**Physical intent**

```
GPU parent port bp1
  pair 1/12  -> plane 1 leaf via fp1
  pair 2/11  -> plane 2 leaf via fp2
  pair 3/10  -> plane 3 leaf via fp3
  pair 4/9   -> plane 4 leaf via fp4
```

**Strand-level example for one shuffle module**

```text
bp1-p1   <-> fp1-p12
bp1-p12  <-> fp1-p1
bp1-p2   <-> fp2-p12
bp1-p11  <-> fp2-p1
bp1-p3   <-> fp3-p12
bp1-p10  <-> fp3-p1
bp1-p4   <-> fp4-p12
bp1-p9   <-> fp4-p1
```

**Illustrative front view**

```text
Rear / GPU side                  Front / leaf side
-----------------------------    -----------------------------------------
bp1 (GPU NIC MPO-12)             fp1 -> plane 1 leaf
  p1/p12 --------------------->  fp1 p12/p1
  p2/p11 --------------------->  fp2 p12/p1 -> plane 2 leaf
  p3/p10 --------------------->  fp3 p12/p1 -> plane 3 leaf
  p4/p9  --------------------->  fp4 p12/p1 -> plane 4 leaf
```

> 📝 **NOTE:** The executable dev harness in `devrun/runbook_roce.py` instantiates the same
> topology as a four-channel logical passive module (`rear position 1..4 -> front port 1..4`).
> The markdown here is the authoritative physical strand map; the script intentionally collapses
> that map to the four active breakout channels that the plugin can rebuild today.

### Leaf→spine shuffle-board topology

The leaf uplink uses the same four-channel shuffle pattern, but all four channels remain within a
single plane and are distributed across that plane's spine pair:

- one backside MPO-12 port (`bp1`) facing the leaf's `Eth1/49` 800G parent uplink
- four frontside MPO-12 ports (`fp1`–`fp4`) facing the two plane-local spines
- straight MPO-12 patch cables on both sides
- the same four active fiber pairs used by the 4×200G breakout: `(1/12)`, `(2/11)`, `(3/10)`,
  `(4/9)`

**Physical intent**

```text
Leaf uplink parent bp1
  pair 1/12  -> plane spine A port 1 via fp1
  pair 2/11  -> plane spine A port 2 via fp2
  pair 3/10  -> plane spine B port 1 via fp3
  pair 4/9   -> plane spine B port 2 via fp4
```

**Strand-level example for one leaf-spine shuffle module**

```text
bp1-p1   <-> fp1-p12
bp1-p12  <-> fp1-p1
bp1-p2   <-> fp2-p12
bp1-p11  <-> fp2-p1
bp1-p3   <-> fp3-p12
bp1-p10  <-> fp3-p1
bp1-p4   <-> fp4-p12
bp1-p9   <-> fp4-p1
```

**Illustrative front view**

```text
Rear / leaf side                 Front / spine side
-----------------------------    -----------------------------------------
bp1 (leaf uplink MPO-12)         fp1 -> spine A port 1
  p1/p12 --------------------->  fp1 p12/p1
  p2/p11 --------------------->  fp2 p12/p1 -> spine A port 2
  p3/p10 --------------------->  fp3 p12/p1 -> spine B port 1
  p4/p9  --------------------->  fp4 p12/p1 -> spine B port 2
```

---

## Phase 1: Organizational & Tenant Setup (NetBox Core)

Playwright/browser capture used for this phase:

![Phase 1 login](images/runbook-roce-phase1-ui/phase1-login.png)

### Step 1.0 — Create operator tenant (do this first)

Before creating workload tenants, create a third tenant that represents the infrastructure owner.
This is the tenant assigned to shared network plant: the fabric itself, shared racks, and
infrastructure devices (leaves, spines, shuffle modules). Keeping infrastructure ownership separate
from workload tenants clarifies blast-radius scope and multi-tenant isolation queries.

Navigate to: **Tenancy → Tenants → Add**

| Name     | Slug     | Description                                |
|----------|----------|--------------------------------------------|
| Operator | operator | Infrastructure owner for shared resources  |

After creating the `Fabric` in Phase 12, set `Fabric.tenant = Operator`. Set the `tenant` field
on all shared racks and infrastructure devices (leaves, spines, shuffle modules) to `Operator` as
well.

> 📝 **NOTE:** This convention ensures that `PlantNode.tenant` for infrastructure nodes is
> populated as `Operator`, which flows through to graph queries and blast-radius result groupings.
> GPU compute devices carry their respective workload tenant (`Tenant Alpha`, `Tenant Beta`), not
> the `Operator` tenant.

UI capture:

![Step 1.0 operator tenant add form](images/runbook-roce-phase1-ui/phase1-step10-operator-tenant-form.png)

![Step 1.0 operator tenant detail](images/runbook-roce-phase1-ui/phase1-step10-operator-tenant-detail.png)

### Step 1.1 — Create workload tenants

Navigate to: **Tenancy → Tenants → Add**

Create the following two tenants:

| Name         | Slug          | Description           |
|--------------|---------------|-----------------------|
| Tenant Alpha | tenant-alpha  | GPU workload consumer |
| Tenant Beta  | tenant-beta   | GPU workload consumer |

> 📝 **NOTE:** Tenant records are pure organizational metadata in this model. Network infrastructure
> racks and devices (leaf switches, spine switches, management, edge) are assigned to the
> `Operator` tenant (Step 1.0). Only GPU compute racks and devices carry a workload tenant
> assignment.

> ⚠️ **GAP #1 — Fabric model carries a single tenant FK:**
> The `Fabric` model has a single `tenant = ForeignKey('tenancy.Tenant')` field. Set it to
> `Operator` (see Step 1.0). Tenant identity for compute nodes flows through
> `PlantNode.source` → device → device tenant. If you need to model a fabric shared across
> multiple workload tenants, the `Fabric.tenant` field cannot express that relationship — it
> should hold the infrastructure owner (`Operator`), not any workload tenant.

UI capture:

![Step 1.1 Tenant Alpha add form](images/runbook-roce-phase1-ui/phase1-step11-tenant-alpha-form.png)

![Step 1.1 Tenant Alpha detail](images/runbook-roce-phase1-ui/phase1-step11-tenant-alpha-detail.png)

![Step 1.1 Tenant Beta add form](images/runbook-roce-phase1-ui/phase1-step11-tenant-beta-form.png)

![Step 1.1 Tenant Beta detail](images/runbook-roce-phase1-ui/phase1-step11-tenant-beta-detail.png)

### Step 1.2 — Create tenant groups (optional)

If your environment distinguishes workload tenants from infrastructure, create a tenant group:

- **Name:** GPU Workload Tenants  
- Assign `tenant-alpha` and `tenant-beta` to this group.

UI capture:

![Step 1.2 tenant group add form](images/runbook-roce-phase1-ui/phase1-step12-tenant-group-form.png)

![Step 1.2 tenant group detail](images/runbook-roce-phase1-ui/phase1-step12-tenant-group-detail.png)

![Step 1.2 Tenant Alpha group edit](images/runbook-roce-phase1-ui/phase1-step12-tenant-group-tenant-alpha-edit.png)

![Step 1.2 Tenant Alpha group updated](images/runbook-roce-phase1-ui/phase1-step12-tenant-group-tenant-alpha-updated.png)

![Step 1.2 Tenant Beta group edit](images/runbook-roce-phase1-ui/phase1-step12-tenant-group-tenant-beta-edit.png)

![Step 1.2 Tenant Beta group updated](images/runbook-roce-phase1-ui/phase1-step12-tenant-group-tenant-beta-updated.png)

---

## Phase 2: Site Hierarchy (NetBox Core)

### Step 2.1 — Create the site

Navigate to: **Organization → Sites → Add**

| Field       | Value     |
|-------------|-----------|
| Name        | DC Alpha  |
| Slug        | dc-alpha  |
| Status      | Active    |

UI capture:

![Step 2.1 site add form](images/runbook-roce-phase2-ui/phase2-step21-site-form.png)

![Step 2.1 site detail](images/runbook-roce-phase2-ui/phase2-step21-site-detail.png)

### Step 2.2 — Create the building location

Navigate to: **Organization → Locations → Add**

| Field  | Value      |
|--------|------------|
| Name   | Building 1 |
| Slug   | building-1 |
| Site   | DC Alpha   |
| Parent | (none)     |

UI capture:

![Step 2.2 building location add form](images/runbook-roce-phase2-ui/phase2-step22-building-1-form.png)

![Step 2.2 building location detail](images/runbook-roce-phase2-ui/phase2-step22-building-1-detail.png)

### Step 2.3 — Create compute halls

Create four child locations under **building-1**:

| Name             | Slug             | Parent     | Description                     |
|------------------|------------------|------------|---------------------------------|
| Hall Compute A   | hall-compute-a   | building-1 | Compute hall 1 (GPU workloads)  |
| Hall Compute B   | hall-compute-b   | building-1 | Compute hall 2 (GPU workloads)  |
| Hall Compute C   | hall-compute-c   | building-1 | Compute hall 3 (GPU workloads)  |
| Hall Network     | hall-network     | building-1 | Network-dedicated hall          |

> 📝 **NOTE:** The plugin's `Fabric.scope_location` FK supports scoping a fabric to a single
> location. Since this RoCE fabric spans all three compute halls and their leaf switches (which are
> located in each hall), the appropriate scope for this fabric is **site-level** (`scope_site =
> dc-alpha`), not location-level. If you later run multiple distinct fabrics in this building, you
> would need per-pod/per-hall location scoping.

UI capture:

![Step 2.3 Hall Compute A add form](images/runbook-roce-phase2-ui/phase2-step23-hall-compute-a-form.png)

![Step 2.3 Hall Compute A detail](images/runbook-roce-phase2-ui/phase2-step23-hall-compute-a-detail.png)

![Step 2.3 Hall Compute B add form](images/runbook-roce-phase2-ui/phase2-step23-hall-compute-b-form.png)

![Step 2.3 Hall Compute B detail](images/runbook-roce-phase2-ui/phase2-step23-hall-compute-b-detail.png)

![Step 2.3 Hall Compute C add form](images/runbook-roce-phase2-ui/phase2-step23-hall-compute-c-form.png)

![Step 2.3 Hall Compute C detail](images/runbook-roce-phase2-ui/phase2-step23-hall-compute-c-detail.png)

![Step 2.3 Hall Network add form](images/runbook-roce-phase2-ui/phase2-step23-hall-network-form.png)

![Step 2.3 Hall Network detail](images/runbook-roce-phase2-ui/phase2-step23-hall-network-detail.png)

---

## Phase 3: Device Roles (NetBox Core)

Navigate to: **Devices → Device Roles → Add**

Create the following roles. The `slug` values must be consistent — the transformer uses device role
slugs (via `_device_role_name()`) to classify node types:

| Name                   | Slug                    | VM Role | Color    |
|------------------------|-------------------------|---------|----------|
| GPU Server             | gpu-server              | No      | #00bcd4  |
| GPU-Leaf Shuffle Board | gpu-leaf-shuffle-board  | No      | #607d8b  |
| RoCE Leaf Switch       | roce-leaf-switch        | No      | #4caf50  |
| RoCE Spine Switch      | roce-spine-switch       | No      | #8bc34a  |
| Frontside Leaf Switch  | frontside-leaf-switch   | No      | #ff9800  |
| Frontside Spine Switch | frontside-spine-switch  | No      | #ff5722  |
| Management Switch      | management-switch       | No      | #9e9e9e  |
| Edge Router            | edge-router             | No      | #795548  |

> ⚠️ **GAP #2 — No explicit fabric-tier-to-device-role mapping:**
> The `Fabric` model has a `tier_depth` field (default 3: leaf/spine/meta-spine) but carries no
> explicit mapping from NetBox device roles to fabric tiers. The plugin transformer currently
> classifies `PlantNode.role` values as free-form text derived from the device role slug. There is
> no mechanism that definitively tells the plugin "devices with role `roce-leaf-switch` are tier-0
> (leaf), and devices with role `roce-spine-switch` are tier-1 (spine) in this fabric." This means
> the plane audit and path resolver cannot enforce tier-depth policy without an additional
> convention. Options: (a) add a custom field `fabric_tier` to the Device Role model; (b) add an
> explicit device-role-to-tier mapping to the `Fabric` model; (c) derive tier from graph depth
> (post-rebuild). This runbook proceeds with role slugs embedded verbatim in `PlantNode.role`; you
> will likely see missing tier validation in audit results.

UI capture:

![Phase 3 GPU Server role add form](images/runbook-roce-phase3-ui/phase3-step3-gpu-server-form.png)

![Phase 3 GPU Server role detail](images/runbook-roce-phase3-ui/phase3-step3-gpu-server-detail.png)

![Phase 3 GPU-Leaf Shuffle Board role add form](images/runbook-roce-phase3-ui/phase3-step3-gpu-leaf-shuffle-board-form.png)

![Phase 3 GPU-Leaf Shuffle Board role detail](images/runbook-roce-phase3-ui/phase3-step3-gpu-leaf-shuffle-board-detail.png)

![Phase 3 RoCE Leaf Switch role add form](images/runbook-roce-phase3-ui/phase3-step3-roce-leaf-switch-form.png)

![Phase 3 RoCE Leaf Switch role detail](images/runbook-roce-phase3-ui/phase3-step3-roce-leaf-switch-detail.png)

![Phase 3 RoCE Spine Switch role add form](images/runbook-roce-phase3-ui/phase3-step3-roce-spine-switch-form.png)

![Phase 3 RoCE Spine Switch role detail](images/runbook-roce-phase3-ui/phase3-step3-roce-spine-switch-detail.png)

![Phase 3 Frontside Leaf Switch role add form](images/runbook-roce-phase3-ui/phase3-step3-frontside-leaf-switch-form.png)

![Phase 3 Frontside Leaf Switch role detail](images/runbook-roce-phase3-ui/phase3-step3-frontside-leaf-switch-detail.png)

![Phase 3 Frontside Spine Switch role add form](images/runbook-roce-phase3-ui/phase3-step3-frontside-spine-switch-form.png)

![Phase 3 Frontside Spine Switch role detail](images/runbook-roce-phase3-ui/phase3-step3-frontside-spine-switch-detail.png)

![Phase 3 Management Switch role add form](images/runbook-roce-phase3-ui/phase3-step3-management-switch-form.png)

![Phase 3 Management Switch role detail](images/runbook-roce-phase3-ui/phase3-step3-management-switch-detail.png)

![Phase 3 Edge Router role add form](images/runbook-roce-phase3-ui/phase3-step3-edge-router-form.png)

![Phase 3 Edge Router role detail](images/runbook-roce-phase3-ui/phase3-step3-edge-router-detail.png)

---

## Phase 4: Platforms (NetBox Core)

Navigate to: **Devices → Platforms → Add**

Platforms are optional but useful for distinguishing NOS types. Create at minimum:

| Name              | Slug          | Manufacturer     |
|-------------------|---------------|------------------|
| GPU Host OS       | gpu-host-os   | (your vendor)    |
| RoCE Switch NOS   | roce-nos      | (your vendor)    |
| Frontside NOS     | frontside-nos | (your vendor)    |

UI capture:

![Phase 4 GPU Host OS add form](images/runbook-roce-phase4-ui/phase4-gpu-host-os-form.png)

![Phase 4 GPU Host OS detail](images/runbook-roce-phase4-ui/phase4-gpu-host-os-detail.png)

![Phase 4 RoCE Switch NOS add form](images/runbook-roce-phase4-ui/phase4-roce-nos-form.png)

![Phase 4 RoCE Switch NOS detail](images/runbook-roce-phase4-ui/phase4-roce-nos-detail.png)

![Phase 4 Frontside NOS add form](images/runbook-roce-phase4-ui/phase4-frontside-nos-form.png)

![Phase 4 Frontside NOS detail](images/runbook-roce-phase4-ui/phase4-frontside-nos-detail.png)

---

## Phase 5: Device Types (NetBox Core)

Navigate to: **Devices → Device Types → Add**

Device types define the interface templates that will be auto-generated when a device is
instantiated. Getting interface templates right here saves significant manual work later.

### Step 5.1 — GPU server device type

| Field          | Value                                          |
|----------------|------------------------------------------------|
| Manufacturer   | (your GPU server vendor)                       |
| Model          | GPU-Server-8x800G                              |
| Slug           | gpu-server-8x800g                              |
| U height       | 2                                              |
| Is full depth  | Yes                                            |

After creating the device type, add **Interface Templates**:

| Name  | Type              | Speed   | Management-only | Count |
|-------|-------------------|---------|-----------------|-------|
| NIC0  | 800GBASE (or DAC) | 800,000 | No              | 1     |
| NIC1  | 800GBASE (or DAC) | 800,000 | No              | 1     |
| mgmt0 | 1000BASE-T        | 1,000   | Yes             | 1     |

> 📝 **NOTE:** Child interface creation is done **per-device instance** after instantiation, not in
> the device type template. NetBox device types support interface templates for physical ports only;
> breakout children are created on the live device object.

> ⚠️ **GAP #3 — No device type template for child (breakout) interfaces:**
> NetBox 4.5 interface templates in device types cannot express breakout children. When you
> instantiate a GPU server from this device type, you get `NIC0` and `NIC1` but not the four 200G
> children. Every operator must manually create (or script) the child interfaces on each instantiated
> device. For 24 GPU servers × 2 ports × 4 children = 192 child interfaces, this is not acceptable
> as a manual UI workflow. You need either: (a) a script/import to bulk-create children from a CSV,
> or (b) a NetBox script job, before this runbook can proceed at scale.

UI capture:

![Phase 5.1 GPU server device type add form](images/runbook-roce-phase5-ui/phase5-step51-gpu-server-8x800g-device-type-form.png)

![Phase 5.1 GPU server interface template add form](images/runbook-roce-phase5-ui/phase5-step51-gpu-server-8x800g-interface-template-form.png)

![Phase 5.1 GPU server device type detail](images/runbook-roce-phase5-ui/phase5-step51-gpu-server-8x800g-detail.png)

### Step 5.2 — RoCE plane leaf switch device type

| Field        | Value                 |
|--------------|-----------------------|
| Manufacturer | (your switch vendor)  |
| Model        | RoCE-Leaf-Plane-200G  |
| Slug         | roce-leaf-200g-plane  |
| U height     | 1                     |

Add Interface Templates:

| Name pattern | Type | Speed   | Count | Notes                               |
|--------------|------|---------|-------|-------------------------------------|
| Eth1/1-16    | 200G | 200,000 | 16    | Plane-dedicated downlinks to shuffle |
| Eth1/49      | 800G | 800,000 | 1     | Plane-dedicated uplink parent to spine shuffle |

> 📝 **NOTE:** Each leaf device carries exactly one fabric plane. Set `fabric_plane` on the leaf
> interfaces in Phase 8 so the graph seeds native membership on the active-switch side as well.

UI capture:

![Phase 5.2 RoCE leaf device type add form](images/runbook-roce-phase5-ui/phase5-step52-roce-leaf-200g-plane-device-type-form.png)

![Phase 5.2 RoCE leaf interface template add form](images/runbook-roce-phase5-ui/phase5-step52-roce-leaf-200g-plane-interface-template-form.png)

![Phase 5.2 RoCE leaf device type detail](images/runbook-roce-phase5-ui/phase5-step52-roce-leaf-200g-plane-detail.png)

### Step 5.3 — RoCE plane spine switch device type

| Field | Value               |
|-------|---------------------|
| Model | RoCE-Spine-Plane-200G |
| Slug  | roce-spine-200g-plane |

Add Interface Templates:

| Name pattern | Type | Speed   | Count | Notes                          |
|--------------|------|---------|-------|--------------------------------|
| Eth1/1-6     | 200G | 200,000 | 6     | Three halls × two channels per hall |

UI capture:

![Phase 5.3 RoCE spine device type add form](images/runbook-roce-phase5-ui/phase5-step53-roce-spine-200g-plane-device-type-form.png)

![Phase 5.3 RoCE spine interface template add form](images/runbook-roce-phase5-ui/phase5-step53-roce-spine-200g-plane-interface-template-form.png)

![Phase 5.3 RoCE spine device type detail](images/runbook-roce-phase5-ui/phase5-step53-roce-spine-200g-plane-detail.png)

### Step 5.4 — GPU-leaf shuffle-board device type

| Field        | Value                    |
|--------------|--------------------------|
| Manufacturer | (generic / passive vendor) |
| Model        | GPU-Leaf-Shuffle-1x4     |
| Slug         | gpu-leaf-shuffle-1x4     |
| U height     | 1                        |

Create this as a passive device type. In the executable dev model, each shuffle module has:

- one `RearPort` named `bp1`
- four `FrontPort`s named `fp1`–`fp4`
- four `PortMapping` rows (`rear position 1..4 -> front port fp1..fp4`)

> 📝 **NOTE:** This is the logical abstraction of the physical MPO-12 strand map documented in the
> Reference Topology section above.

UI capture:

![Phase 5.4 GPU-leaf shuffle device type add form](images/runbook-roce-phase5-ui/phase5-step54-gpu-leaf-shuffle-1x4-device-type-form.png)

![Phase 5.4 GPU-leaf shuffle rear port form](images/runbook-roce-phase5-ui/phase5-step54-gpu-leaf-shuffle-1x4-rear-port-form.png)

![Phase 5.4 GPU-leaf shuffle front port form](images/runbook-roce-phase5-ui/phase5-step54-gpu-leaf-shuffle-1x4-front-port-form.png)

![Phase 5.4 GPU-leaf shuffle device type detail](images/runbook-roce-phase5-ui/phase5-step54-gpu-leaf-shuffle-1x4-detail.png)

### Step 5.5 — Leaf-spine shuffle-board device type

| Field        | Value                     |
|--------------|---------------------------|
| Manufacturer | (generic / passive vendor) |
| Model        | Leaf-Spine-Shuffle-1x4    |
| Slug         | leaf-spine-shuffle-1x4    |
| U height     | 1                         |

Create this as a passive device type. In the executable dev model, each leaf-spine shuffle module
has:

- one `RearPort` named `bp1`
- four `FrontPort`s named `fp1`–`fp4`
- four `PortMapping` rows (`rear position 1..4 -> front port fp1..fp4`)

> 📝 **NOTE:** This is the logical abstraction of the strand-level leaf→spine shuffle documented in
> the Reference Topology section above.

UI capture:

![Phase 5.5 Leaf-spine shuffle device type add form](images/runbook-roce-phase5-ui/phase5-step55-leaf-spine-shuffle-1x4-device-type-form.png)

![Phase 5.5 Leaf-spine shuffle rear port form](images/runbook-roce-phase5-ui/phase5-step55-leaf-spine-shuffle-1x4-rear-port-form.png)

![Phase 5.5 Leaf-spine shuffle front port form](images/runbook-roce-phase5-ui/phase5-step55-leaf-spine-shuffle-1x4-front-port-form.png)

![Phase 5.5 Leaf-spine shuffle device type detail](images/runbook-roce-phase5-ui/phase5-step55-leaf-spine-shuffle-1x4-detail.png)

### Step 5.6 — Frontside leaf, frontside spine, management switch, edge router

Create device types for these following the same pattern. Frontside interfaces use 100G–400G
depending on your environment. Edge routers use WAN-appropriate interface types.

> 📝 **NOTE:** These device types are standard NetBox inventory work. The plant graph plugin does
> **not** model frontside, management, or edge infrastructure. Steps 5.6 through the management and
> edge entries are NetBox core inventory only.

UI capture:

![Phase 5.6 Frontside leaf device type add form](images/runbook-roce-phase5-ui/phase5-step56-frontside-leaf-generic-device-type-form.png)

![Phase 5.6 Frontside leaf device type detail](images/runbook-roce-phase5-ui/phase5-step56-frontside-leaf-generic-detail.png)

![Phase 5.6 Frontside spine device type add form](images/runbook-roce-phase5-ui/phase5-step56-frontside-spine-generic-device-type-form.png)

![Phase 5.6 Frontside spine device type detail](images/runbook-roce-phase5-ui/phase5-step56-frontside-spine-generic-detail.png)

![Phase 5.6 Management switch device type add form](images/runbook-roce-phase5-ui/phase5-step56-management-switch-generic-device-type-form.png)

![Phase 5.6 Management switch device type detail](images/runbook-roce-phase5-ui/phase5-step56-management-switch-generic-detail.png)

![Phase 5.6 Edge router device type add form](images/runbook-roce-phase5-ui/phase5-step56-edge-router-generic-device-type-form.png)

![Phase 5.6 Edge router device type detail](images/runbook-roce-phase5-ui/phase5-step56-edge-router-generic-detail.png)

---

## Phase 6: Rack Setup (NetBox Core)

Navigate to: **Rack & Cabling → Racks → Add**

### Step 6.1 — Compute hall racks (repeat for halls A, B, C)

**Hall A racks (hall-compute-a):**

| Rack Name          | Site     | Location       | Tenant       | Height | Width  |
|--------------------|----------|----------------|--------------|--------|--------|
| RACK-A-GPU-A1      | DC Alpha | hall-compute-a | Tenant Alpha | 42U    | 600 mm |
| RACK-A-GPU-A2      | DC Alpha | hall-compute-a | Tenant Alpha | 42U    | 600 mm |
| RACK-A-GPU-B1      | DC Alpha | hall-compute-a | Tenant Beta  | 42U    | 600 mm |
| RACK-A-GPU-B2      | DC Alpha | hall-compute-a | Tenant Beta  | 42U    | 600 mm |
| RACK-A-SHUFFLE-1   | DC Alpha | hall-compute-a | (none)       | 42U    | 600 mm |
| RACK-A-ROCE-LEAF-P1| DC Alpha | hall-compute-a | (none)       | 42U    | 600 mm |
| RACK-A-ROCE-LEAF-P2| DC Alpha | hall-compute-a | (none)       | 42U    | 600 mm |
| RACK-A-ROCE-LEAF-P3| DC Alpha | hall-compute-a | (none)       | 42U    | 600 mm |
| RACK-A-ROCE-LEAF-P4| DC Alpha | hall-compute-a | (none)       | 42U    | 600 mm |
| RACK-A-FS-LEAF-1   | DC Alpha | hall-compute-a | (none)       | 42U    | 600 mm |

Repeat for halls B and C, substituting `B` / `C` prefixes.

UI capture:

![Phase 6.1 compute rack add form](images/runbook-roce-phase6-ui/phase6-step61-rack-a-gpu-a1-form.png)

![Phase 6.1 compute rack detail](images/runbook-roce-phase6-ui/phase6-step61-rack-a-gpu-a1-detail.png)

### Step 6.2 — Network hall racks

| Rack Name             | Site     | Location     | Tenant | Height | Width  |
|-----------------------|----------|--------------|--------|--------|--------|
| RACK-NET-ROCE-SHUFFLE-P1| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SHUFFLE-P2| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SHUFFLE-P3| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SHUFFLE-P4| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P1-A| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P1-B| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P2-A| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P2-B| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P3-A| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P3-B| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P4-A| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-ROCE-SPINE-P4-B| DC Alpha | hall-network | (none) | 42U | 600 mm |
| RACK-NET-FS-SPINE-1   | DC Alpha | hall-network | (none) | 42U    | 600 mm |
| RACK-NET-FS-SPINE-2   | DC Alpha | hall-network | (none) | 42U    | 600 mm |
| RACK-NET-MGMT-1       | DC Alpha | hall-network | (none) | 42U    | 600 mm |
| RACK-NET-EDGE-1       | DC Alpha | hall-network | (none) | 42U    | 600 mm |

UI capture:

![Phase 6.2 network rack add form](images/runbook-roce-phase6-ui/phase6-step62-rack-net-roce-shuffle-p1-form.png)

![Phase 6.2 network rack detail](images/runbook-roce-phase6-ui/phase6-step62-rack-net-roce-shuffle-p1-detail.png)

---

## Phase 7: Device Instantiation (NetBox Core)

Navigate to: **Devices → Devices → Add**

### Step 7.1 — GPU servers (24 total)

Create 2 devices per GPU rack. Shown for rack `RACK-A-GPU-A1` (Tenant Alpha, Hall A):

| Name           | Device Type           | Role        | Site     | Rack            | Position | Tenant       |
|----------------|-----------------------|-------------|----------|-----------------|----------|--------------|
| gpu-a-a1-001   | GPU-Server-8x800G     | GPU Server  | DC Alpha | RACK-A-GPU-A1   | 1U       | Tenant Alpha |
| gpu-a-a1-002   | GPU-Server-8x800G     | GPU Server  | DC Alpha | RACK-A-GPU-A1   | 3U       | Tenant Alpha |

Repeat for all 12 GPU racks (24 total GPU servers across both tenants).

**Naming convention:**
- `gpu-{hall}-{rack-code}-{seq}` where hall = `a`/`b`/`c`, rack-code = `a1|a2|b1|b2`, seq = `001|002`
- Example: `gpu-b-b1-002` = Hall B, tenant-beta rack B1, second server

> 📝 **NOTE:** The device template auto-creates `NIC0`, `NIC1`, and `mgmt0` interfaces on each
> GPU server. Child interfaces (`NIC0.plane1` through `NIC0.plane4`) are created in Phase 8.

### Step 7.2 — RoCE leaf switches (12 total, four per compute hall)

Create one leaf per plane in each compute hall. Shown for Hall A:

| Name            | Device Type          | Role             | Site     | Rack                  | Tenant |
|-----------------|----------------------|------------------|----------|-----------------------|--------|
| roce-leaf-ha-p1 | RoCE-Leaf-Plane-200G | RoCE Leaf Switch | DC Alpha | RACK-A-ROCE-LEAF-P1   | (none) |
| roce-leaf-ha-p2 | RoCE-Leaf-Plane-200G | RoCE Leaf Switch | DC Alpha | RACK-A-ROCE-LEAF-P2   | (none) |
| roce-leaf-ha-p3 | RoCE-Leaf-Plane-200G | RoCE Leaf Switch | DC Alpha | RACK-A-ROCE-LEAF-P3   | (none) |
| roce-leaf-ha-p4 | RoCE-Leaf-Plane-200G | RoCE Leaf Switch | DC Alpha | RACK-A-ROCE-LEAF-P4   | (none) |

Repeat for halls B and C.

### Step 7.3 — RoCE spine switches (8 total, two per plane)

| Name              | Device Type           | Role              | Site     | Rack                   | Tenant |
|-------------------|-----------------------|-------------------|----------|------------------------|--------|
| roce-spine-net-p1-a | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P1-A | (none) |
| roce-spine-net-p1-b | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P1-B | (none) |
| roce-spine-net-p2-a | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P2-A | (none) |
| roce-spine-net-p2-b | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P2-B | (none) |
| roce-spine-net-p3-a | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P3-A | (none) |
| roce-spine-net-p3-b | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P3-B | (none) |
| roce-spine-net-p4-a | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P4-A | (none) |
| roce-spine-net-p4-b | RoCE-Spine-Plane-200G | RoCE Spine Switch | DC Alpha | RACK-NET-ROCE-SPINE-P4-B | (none) |

### Step 7.4 — GPU-leaf shuffle modules

Create one logical shuffle module per GPU parent port (`NIC0` and `NIC1`). These live in the
hall shuffle rack.

Example for `gpu-a-a1-001`:

| Name                             | Device Type           | Role                    | Rack             |
|----------------------------------|-----------------------|-------------------------|------------------|
| gpu-leaf-shuffle-a-a1-001-nic0   | GPU-Leaf-Shuffle-1x4  | GPU-Leaf Shuffle Board  | RACK-A-SHUFFLE-1 |
| gpu-leaf-shuffle-a-a1-001-nic1   | GPU-Leaf-Shuffle-1x4  | GPU-Leaf Shuffle Board  | RACK-A-SHUFFLE-1 |

Repeat for both parent NICs on every GPU server.

### Step 7.5 — Leaf-spine shuffle modules

Create one logical leaf-spine shuffle module per hall per plane. These live in the network-hall
plane shuffle rack.

Examples:

| Name                    | Device Type             | Role                      | Rack                    |
|-------------------------|-------------------------|---------------------------|-------------------------|
| leaf-spine-shuffle-ha-p1| Leaf-Spine-Shuffle-1x4  | Leaf-Spine Shuffle Board  | RACK-NET-ROCE-SHUFFLE-P1 |
| leaf-spine-shuffle-hb-p1| Leaf-Spine-Shuffle-1x4  | Leaf-Spine Shuffle Board  | RACK-NET-ROCE-SHUFFLE-P1 |
| leaf-spine-shuffle-hc-p1| Leaf-Spine-Shuffle-1x4  | Leaf-Spine Shuffle Board  | RACK-NET-ROCE-SHUFFLE-P1 |

Repeat for planes 2, 3, and 4.

### Step 7.6 — Frontside, management, and edge devices

Create one device of each remaining type in their respective network-hall racks. These are
standard NetBox inventory entries with no plugin-specific behavior.

| Name              | Device Type              | Role                   | Rack                |
|-------------------|--------------------------|------------------------|---------------------|
| fs-leaf-ha-1      | (frontside leaf type)    | Frontside Leaf Switch  | RACK-A-FS-LEAF-1    |
| fs-leaf-hb-1      | (frontside leaf type)    | Frontside Leaf Switch  | RACK-B-FS-LEAF-1    |
| fs-leaf-hc-1      | (frontside leaf type)    | Frontside Leaf Switch  | RACK-C-FS-LEAF-1    |
| fs-spine-net-1    | (frontside spine type)   | Frontside Spine Switch | RACK-NET-FS-SPINE-1 |
| fs-spine-net-2    | (frontside spine type)   | Frontside Spine Switch | RACK-NET-FS-SPINE-2 |
| mgmt-sw-net-1     | (mgmt switch type)       | Management Switch      | RACK-NET-MGMT-1     |
| edge-rtr-net-1    | (edge router type)       | Edge Router            | RACK-NET-EDGE-1     |

---

## Phase 8: Interface Modeling — Breakout & Custom Fields (NetBox Core + Config)

This is the most labor-intensive phase and the most critical for plugin correctness.

### Step 8.1 — Create the `fabric_plane` custom field

Before creating child interfaces, you must create the NetBox custom field that the transformer
reads to assign plane membership.

Navigate to: **Customization → Custom Fields → Add**

| Field          | Value                   |
|----------------|-------------------------|
| Name           | fabric_plane            |
| Label          | Fabric Plane            |
| Type           | Integer                 |
| Object type(s) | dcim.interface          |
| Required       | No                      |
| Default        | (empty)                 |
| Minimum value  | 1                       |
| Maximum value  | (leave empty or 64)     |
| Description    | RoCE fabric plane number (1–N) for this attachment unit |
| UI visible     | Yes                     |
| UI editable    | Yes                     |

> 📝 **NOTE:** The field name `fabric_plane` matches the plugin's `default_plane_field_name`
> setting (see Phase 10). If you use a different name here, you must update the plugin config
> setting to match. The transformer reads this field with:
> `custom_field_data.get('fabric_plane')` on child interface objects.

> ⚠️ **GAP #4 — RESOLVED:** The plugin now manages this more safely:
> - `post_migrate` ensures the configured plane custom field (default: `fabric_plane`) exists
>   and is attached to `dcim.Interface`
> - a Django system check warns if the field is missing or detached from `dcim.Interface`
> - rebuilds that produce zero `PlaneMembership` rows now emit an explicit warning in
>   `GraphBuildRun.stats['warnings']`
>
> Manual verification is still worth doing in a fresh environment, but the silent-failure trap
> is no longer the current behavior.

### Step 8.2 — Create child interfaces on GPU servers using DeviceBreakoutTemplate

> ⚠️ **GAP #3 + GAP #6 — RESOLVED:** The plugin now ships a `DeviceBreakoutTemplate` and
> `DeviceChildInterfaceSpec` mechanism that automates breakout child creation. The reference
> template `gpu-server-800g-4plane` (slug) is pre-loaded by the plugin data migration and covers
> exactly the NIC0/NIC1 → 4×200G virtual child pattern used in this runbook.

**Option A (recommended) — use `stamp_rack_population` with the template assigned to slots:**

When creating `RackPopulationSlot` records for GPU server racks (Phase 6 / rack population
workflow), set `breakout_template = gpu-server-800g-4plane` on each GPU server slot. When you run
`stamp_rack_population`, child interfaces are created automatically.

Navigate to: **Assembly Templates → Rack Population Templates → [your GPU rack template]**  
Open each GPU server slot and set **Breakout template** to `GPU Server 800G 4-Plane Breakout`.

**Option B — apply to existing devices via the management command:**

For devices that were already instantiated before the breakout template existed:

```bash
python manage.py bulk_apply_breakout \
    --template gpu-server-800g-4plane \
    --role gpu-server
```

Add `--dry-run` first to preview the changes without committing.

This creates the following child interfaces on every GPU server:

| Name           | Parent | Type    | Speed (kbps) | custom: fabric_plane |
|----------------|--------|---------|--------------|----------------------|
| NIC0.plane1    | NIC0   | virtual | 200,000      | 1                    |
| NIC0.plane2    | NIC0   | virtual | 200,000      | 2                    |
| NIC0.plane3    | NIC0   | virtual | 200,000      | 3                    |
| NIC0.plane4    | NIC0   | virtual | 200,000      | 4                    |
| NIC1.plane1    | NIC1   | virtual | 200,000      | 1                    |
| NIC1.plane2    | NIC1   | virtual | 200,000      | 2                    |
| NIC1.plane3    | NIC1   | virtual | 200,000      | 3                    |
| NIC1.plane4    | NIC1   | virtual | 200,000      | 4                    |

> ⚠️ **GAP #5 — Interface type for 200G child of 800G physical port:**
> The reference template uses `virtual` for child interface type, which is functionally correct
> for the plugin graph but loses cable-type metadata. If your environment requires accurate
> physical signaling types (e.g. `200gbase-cr4` for DAC), update the `DeviceChildInterfaceSpec`
> records on the `gpu-server-800g-4plane` template before running the bulk apply:
> navigate to **Assembly Templates → Device Child Interface Specs** and edit the
> `child_interface_type` field on both the NIC0 and NIC1 specs.

> ⚠️ **GAP #3 — Reference to custom DeviceBreakoutTemplate:**
> Navigate to **Assembly Templates → Device Breakout Templates** to view or clone
> `GPU Server 800G 4-Plane Breakout`. Create additional templates for other device types
> following the same pattern.

**This step is a blocker for Phase 13 (graph rebuild) producing correct plane membership.**

### Step 8.3 — Create plane-dedicated interfaces on RoCE leaves and spines

Because active switching is physically disjoint by plane, downlinks on the leaf and hall-facing
interfaces on the spine are plain 200G interfaces with a single `fabric_plane` value. The leaf
uplink is modeled as one 800G parent plus four 200G child interfaces so the leaf→spine shuffle can
use the same breakout/path pattern as the GPU→leaf leg.

For `roce-leaf-ha-p1`, create:

| Name        | Type | Speed   | fabric_plane | Purpose                              |
|-------------|------|---------|--------------|--------------------------------------|
| Eth1/1      | 200G | 200,000 | 1            | GPU/shuffle downlink                 |
| ...         | ...  | ...     | 1            | Repeat through `Eth1/16`             |
| Eth1/49     | 800G | 800,000 | (none)       | Uplink parent to leaf-spine shuffle  |
| Eth1/49.ch1 | 200G | 200,000 | 1            | Uplink child channel 1               |
| Eth1/49.ch2 | 200G | 200,000 | 1            | Uplink child channel 2               |
| Eth1/49.ch3 | 200G | 200,000 | 1            | Uplink child channel 3               |
| Eth1/49.ch4 | 200G | 200,000 | 1            | Uplink child channel 4               |

For `roce-spine-net-p1-a`, create:

| Name   | Type | Speed   | fabric_plane | Purpose                       |
|--------|------|---------|--------------|-------------------------------|
| Eth1/1 | 200G | 200,000 | 1            | Hall A channel 1              |
| Eth1/2 | 200G | 200,000 | 1            | Hall A channel 2              |
| Eth1/3 | 200G | 200,000 | 1            | Hall B channel 1              |
| Eth1/4 | 200G | 200,000 | 1            | Hall B channel 2              |
| Eth1/5 | 200G | 200,000 | 1            | Hall C channel 1              |
| Eth1/6 | 200G | 200,000 | 1            | Hall C channel 2              |

Repeat the same pattern for plane-1 spine B and for planes 2, 3, and 4 on the corresponding leaf
and spine devices.

> ⚠️ **GAP #7 — Plane assignment on switch ports still relies on operator discipline:**
> The switch plane is now carried by the device/rack assignment as well as the interface custom
> field, but NetBox still does not prevent a Hall A plane-1 shuffle front port from being cabled to
> `roce-leaf-ha-p2`. Validation still happens after rebuild/audit, not at data-entry time.

### Step 8.4 — Create passive GPU-leaf shuffle ports and mappings

For each GPU-leaf shuffle module, create:

| Object     | Name | Type | Positions | Notes |
|------------|------|------|-----------|-------|
| RearPort   | bp1  | MPO  | 4 logical positions | Represents the four active breakout channels |
| FrontPort  | fp1  | MPO  | 1         | Plane 1 egress |
| FrontPort  | fp2  | MPO  | 1         | Plane 2 egress |
| FrontPort  | fp3  | MPO  | 1         | Plane 3 egress |
| FrontPort  | fp4  | MPO  | 1         | Plane 4 egress |

Then create four `PortMapping` rows:

| RearPort | Rear position | FrontPort | Front position |
|----------|---------------|-----------|----------------|
| bp1      | 1             | fp1       | 1              |
| bp1      | 2             | fp2       | 1              |
| bp1      | 3             | fp3       | 1              |
| bp1      | 4             | fp4       | 1              |

> 📝 **NOTE:** These four logical positions are the executable abstraction of the strand-level
> MPO-12 shuffle documented in the Reference Topology section.

### Step 8.5 — Create passive leaf-spine shuffle ports and mappings

For each leaf-spine shuffle module, create the same passive port shape:

| Object     | Name | Type | Positions | Notes |
|------------|------|------|-----------|-------|
| RearPort   | bp1  | MPO  | 4 logical positions | Represents the four active leaf uplink channels |
| FrontPort  | fp1  | MPO  | 1         | Spine A channel 1 |
| FrontPort  | fp2  | MPO  | 1         | Spine A channel 2 |
| FrontPort  | fp3  | MPO  | 1         | Spine B channel 1 |
| FrontPort  | fp4  | MPO  | 1         | Spine B channel 2 |

Then create four `PortMapping` rows:

| RearPort | Rear position | FrontPort | Front position |
|----------|---------------|-----------|----------------|
| bp1      | 1             | fp1       | 1              |
| bp1      | 2             | fp2       | 1              |
| bp1      | 3             | fp3       | 1              |
| bp1      | 4             | fp4       | 1              |

> 📝 **NOTE:** This is the executable abstraction of the leaf→spine strand shuffle documented in
> the Reference Topology section.

---

## Phase 9: Cabling — Backside / RoCE Network (NetBox Core)

Navigate to: **Rack & Cabling → Cables → Add**

Cables connect GPU parent ports to passive shuffle ports, then passive shuffle ports to plane
leafs, then plane leaf uplink parents to plane-local leaf-spine shuffle ports, then leaf-spine
shuffle ports to plane-specific spines. The plugin derives `CoarseEdge` records from cables and
uses `CablePath` pre-computed traces (including cable profile expansion and `PortMapping`
passthrough) to derive `FineEdge` records.

### Step 9.1 — Assign cable profiles (if applicable)

If your 800G DAC or breakout cables have a corresponding NetBox cable profile class, assign it
when creating each cable:

| Field           | Value                              |
|-----------------|------------------------------------|
| Cable type      | DAC (or appropriate)               |
| Cable profile   | (select matching profile class)    |
| Status          | Connected                          |
| Termination A   | gpu-a-a1-001 → NIC0               |
| Termination B   | gpu-leaf-shuffle-a-a1-001-nic0 → bp1 |

> ⚠️ **GAP #8 — RESOLVED:** The plugin does not use a raw `Cable.profile` string for
> plugin-defined breakout expansion. The correct mechanism is a plugin-managed Cable object custom
> field, `mpf_breakout_profile` by default, which points to a `BreakoutProfile` row:
>
> 1. Create a `BreakoutProfile` (once per cable type):
>    Navigate to **Connectivity Mapping → Breakout Profiles → Add**
>    | Field              | Value         |
>    |--------------------|---------------|
>    | Name               | 800G-4x200G   |
>    | Slug               | 800g-4x200g   |
>    | Parent speed Gbps  | 800           |
>    | Child count        | 4             |
>    | Child speed Gbps   | 200           |
>    | Mapping mode       | Sequential    |
>
> 2. Set the Cable breakout-profile custom field to that `BreakoutProfile` on each GPU-parent-to-
>    shuffle cable and each leaf-uplink-parent-to-leaf-spine-shuffle cable.
>
> Without this, `signal_lane_fine_edges` will be 0 and per-child FineEdge expansion will not fire.
> See Execution Log for confirmed results.

### Step 9.2 — Create GPU-parent to shuffle cables

For each GPU parent port, create one cable from the 800G parent interface to the shuffle module
rear port:

| Cable | Side A                   | Side B                                   | BreakoutProfile CF |
|-------|--------------------------|------------------------------------------|--------------------|
| 1     | gpu-a-a1-001 → NIC0      | gpu-leaf-shuffle-a-a1-001-nic0 → bp1     | 800g-4x200g        |
| 2     | gpu-a-a1-001 → NIC1      | gpu-leaf-shuffle-a-a1-001-nic1 → bp1     | 800g-4x200g        |
| 3     | gpu-a-a1-002 → NIC0      | gpu-leaf-shuffle-a-a1-002-nic0 → bp1     | 800g-4x200g        |
| ...   | ...                      | ...                                      | ...            |

This is the logical representation of the straight MPO-12 GPU→shuffle patch cord.

> 📝 **NOTE:** After each cable is saved, NetBox automatically recomputes the `CablePath` for
> affected ports. The `is_complete` flag on a `CablePath` is `True` only when both endpoint
> terminations are physical (interface/front-port/rear-port). For this topology, complete paths
> depend on both the breakout cable profile and the shuffle `PortMapping` rows being present.

### Step 9.3 — Create shuffle-to-leaf cables

For each shuffle module, cable the plane-facing front ports to the matching plane leaf in the same
hall:

| Cable | Side A                                     | Side B                    |
|-------|--------------------------------------------|---------------------------|
| 11    | gpu-leaf-shuffle-a-a1-001-nic0 → fp1       | roce-leaf-ha-p1 → Eth1/1  |
| 12    | gpu-leaf-shuffle-a-a1-001-nic0 → fp2       | roce-leaf-ha-p2 → Eth1/1  |
| 13    | gpu-leaf-shuffle-a-a1-001-nic0 → fp3       | roce-leaf-ha-p3 → Eth1/1  |
| 14    | gpu-leaf-shuffle-a-a1-001-nic0 → fp4       | roce-leaf-ha-p4 → Eth1/1  |

Repeat for every GPU parent port in each hall, incrementing the leaf downlink interfaces.

### Step 9.4 — Create leaf-uplink-parent to leaf-spine-shuffle cables

For each leaf, create one breakout-profile cable from the 800G parent uplink to the leaf-spine
shuffle module rear port:

| Cable | Side A                     | Side B                              | Profile     |
|-------|----------------------------|-------------------------------------|-------------|
| 201   | roce-leaf-ha-p1 → Eth1/49  | leaf-spine-shuffle-ha-p1 → bp1      | 800g-4x200g |
| 202   | roce-leaf-hb-p1 → Eth1/49  | leaf-spine-shuffle-hb-p1 → bp1      | 800g-4x200g |
| 203   | roce-leaf-hc-p1 → Eth1/49  | leaf-spine-shuffle-hc-p1 → bp1      | 800g-4x200g |
| ...   | ...                        | ...                                 | ...         |

### Step 9.5 — Create leaf-spine-shuffle to spine cables

Each plane-local leaf-spine shuffle module fans its four channels across the two spine switches for
that plane:

| Cable | Side A                                | Side B                          |
|-------|---------------------------------------|---------------------------------|
| 301   | leaf-spine-shuffle-ha-p1 → fp1        | roce-spine-net-p1-a → Eth1/1    |
| 302   | leaf-spine-shuffle-ha-p1 → fp2        | roce-spine-net-p1-a → Eth1/2    |
| 303   | leaf-spine-shuffle-ha-p1 → fp3        | roce-spine-net-p1-b → Eth1/1    |
| 304   | leaf-spine-shuffle-ha-p1 → fp4        | roce-spine-net-p1-b → Eth1/2    |
| 305   | leaf-spine-shuffle-hb-p1 → fp1        | roce-spine-net-p1-a → Eth1/3    |
| 306   | leaf-spine-shuffle-hb-p1 → fp2        | roce-spine-net-p1-a → Eth1/4    |
| 307   | leaf-spine-shuffle-hb-p1 → fp3        | roce-spine-net-p1-b → Eth1/3    |
| 308   | leaf-spine-shuffle-hb-p1 → fp4        | roce-spine-net-p1-b → Eth1/4    |
| ...   | ...                                   | ...                             |

### Step 9.6 — Frontside, management, and edge cabling

Cable frontside leaf, spine, management, and edge devices following your standard NetBox cabling
practices. These cables are **not** consumed by the plant graph plugin.

---

## Phase 10: IP Addressing & EVPN Overlay (NetBox Core)

The RoCEv2 L3 EVPN overlay lives entirely in NetBox core models. This phase is summarized
rather than fully prescribed, as it is standard NetBox IPAM/VPN work.

### Step 10.1 — VRFs

Create one VRF per tenant per plane (or per tenant if you use per-VRF plane isolation via VNIs):

| VRF Name                  | RD            | Tenant       |
|---------------------------|---------------|--------------|
| ROCE-ALPHA-PLANE1         | 65000:10001   | Tenant Alpha |
| ROCE-ALPHA-PLANE2         | 65000:10002   | Tenant Alpha |
| ROCE-ALPHA-PLANE3         | 65000:10003   | Tenant Alpha |
| ROCE-ALPHA-PLANE4         | 65000:10004   | Tenant Alpha |
| ROCE-BETA-PLANE1          | 65000:20001   | Tenant Beta  |
| ...                       | ...           | ...          |

> 🔍 **QUESTION — VRF-per-plane vs VRF-per-tenant-per-plane:** Whether plane isolation is achieved
> via separate VRFs or via separate VNIs within a shared VRF depends on your EVPN design. The
> plugin is topology-layer only and does not validate IP overlay consistency against the physical
> graph. Document your VRF/VNI allocation convention before populating IP addresses.

### Step 10.2 — L2VPN / EVPN instances

Navigate to: **VPN → L2VPNs** to create EVPN instances for each plane.

### Step 10.3 — Prefixes and IP addresses

Assign loopback addresses (VTEP), IRB gateway addresses, and host-facing IP addresses within
their respective VRFs using the IPAM module.

> ⚠️ **GAP #9 — Plugin has no IP/overlay awareness:**
> The plant graph plugin models the **physical and optical** transport layer only. It has no
> awareness of which VRFs, VNIs, or IP addresses correspond to which planes. A "disjointness
> violation" in the plugin means two planes share a physical attachment unit — it says nothing
> about whether the IP overlay is correctly isolated. The two validation layers (physical
> disjointness via the plugin, IP overlay correctness via NetBox IPAM/VPN) are independent and
> must be validated separately.

---

## Phase 11: Plugin Prerequisites & Configuration

Before creating plugin objects, ensure the plugin is correctly installed and configured.

### Step 11.1 — Verify plugin is loaded

Navigate to: **Admin → Installed Plugins**

Confirm `netbox_plant_graph` appears with status **Active**.

### Step 11.2 — Review plugin settings

In your NetBox `configuration.py`, add or confirm the following plugin config:

```python
PLUGINS_CONFIG = {
    'netbox_plant_graph': {
        'top_level_menu': True,
        'graph_default_resolution': 'attachment_unit',
        'materialize_signal_lanes': True,
        'default_plane_field_name': 'fabric_plane',   # must match Step 8.1
        'persist_unresolved_summaries': True,          # enable durable unresolved tracking
        'enable_incremental_refresh': True,
    }
}
```

> ⚠️ **GAP #10 — RESOLVED:** In the current plugin, `persist_unresolved_summaries` defaults to
> `True` in `PlantGraphConfig.default_settings`. Keep the setting explicit in local configs for
> readability, but a stock install no longer defaults to an empty unresolved-state dashboard.

### Step 11.3 — Run migrations

```bash
python manage.py migrate netbox_plant_graph
```

Verify no pending migrations:

```bash
python manage.py showmigrations netbox_plant_graph
```

---

## Phase 12: Plugin — Create Fabric and Fabric Planes

### Step 12.1 — Create the Fabric

Navigate to: **Plant Graph Tables → Fabrics → Add**

| Field                  | Value                 |
|------------------------|-----------------------|
| Name                   | ROCE-FABRIC-ALPHA     |
| Description            | Multi-tenant RoCEv2 fabric for DC Alpha |
| Expected plane count   | 4                     |
| Tier depth             | 2                     |
| Disjointness policy    | full                  |
| Tenant                 | (leave blank — see GAP #1) |
| Scope site             | DC Alpha              |
| Scope location         | (leave blank)         |

> 📝 **NOTE:** The `scope_site` field is what the extractor uses to scope device queries:
> `device_queryset.filter(site=site)`. Without `scope_site` set, the extractor will query
> **all devices in the entire NetBox instance** on every rebuild. Always set `scope_site`
> for any production fabric.

### Step 12.2 — Create Fabric Planes

Navigate to: **Plant Graph Tables → Fabric Planes → Add**

Create four planes under `ROCE-FABRIC-ALPHA`:

| Fabric             | Plane Number | Description              |
|--------------------|--------------|--------------------------|
| ROCE-FABRIC-ALPHA  | 1            | RoCE Plane 1             |
| ROCE-FABRIC-ALPHA  | 2            | RoCE Plane 2             |
| ROCE-FABRIC-ALPHA  | 3            | RoCE Plane 3             |
| ROCE-FABRIC-ALPHA  | 4            | RoCE Plane 4             |

> 📝 **NOTE:** These `FabricPlane` objects are the FK targets for `PlaneMembership` records. The
> transformer creates `PlaneMembership` rows by matching `plane_number` values from the
> `fabric_plane` custom field on child interfaces against these records. A rebuild run before
> `FabricPlane` records exist will **not fail** — it will simply produce no `PlaneMembership`
> rows. Create planes before the first rebuild.

> ⚠️ **GAP #11 — RESOLVED:** The builder now records a warning when attachment-unit membership
> input references a `plane_number` that has no matching `FabricPlane`. Look for
> `GraphBuildRun.stats['warnings']` entries with `code = "missing_fabric_plane_records"`.

---

## Phase 13: Plugin — Full Graph Rebuild

### Step 13.1 — Trigger the rebuild

The graph rebuild is implemented as a NetBox background job (`FullGraphRebuildJob`).

Navigate to: **Plant Graph Workflows → Health** (or the **Fabric** detail page)

Look for a **Rebuild Graph** action button or navigate to:

> 📝 **UI path confirmed:** From a Fabric detail page, open the operational summary card and use
> the **Rebuild Graph** action. That posts to the fabric-operations workflow and triggers the
> rebuild for the selected fabric. The generic NetBox jobs path (**Admin → Jobs → Run Job**) also
> remains available. Programmatic fallback:
> ```python
> from netbox_plant_graph.services.sync.rebuilder import rebuild_graph
> rebuild_graph(trigger_mode='manual')
> ```
> or via the NetBox Jobs API.

### Step 13.2 — Monitor the rebuild

The rebuild creates a `GraphBuildRun` record. Navigate to:
**Plant Graph Tables → Graph Build Runs**

The most recent run should show `status = complete` when finished.

Verify the stats field on the `GraphBuildRun` record. The current schema is stable and includes at
minimum these keys:

| Key                      | Meaning |
|--------------------------|---------|
| `fabrics`                | Number of fabrics rebuilt |
| `fabric_planes`          | Number of `FabricPlane` records considered |
| `nodes`                  | `PlantNode` count produced |
| `terminations`           | `TerminationPoint` count produced |
| `attachment_units`       | `AttachmentUnit` count produced |
| `signal_lanes`           | `SignalLane` count produced when lane materialization is enabled |
| `coarse_edges`           | `CoarseEdge` count produced from cables |
| `fine_edges`             | `FineEdge` count produced from cable/profile/passthrough traversal |
| `signal_lane_fine_edges` | Lane-granularity `FineEdge` count |
| `transfer_maps`          | `TransferMap` count produced from passive `PortMapping`s |
| `lane_maps`              | `LaneMap` count produced |
| `plane_memberships`      | `PlaneMembership` count produced |
| `warnings`               | Non-fatal rebuild warnings |

> 📝 **GAP #13 — RESOLVED:** `graph_builder.py` now carries an explicit
> `GRAPH_BUILD_STATS_SCHEMA`, and this runbook section documents the operator-facing keys. Warnings
> such as missing plane rows or zero memberships are surfaced under `stats['warnings']`.

---

## Phase 14: Plugin — Graph Verification

After the rebuild completes, verify that the expected plugin graph objects were created.

### Step 14.1 — Verify PlantNodes

Navigate to: **Plant Graph Tables → Plant Nodes**

Expected for the current executable dev model: **104 backside-relevant PlantNodes**
(24 GPU servers + 48 GPU-leaf shuffle modules + 12 leaf-spine shuffle modules + 12 plane leaves + 8 plane spines), plus any additional
frontside/management/edge devices that also exist in `dc-alpha`.

> 📝 **NOTE:** The extractor scopes device queries to `scope_site`. It includes **all** devices at
> that site, not just the devices involved in the RoCE fabric. Frontside leaf switches, frontside
> spine switches, management switches, and edge routers will also appear as `PlantNode` records.
> Filter by `node_type = device` and check that each has a `source` FK pointing to the correct
> NetBox `Device` object.

> ⚠️ **GAP #14 — PlantNode scope includes non-RoCE devices:**
> The extractor has no way to filter "RoCE fabric devices only" vs "all devices at this site."
> All 104+ devices in the current executable model, plus any management/frontside/edge inventory in
> `dc-alpha`, will become `PlantNode` records. These extra nodes are benign but produce noise in
> graph views and inflate blast-radius results. Options: (a) add a `fabric_member` tag or custom
> field on devices to filter the extractor; (b) rely on the absence of RoCE cabling to naturally
> exclude non-RoCE devices from `FineEdge` chains.

### Step 14.2 — Verify TerminationPoints and AttachmentUnits

Navigate to: **Plant Graph Tables → Termination Points**

Each `NIC0` and `NIC1` interface on every GPU server should appear as a `TerminationPoint`.
Each `NIC0.plane1` through `NIC0.plane4` child interface should appear as an `AttachmentUnit`
under the corresponding `TerminationPoint`.

Navigate to: **Plant Graph Tables → Attachment Units**

Filter by `termination_point` for one GPU server. Expected: 8 AttachmentUnits (2 ports × 4 planes).

> ⚠️ **GAP #15 — AttachmentUnit vs child interface alignment requires verification:**
> Confirm that each `AttachmentUnit.source` FK points to the correct child `Interface` object.
> If child interface creation (Phase 8) was done with incorrect `parent` assignments, the
> transformer's child-interface detection logic (`filter(parent__isnull=False)`) will still pick
> them up, but the `_attachment_key` derivation may produce unexpected groupings.

### Step 14.3 — Verify CoarseEdges

Navigate to: **Plant Graph Tables → Coarse Edges**

For the current executable dev model:
- 24 GPU servers × 2 parent ports = 48 GPU→shuffle cables
- 48 shuffle modules × 4 front ports = 192 shuffle→leaf cables
- 12 plane leaves × 1 uplink parent cable = 12 leaf→spine-shuffle cables
- 12 leaf-spine shuffle modules × 4 front ports = 48 leaf-spine-shuffle→spine cables
- Total: **300 CoarseEdges**

Each `CoarseEdge.source` FK should point to the corresponding NetBox `Cable` object.

### Step 14.4 — Verify FineEdges

Navigate to: **Plant Graph Tables → Fine Edges**

For the current executable dev model with the `800g-4x200g` `BreakoutProfile` assigned:
- 48 GPU→shuffle cables × 4 child breakout channels = 192 `derived_cable_segment` fine edges
- 48 GPU-leaf shuffle modules × 4 internal `PortMapping`s = 192 `passthrough_map` fine edges
- 192 shuffle→leaf direct cables = 192 direct fine edges
- 12 leaf→spine-shuffle cables × 4 child breakout channels = 48 `derived_cable_segment` fine edges
- 12 leaf-spine shuffle modules × 4 internal `PortMapping`s = 48 `passthrough_map` fine edges
- 48 leaf-spine-shuffle→spine direct cables = 48 direct fine edges
- Expected total: **720 attachment-unit FineEdges**

If the Cable breakout-profile custom field is missing on either the GPU→shuffle cables or the
leaf-uplink→shuffle cables, the transformer cannot expand that breakout stage and the resulting
graph will undercount per-plane paths sharply.

### Step 14.5 — Verify PlaneMemberships

Navigate to: **Plant Graph Tables → Plane Memberships**

> ⚠️ **GAP #17 — RESOLVED:** Zero `PlaneMembership` output now emits a rebuild warning with
> `code = "zero_plane_memberships"` in `GraphBuildRun.stats['warnings']`. It is still the most
> common first-rebuild failure, but it is no longer silent. If you see that warning, confirm:
> 1. `fabric_plane` custom field exists and is assigned to `dcim.interface`
> 2. At least one child interface has `custom_field_data['fabric_plane']` set to a non-null integer
> 3. All four `FabricPlane` objects exist for `ROCE-FABRIC-ALPHA`
> Then re-run the rebuild.

---

## Phase 15: Plugin — Plane Membership Verification

After confirming non-zero `PlaneMembership` records, verify correct assignment.

### Step 15.1 — Check attachment-unit plane assignments

For GPU server `gpu-a-a1-001`, navigate to its `AttachmentUnit` list filtered by device:

Each of the 8 attachment units should have exactly one `PlaneMembership` with `membership_role = native`:

| AttachmentUnit    | Expected Plane | Expected Role |
|-------------------|----------------|---------------|
| NIC0.plane1       | 1              | native        |
| NIC0.plane2       | 2              | native        |
| NIC0.plane3       | 3              | native        |
| NIC0.plane4       | 4              | native        |
| NIC1.plane1       | 1              | native        |
| NIC1.plane2       | 2              | native        |
| NIC1.plane3       | 3              | native        |
| NIC1.plane4       | 4              | native        |

### Step 15.2 — Verify transit propagation on leaf switch ports

The transformer propagates plane membership transitively through `FineEdge` adjacency. Passive
shuffle attachment units should receive `membership_role = transit` from the GPU and leaf uplink
children, while the plane-dedicated leaf downlink interfaces, leaf uplink child interfaces, and
spine interfaces should already have native membership from their own `fabric_plane` custom field
values.

Navigate to: **Plant Graph Tables → Plane Memberships** and filter by `plane` and
`member_type = attachmentunit` on one of the RoCE leaf switch nodes.

> ⚠️ **GAP #18 — Transit propagation depends on FineEdge completeness:**
> The `_propagate_plane_memberships()` function walks the FineEdge adjacency graph to spread
> plane identity from seeded (native) attachment units outward. If FineEdges are missing
> (GAP #16, no cable profiles), the propagation has no adjacency to walk. Transit memberships
> on leaf/spine switch ports will be absent, and the plane audit will report missing memberships
> for every switch attachment unit — which is technically correct (they have no native
> membership) but misleading if the cause is missing cable profiles rather than real miscabling.

---

## Phase 16: Plugin — Audit Workflows

### Step 16.1 — Run the plane audit

Navigate to: **Plant Graph Workflows → Plane Audit**

Trigger a plane audit for `ROCE-FABRIC-ALPHA`. This can also be triggered as a background job:
**Admin → Jobs → Run Job → Persistent Plane Audit**

### Step 16.2 — Review audit findings

Navigate to: **Plant Graph Tables → Audit Findings**

Filter by `fabric = ROCE-FABRIC-ALPHA`. Expected findings for this topology:

| Finding type                      | Expected cause                                      |
|-----------------------------------|-----------------------------------------------------|
| `shared_shuffle_artifact`         | Any GPU-leaf shuffle module if policy treats shared passive fanout as disallowed |
| `missing_plane_membership`        | Any port without `fabric_plane` custom field set    |
| `missing_port_mapping`            | Any shuffle front/rear passive path without `PortMapping` coverage |
| `partial_profile_mapping`         | Any cable without a valid cable profile assigned    |
| `orphaned_attachment_unit`        | Any child interface not reachable by a CablePath    |
| `unresolved_profile_mapping`      | Any cable with a profile class that returns no mapping |

> ⚠️ **GAP #19 — Passive-shuffle policy semantics and resolution path:**
> The active switches in this topology are physically disjoint by plane, which is the required
> architecture change. However, each GPU parent port still traverses one passive shuffle module
> shared by all four planes for that host. If your interpretation of `full` disjointness forbids
> any shared passive artifact, the audit may report `shared_shuffle_artifact` findings on the
> GPU-leaf shuffle modules. The leaf-spine shuffle modules are plane-local and should not represent
> cross-plane sharing.
>
> **Choose one of the following resolution paths:**
>
> **Option A — Accept shared passive artifacts via DisjointnessExceptions (recommended)**
>
> This is appropriate if your interpretation of `full` disjointness applies to the *active*
> switching layer only, and you accept that passive shuffle modules are legitimately shared.
>
> For each GPU-leaf shuffle module that triggers a finding, create a `DisjointnessException`:
>
> Navigate to: **Plant Graph Workflows → Exception Request → Add**
>
> | Field              | Value                                              |
> |--------------------|----------------------------------------------------|
> | Exception type     | `shared_passive_artifact`                          |
> | Scope kind         | `artifact`                                         |
> | Target object      | The specific shuffle module PlantNode              |
> | Justification      | "GPU-leaf shuffle is passive optical; all four plane channels traverse the same physical cassette" |
>
> For 48 GPU servers × 2 NICs = 48 shuffle modules, this is 48 exceptions. You can create them
> via the API in bulk:
>
> ```python
> from netbox_plant_graph.models import DisjointnessException, PlantNode
> shuffle_nodes = PlantNode.objects.filter(
>     fabric__name='ROCE-FABRIC-ALPHA', role='gpu-leaf-shuffle-board'
> )
> for node in shuffle_nodes:
>     DisjointnessException.objects.get_or_create(
>         exception_type='shared_passive_artifact',
>         scope_kind='artifact',
>         target_content_type=ContentType.objects.get_for_model(node),
>         target_object_id=node.pk,
>         defaults={'justification': 'Passive GPU-leaf shuffle — shared by design'},
>     )
> ```
>
> **Option B — Eliminate the shared passive element (topology change)**
>
> Replace each GPU-leaf shuffle module with four dedicated per-plane passive units, one per
> plane. Each GPU parent NIC would then cable to four separate shuffle modules (one per plane)
> instead of one shared module. This satisfies `full` disjointness at the passive layer at the
> cost of 4× the passive module count (192 shuffle modules instead of 48).
>
> This is a physical plant change. If you proceed with Option B, re-run the full graph rebuild
> and audit; no `DisjointnessException` records are needed.
>
> **For the current reference runbook, Option A is assumed.** The audit findings on the shared
> shuffle modules are expected and intentional.

### Step 16.3 — Navigate to the Audit Dashboard

Navigate to: **Plant Graph Workflows → Audit Dashboard**

Verify:
- Active finding count is non-zero
- Backlog widgets show findings grouped by type
- Graph build run history is visible

> ⚠️ **GAP #20 — RESOLVED:** Current plugin defaults already enable
> `persist_unresolved_summaries = True` (see GAP #10). Older environments or hand-written configs
> should still verify the setting explicitly, but a stock install no longer requires a special
> opt-in before durable audit widgets populate.

---

## Phase 17: Plugin — Operational Workflows

### Step 17.1 — Path Resolver

Navigate to: **Plant Graph Workflows → Path Resolver**

Enter a path resolution request:

| Field             | Value                                        |
|-------------------|----------------------------------------------|
| Source object     | `gpu-a-a1-001 → NIC0.plane1` (AttachmentUnit) |
| Destination       | `roce-leaf-ha-p1 → Eth1/1` (AttachmentUnit) |
| Resolution        | attachment_unit                              |
| Plane             | 1                                            |

Expected result:
- `path_found: true`
- Path traverses GPU child -> shuffle module -> plane-1 leaf

> 📝 **NOTE:** The current graph model terminates path resolution at Ethernet interface attachment
> units. Passive continuity through shuffle modules is modeled, but active forwarding across the
> leaf switch fabric is not. As a result, attachment-unit path resolution is expected to stop at
> the plane-local leaf interface rather than traverse onward to a spine-facing uplink or spine
> port.

> ⚠️ **GAP #21 — RESOLVED:** The path resolver now uses selector-backed object pickers for
> source and destination objects instead of raw ID entry. Operators can choose the registry type
> and then use the NetBox selector widget to pick the concrete object.

### Step 17.2 — Physical Cable Blast Radius

Navigate to: **Plant Graph Workflows → Physical Cable Blast Radius**

Select `roce-leaf-ha-p1 → Eth1/1` as the failure target.

Expected result:
- Only plane-1 Hall A GPU attachments appear as directly impacted
- The blast radius remains limited to the modeled passive chain for that interface
- The impacted object set is expected to stay on the local `GPU child -> GPU-leaf shuffle -> leaf Eth1/1` path and not extend through active switching to spine ports

> 📝 **NOTE:** This matches the current graph boundary described in Step 17.1. Spine devices and
> leaf uplink paths are present in the inventory and coarse/fine edge model, but the blast-radius
> workflow does not currently model active leaf forwarding from a downlink interface to spine-side
> uplink interfaces.

> ⚠️ **GAP #22 — RESOLVED:** `BlastRadiusJob.run()` now delegates to the blast-radius service,
> and the blast-radius UI page also calls the same service path directly. The workflow is
> functional through both the job wrapper and the UI surface.

### Step 17.3 — Lane Drilldown

Navigate to: **Plant Graph Workflows → Lane Drilldown**

Select any `AttachmentUnit` for a GPU server `NIC0.plane1`.

This view requires `materialize_signal_lanes = True` in config and at least one `SignalLane`
record to exist. Signal lanes are materialized during the rebuild if the config is enabled.

> 📝 **NOTE:** Lane drilldown resolves to `SignalLane` granularity (`4 PAM4 TX + 4 PAM4 RX lanes`
> per 200G child interface). These records are only created if `materialize_signal_lanes = True`.
> For initial proof-of-concept testing, lane drilldown is optional.

### Step 17.4 — Lane Workspace

Navigate to: **Plant Graph Workflows → Lane Workspace**

The lane workspace provides a unified view of lane-set coverage, grouped paths, and compare mode.

> 🔍 **QUESTION — Lane workspace scope selection:** Does the lane workspace require a
> specific `Fabric`, `FabricPlane`, or starting `AttachmentUnit` as its entry scope, or does
> it default to the most recently used fabric? Document the entry-point contract here after
> first live test.

---

## Phase 18: Multi-Tenant Isolation Verification

### Step 18.1 — Verify tenant resolution on PlantNodes

Navigate to: **Plant Graph Tables → Plant Nodes**

Each PlantNode derived from a Tenant Alpha GPU server should have `resolved_tenant = Tenant Alpha`
(resolved via `PlantNode.source → Device.tenant`). PlantNodes for shared infrastructure (leaf,
spine) should have `resolved_tenant = None`.

> ⚠️ **GAP #23 — Plugin has no per-tenant graph query filter:**
> There is no UI filter or API parameter to query "all graph objects for Tenant Alpha only."
> The `resolved_tenant` property exists on `RegistryModelMixin` but is computed dynamically (not
> stored as a DB field), so it cannot be used in ORM filters or list-view filterset queries.
> If an operator wants to see "all paths belonging to Tenant Alpha" vs "all paths belonging to
> Tenant Beta," they must manually identify Tenant Alpha's `PlantNode` PKs and construct
> blast-radius or path queries scoped to those. **This is a significant operational gap for
> multi-tenant deployments.** Options: (a) materialize `tenant_id` as a real DB field on
> `PlantNode`; (b) add a resolved_tenant filter that does a subquery join through the source
> device; (c) accept that per-tenant graph filtering is out of scope for v1.

### Step 18.2 — Verify cross-tenant path isolation (policy)

Navigate to: **Plant Graph Workflows → Policy Review**

Confirm that there are no cross-tenant `DisjointnessException` records. The active switch topology
is plane-disjoint, but both tenants still share the same plane-specific leafs/spines for a given
plane. The disjointness policy applies to **plane isolation**, not to **tenant isolation** —
these are orthogonal concepts in this model.

> ⚠️ **GAP #24 — No tenant-isolation policy in the plugin:**
> The plugin's disjointness/contamination policy checks whether different **planes** share
> passive artifacts. It has no concept of checking whether different **tenants** share transport
> paths in a way that violates tenant isolation requirements. In this topology, Tenant Alpha and
> Tenant Beta still share the same plane-1 leaf/spine devices for their plane-1 traffic. The
> plugin has no finding type for "cross-tenant path sharing." This may or may not be a concern
> depending on whether your tenants require physical tenant isolation, but it should be a
> documented decision.

---

## Execution Log — Historical Direct-Attach Run (2026-04-20/21)

The following execution log is retained for the extractor/transformer bug history, but it reflects
the **previous direct-attach shared-leaf topology**, not the current disjoint-switch + passive
shuffle topology described above. Treat the bug notes as still relevant, but do **not** treat the
object counts or topology-specific findings below as validation of the current architecture.

### Execution method

Phases 1–12 were executed via direct Django ORM (script `devrun/runbook_roce.py`) rather than the
web UI, due to the bulk volumes involved. The API token approach (Phase 9 step) failed — HOTP
token format mismatch with NetBox 4.5.7's expected v2 format. Django ORM is the recommended
approach for bulk setup in dev environments.

### Phase 13 — Three critical extractor bugs hit in sequence

The first rebuild attempt OOM-killed the process (4.7 GB). Two more bugs followed. All three were
fixed in `netbox_plant_graph/services/sync/extractor.py`.

**Bug E1 — Scope identity blindness (OOM)**
`_scope_value(fabric_instance, 'fabric')` called `getattr(fabric, 'fabric', None)` → `None`,
because `Fabric` has no self-referential `.fabric` attribute. With `fabric=None` and no
`scope_site`, the device queryset fell through to `Device.objects.all()` = 38k devices = 4.7 GB OOM.
Fix: `isinstance(scope, Fabric)` guard to assign `fabric = scope` directly. Refactored into a
`_resolve_scope()` helper.

**Bug E2 — Full cable-table scan (OOM)**
After E1, `Cable.objects.all().prefetch_related('terminations__termination')` loaded all 68k cables
globally, then filtered in Python.
Fix: query `CableTermination` by content type + termination IDs to build `relevant_cable_ids`,
then `Cable.objects.filter(pk__in=relevant_cable_ids)`.

**Bug E3 — Invalid `CablePath` reverse relation (infinite hang)**
Attempted `CablePath.objects.filter(frontport__pk__in=...)` — but `frontport` and `rearport` are
not reverse relation names on `CablePath` in NetBox 4.5.7 (introspected field list confirmed).
Fix: use `Interface._path_id` direct FK:
`Interface.objects.filter(pk__in=all_iface_ids, _path__isnull=False).values_list('_path_id', flat=True)`.

### Phase 9 — CableTermination path resolution gap

`CableTermination.objects.create()` bypasses NetBox's `rebuild_cablepaths` signal, so
`Interface._path_id` remained `None` after bulk cable creation. Must call
`from dcim.utils import create_cablepaths; create_cablepaths([iface])` for each cable A-end after
creation. This was done manually during the original live run; `devrun/runbook_roce.py` now makes
the corresponding `create_cablepaths()` calls for idempotent re-runs.

### Phase 13 — Rebuild result (build_run=4)

After extractor fixes and `create_cablepaths()` calls:

```json
{
  "nodes": 17, "coarse_edges": 30, "fine_edges": 30,
  "signal_lane_fine_edges": 0, "plane_memberships": 312,
  "fabric_planes": 4, "status": "completed"
}
```

`signal_lane_fine_edges=0` because cables had `profile=''` — the transformer's tier 2.5
BreakoutProfile lookup never fired.

### Phase 13 — BreakoutProfile activation (build_run=6)

**Resolution of GAP #8:** The correct mechanism is a `BreakoutProfile` DB object assigned through
the plugin-managed Cable object custom field. This replaces the undocumented raw `Cable.profile`
string approach described in older notes. Steps taken:

1. Created `BreakoutProfile` (`slug='800g-4x200g'`, `child_count=4`, `mapping_mode='sequential'`).
2. Updated all 30 fabric cables to reference that `BreakoutProfile` through the Cable custom field.
3. Fixed a secondary transformer bug in the tier 2.5 branch:
   - When both cable ends are single-position Interfaces (`_iter_path_positions` returns `(None,)`),
     the original code produced only one parent↔parent fine edge instead of iterating `1..child_count`
     to produce per-child AU pairs.
   - A guard was added: only count `bp_pairs` when both child AU attachment lookups succeed —
     without this, cables where one side has no children (leaf uplinks) would block the
     identity-mapping fallback, dropping their coarse edges from 30 to 24.

**Rebuild result after fix (build_run=6):**

```json
{
  "nodes": 17,
  "coarse_edges": 30,
  "fine_edges": 102,
  "signal_lane_fine_edges": 384,
  "signal_lanes": 1248,
  "plane_memberships": 312,
  "fabric_planes": 4,
  "status": "completed",
  "build_run": 6
}
```

Breakdown:
- `coarse_edges=30`: all 30 cables (24 GPU→leaf + 6 leaf→spine) ✓
- `fine_edges=102`: 96 GPU→leaf child-AU edges (24 cables × 4 children) + 6 leaf→spine parent-AU identity-mapped ✓
- `signal_lane_fine_edges=384`: 96 child-AU fine edges × 4 signal lanes @ 50 Gbps/lane ✓
- `plane_memberships=312`: 78 per plane across planes 1–4 ✓

> 📝 **NOTE (replaces GAP #8):** The plugin does not use a raw `Cable.profile` string for
> plugin-defined breakout mapping. The mechanism is a `BreakoutProfile` plugin model assigned via a
> Cable object custom field (`mpf_breakout_profile` by default). Create one `BreakoutProfile` per
> cable breakout type before running the rebuild. For 800G→4×200G sequential breakout:
> `slug='800g-4x200g'`, `child_count=4`, `mapping_mode='sequential'`.

### Phase 14 — Verified graph objects (build_run=6)

| Object             | Expected | Actual | Notes                          |
|--------------------|----------|--------|--------------------------------|
| PlantNodes         | 17       | 17     | 12 GPU + 3 leaves + 2 spines  |
| TerminationPoints  | 84       | 84     | 2 per GPU × 12 + 18+3 leaves/spines |
| AttachmentUnits    | 396      | 396    | 84 parents + 312 children     |
| CoarseEdges        | 30       | 30     | All cables represented        |
| FineEdges (AU)     | 102      | 102    | 96 child + 6 identity         |
| SignalLaneFineEdges| 384      | 384    | 4 per child fine edge         |
| PlaneMemberships   | 312      | 312    | 78 per plane × 4 planes       |
| GraphBuildRun status | completed | completed | build_run=6, ~34s          |

> ⚠️ **GAP #2 status — tier_level=None on GPU servers:**
> `PlantNode.metadata['tier_level']` is `None` for all GPU server nodes. Only leaf (tier=0) and
> spine (tier=1) switches have tiers assigned. This is because `Fabric.tier_role_map` maps role
> slugs to tier integers, but the runbook setup did not populate `tier_role_map` for `gpu-server`.
> This is expected behavior — compute nodes are endpoints, not fabric tiers. If your disjointness
> policy or path resolver needs to classify GPU nodes as a specific tier, populate
> `Fabric.tier_role_map` with `{"gpu-server": null}` or the desired integer.

---

## Aggregated Gap Log

Status legend: **Open** = still unresolved · **Fixed** = code change made · **Resolved** = known workaround or design decision documented · **By design** = expected behavior, no fix needed

| # | Phase     | Severity | Status   | Summary                                                                 |
|---|-----------|----------|----------|-------------------------------------------------------------------------|
| 1 | Ph 1      | Design   | Open     | `Fabric.tenant` is single-FK; shared fabrics cannot express co-tenancy |
| 2 | Ph 3      | Design   | Fixed    | `Fabric.tier_role_map` JSONField + transformer reads it into `PlantNode.metadata['tier_level']`; `run_tier_depth_audit()` emits `tier_depth_mismatch` findings |
| 3 | Ph 5      | Blocker  | Fixed    | `DeviceBreakoutTemplate` + `DeviceChildInterfaceSpec` models; `bulk_apply_breakout` management command; reference template `gpu-server-800g-4plane` pre-loaded by migration 0014 |
| 4 | Ph 8      | Blocker  | Fixed    | Plugin ensures `fabric_plane` post-migrate and warns via system check if missing |
| 5 | Ph 8      | Design   | Open     | Interface type for 200G child of 800G parent is ambiguous               |
| 6 | Ph 8      | Blocker  | Fixed    | `python manage.py bulk_apply_breakout --template gpu-server-800g-4plane --role gpu-server`; or assign `breakout_template` on `RackPopulationSlot` before stamping |
| 7 | Ph 8      | Design   | Open     | No cross-device plane-number alignment validation at data-entry time    |
| 8 | Ph 9      | Blocker  | Fixed    | 800G 4:1 breakout cable mapping — use Cable object custom field `mpf_breakout_profile` pointing to `BreakoutProfile` |
| 9 | Ph 10     | Design   | Open     | Plugin has no IP/overlay awareness; physical and overlay validation are separate |
| 10 | Ph 11   | Config   | Resolved | Current plugin default is `persist_unresolved_summaries=True`           |
| 11 | Ph 12   | Bug      | Fixed    | Builder records `missing_fabric_plane_records` warning instead of silent skip |
| 12 | Ph 13   | UX       | Resolved | Fabric detail operational card exposes **Rebuild Graph** action         |
| 13 | Ph 13   | UX       | Resolved | `GraphBuildRun.stats` schema is documented and backed by `GRAPH_BUILD_STATS_SCHEMA` |
| 14 | Ph 14   | Design   | Open     | Extractor includes all site devices; non-RoCE devices become PlantNodes |
| 15 | Ph 14   | Verify   | Resolved | AttachmentUnit source-FK alignment verified live — correct             |
| 16 | Ph 14   | Blocker  | Fixed    | FineEdge expansion — fixed via BreakoutProfile + transformer tier 2.5 patch (96 child-AU edges, see Execution Log) |
| 17 | Ph 14   | Blocker  | Fixed    | Zero memberships now emit `zero_plane_memberships` rebuild warning      |
| 18 | Ph 15   | Observe  | Resolved | Transit propagation confirmed working once FineEdges resolved (#16 fixed) |
| 19 | Ph 16   | Design   | Resolved | Option A: create `DisjointnessException` per GPU-leaf shuffle (bulk API snippet in Step 16.2). Option B: per-plane passive modules. Runbook now documents both paths. |
| 20 | Ph 16   | Config   | Resolved | Current default already enables unresolved-state durability             |
| E1 | Ph 13  | Bug      | Fixed    | Extractor scope identity blindness → OOM (see Execution Log Bug E1)     |
| E2 | Ph 13  | Bug      | Fixed    | Extractor full cable-table scan → OOM (see Execution Log Bug E2)        |
| E3 | Ph 13  | Bug      | Fixed    | Extractor invalid CablePath reverse relation → infinite hang (see Execution Log Bug E3) |
| E4 | Ph 9   | Ops      | Fixed    | `devrun/runbook_roce.py` now calls `create_cablepaths()` after bulk cable creation |
| E5 | Ph 13  | Bug      | Fixed    | Transformer tier 2.5: Interface↔Interface breakout produced 1 parent edge instead of 4 child-AU edge pairs |
| E6 | Ph 13  | Bug      | Fixed    | Transformer tier 2.5: `bp_pairs` counted even when child AU lookup returned None, blocking identity-mapping fallback on leaf uplinks |
| 21 | Ph 17  | UX       | Fixed    | Path Resolver now uses selector-backed object pickers                   |
| 22 | Ph 17  | Bug      | Fixed    | `BlastRadiusJob.run()` delegates to the blast-radius service            |
| 23 | Ph 18  | Design   | Open     | No per-tenant graph query/filter capability                             |
| 24 | Ph 18  | Design   | Open     | No cross-tenant path-sharing policy/finding type                        |

---

## Next Steps

### Phase 17–18 workflows (not yet executed)

The following phases were not run against the live instance **before the teardown in Phase 19
below**. To execute them now, first recreate the dataset with `devrun/runbook_roce.py`, then
proceed in order:

1. **Phase 17 — Path Resolver:** Resolve a path from `gpu-a-a1-001:NIC0.plane1` to
   `roce-leaf-ha-p1:Eth1/1`. Verify the resolver traverses GPU shuffle -> leaf and stops at the
   plane-local Ethernet interface boundary.

2. **Phase 17 — Physical Cable Blast Radius:** Select `roce-leaf-ha-p1:Eth1/1` as failure
   target. Verify only the local Hall A plane-1 passive chain appears as directly impacted.

3. **Phase 16 — Plane Audit:** Run the plane audit and verify the expected
   passive-shuffle findings, if any, for the disjoint-switch topology.

4. **Phase 18 — Tenant Isolation:** Verify `PlantNode.tenant` assignments for Alpha vs Beta GPU
   servers and confirm shared infrastructure nodes have `tenant=None`.

### Blockers still requiring code changes

No remaining code blockers from the original runbook blocker list are open after the fixes above.
The remaining open items are design decisions or future enhancements rather than immediate
correctness blockers.

### Design decisions still open

- **GAP #1:** Shared-fabric multi-tenancy convention — null `Fabric.tenant` is the current de-facto approach.
- **GAP #14:** Whether to filter non-RoCE devices from extractor scope (tag-based or role-based allowlist).
- **GAP #19 — Resolved:** Option A (DisjointnessExceptions) and Option B (per-plane passive topology) are both documented in Step 16.2.

---

## Phase 19: Teardown / Removal Procedure

This phase removes the dataset created by Phases 1–13 and the follow-on breakout activation work in
the Execution Log. It was executed and validated against the live dev instance on **2026-04-21**.

### Step 19.1 — Preview the delete set

From the repo root, run the dedicated cleanup wrapper in dry-run mode:

```bash
./devrun/runbook-roce-cleanup.sh
```

This prints the exact runbook-owned object counts that will be removed, including:

- site `dc-alpha`
- fabric `ROCE-FABRIC-ALPHA`
- breakout profile `800g-4x200g`
- tenants `tenant-alpha` and `tenant-beta`
- runbook-specific device roles and device types
- all plugin-derived graph/build/unresolved-state objects attached to the fabric

### Step 19.2 — Execute the teardown

Run the same wrapper with `--execute`:

```bash
./devrun/runbook-roce-cleanup.sh --execute
```

The cleanup script deletes objects in dependency order:

1. Deletes `Fabric(name='ROCE-FABRIC-ALPHA')`, which cascades the derived plugin graph records
   (`FabricPlane`, `PlantNode`, `TerminationPoint`, `AttachmentUnit`, `SignalLane`, `CoarseEdge`,
   `FineEdge`, `PlaneMembership`, `GraphBuildRun`, `UnresolvedStateSummary`,
   `UnresolvedStateObservation`, and related fabric-scoped records).
2. Deletes all cables attached to interfaces on `Site(slug='dc-alpha')`.
3. Deletes all devices in `dc-alpha`, then the site's racks, then the site's locations, then the
   site record itself.
4. Deletes `BreakoutProfile(slug='800g-4x200g')`.
5. Deletes the runbook tenants, device roles, and device types once they are no longer referenced.
6. Deletes tenant group `GPU Workload Tenants` if it exists.

> 📝 **NOTE:** The plugin-managed `fabric_plane` custom field is intentionally **not** removed. It
> is plugin infrastructure, not runbook-owned sample data.

> 📝 **NOTE:** The generic manufacturer record (`slug='generic'`) is removed only if it becomes
> orphaned. On the live dev instance used for validation, it remained because one non-runbook
> device type still referenced it.

### Step 19.3 — Independently validate that the dataset is gone

After teardown, run an independent NetBox shell query:

```bash
cd ~/src/netbox-v4.5.7/netbox
NETBOX_PLANT_GRAPH_ENABLE=1 ~/.virtualenvs/netbox-4.5.7/bin/python manage.py shell -c "
from dcim.models import Site, DeviceRole, DeviceType
from tenancy.models import Tenant
from netbox_plant_graph.models import Fabric, BreakoutProfile, GraphBuildRun, PlantNode, AttachmentUnit
print({
    'site_dc_alpha': Site.objects.filter(slug='dc-alpha').count(),
    'fabric_roce': Fabric.objects.filter(name='ROCE-FABRIC-ALPHA').count(),
    'tenant_alpha': Tenant.objects.filter(slug='tenant-alpha').count(),
    'tenant_beta': Tenant.objects.filter(slug='tenant-beta').count(),
    'breakout_800g': BreakoutProfile.objects.filter(slug='800g-4x200g').count(),
    'gpu_role': DeviceRole.objects.filter(slug='gpu-server').count(),
    'gpu_type': DeviceType.objects.filter(slug='gpu-server-8x800g').count(),
    'graph_runs_for_fabric': GraphBuildRun.objects.filter(fabric__name='ROCE-FABRIC-ALPHA').count(),
    'plant_nodes_for_fabric': PlantNode.objects.filter(fabric__name='ROCE-FABRIC-ALPHA').count(),
    'attachment_units_for_fabric': AttachmentUnit.objects.filter(
        termination_point__plant_node__fabric__name='ROCE-FABRIC-ALPHA'
    ).count(),
})
"
```

Expected result:

```python
{
    'site_dc_alpha': 0,
    'fabric_roce': 0,
    'tenant_alpha': 0,
    'tenant_beta': 0,
    'breakout_800g': 0,
    'gpu_role': 0,
    'gpu_type': 0,
    'graph_runs_for_fabric': 0,
    'plant_nodes_for_fabric': 0,
    'attachment_units_for_fabric': 0,
}
```

### Step 19.4 — Current validated state

The teardown above was executed successfully on **2026-04-21**. The validation query returned all
zero counts for the runbook-owned site, fabric, tenants, breakout profile, representative device
role/type, and derived graph objects. At the time this document was finalized, the RoCE runbook
dataset had actually been removed from the dev database.
