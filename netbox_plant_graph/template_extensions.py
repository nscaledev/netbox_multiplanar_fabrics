from urllib.parse import urlencode

from django.urls import reverse
from netbox.plugins.templates import PluginTemplateExtension

from .models import AttachmentUnit, SignalLane
from .services.netbox.lookup import get_registry_key_for_object


def _build_operational_url(view_name: str, params: dict[str, object]) -> str:
    return f"{reverse(view_name)}?{urlencode(params)}"


def _default_resolution(obj) -> str:
    if isinstance(obj, SignalLane):
        return 'signal_lane'
    return 'attachment_unit'


def _supports_signal_lane_mode(obj) -> bool:
    if isinstance(obj, SignalLane):
        return True
    if isinstance(obj, AttachmentUnit):
        return obj.signal_lanes.exists()
    return False


class PlantGraphObjectBadges(PluginTemplateExtension):
    models = [
        'dcim.interface',
        'dcim.frontport',
        'dcim.rearport',
        'netbox_plant_graph.attachmentunit',
        'netbox_plant_graph.signallane',
    ]

    def right_page(self):
        obj = self.context['object']
        registry_key = get_registry_key_for_object(obj)
        if registry_key is None:
            return ''

        resolution = _default_resolution(obj)
        graph_links = {
            'path_resolver': _build_operational_url('plugins:netbox_plant_graph:path_resolver', {
                'source_registry_key': registry_key,
                'source_id': obj.pk,
                'resolution': resolution,
            }),
            'blast_radius': _build_operational_url('plugins:netbox_plant_graph:blast_radius', {
                'target_registry_key': registry_key,
                'target_id': obj.pk,
                'resolution': resolution,
            }),
        }
        if _supports_signal_lane_mode(obj):
            graph_links['signal_path_resolver'] = _build_operational_url('plugins:netbox_plant_graph:path_resolver', {
                'source_registry_key': registry_key,
                'source_id': obj.pk,
                'resolution': 'signal_lane',
            })
            graph_links['signal_blast_radius'] = _build_operational_url('plugins:netbox_plant_graph:blast_radius', {
                'target_registry_key': registry_key,
                'target_id': obj.pk,
                'resolution': 'signal_lane',
            })

        return self.render('netbox_plant_graph/includes/object_badges.html', extra_context={
            'object': obj,
            'graph_links': graph_links,
            'registry_key': registry_key,
            'default_resolution': resolution,
        })


template_extensions = [PlantGraphObjectBadges]
