from __future__ import annotations

import csv
import os
import re
from collections import Counter
from pathlib import Path
import sys

from django.db import transaction

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


MAD_SITE_SLUG = 'gs001'
TARGET_SU_TAG = 'nv_su_1'
TARGET_ROW_ID_TAGS = {'nscale-row-id-a9', 'nscale-row-id-a10'}
SOURCE_MARKER = 'madison_su1_fiber_policy_v2'
SUPERSEDED_PATH_MARKERS = (
    'madison_graph_endpoint_test_subset_v2',
    'madison_first_nvl72_fiber_policy_v2',
)
PATH_KEY = 'madison-su1-leaf16-four-plane-lane-aware-v2'
OPTICAL_LANE_INDEXES = ACTIVE_MPO_LANE_INDEXES

PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_SU1_FIBER_APPLY') == '1'


def pattern_input_path() -> Path:
    for path in PATTERN_INPUT_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison elevation shuffle/leaf pattern CSV not found in: {PATTERN_INPUT_PATHS}')


def natural_key(value: str):
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value))


def read_pattern_rows() -> list[dict[str, str]]:
    with pattern_input_path().open(newline='') as handle:
        rows = [row for row in csv.DictReader(handle)]
    rows = [
        row for row in rows
        if row['status'] == 'ok'
        and row['su_tags'] == TARGET_SU_TAG
        and row['row_id_tags'] in TARGET_ROW_ID_TAGS
        and row['side'] in {'A', 'B'}
    ]
    if len(rows) != 8:
        raise RuntimeError(f'Expected 8 SU1 A/B shuffle leaf pattern rows; found {len(rows)}.')
    return sorted(rows, key=lambda row: (row['side'], int(row['nic_index_zero'])))


def tray_sort_key(device: Device) -> tuple[int, str]:
    return (int(device.position or 0), device.name)


def su1_gb300_racks() -> list[Rack]:
    racks = list(
        Rack.objects.filter(
            site__slug=MAD_SITE_SLUG,
            role__slug='nvl72_poweredgexe9712',
            tags__slug=TARGET_SU_TAG,
        )
        .prefetch_related('tags')
        .order_by('name')
    )
    racks = sorted(racks, key=lambda rack: natural_key(rack.name))
    if len(racks) != 7:
        raise RuntimeError(f'Expected 7 SU1 NVL72 racks; found {len(racks)}: {[rack.name for rack in racks]}')
    return racks


def su1_gb300_trays(racks: list[Rack]) -> list[Device]:
    rows = []
    for rack in racks:
        trays = list(
            Device.objects.filter(
                site__slug=MAD_SITE_SLUG,
                rack=rack,
                device_type__slug='gb300ct',
                local_context_data__madison_active_endpoint_test_subset=True,
            )
            .select_related('rack')
            .order_by('position', 'name')
        )
        if len(trays) != 18:
            raise RuntimeError(f'Expected 18 GB300 compute trays in rack {rack.name}; found {len(trays)}.')
        for rack_index, tray in enumerate(sorted(trays, key=tray_sort_key), start=1):
            rows.append(
                {
                    'rack': rack,
                    'rack_index': racks.index(rack) + 1,
                    'tray_index': rack_index,
                    'device': tray,
                }
            )
    if len(rows) != 126:
        raise RuntimeError(f'Expected 126 SU1 GB300 compute trays; found {len(rows)}.')
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
    expected_count = int(pattern_row['total_pair_cassettes'])
    if len(cassettes) != expected_count:
        raise RuntimeError(
            f"{pattern_row['row_id_tags']} side {pattern_row['side']} NIC{pattern_row['nic_index_zero']} "
            f'has {len(cassettes)} cassettes; expected {expected_count}.'
        )
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
        raise RuntimeError(f'Expected 128 cassette MPO positions for {pattern_row}; found {len(rows)}.')
    return rows


def leaf_endpoint_sequence(pattern_row: dict[str, str]) -> list[dict]:
    planes = [int(value) for value in pattern_row['planes'].split(',')]
    leaf_names = pattern_row['leaf_devices'].split('|')
    if len(planes) != 2 or len(leaf_names) != 2:
        raise RuntimeError(f'Unexpected plane/leaf pair in pattern row: {pattern_row}')
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
    if len(rows) != 128:
        raise RuntimeError(f'Expected 128 leaf MPO positions for {pattern_row}; found {len(rows)}.')
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


def path_segments(pattern_rows: list[dict[str, str]], gb_rows: list[dict]) -> list[dict]:
    segments = []
    for pattern_row in pattern_rows:
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
                zip(gb_rows, cassette_rows[:126], leaf_rows[:126], strict=True),
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
                'plane_membership_granularity': 'signal_lane',
                'cable_profile_name': 'madison-mpo8-smf-patch-optical-lanes',
            }
            metadata = {
                SOURCE_MARKER: True,
                'modeled_status': 'planned',
                'source_authority': 'Madison rack elevation worksheets',
                'architecture_reference': 'ROCE 4-plane Shuffle Cabling Patterns',
                'policy': PATH_KEY,
                'su_tag': TARGET_SU_TAG,
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
                        'metadata': {
                            **metadata,
                            'segment_kind': 'shuffle_to_leaf',
                        },
                    },
                ]
            )
    return segments


def print_dry_run(pattern_rows: list[dict[str, str]], gb_rows: list[dict], segments: list[dict]) -> None:
    print('Madison SU1 fiber policy dry run')
    print(f'path_key={PATH_KEY}')
    print(f'target_su_tag={TARGET_SU_TAG}')
    print(f'gb300_racks={len({row["rack"].name for row in gb_rows})}')
    print(f'gb300_trays={len(gb_rows)}')
    print(f'pattern_rows={len(pattern_rows)}')
    print(f'leaf_devices={len({leaf for row in pattern_rows for leaf in row["leaf_devices"].split("|")})}')
    print(f'external_segments={len(segments)}')
    print(f'expected_signal_lane_fine_edges={sum(len(segment["lane_indexes"]) for segment in segments)}')
    print('policy:')
    print('  - side A uses GB300 MPO 1 and planes 1/2')
    print('  - side B uses GB300 MPO 2 and planes 3/4')
    print('  - each cassette-pair exposes 128 MPO positions; the first 126 map to the 126 GB300 trays in SU1')
    print('  - source MPO-12 active positions are assigned per 2x2 shuffle channel group')
    for segment in segments[:8]:
        plane_label = segment.get('plane_number') or segment['metadata'].get('position_plane_numbers')
        print(
            f"  sample plane={plane_label} lanes={segment['lane_indexes']} "
            f"{segment['a']} -> {segment['b']}"
        )


def main() -> None:
    fabric = ensure_fabric()
    pattern_rows = read_pattern_rows()
    racks = su1_gb300_racks()
    gb_rows = su1_gb300_trays(racks)
    segments = path_segments(pattern_rows, gb_rows)
    print_dry_run(pattern_rows, gb_rows, segments)
    if not apply_enabled():
        print('apply=false; set MADISON_SU1_FIBER_APPLY=1 to stamp the SU1 fiber policy.')
        return

    counters = Counter()
    with transaction.atomic():
        devices = {row['device'] for row in gb_rows}
        for segment in segments:
            for device_name, _port_name, _mpo in (segment['a'], segment['b']):
                device = Device.objects.filter(site__slug=MAD_SITE_SLUG, name=device_name).first()
                if device is not None:
                    devices.add(device)
        ensure_surfaces_for_devices(fabric, sorted(devices, key=lambda device: device.name), marker=SOURCE_MARKER, counters=counters)
        for marker in SUPERSEDED_PATH_MARKERS:
            delete_marked_connectivity(marker, fabric=fabric, counters=counters)
        counters.update(stamp_path_segments(fabric, marker=SOURCE_MARKER, path_key=PATH_KEY, segments=segments))

    print('Madison SU1 fiber policy seed complete for netbox_plant_graph v2.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_fiber_segments={FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).count()}')


main()
