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
TARGET_DEVICE_TYPE = 'poweredge-xe9712-gb300-compute-tray'
SOURCE_MARKER_BEGIN = '<!-- madison-pattern-su-gb300-compute-trays:start -->'
SOURCE_MARKER_END = '<!-- madison-pattern-su-gb300-compute-trays:end -->'

PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]
MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_PATTERN_SU_GB300_APPLY') == '1'


def target_sus() -> set[int] | None:
    raw = os.environ.get('MADISON_TARGET_SUS', '').strip()
    if not raw:
        return None
    return {int(item) for item in re.split(r'[, ]+', raw) if item}


def first_existing(paths: list[Path], label: str) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise RuntimeError(f'{label} not found in: {paths}')


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def natural_key(value: str):
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value))


def su_number(slug: str) -> int:
    suffix = slug.rsplit('_', 1)[-1]
    if not suffix.isdigit():
        raise RuntimeError(f'Unexpected SU tag slug: {slug!r}')
    return int(suffix)


def single_su_number(value: str) -> int | None:
    tags = [item for item in value.split(',') if item]
    if len(tags) != 1:
        return None
    return su_number(tags[0])


def pattern_sus() -> set[int]:
    rows = read_csv(first_existing(PATTERN_INPUT_PATHS, 'Madison elevation shuffle/leaf pattern CSV'))
    sus = set()
    for row in rows:
        su = single_su_number(row.get('su_tags') or '')
        if su is not None and row['status'] == 'ok':
            sus.add(su)
    targets = target_sus()
    return sus if targets is None else sus & targets


def racks_by_su(sus: set[int]) -> dict[int, list[Rack]]:
    grouped = defaultdict(list)
    racks = (
        Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__slug='nvl72_poweredgexe9712')
        .prefetch_related('tags')
        .order_by('name')
    )
    for rack in racks:
        tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith('nv_su_')]
        if len(tags) != 1:
            continue
        su = su_number(tags[0])
        if su in sus:
            grouped[su].append(rack)
    return {su: sorted(racks, key=lambda rack: natural_key(rack.name)) for su, racks in grouped.items()}


def selected_manifest_rows(racks: dict[int, list[Rack]]) -> list[dict[str, str]]:
    wanted_slots = {rack.name: su for su, su_racks in racks.items() for rack in su_racks}
    rows = [
        row for row in read_csv(first_existing(MANIFEST_PATHS, 'Madison device placement manifest'))
        if row['physical_slot'].upper() in wanted_slots
        and row['device_type_slug'] == TARGET_DEVICE_TYPE
    ]
    return sorted(rows, key=lambda row: (wanted_slots[row['physical_slot'].upper()], natural_key(row['physical_slot']), int(row['ru_bottom'])))


def device_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru = int(row['ru_bottom'])
    return f'mad1-{slot}-u{ru:02d}-{row["device_type_slug"]}'


def staged_comments(row: dict[str, str], su: int) -> str:
    return f"""\
{SOURCE_MARKER_BEGIN}
Source workbook row elevation placement for pattern-backed SU GB300 endpoint expansion.
- scalable_unit: {su}
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


def print_dry_run(sus: set[int], racks: dict[int, list[Rack]], rows: list[dict[str, str]]) -> None:
    by_su = Counter()
    by_slot = Counter()
    slot_to_su = {rack.name: su for su, su_racks in racks.items() for rack in su_racks}
    for row in rows:
        su = slot_to_su[row['physical_slot'].upper()]
        by_su[su] += 1
        by_slot[row['physical_slot'].upper()] += 1
    print('Madison pattern-backed SU GB300 compute tray dry run')
    print(f'target_sus={",".join(str(su) for su in sorted(sus))}')
    print(f'rack_count={sum(len(value) for value in racks.values())}')
    print(f'rows={len(rows)}')
    print(f'by_su={dict(sorted(by_su.items()))}')
    incomplete = {slot: count for slot, count in sorted(by_slot.items(), key=lambda item: natural_key(item[0])) if count != 18}
    print(f'incomplete_slots={incomplete}')
    for row in rows[:12]:
        print(f'  {device_name(row)} {row["physical_slot"]} U{row["ru_bottom"]} source={row["source_cell"]}')


def main() -> None:
    sus = pattern_sus()
    racks = racks_by_su(sus)
    rows = selected_manifest_rows(racks)
    print_dry_run(sus, racks, rows)
    if not apply_enabled():
        print('apply=false; set MADISON_PATTERN_SU_GB300_APPLY=1 to create/update GB300 trays for pattern-backed SUs.')
        return

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    device_type = DeviceType.objects.get(slug=TARGET_DEVICE_TYPE)
    role = DeviceRole.objects.get(slug=TARGET_DEVICE_TYPE)
    row_tags = {tag.slug: tag for tag in Tag.objects.filter(slug__startswith='nscale-row-id-')}
    su_tags = {su_number(tag.slug): tag for tag in Tag.objects.filter(slug__startswith='nv_su_')}
    slot_to_rack = {rack.name: rack for su_racks in racks.values() for rack in su_racks}
    slot_to_su = {rack.name: su for su, su_racks in racks.items() for rack in su_racks}
    counters = Counter()

    with transaction.atomic():
        for row in rows:
            rack = slot_to_rack.get(row['physical_slot'].upper())
            if rack is None:
                raise RuntimeError(f'No MAD-1 target rack found for slot {row["physical_slot"]}.')
            su = slot_to_su[rack.name]
            row_tag = row_tags.get(row['row_id_tag'].lower())
            su_tag = su_tags.get(su)
            if row_tag is None:
                raise RuntimeError(f'Missing row-id tag {row["row_id_tag"]}.')
            if su_tag is None:
                raise RuntimeError(f'Missing SU tag nv_su_{su}.')
            context = {
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
                    'scalable_units': [su],
                    'backend_planes': [],
                },
                'madison_pattern_su_gb300_compute_trays': True,
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
                'comments': staged_comments(row, su),
                'local_context_data': context,
            }
            device, created = Device.objects.get_or_create(site=rack.site, name=device_name(row), defaults=defaults)
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
            for tag in (row_tag, su_tag):
                if not device.tags.filter(pk=tag.pk).exists():
                    device.tags.add(tag)
                    counters['tags_added'] += 1

    print('Madison pattern-backed SU GB300 compute tray seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')


main()
