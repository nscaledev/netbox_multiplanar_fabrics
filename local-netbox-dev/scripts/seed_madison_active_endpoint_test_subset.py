from __future__ import annotations

import csv
import os
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from django.db import transaction

from dcim.models import Device, DeviceRole, DeviceType, Rack
from extras.models import Tag
from tenancy.models import Tenant


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
SU_TAG_PREFIX = 'nv_su_'
SOURCE_MARKER_BEGIN = '<!-- madison-active-endpoint-test-subset:start -->'
SOURCE_MARKER_END = '<!-- madison-active-endpoint-test-subset:end -->'

DEFAULT_NVL72_SLOT = 'A2'
LEAFS_PER_PLANE = 16

NVL72_DEVICE_TYPE_SLUGS = {
    'poweredge-xe9712-gb300-compute-tray',
    'ps33-33kw-power-shelf',
    'gb300-nvl72-nvlink-switch-tray',
    'sn2201_m',
}

LEAF_PATTERN = re.compile(r'\bSU(?P<su>\d+)\s+BE\s+LEAF#(?P<leaf>\d+).*\.PL(?P<plane>[1-4])\b', re.IGNORECASE)

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_ACTIVE_TEST_APPLY') == '1'


def target_nvl72_slot() -> str:
    return os.environ.get('MADISON_TEST_NVL72_SLOT', DEFAULT_NVL72_SLOT).upper()


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison device placement manifest not found in: {MANIFEST_PATHS}')


def read_manifest() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        return list(csv.DictReader(handle))


def parse_csv_ints(value: str) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split(',') if part]


def leaf_metadata(row: dict[str, str]) -> tuple[int, int, int] | None:
    match = LEAF_PATTERN.search(row['source_label'])
    if not match:
        return None
    return int(match.group('su')), int(match.group('leaf')), int(match.group('plane'))


def row_sort_key(row: dict[str, str]) -> tuple:
    slot = row['physical_slot']
    return (slot[0], int(slot[1:]), int(row['ru_bottom']), row['source_cell'])


def select_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    slot = target_nvl72_slot()
    nvl72_rows = [
        row for row in rows
        if row['physical_slot'].upper() == slot and row['device_type_slug'] in NVL72_DEVICE_TYPE_SLUGS
    ]
    if not nvl72_rows:
        raise RuntimeError(f'No NVL72 active device rows found for slot {slot}.')

    leaf_candidates: list[tuple[int, int, int, dict[str, str]]] = []
    for row in rows:
        if row['device_type_slug'] != 'sn5610' or row['device_role_slug'] != 'be-leaf-switch':
            continue
        metadata = leaf_metadata(row)
        if metadata is None:
            continue
        su, leaf, plane = metadata
        leaf_candidates.append((su, leaf, plane, row))

    selected_leaf_rows: list[dict[str, str]] = []
    for plane in range(1, 5):
        plane_rows = [
            (su, leaf, row)
            for su, leaf, row_plane, row in leaf_candidates
            if row_plane == plane
        ]
        plane_rows.sort(key=lambda item: (item[0], item[1], row_sort_key(item[2])))
        first_rows = plane_rows[:LEAFS_PER_PLANE]
        if len(first_rows) != LEAFS_PER_PLANE:
            raise RuntimeError(f'Expected {LEAFS_PER_PLANE} leaf rows for plane {plane}; found {len(first_rows)}.')
        selected_leaf_rows.extend(row for _, _, row in first_rows)

    rows_by_key = {}
    for row in [*nvl72_rows, *selected_leaf_rows]:
        key = (row['physical_slot'], row['source_cell'], row['device_type_slug'])
        rows_by_key[key] = row
    return sorted(rows_by_key.values(), key=row_sort_key)


def device_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'mad1-{slot}-{ru}-{row["device_type_slug"]}'


def staged_comments(row: dict[str, str]) -> str:
    return f"""\
{SOURCE_MARKER_BEGIN}
Source workbook row elevation placement for active endpoint test subset.
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


def racks_by_slot() -> dict[str, Rack]:
    racks = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').select_related('site', 'location'):
        row_tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith(ROW_ID_TAG_PREFIX)]
        if len(row_tags) != 1:
            raise RuntimeError(f'Rack {rack.name} has {len(row_tags)} row-id tags; expected exactly one.')
        racks[row_tags[0][len(ROW_ID_TAG_PREFIX):].upper()] = rack
    return racks


def desired_su_tags(row: dict[str, str], rack: Rack, su_tags_by_number: dict[int, Tag]) -> list[Tag]:
    explicit_sus = parse_csv_ints(row['scalable_units'])
    if explicit_sus:
        return [su_tags_by_number[number] for number in explicit_sus]
    return sorted(
        [tag for tag in rack.tags.all() if tag.slug.startswith(SU_TAG_PREFIX)],
        key=lambda tag: tag.slug,
    )


def replace_prefix_tags(device: Device, desired_tags: list[Tag], prefix: str, counters: Counter, counter_prefix: str) -> None:
    current_tags = list(device.tags.filter(slug__startswith=prefix))
    desired_ids = {tag.pk for tag in desired_tags}
    current_ids = {tag.pk for tag in current_tags}
    for tag in current_tags:
        if tag.pk not in desired_ids:
            device.tags.remove(tag)
            counters[f'{counter_prefix}_tags_removed'] += 1
    for tag in desired_tags:
        if tag.pk not in current_ids:
            device.tags.add(tag)
            counters[f'{counter_prefix}_tags_added'] += 1


def validate_placement_height(row: dict[str, str], device_type: DeviceType) -> None:
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


def print_dry_run(rows: list[dict[str, str]]) -> None:
    leaf_rows = [row for row in rows if row['device_type_slug'] == 'sn5610' and row['device_role_slug'] == 'be-leaf-switch']
    leaf_by_plane = Counter()
    leaf_by_su = Counter()
    for row in leaf_rows:
        su, _, plane = leaf_metadata(row) or (None, None, None)
        leaf_by_plane[plane] += 1
        leaf_by_su[su] += 1

    print('Madison active endpoint test subset dry run')
    print(f'target_nvl72_slot={target_nvl72_slot()}')
    print(f'total_rows={len(rows)}')
    print(f'nvl72_rows={sum(1 for row in rows if row["physical_slot"].upper() == target_nvl72_slot())}')
    print(f'be_leaf_rows={len(leaf_rows)}')
    print(f'by_device_type={dict(sorted(Counter(row["device_type_slug"] for row in rows).items()))}')
    print(f'leaf_by_plane={dict(sorted(leaf_by_plane.items()))}')
    print(f'leaf_by_su={dict(sorted(leaf_by_su.items()))}')
    print('samples:')
    for row in rows[:20]:
        print(
            f'  {device_name(row)} slot={row["physical_slot"]} ru={row["ru_bottom"]}-{row["ru_top"]} '
            f'type={row["device_type_slug"]} label="{row["source_label"]}"'
        )


def main() -> None:
    rows = select_rows(read_manifest())
    print_dry_run(rows)

    racks = racks_by_slot()
    missing_racks = sorted({row['physical_slot'] for row in rows if row['physical_slot'] not in racks})
    if missing_racks:
        raise RuntimeError(f'Missing racks for active endpoint test subset: {missing_racks}')

    if not apply_enabled():
        print('apply=false; set MADISON_ACTIVE_TEST_APPLY=1 to create/update NetBox objects.')
        return

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    device_types = {device_type.slug: device_type for device_type in DeviceType.objects.filter(slug__in={row['device_type_slug'] for row in rows})}
    roles = {role.slug: role for role in DeviceRole.objects.filter(slug__in={row['device_role_slug'] for row in rows})}
    missing_device_types = sorted({row['device_type_slug'] for row in rows} - set(device_types))
    missing_roles = sorted({row['device_role_slug'] for row in rows} - set(roles))
    if missing_device_types or missing_roles:
        raise RuntimeError(f'Missing device_types={missing_device_types} roles={missing_roles}')

    su_numbers = {number for row in rows for number in parse_csv_ints(row['scalable_units'])}
    su_numbers.update(range(1, 38))
    su_tags_by_number = {number: Tag.objects.get(slug=f'{SU_TAG_PREFIX}{number}') for number in su_numbers}
    row_tags_by_slug = {tag.slug: tag for tag in Tag.objects.filter(slug__startswith=ROW_ID_TAG_PREFIX)}

    counters = Counter()
    with transaction.atomic():
        for row in rows:
            rack = racks[row['physical_slot']]
            device_type = device_types[row['device_type_slug']]
            validate_placement_height(row, device_type)
            position = None
            face = ''
            if int(device_type.u_height or 0) > 0:
                position = Decimal(row['ru_bottom'])
                face = 'front'

            defaults = {
                'device_type': device_type,
                'role': roles[row['device_role_slug']],
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
                    },
                    'madison_active_endpoint_test_subset': True,
                },
            }

            name = device_name(row)
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

            replace_prefix_tags(device, desired_su_tags(row, rack, su_tags_by_number), SU_TAG_PREFIX, counters, 'su')
            row_tag = row_tags_by_slug.get(row['row_id_tag'].lower())
            if row_tag is not None:
                replace_prefix_tags(device, [row_tag], ROW_ID_TAG_PREFIX, counters, 'row_id')

    print('Madison active endpoint test subset seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'test_subset_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG, local_context_data__madison_active_endpoint_test_subset=True).count()}')


main()
