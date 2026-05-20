import django_tables2 as tables
from netbox.tables import NetBoxTable

from .models import Fabric, FabricArchitecture


class FabricArchitectureTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = FabricArchitecture
        fields = ('pk', 'id', 'name', 'slug', 'version', 'status', 'plane_count', 'description')
        default_columns = ('name', 'slug', 'version', 'status', 'plane_count')


class FabricTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = Fabric
        fields = ('pk', 'id', 'name', 'slug', 'architecture', 'status', 'tenant', 'scope_site', 'scope_location')
        default_columns = ('name', 'slug', 'architecture', 'status', 'tenant')
