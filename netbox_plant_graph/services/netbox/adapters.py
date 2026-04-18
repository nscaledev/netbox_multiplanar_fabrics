from urllib.parse import urlencode

from django.urls import reverse

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


def build_object_reference(obj):
    reference = {
        'app_label': obj._meta.app_label,
        'model': obj._meta.model_name,
        'pk': obj.pk,
        'display': str(obj),
    }
    url = getattr(obj, 'get_absolute_url', None)
    if callable(url):
        reference['url'] = url()
    registry_key = get_registry_key_for_object(obj)
    if registry_key:
        reference['registry_key'] = registry_key
    if registry_key in OPERATIONAL_REGISTRY_KEYS:
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
    return reference
