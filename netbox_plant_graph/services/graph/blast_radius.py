from collections import deque

from netbox_plant_graph.models import AttachmentUnit, SignalLane

from ..netbox.adapters import build_object_reference
from .resolver import _build_attachment_adjacency, _build_signal_adjacency, _normalize_attachment_targets, _normalize_resolution, _normalize_signal_targets


def _serialize_target(obj):
    payload = build_object_reference(obj)
    if isinstance(obj, AttachmentUnit):
        payload['termination_point_id'] = obj.termination_point_id
    if isinstance(obj, SignalLane):
        payload['attachment_unit_id'] = obj.attachment_unit_id
    return payload


def compute_blast_radius(*, target, resolution='attachment_unit'):
    resolution = _normalize_resolution(resolution)
    if resolution == 'signal_lane':
        start_nodes = _normalize_signal_targets(target)
        adjacency = _build_signal_adjacency()
        model = SignalLane
    else:
        start_nodes = _normalize_attachment_targets(target)
        adjacency = _build_attachment_adjacency()
        model = AttachmentUnit

    start_ids = {node.pk for node in start_nodes}
    queue = deque((node_id, 0) for node_id in start_ids)
    seen = {node_id: 0 for node_id in start_ids}

    while queue:
        node_id, depth = queue.popleft()
        for neighbor_id, edge_kind, edge in adjacency.get(node_id, ()):
            if neighbor_id in seen:
                continue
            seen[neighbor_id] = depth + 1
            queue.append((neighbor_id, depth + 1))

    impacted_nodes = list(model.objects.filter(pk__in=seen.keys()))
    impacted_nodes.sort(key=lambda obj: (seen[obj.pk], obj.pk))

    return {
        'target': getattr(target, 'pk', target),
        'resolution': resolution,
        'impacted_paths': [
            {
                'object': _serialize_target(obj),
                'distance': seen[obj.pk],
            }
            for obj in impacted_nodes
        ],
        'impacted_objects': [_serialize_target(obj) for obj in impacted_nodes],
    }
