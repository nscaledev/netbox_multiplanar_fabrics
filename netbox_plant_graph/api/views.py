from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView

from netbox_plant_graph.api import serializers as api_serializers
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


class RootView(APIRootView):
    def get_view_name(self):
        return 'multiplanar-fabrics'


def _build_viewset(spec):
    serializer_class = getattr(api_serializers, spec.serializer_name)
    return type(
        spec.viewset_name,
        (NetBoxModelViewSet,),
        {
            'queryset': spec.model.objects.all(),
            'serializer_class': serializer_class,
        },
    )


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.viewset_name] = _build_viewset(_spec)


class PathQueryAPIView(APIView):
    def get(self, request):
        return Response({
            'status': 'not_implemented',
            'detail': 'V2 path resolution API scaffold is installed.',
        })


__all__ = ('RootView', 'PathQueryAPIView') + tuple(spec.viewset_name for spec in V2_OBJECT_SPECS)
