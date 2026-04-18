from netbox_plant_graph.models import SignalLane

from ..netbox.adapters import build_object_reference
from .resolver import _normalize_attachment_targets


def build_lane_drilldown(*, target, lane_index=None):
    try:
        normalized_lane_index = int(lane_index) if lane_index not in (None, '') else None
    except (TypeError, ValueError):
        normalized_lane_index = None

    attachment_units = _normalize_attachment_targets(target)
    attachment_payloads = []
    available_lane_indexes = set()
    total_signal_lanes = 0

    for attachment_unit in attachment_units:
        lane_queryset = attachment_unit.signal_lanes.order_by('lane_index', 'pk')
        if normalized_lane_index is not None:
            lane_queryset = lane_queryset.filter(lane_index=normalized_lane_index)
        lanes = list(lane_queryset)
        if not lanes:
            continue

        available_lane_indexes.update(
            SignalLane.objects.filter(attachment_unit=attachment_unit).values_list('lane_index', flat=True)
        )
        total_signal_lanes += len(lanes)
        attachment_payloads.append({
            'attachment_unit': build_object_reference(attachment_unit),
            'lane_count': len(lanes),
            'lanes': [build_object_reference(lane) for lane in lanes],
        })

    return {
        'target': build_object_reference(target),
        'lane_index': normalized_lane_index,
        'attachment_units': attachment_payloads,
        'total_attachment_units': len(attachment_payloads),
        'total_signal_lanes': total_signal_lanes,
        'available_lane_indexes': sorted(available_lane_indexes),
    }
