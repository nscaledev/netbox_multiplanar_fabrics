from __future__ import annotations

import os
from collections import Counter, defaultdict

from netbox_plant_graph.models import (
    ConnectorPosition,
    Endpoint,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StrandTermination,
    TransferMap,
)
from netbox_plant_graph.services.architecture import CHANNEL_MAP_MATRIX


FABRIC_SLUG = 'mad-1-roce-fabric'
DEFAULT_PATH_KEY = 'madison-ready-sus-leaf16-four-plane-lane-aware-v2'
PATH_KEY = os.environ.get('MADISON_FIBER_PATH_KEY', DEFAULT_PATH_KEY)
ACTIVE_MPO_POSITIONS = tuple(
    position
    for entry in CHANNEL_MAP_MATRIX
    if int(entry['mpo_index']) == 1
    for position in entry['positions']
)
ACTIVE_MPO_LANE_INDEXES = tuple(range(len(ACTIVE_MPO_POSITIONS)))


def edge_key(metadata: dict) -> tuple:
    return (
        metadata.get('su'),
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


def lane_positions(metadata: dict) -> tuple[int, ...]:
    raw_indexes = metadata.get('lane_indexes') or list(ACTIVE_MPO_LANE_INDEXES)
    return tuple(sorted({ACTIVE_MPO_POSITIONS[int(raw_index)] for raw_index in raw_indexes}))


def main() -> None:
    counters = Counter()
    findings = []
    segments = list(
        FiberSegment.objects.filter(fabric__slug=FABRIC_SLUG, metadata__path_key=PATH_KEY)
        .select_related('a_endpoint__parent', 'b_endpoint__parent')
        .order_by('pk')
    )
    counters['fiber_segments'] = len(segments)
    segment_ids = [segment.pk for segment in segments]
    gb_segments = {}
    leaf_segments = {}
    endpoint_ids = set()
    expected_positions_by_segment = {}

    for segment in segments:
        metadata = segment.metadata or {}
        endpoint_ids.update([segment.a_endpoint_id, segment.b_endpoint_id])
        expected_positions = lane_positions(metadata)
        expected_positions_by_segment[segment.pk] = expected_positions
        if metadata.get('segment_kind') == 'gb300_to_shuffle':
            gb_segments[edge_key(metadata)] = segment
        elif metadata.get('segment_kind') == 'shuffle_to_leaf':
            leaf_segments[edge_key(metadata)] = segment

    counters['gb300_to_shuffle_groups'] = len(gb_segments)
    counters['shuffle_to_leaf_groups'] = len(leaf_segments)
    if len(gb_segments) != len(leaf_segments):
        findings.append(f'gb/leaf group count mismatch: {len(gb_segments)} != {len(leaf_segments)}.')

    missing_gb = [key for key in leaf_segments if key not in gb_segments]
    counters['missing_gb300_to_shuffle_groups'] = len(missing_gb)
    if missing_gb:
        findings.append(f'Missing GB300-to-shuffle groups: {missing_gb[:5]!r}.')

    strand_rows = list(FiberStrand.objects.filter(segment_id__in=segment_ids).values_list('id', 'segment_id', 'strand_index'))
    strand_ids = [row[0] for row in strand_rows]
    strands_by_segment = defaultdict(set)
    for _strand_id, segment_id, strand_index in strand_rows:
        strands_by_segment[segment_id].add(strand_index)
    counters['fiber_strands'] = len(strand_rows)

    bad_strand_segments = []
    for segment_id, expected_positions in expected_positions_by_segment.items():
        if strands_by_segment.get(segment_id, set()) != set(expected_positions):
            bad_strand_segments.append(segment_id)
    counters['segments_with_bad_strand_set'] = len(bad_strand_segments)
    if bad_strand_segments:
        findings.append(f'Segments with incorrect strand sets: {bad_strand_segments[:10]}.')

    termination_count = StrandTermination.objects.filter(strand_id__in=strand_ids).count()
    counters['strand_terminations'] = termination_count
    expected_terminations = sum(len(value) * 2 for value in expected_positions_by_segment.values())
    counters['expected_strand_terminations'] = expected_terminations
    if termination_count != expected_terminations:
        findings.append(f'Strand termination count {termination_count} != expected {expected_terminations}.')

    positions = {
        (position.endpoint_id, position.position_number): position.pk
        for position in ConnectorPosition.objects.filter(endpoint_id__in=endpoint_ids)
    }

    transfer_pairs = set(
        TransferMap.objects.filter(fabric__slug=FABRIC_SLUG).values_list('src_position_id', 'dst_position_id')
    )

    plane_ids = dict(Plane.objects.filter(fabric__slug=FABRIC_SLUG).values_list('plane_number', 'pk'))
    parent_endpoint_ids = {
        segment.a_endpoint.parent_id for segment in segments if segment.a_endpoint.parent_id
    } | {
        segment.b_endpoint.parent_id for segment in segments if segment.b_endpoint.parent_id
    }
    mpo_endpoint_ids = endpoint_ids
    optical_lane_rows = OpticalLane.objects.filter(
        fabric__slug=FABRIC_SLUG,
        endpoint_id__in=parent_endpoint_ids,
        local_mpo_endpoint_id__in=mpo_endpoint_ids,
    ).exclude(channel=None).values_list(
        'endpoint_id',
        'local_mpo_endpoint_id',
        'local_mpo_position_id',
        'direction',
        'plane_id',
        'channel_id',
    )
    optical_lane_keys = {
        (endpoint_id, local_mpo_endpoint_id, local_mpo_position_id, direction, plane_id)
        for endpoint_id, local_mpo_endpoint_id, local_mpo_position_id, direction, plane_id, channel_id in optical_lane_rows
        if channel_id is not None
    }

    sample_verified = []
    for key, leaf_segment in sorted(leaf_segments.items()):
        gb_segment = gb_segments.get(key)
        if gb_segment is None:
            continue
        metadata = leaf_segment.metadata or {}
        plane_number = metadata.get('plane_number')
        plane_id = plane_ids.get(plane_number)
        if plane_id is None:
            counters['missing_plane_id'] += 1
            findings.append(f'Missing Plane row for plane_number={plane_number}.')
            continue
        for position_number in lane_positions(metadata):
            counters['expected_end_to_end_paths'] += 1
            counters[f'plane_{plane_number}_expected_paths'] += 1

            gb_a_position_id = positions.get((gb_segment.a_endpoint_id, position_number))
            gb_b_position_id = positions.get((gb_segment.b_endpoint_id, position_number))
            leaf_a_position_id = positions.get((leaf_segment.a_endpoint_id, position_number))
            leaf_b_position_id = positions.get((leaf_segment.b_endpoint_id, position_number))
            if None in {gb_a_position_id, gb_b_position_id, leaf_a_position_id, leaf_b_position_id}:
                counters['missing_connector_positions'] += 1
                continue
            if (gb_b_position_id, leaf_a_position_id) not in transfer_pairs and (leaf_a_position_id, gb_b_position_id) not in transfer_pairs:
                counters['missing_shuffle_transfer_maps'] += 1
                continue

            source_lane_key = (gb_segment.a_endpoint.parent_id, gb_segment.a_endpoint_id, gb_a_position_id, 'send', plane_id)
            destination_lane_key = (leaf_segment.b_endpoint.parent_id, leaf_segment.b_endpoint_id, leaf_b_position_id, 'receive', plane_id)
            if source_lane_key not in optical_lane_keys:
                counters['missing_source_optical_lanes'] += 1
                continue
            if destination_lane_key not in optical_lane_keys:
                counters['missing_destination_optical_lanes'] += 1
                continue
            counters['verified_end_to_end_paths'] += 1
            counters[f'plane_{plane_number}_verified_paths'] += 1
            if len(sample_verified) < 8:
                sample_verified.append(
                    f"su={metadata.get('su')} plane={plane_number} strand={position_number} "
                    f"{metadata.get('gb300_device')}:{metadata.get('gb300_interface')}:mpo-{metadata.get('gb300_mpo')} "
                    f"-> {metadata.get('leaf_device')}:{metadata.get('leaf_interface')}:mpo-{metadata.get('leaf_mpo')} "
                    f"via {metadata.get('shuffle_cassette')}:mpo-{metadata.get('shuffle_mpo')}"
                )

    for key in (
        'missing_connector_positions',
        'missing_shuffle_transfer_maps',
        'missing_source_optical_lanes',
        'missing_destination_optical_lanes',
    ):
        if counters[key]:
            findings.append(f'{key}={counters[key]}')

    print('Madison ready-SU fast fiber audit for netbox_plant_graph v2')
    print(f'path_key={PATH_KEY}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print('samples:')
    for sample in sample_verified:
        print(f'- {sample}')
    if findings:
        print('findings=FAIL')
        for finding in findings[:50]:
            print(f'- {finding}')
        raise SystemExit(1)
    print('findings=PASS')


main()
