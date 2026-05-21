from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path
import sys

from netbox_plant_graph.models import FiberSegment
from netbox_plant_graph.services.resolver import resolve_optical_lane_path


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_v2_graph import (  # noqa: E402
    ACTIVE_MPO_LANE_INDEXES,
    get_fabric,
    mpo_endpoint_for_device_port,
    optical_lane_for,
    position_numbers_for_lane_indexes,
)


DEFAULT_PATH_KEY = 'madison-first-nvl72-a2-leaf16-four-plane-lane-aware-v2'
PATH_KEY = os.environ.get('MADISON_FIBER_PATH_KEY', DEFAULT_PATH_KEY)


def edge_key(metadata: dict) -> tuple:
    return (
        metadata.get('plane_number'),
        metadata.get('gb300_device'),
        metadata.get('gb300_interface'),
        metadata.get('gb300_mpo'),
        metadata.get('side'),
        metadata.get('nic_index_zero'),
        metadata.get('leaf_device'),
        metadata.get('leaf_interface'),
        metadata.get('leaf_mpo'),
        metadata.get('shuffle_cassette'),
        metadata.get('shuffle_mpo'),
        tuple(metadata.get('lane_indexes') or ()),
    )


def lane_positions(metadata: dict) -> list[int]:
    raw_indexes = metadata.get('lane_indexes') or list(ACTIVE_MPO_LANE_INDEXES)
    return position_numbers_for_lane_indexes(raw_indexes)


def step_label(step) -> str:
    return getattr(step, 'label', '') or f'{getattr(step, "object_type", "object")}:{getattr(step, "object_id", "")}'


def main() -> None:
    fabric = get_fabric()
    segments = list(FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).order_by('pk'))
    gb_segments = {}
    leaf_segments = {}
    for segment in segments:
        metadata = segment.metadata or {}
        if metadata.get('segment_kind') == 'gb300_to_shuffle':
            gb_segments[edge_key(metadata)] = segment
        elif metadata.get('segment_kind') == 'shuffle_to_leaf':
            leaf_segments[edge_key(metadata)] = segment

    counters = Counter()
    grouped_by_plane = defaultdict(Counter)
    missing_groups = []
    missing_paths = []
    sample_traces = []
    for key, leaf_segment in sorted(leaf_segments.items()):
        gb_segment = gb_segments.get(key)
        metadata = leaf_segment.metadata or {}
        plane_number = metadata.get('plane_number')
        if gb_segment is None:
            counters['missing_gb300_to_shuffle_segment_groups'] += 1
            missing_groups.append(key)
            continue

        gb_metadata = gb_segment.metadata or {}
        source_parent = mpo_endpoint_for_device_port(
            fabric,
            gb_metadata['gb300_device'],
            gb_metadata['gb300_interface'],
            int(gb_metadata['gb300_mpo']),
        ).parent
        destination_parent = mpo_endpoint_for_device_port(
            fabric,
            metadata['leaf_device'],
            metadata['leaf_interface'],
            int(metadata['leaf_mpo']),
        ).parent

        for position_number in lane_positions(metadata):
            counters['expected_optical_lane_paths'] += 1
            grouped_by_plane[plane_number]['expected'] += 1
            source_lane = optical_lane_for(
                source_parent,
                mpo_index=int(gb_metadata['gb300_mpo']),
                position_number=position_number,
                direction='send',
            )
            destination_lane = optical_lane_for(
                destination_parent,
                mpo_index=int(metadata['leaf_mpo']),
                position_number=position_number,
                direction='receive',
            )
            result = resolve_optical_lane_path(source=source_lane, destination=destination_lane)
            if result.path_found:
                counters['resolved_optical_lane_paths'] += 1
                grouped_by_plane[plane_number]['resolved'] += 1
                if len(sample_traces) < 4:
                    sample_traces.append((metadata, position_number, result))
            else:
                counters['missing_optical_lane_paths'] += 1
                grouped_by_plane[plane_number]['missing'] += 1
                if len(missing_paths) < 20:
                    missing_paths.append((metadata, position_number, result.error))

    print('Madison first NVL72 optical lane path report for netbox_plant_graph v2')
    print(f'path_key={PATH_KEY}')
    print(f'fiber_segments={len(segments)}')
    print(f'gb300_to_shuffle_groups={len(gb_segments)}')
    print(f'shuffle_to_leaf_groups={len(leaf_segments)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    for plane_number in sorted(grouped_by_plane):
        counts = grouped_by_plane[plane_number]
        print(
            f'plane_{plane_number}: expected={counts["expected"]} '
            f'resolved={counts["resolved"]} missing={counts["missing"]}'
        )
    if missing_groups:
        print('missing_segment_groups=FAIL')
        for group in missing_groups[:10]:
            print(f'- {group}')
        raise SystemExit(1)
    if counters['missing_optical_lane_paths']:
        print('path_resolution=FAIL')
        print('missing_path_samples:')
        for metadata, position_number, error in missing_paths:
            print(
                f"  plane={metadata.get('plane_number')} strand={position_number} "
                f"{metadata.get('gb300_device')}:{metadata.get('gb300_interface')}:mpo-{metadata.get('gb300_mpo')} "
                f"-> {metadata.get('leaf_device')}:{metadata.get('leaf_interface')}:mpo-{metadata.get('leaf_mpo')} "
                f"via {metadata.get('shuffle_cassette')} mpo-{metadata.get('shuffle_mpo')} error={error}"
            )
        raise SystemExit(1)
    print('path_resolution=PASS')
    print('sample_traces:')
    for metadata, position_number, result in sample_traces:
        print(
            f"  plane={metadata['plane_number']} strand={position_number} "
            f"{metadata['gb300_device']}:{metadata['gb300_interface']}:mpo-{metadata['gb300_mpo']} "
            f"-> {metadata['leaf_device']}:{metadata['leaf_interface']}:mpo-{metadata['leaf_mpo']}"
        )
        for idx, step in enumerate(result.steps or (), start=1):
            print(f'    {idx:02d}. {step_label(step)}')


main()
