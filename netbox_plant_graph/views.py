from django.views.generic import TemplateView
from netbox.views import generic

from .filtersets import FabricArchitectureFilterSet, FabricFilterSet
from .forms import FabricArchitectureFilterForm, FabricArchitectureForm, FabricFilterForm, FabricForm
from .models import Fabric, FabricArchitecture, OpticalLane, Plane, TransferMap
from .tables import FabricArchitectureTable, FabricTable


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


class FabricArchitectureListView(generic.ObjectListView):
    queryset = FabricArchitecture.objects.all()
    table = FabricArchitectureTable
    filterset = FabricArchitectureFilterSet
    filterset_form = FabricArchitectureFilterForm


class FabricArchitectureView(generic.ObjectView):
    queryset = FabricArchitecture.objects.all()


class FabricArchitectureEditView(generic.ObjectEditView):
    queryset = FabricArchitecture.objects.all()
    form = FabricArchitectureForm


class FabricListView(generic.ObjectListView):
    queryset = Fabric.objects.all()
    table = FabricTable
    filterset = FabricFilterSet
    filterset_form = FabricFilterForm


class FabricView(generic.ObjectView):
    queryset = Fabric.objects.all()


class FabricEditView(generic.ObjectEditView):
    queryset = Fabric.objects.all()
    form = FabricForm


class PathQueryView(TemplateView):
    template_name = 'netbox_plant_graph/path_query.html'
