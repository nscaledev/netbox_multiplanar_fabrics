from __future__ import annotations

import csv
import os
import re
from collections import Counter
from pathlib import Path
import sys

from django.db import transaction

from dcim.models import Device
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
    stamp_path_segments,
)


MAD_SITE_SLUG = 'mad-1'
TARGET_NVL72_RACK = 'A2'
TARGET_SU_TAG = 'nv_su_1'
SOURCE_MARKER = 'madison_first_nvl72_fiber_policy_v2'
SUPERSEDED_SAMPLE_MARKER = 'madison_graph_endpoint_test_subset_v2'
PATH_KEY = 'madison-first-nvl72-a2-leaf16-four-plane-lane-aware-v2'

PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_FIRST_NVL72_FIBER_APPLY') == '1'


def pattern_input_path() -> Path:
    for path in PATTERN_INPUT_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison elevation shuffle/leaf pattern CSV not found in: {PATTERN_INPUT_PATHS}')


def read_pattern_rows() -> list[dict[str, str]]:
    with pattern_input_path().open(newline='') as handle:
        rows = [row for row in csv.DictReader(handle)]
    rows = [
        row for row in rows
        if row['status'] == 'ok'
        and row['su_tags'] == TARGET_SU_TAG
        and row['row_id_tags'] in {'nscale-row-id-a9', 'nscale-row-id-a10'}
        and row['side'] in {'A', 'B'}
    ]
    if len(rows) != 8:
        raise RuntimeError(f'Expected 8 SU1 A/B shuffle leaf pattern rows; found {len(rows)}.')
    return sorted(rows, key=lambda row: (row['side'], int(row['nic_index_zero'])))


def tray_sort_key(device: Device) -> tuple[int, str]:
    position = getattr(device, 'position', None)
    return (int(position or 0), device.name)


def target_gb300_trays() -> list[Device]:
    trays = list(
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            rack__name=TARGET_NVL72_RACK,
            device_type__slug='poweredge-xe9712-gb300-compute-tray',
            local_context_data__madison_active_endpoint_test_subset=True,
        )
        .select_related('rack')
        .order_by('position', 'name')
    )
    if len(trays) != 18:
        raise RuntimeError(f'Expected 18 GB300 compute trays in rack {TARGET_NVL72_RACK}; found {len(trays)}.')
    return sorted(trays, key=tray_sort_key)


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


def endpoint(device_name: str, termination: str, ordinal: int) -> tuple[str, str, int]:
    return (device_name, termination, ordinal)


def pattern_policy_rows(pattern_rows: list[dict[str, str]], trays: list[Device]) -> list[dict]:
    segments = []
    for pattern_row in pattern_rows:
        planes = [int(value) for value in pattern_row['planes'].split(',')]
        leaf_devices = pattern_row['leaf_devices'].split('|')
        if len(planes) != 2 or len(leaf_devices) != 2:
            raise RuntimeError(f'Unexpected plane/leaf pair in pattern row: {pattern_row}')

        nic_index = int(pattern_row['nic_index_zero'])
        osfp_name = f'osfp{nic_index + 1}'
        gb_mpo = 1 if pattern_row['side'] == 'A' else 2
        cassettes = cassettes_for_box(pattern_row['shuffle_18_box'])
        if len(cassettes) < len(trays):
            raise RuntimeError(
                f"{pattern_row['shuffle_18_box']} has {len(cassettes)} cassettes; "
                f'{len(trays)} are required for the first NVL72 rack pass.'
            )

        for tray_index, (tray, cassette) in enumerate(zip(trays, cassettes, strict=True), start=1):
            for pair_index, (plane_number, leaf_device_name) in enumerate(zip(planes, leaf_devices, strict=True)):
                lane_indexes = (
                    tuple(ACTIVE_MPO_LANE_INDEXES[:4])
                    if pair_index == 0
                    else tuple(ACTIVE_MPO_LANE_INDEXES[4:])
                )
                cassette_mpo = pair_index + 1
                leaf_interface = f'swp{tray_index}'
                common = {
                    'plane_number': plane_number,
                    'lane_indexes': lane_indexes,
                    'plane_membership_granularity': 'signal_lane',
                    'cable_profile_name': 'madison-mpo8-smf-patch-lane-split',
                }
                metadata = {
                    SOURCE_MARKER: True,
                    'modeled_status': 'planned',
                    'source_authority': 'Madison rack elevation worksheets',
                    'policy': PATH_KEY,
                    'su_tag': TARGET_SU_TAG,
                    'gb300_rack': TARGET_NVL72_RACK,
                    'gb300_device': tray.name,
                    'gb300_tray_index': tray_index,
                    'gb300_interface': osfp_name,
                    'gb300_mpo': gb_mpo,
                    'side': pattern_row['side'],
                    'nic_index_zero': nic_index,
                    'leaf_index_bottom_up': int(pattern_row['leaf_index_bottom_up']),
                    'leaf_device': leaf_device_name,
                    'leaf_interface': leaf_interface,
                    'leaf_mpo': 1,
                    'shuffle_18_box': pattern_row['shuffle_18_box'],
                    'shuffle_14_box': pattern_row['shuffle_14_box'],
                    'shuffle_cassette': cassette.name,
                    'shuffle_mpo': cassette_mpo,
                    'lane_indexes': list(lane_indexes),
                }
                segments.extend([
                    {
                        **common,
                        'a': endpoint(tray.name, osfp_name, gb_mpo),
                        'b': endpoint(cassette.name, f'front-mpo-{cassette_mpo:02d}', 1),
                        'segment_role': 'gb300_to_shuffle_mpo12_active_lane_split',
                        'metadata': {
                            **metadata,
                            'segment_kind': 'gb300_to_shuffle',
                        },
                    },
                    {
                        **common,
                        'a': endpoint(cassette.name, f'rear-mpo-{cassette_mpo:02d}', 1),
                        'b': endpoint(leaf_device_name, leaf_interface, 1),
                        'segment_role': 'shuffle_to_leaf_mpo12_active_lane_split',
                        'metadata': {
                            **metadata,
                            'segment_kind': 'shuffle_to_leaf',
                        },
                    },
                ])
    return segments


def print_dry_run(pattern_rows: list[dict[str, str]], trays: list[Device], segments: list[dict]) -> None:
    print('Madison first NVL72 fiber policy dry run')
    print(f'path_key={PATH_KEY}')
    print(f'target_nvl72_rack={TARGET_NVL72_RACK}')
    print(f'target_su_tag={TARGET_SU_TAG}')
    print(f'gb300_trays={len(trays)}')
    print(f'pattern_rows={len(pattern_rows)}')
    print(f'leaf_devices={len({leaf for row in pattern_rows for leaf in row["leaf_devices"].split("|")})}')
    print(f'external_segments={len(segments)}')
    print(f'expected_signal_lane_fine_edges={sum(len(segment["lane_indexes"]) for segment in segments)}')
    print('policy:')
    print('  - side A uses GB300 MPO 1 and leaf planes 1/2')
    print('  - side B uses GB300 MPO 2 and leaf planes 3/4')
    print('  - each side/plane pair splits MPO optical lanes as 0-3 for the lower plane and 4-7 for the upper plane')
    print('  - first NVL72 tray index maps to leaf swp index and the corresponding 18-cassette shuffle box positions')
    for segment in segments[:8]:
        print(
            f"  sample plane={segment['plane_number']} lanes={segment['lane_indexes']} "
            f"{segment['a']} -> {segment['b']}"
        )


def main() -> None:
    fabric = ensure_fabric()
    pattern_rows = read_pattern_rows()
    trays = target_gb300_trays()
    segments = pattern_policy_rows(pattern_rows, trays)
    print_dry_run(pattern_rows, trays, segments)
    if not apply_enabled():
        print('apply=false; set MADISON_FIRST_NVL72_FIBER_APPLY=1 to stamp the bounded fiber policy.')
        return

    counters = Counter()
    with transaction.atomic():
        devices = set(trays)
        for segment in segments:
            for device_name, _port_name, _mpo in (segment['a'], segment['b']):
                device = Device.objects.filter(site__slug=MAD_SITE_SLUG, name=device_name).first()
                if device is not None:
                    devices.add(device)
        ensure_surfaces_for_devices(fabric, sorted(devices, key=lambda device: device.name), marker=SOURCE_MARKER, counters=counters)
        delete_marked_connectivity(SUPERSEDED_SAMPLE_MARKER, fabric=fabric, counters=counters)
        counters.update(stamp_path_segments(fabric, marker=SOURCE_MARKER, path_key=PATH_KEY, segments=segments))

    print('Madison first NVL72 fiber policy seed complete for netbox_plant_graph v2.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_fiber_segments={FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).count()}')


main()
