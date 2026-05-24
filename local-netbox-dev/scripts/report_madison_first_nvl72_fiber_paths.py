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
    get_fabric,
    mpo_endpoint_for_device_port,
    optical_lane_for,
)


DEFAULT_PATH_KEY = 'madison-first-nvl72-a2-leaf16-four-plane-lane-aware-v2'
PATH_KEY = os.environ.get('MADISON_FIBER_PATH_KEY', DEFAULT_PATH_KEY)


def segment_position_numbers(segment: FiberSegment) -> list[int]:
    metadata = segment.metadata or {}
    values = metadata.get('position_numbers') or ()
    try:
        return sorted({int(value) for value in values})
    except (TypeError, ValueError):
        return []


def step_label(step) -> str:
    return getattr(step, 'label', '') or f'{getattr(step, "object_type", "object")}:{getattr(step, "object_id", "")}'


def main() -> None:
    fabric = get_fabric()
    segments = list(FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).order_by('pk'))
    gb_segments = []
    leaf_segments = []
    for segment in segments:
        metadata = segment.metadata or {}
        if metadata.get('segment_kind') == 'gb300_to_shuffle':
            gb_segments.append(segment)
        elif metadata.get('segment_kind') == 'shuffle_to_leaf':
            leaf_segments.append(segment)

    counters = Counter()
    grouped_by_plane = defaultdict(Counter)
    missing_paths = []
    structural_findings = []
    sample_traces = []

    destination_sets_by_source = defaultdict(set)
    for segment in gb_segments:
        destination_sets_by_source[segment.a_endpoint_id].add(segment.b_endpoint_id)
        position_numbers = segment_position_numbers(segment)
        if len(position_numbers) != 8:
            structural_findings.append(
                f'{segment.name} has {len(position_numbers)} modeled positions; expected one 8-strand source MPO jumper.'
            )
    split_source_mpos = {
        source_id: destinations
        for source_id, destinations in destination_sets_by_source.items()
        if len(destinations) != 1
    }
    if split_source_mpos:
        counters['source_mpos_with_split_shuffle_fronts'] = len(split_source_mpos)
        sample_source_ids = sorted(split_source_mpos)[:10]
        structural_findings.append(
            f'{len(split_source_mpos)} source MPO endpoint(s) map to multiple shuffle front MPOs; sample endpoint ids {sample_source_ids}.'
        )

    for segment in leaf_segments:
        position_numbers = segment_position_numbers(segment)
        if len(position_numbers) != 8:
            structural_findings.append(
                f'{segment.name} has {len(position_numbers)} modeled positions; expected one 8-strand shuffle-to-leaf jumper.'
            )

    for gb_segment in sorted(gb_segments, key=lambda segment: segment.name):
        gb_metadata = gb_segment.metadata or {}
        source_parent = mpo_endpoint_for_device_port(
            fabric,
            gb_metadata['gb300_device'],
            gb_metadata['gb300_interface'],
            int(gb_metadata['gb300_mpo']),
        ).parent
        for position_number in segment_position_numbers(gb_segment):
            counters['expected_optical_lane_paths'] += 1
            source_lane = optical_lane_for(
                source_parent,
                mpo_index=int(gb_metadata['gb300_mpo']),
                position_number=position_number,
                direction='send',
            )
            plane_number = source_lane.plane.plane_number if source_lane.plane_id else 'unassigned'
            grouped_by_plane[plane_number]['expected'] += 1
            result = resolve_optical_lane_path(source=source_lane, destination=None)
            if result.path_found:
                counters['resolved_optical_lane_paths'] += 1
                grouped_by_plane[plane_number]['resolved'] += 1
                if len(sample_traces) < 4:
                    sample_traces.append((gb_metadata, position_number, plane_number, result))
            else:
                counters['missing_optical_lane_paths'] += 1
                grouped_by_plane[plane_number]['missing'] += 1
                if len(missing_paths) < 20:
                    missing_paths.append((gb_metadata, position_number, result.error))

    print('Madison first NVL72 optical lane path report for netbox_plant_graph v2')
    print(f'path_key={PATH_KEY}')
    print(f'fiber_segments={len(segments)}')
    print(f'gb300_to_shuffle_segments={len(gb_segments)}')
    print(f'shuffle_to_leaf_segments={len(leaf_segments)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    for plane_number in sorted(grouped_by_plane, key=lambda value: (str(value))):
        counts = grouped_by_plane[plane_number]
        print(
            f'plane_{plane_number}: expected={counts["expected"]} '
            f'resolved={counts["resolved"]} missing={counts["missing"]}'
        )
    if structural_findings:
        print('physical_jumper_structure=FAIL')
        for finding in structural_findings[:20]:
            print(f'- {finding}')
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
    print('physical_jumper_structure=PASS')
    print('path_resolution=PASS')
    print('sample_traces:')
    for metadata, position_number, plane_number, result in sample_traces:
        print(
            f"  plane={plane_number} strand={position_number} "
            f"{metadata['gb300_device']}:{metadata['gb300_interface']}:mpo-{metadata['gb300_mpo']}"
        )
        for idx, step in enumerate(result.steps or (), start=1):
            print(f'    {idx:02d}. {step_label(step)}')


main()
