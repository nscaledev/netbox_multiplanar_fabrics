from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


DEVICE_MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
]
SHUFFLE_MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_shuffle_box_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_shuffle_box_placement_manifest.csv'),
]
OUTPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
]

BE_LEAF_PATTERN = re.compile(
    r'\bSU(?P<su>\d+)\s+BE\s+LEAF#(?P<leaf>\d+)\.NIC(?P<nic>\d+)(?P<side>[AB])\.PL(?P<plane>\d+)\b'
)
RACK_SOURCE_SU_PATTERN = re.compile(r'\bSU(?P<su>\d+)\s+BE\s+-\s+Leaf\b', re.IGNORECASE)


FIELDNAMES = [
    'status',
    'su_tags',
    'rack',
    'row_id_tags',
    'side',
    'nic_index_zero',
    'leaf_index_bottom_up',
    'gb300_osfp',
    'gb300_mpo',
    'planes',
    'leaf_devices',
    'leaf_ru_tops',
    'shuffle_18_box',
    'shuffle_18_ru',
    'shuffle_18_source_label',
    'shuffle_14_box',
    'shuffle_14_ru',
    'shuffle_14_source_label',
    'total_pair_cassettes',
    'inferred_leaf_cage_capacity',
    'inferred_spare_positions_per_pair',
    'notes',
    'selected_su',
    'selected_su_source',
    'rack_label_sus',
    'leaf_label_sus',
    'manifest_scalable_units',
    'leaf_source_cells',
    'shuffle_source_cells',
]


def first_existing(paths: list[Path], label: str) -> Path:
    for path in paths:
        if path.exists():
            return path
    raise RuntimeError(f'{label} not found in: {paths}')


def output_path() -> Path:
    for path in OUTPUT_PATHS:
        if path.parent.exists():
            return path
    OUTPUT_PATHS[-1].parent.mkdir(parents=True, exist_ok=True)
    return OUTPUT_PATHS[-1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def natural_key(value: str):
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value))


def parse_int_csv(value: str) -> list[int]:
    if not value:
        return []
    return [int(part) for part in value.split(',') if part]


def rack_source_su(value: str) -> int | None:
    match = RACK_SOURCE_SU_PATTERN.search(value or '')
    return int(match.group('su')) if match else None


def device_name(row: dict[str, str]) -> str:
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'gs001-{row["physical_slot"].lower()}-{ru}-{row["device_type_slug"]}'


def parse_leaf(row: dict[str, str]) -> dict[str, object] | None:
    match = BE_LEAF_PATTERN.search(row['source_label'] or '')
    if not match:
        return None
    return {
        'leaf': int(match.group('leaf')),
        'nic': int(match.group('nic')),
        'side': match.group('side'),
        'plane': int(match.group('plane')),
        'leaf_label_su': int(match.group('su')),
        'rack_label_su': rack_source_su(row['rack_source_label']),
        'manifest_sus': parse_int_csv(row.get('scalable_units', '')),
    }


def selected_su_for_leaf_items(leaf_items: list[tuple[dict[str, str], dict[str, object]]]) -> tuple[int | None, str]:
    rack_sus = sorted({item[1]['rack_label_su'] for item in leaf_items if item[1]['rack_label_su'] is not None})
    leaf_sus = sorted({item[1]['leaf_label_su'] for item in leaf_items})
    manifest_sus = sorted({su for item in leaf_items for su in item[1]['manifest_sus']})

    if len(rack_sus) == 1:
        return int(rack_sus[0]), 'rack_source_label'
    if len(manifest_sus) == 1:
        return int(manifest_sus[0]), 'manifest_scalable_units'
    if len(leaf_sus) == 1:
        return int(leaf_sus[0]), 'leaf_source_label'
    return None, 'unresolved'


def grouped_leaf_rows(rows: list[dict[str, str]]) -> dict[tuple[str, int, str], list[tuple[dict[str, str], dict[str, object]]]]:
    groups = defaultdict(list)
    for row in rows:
        if row['device_role_slug'] != 'be-leaf-switch':
            continue
        parsed = parse_leaf(row)
        if parsed is None:
            continue
        groups[(row['physical_slot'].upper(), int(parsed['nic']), str(parsed['side']))].append((row, parsed))
    return groups


def grouped_shuffle_rows(rows: list[dict[str, str]]) -> dict[tuple[str, int, str], list[dict[str, str]]]:
    groups = defaultdict(list)
    for row in rows:
        if not row['rack_source_label'].endswith('BE - Leaf'):
            continue
        if row['side'] not in {'A', 'B'} or not row['nic_index_zero']:
            continue
        groups[(row['physical_slot'].upper(), int(row['nic_index_zero']), row['side'])].append(row)
    return groups


def pattern_row(
    key: tuple[str, int, str],
    leaf_items: list[tuple[dict[str, str], dict[str, object]]],
    shuffle_items: list[dict[str, str]],
) -> dict[str, str]:
    slot, nic, side = key
    leaf_items = sorted(leaf_items, key=lambda item: int(item[1]['plane']))
    shuffle_items = sorted(shuffle_items, key=lambda item: int(item['ru_top']))
    planes = [int(item[1]['plane']) for item in leaf_items]
    leaf_numbers = sorted({int(item[1]['leaf']) for item in leaf_items})
    leaf_ru_tops = [int(item[0]['ru_top']) for item in leaf_items]
    rack_sus = sorted({int(item[1]['rack_label_su']) for item in leaf_items if item[1]['rack_label_su'] is not None})
    leaf_sus = sorted({int(item[1]['leaf_label_su']) for item in leaf_items})
    manifest_sus = sorted({su for item in leaf_items for su in item[1]['manifest_sus']})
    selected_su, selected_su_source = selected_su_for_leaf_items(leaf_items)
    expected_planes = [1, 2] if side == 'A' else [3, 4]

    boxes_by_count = {int(row['populated_cassettes']): row for row in shuffle_items if row['populated_cassettes']}
    box_18 = boxes_by_count.get(18)
    box_14 = boxes_by_count.get(14)
    statuses = []
    warnings = []

    if selected_su is None:
        statuses.append('unresolved_su')
    if len(leaf_items) != 2:
        statuses.append('expected_two_leaf_switches')
    if planes != expected_planes:
        statuses.append('unexpected_plane_pair')
    if leaf_numbers != [nic + 1]:
        statuses.append('leaf_index_does_not_match_nic_plus_one')
    if len(shuffle_items) != 2:
        statuses.append('expected_two_shuffle_boxes')
    if box_18 is None:
        statuses.append('missing_18_cassette_box')
    if box_14 is None:
        statuses.append('missing_14_cassette_box')
    if box_18 and box_14 and leaf_ru_tops:
        top_leaf_ru = max(leaf_ru_tops)
        if int(box_18['ru_top']) != top_leaf_ru + 1 or int(box_14['ru_top']) != top_leaf_ru + 2:
            statuses.append('shuffle_pair_not_directly_above_leaf_pair')

    if len(rack_sus) != 1:
        warnings.append(f'rack_label_sus={rack_sus}')
    if len(leaf_sus) != 1:
        warnings.append(f'leaf_label_sus={leaf_sus}')
    if len(manifest_sus) != 1:
        warnings.append(f'manifest_scalable_units={manifest_sus}')
    if selected_su is not None and leaf_sus and leaf_sus != [selected_su]:
        warnings.append(f'leaf_label_su_mismatch={leaf_sus}')
    if selected_su is not None and manifest_sus and manifest_sus != [selected_su]:
        warnings.append(f'manifest_su_mismatch={manifest_sus}')

    notes = [
        'Manifest-derived elevation-authoritative pattern: bottom-up leaf index maps to NIC index; '
        'side A uses GB300 MPO1 for planes 1/2, side B uses GB300 MPO2 for planes 3/4; '
        'lower 18-cassette box plus upper 14-cassette box form the 32-cassette leaf group.'
    ]
    if warnings:
        notes.append('Extraction warnings: ' + '; '.join(warnings))

    return {
        'status': ';'.join(statuses) or 'ok',
        'su_tags': f'nv_su_{selected_su}' if selected_su is not None else '',
        'rack': slot,
        'row_id_tags': (leaf_items[0][0]['row_id_tag'].lower() if leaf_items else ''),
        'side': side,
        'nic_index_zero': str(nic),
        'leaf_index_bottom_up': str(nic + 1),
        'gb300_osfp': f'osfp{nic + 1}',
        'gb300_mpo': '1' if side == 'A' else '2',
        'planes': ','.join(str(plane) for plane in planes),
        'leaf_devices': '|'.join(device_name(item[0]) for item in leaf_items),
        'leaf_ru_tops': ','.join(str(ru) for ru in leaf_ru_tops),
        'shuffle_18_box': box_18['box_name'] if box_18 else '',
        'shuffle_18_ru': box_18['ru_top'] if box_18 else '',
        'shuffle_18_source_label': box_18['source_label'] if box_18 else '',
        'shuffle_14_box': box_14['box_name'] if box_14 else '',
        'shuffle_14_ru': box_14['ru_top'] if box_14 else '',
        'shuffle_14_source_label': box_14['source_label'] if box_14 else '',
        'total_pair_cassettes': str((int(box_18['populated_cassettes']) if box_18 else 0) + (int(box_14['populated_cassettes']) if box_14 else 0)),
        'inferred_leaf_cage_capacity': '32',
        'inferred_spare_positions_per_pair': '2',
        'notes': ' '.join(notes),
        'selected_su': str(selected_su or ''),
        'selected_su_source': selected_su_source,
        'rack_label_sus': ','.join(str(su) for su in rack_sus),
        'leaf_label_sus': ','.join(str(su) for su in leaf_sus),
        'manifest_scalable_units': ','.join(str(su) for su in manifest_sus),
        'leaf_source_cells': '|'.join(item[0]['source_cell'] for item in leaf_items),
        'shuffle_source_cells': '|'.join(item['source_cell'] for item in shuffle_items),
    }


def main() -> None:
    device_path = first_existing(DEVICE_MANIFEST_PATHS, 'Madison device placement manifest')
    shuffle_path = first_existing(SHUFFLE_MANIFEST_PATHS, 'Madison shuffle box placement manifest')
    leaf_groups = grouped_leaf_rows(read_csv(device_path))
    shuffle_groups = grouped_shuffle_rows(read_csv(shuffle_path))
    keys = sorted(set(leaf_groups) | set(shuffle_groups), key=lambda key: (natural_key(key[0]), key[1], key[2]))
    rows = [pattern_row(key, leaf_groups.get(key, []), shuffle_groups.get(key, [])) for key in keys]

    path = output_path()
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    status_counts = Counter(row['status'] for row in rows)
    selected_su_counts = Counter(row['selected_su'] for row in rows if row['selected_su'])
    warning_rows = [row for row in rows if 'Extraction warnings:' in row['notes']]

    print('Madison manifest-derived elevation shuffle/leaf pattern report complete.')
    print(f'device_manifest={device_path}')
    print(f'shuffle_manifest={shuffle_path}')
    print(f'pattern_rows={len(rows)}')
    print(f'wrote_csv={path}')
    for status, count in sorted(status_counts.items()):
        print(f'{status}={count}')
    print('patterns_by_su=' + ','.join(f'SU{su}:{count}' for su, count in sorted(selected_su_counts.items(), key=lambda item: int(item[0]))))
    print(f'warning_rows={len(warning_rows)}')
    for row in warning_rows[:12]:
        print(
            f"warning {row['rack']} side={row['side']} nic={row['nic_index_zero']} "
            f"selected_su={row['selected_su']} source={row['selected_su_source']} "
            f"rack_label_sus={row['rack_label_sus']} leaf_label_sus={row['leaf_label_sus']} "
            f"manifest_scalable_units={row['manifest_scalable_units']}"
        )


main()
