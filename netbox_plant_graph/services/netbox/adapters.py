from urllib.parse import urlencode

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from netbox_plant_graph.models import AttachmentUnit, PlaneMembership, SignalLane

from .lookup import get_registry_key_for_object


OPERATIONAL_REGISTRY_KEYS = {
    'interface',
    'frontport',
    'rearport',
    'attachmentunit',
    'signallane',
    'terminationpoint',
    'plantnode',
    'coarseedge',
}


def _build_operational_url(view_name: str, params: dict[str, object]) -> str:
    return f"{reverse(view_name)}?{urlencode(params)}"


def _source_chain(obj):
    """Return the graph object plus its nearest graph parents and NetBox source."""
    chain = {'object': obj, 'source': getattr(obj, 'source', None)}
    model_name = getattr(getattr(obj, '_meta', None), 'model_name', '')
    if model_name == 'signallane':
        attachment_unit = obj.attachment_unit
        termination_point = attachment_unit.termination_point
        plant_node = termination_point.plant_node
        chain.update({
            'attachment_unit': attachment_unit,
            'termination_point': termination_point,
            'plant_node': plant_node,
            'source': obj.metadata.get('source') or getattr(attachment_unit, 'source', None) or getattr(termination_point, 'source', None) or getattr(plant_node, 'source', None),
        })
    elif model_name == 'attachmentunit':
        termination_point = obj.termination_point
        plant_node = termination_point.plant_node
        chain.update({
            'attachment_unit': obj,
            'termination_point': termination_point,
            'plant_node': plant_node,
            'source': getattr(obj, 'source', None) or getattr(termination_point, 'source', None) or getattr(plant_node, 'source', None),
        })
    elif model_name == 'terminationpoint':
        plant_node = obj.plant_node
        chain.update({
            'termination_point': obj,
            'plant_node': plant_node,
            'source': getattr(obj, 'source', None) or getattr(plant_node, 'source', None),
        })
    elif model_name == 'plantnode':
        chain.update({'plant_node': obj, 'source': getattr(obj, 'source', None)})
    return chain


def _device_for_source(source):
    if source is None:
        return None
    if getattr(getattr(source, '_meta', None), 'model_name', '') == 'device':
        return source
    device = getattr(source, 'device', None)
    if device is not None:
        return device
    module = getattr(source, 'module', None)
    if module is not None:
        return getattr(module, 'device', None)
    return None


def _plane_numbers_for_member(member) -> tuple[int, ...]:
    if member is None or getattr(member, 'pk', None) is None:
        return ()
    member_type = ContentType.objects.get_for_model(member.__class__)
    return tuple(
        PlaneMembership.objects.filter(
            member_type=member_type,
            member_id=member.pk,
        )
        .order_by('plane__plane_number')
        .values_list('plane__plane_number', flat=True)
    )


def _lane_plane_summary(attachment_unit: AttachmentUnit) -> tuple[int, ...]:
    signal_lane_type = ContentType.objects.get_for_model(SignalLane)
    lane_ids = list(attachment_unit.signal_lanes.values_list('pk', flat=True))
    if not lane_ids:
        return ()
    return tuple(
        PlaneMembership.objects.filter(
            member_type=signal_lane_type,
            member_id__in=lane_ids,
        )
        .order_by('plane__plane_number')
        .values_list('plane__plane_number', flat=True)
        .distinct()
    )


def _endpoint_context(obj) -> dict[str, object]:
    chain = _source_chain(obj)
    source = chain.get('source')
    device = _device_for_source(source)
    module = getattr(source, 'module', None) if source is not None else None
    termination_point = chain.get('termination_point')
    attachment_unit = chain.get('attachment_unit')
    plant_node = chain.get('plant_node')

    parts = []
    if device is not None:
        parts.append(str(device))
        if getattr(device, 'device_type_id', None):
            parts.append(str(device.device_type))
        if getattr(device, 'rack_id', None):
            position = getattr(device, 'position', None)
            rack_piece = f'{device.rack}'
            if position is not None:
                rack_piece = f'{rack_piece} U{position:g}'
            parts.append(rack_piece)
    elif plant_node is not None:
        parts.append(str(plant_node))

    if source is not None and source is not device:
        source_model = getattr(getattr(source, '_meta', None), 'verbose_name', 'source')
        parts.append(f'{source_model}: {source}')
    if module is not None:
        parts.append(f'module: {module}')
    if termination_point is not None:
        parts.append(f'termination: {termination_point.name}')
    if attachment_unit is not None:
        parts.append(f'attachment: {attachment_unit.name}')
    model_name = getattr(getattr(obj, '_meta', None), 'model_name', '')
    if model_name == 'signallane':
        parts.append(f'optical lane {obj.lane_index} ({obj.wavelength_nm}nm)')
        lane_planes = _plane_numbers_for_member(obj)
        if lane_planes:
            parts.append(f'plane: {", ".join(str(number) for number in lane_planes)}')
    elif model_name == 'attachmentunit':
        attachment_planes = _plane_numbers_for_member(obj)
        if attachment_planes:
            parts.append(f'planes: {", ".join(str(number) for number in attachment_planes)}')
        else:
            lane_planes = _lane_plane_summary(obj)
            if lane_planes:
                parts.append(f'optical-lane planes: {", ".join(str(number) for number in lane_planes)}')

    context = ' | '.join(part for part in parts if part)
    payload = {
        'endpoint_context': context,
        'endpoint_label': context or str(obj),
    }
    if device is not None:
        payload.update({
            'endpoint_device': str(device),
            'endpoint_device_type': str(device.device_type) if getattr(device, 'device_type_id', None) else '',
            'endpoint_role': str(device.role) if getattr(device, 'role_id', None) else '',
            'endpoint_rack': str(device.rack) if getattr(device, 'rack_id', None) else '',
        })
    if source is not None:
        payload['endpoint_source'] = str(source)
    if module is not None:
        payload['endpoint_module'] = str(module)
    if model_name == 'signallane':
        payload['wavelength_nm'] = obj.wavelength_nm
    return payload


def build_object_reference(obj):
    reference = {
        'app_label': obj._meta.app_label,
        'model': obj._meta.model_name,
        'pk': obj.pk,
        'display': str(obj),
    }
    if obj._meta.model_name in {'signallane', 'attachmentunit', 'terminationpoint', 'plantnode'}:
        reference.update(_endpoint_context(obj))
    url = getattr(obj, 'get_absolute_url', None)
    if callable(url):
        reference['url'] = url()
    registry_key = get_registry_key_for_object(obj)
    if registry_key:
        reference['registry_key'] = registry_key
    if registry_key in OPERATIONAL_REGISTRY_KEYS:
        lane_workspace_params = {'target_registry_key': registry_key, 'target_id': obj.pk}
        if registry_key == 'plantnode':
            lane_workspace_params['group_by'] = 'node'
        elif registry_key == 'coarseedge':
            lane_workspace_params['group_by'] = 'path'
        elif registry_key == 'signallane':
            lane_workspace_params['mode'] = 'lane'
            lane_workspace_params['lane_index'] = obj.lane_index
        reference['path_resolver_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:path_resolver',
            {'source_registry_key': registry_key, 'source_id': obj.pk},
        )
        reference['blast_radius_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:blast_radius',
            {'target_registry_key': registry_key, 'target_id': obj.pk},
        )
        reference['lane_drilldown_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:lane_drilldown',
            {'target_registry_key': registry_key, 'target_id': obj.pk},
        )
        reference['lane_workspace_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:lane_workspace',
            lane_workspace_params,
        )
        reference['signal_path_resolver_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:path_resolver',
            {'source_registry_key': registry_key, 'source_id': obj.pk, 'resolution': 'signal_lane'},
        )
        reference['signal_blast_radius_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:blast_radius',
            {'target_registry_key': registry_key, 'target_id': obj.pk, 'resolution': 'signal_lane'},
        )
    if registry_key == 'fabric':
        reference['health_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:health',
            {'fabric_id': obj.pk},
        )
    elif registry_key == 'fabricplane':
        reference['health_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:health',
            {'fabric_id': obj.fabric_id},
        )
        reference['lane_workspace_url'] = _build_operational_url(
            'plugins:netbox_plant_graph:lane_workspace',
            {'target_registry_key': registry_key, 'target_id': obj.pk, 'group_by': 'plane', 'plane_id': obj.plane_number},
        )
    return reference
