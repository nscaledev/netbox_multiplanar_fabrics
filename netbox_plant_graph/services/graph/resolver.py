from collections import defaultdict, deque

from django.contrib.contenttypes.models import ContentType

from dcim.models import FrontPort, Interface, RearPort

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, FabricPlane, FineEdge, LaneMap, PlantNode, PlaneMembership, SignalLane, TerminationPoint, TransferMap

from .payloads import LanePathPayload, LanePathStepPayload, LanePathSummaryPayload, ObjectReferencePayload
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
        return list(target.signal_lanes.select_related('attachment_unit__termination_point__plant_node'))
    for attachment_unit in _normalize_attachment_targets(target):
        signal_lanes = list(attachment_unit.signal_lanes.select_related('attachment_unit__termination_point__plant_node'))
        if signal_lanes:
            return signal_lanes
    return []


def describe_signal_resolution_error(target) -> str | None:
    if target is None:
        return 'Select a valid signal-lane-capable object.'

    if _normalize_signal_targets(target):
        return None

    if isinstance(target, Interface):
        if Interface.objects.filter(parent_id=target.pk).exists():
            return (
                'Signal-lane path resolution requires a channelized child interface, not the parent 800G interface. '
                'Select one of the child interfaces for this port instead.'
            )
        return 'The selected interface is not represented as a signal-lane endpoint in the plant graph.'

    if isinstance(target, (FrontPort, RearPort)):
        return 'The selected passive port does not currently materialize any signal lanes in the plant graph.'

    return 'The selected object does not currently resolve to any signal lanes in the plant graph.'


def _attachment_scope_filters(
    *,
    source_nodes: list[AttachmentUnit] | None = None,
    destination_nodes: list[AttachmentUnit] | None = None,
    plane=None,
) -> tuple[set[int] | None, set[int] | None]:
    fabric_ids = {
        node.termination_point.plant_node.fabric_id
        for node in [*(source_nodes or []), *(destination_nodes or [])]
        if node.termination_point_id and node.termination_point.plant_node_id
    }
    allowed_attachment_ids = _allowed_attachment_ids(plane)
    return (fabric_ids or None, allowed_attachment_ids)


def _build_attachment_adjacency(*, source_nodes: list[AttachmentUnit] | None = None, destination_nodes: list[AttachmentUnit] | None = None, plane=None):
    fabric_ids, allowed_ids = _attachment_scope_filters(
        source_nodes=source_nodes,
        destination_nodes=destination_nodes,
        plane=plane,
    )
    adjacency = defaultdict(list)

    fine_edges = FineEdge.objects.filter(granularity='attachment_unit').select_related(
        'a_au__termination_point__plant_node',
        'b_au__termination_point__plant_node',
        'parent_coarse_edge',
    )
    if fabric_ids is not None:
        fine_edges = fine_edges.filter(
            a_au__termination_point__plant_node__fabric_id__in=fabric_ids,
            b_au__termination_point__plant_node__fabric_id__in=fabric_ids,
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
    if fabric_ids is not None:
        transfer_maps = transfer_maps.filter(
            src_attachment_unit__termination_point__plant_node__fabric_id__in=fabric_ids,
            dst_attachment_unit__termination_point__plant_node__fabric_id__in=fabric_ids,
        )
    for transfer_map in transfer_maps:
        if allowed_ids is not None and (
            transfer_map.src_attachment_unit_id not in allowed_ids or transfer_map.dst_attachment_unit_id not in allowed_ids
        ):
            continue
        adjacency[transfer_map.src_attachment_unit_id].append((transfer_map.dst_attachment_unit_id, 'transfer_map', transfer_map))
        adjacency[transfer_map.dst_attachment_unit_id].append((transfer_map.src_attachment_unit_id, 'transfer_map', transfer_map))

    return adjacency


def _signal_scope_filters(*, source_nodes: list[SignalLane], destination_nodes: list[SignalLane], plane=None) -> tuple[set[int] | None, set[int] | None]:
    fabric_ids = {
        lane.attachment_unit.termination_point.plant_node.fabric_id
        for lane in [*source_nodes, *destination_nodes]
        if lane.attachment_unit_id and lane.attachment_unit.termination_point_id and lane.attachment_unit.termination_point.plant_node_id
    }
    allowed_attachment_ids = _allowed_attachment_ids(plane)
    return (fabric_ids or None, allowed_attachment_ids)


def _build_signal_adjacency(*, source_nodes: list[SignalLane], destination_nodes: list[SignalLane], plane=None):
    adjacency = defaultdict(list)
    fabric_ids, allowed_attachment_ids = _signal_scope_filters(
        source_nodes=source_nodes,
        destination_nodes=destination_nodes,
        plane=plane,
    )

    fine_edges = FineEdge.objects.filter(granularity='signal_lane').select_related(
        'a_lane__attachment_unit__termination_point__plant_node',
        'b_lane__attachment_unit__termination_point__plant_node',
        'parent_coarse_edge',
    )
    if fabric_ids is not None:
        fine_edges = fine_edges.filter(
            a_lane__attachment_unit__termination_point__plant_node__fabric_id__in=fabric_ids,
            b_lane__attachment_unit__termination_point__plant_node__fabric_id__in=fabric_ids,
        )
    if allowed_attachment_ids is not None:
        fine_edges = fine_edges.filter(
            a_lane__attachment_unit_id__in=allowed_attachment_ids,
            b_lane__attachment_unit_id__in=allowed_attachment_ids,
        )

    for fine_edge in fine_edges:
        if not fine_edge.a_lane_id or not fine_edge.b_lane_id:
            continue
        adjacency[fine_edge.a_lane_id].append((fine_edge.b_lane_id, 'fine_edge', fine_edge))
        adjacency[fine_edge.b_lane_id].append((fine_edge.a_lane_id, 'fine_edge', fine_edge))

    lane_maps = LaneMap.objects.select_related(
        'owner_node',
        'owner_edge',
        'src_lane__attachment_unit__termination_point__plant_node',
        'dst_lane__attachment_unit__termination_point__plant_node',
    )
    if fabric_ids is not None:
        lane_maps = lane_maps.filter(
            src_lane__attachment_unit__termination_point__plant_node__fabric_id__in=fabric_ids,
            dst_lane__attachment_unit__termination_point__plant_node__fabric_id__in=fabric_ids,
        )
    if allowed_attachment_ids is not None:
        lane_maps = lane_maps.filter(
            src_lane__attachment_unit_id__in=allowed_attachment_ids,
            dst_lane__attachment_unit_id__in=allowed_attachment_ids,
        )

    for lane_map in lane_maps:
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
    payload['termination_point'] = build_object_reference(signal_lane.attachment_unit.termination_point)
    payload['plant_node'] = build_object_reference(signal_lane.attachment_unit.termination_point.plant_node)
    return payload


def _object_reference_payload(reference):
    return ObjectReferencePayload(
        app_label=reference['app_label'],
        model=reference['model'],
        pk=reference['pk'],
        display=reference['display'],
        registry_key=reference.get('registry_key'),
        url=reference.get('url'),
        path_resolver_url=reference.get('path_resolver_url'),
        blast_radius_url=reference.get('blast_radius_url'),
        lane_drilldown_url=reference.get('lane_drilldown_url'),
        lane_workspace_url=reference.get('lane_workspace_url'),
        signal_path_resolver_url=reference.get('signal_path_resolver_url'),
        signal_blast_radius_url=reference.get('signal_blast_radius_url'),
        health_url=reference.get('health_url'),
    )


def _resolve_selected_signal_lane(target, lane_index=None):
    lanes = _normalize_signal_targets(target)
    if lane_index is None:
        return target
    matching_lanes = [lane for lane in lanes if lane.lane_index == lane_index]
    if not matching_lanes:
        return None
    return matching_lanes[0]


def _typed_lane_path_step(step) -> LanePathStepPayload:
    object_reference = step.get('object')
    if object_reference is None and step.get('app_label') and step.get('model') and step.get('pk') is not None:
        object_reference = step
    signal_lane = None
    if step.get('model') == 'signallane':
        signal_lane = object_reference
    return LanePathStepPayload(
        step_kind=step.get('kind') or 'object',
        display=step.get('display') or step.get('kind') or '',
        edge_type=step.get('edge_type'),
        object=_object_reference_payload(object_reference) if object_reference else None,
        signal_lane=_object_reference_payload(signal_lane) if signal_lane else None,
        attachment_unit=_object_reference_payload(step['attachment_unit']) if step.get('attachment_unit') else None,
        termination_point=_object_reference_payload(step['termination_point']) if step.get('termination_point') else None,
        plant_node=_object_reference_payload(step['plant_node']) if step.get('plant_node') else None,
        parent_coarse_edge=_object_reference_payload(step['parent_coarse_edge']) if step.get('parent_coarse_edge') else None,
        owner_node=_object_reference_payload(step['owner_node']) if step.get('owner_node') else None,
        owner_edge=_object_reference_payload(step['owner_edge']) if step.get('owner_edge') else None,
    )


def resolve_path(*, source, destination=None, plane=None, resolution='attachment_unit', max_depth=128):
    resolution = _normalize_resolution(resolution)

    if resolution == 'signal_lane':
        source_nodes = _normalize_signal_targets(source)
        destination_nodes = _normalize_signal_targets(destination) if destination is not None else []
        if not source_nodes or (destination is not None and not destination_nodes):
            return {
                'resolution': resolution,
                'source': getattr(source, 'pk', source),
                'destination': getattr(destination, 'pk', destination) if destination is not None else None,
                'plane': getattr(_normalize_plane(plane), 'pk', plane),
                'max_depth': max_depth,
                'path_found': False,
                'path': [],
                'summary': {},
            }
        source_fabric_ids = {lane.attachment_unit.termination_point.plant_node.fabric_id for lane in source_nodes}
        destination_fabric_ids = {lane.attachment_unit.termination_point.plant_node.fabric_id for lane in destination_nodes}
        if destination is not None and source_fabric_ids.isdisjoint(destination_fabric_ids):
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
        adjacency = _build_signal_adjacency(
            source_nodes=source_nodes,
            destination_nodes=destination_nodes,
            plane=plane,
        )
        serializer = _serialize_signal_lane
    else:
        source_nodes = _normalize_attachment_targets(source)
        destination_nodes = _normalize_attachment_targets(destination) if destination is not None else []
        if not source_nodes or (destination is not None and not destination_nodes):
            return {
                'resolution': resolution,
                'source': getattr(source, 'pk', source),
                'destination': getattr(destination, 'pk', destination) if destination is not None else None,
                'plane': getattr(_normalize_plane(plane), 'pk', plane),
                'max_depth': max_depth,
                'path_found': False,
                'path': [],
                'summary': {},
            }
        adjacency = _build_attachment_adjacency(
            source_nodes=source_nodes,
            destination_nodes=destination_nodes,
            plane=plane,
        )
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
    else:
        summary['planes_touched'] = _path_plane_numbers([lane.attachment_unit for lane in path_nodes])

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


def resolve_typed_lane_path(
    *,
    source,
    source_lane_index=None,
    destination=None,
    destination_lane_index=None,
    plane=None,
    max_depth=128,
) -> LanePathPayload:
    selected_source = _resolve_selected_signal_lane(source, lane_index=source_lane_index)
    selected_destination = None
    if destination is not None:
        selected_destination = _resolve_selected_signal_lane(destination, lane_index=destination_lane_index)
        if destination_lane_index is not None and selected_destination is None:
            source_reference = build_object_reference(source)
            destination_reference = build_object_reference(destination)
            return LanePathPayload(
                source=_object_reference_payload(source_reference),
                destination=_object_reference_payload(destination_reference),
                source_lane_index=source_lane_index,
                destination_lane_index=destination_lane_index,
                path_found=False,
                plane_id=getattr(_normalize_plane(plane), 'pk', None),
                max_depth=max_depth,
                steps=(),
                summary=LanePathSummaryPayload(
                    coarse_edges_crossed=0,
                    transfer_maps_crossed=0,
                    shuffle_modules_crossed=0,
                    planes_touched=(),
                ),
            )
    if selected_source is None:
        source_reference = build_object_reference(source)
        destination_reference = build_object_reference(destination) if destination is not None else None
        return LanePathPayload(
            source=_object_reference_payload(source_reference),
            destination=_object_reference_payload(destination_reference) if destination_reference else None,
            source_lane_index=source_lane_index,
            destination_lane_index=destination_lane_index,
            path_found=False,
            plane_id=getattr(_normalize_plane(plane), 'pk', None),
            max_depth=max_depth,
            steps=(),
            summary=LanePathSummaryPayload(
                coarse_edges_crossed=0,
                transfer_maps_crossed=0,
                shuffle_modules_crossed=0,
                planes_touched=(),
            ),
        )

    result = resolve_path(
        source=selected_source,
        destination=selected_destination if destination_lane_index is not None else destination,
        plane=plane,
        resolution='signal_lane',
        max_depth=max_depth,
    )
    result_summary = result.get('summary', {})
    return LanePathPayload(
        source=_object_reference_payload(build_object_reference(source)),
        destination=_object_reference_payload(build_object_reference(destination)) if destination is not None else None,
        source_lane_index=source_lane_index,
        destination_lane_index=destination_lane_index,
        path_found=result['path_found'],
        plane_id=getattr(_normalize_plane(plane), 'pk', None),
        max_depth=max_depth,
        steps=tuple(_typed_lane_path_step(step) for step in result.get('path', [])),
        summary=LanePathSummaryPayload(
            coarse_edges_crossed=result_summary.get('coarse_edges_crossed', 0),
            transfer_maps_crossed=result_summary.get('transfer_maps_crossed', 0),
            shuffle_modules_crossed=result_summary.get('shuffle_modules_crossed', 0),
            planes_touched=tuple(result_summary.get('planes_touched', ()) or ()),
        ),
    )


def build_representative_typed_lane_path(
    *,
    source,
    source_lane_index=None,
    plane=None,
    max_depth=128,
) -> LanePathPayload:
    selected_source = _resolve_selected_signal_lane(source, lane_index=source_lane_index)
    if selected_source is None:
        return resolve_typed_lane_path(
            source=source,
            source_lane_index=source_lane_index,
            plane=plane,
            max_depth=max_depth,
        )

    source_nodes = [selected_source]
    adjacency = _build_signal_adjacency(
        source_nodes=source_nodes,
        destination_nodes=source_nodes,
        plane=plane,
    )
    queue = deque([(selected_source.pk, 0)])
    seen = {selected_source.pk: 0}

    while queue:
        node_id, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor_id, edge_kind, edge in adjacency.get(node_id, ()):
            if neighbor_id in seen:
                continue
            seen[neighbor_id] = depth + 1
            queue.append((neighbor_id, depth + 1))

    farthest_lane_id = min(
        (
            lane_id
            for lane_id, distance in seen.items()
            if distance == max(seen.values())
        ),
        default=selected_source.pk,
    )
    destination_lane = SignalLane.objects.select_related('attachment_unit').filter(pk=farthest_lane_id).first()
    if destination_lane is None:
        destination_lane = selected_source

    return resolve_typed_lane_path(
        source=selected_source,
        destination=destination_lane,
        plane=plane,
        max_depth=max_depth,
    )
