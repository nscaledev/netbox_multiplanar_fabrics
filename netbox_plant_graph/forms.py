from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm

from .models import Fabric, FabricArchitecture


class FabricArchitectureForm(NetBoxModelForm):
    class Meta:
        model = FabricArchitecture
        fields = ('name', 'slug', 'version', 'status', 'plane_count', 'description', 'metadata')


class FabricArchitectureFilterForm(NetBoxModelFilterSetForm):
    model = FabricArchitecture


class FabricForm(NetBoxModelForm):
    class Meta:
        model = Fabric
        fields = ('architecture', 'name', 'slug', 'status', 'tenant', 'scope_site', 'scope_location', 'metadata')


class FabricFilterForm(NetBoxModelFilterSetForm):
    model = Fabric
