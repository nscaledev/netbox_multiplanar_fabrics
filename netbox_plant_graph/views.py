from types import SimpleNamespace

from django.shortcuts import render
from netbox.object_actions import AddObject, BulkExport
from netbox.views import generic

from .choices import GraphResolutionChoices
from .detail_specs import DETAIL_SPECS
from . import filtersets as filterset_module
from . import forms as forms_module
from .forms import *  # noqa: F401,F403
from .models import AttachmentUnit, CoarseEdge, Fabric, FabricPlane, FineEdge, LaneMap, PlaneMembership, PlantNode, SignalLane, TerminationPoint, TransferMap
from .object_registry import VIEW_OBJECT_SPECS, get_object_spec
from .services import compute_blast_radius, resolve_path, run_plane_audit
from .services.graph.resolution import DEFAULT_RESOLUTION
from .services.netbox.lookup import get_registry_label, resolve_registry_object
from . import tables as tables_module
from .tables import *  # noqa: F401,F403


class MetadataDrivenDetailView(generic.ObjectView):
    template_name = 'netbox_plant_graph/object_detail.html'
    detail_spec = None

    def get_detail_spec(self):
        if self.detail_spec is None:
            raise AttributeError(f'{self.__class__.__name__} must define detail_spec')
        return self.detail_spec

    def build_detail_field(self, field_spec, instance):
        value = getattr(instance, field_spec.name, None)
        url = getattr(value, 'get_absolute_url', None)
        return {
            'label': field_spec.label,
            'value': value,
            'url': url() if callable(url) else None,
            'is_empty': value in (None, ''),
        }

    def get_extra_context(self, request, instance):
        detail_spec = self.get_detail_spec()
        return {
            'detail_spec': detail_spec,
            'detail_fields': [self.build_detail_field(field_spec, instance) for field_spec in detail_spec.fields],
            'object': instance,
        }


def build_generated_detail_spec(spec):
    custom_spec = DETAIL_SPECS.get(spec.registry_key)
    if custom_spec is None:
        fields = tuple(
            SimpleNamespace(name=field_name, label=str(spec.model._meta.get_field(field_name).verbose_name).title())
            for field_name in spec.api.fields
            if field_name not in {'id', 'url'}
        )
    else:
        fields = custom_spec
    return SimpleNamespace(card_title=spec.labels.singular, fields=fields)


OPERATIONAL_REGISTRY_KEYS = (
    'interface',
    'frontport',
    'rearport',
    'attachmentunit',
    'signallane',
    'terminationpoint',
    'plantnode',
    'coarseedge',
)


def _parse_int(value, default=None):
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _operational_registry_choices():
    return tuple(
        {
            'value': registry_key,
            'label': get_registry_label(registry_key),
        }
        for registry_key in OPERATIONAL_REGISTRY_KEYS
    )


def _resolution_choices():
    allowed_values = {'attachment_unit', 'signal_lane'}
    return tuple(
        {'value': value, 'label': label}
        for value, label in GraphResolutionChoices.CHOICES
        if value in allowed_values
    )


def _fabric_choices():
    return tuple(Fabric.objects.order_by('name', 'pk'))


def _plane_choices():
    return tuple(FabricPlane.objects.select_related('fabric').order_by('fabric__name', 'plane_number', 'pk'))


def _selected_fabric(fabric_id):
    if fabric_id in (None, ''):
        return Fabric.objects.order_by('name', 'pk').first()
    return Fabric.objects.filter(pk=_parse_int(fabric_id)).first()


def _selected_plane(plane_id):
    if plane_id in (None, ''):
        return None
    return FabricPlane.objects.select_related('fabric').filter(pk=_parse_int(plane_id)).first()


def _graph_counts_for_fabric(fabric):
    if fabric is None:
        return ()
    counts = (
        ('Fabric Planes', FabricPlane.objects.filter(fabric=fabric).count()),
        ('Plant Nodes', PlantNode.objects.filter(fabric=fabric).count()),
        ('Termination Points', TerminationPoint.objects.filter(plant_node__fabric=fabric).count()),
        ('Attachment Units', AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric).count()),
        ('Signal Lanes', SignalLane.objects.filter(attachment_unit__termination_point__plant_node__fabric=fabric).count()),
        ('Coarse Edges', CoarseEdge.objects.filter(a_tp__plant_node__fabric=fabric).count()),
        ('Fine Edges', FineEdge.objects.filter(a_au__termination_point__plant_node__fabric=fabric).count()),
        ('Signal-Lane Fine Edges', FineEdge.objects.filter(a_lane__attachment_unit__termination_point__plant_node__fabric=fabric).count()),
        ('Transfer Maps', TransferMap.objects.filter(owner_node__fabric=fabric).count()),
        ('Lane Maps', LaneMap.objects.filter(owner_node__fabric=fabric).count()),
        ('Plane Memberships', PlaneMembership.objects.filter(plane__fabric=fabric).count()),
    )
    return tuple({'label': label, 'value': value} for label, value in counts)


def _finding_counts(findings):
    by_severity = {}
    by_type = {}
    for finding in findings:
        severity = finding.get('severity', 'unknown')
        finding_type = finding.get('finding_type', 'unknown')
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_type[finding_type] = by_type.get(finding_type, 0) + 1
    return {
        'by_severity': tuple(sorted(by_severity.items())),
        'by_type': tuple(sorted(by_type.items())),
    }


def build_list_view_class(spec):
    actions = [BulkExport]
    if spec.view.supports_create:
        actions.insert(0, AddObject)

    return type(spec.view.list_class_name, (generic.ObjectListView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'table': getattr(tables_module, spec.table.class_name),
        'filterset': getattr(filterset_module, spec.filterset.class_name),
        'filterset_form': getattr(forms_module, spec.filter_form.class_name),
        'actions': tuple(actions),
    })


def build_detail_view_class(spec):
    return type(spec.view.detail_class_name, (MetadataDrivenDetailView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'detail_spec': build_generated_detail_spec(spec),
    })


def build_edit_view_class(spec):
    if spec.view.edit_class_name is None:
        return None
    return type(spec.view.edit_class_name, (generic.ObjectEditView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'form': getattr(forms_module, spec.form.class_name),
    })


def build_delete_view_class(spec):
    if spec.view.delete_class_name is None:
        return None
    return type(spec.view.delete_class_name, (generic.ObjectDeleteView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
    })


for object_spec in VIEW_OBJECT_SPECS:
    globals()[object_spec.view.list_class_name] = build_list_view_class(object_spec)
    globals()[object_spec.view.detail_class_name] = build_detail_view_class(object_spec)
    edit_view = build_edit_view_class(object_spec)
    if edit_view is not None:
        globals()[object_spec.view.edit_class_name] = edit_view
    delete_view = build_delete_view_class(object_spec)
    if delete_view is not None:
        globals()[object_spec.view.delete_class_name] = delete_view


class GraphOverviewView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        return render(request, 'netbox_plant_graph/graph_overview.html', {
            'page_title': 'Graph Overview',
            'fabrics': _fabric_choices(),
            'selected_fabric': selected_fabric,
            'graph_counts': _graph_counts_for_fabric(selected_fabric),
        })


class PathResolverView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        query = {
            'source_registry_key': request.GET.get('source_registry_key', 'attachmentunit'),
            'source_id': request.GET.get('source_id', ''),
            'destination_registry_key': request.GET.get('destination_registry_key', 'attachmentunit'),
            'destination_id': request.GET.get('destination_id', ''),
            'plane_id': request.GET.get('plane_id', ''),
            'resolution': request.GET.get('resolution', DEFAULT_RESOLUTION),
            'max_depth': request.GET.get('max_depth', '128'),
        }
        result = None
        error = None

        source = resolve_registry_object(query['source_registry_key'], query['source_id'])
        destination_requested = query['destination_registry_key'] or query['destination_id']
        destination = resolve_registry_object(query['destination_registry_key'], query['destination_id']) if destination_requested else None
        plane = _selected_plane(query['plane_id'])
        max_depth = _parse_int(query['max_depth'], 128) or 128

        if query['source_id']:
            if source is None:
                error = 'Select a valid source object.'
            elif destination_requested and destination is None:
                error = 'Select a valid destination object.'
            elif query['plane_id'] and plane is None:
                error = 'Select a valid plane filter.'
            else:
                result = resolve_path(
                    source=source,
                    destination=destination,
                    plane=plane,
                    resolution=query['resolution'],
                    max_depth=max_depth,
                )

        return render(request, 'netbox_plant_graph/path_detail.html', {
            'page_title': 'Path Resolver',
            'registry_choices': _operational_registry_choices(),
            'plane_choices': _plane_choices(),
            'resolution_choices': _resolution_choices(),
            'query': query,
            'result': result,
            'error': error,
        })


class PlaneAuditView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        result = run_plane_audit(fabric=selected_fabric) if selected_fabric is not None else None
        findings = result.get('findings', []) if result is not None else []
        return render(request, 'netbox_plant_graph/plane_audit.html', {
            'page_title': 'Plane Audit',
            'fabrics': _fabric_choices(),
            'selected_fabric': selected_fabric,
            'result': result,
            'finding_counts': _finding_counts(findings),
        })


class BlastRadiusView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        query = {
            'target_registry_key': request.GET.get('target_registry_key', 'attachmentunit'),
            'target_id': request.GET.get('target_id', ''),
            'resolution': request.GET.get('resolution', DEFAULT_RESOLUTION),
        }
        result = None
        error = None
        target = resolve_registry_object(query['target_registry_key'], query['target_id'])
        if query['target_id']:
            if target is None:
                error = 'Select a valid target object.'
            else:
                result = compute_blast_radius(target=target, resolution=query['resolution'])

        return render(request, 'netbox_plant_graph/blast_radius.html', {
            'page_title': 'Blast Radius',
            'registry_choices': _operational_registry_choices(),
            'resolution_choices': _resolution_choices(),
            'query': query,
            'result': result,
            'error': error,
        })
