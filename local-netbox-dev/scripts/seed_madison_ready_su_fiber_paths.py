from __future__ import annotations

import csv
import os
import re
from collections import Counter
from pathlib import Path
import sys

from django.db import transaction
from django.db.models import Q

from dcim.models import Device, Rack
from netbox_plant_graph.models import FiberSegment


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_v2_graph import (  # noqa: E402
    ACTIVE_MPO_LANE_INDEXES,
    delete_marked_connectivity,
    ensure_fabric,
    ensure_surfaces_for_devices,
    shuffle_group_mpos,
    source_position_plane_numbers_for_shuffle_front,
    stamp_path_segments,
)
from madison_nvl72_appliance import (  # noqa: E402
    device_effective_position_sort_key,
)


MAD_SITE_SLUG = 'gs001'
SOURCE_MARKER = 'madison_ready_su_fiber_policy_v2'
PATH_KEY = 'madison-ready-sus-leaf16-four-plane-lane-aware-v2'
SUPERSEDED_PATH_MARKERS = (
    'madison_graph_endpoint_test_subset_v2',
    'madison_first_nvl72_fiber_policy_v2',
    'madison_su1_fiber_policy_v2',
    SOURCE_MARKER,
)
OPTICAL_LANE_INDEXES = ACTIVE_MPO_LANE_INDEXES
EXPECTED_GB300_RACKS_PER_SU = 7
EXPECTED_GB300_TRAYS_PER_RACK = 18
EXPECTED_PATTERN_ROWS_PER_SU = 8

PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns_from_manifests.csv'),
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_READY_SU_FIBER_APPLY') == '1'


def target_sus() -> set[int] | None:
    raw = os.environ.get('MADISON_TARGET_SUS', '').strip()
    if not raw:
        return None
    return {int(item) for item in re.split(r'[, ]+', raw) if item}


def pattern_input_path() -> Path:
    for path in PATTERN_INPUT_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison elevation shuffle/leaf pattern CSV not found in: {PATTERN_INPUT_PATHS}')


def natural_key(value: str):
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value))


def su_number(slug: str) -> int:
    suffix = slug.rsplit('_', 1)[-1]
    if not suffix.isdigit():
        raise RuntimeError(f'Unexpected SU tag slug: {slug!r}')
    return int(suffix)


def single_su_number(value: str) -> int:
    tags = [item for item in value.split(',') if item]
    if len(tags) != 1:
        raise RuntimeError(f'Expected exactly one SU tag, got {value!r}.')
    return su_number(tags[0])


def read_pattern_rows() -> list[dict[str, str]]:
    with pattern_input_path().open(newline='') as handle:
        rows = [row for row in csv.DictReader(handle)]
    rows = [row for row in rows if row['status'] == 'ok']
    targets = target_sus()
    if targets is not None:
        rows = [row for row in rows if single_su_number(row['su_tags']) in targets]
    return sorted(rows, key=lambda row: (single_su_number(row['su_tags']), row['side'], int(row['nic_index_zero'])))


def su_racks() -> dict[int, list[Rack]]:
    grouped: dict[int, list[Rack]] = {}
    for rack in (
        Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__slug='nvl72_poweredgexe9712')
        .prefetch_related('tags')
        .order_by('name')
    ):
        tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith('nv_su_')]
        if len(tags) == 1:
            grouped.setdefault(su_number(tags[0]), []).append(rack)
    return {su: sorted(racks, key=lambda rack: natural_key(rack.name)) for su, racks in grouped.items()}


def gb300_trays_by_rack(rack_ids: list[int]) -> dict[int, list[Device]]:
    grouped: dict[int, list[Device]] = {}
    for device in (
        Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug='gb300ct')
        .filter(Q(rack_id__in=rack_ids) | Q(parent_bay__device__rack_id__in=rack_ids))
        .select_related('rack', 'parent_bay__device__rack')
        .order_by('name')
    ):
        parent_device = getattr(getattr(device, 'parent_bay', None), 'device', None)
        rack_id = device.rack_id or (parent_device.rack_id if parent_device is not None else None)
        if rack_id is None:
            continue
        grouped.setdefault(rack_id, []).append(device)
    return grouped


def complete_su_numbers(pattern_rows: list[dict[str, str]], racks_by_su: dict[int, list[Rack]], trays_by_rack: dict[int, list[Device]]) -> tuple[list[int], Counter, list[str]]:
    counters = Counter()
    blockers = []
    pattern_count_by_su = Counter(single_su_number(row['su_tags']) for row in pattern_rows)
    complete = []
    for su in sorted(pattern_count_by_su):
        racks = racks_by_su.get(su, [])
        if pattern_count_by_su[su] != EXPECTED_PATTERN_ROWS_PER_SU:
            blockers.append(f'SU{su}: pattern_rows={pattern_count_by_su[su]} expected={EXPECTED_PATTERN_ROWS_PER_SU}')
            counters['blocked_sus'] += 1
            continue
        if len(racks) != EXPECTED_GB300_RACKS_PER_SU:
            blockers.append(f'SU{su}: nvl72_racks={len(racks)} expected={EXPECTED_GB300_RACKS_PER_SU}')
            counters['blocked_sus'] += 1
            continue
        incomplete = [rack.name for rack in racks if len(trays_by_rack.get(rack.pk, [])) != EXPECTED_GB300_TRAYS_PER_RACK]
        if incomplete:
            blockers.append(f'SU{su}: incomplete_gb300_tray_racks={",".join(incomplete)}')
            counters['blocked_sus'] += 1
            continue
        complete.append(su)
        counters['ready_sus'] += 1
    return complete, counters, blockers


def tray_sort_key(device: Device) -> tuple[int, str]:
    position, name = device_effective_position_sort_key(device)
    return (int(position or 0), name)


def gb300_rows_for_su(su: int, racks_by_su: dict[int, list[Rack]], trays_by_rack: dict[int, list[Device]]) -> list[dict]:
    rows = []
    for rack_index, rack in enumerate(racks_by_su[su], start=1):
        trays = sorted(trays_by_rack.get(rack.pk, []), key=tray_sort_key)
        if len(trays) != EXPECTED_GB300_TRAYS_PER_RACK:
            raise RuntimeError(f'SU{su} rack {rack.name} has {len(trays)} GB300 trays; expected {EXPECTED_GB300_TRAYS_PER_RACK}.')
        for tray_index, tray in enumerate(trays, start=1):
            rows.append({'rack_index': rack_index, 'rack': rack, 'tray_index': tray_index, 'device': tray})
    return rows


def cassettes_for_box(box_name: str) -> list[Device]:
    pattern = re.compile(r'-cassette-(?P<tray>\d+)\.(?P<slot>\d+)$')
    cassettes = list(
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            name__startswith=f'{box_name}-cassette-',
            device_type__slug='shuffle-cassette-2x2-mpo',
        ).order_by('name')
    )
    if not cassettes:
        raise RuntimeError(f'No cassette devices found for shuffle box {box_name}.')
    return sorted(
        cassettes,
        key=lambda device: (
            int(pattern.search(device.name).group('tray')),
            int(pattern.search(device.name).group('slot')),
        ),
    )


def cassette_mpo_sequence(pattern_row: dict[str, str]) -> list[dict]:
    cassettes = [
        *cassettes_for_box(pattern_row['shuffle_18_box']),
        *cassettes_for_box(pattern_row['shuffle_14_box']),
    ]
    if len(cassettes) != int(pattern_row['total_pair_cassettes']):
        raise RuntimeError(f'Unexpected cassette count for row {pattern_row}.')
    rows = []
    for cassette_index, cassette in enumerate(cassettes, start=1):
        for mpo in range(1, 5):
            rows.append(
                {
                    'cassette_index': cassette_index,
                    'mpo': mpo,
                    'device': cassette,
                    'front': (cassette.name, f'front-mpo-{mpo:02d}', 1),
                    'rear': (cassette.name, f'rear-mpo-{mpo:02d}', 1),
                }
            )
    if len(rows) != 128:
        raise RuntimeError(f'Expected 128 cassette MPO positions for row {pattern_row}; found {len(rows)}.')
    return rows


def leaf_endpoint_sequence(pattern_row: dict[str, str]) -> list[dict]:
    planes = [int(value) for value in pattern_row['planes'].split(',')]
    leaf_names = pattern_row['leaf_devices'].split('|')
    if len(planes) != 2 or len(leaf_names) != 2:
        raise RuntimeError(f'Unexpected plane/leaf pair in pattern row: {pattern_row}')
    missing = [
        name for name in leaf_names
        if not Device.objects.filter(site__slug=MAD_SITE_SLUG, name=name, device_type__slug='sn5610').exists()
    ]
    if missing:
        raise RuntimeError(f'Missing leaf device(s): {missing}')
    rows = []
    for cage in range(1, 33):
        rows.extend(
            [
                {'plane': planes[0], 'device_name': leaf_names[0], 'interface': f'swp{cage}', 'mpo': 1},
                {'plane': planes[1], 'device_name': leaf_names[1], 'interface': f'swp{cage}', 'mpo': 1},
                {'plane': planes[0], 'device_name': leaf_names[0], 'interface': f'swp{cage}', 'mpo': 2},
                {'plane': planes[1], 'device_name': leaf_names[1], 'interface': f'swp{cage}', 'mpo': 2},
            ]
        )
    return rows


def endpoint(device_name: str, termination: str, ordinal: int) -> tuple[str, str, int]:
    return (device_name, termination, ordinal)


def plane_by_mpo_for_cassette(rows_by_cassette_mpo: dict[tuple[str, int], dict], cassette_name: str, mpo: int) -> int | None:
    row = rows_by_cassette_mpo.get((cassette_name, mpo))
    if row is None:
        return None
    return int(row['leaf_row']['plane'])


def source_position_plane_numbers(
    *,
    rows_by_cassette_mpo: dict[tuple[str, int], dict],
    cassette_row: dict,
    leaf_row: dict,
) -> dict[int, int]:
    cassette_name = cassette_row['device'].name
    front_mpo = int(cassette_row['mpo'])
    group_first_mpo, group_second_mpo = shuffle_group_mpos(front_mpo)
    first_plane = plane_by_mpo_for_cassette(rows_by_cassette_mpo, cassette_name, group_first_mpo)
    second_plane = plane_by_mpo_for_cassette(rows_by_cassette_mpo, cassette_name, group_second_mpo)
    if first_plane is None:
        first_plane = int(leaf_row['plane'])
    if second_plane is None:
        second_plane = int(leaf_row['plane'])
    return source_position_plane_numbers_for_shuffle_front(
        front_mpo=front_mpo,
        first_plane=first_plane,
        second_plane=second_plane,
    )


def path_segments(pattern_rows: list[dict[str, str]], gb_rows_by_su: dict[int, list[dict]]) -> list[dict]:
    segments = []
    for pattern_row in pattern_rows:
        su = single_su_number(pattern_row['su_tags'])
        gb_mpo = int(pattern_row['gb300_mpo'])
        osfp_name = pattern_row['gb300_osfp']
        cassette_rows = cassette_mpo_sequence(pattern_row)
        leaf_rows = leaf_endpoint_sequence(pattern_row)
        sequence_rows = [
            {
                'gb_row': gb_row,
                'cassette_row': cassette_row,
                'leaf_row': leaf_row,
                'sequence_index': sequence_index,
            }
            for sequence_index, (gb_row, cassette_row, leaf_row) in enumerate(
                zip(gb_rows_by_su[su], cassette_rows[:126], leaf_rows[:126], strict=True),
                start=1,
            )
        ]
        rows_by_cassette_mpo = {
            (row['cassette_row']['device'].name, int(row['cassette_row']['mpo'])): row
            for row in sequence_rows
        }
        for row in sequence_rows:
            sequence_index = row['sequence_index']
            gb_row = row['gb_row']
            cassette_row = row['cassette_row']
            leaf_row = row['leaf_row']
            source_plane_map = source_position_plane_numbers(
                rows_by_cassette_mpo=rows_by_cassette_mpo,
                cassette_row=cassette_row,
                leaf_row=leaf_row,
            )
            common = {
                'lane_indexes': OPTICAL_LANE_INDEXES,
                'plane_membership_granularity': 'optical_lane',
                'cable_profile_name': 'madison-mpo8-smf-patch-optical-lanes',
            }
            metadata = {
                SOURCE_MARKER: True,
                'modeled_status': 'planned',
                'source_authority': 'Madison rack elevation worksheets',
                'architecture_reference': 'ROCE 4-plane Shuffle Cabling Patterns',
                'policy': PATH_KEY,
                'su': su,
                'su_tag': pattern_row['su_tags'],
                'row_id_tags': pattern_row['row_id_tags'],
                'be_rack': pattern_row['rack'],
                'side': pattern_row['side'],
                'nic_index_zero': int(pattern_row['nic_index_zero']),
                'leaf_index_bottom_up': int(pattern_row['leaf_index_bottom_up']),
                'sequence_index': sequence_index,
                'gb300_rack_index': gb_row['rack_index'],
                'gb300_rack': gb_row['rack'].name,
                'gb300_tray_index': gb_row['tray_index'],
                'gb300_device': gb_row['device'].name,
                'gb300_interface': osfp_name,
                'gb300_mpo': gb_mpo,
                'leaf_device': leaf_row['device_name'],
                'leaf_interface': leaf_row['interface'],
                'leaf_mpo': leaf_row['mpo'],
                'shuffle_18_box': pattern_row['shuffle_18_box'],
                'shuffle_14_box': pattern_row['shuffle_14_box'],
                'shuffle_cassette': cassette_row['device'].name,
                'shuffle_cassette_index_in_pair': cassette_row['cassette_index'],
                'shuffle_mpo': cassette_row['mpo'],
                'lane_indexes': list(OPTICAL_LANE_INDEXES),
                'optical_lane_semantics': 'single_fiber_strand',
            }
            segments.extend(
                [
                    {
                        **common,
                        'a': endpoint(gb_row['device'].name, osfp_name, gb_mpo),
                        'b': cassette_row['front'],
                        'segment_role': 'gb300_to_shuffle_mpo12_active_lanes',
                        'metadata': {
                            **metadata,
                            'segment_kind': 'gb300_to_shuffle',
                            'position_plane_numbers': source_plane_map,
                            'plane_assignment': 'per_position_from_2x2_shuffle_group',
                        },
                    },
                    {
                        **common,
                        'plane_number': int(leaf_row['plane']),
                        'a': cassette_row['rear'],
                        'b': endpoint(leaf_row['device_name'], leaf_row['interface'], leaf_row['mpo']),
                        'segment_role': 'shuffle_to_leaf_mpo12_active_lanes',
                        'metadata': {**metadata, 'segment_kind': 'shuffle_to_leaf'},
                    },
                ]
            )
    return segments


def print_dry_run(pattern_rows: list[dict[str, str]], ready_sus: list[int], segments: list[dict], blockers: list[str]) -> None:
    by_su = Counter(single_su_number(row['su_tags']) for row in pattern_rows if single_su_number(row['su_tags']) in ready_sus)
    print('Madison ready-SU fiber policy dry run')
    print(f'path_key={PATH_KEY}')
    print(f'ready_sus={",".join(str(su) for su in ready_sus)}')
    print(f'pattern_rows={len(pattern_rows)}')
    print(f'pattern_rows_by_ready_su={dict(sorted(by_su.items()))}')
    print(f'external_segments={len(segments)}')
    print(f'expected_segment_optical_lane_hops={sum(len(segment["lane_indexes"]) for segment in segments)}')
    print(f'expected_end_to_end_optical_lane_paths={sum(len(segment["lane_indexes"]) for segment in segments) // 2}')
    print(f'blocked_su_findings={len(blockers)}')
    for blocker in blockers[:12]:
        print(f'  blocker: {blocker}')
    for segment in segments[:8]:
        plane_label = segment.get('plane_number') or segment['metadata'].get('position_plane_numbers')
        print(
            f"  sample su={segment['metadata']['su']} plane={plane_label} "
            f"{segment['a']} -> {segment['b']}"
        )


def main() -> None:
    fabric = ensure_fabric()
    pattern_rows = read_pattern_rows()
    racks_by_su = su_racks()
    trays_by_rack = gb300_trays_by_rack([rack.pk for racks in racks_by_su.values() for rack in racks])
    ready_sus, counters, blockers = complete_su_numbers(pattern_rows, racks_by_su, trays_by_rack)
    ready_patterns = [row for row in pattern_rows if single_su_number(row['su_tags']) in ready_sus]
    gb_rows_by_su = {su: gb300_rows_for_su(su, racks_by_su, trays_by_rack) for su in ready_sus}
    segments = path_segments(ready_patterns, gb_rows_by_su)
    print_dry_run(ready_patterns, ready_sus, segments, blockers)
    if not apply_enabled():
        print('apply=false; set MADISON_READY_SU_FIBER_APPLY=1 to stamp ready-SU fiber paths.')
        return

    with transaction.atomic():
        devices = set()
        for row in gb_rows_by_su.values():
            devices.update(item['device'] for item in row)
        for segment in segments:
            for device_name, _port_name, _mpo in (segment['a'], segment['b']):
                device = Device.objects.filter(site__slug=MAD_SITE_SLUG, name=device_name).first()
                if device is not None:
                    devices.add(device)
        ensure_surfaces_for_devices(fabric, sorted(devices, key=lambda device: device.name), marker=SOURCE_MARKER, counters=counters)
        for marker in SUPERSEDED_PATH_MARKERS:
            delete_marked_connectivity(marker, fabric=fabric, counters=counters)
        counters.update(stamp_path_segments(fabric, marker=SOURCE_MARKER, path_key=PATH_KEY, segments=segments))

    print('Madison ready-SU fiber policy seed complete for netbox_plant_graph v2.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_fiber_segments={FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).count()}')


main()
