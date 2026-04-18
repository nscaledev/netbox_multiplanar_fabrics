from collections import defaultdict, deque

from django.contrib.contenttypes.models import ContentType

from dcim.models import FrontPort, Interface, RearPort

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, FabricPlane, FineEdge, LaneMap, PlantNode, PlaneMembership, SignalLane, TerminationPoint, TransferMap

from .resolution import DEFAULT_RESOLUTION, SUPPORTED_RESOLUTIONS
from ..netbox.adapters import build_object_reference


def _normalize_resolution(resolution: str) -> str:
    if resolution in SUPPORTED_RESOLUTIONS:
        return resolution
    return DEFAULT_RESOLUTION


def _normalize_plane(plane):
    if plane is None or isinstance(plane, FabricPlane):
        return plane
    try:
        plane_id = int(getattr(plane, 'pk', plane))
    except (TypeError, ValueError):
        return None
    return FabricPlane.objects.filter(pk=plane_id).first()


def _allowed_attachment_ids(plane) -> set[int] | None:
    plane = _normalize_plane(plane)
    if plane is None:
        return None
    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    return set(
        PlaneMembership.objects.filter(plane=plane, member_type=attachment_type).values_list('member_id', flat=True)
    )


def _normalize_attachment_targets(target) -> list[AttachmentUnit]:
    if target is None:
        return []
    if isinstance(target, AttachmentUnit):
        return [target]
    if isinstance(target, SignalLane):
        return [target.attachment_unit]
    if isinstance(target, TerminationPoint):
        return list(target.attachment_units.all())
    if isinstance(target, PlantNode):
        return list(AttachmentUnit.objects.filter(termination_point__plant_node=target))
    if isinstance(target, CoarseEdge):
        fine_edge_units = set()
        for fine_edge in target.fine_edges.select_related('a_au', 'b_au'):
            if fine_edge.a_au_id:
                fine_edge_units.add(fine_edge.a_au)
            if fine_edge.b_au_id:
                fine_edge_units.add(fine_edge.b_au)
        return list(fine_edge_units)
    if isinstance(target, (Interface, FrontPort, RearPort)):
        source_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
        return list(AttachmentUnit.objects.filter(source_type=source_type, source_id=target.pk))
    if hasattr(target, '_meta'):
        source_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
        return list(AttachmentUnit.objects.filter(source_type=source_type, source_id=target.pk))
    return []


def _normalize_signal_targets(target) -> list[SignalLane]:
    if target is None:
        return []
    if isinstance(target, SignalLane):
        return [target]
    if isinstance(target, AttachmentUnit):
        return list(target.signal_lanes.all())
    for attachment_unit in _normalize_attachment_targets(target):
        signal_lanes = list(attachment_unit.signal_lanes.all())
        if signal_lanes:
            return signal_lanes
    return []


def _build_attachment_adjacency(plane=None):
    allowed_ids = _allowed_attachment_ids(plane)
    adjacency = defaultdict(list)

    fine_edges = FineEdge.objects.filter(granularity='attachment_unit').select_related(
        'a_au__termination_point__plant_node',
        'b_au__termination_point__plant_node',
        'parent_coarse_edge',
    )
    for fine_edge in fine_edges:
        if fine_edge.edge_type == 'passthrough_map':
            continue
        if not fine_edge.a_au_id or not fine_edge.b_au_id:
            continue
        if allowed_ids is not None and (fine_edge.a_au_id not in allowed_ids or fine_edge.b_au_id not in allowed_ids):
            continue
        adjacency[fine_edge.a_au_id].append((fine_edge.b_au_id, 'fine_edge', fine_edge))
        adjacency[fine_edge.b_au_id].append((fine_edge.a_au_id, 'fine_edge', fine_edge))

    transfer_maps = TransferMap.objects.select_related(
        'owner_node',
        'src_attachment_unit__termination_point__plant_node',
        'dst_attachment_unit__termination_point__plant_node',
    )
    for transfer_map in transfer_maps:
        if allowed_ids is not None and (
            transfer_map.src_attachment_unit_id not in allowed_ids or transfer_map.dst_attachment_unit_id not in allowed_ids
        ):
            continue
        adjacency[transfer_map.src_attachment_unit_id].append((transfer_map.dst_attachment_unit_id, 'transfer_map', transfer_map))
        adjacency[transfer_map.dst_attachment_unit_id].append((transfer_map.src_attachment_unit_id, 'transfer_map', transfer_map))

    return adjacency


def _build_signal_adjacency():
    adjacency = defaultdict(list)

    for fine_edge in FineEdge.objects.filter(granularity='signal_lane').select_related('a_lane', 'b_lane', 'parent_coarse_edge'):
        if not fine_edge.a_lane_id or not fine_edge.b_lane_id:
            continue
        adjacency[fine_edge.a_lane_id].append((fine_edge.b_lane_id, 'fine_edge', fine_edge))
        adjacency[fine_edge.b_lane_id].append((fine_edge.a_lane_id, 'fine_edge', fine_edge))

    for lane_map in LaneMap.objects.select_related('owner_node', 'owner_edge', 'src_lane', 'dst_lane'):
        adjacency[lane_map.src_lane_id].append((lane_map.dst_lane_id, 'lane_map', lane_map))
        adjacency[lane_map.dst_lane_id].append((lane_map.src_lane_id, 'lane_map', lane_map))

    return adjacency


def _path_plane_numbers(path_nodes: list[AttachmentUnit]) -> list[int]:
    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    plane_numbers = set(
        PlaneMembership.objects.filter(
            member_type=attachment_type,
            member_id__in=[node.pk for node in path_nodes],
        ).values_list('plane__plane_number', flat=True)
    )
    return sorted(plane_numbers)


def _serialize_edge(edge_kind: str, edge) -> dict:
    payload = {
        'kind': edge_kind,
        'edge_type': getattr(edge, 'edge_type', getattr(edge, 'mapping_type', '')),
        'object': build_object_reference(edge),
    }
    if isinstance(edge, FineEdge) and edge.parent_coarse_edge_id:
        payload['parent_coarse_edge'] = build_object_reference(edge.parent_coarse_edge)
    if isinstance(edge, TransferMap):
        payload['owner_node'] = build_object_reference(edge.owner_node)
    if isinstance(edge, LaneMap):
        if edge.owner_node_id:
            payload['owner_node'] = build_object_reference(edge.owner_node)
        if edge.owner_edge_id:
            payload['owner_edge'] = build_object_reference(edge.owner_edge)
    return payload


def _serialize_attachment_unit(attachment_unit: AttachmentUnit) -> dict:
    payload = build_object_reference(attachment_unit)
    payload['termination_point'] = build_object_reference(attachment_unit.termination_point)
    payload['plant_node'] = build_object_reference(attachment_unit.termination_point.plant_node)
    return payload


def _serialize_signal_lane(signal_lane: SignalLane) -> dict:
    payload = build_object_reference(signal_lane)
    payload['attachment_unit'] = build_object_reference(signal_lane.attachment_unit)
    return payload


def resolve_path(*, source, destination=None, plane=None, resolution='attachment_unit', max_depth=128):
    resolution = _normalize_resolution(resolution)

    if resolution == 'signal_lane':
        source_nodes = _normalize_signal_targets(source)
        destination_nodes = _normalize_signal_targets(destination) if destination is not None else []
        adjacency = _build_signal_adjacency()
        serializer = _serialize_signal_lane
    else:
        source_nodes = _normalize_attachment_targets(source)
        destination_nodes = _normalize_attachment_targets(destination) if destination is not None else []
        adjacency = _build_attachment_adjacency(plane=plane)
        serializer = _serialize_attachment_unit

    source_ids = [node.pk for node in source_nodes]
    destination_ids = {node.pk for node in destination_nodes}
    node_lookup = {node.pk: node for node in source_nodes + destination_nodes}

    queue = deque((node_id, 0) for node_id in source_ids)
    parent = {node_id: None for node_id in source_ids}
    via = {}
    found_id = None

    while queue:
        node_id, depth = queue.popleft()
        if destination_ids and node_id in destination_ids:
            found_id = node_id
            break
        if depth >= max_depth:
            continue
        for neighbor_id, edge_kind, edge in adjacency.get(node_id, ()): 
            if neighbor_id in parent:
                continue
            parent[neighbor_id] = node_id
            via[neighbor_id] = (edge_kind, edge)
            queue.append((neighbor_id, depth + 1))
            node_lookup.setdefault(neighbor_id, getattr(edge, 'a_au', None) if getattr(edge, 'a_au_id', None) == neighbor_id else None)

    if destination is None:
        reachable_nodes = []
        if resolution == 'signal_lane':
            reachable_nodes = list(SignalLane.objects.filter(pk__in=parent.keys()).select_related('attachment_unit'))
        else:
            reachable_nodes = list(
                AttachmentUnit.objects.filter(pk__in=parent.keys()).select_related('termination_point__plant_node')
            )
        return {
            'resolution': resolution,
            'source': getattr(source, 'pk', source),
            'destination': None,
            'plane': getattr(_normalize_plane(plane), 'pk', plane),
            'max_depth': max_depth,
            'path_found': bool(reachable_nodes),
            'path': [serializer(node) for node in source_nodes],
            'summary': {
                'reachable_objects': [serializer(node) for node in sorted(reachable_nodes, key=lambda obj: obj.pk)],
                'reachable_count': len(reachable_nodes),
            },
        }

    if found_id is None:
        return {
            'resolution': resolution,
            'source': getattr(source, 'pk', source),
            'destination': getattr(destination, 'pk', destination),
            'plane': getattr(_normalize_plane(plane), 'pk', plane),
            'max_depth': max_depth,
            'path_found': False,
            'path': [],
            'summary': {},
        }

    node_ids = [found_id]
    edge_steps = []
    cursor = found_id
    while parent[cursor] is not None:
        edge_steps.append(via[cursor])
        cursor = parent[cursor]
        node_ids.append(cursor)
    node_ids.reverse()
    edge_steps.reverse()

    if resolution == 'signal_lane':
        path_nodes = list(SignalLane.objects.filter(pk__in=node_ids).select_related('attachment_unit'))
    else:
        path_nodes = list(AttachmentUnit.objects.filter(pk__in=node_ids).select_related('termination_point__plant_node'))
    path_node_map = {node.pk: node for node in path_nodes}

    path = []
    for index, node_id in enumerate(node_ids):
        path.append(serializer(path_node_map[node_id]))
        if index < len(edge_steps):
            edge_kind, edge = edge_steps[index]
            path.append(_serialize_edge(edge_kind, edge))

    coarse_edge_ids = {
        edge.parent_coarse_edge_id
        for edge_kind, edge in edge_steps
        if isinstance(edge, FineEdge) and edge.parent_coarse_edge_id
    }
    transfer_owner_ids = {
        edge.owner_node_id
        for edge_kind, edge in edge_steps
        if isinstance(edge, TransferMap) and edge.owner_node_id
    }

    summary = {
        'coarse_edges_crossed': len(coarse_edge_ids),
        'transfer_maps_crossed': sum(1 for edge_kind, edge in edge_steps if isinstance(edge, TransferMap)),
        'shuffle_modules_crossed': len(transfer_owner_ids),
    }
    if resolution == 'attachment_unit':
        summary['planes_touched'] = _path_plane_numbers(path_nodes)

    return {
        'resolution': resolution,
        'source': getattr(source, 'pk', source),
        'destination': getattr(destination, 'pk', destination),
        'plane': getattr(_normalize_plane(plane), 'pk', plane),
        'max_depth': max_depth,
        'path_found': True,
        'path': path,
        'summary': summary,
    }
