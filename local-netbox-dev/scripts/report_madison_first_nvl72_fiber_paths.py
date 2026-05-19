from __future__ import annotations

from collections import Counter, defaultdict

from netbox_plant_graph.models import CoarseEdge, Fabric, FabricPlane, SignalLane
from netbox_plant_graph.services.graph.resolver import resolve_path


FABRIC_NAME = 'MAD-1 RoCE Fabric'
PATH_KEY = 'madison-first-nvl72-a2-leaf16-four-plane-lane-aware-v1'


def lane_for(attachment_unit_id: int, lane_index: int) -> SignalLane:
    return SignalLane.objects.select_related(
        'attachment_unit__termination_point__plant_node',
    ).get(attachment_unit_id=attachment_unit_id, lane_index=lane_index)


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


def step_label(step: dict) -> str:
    if step.get('endpoint_context'):
        return step['endpoint_context']
    for key in ('attachment_unit', 'termination_point', 'plant_node', 'parent_coarse_edge', 'owner_node', 'owner_edge'):
        nested = step.get(key)
        if nested and nested.get('endpoint_context'):
            return nested['endpoint_context']
        if nested and nested.get('display'):
            return nested['display']
    if step.get('display'):
        return step['display']
    if step.get('object') and step['object'].get('display'):
        return step['object']['display']
    return step.get('kind') or step.get('edge_type') or 'step'


def main() -> None:
    fabric = Fabric.objects.get(name=FABRIC_NAME)
    planes = {plane.plane_number: plane for plane in FabricPlane.objects.filter(fabric=fabric)}
    coarse_edges = list(CoarseEdge.objects.filter(metadata__path_key=PATH_KEY).order_by('pk'))
    gb_edges = {}
    leaf_edges = {}
    for edge in coarse_edges:
        metadata = edge.metadata or {}
        if metadata.get('segment_kind') == 'gb300_to_shuffle':
            gb_edges[edge_key(metadata)] = edge
        elif metadata.get('segment_kind') == 'shuffle_to_leaf':
            leaf_edges[edge_key(metadata)] = edge

    counters = Counter()
    missing_groups = []
    sample_traces = []
    grouped_by_plane = defaultdict(Counter)
    for key, leaf_edge in sorted(leaf_edges.items()):
        gb_edge = gb_edges.get(key)
        metadata = leaf_edge.metadata or {}
        plane_number = metadata.get('plane_number')
        plane = planes[plane_number]
        if gb_edge is None:
            counters['missing_gb_segment_groups'] += 1
            missing_groups.append(key)
            continue
        gb_metadata = gb_edge.metadata or {}
        lane_indexes = metadata.get('lane_indexes') or ()
        for lane_index in lane_indexes:
            counters['expected_optical_lane_paths'] += 1
            grouped_by_plane[plane_number]['expected'] += 1
            source_lane = lane_for(gb_metadata['a_attachment_unit_id'], lane_index)
            destination_lane = lane_for(metadata['b_attachment_unit_id'], lane_index)
            result = resolve_path(
                source=source_lane,
                destination=destination_lane,
                plane=plane,
                resolution='signal_lane',
            )
            if result['path_found']:
                counters['resolved_optical_lane_paths'] += 1
                grouped_by_plane[plane_number]['resolved'] += 1
                if len(sample_traces) < 4:
                    sample_traces.append((metadata, lane_index, result))
            else:
                counters['missing_optical_lane_paths'] += 1
                grouped_by_plane[plane_number]['missing'] += 1

    print('Madison first NVL72 optical lane path report')
    print(f'path_key={PATH_KEY}')
    print(f'coarse_edges={len(coarse_edges)}')
    print(f'gb300_to_shuffle_groups={len(gb_edges)}')
    print(f'shuffle_to_leaf_groups={len(leaf_edges)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    for plane_number in sorted(grouped_by_plane):
        counts = grouped_by_plane[plane_number]
        print(
            f'plane_{plane_number}: expected={counts["expected"]} '
            f'resolved={counts["resolved"]} missing={counts["missing"]}'
        )
    if missing_groups:
        print('missing_groups=FAIL')
        for group in missing_groups[:10]:
            print(f'- {group}')
        raise SystemExit(1)
    if counters['missing_optical_lane_paths']:
        print('path_resolution=FAIL')
        raise SystemExit(1)
    print('path_resolution=PASS')
    print('sample_traces:')
    for metadata, lane_index, result in sample_traces:
        print(
            f"  plane={metadata['plane_number']} lane={lane_index + 1} "
            f"{metadata['gb300_device']}:{metadata['gb300_interface']}:mpo-{metadata['gb300_mpo']} "
            f"-> {metadata['leaf_device']}:{metadata['leaf_interface']}:mpo-{metadata['leaf_mpo']}"
        )
        for idx, step in enumerate(result.get('path') or (), start=1):
            print(f'    {idx:02d}. {step_label(step)}')


main()
