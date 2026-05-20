from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView
from django.views import View
from netbox.views import generic

from .filtersets import FabricArchitectureFilterSet, FabricFilterSet
from .forms import FabricArchitectureFilterForm, FabricArchitectureForm, FabricFilterForm, FabricForm
from .models import Fabric, FabricArchitecture, OpticalLane, Plane, TransferMap
from .tables import FabricArchitectureTable, FabricTable
from .services.resolver import resolve_optical_lane_path
from .services.stamping import stamp_roce_4plane_mini_fabric
from .v2_registry import get_v2_object_spec_for_model


class V2RegisteredObjectView(generic.ObjectView):
    template_name = 'netbox_plant_graph/v2_object.html'

    def get_extra_context(self, request, instance):
        spec = get_v2_object_spec_for_model(instance.__class__)
        detail_fields = []
        for field_name in spec.resolved_detail_fields:
            value = getattr(instance, field_name)
            detail_fields.append({
                'name': field_name,
                'label': instance._meta.get_field(field_name).verbose_name.title(),
                'value': value,
                'is_empty': value in (None, ''),
            })
        return {
            'object_spec': spec,
            'detail_fields': detail_fields,
        }


class HomeView(TemplateView):
    template_name = 'netbox_plant_graph/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'architecture_count': FabricArchitecture.objects.count(),
            'fabric_count': Fabric.objects.count(),
            'plane_count': Plane.objects.count(),
            'optical_lane_count': OpticalLane.objects.count(),
            'transfer_map_count': TransferMap.objects.count(),
        })
        return context


class SeedV2ProofView(View):
    def post(self, request):
        result = stamp_roce_4plane_mini_fabric()
        failures = [path for path in result.resolved_paths if not path.path_found]
        if failures:
            messages.error(request, f'Stamped {result.fabric.name}, but {len(failures)} proof paths failed.')
        else:
            messages.success(request, f'Stamped {result.fabric.name} with {len(result.resolved_paths)} resolved paths.')
        return redirect('plugins:netbox_plant_graph:home')


class FabricArchitectureListView(generic.ObjectListView):
    queryset = FabricArchitecture.objects.all()
    table = FabricArchitectureTable
    filterset = FabricArchitectureFilterSet
    filterset_form = FabricArchitectureFilterForm


class FabricArchitectureView(V2RegisteredObjectView):
    queryset = FabricArchitecture.objects.all()


class FabricArchitectureEditView(generic.ObjectEditView):
    queryset = FabricArchitecture.objects.all()
    form = FabricArchitectureForm


class FabricArchitectureDeleteView(generic.ObjectDeleteView):
    queryset = FabricArchitecture.objects.all()


class FabricArchitectureChangeLogView(generic.ObjectChangeLogView):
    queryset = FabricArchitecture.objects.all()


class FabricArchitectureJournalView(generic.ObjectJournalView):
    queryset = FabricArchitecture.objects.all()


class FabricListView(generic.ObjectListView):
    queryset = Fabric.objects.all()
    table = FabricTable
    filterset = FabricFilterSet
    filterset_form = FabricFilterForm


class FabricView(V2RegisteredObjectView):
    queryset = Fabric.objects.all()


class FabricEditView(generic.ObjectEditView):
    queryset = Fabric.objects.all()
    form = FabricForm


class FabricDeleteView(generic.ObjectDeleteView):
    queryset = Fabric.objects.all()


class FabricChangeLogView(generic.ObjectChangeLogView):
    queryset = Fabric.objects.all()


class FabricJournalView(generic.ObjectJournalView):
    queryset = Fabric.objects.all()


class PathQueryView(TemplateView):
    template_name = 'netbox_plant_graph/path_query.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        source_lanes = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'local_mpo_endpoint',
            'local_mpo_position',
            'plane',
        ).filter(direction='send').order_by('fabric__name', 'endpoint__address', 'lane_index')
        destination_lanes = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'local_mpo_endpoint',
            'local_mpo_position',
            'plane',
        ).filter(direction='receive').order_by('fabric__name', 'endpoint__address', 'lane_index')

        selected_source = None
        selected_destination = None
        resolved_path = None
        source_id = self.request.GET.get('source_lane')
        destination_id = self.request.GET.get('destination_lane')

        if source_id:
            selected_source = source_lanes.filter(pk=source_id).first()
            if selected_source is None:
                messages.error(self.request, 'Selected source lane was not found.')
            else:
                destination_queryset = destination_lanes.filter(fabric=selected_source.fabric)
                if destination_id:
                    selected_destination = destination_queryset.filter(pk=destination_id).first()
                    if selected_destination is None:
                        messages.error(self.request, 'Selected destination lane was not found in the source fabric.')
                resolved_path = resolve_optical_lane_path(
                    source=selected_source,
                    destination=selected_destination,
                )
                destination_lanes = destination_queryset

        context.update({
            'source_lanes': source_lanes,
            'destination_lanes': destination_lanes,
            'selected_source': selected_source,
            'selected_destination': selected_destination,
            'resolved_path': resolved_path,
        })
        return context
