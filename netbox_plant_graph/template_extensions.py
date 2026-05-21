from urllib.parse import urlencode

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from netbox.plugins.templates import PluginTemplateExtension

from .models import Endpoint, OpticalLane


def _path_with_query(route_name, params):
    filtered = {key: value for key, value in params.items() if value not in (None, '')}
    url = reverse(f'plugins:netbox_plant_graph:{route_name}')
    if not filtered:
        return url
    return f'{url}?{urlencode(filtered)}'


class InterfaceMultiplanarContext(PluginTemplateExtension):
    models = ['dcim.interface']

    def right_page(self):
        interface = self.context['object']
        request = self.context.get('request')
        source_ct = ContentType.objects.get_for_model(interface, for_concrete_model=False)

        endpoint_queryset = Endpoint.objects.filter(
            source_type=source_ct,
            source_id=interface.pk,
        ).select_related(
            'fabric',
            'node',
            'parent',
        ).order_by(
            'fabric__name',
            'address',
            'pk',
        )
        if request is not None and hasattr(endpoint_queryset, 'restrict'):
            endpoint_queryset = endpoint_queryset.restrict(request.user, 'view')
        endpoints = tuple(endpoint_queryset)

        lane_queryset = OpticalLane.objects.filter(endpoint__in=endpoints).select_related(
            'fabric',
            'endpoint',
            'endpoint__node',
            'plane',
            'local_mpo_endpoint',
            'local_mpo_position',
        ).order_by(
            'fabric__name',
            'endpoint__address',
            'lane_index',
            'direction',
            'pk',
        )
        if request is not None and hasattr(lane_queryset, 'restrict'):
            lane_queryset = lane_queryset.restrict(request.user, 'view')
        lanes = tuple(lane_queryset)

        summaries = {}
        for endpoint in endpoints:
            summary = summaries.setdefault(
                endpoint.fabric_id,
                {
                    'fabric': endpoint.fabric,
                    'endpoint_count': 0,
                    'lane_count': 0,
                    'plane_ids': set(),
                },
            )
            summary['endpoint_count'] += 1
        for lane in lanes:
            summary = summaries.setdefault(
                lane.fabric_id,
                {
                    'fabric': lane.fabric,
                    'endpoint_count': 0,
                    'lane_count': 0,
                    'plane_ids': set(),
                },
            )
            summary['lane_count'] += 1
            if lane.plane_id is not None:
                summary['plane_ids'].add(lane.plane_id)

        fabric_summaries = []
        for summary in sorted(summaries.values(), key=lambda row: (row['fabric'].name, row['fabric'].pk)):
            fabric = summary['fabric']
            fabric_summaries.append(
                {
                    'fabric': fabric,
                    'endpoint_count': summary['endpoint_count'],
                    'lane_count': summary['lane_count'],
                    'plane_count': len(summary['plane_ids']),
                    'workspace_url': _path_with_query('lane_workspace', {'fabric': fabric.pk}),
                }
            )

        return self.render(
            'netbox_plant_graph/includes/interface_multiplanar_context.html',
            extra_context={
                'interface': interface,
                'endpoints': endpoints,
                'lanes': lanes,
                'fabric_summaries': tuple(fabric_summaries),
                'has_multiplanar_linkage': bool(endpoints),
                'path_resolver_url': _path_with_query(
                    'path_resolver',
                    {
                        'source_registry_key': 'interface',
                        'source_id': interface.pk,
                        'resolution': 'signal_lane',
                    },
                ),
                'lane_drilldown_url': _path_with_query(
                    'lane_drilldown',
                    {
                        'target_registry_key': 'interface',
                        'target_id': interface.pk,
                    },
                ),
                'blast_radius_url': _path_with_query(
                    'blast_radius',
                    {
                        'target_registry_key': 'interface',
                        'target_id': interface.pk,
                        'resolution': 'signal_lane',
                    },
                ),
            },
        )


template_extensions = [InterfaceMultiplanarContext]
