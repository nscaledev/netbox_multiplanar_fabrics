# Stamp System Gap Closure Plan

> **Historical note:** This plan predates the V2 rewrite and describes the
> original stamp/template/deployment stack (`DeploymentPlan`, `StampRecord`,
> spatial/rack/assembly templates, and NetBox-native cable-oriented behavior).
> It remains useful for design history, but current V2 stamping semantics are
> documented in `docs/data_model.md`, `docs/v2_ground_up_rewrite_plan.md`, and
> `docs/v2_cutover_runbook.md`.

**Date:** 2026-04-21  
**Context:** Assessment of the plugin's ability to define a reference design/architecture for a
multi-planar fabric from which implementations can be stamped. Six gaps were identified. This
document records the closure plan.

## Historical implementation status

| Workstream | Status | Notes |
|------------|--------|-------|
| W1 — execute_plan dispatch | Complete | Plan execution dispatches queued stamp records and preserves compatibility for legacy records without `stamp_type`. |
| W2 — standalone breakout wizard | Complete | Breakout template wizard path is implemented and integrated with plan queueing. |
| W3 — ConnectionTemplate model + execution | Complete | `ConnectionTemplate` model/migration/service path is in place; builder UX route/page is now implemented. |
| W4 — template variable substitution | Complete | Dynamic variable fields are injected for spatial/rack stamp forms and values flow through both execute-now and add-to-plan paths. |
| W5 — rack plane multiplier | Complete | Spatial-layer rack creation now multiplies per plane when rack-pop templates define plane multiplier/fabric default. |

---

## Background: Current state

The plugin has a three-layer templating system:

| Layer | Models | Stamps |
|-------|--------|--------|
| Spatial hierarchy | `SpatialTemplate`, `SpatialTemplateNode` | Locations, Racks, SpatialPlacements |
| Rack population | `RackPopulationTemplate`, `RackPopulationSlot` | Devices; cascades to assembly stamp and breakout stamp |
| Assembly / passive device | `AssemblyTemplate`, `AssemblyConnectorTemplate`, `AssemblyMappingTemplate` | Passive devices with FrontPorts/RearPorts/PortMappings |

Supporting models: `DeviceBreakoutTemplate` + `DeviceChildInterfaceSpec` (child interface
auto-creation), `DeploymentPlan` + `StampRecord` (lifecycle tracking), `BreakoutProfile`
(cable-segment expansion).

---

## Gap inventory

| # | Gap | Severity | Migration required |
|---|-----|----------|-------------------|
| G1 | `execute_plan()` is a stub — updates status but calls no stamp service | High | No |
| G2 | `DeviceBreakoutTemplate` is only reachable via `RackPopulationSlot`; no path to expand breakouts on a pre-existing device | Medium | No |
| G3 | No `ConnectionTemplate` model; cabling between stamped slots cannot be expressed or executed from the template layer | Critical | Yes (0012) |
| G4 | Name patterns and quantities are hardcoded; scaling the topology requires editing the node tree directly | Medium | Yes (0012) |
| G5 | No plane multiplier on `RackPopulationTemplate`; per-plane device populations must be stamped by hand N times | Medium | Yes (0012) |

---

## Workstream 1 — Fix `execute_plan()` (no migration)

**Closes:** G1

### Problem

`execute_plan()` in `services/plan_execution.py` iterates pending `StampRecord` rows and sets their
status to `'stamped'` without calling any stamp service. A `StampRecord` is a provenance record for
a stamp already performed interactively via a wizard page. The plan lifecycle (draft → approved →
stamping → active) and the rollback path both exist, but executing a plan does nothing beyond
updating status fields.

### Design

`StampRecord.parameters` is already a JSONField. Define a convention for its contents so that a
record can be queued before execution and dispatched at execute time.

**`parameters` schema (keyed by `stamp_type`):**

```json
// Spatial
{"stamp_type": "spatial", "template_id": 5, "scope_pk": 42, "scope_type": "site"}

// Rack population
{"stamp_type": "rack_population", "template_id": 7, "rack_pk": 91}

// Assembly passive device
{"stamp_type": "assembly_passive", "template_id": 3, "rack_pk": 91, "name": "shuf-ha-p1-01", "device_role_pk": 12}

// Cable assembly (once ConnectionTemplate exists — see G3)
{"stamp_type": "cable_assembly", "template_id": 3, "a_pks": [10, 11], "b_pks": [20, 21], "label": "gpu-shuf-001"}

// Breakout child interfaces
{"stamp_type": "breakout", "template_id": 8, "device_pk": 55}
```

**Changes to `execute_plan()`:** Dispatch on `record.parameters['stamp_type']`, resolve PKs to
objects, call the appropriate service (`stamp_spatial_template`, `stamp_rack_population`,
`stamp_passive_device`, `stamp_cable_assembly`, `create_child_interfaces_from_breakout_spec`).
On failure, set `record.status = 'failed'` + `error_detail`, stop, and set plan status back to
`'draft'`.

**Changes to wizard views:** Each wizard view gains an "Add to plan" path that creates a
`StampRecord(status='pending', parameters=<serialized params>)` and returns without executing.
The existing "Execute now" path is unchanged. The wizard form adds an
"Add to plan" vs "Execute now" radio button when the user selects a `DeploymentPlan`.

**Files to change:**
- `services/plan_execution.py`
- `views.py` (wizard views: `AssemblyStampWizardView`, `SpatialStampWizardView`, `RackPopulationStampWizardView`)
- `forms.py` (add `action` radio to `AssemblyStampForm`, `SpatialStampForm`, `RackPopulationStampForm`)

---

## Workstream 2 — Standalone breakout stamp wizard (no migration)

**Closes:** G2

### Problem

`create_child_interfaces_from_breakout_spec()` exists and is correct, but it is only invoked from
`stamp_rack_population()` when `RackPopulationSlot.breakout_template` is set. There is no UI path
to apply a `DeviceBreakoutTemplate` to a device that was created before the template system existed
or was populated without a matching slot.

### Design

Add a `BreakoutStampWizardView` accessible from the `DeviceBreakoutTemplate` detail page.

**Form fields:**
- Device picker (required)
- Dry-run checkbox (default checked on initial GET; unchecked on confirm POST)
- Optional `DeploymentPlan` assignment

**GET response:** Shows the template's `DeviceChildInterfaceSpec` rows and, if a device is
pre-selected, a dry-run result previewing which child interfaces would be created and which would
be skipped as already-existing.

**POST response:** Calls `create_child_interfaces_from_breakout_spec()` with `dry_run=False` (or
queues a `StampRecord` if a plan was selected and "Add to plan" was chosen per W1 convention).

**Files to change:**
- `views.py` (add `BreakoutStampWizardView`)
- `urls.py` (add `breakout-template/<int:pk>/stamp/`)
- `forms.py` (add `BreakoutStampForm`)
- `templates/netbox_plant_graph/breakout_stamp_wizard.html` (new)

---

## Workstream 3 — `ConnectionTemplate` model (migration 0012)

**Closes:** G3 — the critical gap

### Problem

After a `SpatialTemplate` stamps a topology, the devices exist at the correct rack positions but are
unconnected. `stamp_cable_assembly()` already works, but there is no model that encodes "source
slot X connects to dest slot Y via assembly template T" so that cabling can be authored once in
the reference design and replayed at stamp time.

### New model — `ConnectionTemplate`

Belongs to a `SpatialTemplate` so all connection topology is co-located with the spatial hierarchy.

```python
class ConnectionTemplate(RegistryModelMixin):
    registry_key = 'connectiontemplate'

    spatial_template = ForeignKey(
        'SpatialTemplate', related_name='connections', on_delete=CASCADE
    )
    name = CharField(max_length=200)
    description = CharField(blank=True, max_length=200)
    assembly_template = ForeignKey(
        'AssemblyTemplate', on_delete=PROTECT,
        help_text='Defines connector shape and breakout profile for each cable stamped.',
    )

    # A-side: which stamped device provides the A termination
    source_node = ForeignKey(
        'SpatialTemplateNode', related_name='source_connections', on_delete=CASCADE,
        help_text='SpatialTemplateNode whose stamped devices are the A-side.',
    )
    source_slot_index = PositiveIntegerField(
        help_text='1-based index into source_node.rack_population_template.slots for the A-side port.',
    )
    source_connector_number = PositiveIntegerField(default=1)

    # B-side: which stamped device provides the B termination
    dest_node = ForeignKey(
        'SpatialTemplateNode', related_name='dest_connections', on_delete=CASCADE,
    )
    dest_slot_index = PositiveIntegerField()
    dest_connector_number = PositiveIntegerField(default=1)

    # How to pair source and dest instances when nodes have quantity > 1
    enumeration_mode = CharField(
        max_length=20,
        choices=[
            ('one_to_one', 'One-to-one (source[i] → dest[i])'),
            ('fan_out',    'Fan-out (each source → all dests)'),
            ('fan_in',     'Fan-in (all sources → one dest)'),
        ],
        default='one_to_one',
    )
    label_pattern = CharField(
        max_length=200, blank=True,
        help_text='Python str.format pattern. Available vars: {a_name}, {b_name}, {index}.',
    )
    sort_order = PositiveIntegerField(default=0)
    metadata = JSONField(default=dict, blank=True)
```

### Changes to `stamp_spatial_template()`

After all location/rack/device stamps are committed, resolve `ConnectionTemplate` rows attached to
the `SpatialTemplate`. For each row, look up the concrete devices that were just created for
`source_node` and `dest_node` (keyed from `SpatialStampResult`), enumerate pairs per
`enumeration_mode`, and call `stamp_cable_assembly()` for each pair. Record each cable as a
`StampRecord` so rollback deletes it.

### Builder UX — `ConnectionTemplateBuilderView`

A page on the `SpatialTemplate` detail that shows a matrix of source nodes × dest nodes. The
operator selects a cell, picks an `AssemblyTemplate`, assigns slot indices and connector numbers,
and clicks "Add connection." The view is POST-driven with action tokens mirroring the
`AssemblyTemplateBuilderView` pattern.

**Files to change:**
- `models.py` (add `ConnectionTemplate`)
- `migrations/0012_connection_template.py`
- `object_registry.py` (register `ConnectionTemplate` with CRUD API + nav entry)
- `services/spatial_stamp.py` (post-rack-population connection enumeration)
- `services/assembly_stamp.py` (minor — `stamp_cable_assembly` already works)
- `views.py` (add `ConnectionTemplateBuilderView`, update `SpatialTemplateDetailView`)
- `urls.py` (add `spatial-templates/<int:pk>/connections/`)
- `forms.py` (add `ConnectionTemplateForm`)
- `templates/netbox_plant_graph/connection_template_builder.html` (new)

---

## Workstream 4 — Template variable substitution (migration 0012, same as W3)

**Closes:** G4

### Problem

`SpatialTemplateNode.quantity` and `name_pattern` are hardcoded. Changing "3 compute halls" to
"4 compute halls" requires editing every affected node individually. There is no way to define a
reference topology with parameters that are resolved at stamp time.

### Design

Add `parameters` JSONField to `SpatialTemplate` (and optionally `RackPopulationTemplate`).

**Schema convention:**

```json
{
  "hall_count":       {"type": "int",    "default": 3,    "label": "Compute hall count"},
  "spines_per_plane": {"type": "int",    "default": 2,    "label": "Spines per plane"},
  "hall_prefix":      {"type": "str",    "default": "ha", "label": "Hall identifier prefix"}
}
```

**Name pattern / quantity substitution:**

- `SpatialTemplateNode.name_pattern`: supports `{hall_prefix}` → resolved from variables at stamp time
- `SpatialTemplateNode.quantity`: stored as a string field (or kept as int with an override field `quantity_var = CharField`) whose value can be `"{hall_count}"` → evaluated as `int(variables['hall_count'])`

**Implementation note:** The simpler approach is to add a `quantity_expr` CharField alongside the
existing `quantity` IntegerField and have `stamp_spatial_template()` prefer `quantity_expr` when
set. `name_pattern` already goes through `render_name_pattern()` — extend that function to accept
a `variables` dict and call `pattern.format(**base_vars, **template_vars)`.

**Form impact:** `SpatialStampForm` and `RackPopulationStampForm` introspect the template's
`parameters` schema at form construction time and emit one extra field per declared variable.

**Migration footprint:** `AddField(SpatialTemplate, 'parameters', JSONField(default=dict))` +
`AddField(RackPopulationTemplate, 'parameters', JSONField(default=dict))` +
`AddField(SpatialTemplateNode, 'quantity_expr', CharField(blank=True, max_length=100))`.

**Files to change:**
- `models.py` (add fields)
- `migrations/0012_connection_template.py` (add these fields in the same migration)
- `services/spatial_stamp.py` (`render_name_pattern` extension, `quantity_expr` resolution)
- `services/rack_population_stamp.py` (variable pass-through)
- `forms.py` (dynamic field injection in `SpatialStampForm`)
- `views.py` (pass variable values from form into stamp call)

---

## Workstream 5 — Plane multiplier on `RackPopulationTemplate` (migration 0012, same as W3/W4)

**Closes:** G5

### Problem

Per-plane device populations (one leaf switch rack per plane, one leaf-spine shuffle rack per plane)
must be stamped N times manually. There is no way to express "this rack type repeats once per
fabric plane" in the reference design.

### Design

Add two fields to `RackPopulationTemplate`:

```python
plane_multiplier = PositiveIntegerField(
    null=True, blank=True,
    help_text=(
        'If set, stamp once per plane 1..N. '
        '{plane} is available in name patterns during each iteration.'
    ),
)
fabric = ForeignKey(
    'Fabric', null=True, blank=True, related_name='+', on_delete=SET_NULL,
    help_text='If set and plane_multiplier is None, defaults plane_multiplier to fabric.expected_plane_count.',
)
```

**Changes to `stamp_rack_population()`:**

When `plane_multiplier` is set (or derivable from `template.fabric`), wrap the current slot
iteration in an outer loop over `range(1, plane_multiplier + 1)`. Pass `plane=p` into
`render_name_pattern()` so slot `name_pattern` values like `roce-leaf-{plane}-ha` resolve
correctly. The `fabric_plane_start` on `DeviceChildInterfaceSpec` rows is overridden with the
current plane number during each iteration.

**Changes to `SpatialTemplateNode`:**

When a node's `rack_population_template` has `plane_multiplier > 1`, `stamp_spatial_template()`
loops over planes when creating racks for that node, producing one rack per plane per
`quantity` unit (e.g., 3 halls × 4 planes = 12 leaf racks from a single node definition).

**Migration footprint:** Two `AddField` ops on `RackPopulationTemplate`, included in migration
0012.

**Files to change:**
- `models.py` (add fields)
- `migrations/0012_connection_template.py` (same migration)
- `services/rack_population_stamp.py` (plane loop)
- `services/spatial_stamp.py` (plane-multiplied rack creation)
- `forms.py` (surface `plane_multiplier` override at stamp time)

---

## Migration 0012 scope summary

All new DB schema from W3, W4, and W5 lands in a single migration:

```
migrations/0012_connection_template.py
  CreateModel ConnectionTemplate
  AddField SpatialTemplate.parameters
  AddField SpatialTemplateNode.quantity_expr
  AddField RackPopulationTemplate.parameters
  AddField RackPopulationTemplate.plane_multiplier
  AddField RackPopulationTemplate.fabric (FK)
```

---

## Delivery order

```
W1  fix execute_plan()        — unblocks plan-driven stamp execution end-to-end; no migration
W2  breakout stamp wizard      — independent; low-risk; no migration
W3  ConnectionTemplate         — the structural gap; unblocks cabling from templates; migration 0012
W4  variable substitution      — layers on top of W3 once basic cabling is proven; same migration
W5  plane multiplier           — last; depends on W3 being proven for the non-multiplied case; same migration
```

W3 is the highest-value item and carries the most design work (the builder UX for defining
cross-rack connections inside a spatial template is non-trivial). Recommended sequence:
W1 → W2 → W3 → W4 → W5.
