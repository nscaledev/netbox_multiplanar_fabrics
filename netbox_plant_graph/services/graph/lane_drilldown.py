from netbox_plant_graph.models import SignalLane

from ..netbox.adapters import build_object_reference
from .payloads import LaneAttachmentGroupPayload, LaneDrilldownPayload, ObjectReferencePayload
from .resolver import _normalize_attachment_targets


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
        endpoint_label=reference.get('endpoint_label'),
        endpoint_context=reference.get('endpoint_context'),
        endpoint_device=reference.get('endpoint_device'),
        endpoint_device_type=reference.get('endpoint_device_type'),
        endpoint_role=reference.get('endpoint_role'),
        endpoint_rack=reference.get('endpoint_rack'),
        endpoint_source=reference.get('endpoint_source'),
        endpoint_module=reference.get('endpoint_module'),
        wavelength_nm=reference.get('wavelength_nm'),
    )


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
        available_lane_indexes.update(
            SignalLane.objects.filter(attachment_unit=attachment_unit).values_list('lane_index', flat=True)
        )
        lane_queryset = attachment_unit.signal_lanes.order_by('lane_index', 'pk')
        if normalized_lane_index is not None:
            lane_queryset = lane_queryset.filter(lane_index=normalized_lane_index)
        lanes = list(lane_queryset)
        if not lanes:
            continue

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


def build_typed_lane_drilldown(*, target, lane_index=None) -> LaneDrilldownPayload:
    payload = build_lane_drilldown(target=target, lane_index=lane_index)
    return LaneDrilldownPayload(
        target=_object_reference_payload(payload['target']),
        lane_index=payload['lane_index'],
        attachment_units=tuple(
            LaneAttachmentGroupPayload(
                attachment_unit=_object_reference_payload(group['attachment_unit']),
                lane_count=group['lane_count'],
                lanes=tuple(_object_reference_payload(lane) for lane in group['lanes']),
            )
            for group in payload['attachment_units']
        ),
        total_attachment_units=payload['total_attachment_units'],
        total_signal_lanes=payload['total_signal_lanes'],
        available_lane_indexes=tuple(payload['available_lane_indexes']),
    )
