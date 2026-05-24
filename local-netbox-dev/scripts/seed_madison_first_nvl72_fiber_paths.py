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
    stamp_path_segments,
    source_position_plane_numbers_for_shuffle_front,
)
from madison_nvl72_appliance import (  # noqa: E402
    device_effective_position_sort_key,
    device_effective_rack_filter,
)


MAD_SITE_SLUG = 'gs001'
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
    position, name = device_effective_position_sort_key(device)
    return (int(position or 0), name)


def target_gb300_trays() -> list[Device]:
    rack = Rack.objects.get(site__slug=MAD_SITE_SLUG, name=TARGET_NVL72_RACK)
    trays = list(
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            device_type__slug='gb300ct',
            local_context_data__madison_active_endpoint_test_subset=True,
        )
        .filter(device_effective_rack_filter(rack))
        .select_related('rack', 'parent_bay__device__rack')
        .order_by('name')
    )
    if len(trays) != 18:
        raise RuntimeError(f'Expected 18 GB300 compute trays in rack {TARGET_NVL72_RACK}; found {len(trays)}.')
    return sorted(trays, key=tray_sort_key)


def cassettes_for_box(box_name: str) -> list[Device]:
    box_name = normalize_shuffle_box_name(box_name)
    cassette_prefix = f'{box_name[:-3]}-sbc-' if box_name.endswith('-sb') else f'{box_name}-sbc-'
    pattern = re.compile(r'-sbc-(?P<tray>\d+)\.(?P<slot>\d+)$')
    cassettes = list(
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            name__startswith=cassette_prefix,
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


def normalize_device_name(name: str) -> str:
    if name.startswith('mad1-'):
        return f'gs001-{name[len("mad1-"):]}'
    return name


def normalize_shuffle_box_name(name: str) -> str:
    name = normalize_device_name(name)
    if name.endswith('-shuffle-box'):
        return f'{name[:-len("-shuffle-box")]}-sb'
    return name


def endpoint(device_name: str, termination: str, ordinal: int) -> tuple[str, str, int]:
    return (normalize_device_name(device_name), termination, ordinal)


def normalized_leaf_devices(value: str) -> list[str]:
    return [normalize_device_name(device_name) for device_name in value.split('|')]


def cassette_mpo_sequence(cassettes: list[Device]) -> list[dict]:
    rows = []
    for cassette_index, cassette in enumerate(cassettes, start=1):
        for mpo in range(1, 5):
            rows.append(
                {
                    'cassette_index': cassette_index,
                    'mpo': mpo,
                    'device': cassette,
                    'front': endpoint(cassette.name, f'front-mpo-{mpo:02d}', 1),
                    'rear': endpoint(cassette.name, f'rear-mpo-{mpo:02d}', 1),
                }
            )
    return rows


def leaf_endpoint_sequence(pattern_row: dict[str, str]) -> list[dict]:
    planes = [int(value) for value in pattern_row['planes'].split(',')]
    leaf_devices = normalized_leaf_devices(pattern_row['leaf_devices'])
    if len(planes) != 2 or len(leaf_devices) != 2:
        raise RuntimeError(f'Unexpected plane/leaf pair in pattern row: {pattern_row}')
    rows = []
    for cage in range(1, 33):
        rows.extend(
            [
                {'plane': planes[0], 'device_name': leaf_devices[0], 'interface': f'swp{cage}', 'mpo': 1},
                {'plane': planes[1], 'device_name': leaf_devices[1], 'interface': f'swp{cage}', 'mpo': 1},
                {'plane': planes[0], 'device_name': leaf_devices[0], 'interface': f'swp{cage}', 'mpo': 2},
                {'plane': planes[1], 'device_name': leaf_devices[1], 'interface': f'swp{cage}', 'mpo': 2},
            ]
        )
    return rows


def plane_by_mpo_for_cassette(rows_by_cassette_mpo: dict[tuple[str, int], dict], cassette_name: str, mpo: int) -> int | None:
    row = rows_by_cassette_mpo.get((cassette_name, int(mpo)))
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


def pattern_policy_rows(pattern_rows: list[dict[str, str]], trays: list[Device]) -> list[dict]:
    segments = []
    for pattern_row in pattern_rows:
        nic_index = int(pattern_row['nic_index_zero'])
        osfp_name = f'osfp{nic_index + 1}'
        gb_mpo = 1 if pattern_row['side'] == 'A' else 2
        shuffle_18_box = normalize_shuffle_box_name(pattern_row['shuffle_18_box'])
        shuffle_14_box = normalize_shuffle_box_name(pattern_row['shuffle_14_box'])
        cassettes = cassettes_for_box(shuffle_18_box)
        if len(cassettes) < len(trays):
            raise RuntimeError(
                f"{shuffle_18_box} has {len(cassettes)} cassettes; "
                f'{len(trays)} are required for the first NVL72 rack pass.'
            )

        cassette_rows = cassette_mpo_sequence(cassettes)
        leaf_rows = leaf_endpoint_sequence(pattern_row)
        sequence_rows = [
            {
                'tray_index': tray_index,
                'tray': tray,
                'cassette_row': cassette_row,
                'leaf_row': leaf_row,
            }
            for tray_index, (tray, cassette_row, leaf_row) in enumerate(
                zip(trays, cassette_rows[:len(trays)], leaf_rows[:len(trays)], strict=True),
                start=1,
            )
        ]
        rows_by_cassette_mpo = {
            (row['cassette_row']['device'].name, int(row['cassette_row']['mpo'])): row
            for row in sequence_rows
        }

        for row in sequence_rows:
            tray_index = row['tray_index']
            tray = row['tray']
            cassette_row = row['cassette_row']
            leaf_row = row['leaf_row']
            cassette = cassette_row['device']
            cassette_mpo = int(cassette_row['mpo'])
            leaf_device_name = leaf_row['device_name']
            leaf_interface = leaf_row['interface']
            leaf_mpo = int(leaf_row['mpo'])
            source_plane_map = source_position_plane_numbers(
                rows_by_cassette_mpo=rows_by_cassette_mpo,
                cassette_row=cassette_row,
                leaf_row=leaf_row,
            )
            common = {
                'lane_indexes': ACTIVE_MPO_LANE_INDEXES,
                'plane_membership_granularity': 'signal_lane',
                'cable_profile_name': 'madison-mpo8-smf-patch',
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
                'leaf_mpo': leaf_mpo,
                'shuffle_18_box': shuffle_18_box,
                'shuffle_14_box': shuffle_14_box,
                'shuffle_cassette': cassette.name,
                'shuffle_cassette_index_in_box': cassette_row['cassette_index'],
                'shuffle_mpo': cassette_mpo,
                'lane_indexes': list(ACTIVE_MPO_LANE_INDEXES),
            }
            segments.extend([
                {
                    **common,
                    'a': endpoint(tray.name, osfp_name, gb_mpo),
                    'b': cassette_row['front'],
                    'segment_role': 'gb300_to_shuffle_mpo8_jumper',
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
                    'b': endpoint(leaf_device_name, leaf_interface, leaf_mpo),
                    'segment_role': 'shuffle_to_leaf_mpo8_jumper',
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
    print(f'expected_fiber_strands={sum(len(segment["lane_indexes"]) for segment in segments)}')
    print('policy:')
    print('  - side A uses GB300 MPO 1 and leaf planes 1/2')
    print('  - side B uses GB300 MPO 2 and leaf planes 3/4')
    print('  - every source MPO is modeled as one 8-strand MPO-to-MPO jumper to one cassette front MPO')
    print('  - source MPO lane groups carry per-position plane assignment; the cassette transfer maps split them internally')
    print('  - first NVL72 tray indexes consume the first 18 cassette front MPO positions in the elevation-derived fill order')
    for segment in segments[:8]:
        plane_label = segment.get('plane_number') or segment.get('metadata', {}).get('position_plane_numbers')
        print(f"  sample plane={plane_label} lanes={segment['lane_indexes']} {segment['a']} -> {segment['b']}")


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
