from __future__ import annotations

import os
from collections import Counter, defaultdict

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from netbox_plant_graph.models import CoarseEdge, Fabric, FineEdge, LaneMap, PlaneMembership, SignalLane


FABRIC_NAME = 'MAD-1 RoCE Fabric'
DEFAULT_PATH_KEY = 'madison-first-nvl72-a2-leaf16-four-plane-lane-aware-v1'
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


def lane_memberships(fabric: Fabric) -> dict[int, set[int]]:
    signal_lane_type = ContentType.objects.get_for_model(SignalLane)
    memberships = defaultdict(set)
    for lane_id, plane_number in PlaneMembership.objects.filter(
        plane__fabric=fabric,
        member_type=signal_lane_type,
        membership_role='native',
    ).values_list('member_id', 'plane__plane_number'):
        memberships[lane_id].add(plane_number)
    return memberships


def lane_edges_by_parent(coarse_edge_ids: list[int]) -> dict[int, dict[int, FineEdge]]:
    by_parent: dict[int, dict[int, FineEdge]] = defaultdict(dict)
    for edge in FineEdge.objects.filter(
        parent_coarse_edge_id__in=coarse_edge_ids,
        granularity='signal_lane',
    ).select_related('a_lane', 'b_lane', 'a_au__source_type', 'b_au__source_type'):
        if edge.a_lane is None or edge.b_lane is None:
            continue
        if edge.a_lane.lane_index != edge.b_lane.lane_index:
            by_parent[edge.parent_coarse_edge_id][f'mismatch:{edge.pk}'] = edge
            continue
        by_parent[edge.parent_coarse_edge_id][edge.a_lane.lane_index] = edge
    return by_parent


def lane_map_pairs(lane_ids: set[int]) -> set[tuple[int, int]]:
    pairs = set()
    for src_lane_id, dst_lane_id in LaneMap.objects.filter(
        Q(src_lane_id__in=lane_ids) | Q(dst_lane_id__in=lane_ids)
    ).values_list('src_lane_id', 'dst_lane_id'):
        pairs.add((src_lane_id, dst_lane_id))
        pairs.add((dst_lane_id, src_lane_id))
    return pairs


def source_obj(attachment_unit):
    return attachment_unit.source or attachment_unit.termination_point.source or attachment_unit.termination_point.plant_node.source


def native_cabled_source_labels(edges: list[FineEdge]) -> list[str]:
    labels = set()
    for edge in edges:
        for attachment_unit in (edge.a_au, edge.b_au):
            if attachment_unit is None:
                continue
            obj = source_obj(attachment_unit)
            cable_id = getattr(obj, 'cable_id', None)
            if cable_id:
                labels.add(f'{obj._meta.label}:{obj.pk}:{obj}')
    return sorted(labels)


def main() -> None:
    fabric = Fabric.objects.get(name=FABRIC_NAME)
    coarse_edges = list(CoarseEdge.objects.filter(metadata__path_key=PATH_KEY).order_by('pk'))
    coarse_edge_ids = [edge.pk for edge in coarse_edges]
    lane_edges = lane_edges_by_parent(coarse_edge_ids)
    gb_edges = {}
    leaf_edges = {}
    for edge in coarse_edges:
        metadata = edge.metadata or {}
        if metadata.get('segment_kind') == 'gb300_to_shuffle':
            gb_edges[edge_key(metadata)] = edge
        elif metadata.get('segment_kind') == 'shuffle_to_leaf':
            leaf_edges[edge_key(metadata)] = edge

    all_lane_ids = set()
    for parent_edges in lane_edges.values():
        for fine_edge in parent_edges.values():
            if isinstance(fine_edge, FineEdge):
                if fine_edge.a_lane_id:
                    all_lane_ids.add(fine_edge.a_lane_id)
                if fine_edge.b_lane_id:
                    all_lane_ids.add(fine_edge.b_lane_id)

    lane_maps = lane_map_pairs(all_lane_ids)
    memberships = lane_memberships(fabric)
    counters = Counter()
    findings = []
    sample_checked = []
    involved_attachment_edges = list(
        FineEdge.objects.filter(parent_coarse_edge_id__in=coarse_edge_ids, granularity='attachment_unit').select_related(
            'a_au__termination_point__plant_node',
            'b_au__termination_point__plant_node',
            'a_au__source_type',
            'b_au__source_type',
        )
    )

    for key, leaf_edge in sorted(leaf_edges.items()):
        gb_edge = gb_edges.get(key)
        metadata = leaf_edge.metadata or {}
        plane_number = metadata.get('plane_number')
        lane_indexes = tuple(metadata.get('lane_indexes') or ())
        counters['fiber_groups_expected'] += 1
        if gb_edge is None:
            counters['missing_gb300_to_shuffle_groups'] += 1
            findings.append(f'Missing GB300->shuffle group for key={key!r}.')
            continue
        for lane_index in lane_indexes:
            counters['optical_lane_paths_expected'] += 1
            gb_lane_edge = lane_edges.get(gb_edge.pk, {}).get(lane_index)
            leaf_lane_edge = lane_edges.get(leaf_edge.pk, {}).get(lane_index)
            if gb_lane_edge is None:
                counters['missing_gb300_to_shuffle_lane_edges'] += 1
                findings.append(f'Missing GB300->shuffle lane edge for group={key!r} lane={lane_index}.')
                continue
            if leaf_lane_edge is None:
                counters['missing_shuffle_to_leaf_lane_edges'] += 1
                findings.append(f'Missing shuffle->leaf lane edge for group={key!r} lane={lane_index}.')
                continue

            gb_lane_id = gb_lane_edge.a_lane_id
            cassette_front_lane_id = gb_lane_edge.b_lane_id
            cassette_rear_lane_id = leaf_lane_edge.a_lane_id
            leaf_lane_id = leaf_lane_edge.b_lane_id
            for role, lane_id in (
                ('gb300', gb_lane_id),
                ('cassette_front', cassette_front_lane_id),
                ('cassette_rear', cassette_rear_lane_id),
                ('leaf', leaf_lane_id),
            ):
                lane_planes = memberships.get(lane_id, set())
                if lane_planes != {plane_number}:
                    counters['lane_plane_membership_mismatches'] += 1
                    findings.append(
                        f'{role} lane id={lane_id} expected plane {plane_number}; got {sorted(lane_planes)}.'
                    )

            if (cassette_front_lane_id, cassette_rear_lane_id) not in lane_maps:
                counters['missing_cassette_lane_maps'] += 1
                findings.append(
                    f'Missing cassette LaneMap from front lane {cassette_front_lane_id} '
                    f'to rear lane {cassette_rear_lane_id} for group={key!r}.'
                )
                continue

            counters['optical_lane_paths_verified'] += 1
            counters[f'plane_{plane_number}_verified'] += 1
            if len(sample_checked) < 6:
                sample_checked.append(
                    f"plane={plane_number} lane={lane_index + 1} "
                    f"{metadata.get('gb300_device')}:{metadata.get('gb300_interface')}:mpo-{metadata.get('gb300_mpo')} "
                    f"-> {metadata.get('leaf_device')}:{metadata.get('leaf_interface')}:mpo-{metadata.get('leaf_mpo')}"
                )

    native_cabled = native_cabled_source_labels(involved_attachment_edges)
    counters['native_cabled_sources'] = len(native_cabled)
    if native_cabled:
        findings.append(f'Native dcim.Cable links exist on modeled fiber endpoint sources: {native_cabled[:10]}.')

    print('Madison fast fiber audit')
    print(f'path_key={PATH_KEY}')
    print(f'coarse_edges={len(coarse_edges)}')
    print(f'gb300_to_shuffle_groups={len(gb_edges)}')
    print(f'shuffle_to_leaf_groups={len(leaf_edges)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print('samples:')
    for item in sample_checked:
        print(f'- {item}')
    if findings:
        print('findings=FAIL')
        for finding in findings[:50]:
            print(f'- {finding}')
        raise SystemExit(1)
    print('findings=PASS')


main()
