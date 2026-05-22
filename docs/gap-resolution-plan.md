# Gap Resolution Plan

> **Historical note:** This plan belongs to the original/V1 graph model and
> refers to objects such as `PlantNode`, `AttachmentUnit`, `FineEdge`,
> `BreakoutProfile`, and `CablePath`. It is retained as an implementation
> history record, not as current V2 guidance. For active V2 semantics, use
> `docs/data_model.md`.

**Source:** `docs/runbook-roce-fabric-modeling.md`, gaps #1–24  
**Guidance applied:**
- Create an "operator" tenant as the registered owner of shared/multi-tenant resources
- Client-dedicated elements → customer tenant; shared/leveraged elements → operator tenant
- Plugin must be able to define custom cable breakout profiles (DB-backed, not Python-only)
- Child interface auto-creation must be driveable by switch templates in the stamp workflow

---

## Priority overview

| Pri | Workstream | Gaps closed | Migrations? |
|-----|-----------|-------------|-------------|
| 1   | B — Install self-checks | #4, #10, #11, #17 | No |
| 2   | C — BreakoutProfile model | #8, #16, #18 | Yes (0011) |
| 3   | D — DeviceBreakoutTemplate | #3, #6, #7 | Yes (0011) |
| 4   | A — Tenancy / operator-tenant | #1, #23, #24 | Yes (0011) |
| 5   | E — Fabric tier role map | #2 | Yes (0011) |
| 6   | F — BlastRadiusJob fix | #22 | No |
| 7   | G — Rebuild observability | #12, #13 | No |
| 8   | H — Runbook + doc updates | #5, #9, #14, #15, #19, #20, #21 | No |

All migrations in priorities 2–5 are batched into a single `0011` migration.

---

## Workstream A: Tenancy / Operator-Tenant Model

**Gaps:** #1 (single-FK fabric tenant), #23 (no per-tenant graph filter), #24 (no cross-tenant policy)

### A1 — Convention: operator tenant owns shared infrastructure

No schema change required for the core convention. Document and enforce:

- Create one tenant with slug `operator` (or `noc`) in NetBox. This is the "infrastructure owner."
- `Fabric.tenant` → operator tenant for all shared fabrics (not null, not customer tenant)
- Rack records for shared plant (leaf, spine, management, edge) → operator tenant
- Rack records and Device records for GPU compute → customer tenant
- `AssemblyTemplate` records for shared passive plant → operator tenant
- `RackPopulationTemplate` records for shared network racks → operator tenant

This resolves GAP #1 without any model change: the single `Fabric.tenant` FK now has a meaningful
non-null value that correctly represents shared ownership.

### A2 — Add `tenant` FK to `PlantNode`

Currently `PlantNode.resolved_tenant` is a runtime-computed property that walks the GFK source
chain to the Device's tenant. This is not filterable via the ORM.

**Change:** Add `tenant = ForeignKey('tenancy.Tenant', null=True, blank=True, on_delete=SET_NULL)`
directly to `PlantNode`.

**Populate at rebuild time:** In `transformer.py`, when building the `node_inputs` dict, include
`'tenant': getattr(device, 'tenant', None)`. In `graph_builder.py`, set `PlantNode.tenant` from
the input.

**Effect on filterset:** Add `tenant` to `PlantNodeFilterSet` (already handled by registry update).

**Effect on queries:** Operators can now do
`PlantNode.objects.filter(tenant__slug='tenant-alpha')` to isolate one customer's plant nodes.

> ⚠️ **Remaining GAP #24 (cross-tenant policy):** Not addressed in this plan. The plugin's
> disjointness policy checks plane isolation, not tenant isolation. Accept as documented
> out-of-scope for v1 and note in design doc.

### A3 — Migration: add `tenant` to `PlantNode`

In migration 0011, add the nullable FK. Backfill is not needed (null = unknown/shared).

---

## Workstream B: Plugin Install Self-Checks

**Gaps:** #4 (`fabric_plane` custom field not auto-created), #10 (`persist_unresolved_summaries`
defaults off), #11 (FabricPlane absence silently skips PlaneMembership), #17 (zero plane
memberships is silent)

These changes require **no migration** and are low-risk.

### B1 — Change `persist_unresolved_summaries` default to `True`

In `netbox_plant_graph/__init__.py`, change:
```python
'persist_unresolved_summaries': False,
```
to:
```python
'persist_unresolved_summaries': True,
```

This is a single-character change. Existing deployments with explicit `False` in their
`PLUGINS_CONFIG` are unaffected.

### B2 — Auto-create `fabric_plane` custom field on `post_migrate`

In `PlantGraphConfig.ready()`, connect a `post_migrate` receiver that ensures the `fabric_plane`
custom field exists and is assigned to `dcim.interface`.

```python
# In netbox_plant_graph/__init__.py, inside ready():
from django.db.models.signals import post_migrate
post_migrate.connect(_ensure_fabric_plane_custom_field, sender=self)
```

```python
def _ensure_fabric_plane_custom_field(sender, **kwargs):
    """
    Ensure the 'fabric_plane' custom field exists and is applied to
    dcim.Interface. Runs after every migration to stay idempotent.
    """
    try:
        from extras.models import CustomField
        from django.contrib.contenttypes.models import ContentType

        cf, created = CustomField.objects.get_or_create(
            name='fabric_plane',
            defaults={
                'label': 'Fabric Plane',
                'type': 'integer',
                'required': False,
                'description': (
                    'RoCE fabric plane number (1–N) for this attachment unit. '
                    'Read by the Plant Graph plugin to assign PlaneMembership records.'
                ),
            },
        )
        from dcim.models import Interface
        iface_ct = ContentType.objects.get_for_model(Interface)
        if not cf.object_types.filter(pk=iface_ct.pk).exists():
            cf.object_types.add(iface_ct)
        if created:
            import logging
            logging.getLogger('netbox_plant_graph').info(
                'Created fabric_plane custom field on dcim.Interface'
            )
    except Exception as exc:
        import logging
        logging.getLogger('netbox_plant_graph').warning(
            'Could not ensure fabric_plane custom field: %s', exc
        )
```

This is safe to run repeatedly (idempotent via `get_or_create`).

### B3 — Warn in `graph_builder.py` when `plane_number` has no matching `FabricPlane`

In `services/sync/graph_builder.py`, when creating `PlaneMembership` rows, collect the set of
`plane_numbers` that appear in attachment-unit inputs and compare against the set of
`FabricPlane.plane_number` values that exist for the fabric. Log a warning for each missing plane.

Additionally, after the full rebuild, if `attachment_unit_count > 0` and
`plane_membership_count == 0`, emit a WARNING-level log message and record a note in
`GraphBuildRun.stats['warnings']`.

```python
# In graph_builder.py, near PlaneMembership creation:
requested_plane_numbers = {m['plane_number'] for m in plane_membership_inputs.values()}
existing_plane_numbers = set(
    FabricPlane.objects.filter(fabric=fabric).values_list('plane_number', flat=True)
)
missing = requested_plane_numbers - existing_plane_numbers
if missing:
    logger.warning(
        'Fabric %s: plane numbers %s found in attachment-unit custom fields '
        'but no matching FabricPlane records exist. '
        'PlaneMembership rows for these planes will be skipped. '
        'Create FabricPlane records before rebuilding.',
        fabric, sorted(missing),
    )
    stats.setdefault('warnings', []).append({
        'code': 'missing_fabric_plane_records',
        'plane_numbers': sorted(missing),
    })
```

### B4 — Zero plane memberships post-rebuild warning

In `rebuilder.py`, after `build_graph()` completes:

```python
if stats.get('attachment_units_created', 0) > 0 and stats.get('plane_memberships_created', 0) == 0:
    logger.warning(
        'Rebuild completed with %d AttachmentUnits but ZERO PlaneMembership records. '
        'Check: (1) fabric_plane custom field exists on dcim.Interface, '
        '(2) child interfaces have fabric_plane values set, '
        '(3) FabricPlane records exist for this fabric.',
        stats['attachment_units_created'],
    )
    stats.setdefault('warnings', []).append({'code': 'zero_plane_memberships'})
```

---

## Workstream C: `BreakoutProfile` Model (Plugin-Owned Cable Breakout Definitions)

**Gaps:** #8 (800G breakout cable profile availability), #16 (FineEdge expansion absent without
profile), #18 (transit propagation absent when FineEdges missing — cascades from #16)

### Problem recap

The transformer's cable-segment derivation has three tiers:
1. NetBox native profile class → `cable.profile_class.get_mapped_position()`
2. `AssemblyMappingTemplate` via `StampRecord` provenance
3. Identity map (only if exactly 1:1 port connection — fails for breakout cables)

For an 800G DAC that breaks out to 4×200G child interfaces, there is no NetBox-native profile
class covering this case (the built-in profiles are for MTP/MPO shuffle trunks). The result is
that `profile_pairs == 0`, `assembly_pairs == 0`, and `_can_identity_map_without_profile()` returns
`False` for a 4-position RearPort — so the cable produces **zero FineEdges**.

The fix: add a fourth tier — a DB-backed `BreakoutProfile` model that the operator creates once
per cable type and references via `cable.profile` (the profile string field on `Cable`).

### C1 — New model: `BreakoutProfile`

```python
class BreakoutProfile(RegistryModelMixin):
    registry_key = 'breakoutprofile'

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    # slug must match the value stored in Cable.profile for auto-lookup
    description = models.CharField(max_length=200, blank=True)

    parent_speed_gbps = models.PositiveIntegerField(null=True, blank=True)
    # Informational; used for validation and display
    child_count = models.PositiveIntegerField(default=4)
    child_speed_gbps = models.PositiveIntegerField(null=True, blank=True)

    mapping_mode = models.CharField(
        max_length=20,
        choices=[
            ('sequential', 'Sequential (child ordinal = parent position − 1)'),
            ('explicit', 'Explicit (use position_map)'),
        ],
        default='sequential',
    )
    position_map = models.JSONField(default=dict, blank=True)
    # For 'explicit' mode only.
    # Format: {"<parent_position_1-indexed>": <child_ordinal_0-indexed>}
    # Example for sequential 4:1: {"1": 0, "2": 1, "3": 2, "4": 3}
    # Example for shuffled:       {"1": 2, "2": 0, "3": 3, "4": 1}

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name

    def get_child_ordinal(self, parent_position: int) -> int | None:
        """
        Map a 1-indexed parent cable position to a 0-indexed child ordinal.
        Returns None if the position has no mapping.
        """
        if self.mapping_mode == 'sequential':
            if 1 <= parent_position <= self.child_count:
                return parent_position - 1
            return None
        return self.position_map.get(str(parent_position))
```

#### Registry entry

Add a standard `ObjectSpec` for `BreakoutProfile` in `object_registry.py`:
- Navigation group: `Connectivity Mapping`
- Standard CRUD (operators create and manage these)
- Simple detail view

#### AssemblyTemplate upgrade

Add `breakout_profile = ForeignKey('BreakoutProfile', null=True, blank=True, on_delete=SET_NULL)`
to `AssemblyTemplate`. This replaces the current `cable_profile_hint` CharField as the authoritative
reference when the template is used to stamp a cable assembly.

The `stamp_cable_assembly` service should set `cable.profile = template.breakout_profile.slug`
when the template has a `breakout_profile` assigned.

### C2 — Transformer: add Tier 2.5 (BreakoutProfile lookup)

In `transformer.py`, after the `AssemblyMappingTemplate` fallback (`if assembly_pairs: continue`)
and before the identity-map fallback, add:

```python
# Tier 2.5: BreakoutProfile by cable.profile slug
if not profile_pairs and not assembly_pairs:
    cable_profile_slug = getattr(cable, 'profile', '') or ''
    if cable_profile_slug:
        breakout_profile = _breakout_profile_cache.get(cable_profile_slug)
        if breakout_profile is None:
            # Cache a sentinel (False) for misses to avoid repeated DB hits
            try:
                from netbox_plant_graph.models import BreakoutProfile as _BP
                breakout_profile = _BP.objects.get(slug=cable_profile_slug)
            except _BP.DoesNotExist:
                breakout_profile = False
            _breakout_profile_cache[cable_profile_slug] = breakout_profile

        if breakout_profile:
            # Map each left termination position to the matching right child attachment unit
            breakout_pairs = 0
            for left_termination in left_terminations:
                for left_position in _iter_path_positions(left_termination):
                    child_ordinal = breakout_profile.get_child_ordinal(left_position or 1)
                    if child_ordinal is None:
                        continue
                    # Find the right-side child interface with matching ordinal
                    right_child_au = _find_attachment_by_child_ordinal(
                        attachment_lookup, right_terminations, child_ordinal
                    )
                    if right_child_au is None:
                        continue
                    left_au_key = _first_attachment_key(
                        attachment_lookup, left_termination, left_position
                    )
                    if not left_au_key:
                        continue
                    # Build FineEdge directly
                    coarse_key = _canonical_pair(
                        'coarse',
                        _termination_key(left_termination),
                        _termination_key(right_terminations[0]),
                        f'cable:{cable.pk}',
                    )
                    coarse_edges.setdefault(coarse_key, { ... })  # same as add_cable_segment
                    fine_key = _canonical_pair('fine', left_au_key, right_child_au, coarse_key)
                    fine_edges.setdefault(fine_key, {
                        'key': fine_key,
                        'granularity': 'attachment_unit',
                        'edge_type': 'derived_cable_segment',
                        'a_au_key': min(left_au_key, right_child_au),
                        'b_au_key': max(left_au_key, right_child_au),
                        'parent_coarse_key': coarse_key,
                        'derived_from_profile': True,  # treat as profile-derived
                        'metadata': {'breakout_profile': breakout_profile.slug},
                    })
                    breakout_pairs += 1
            if breakout_pairs:
                continue  # skip identity fallback
```

`_breakout_profile_cache` is a `dict` initialized at the top of `transform_source_bundle()` to
avoid N+1 DB queries.

`_find_attachment_by_child_ordinal(attachment_lookup, right_terminations, ordinal)` looks up
the right-side child interface at ordinal position. This finds the child-interface attachment unit
created from child interfaces (sorted by name, ordinal `ordinal`).

### C3 — Unresolved finding for cables with profile slug but missing BreakoutProfile

In the existing unresolved-candidate extraction (`services/graph/unresolved_candidates.py`), add a
new cause: `missing_breakout_profile`. Triggered when `cable.profile` is set to a non-empty
string that does not match any `BreakoutProfile.slug` or any NetBox native profile class name.
This produces an `UnresolvedStateSummary` row with `cause_code = 'missing_breakout_profile'`.

Add `'missing_breakout_profile'` to `UnresolvedStateCauseChoices`.

### C4 — Pre-built BreakoutProfile for 800G 4:1

Create a data migration (or document as a "first-run setup" step) to create the standard
`BreakoutProfile` that covers the reference topology:

```
name: 800G 4x200G Sequential Breakout
slug: breakout-800g-4x200g
parent_speed_gbps: 800
child_count: 4
child_speed_gbps: 200
mapping_mode: sequential
```

With this record in the DB and `cable.profile = 'breakout-800g-4x200g'` set on each GPU-to-leaf
cable, the transformer will correctly derive 4 attachment-unit-level `FineEdge`s per cable.

---

## Workstream D: `DeviceBreakoutTemplate` — Child Interface Auto-Creation

**Gaps:** #3 (device type templates can't express breakout children), #6 (192 manual child
interface creates), #7 (no cross-device plane alignment validation at data entry time)

### Problem recap

When `stamp_rack_population` creates a Device from a `RackPopulationSlot`, it calls
`Device.objects.create(...)` and gets the interface templates from `DeviceType`. NetBox `DeviceType`
interface templates cannot express breakout child interfaces. Every child interface is currently
created manually after device instantiation.

The fix: add a reusable `DeviceBreakoutTemplate` model that a `RackPopulationSlot` can reference.
When the slot stamp runs, after the Device is created, the stamp service iterates the breakout
template's specs and creates child `Interface` objects — including setting the `fabric_plane`
custom field correctly.

### D1 — New model: `DeviceBreakoutTemplate`

```python
class DeviceBreakoutTemplate(RegistryModelMixin):
    registry_key = 'devicebreakouttemplate'

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    # Optional: intended for this device type (informational, not enforced)
    device_type = models.ForeignKey(
        'dcim.DeviceType', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name
```

### D2 — New model: `DeviceChildInterfaceSpec`

```python
class DeviceChildInterfaceSpec(RegistryModelMixin):
    registry_key = 'devicechildinterfacespec'

    breakout_template = models.ForeignKey(
        'DeviceBreakoutTemplate', related_name='child_specs', on_delete=models.CASCADE
    )
    parent_interface_name = models.CharField(max_length=200)
    # Exact name of the parent interface as it appears on the Device, e.g. "NIC0"

    child_name_pattern = models.CharField(max_length=200)
    # Python str.format pattern. Available vars: {parent} (parent interface name),
    # {n} (1-based child number), {plane} (fabric plane number).
    # Example: "{parent}.plane{plane}"  → "NIC0.plane1", "NIC0.plane2", etc.

    child_count = models.PositiveIntegerField(default=4)
    child_interface_type = models.CharField(max_length=50, default='virtual')
    # NetBox interface type slug, e.g. 'virtual', '200gbase-cr4', '200gbase-sr4'

    child_speed_kbps = models.PositiveIntegerField(null=True, blank=True)
    # Speed in kbps as stored by NetBox Interface.speed (e.g. 200_000_000 for 200G)

    fabric_plane_start = models.PositiveIntegerField(default=1)
    # Child 0 gets fabric_plane = fabric_plane_start,
    # Child 1 gets fabric_plane = fabric_plane_start + 1, etc.

    breakout_profile = models.ForeignKey(
        'BreakoutProfile', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    # Optional: reference to the BreakoutProfile that governs cable-segment
    # FineEdge derivation for cables connected to this parent interface.
    # The stamp service can use this to auto-set cable.profile on cables
    # created via stamp_cable_assembly for slots that use this spec.

    sort_order = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('breakout_template', 'sort_order', 'parent_interface_name')
        unique_together = ('breakout_template', 'parent_interface_name')

    def __str__(self) -> str:
        return f'{self.breakout_template}: {self.parent_interface_name} → {self.child_count}× children'

    def generate_child_name(self, child_index: int) -> str:
        """child_index is 0-based."""
        plane = self.fabric_plane_start + child_index
        n = child_index + 1
        return self.child_name_pattern.format(
            parent=self.parent_interface_name,
            n=n,
            plane=plane,
        )
```

### D3 — Add `breakout_template` FK to `RackPopulationSlot`

```python
# In models.py, on RackPopulationSlot:
breakout_template = models.ForeignKey(
    'DeviceBreakoutTemplate',
    null=True,
    blank=True,
    related_name='+',
    on_delete=models.SET_NULL,
)
```

### D4 — New service: `create_child_interfaces_from_breakout_spec`

Add to `services/assembly_stamp.py` (or a new `services/interface_stamp.py`):

```python
def create_child_interfaces_from_breakout_spec(
    device,
    breakout_template,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> list:
    """
    Create child Interface objects on `device` from a DeviceBreakoutTemplate.

    Parameters
    ----------
    device : Device
        The target device.
    breakout_template : DeviceBreakoutTemplate
        The template whose child_specs drive interface creation.
    dry_run : bool
        If True, validate and return expected creates without committing.
    overwrite : bool
        If False (default), skip existing interfaces (idempotent).
        If True, update speed/custom-fields on existing child interfaces.

    Returns
    -------
    list of Interface  (created or updated)
    """
    from dcim.models import Interface
    from django.conf import settings

    plane_field_name = (
        settings.PLUGINS_CONFIG
        .get('netbox_plant_graph', {})
        .get('default_plane_field_name', 'fabric_plane')
    )

    results = []
    for spec in breakout_template.child_specs.order_by('sort_order', 'parent_interface_name'):
        try:
            parent_iface = device.interfaces.get(name=spec.parent_interface_name)
        except Interface.DoesNotExist:
            logger.warning(
                'Device %s has no interface named %r; skipping breakout spec.',
                device, spec.parent_interface_name,
            )
            continue

        for child_index in range(spec.child_count):
            child_name = spec.generate_child_name(child_index)
            plane_number = spec.fabric_plane_start + child_index

            if dry_run:
                results.append({'name': child_name, 'plane': plane_number, 'action': 'would_create'})
                continue

            iface, created = Interface.objects.get_or_create(
                device=device,
                name=child_name,
                defaults={
                    'parent': parent_iface,
                    'type': spec.child_interface_type or 'virtual',
                    'speed': spec.child_speed_kbps,
                    'custom_field_data': {plane_field_name: plane_number},
                },
            )
            if not created and overwrite:
                iface.speed = spec.child_speed_kbps
                cf_data = iface.custom_field_data or {}
                cf_data[plane_field_name] = plane_number
                iface.custom_field_data = cf_data
                iface.save()
            results.append(iface)

    return results
```

### D5 — Integrate into `stamp_rack_population`

In `services/rack_population_stamp.py`, after a Device is created from a slot without an
`assembly_template` (the bare `Device.objects.create(...)` path), add:

```python
if slot.breakout_template and device:
    from .assembly_stamp import create_child_interfaces_from_breakout_spec
    create_child_interfaces_from_breakout_spec(
        device=device,
        breakout_template=slot.breakout_template,
        dry_run=dry_run,
    )
```

And for slots with `assembly_template`, also apply the breakout template after the device is
created (passive devices typically don't have child interfaces, but the hook should be there for
completeness).

### D6 — Registry entries

Add standard `ObjectSpec` entries for both `DeviceBreakoutTemplate` and `DeviceChildInterfaceSpec`:
- Navigation group: `Assembly Templates` (same group as existing templates)
- `DeviceBreakoutTemplate`: standard CRUD with a curated detail page showing its specs
- `DeviceChildInterfaceSpec`: inline-only from `DeviceBreakoutTemplate` detail, or simple list

### D7 — Reference data: GPU server breakout template

After migration, create the standard template for the reference topology:

```
DeviceBreakoutTemplate:
  name: GPU Server 800G 4-Plane Breakout
  slug: gpu-server-800g-4plane
  device_type: GPU-Server-8x800G  (FK)

DeviceChildInterfaceSpec (× 2):
  Spec 1:
    parent_interface_name: NIC0
    child_name_pattern: {parent}.plane{plane}
    child_count: 4
    child_interface_type: virtual
    child_speed_kbps: 200_000_000
    fabric_plane_start: 1
    breakout_profile: breakout-800g-4x200g
    sort_order: 0

  Spec 2:
    parent_interface_name: NIC1
    child_name_pattern: {parent}.plane{plane}
    child_count: 4
    child_interface_type: virtual
    child_speed_kbps: 200_000_000
    fabric_plane_start: 1
    sort_order: 1
```

### D8 — Standalone bulk-apply script/job

For devices that already exist (deployed before this feature), add a one-shot bulk-apply path:
either a NetBox script or a management command that takes a `DeviceBreakoutTemplate` slug and a
queryset of Device PKs and calls `create_child_interfaces_from_breakout_spec` for each.

This is the escape hatch for GAP #6's existing 192 manually-uncreated interfaces.

---

## Workstream E: `Fabric` Tier Role Map

**Gap:** #2 (no device-role → fabric-tier mapping, so PlantNode.role has no semantic validation)

### E1 — Add `tier_role_map` JSONField to `Fabric`

```python
# In models.py, on Fabric:
tier_role_map = models.JSONField(
    default=dict,
    blank=True,
    help_text=(
        'Map of NetBox device role slug → fabric tier level (integer, 0-indexed). '
        'Example: {"roce-leaf-switch": 0, "roce-spine-switch": 1}. '
        'Used by the transformer and audit service to classify PlantNode tier depth.'
    ),
)
```

### E2 — Use in `transformer.py`

In `transform_source_bundle`, when building `node_inputs`, apply the tier map:

```python
tier_role_map = getattr(fabric, 'tier_role_map', None) or {}
...
node_inputs[key] = {
    ...
    'role': _device_role_name(device),
    'tier_level': tier_role_map.get(_device_role_name(device)),  # int or None
    ...
}
```

Store `tier_level` in `PlantNode.metadata['tier_level']` at rebuild time (no separate DB field
needed for now — the JSONField is sufficient for audit and query use).

### E3 — Use in audit service

In `services/graph/audits.py`, when performing tier-depth validation, read
`fabric.tier_role_map` and `planting.metadata.get('tier_level')` to verify that paths actually
traverse the expected number of tier levels. Tier-depth violation becomes an explicit audit
finding type: `tier_depth_mismatch`.

### E4 — Migration

Add `tier_role_map = JSONField(default=dict, blank=True)` to `Fabric` in migration 0011.

---

## Workstream F: `BlastRadiusJob` Fix

**Gap:** #22 (`BlastRadiusJob.run()` returns "Not implemented yet")

### F1 — Wire the job to the service

The `compute_blast_radius` service in `services/graph/blast_radius.py` is fully implemented and
takes `target` (an `AttachmentUnit` or `SignalLane` object) and `resolution`.

The job's `run()` currently ignores kwargs. Fix:

```python
class BlastRadiusJob(JobRunner):
    class Meta:
        name = 'Blast Radius'
        description = 'Compute the blast radius for a selected failure.'

    def run(self, *args, **kwargs):
        from netbox_plant_graph.services.graph.blast_radius import compute_blast_radius
        from netbox_plant_graph.models import AttachmentUnit, PlantNode

        target_type = kwargs.get('target_type')
        target_id = kwargs.get('target_id')
        resolution = kwargs.get('resolution', 'attachment_unit')

        if not target_type or not target_id:
            raise ValueError('BlastRadiusJob requires target_type and target_id kwargs.')

        MODEL_MAP = {
            'attachmentunit': AttachmentUnit,
            'plantnode': PlantNode,
        }
        model = MODEL_MAP.get(target_type)
        if model is None:
            raise ValueError(f'Unsupported target_type: {target_type!r}')

        target = model.objects.get(pk=target_id)
        return compute_blast_radius(target=target, resolution=resolution)
```

> 📝 **NOTE:** The blast-radius UI page (`views/`) may already call `compute_blast_radius`
> directly, bypassing the job. Confirm the UI path before deploying this fix to avoid
> calling the service twice on a single user action.

---

## Workstream G: Rebuild Observability

**Gaps:** #12 (no documented UI trigger for `FullGraphRebuildJob`), #13 (`GraphBuildRun.stats`
schema not specified)

### G1 — Add "Trigger Rebuild" action to Fabric detail page

In `views.py` (or the `FabricDetailView` subclass), add a POST handler that enqueues
`FullGraphRebuildJob` with `fabric` as the scope. The Fabric detail template should render a
**Rebuild Graph** button that submits this form.

The button should be protected with a `permissions_required` check
(`netbox_plant_graph.change_fabric` or a dedicated `run_rebuild` permission).

### G2 — Document `GraphBuildRun.stats` schema

Add a module-level constant `GRAPH_BUILD_STATS_SCHEMA` to `services/sync/graph_builder.py`:

```python
GRAPH_BUILD_STATS_SCHEMA = {
    'plant_nodes_created': int,
    'plant_nodes_updated': int,
    'termination_points_created': int,
    'termination_points_updated': int,
    'attachment_units_created': int,
    'attachment_units_updated': int,
    'coarse_edges_created': int,
    'coarse_edges_updated': int,
    'fine_edges_created': int,
    'fine_edges_deleted_stale': int,
    'plane_memberships_created': int,
    'plane_memberships_deleted_stale': int,
    'signal_lanes_created': int,
    'transfer_maps_created': int,
    'warnings': list,   # list of {code: str, ...} dicts
}
```

The health page and Fabric detail view can use this schema to render a structured stats table.

---

## Migration 0011 — Batch schema changes

All model changes from Workstreams A–E are batched into a single migration `0011`.

Changes:
1. Add `netbox_plant_graph.BreakoutProfile` (new model)
2. Add `netbox_plant_graph.DeviceBreakoutTemplate` (new model)
3. Add `netbox_plant_graph.DeviceChildInterfaceSpec` (new model)
4. Add `Fabric.tier_role_map` (JSONField, default=dict)
5. Add `PlantNode.tenant` (ForeignKey to tenancy.Tenant, null=True)
6. Add `RackPopulationSlot.breakout_template` (ForeignKey to DeviceBreakoutTemplate, null=True)
7. Add `AssemblyTemplate.breakout_profile` (ForeignKey to BreakoutProfile, null=True)
8. Add `missing_breakout_profile` to `UnresolvedStateCauseChoices`

Dependencies: 0010 must be applied first (current latest migration).

---

## Workstream H: Runbook & Documentation Updates

**Gaps:** #5, #9, #14, #15, #19, #20, #21 (documentation, design decisions, UX)

These require runbook edits, not code changes.

### H1 — Update runbook Phase 8 with DeviceBreakoutTemplate workflow

Replace the manual child-interface creation section (Phase 8.2/8.3) with the new
DeviceBreakoutTemplate workflow:
- Create `DeviceBreakoutTemplate` and `DeviceChildInterfaceSpec` records
- Assign `breakout_template` to each `RackPopulationSlot`
- Run `stamp_rack_population` to create devices + child interfaces in one pass

### H2 — Update runbook Phase 9 with BreakoutProfile workflow

Replace the cable profile notes with:
- Create `BreakoutProfile` record (or confirm the standard one exists)
- Set `cable.profile = breakout-800g-4x200g` on all GPU-to-leaf and leaf-to-spine cables
  (or: add `breakout_profile` to `AssemblyTemplate` and have `stamp_cable_assembly` set it)

### H3 — Document GAP #19 resolution path

Add a section to the runbook explaining the two options for the shared-leaf topology:

**Option A:** Accept `DisjointnessException` for each plane-pair that shares a leaf.
- Navigate to: **Plant Graph Workflows → Exception Request**
- Create one `DisjointnessException` per plane-pair (6 pairs for 4 planes)
- Set `exception_type = shared_passive_artifact`, `scope_kind = artifact`
- Set `target = roce-leaf-ha-1` (or `hb-1`, `hc-1` as applicable)

**Option B:** Add dedicated per-plane leaf switches (4 per compute hall × 3 halls = 12 leaf
switches). This changes the topology to satisfy `disjointness_policy = full` without exceptions.

### H4 — Document operator tenant setup step

Add to Phase 1 of the runbook:

**Step 1.0 — Create operator tenant (do this first)**

Create a third tenant before the customer tenants:

| Name     | Slug     | Description                               |
|----------|----------|-------------------------------------------|
| Operator | operator | Infrastructure owner for shared resources |

Assign `Fabric.tenant = operator` for all fabrics.
Assign rack/device tenant = `operator` for all shared infrastructure.

---

## Implementation order

Execute workstreams in this order to minimize dependency chains:

```
B (self-checks, no migration) → run & validate
C + D + A + E (batch migration 0011) → run & validate
F (job fix, no migration) → run & validate
G (observability, no migration) → run & validate
H (runbook updates) → continuous
```

After migration 0011:
1. Create `BreakoutProfile: breakout-800g-4x200g` (Workstream C4)
2. Create `DeviceBreakoutTemplate: gpu-server-800g-4plane` + specs (Workstream D7)
3. Create `operator` tenant (Workstream H4)
4. Update `Fabric.tier_role_map` on existing/new fabrics (Workstream E)
5. Re-run `stamp_rack_population` on any existing rack populations OR run the bulk-apply script
   for existing devices (Workstream D8)
6. Set `cable.profile = breakout-800g-4x200g` on all relevant cables
7. Run `FullGraphRebuildJob`
8. Verify `GraphBuildRun.stats` shows non-zero `fine_edges_created` and `plane_memberships_created`
