from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from dcim.models import Device


MAD_SITE_SLUG = 'mad-1'
OUTPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]
BE_LEAF_PATTERN = re.compile(r'\bBE LEAF#(?P<leaf>\d+)\.NIC(?P<nic>\d+)(?P<side>[AB])\.PL(?P<plane>\d+)\b')


def output_path() -> Path:
    for path in OUTPUT_PATHS:
        if path.parent.exists():
            return path
    OUTPUT_PATHS[-1].parent.mkdir(parents=True, exist_ok=True)
    return OUTPUT_PATHS[-1]


def su_tags(device: Device) -> list[str]:
    return sorted(tag.slug for tag in device.tags.all() if tag.slug.startswith('nv_su_'))


def row_id_tags(device: Device) -> list[str]:
    return sorted(tag.slug for tag in device.tags.all() if tag.slug.startswith('nscale-row-id-'))


def device_ru_top(device: Device) -> int | None:
    workbook = (device.local_context_data or {}).get('madison_workbook') or {}
    if workbook.get('ru_top') is not None:
        return int(workbook['ru_top'])
    if device.position is not None:
        return int(device.position)
    return None


def parse_leaf(device: Device) -> dict | None:
    source = (device.local_context_data.get('madison_workbook') or {}).get('source_label') or device.description
    match = BE_LEAF_PATTERN.search(source or '')
    if not match:
        return None
    return {
        'leaf': int(match.group('leaf')),
        'nic': int(match.group('nic')),
        'side': match.group('side'),
        'plane': int(match.group('plane')),
        'source_label': source,
    }


def parse_shuffle_box(device: Device) -> dict:
    context = device.local_context_data or {}
    metadata = (
        context.get('madison_shuffle_flattened_containment_v1')
        or context.get('madison_shuffle_elevation_migration_v1')
        or {}
    )
    return {
        'nic': metadata.get('nic_index_zero'),
        'side': metadata.get('side') or '',
        'logical_shuffle_box': metadata.get('logical_shuffle_box'),
        'populated_cassettes': metadata.get('populated_cassettes'),
        'source_label': metadata.get('source_label') or device.description,
        'ru': metadata.get('ru') or int(device.position or 0),
    }


def main() -> None:
    leaf_groups = defaultdict(list)
    for device in (
        Device.objects.filter(site__slug=MAD_SITE_SLUG, role__slug='be-leaf-switch')
        .select_related('rack')
        .prefetch_related('tags')
        .order_by('rack__name', 'position', 'name')
    ):
        parsed = parse_leaf(device)
        if not parsed:
            continue
        leaf_groups[(device.rack_id, parsed['nic'], parsed['side'])].append((device, parsed))

    box_groups = defaultdict(list)
    for device in (
        Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug='shuffle-box-3tray-18cassette')
        .select_related('rack')
        .order_by('rack__name', 'position', 'name')
    ):
        parsed = parse_shuffle_box(device)
        if parsed['nic'] is None or parsed['side'] not in {'A', 'B'}:
            continue
        box_groups[(device.rack_id, int(parsed['nic']), parsed['side'])].append((device, parsed))

    rows = []
    for key, leaf_items in sorted(
        leaf_groups.items(),
        key=lambda item: (item[1][0][0].rack.name if item[1][0][0].rack else '', item[0][1], item[0][2]),
    ):
        rack_id, nic, side = key
        leaf_items = sorted(leaf_items, key=lambda item: item[1]['plane'])
        first_device = leaf_items[0][0]
        rack = first_device.rack
        boxes = sorted(box_groups.get(key, []), key=lambda item: item[1]['ru'])
        planes = [item[1]['plane'] for item in leaf_items]
        leaf_numbers = sorted({item[1]['leaf'] for item in leaf_items})
        leaf_ru_tops = [device_ru_top(item[0]) for item in leaf_items]
        expected_planes = [1, 2] if side == 'A' else [3, 4]

        boxes_by_count = {item[1]['populated_cassettes']: item for item in boxes}
        box_18 = boxes_by_count.get(18)
        box_14 = boxes_by_count.get(14)
        statuses = []
        if len(leaf_items) != 2:
            statuses.append('expected_two_leaf_switches')
        if planes != expected_planes:
            statuses.append('unexpected_plane_pair')
        if leaf_numbers != [nic + 1]:
            statuses.append('leaf_index_does_not_match_nic_plus_one')
        if len(boxes) != 2:
            statuses.append('expected_two_shuffle_boxes')
        if box_18 is None:
            statuses.append('missing_18_cassette_box')
        if box_14 is None:
            statuses.append('missing_14_cassette_box')
        if box_18 and box_14 and leaf_ru_tops and all(ru is not None for ru in leaf_ru_tops):
            top_leaf_ru = max(leaf_ru_tops)
            if int(box_18[1]['ru']) != top_leaf_ru + 1 or int(box_14[1]['ru']) != top_leaf_ru + 2:
                statuses.append('shuffle_pair_not_directly_above_leaf_pair')

        rows.append(
            {
                'status': ';'.join(statuses) or 'ok',
                'su_tags': ','.join(su_tags(first_device)),
                'rack': rack.name if rack else '',
                'row_id_tags': ','.join(row_id_tags(rack) if rack else []),
                'side': side,
                'nic_index_zero': nic,
                'leaf_index_bottom_up': nic + 1,
                'gb300_osfp': f'osfp{nic + 1}',
                'gb300_mpo': 1 if side == 'A' else 2,
                'planes': ','.join(str(plane) for plane in planes),
                'leaf_devices': '|'.join(item[0].name for item in leaf_items),
                'leaf_ru_tops': ','.join(str(ru) for ru in leaf_ru_tops),
                'shuffle_18_box': box_18[0].name if box_18 else '',
                'shuffle_18_ru': box_18[1]['ru'] if box_18 else '',
                'shuffle_18_source_label': box_18[1]['source_label'] if box_18 else '',
                'shuffle_14_box': box_14[0].name if box_14 else '',
                'shuffle_14_ru': box_14[1]['ru'] if box_14 else '',
                'shuffle_14_source_label': box_14[1]['source_label'] if box_14 else '',
                'total_pair_cassettes': (int(box_18[1]['populated_cassettes']) if box_18 else 0) + (int(box_14[1]['populated_cassettes']) if box_14 else 0),
                'inferred_leaf_cage_capacity': 32,
                'inferred_spare_positions_per_pair': 2,
                'notes': (
                    'Elevation-authoritative pattern: bottom-up leaf index maps to NIC index; '
                    'side A uses GB300 MPO1 for planes 1/2, side B uses GB300 MPO2 for planes 3/4; '
                    'lower 18-cassette box plus upper 14-cassette box form the 32-cassette leaf group.'
                ),
            }
        )

    path = output_path()
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ['status'])
        writer.writeheader()
        writer.writerows(rows)

    status_counts = defaultdict(int)
    for row in rows:
        status_counts[row['status']] += 1

    print('Madison elevation shuffle/leaf pattern report complete.')
    print(f'pattern_rows={len(rows)}')
    print(f'wrote_csv={path}')
    for status, count in sorted(status_counts.items()):
        print(f'{status}={count}')


main()
