from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from dcim.models import Device, Rack


MAD_SITE_SLUG = 'gs001'
EXPECTED_NVL72_RACKS_PER_SU = 7
EXPECTED_GB300_TRAYS_PER_RACK = 18
EXPECTED_PATTERN_ROWS_PER_SU = 8
EXPECTED_LEAF_DEVICES_PER_SU = 16
EXPECTED_CASSETTES_PER_PATTERN = 32
EXPECTED_SEGMENTS_PER_PATTERN = 252
EXPECTED_OPTICAL_LANE_PATHS_PER_PATTERN = 1008

PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]
OUTPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_su_fiber_readiness.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_su_fiber_readiness.csv'),
]


def pattern_input_path() -> Path:
    for path in PATTERN_INPUT_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison elevation shuffle/leaf pattern CSV not found in: {PATTERN_INPUT_PATHS}')


def output_path() -> Path:
    for path in OUTPUT_PATHS:
        if path.parent.exists():
            return path
    OUTPUT_PATHS[-1].parent.mkdir(parents=True, exist_ok=True)
    return OUTPUT_PATHS[-1]


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


def read_pattern_rows() -> list[dict[str, str]]:
    with pattern_input_path().open(newline='') as handle:
        return [row for row in csv.DictReader(handle)]


def grouped_pattern_rows(rows: list[dict[str, str]]) -> dict[int, list[dict[str, str]]]:
    grouped = defaultdict(list)
    for row in rows:
        su = single_su_number(row.get('su_tags') or '')
        if su is not None:
            grouped[su].append(row)
    return grouped


def su_racks() -> dict[int, list[Rack]]:
    grouped = defaultdict(list)
    racks = (
        Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__slug='nvl72_poweredgexe9712')
        .prefetch_related('tags')
        .order_by('name')
    )
    for rack in racks:
        tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith('nv_su_')]
        if len(tags) == 1:
            grouped[su_number(tags[0])].append(rack)
    return {su: sorted(racks, key=lambda rack: natural_key(rack.name)) for su, racks in grouped.items()}


def gb300_trays_by_rack(racks_by_su: dict[int, list[Rack]]) -> dict[int, list[Device]]:
    rack_ids = [rack.pk for racks in racks_by_su.values() for rack in racks]
    grouped = defaultdict(list)
    for device in (
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            rack_id__in=rack_ids,
            device_type__slug='gb300ct',
        )
        .select_related('rack')
        .order_by('rack__name', 'position', 'name')
    ):
        grouped[device.rack_id].append(device)
    return grouped


def cassettes_for_box(box_name: str) -> list[Device]:
    return list(
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            name__startswith=f'{box_name}-cassette-',
            device_type__slug='shuffle-cassette-2x2-mpo',
        )
        .select_related('parent_bay__device')
        .order_by('name')
    )


def status_for_su(
    su: int,
    rows: list[dict[str, str]],
    racks_by_su: dict[int, list[Rack]],
    trays_by_rack: dict[int, list[Device]],
) -> dict[str, object]:
    counters = Counter()
    blockers = []
    warnings = []
    racks = racks_by_su.get(su, [])

    ok_pattern_rows = [row for row in rows if row['status'] == 'ok']
    if len(rows) != EXPECTED_PATTERN_ROWS_PER_SU:
        blockers.append(f'pattern_rows={len(rows)} expected={EXPECTED_PATTERN_ROWS_PER_SU}')
    if len(ok_pattern_rows) != len(rows):
        blockers.append(f'non_ok_pattern_rows={len(rows) - len(ok_pattern_rows)}')

    if len(racks) != EXPECTED_NVL72_RACKS_PER_SU:
        blockers.append(f'nvl72_racks={len(racks)} expected={EXPECTED_NVL72_RACKS_PER_SU}')

    incomplete_racks = []
    for rack in racks:
        tray_count = len(trays_by_rack.get(rack.pk, []))
        counters['gb300_trays'] += tray_count
        if tray_count != EXPECTED_GB300_TRAYS_PER_RACK:
            incomplete_racks.append(f'{rack.name}:{tray_count}')
    if incomplete_racks:
        blockers.append(f'gb300_trays_per_rack_incomplete={";".join(incomplete_racks)}')

    leaf_names = sorted({name for row in ok_pattern_rows for name in row['leaf_devices'].split('|') if name})
    existing_leaf_names = set(
        Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=leaf_names, device_type__slug='sn5610').values_list('name', flat=True)
    )
    missing_leaf_names = [name for name in leaf_names if name not in existing_leaf_names]
    if len(leaf_names) != EXPECTED_LEAF_DEVICES_PER_SU:
        blockers.append(f'leaf_names_in_patterns={len(leaf_names)} expected={EXPECTED_LEAF_DEVICES_PER_SU}')
    if missing_leaf_names:
        blockers.append(f'missing_leaf_devices={len(missing_leaf_names)}')

    missing_boxes = []
    incomplete_boxes = []
    for row in ok_pattern_rows:
        for box_field, expected_count in (('shuffle_18_box', 18), ('shuffle_14_box', 14)):
            box_name = row[box_field]
            box = Device.objects.filter(site__slug=MAD_SITE_SLUG, name=box_name, device_type__slug='sb').first()
            if box is None:
                missing_boxes.append(box_name)
                continue
            cassette_count = len(cassettes_for_box(box_name))
            counters['shuffle_cassettes'] += cassette_count
            if cassette_count != expected_count:
                incomplete_boxes.append(f'{box_name}:{cassette_count}/{expected_count}')
    if missing_boxes:
        blockers.append(f'missing_shuffle_boxes={len(missing_boxes)}')
    if incomplete_boxes:
        blockers.append(f'incomplete_shuffle_boxes={";".join(incomplete_boxes[:8])}')

    expected_segments = len(ok_pattern_rows) * EXPECTED_SEGMENTS_PER_PATTERN
    expected_paths = len(ok_pattern_rows) * EXPECTED_OPTICAL_LANE_PATHS_PER_PATTERN
    status = 'ready' if not blockers else 'blocked'
    if status == 'ready' and counters['shuffle_cassettes'] != EXPECTED_CASSETTES_PER_PATTERN * EXPECTED_PATTERN_ROWS_PER_SU:
        warnings.append(
            f'shuffle_cassettes={counters["shuffle_cassettes"]} '
            f'expected={EXPECTED_CASSETTES_PER_PATTERN * EXPECTED_PATTERN_ROWS_PER_SU}'
        )

    return {
        'su': su,
        'status': status,
        'pattern_rows': len(rows),
        'ok_pattern_rows': len(ok_pattern_rows),
        'nvl72_racks': len(racks),
        'gb300_trays': counters['gb300_trays'],
        'leaf_devices_in_patterns': len(leaf_names),
        'leaf_devices_existing': len(existing_leaf_names),
        'shuffle_cassettes': counters['shuffle_cassettes'],
        'expected_fiber_segments': expected_segments,
        'expected_optical_lane_paths': expected_paths,
        'blockers': ' | '.join(blockers),
        'warnings': ' | '.join(warnings),
    }


def write_report(rows: list[dict[str, object]]) -> Path:
    path = output_path()
    fieldnames = [
        'su',
        'status',
        'pattern_rows',
        'ok_pattern_rows',
        'nvl72_racks',
        'gb300_trays',
        'leaf_devices_in_patterns',
        'leaf_devices_existing',
        'shuffle_cassettes',
        'expected_fiber_segments',
        'expected_optical_lane_paths',
        'blockers',
        'warnings',
    ]
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    pattern_rows = read_pattern_rows()
    grouped_patterns = grouped_pattern_rows(pattern_rows)
    racks_by_su = su_racks()
    trays_by_rack = gb300_trays_by_rack(racks_by_su)
    su_numbers = sorted(set(grouped_patterns) | set(racks_by_su))
    rows = [
        status_for_su(su, grouped_patterns.get(su, []), racks_by_su, trays_by_rack)
        for su in su_numbers
    ]
    path = write_report(rows)
    counters = Counter(row['status'] for row in rows)

    print('Madison SU fiber readiness report')
    print(f'pattern_rows={len(pattern_rows)}')
    print(f'sus_seen={len(rows)}')
    print(f'ready_sus={counters["ready"]}')
    print(f'blocked_sus={counters["blocked"]}')
    print(f'wrote_csv={path}')
    for row in rows:
        print(
            f"SU{row['su']}: {row['status']} "
            f"racks={row['nvl72_racks']} gb300_trays={row['gb300_trays']} "
            f"leaves={row['leaf_devices_existing']}/{row['leaf_devices_in_patterns']} "
            f"shuffle_cassettes={row['shuffle_cassettes']} "
            f"segments={row['expected_fiber_segments']} paths={row['expected_optical_lane_paths']}"
        )
        if row['blockers']:
            print(f"  blockers: {row['blockers']}")
        if row['warnings']:
            print(f"  warnings: {row['warnings']}")


main()
