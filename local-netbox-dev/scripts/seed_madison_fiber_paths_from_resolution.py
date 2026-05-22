from __future__ import annotations

import csv
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

from madison_v2_graph import MAD_SITE_SLUG, ensure_fabric, ensure_surfaces_for_devices, stamp_path_segments  # noqa: E402


SOURCE_MARKER = 'madison_fiber_path_resolution_v2'
PATH_KEY = 'madison-fiber-path-resolution-v2'
RESOLUTION_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_fiber_path_resolution.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_fiber_path_resolution.csv'),
]


def resolution_path() -> Path:
    for path in RESOLUTION_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison fiber path resolution CSV not found in: {RESOLUTION_PATHS}')


def read_rows() -> list[dict[str, str]]:
    with resolution_path().open(newline='') as handle:
        return list(csv.DictReader(handle))


def cassette_name(box_name: str, tray: str, cassette: str) -> str:
    if box_name.endswith('-sb'):
        return f'{box_name[:-3]}-sbc-{int(tray)}.{int(cassette)}'
    return f'{box_name}-sbc-{int(tray)}.{int(cassette)}'


def endpoint(device_name: str, port_name: str, mpo: int) -> tuple[str, str, int]:
    return (device_name, port_name, mpo)


def row_metadata(row: dict[str, str], *, segment: str, cassette: str) -> dict:
    return {
        SOURCE_MARKER: True,
        'modeled_status': 'planned',
        'segment': segment,
        'su': int(row['su']),
        'worksheet_path_index': int(row['path_index']),
        'nvl72_rack_ordinal': int(row['nvl72_rack_ordinal']),
        'compute_tray_ordinal': int(row['compute_tray_ordinal']),
        'cx8': int(row['cx8']),
        'gb300_mpo': int(row['gb300_mpo']),
        'shuffle_box_ordinal': int(row['shuffle_box_ordinal']),
        'shuffle_tray': int(row['shuffle_tray']),
        'shuffle_cassette': int(row['shuffle_cassette']),
        'shuffle_mpo': int(row['shuffle_mpo']),
        'shuffle_cassette_device': cassette,
        'plane': int(row['plane']),
        'leaf_switch': int(row['leaf_switch']),
        'leaf_cage': int(row['leaf_cage']),
        'leaf_mpo': int(row['leaf_mpo']),
    }


def build_segments(rows: list[dict[str, str]]):
    segments = []
    devices = set()
    for row in rows:
        box_name = row['shuffle_label_matching_candidate_boxes']
        cassette = cassette_name(box_name, row['shuffle_tray'], row['shuffle_cassette'])
        shuffle_mpo = int(row['shuffle_mpo'])
        gb300_mpo = int(row['gb300_mpo'])
        leaf_mpo = int(row['leaf_mpo'])
        segments.extend(
            [
                {
                    'a': endpoint(row['gb300_device'], row['gb300_interface'], gb300_mpo),
                    'b': endpoint(cassette, f'front-mpo-{shuffle_mpo:02d}', 1),
                    'segment_role': 'gb300_to_shuffle',
                    'metadata': row_metadata(row, segment='gb300_to_shuffle', cassette=cassette),
                },
                {
                    'a': endpoint(cassette, f'rear-mpo-{shuffle_mpo:02d}', 1),
                    'b': endpoint(row['leaf_device'], row['leaf_interface'], leaf_mpo),
                    'segment_role': 'shuffle_to_leaf',
                    'metadata': row_metadata(row, segment='shuffle_to_leaf', cassette=cassette),
                },
            ]
        )
        for device_name in (row['gb300_device'], cassette, row['leaf_device']):
            device = Device.objects.filter(site__slug=MAD_SITE_SLUG, name=device_name).first()
            if device is not None:
                devices.add(device)
    return segments, devices


@transaction.atomic
def main() -> None:
    rows = [row for row in read_rows() if row['status'] == 'fully_resolved_by_label']
    counters = Counter()
    fabric = ensure_fabric()
    segments, devices = build_segments(rows)
    ensure_surfaces_for_devices(fabric, sorted(devices, key=lambda device: device.name), marker=SOURCE_MARKER, counters=counters)
    counters.update(stamp_path_segments(fabric, marker=SOURCE_MARKER, path_key=PATH_KEY, segments=segments))

    print('Madison fiber path edge seeding complete for netbox_plant_graph v2.')
    print(f'input_fully_resolved_rows={len(rows)}')
    print(f'external_segments={len(segments)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_fiber_segments={FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).count()}')


main()
