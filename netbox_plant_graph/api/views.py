from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView

from netbox_plant_graph.api.serializers import FabricArchitectureSerializer, FabricSerializer
from netbox_plant_graph.models import Fabric, FabricArchitecture


class RootView(APIRootView):
    def get_view_name(self):
        return 'multiplanar-fabrics'


class FabricArchitectureViewSet(NetBoxModelViewSet):
    queryset = FabricArchitecture.objects.all()
    serializer_class = FabricArchitectureSerializer


class FabricViewSet(NetBoxModelViewSet):
    queryset = Fabric.objects.all()
    serializer_class = FabricSerializer


class PathQueryAPIView(APIView):
    def get(self, request):
        return Response({
            'status': 'not_implemented',
            'detail': 'V2 path resolution API scaffold is installed.',
        })
