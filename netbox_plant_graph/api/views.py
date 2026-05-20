from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView

from netbox_plant_graph.api import serializers as api_serializers
from netbox_plant_graph.models import OpticalLane
from netbox_plant_graph.services.resolver import resolve_optical_lane_path
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
    queryset = OpticalLane.objects.all()

    def get(self, request):
        source_lane_id = request.query_params.get('source_lane')
        destination_lane_id = request.query_params.get('destination_lane')
        if not source_lane_id:
            return Response(
                {'detail': 'source_lane query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        source_lane = OpticalLane.objects.filter(pk=source_lane_id).first()
        if source_lane is None:
            return Response(
                {'detail': f'Source lane {source_lane_id!r} was not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        destination_lane = None
        if destination_lane_id:
            destination_lane = OpticalLane.objects.filter(pk=destination_lane_id).first()
            if destination_lane is None:
                return Response(
                    {'detail': f'Destination lane {destination_lane_id!r} was not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        resolved_path = resolve_optical_lane_path(source=source_lane, destination=destination_lane)
        return Response({
            'path_found': resolved_path.path_found,
            'source_lane_id': resolved_path.source_lane_id,
            'destination_lane_id': resolved_path.destination_lane_id,
            'error': resolved_path.error,
            'steps': [
                {
                    'step_type': step.step_type,
                    'object_type': step.object_type,
                    'object_id': step.object_id,
                    'label': step.label,
                    'metadata': step.metadata,
                }
                for step in resolved_path.steps
            ],
        })


__all__ = ('RootView', 'PathQueryAPIView') + tuple(spec.viewset_name for spec in V2_OBJECT_SPECS)
