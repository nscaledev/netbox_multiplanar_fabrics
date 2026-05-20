from django.http import JsonResponse
from django.views import View
from netbox.views import generic

from .filtersets import FabricArchitectureFilterSet, FabricFilterSet
from .forms import FabricArchitectureFilterForm, FabricArchitectureForm, FabricFilterForm, FabricForm
from .models import Fabric, FabricArchitecture
from .tables import FabricArchitectureTable, FabricTable


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


class PathQueryView(View):
    def get(self, request):
        return JsonResponse({
            'status': 'not_implemented',
            'detail': 'V2 path resolution endpoint scaffold is installed.',
        })
