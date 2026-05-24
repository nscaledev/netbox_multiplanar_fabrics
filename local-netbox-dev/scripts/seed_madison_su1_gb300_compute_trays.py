from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from django.db import transaction

from dcim.models import Device, DeviceRole, DeviceType, Rack
from extras.models import Tag
from tenancy.models import Tenant

for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_nvl72_appliance import (  # noqa: E402
    device_bay_for_child_row,
    install_child_device_in_bay,
    nvl72_bay_assignments,
)


MAD_SITE_SLUG = 'gs001'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
TARGET_SU_TAG = 'nv_su_1'
TARGET_SLOTS = {'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8'}
TARGET_DEVICE_TYPE = 'gb300ct'
SOURCE_MARKER_BEGIN = '<!-- madison-su1-gb300-compute-trays:start -->'
SOURCE_MARKER_END = '<!-- madison-su1-gb300-compute-trays:end -->'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_SU1_GB300_APPLY') == '1'


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison device placement manifest not found in: {MANIFEST_PATHS}')


def read_manifest() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        return list(csv.DictReader(handle))


def selected_rows() -> list[dict[str, str]]:
    rows = [
        row for row in read_manifest()
        if row['physical_slot'].upper() in TARGET_SLOTS
        and row['device_type_slug'] == TARGET_DEVICE_TYPE
        and row['rack_source_label'] == 'SU1 GPU Rack'
    ]
    expected = len(TARGET_SLOTS) * 18
    if len(rows) != expected:
        raise RuntimeError(f'Expected {expected} SU1 GB300 compute tray rows; found {len(rows)}.')
    return sorted(rows, key=lambda row: (row['physical_slot'], int(row['ru_bottom'])))


def device_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru = int(row['ru_bottom'])
    return f'gs001-{slot}-u{ru:02d}-{row["device_type_slug"]}'


def staged_comments(row: dict[str, str]) -> str:
    return f"""\
{SOURCE_MARKER_BEGIN}
Source workbook row elevation placement for SU1 GB300 endpoint expansion.
- physical_slot: {row['physical_slot']}
- row_id_tag: {row['row_id_tag']}
- row_sheet: {row['row_sheet']}
- source_cell: {row['source_cell']}
- source_label: {row['source_label']}
- normalized_label: {row['normalized_label']}
- ru_top: {row['ru_top']}
- ru_bottom: {row['ru_bottom']}
- height_u: {row['height_u']}
- rack_source_label: {row['rack_source_label']}
{SOURCE_MARKER_END}"""


def racks_by_slot() -> dict[str, Rack]:
    racks = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').select_related('site', 'location'):
        row_tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith('nscale-row-id-')]
        if len(row_tags) != 1:
            continue
        racks[row_tags[0].removeprefix('nscale-row-id-').upper()] = rack
    return racks


def print_dry_run(rows: list[dict[str, str]]) -> None:
    by_slot = Counter(row['physical_slot'] for row in rows)
    print('Madison SU1 GB300 compute tray dry run')
    print(f'target_su_tag={TARGET_SU_TAG}')
    print(f'target_slots={",".join(sorted(TARGET_SLOTS))}')
    print(f'rows={len(rows)}')
    print(f'by_slot={dict(sorted(by_slot.items()))}')
    for row in rows[:10]:
        print(f'  {device_name(row)} {row["physical_slot"]} U{row["ru_bottom"]} source={row["source_cell"]}')


def main() -> None:
    rows = selected_rows()
    print_dry_run(rows)
    nvl72_bay_map = nvl72_bay_assignments(rows)
    if not apply_enabled():
        print('apply=false; set MADISON_SU1_GB300_APPLY=1 to create/update SU1 GB300 trays.')
        return

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    device_type = DeviceType.objects.get(slug=TARGET_DEVICE_TYPE)
    role = DeviceRole.objects.get(slug=TARGET_DEVICE_TYPE)
    su_tag = Tag.objects.get(slug=TARGET_SU_TAG)
    row_tags = {tag.slug: tag for tag in Tag.objects.filter(slug__startswith='nscale-row-id-')}
    racks = racks_by_slot()
    counters = Counter()

    with transaction.atomic():
        for row in rows:
            rack = racks.get(row['physical_slot'])
            if rack is None:
                raise RuntimeError(f'No GS001 rack found for row-id slot {row["physical_slot"]}.')
            row_tag = row_tags.get(row['row_id_tag'].lower())
            if row_tag is None:
                raise RuntimeError(f'Missing row-id tag {row["row_id_tag"]}.')

            local_context_data = {
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
                    'scalable_units': [1],
                    'backend_planes': [],
                },
                'madison_active_endpoint_test_subset': True,
            }
            defaults = {
                'device_type': device_type,
                'role': role,
                'tenant': tenant,
                'site': rack.site,
                'location': rack.location,
                'rack': rack,
                'position': Decimal(row['ru_bottom']),
                'face': 'front',
                'status': PLANNED_STATUS,
                'description': row['source_label'][:200],
                'comments': staged_comments(row),
                'local_context_data': local_context_data,
            }
            parent_bay = device_bay_for_child_row(rack=rack, row=row, assignments=nvl72_bay_map)
            if parent_bay is not None:
                defaults['rack'] = None
                defaults['position'] = None
                defaults['face'] = ''
            device, created = Device.objects.get_or_create(
                site=rack.site,
                name=device_name(row),
                defaults=defaults,
            )
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
            if parent_bay is not None:
                install_child_device_in_bay(device, parent_bay, counters)
            for tag in (su_tag, row_tag):
                if not device.tags.filter(pk=tag.pk).exists():
                    device.tags.add(tag)
                    counters['tags_added'] += 1

    print('Madison SU1 GB300 compute tray seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    for slot in sorted(TARGET_SLOTS):
        count = Device.objects.filter(site__slug=MAD_SITE_SLUG, rack__name=slot, device_type__slug=TARGET_DEVICE_TYPE).count()
        print(f'{slot}_gb300_trays={count}')


main()
