from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from django.db import transaction

from dcim.models import Device, DeviceRole, DeviceType, Rack
from extras.models import Tag
from tenancy.models import Tenant


MAD_SITE_SLUG = 'gs001'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
SU_TAG_PREFIX = 'nv_su_'
SOURCE_MARKER_BEGIN = '<!-- madison-workbook-device-instantiation:start -->'
SOURCE_MARKER_END = '<!-- madison-workbook-device-instantiation:end -->'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path(__file__).resolve().parents[1] / 'data' / 'generated' / 'madison_device_placement_manifest.csv',
]

COMPONENT_TEMPLATE_RELATED_NAMES = [
    'consoleporttemplates',
    'consoleserverporttemplates',
    'powerporttemplates',
    'poweroutlettemplates',
    'interfacetemplates',
    'rearporttemplates',
    'frontporttemplates',
    'modulebaytemplates',
    'devicebaytemplates',
    'inventoryitemtemplates',
]

SU_INHERIT_DEVICE_ROLES = {
    'be-leaf-switch',
    'fe-leaf-switch',
    'fe-spine-switch',
    'gb300ct',
    'gb300ps',
    'gb300st',
    'shuffle-cassette',
}


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison device placement manifest not found in: {MANIFEST_PATHS}')


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def parse_csv_ints(value: str) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split(',') if part]


def staged_comments(row: dict[str, str]) -> str:
    section = f"""\
{SOURCE_MARKER_BEGIN}
Source workbook row elevation placement.
- physical_slot: {row['physical_slot']}
- row_id_tag: {row['row_id_tag']}
- row_sheet: {row['row_sheet']}
- source_cell: {row['source_cell']}
- source_label: {row['source_label']}
- normalized_label: {row['normalized_label']}
- ru_top: {row['ru_top']}
- ru_bottom: {row['ru_bottom']}
- height_u: {row['height_u']}
- merged_range: {row['merged_range'] or 'none'}
{SOURCE_MARKER_END}"""
    return section


def device_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'gs001-{slot}-{ru}-{row["device_type_slug"]}'


def racks_by_slot():
    racks = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').select_related('site', 'location'):
        row_tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith(ROW_ID_TAG_PREFIX)]
        if len(row_tags) != 1:
            raise RuntimeError(f'Rack {rack.name} has {len(row_tags)} row-id tags; expected exactly one.')
        slot = row_tags[0][len(ROW_ID_TAG_PREFIX):].upper()
        racks[slot] = rack
    return racks


def singleton_by_slug(model, slugs):
    found = defaultdict(list)
    for obj in model.objects.filter(slug__in=slugs):
        found[obj.slug].append(obj)
    duplicates = {slug: [obj.pk for obj in objs] for slug, objs in found.items() if len(objs) > 1}
    if duplicates:
        raise RuntimeError(f'Duplicate {model.__name__} slugs prevent deterministic seeding: {duplicates}')
    missing = sorted(set(slugs) - set(found))
    if missing:
        raise RuntimeError(f'Missing {model.__name__} slugs. Run seed_madison_device_definitions.py first: {missing}')
    return {slug: objs[0] for slug, objs in found.items()}


def desired_su_tags(row, rack, su_tags_by_number):
    explicit_sus = parse_csv_ints(row['scalable_units'])
    if explicit_sus:
        return [su_tags_by_number[number] for number in explicit_sus]

    rack_su_tags = sorted(
        [tag for tag in rack.tags.all() if tag.slug.startswith(SU_TAG_PREFIX)],
        key=lambda tag: tag.slug,
    )
    if row['device_role_slug'] in SU_INHERIT_DEVICE_ROLES:
        return rack_su_tags
    return []


def replace_su_tags(device, desired_tags, counters):
    current_tags = list(device.tags.filter(slug__startswith=SU_TAG_PREFIX))
    desired_ids = {tag.pk for tag in desired_tags}
    current_ids = {tag.pk for tag in current_tags}

    for tag in current_tags:
        if tag.pk not in desired_ids:
            device.tags.remove(tag)
            counters['su_tags_removed'] += 1
    for tag in desired_tags:
        if tag.pk not in current_ids:
            device.tags.add(tag)
            counters['su_tags_added'] += 1

    if len(desired_tags) > 1:
        counters['devices_with_multiple_su_tags'] += 1
    elif desired_tags:
        counters['devices_with_single_su_tag'] += 1
    else:
        counters['devices_without_su_tags'] += 1


def count_components(device, counters):
    for related_name in COMPONENT_TEMPLATE_RELATED_NAMES:
        counters[f'{related_name.removesuffix("templates")}_components_present'] += component_count(device, related_name)


def component_count(device, related_name):
    component_related_name = related_name.removesuffix('templates')
    if component_related_name == 'interface':
        component_related_name = 'interfaces'
    elif component_related_name == 'inventoryitem':
        component_related_name = 'inventoryitems'
    elif component_related_name == 'modulebay':
        component_related_name = 'modulebays'
    elif component_related_name == 'devicebay':
        component_related_name = 'devicebays'
    elif component_related_name == 'poweroutlet':
        component_related_name = 'poweroutlets'
    elif component_related_name == 'powerport':
        component_related_name = 'powerports'
    elif component_related_name == 'consoleport':
        component_related_name = 'consoleports'
    elif component_related_name == 'consoleserverport':
        component_related_name = 'consoleserverports'
    elif component_related_name == 'frontport':
        component_related_name = 'frontports'
    elif component_related_name == 'rearport':
        component_related_name = 'rearports'
    return getattr(device, component_related_name).count()


def validate_placement_height(row, device_type):
    workbook_height = int(row['height_u'])
    device_type_height = int(device_type.u_height or 0)
    if device_type_height == 0:
        return
    if device_type_height != workbook_height:
        raise RuntimeError(
            f'Device type height mismatch for {row["physical_slot"]} {row["source_cell"]} '
            f'{row["source_label"]}: manifest={workbook_height}U device_type={device_type_height}U '
            f'({device_type.slug})'
        )


def main():
    path = manifest_path()
    rows = read_manifest(path)
    seed_rows = [
        row for row in rows
        if row['device_type_slug'] and row['source_label'] != '(Moved to MMR) Nscale OOB Edge #1'
    ]
    skipped_rows = len(rows) - len(seed_rows)

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    racks = racks_by_slot()
    device_types = singleton_by_slug(DeviceType, {row['device_type_slug'] for row in seed_rows})
    roles = singleton_by_slug(DeviceRole, {row['device_role_slug'] for row in seed_rows})
    su_numbers = {
        number
        for row in seed_rows
        for number in parse_csv_ints(row['scalable_units'])
    }
    su_numbers.update(range(1, 38))
    su_tags_by_number = {
        number: Tag.objects.get(slug=f'{SU_TAG_PREFIX}{number}')
        for number in su_numbers
    }

    counters = Counter()
    with transaction.atomic():
        for row in seed_rows:
            rack = racks.get(row['physical_slot'])
            if rack is None:
                raise RuntimeError(f'No GS001 rack found with row-id slot {row["physical_slot"]}')

            device_type = device_types[row['device_type_slug']]
            validate_placement_height(row, device_type)
            role = roles[row['device_role_slug']]
            name = device_name(row)

            position = None
            face = ''
            if int(device_type.u_height or 0) > 0:
                position = Decimal(row['ru_bottom'])
                face = 'front'
            else:
                counters['zero_u_devices_without_native_position'] += 1

            defaults = {
                'device_type': device_type,
                'role': role,
                'tenant': tenant,
                'site': rack.site,
                'location': rack.location,
                'rack': rack,
                'position': position,
                'face': face,
                'status': PLANNED_STATUS,
                'description': row['source_label'][:200],
                'comments': staged_comments(row),
                'local_context_data': {
                    'madison_workbook': {
                        'physical_slot': row['physical_slot'],
                        'row_id_tag': row['row_id_tag'],
                        'row_sheet': row['row_sheet'],
                        'source_cell': row['source_cell'],
                        'source_label': row['source_label'],
                        'normalized_label': row['normalized_label'],
                        'ru_top': int(row['ru_top']),
                        'ru_bottom': int(row['ru_bottom']),
                        'height_u': int(row['height_u']),
                        'merged_range': row['merged_range'] or None,
                        'scalable_units': parse_csv_ints(row['scalable_units']),
                        'backend_planes': parse_csv_ints(row['backend_planes']),
                    }
                },
            }

            device, created = Device.objects.get_or_create(site=rack.site, name=name, defaults=defaults)
            changed = False
            if not created:
                for field, value in defaults.items():
                    if getattr(device, field) != value:
                        setattr(device, field, value)
                        changed = True
            if created or changed:
                device.full_clean()
                device.save()

            counters['devices_created' if created else 'devices_updated' if changed else 'devices_unchanged'] += 1
            counters[f'device_type_{device_type.slug}'] += 1
            counters[f'row_{row["physical_slot"][0]}_devices'] += 1

            replace_su_tags(device, desired_su_tags(row, rack, su_tags_by_number), counters)

            if created:
                count_components(device, counters)

    print('Madison workbook device instantiation complete.')
    print(f'manifest_path={path}')
    print(f'manifest_rows={len(rows)}')
    print(f'rows_seeded={len(seed_rows)}')
    print(f'rows_skipped={skipped_rows}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'madison_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG).count()}')
    print(f'madison_planned_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG, status=PLANNED_STATUS).count()}')
    print(f'madison_nscale_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG, tenant=tenant).count()}')


main()
