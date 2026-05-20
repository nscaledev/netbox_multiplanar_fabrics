from netbox.api.serializers import NetBoxModelSerializer

from netbox_plant_graph.models import Fabric, FabricArchitecture


class FabricArchitectureSerializer(NetBoxModelSerializer):
    class Meta:
        model = FabricArchitecture
        fields = (
            'id',
            'url',
            'display',
            'name',
            'slug',
            'version',
            'status',
            'plane_count',
            'description',
            'metadata',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'slug', 'version')


class FabricSerializer(NetBoxModelSerializer):
    class Meta:
        model = Fabric
        fields = (
            'id',
            'url',
            'display',
            'architecture',
            'name',
            'slug',
            'status',
            'tenant',
            'scope_site',
            'scope_location',
            'metadata',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'slug', 'status')
