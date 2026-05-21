from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from django.db import transaction

from dcim.models import Site
from tenancy.models import Tenant

from netbox_plant_graph.models import Fabric, FabricNode, Plane, StampTemplate, TransferPattern
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
FABRIC_NAME = 'MAD-1 RoCE Fabric'
FABRIC_SLUG = 'mad-1-roce-fabric'
PLAN_NAME = 'MAD-1 Fiber BOM v1.4'
PLAN_SLUG = 'mad-1-fiber-bom-v1-4'
SOURCE_MARKER = 'madison_fiber_bom_v1_4'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_fiber_bom_manifest.csv'),
    Path(__file__).resolve().parents[1] / 'data' / 'generated' / 'madison_fiber_bom_manifest.csv',
]


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison fiber BOM manifest not found in: {MANIFEST_PATHS}')


def read_rows() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        return list(csv.DictReader(handle))


def slugify(value: str) -> str:
    value = value.lower().replace('\u2192', '-to-')
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-')


def as_int(value: str | int | float | None) -> int:
    if value in (None, ''):
        return 0
    return int(float(value))


def as_float_label(value: str | int | float | None) -> str:
    if value in (None, ''):
        return '0'
    parsed = float(value)
    return str(int(parsed)) if parsed.is_integer() else str(parsed).replace('.', 'p')


def connector_count_for_fiber_count(fiber_count: int) -> int:
    if fiber_count and fiber_count % 8 == 0:
        return fiber_count // 8
    return 1


def template_slug_for(row: dict[str, str]) -> str:
    fiber_count = as_int(row['fiber_count'])
    if fiber_count <= 8:
        return 'madison-mpo8-smf-patch'
    return f'madison-{fiber_count}f-mpo8-smf-trunk'


def template_name_for(row: dict[str, str]) -> str:
    fiber_count = as_int(row['fiber_count'])
    if fiber_count <= 8:
        return 'Madison MPO8 SMF Patch'
    return f'Madison {fiber_count}f MPO8 SMF Trunk'


def ensure_fabric(tenant: Tenant, site: Site, counters: Counter) -> Fabric:
    fixture = ensure_roce_4plane_shuffle_architecture()
    fabric, created = Fabric.objects.get_or_create(
        slug=FABRIC_SLUG,
        defaults={
            'architecture': fixture.architecture,
            'name': FABRIC_NAME,
            'tenant': tenant,
            'scope_site': site,
            'status': 'planned',
            'metadata': {
                SOURCE_MARKER: True,
                'source': PLAN_NAME,
                'modeled_status': 'planned',
                'netbox_cables': 'forbidden_for_modeled_fabric',
            },
        },
    )
    changed = created
    desired = {
        'architecture': fixture.architecture,
        'name': FABRIC_NAME,
        'tenant': tenant,
        'scope_site': site,
        'status': 'planned',
    }
    for field, value in desired.items():
        if getattr(fabric, field) != value:
            setattr(fabric, field, value)
            changed = True
    metadata = dict(fabric.metadata or {})
    metadata.update(
        {
            SOURCE_MARKER: True,
            'source': PLAN_NAME,
            'modeled_status': 'planned',
            'netbox_cables': 'forbidden_for_modeled_fabric',
        }
    )
    if fabric.metadata != metadata:
        fabric.metadata = metadata
        changed = True
    if changed:
        fabric.full_clean()
        fabric.save()
    counters['fabric_created' if created else 'fabric_updated' if changed else 'fabric_unchanged'] += 1

    for plane_number in range(1, 5):
        plane, plane_created = Plane.objects.get_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'label': f'Plane {plane_number}',
                'metadata': {SOURCE_MARKER: True, 'modeled_status': 'planned'},
            },
        )
        plane_changed = plane_created
        metadata = dict(plane.metadata or {})
        metadata.update({SOURCE_MARKER: True, 'modeled_status': 'planned'})
        if plane.metadata != metadata:
            plane.metadata = metadata
            plane_changed = True
        if plane_changed:
            plane.full_clean()
            plane.save()
        counters['planes_created' if plane_created else 'planes_updated' if plane_changed else 'planes_unchanged'] += 1

    return fabric


def ensure_plan_template(fabric: Fabric, rows: list[dict[str, str]], counters: Counter) -> StampTemplate:
    totals = Counter()
    for row in rows:
        totals[f"{row['segment']}:trunk_or_patch_qty"] += as_int(row['trunk_qty'])
        totals[f"{row['segment']}:active_mpo_qty"] += as_int(row['active_mpo_qty'])

    template, created = StampTemplate.objects.get_or_create(
        slug=PLAN_SLUG,
        defaults={
            'architecture': fabric.architecture,
            'name': PLAN_NAME,
            'description': 'Madison fiber cable plant BOM lot plan. Creates logical FabricNode lots only; exact endpoints are stamped later.',
            'template': {
                'kind': 'fiber_bom_plan',
                'schema_version': 2,
                'fabric_slug': fabric.slug,
                'source_file': 'Nscale NC 18k Fiber BOM v1.4.xlsx',
                'bom_rows': len(rows),
                'totals': dict(totals),
                'v2_instantiation_targets': ['FabricNode', 'CableAssembly', 'FiberSegment', 'FiberStrand', 'StrandTermination'],
            },
            'metadata': {
                SOURCE_MARKER: True,
                'source': PLAN_NAME,
                'template_family': 'fiber_bom_plan',
                'modeled_status': 'planned',
            },
        },
    )
    changed = created
    desired_template = {
        'kind': 'fiber_bom_plan',
        'schema_version': 2,
        'fabric_slug': fabric.slug,
        'source_file': 'Nscale NC 18k Fiber BOM v1.4.xlsx',
        'bom_rows': len(rows),
        'totals': dict(totals),
        'v2_instantiation_targets': ['FabricNode', 'CableAssembly', 'FiberSegment', 'FiberStrand', 'StrandTermination'],
    }
    if template.architecture_id != fabric.architecture_id:
        template.architecture = fabric.architecture
        changed = True
    if template.template != desired_template:
        template.template = desired_template
        changed = True
    metadata = dict(template.metadata or {})
    metadata.update({SOURCE_MARKER: True, 'source': PLAN_NAME, 'template_family': 'fiber_bom_plan', 'modeled_status': 'planned'})
    if template.metadata != metadata:
        template.metadata = metadata
        changed = True
    if changed:
        template.full_clean()
        template.save()
    counters['plan_templates_created' if created else 'plan_templates_updated' if changed else 'plan_templates_unchanged'] += 1
    return template


def ensure_transfer_pattern(architecture, row: dict[str, str], counters: Counter) -> TransferPattern:
    slug = template_slug_for(row)
    fiber_count = as_int(row['fiber_count'])
    connector_count = connector_count_for_fiber_count(fiber_count)
    pattern, created = TransferPattern.objects.get_or_create(
        architecture=architecture,
        slug=slug,
        defaults={
            'name': template_name_for(row),
            'pattern_kind': 'identity',
            'rule': {
                'type': 'fiber_assembly_identity',
                'fiber_count': fiber_count,
                'connector_kind': 'mpo-12',
                'connector_count_per_side': connector_count,
                'positions_per_connector': 12,
                'mapping_mode': 'sequential',
            },
            'metadata': {
                SOURCE_MARKER: True,
                'source': PLAN_NAME,
            },
        },
    )
    changed = created
    desired_rule = {
        'type': 'fiber_assembly_identity',
        'fiber_count': fiber_count,
        'connector_kind': 'mpo-12',
        'connector_count_per_side': connector_count,
        'positions_per_connector': 12,
        'mapping_mode': 'sequential',
    }
    if pattern.name != template_name_for(row):
        pattern.name = template_name_for(row)
        changed = True
    if pattern.pattern_kind != 'identity':
        pattern.pattern_kind = 'identity'
        changed = True
    if pattern.rule != desired_rule:
        pattern.rule = desired_rule
        changed = True
    metadata = dict(pattern.metadata or {})
    metadata.update({SOURCE_MARKER: True, 'source': PLAN_NAME})
    if pattern.metadata != metadata:
        pattern.metadata = metadata
        changed = True
    if changed:
        pattern.full_clean()
        pattern.save()
    counters['transfer_patterns_created' if created else 'transfer_patterns_updated' if changed else 'transfer_patterns_unchanged'] += 1
    return pattern


def bom_lot_address(row: dict[str, str]) -> str:
    sheet_code = ''.join(part[0] for part in slugify(row['source_sheet']).split('-') if part)[:5]
    length = as_float_label(row['length_m'])
    dest = slugify(row['dest_rack'])[:36] or 'any'
    return f"mad1.fiber.v14.{sheet_code}.r{as_int(row['source_row']):03d}.{row['segment']}.{as_int(row['fiber_count'])}f.{length}m.{dest}"[:500]


def bom_lot_name(row: dict[str, str]) -> str:
    return bom_lot_address(row).replace('.', '-').replace('_', '-')[:200]


def ensure_bom_lot(row: dict[str, str], fabric: Fabric, pattern: TransferPattern, counters: Counter) -> FabricNode:
    metadata = {
        SOURCE_MARKER: True,
        'source_file': 'Nscale NC 18k Fiber BOM v1.4.xlsx',
        'source_sheet': row['source_sheet'],
        'source_row': as_int(row['source_row']),
        'domain': row['domain'],
        'segment': row['segment'],
        'section': row['section'],
        'source': row['source'],
        'dest_rack': row['dest_rack'],
        'cable_type': row['cable_type'],
        'fiber_count': as_int(row['fiber_count']),
        'length_m': float(row['length_m'] or 0),
        'trunk_or_patch_qty': as_int(row['trunk_qty']),
        'active_mpo_qty': as_int(row['active_mpo_qty']),
        'carried_cable_qty': as_int(row['carried_cable_qty']),
        'transfer_pattern_slug': pattern.slug,
        'modeled_status': 'planned',
        'notes': row['notes'],
    }
    node, created = FabricNode.objects.get_or_create(
        fabric=fabric,
        address=bom_lot_address(row),
        defaults={
            'name': bom_lot_name(row),
            'node_kind': 'logical_container',
            'metadata': metadata,
        },
    )
    changed = created
    if node.name != bom_lot_name(row):
        node.name = bom_lot_name(row)
        changed = True
    if node.node_kind != 'logical_container':
        node.node_kind = 'logical_container'
        changed = True
    if node.metadata != metadata:
        node.metadata = metadata
        changed = True
    if changed:
        node.full_clean()
        node.save()
    counters['bom_lots_created' if created else 'bom_lots_updated' if changed else 'bom_lots_unchanged'] += 1
    return node


@transaction.atomic
def main() -> None:
    rows = read_rows()
    if not rows:
        raise RuntimeError('Madison fiber BOM manifest has no rows.')

    counters = Counter()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    site = Site.objects.get(slug=MAD_SITE_SLUG)
    fabric = ensure_fabric(tenant, site, counters)
    ensure_plan_template(fabric, rows, counters)

    pattern_by_slug: dict[str, TransferPattern] = {}
    for row in rows:
        slug = template_slug_for(row)
        if slug not in pattern_by_slug:
            pattern_by_slug[slug] = ensure_transfer_pattern(fabric.architecture, row, counters)
        ensure_bom_lot(row, fabric, pattern_by_slug[slug], counters)

    print('Madison fiber BOM plan seeding complete for netbox_plant_graph v2.')
    for key, value in sorted(counters.items()):
        print(f'{key}={value}')
    print(f'fabric={fabric.name}')
    print(f'bom_lots={FabricNode.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'transfer_patterns={TransferPattern.objects.filter(architecture=fabric.architecture, metadata__has_key=SOURCE_MARKER).count()}')


if __name__ == '__main__':
    main()
