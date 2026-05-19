from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from django.db import transaction

from dcim.models import Manufacturer, Site
from tenancy.models import Tenant

from netbox_plant_graph.models import (
    AssemblyConnectorTemplate,
    AssemblyMappingTemplate,
    AssemblyTemplate,
    BreakoutProfile,
    DeploymentPlan,
    Fabric,
    FabricPlane,
    PlantNode,
)


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
FABRIC_NAME = 'MAD-1 RoCE Fabric'
PLAN_NAME = 'MAD-1 Fiber BOM v1.4'
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


def assembly_slug_for(row: dict[str, str]) -> str:
    fiber_count = as_int(row['fiber_count'])
    cable_type = row['cable_type'] or 'smf-mpo8'
    if fiber_count <= 8:
        return 'sm-mpo8-patch'
    return f'{fiber_count}f-sm-mpo8-trunk'


def assembly_name_for(row: dict[str, str]) -> str:
    fiber_count = as_int(row['fiber_count'])
    if fiber_count <= 8:
        return 'SM MPO8 Patch Cable'
    return f'{fiber_count}f SM MPO8 Trunk Assembly'


def ensure_fabric(tenant: Tenant, site: Site, counters: Counter) -> Fabric:
    fabric, created = Fabric.objects.get_or_create(
        name=FABRIC_NAME,
        defaults={
            'tenant': tenant,
            'scope_site': site,
            'expected_plane_count': 4,
            'tier_depth': 3,
            'description': 'Madison NC RoCE fabric staged from workbook, Notion shuffle patterns, and fiber BOM.',
            'metadata': {
                SOURCE_MARKER: True,
                'source': PLAN_NAME,
                'modeled_status': 'planned',
            },
        },
    )
    changed = created
    desired = {
        'tenant': tenant,
        'scope_site': site,
        'expected_plane_count': 4,
        'tier_depth': 3,
    }
    for field, value in desired.items():
        if getattr(fabric, field) != value:
            setattr(fabric, field, value)
            changed = True
    metadata = dict(fabric.metadata or {})
    if metadata.get(SOURCE_MARKER) is not True:
        metadata[SOURCE_MARKER] = True
        changed = True
    if metadata.get('modeled_status') != 'planned':
        metadata['modeled_status'] = 'planned'
        changed = True
    fabric.metadata = metadata
    if changed:
        fabric.full_clean()
        fabric.save()
    counters['fabric_created' if created else 'fabric_updated' if changed else 'fabric_unchanged'] += 1

    for plane_number in range(1, 5):
        plane, plane_created = FabricPlane.objects.get_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'description': f'MAD-1 RoCE plane {plane_number}',
                'metadata': {SOURCE_MARKER: True, 'modeled_status': 'planned'},
            },
        )
        plane_changed = plane_created
        metadata = dict(plane.metadata or {})
        if metadata.get(SOURCE_MARKER) is not True:
            metadata[SOURCE_MARKER] = True
            plane_changed = True
        if metadata.get('modeled_status') != 'planned':
            metadata['modeled_status'] = 'planned'
            plane_changed = True
        plane.metadata = metadata
        if plane_changed:
            plane.full_clean()
            plane.save()
        counters['planes_created' if plane_created else 'planes_updated' if plane_changed else 'planes_unchanged'] += 1

    return fabric


def ensure_plan(fabric: Fabric, tenant: Tenant, rows: list[dict[str, str]], counters: Counter) -> DeploymentPlan:
    totals = Counter()
    for row in rows:
        totals[f"{row['segment']}:trunk_or_patch_qty"] += as_int(row['trunk_qty'])
        totals[f"{row['segment']}:active_mpo_qty"] += as_int(row['active_mpo_qty'])

    plan, created = DeploymentPlan.objects.get_or_create(
        name=PLAN_NAME,
        defaults={
            'fabric': fabric,
            'tenant': tenant,
            'status': 'review',
            'description': 'Staged Madison fiber cable plant BOM lots; exact endpoint stamping remains planned.',
            'metadata': {
                SOURCE_MARKER: True,
                'source_file': 'Nscale NC 18k Fiber BOM v1.4.xlsx',
                'bom_rows': len(rows),
                'totals': dict(totals),
                'modeled_status': 'planned',
            },
        },
    )
    changed = created
    desired = {
        'fabric': fabric,
        'tenant': tenant,
        'status': 'review',
        'description': 'Staged Madison fiber cable plant BOM lots; exact endpoint stamping remains planned.',
    }
    for field, value in desired.items():
        if getattr(plan, field) != value:
            setattr(plan, field, value)
            changed = True
    metadata = dict(plan.metadata or {})
    metadata.update(
        {
            SOURCE_MARKER: True,
            'source_file': 'Nscale NC 18k Fiber BOM v1.4.xlsx',
            'bom_rows': len(rows),
            'totals': dict(totals),
            'modeled_status': 'planned',
        }
    )
    if plan.metadata != metadata:
        plan.metadata = metadata
        changed = True
    if changed:
        plan.full_clean()
        plan.save()
    counters['plans_created' if created else 'plans_updated' if changed else 'plans_unchanged'] += 1
    return plan


def ensure_breakout_profile(row: dict[str, str], counters: Counter) -> BreakoutProfile:
    slug = assembly_slug_for(row)
    fiber_count = as_int(row['fiber_count'])
    child_count = connector_count_for_fiber_count(fiber_count)
    profile, created = BreakoutProfile.objects.get_or_create(
        slug=slug,
        defaults={
            'name': assembly_name_for(row),
            'description': f'{fiber_count} fiber MPO8 assembly profile staged from Madison fiber BOM.' if fiber_count else 'MPO8 assembly profile staged from Madison fiber BOM.',
            'child_count': child_count,
            'mapping_mode': 'sequential',
            'metadata': {
                SOURCE_MARKER: True,
                'fiber_count': fiber_count,
                'connector_count_per_side': child_count,
                'active_mpo_per_trunk': as_int(row.get('active_mpo_per_trunk')),
                'spare_mpo_per_trunk': as_int(row.get('spare_mpo_per_trunk')),
            },
        },
    )
    changed = created
    desired = {
        'name': assembly_name_for(row),
        'child_count': child_count,
        'mapping_mode': 'sequential',
    }
    for field, value in desired.items():
        if getattr(profile, field) != value:
            setattr(profile, field, value)
            changed = True
    metadata = dict(profile.metadata or {})
    metadata.update(
        {
            SOURCE_MARKER: True,
            'fiber_count': fiber_count,
            'connector_count_per_side': child_count,
        }
    )
    if profile.metadata != metadata:
        profile.metadata = metadata
        changed = True
    if changed:
        profile.full_clean()
        profile.save()
    counters['breakout_profiles_created' if created else 'breakout_profiles_updated' if changed else 'breakout_profiles_unchanged'] += 1
    return profile


def ensure_assembly_template(row: dict[str, str], tenant: Tenant, manufacturer: Manufacturer, counters: Counter) -> AssemblyTemplate:
    profile = ensure_breakout_profile(row, counters)
    slug = assembly_slug_for(row)
    fiber_count = as_int(row['fiber_count'])
    connector_count = connector_count_for_fiber_count(fiber_count)
    template, created = AssemblyTemplate.objects.get_or_create(
        slug=slug,
        defaults={
            'name': assembly_name_for(row),
            'tenant': tenant,
            'manufacturer': manufacturer,
            'assembly_type': 'trunk_bundle',
            'cable_profile_hint': slug,
            'breakout_profile': profile,
            'description': f'{fiber_count} fiber singlemode MPO8 assembly from Madison fiber BOM.' if fiber_count else 'Singlemode MPO8 assembly from Madison fiber BOM.',
            'metadata': {
                SOURCE_MARKER: True,
                'fiber_count': fiber_count,
                'connector_count_per_side': connector_count,
            },
        },
    )
    changed = created
    desired = {
        'name': assembly_name_for(row),
        'tenant': tenant,
        'manufacturer': manufacturer,
        'assembly_type': 'trunk_bundle',
        'cable_profile_hint': slug,
        'breakout_profile': profile,
    }
    for field, value in desired.items():
        if getattr(template, field) != value:
            setattr(template, field, value)
            changed = True
    metadata = dict(template.metadata or {})
    metadata.update(
        {
            SOURCE_MARKER: True,
            'fiber_count': fiber_count,
            'connector_count_per_side': connector_count,
        }
    )
    if template.metadata != metadata:
        template.metadata = metadata
        changed = True
    if changed:
        template.full_clean()
        template.save()
    counters['assembly_templates_created' if created else 'assembly_templates_updated' if changed else 'assembly_templates_unchanged'] += 1

    ensure_connectors_and_mappings(template, connector_count, counters)
    return template


def ensure_connectors_and_mappings(template: AssemblyTemplate, connector_count: int, counters: Counter) -> None:
    desired_keys = {(side, number) for side in ('A', 'B') for number in range(1, connector_count + 1)}
    stale_connectors = template.connectors.exclude(
        side__in=('A', 'B'),
    )
    counters['stale_connectors_deleted'] += stale_connectors.count()
    stale_connectors.delete()

    for connector in list(template.connectors.all()):
        if (connector.side, connector.connector_number) not in desired_keys:
            connector.delete()
            counters['stale_connectors_deleted'] += 1

    connectors = {}
    for side, number in sorted(desired_keys):
        connector, created = AssemblyConnectorTemplate.objects.get_or_create(
            template=template,
            side=side,
            connector_number=number,
            defaults={
                'connector_type': 'mpo-8',
                'position_count': 8,
                'label': f'{side}{number:02d}',
                'metadata': {SOURCE_MARKER: True},
            },
        )
        changed = created
        desired = {'connector_type': 'mpo-8', 'position_count': 8, 'label': f'{side}{number:02d}'}
        for field, value in desired.items():
            if getattr(connector, field) != value:
                setattr(connector, field, value)
                changed = True
        metadata = dict(connector.metadata or {})
        if metadata.get(SOURCE_MARKER) is not True:
            metadata[SOURCE_MARKER] = True
            changed = True
        connector.metadata = metadata
        if changed:
            connector.full_clean()
            connector.save()
        counters['connectors_created' if created else 'connectors_updated' if changed else 'connectors_unchanged'] += 1
        connectors[(side, number)] = connector

    expected_mapping_keys = set()
    for number in range(1, connector_count + 1):
        a_connector = connectors[('A', number)]
        b_connector = connectors[('B', number)]
        for position in range(1, 9):
            expected_mapping_keys.add((a_connector.pk, position))
            mapping, created = AssemblyMappingTemplate.objects.get_or_create(
                template=template,
                a_connector=a_connector,
                a_position=position,
                defaults={
                    'b_connector': b_connector,
                    'b_position': position,
                    'mapping_type': 'identity',
                    'metadata': {SOURCE_MARKER: True},
                },
            )
            changed = created
            desired = {'b_connector': b_connector, 'b_position': position, 'mapping_type': 'identity'}
            for field, value in desired.items():
                if getattr(mapping, field) != value:
                    setattr(mapping, field, value)
                    changed = True
            metadata = dict(mapping.metadata or {})
            if metadata.get(SOURCE_MARKER) is not True:
                metadata[SOURCE_MARKER] = True
                changed = True
            mapping.metadata = metadata
            if changed:
                mapping.full_clean()
                mapping.save()
            counters['mappings_created' if created else 'mappings_updated' if changed else 'mappings_unchanged'] += 1

    for mapping in template.mappings.select_related('a_connector'):
        if (mapping.a_connector_id, mapping.a_position) not in expected_mapping_keys:
            mapping.delete()
            counters['stale_mappings_deleted'] += 1


def plant_node_name(row: dict[str, str]) -> str:
    sheet_code = ''.join(part[0] for part in slugify(row['source_sheet']).split('-') if part)[:5]
    length = as_float_label(row['length_m'])
    dest = slugify(row['dest_rack'])[:36] or 'any'
    return f"mad1-fiber-v14-{sheet_code}-r{as_int(row['source_row']):03d}-{row['segment']}-{as_int(row['fiber_count'])}f-{length}m-{dest}"[:200]


def ensure_bom_lot(row: dict[str, str], fabric: Fabric, tenant: Tenant, assembly: AssemblyTemplate, counters: Counter) -> PlantNode:
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
        'assembly_template_slug': assembly.slug,
        'modeled_status': 'planned',
        'notes': row['notes'],
    }
    node_type = 'trunk_bundle' if as_int(row['fiber_count']) > 8 else 'cable_assembly'
    node, created = PlantNode.objects.get_or_create(
        fabric=fabric,
        name=plant_node_name(row),
        defaults={
            'node_type': node_type,
            'role': row['segment'],
            'status': 'planned',
            'tenant': tenant,
            'metadata': metadata,
        },
    )
    changed = created
    desired = {'node_type': node_type, 'role': row['segment'], 'status': 'planned', 'tenant': tenant}
    for field, value in desired.items():
        if getattr(node, field) != value:
            setattr(node, field, value)
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
    manufacturer = Manufacturer.objects.get(slug='nscale')
    fabric = ensure_fabric(tenant, site, counters)
    ensure_plan(fabric, tenant, rows, counters)

    assembly_by_slug: dict[str, AssemblyTemplate] = {}
    for row in rows:
        slug = assembly_slug_for(row)
        if slug not in assembly_by_slug:
            assembly_by_slug[slug] = ensure_assembly_template(row, tenant, manufacturer, counters)
        ensure_bom_lot(row, fabric, tenant, assembly_by_slug[slug], counters)

    print('Madison fiber BOM plan seeding complete.')
    for key, value in sorted(counters.items()):
        print(f'{key}={value}')
    print(f'fabric={fabric.name}')
    print(f'bom_lots={PlantNode.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'assembly_templates={AssemblyTemplate.objects.filter(metadata__has_key=SOURCE_MARKER).count()}')


if __name__ == '__main__':
    main()
