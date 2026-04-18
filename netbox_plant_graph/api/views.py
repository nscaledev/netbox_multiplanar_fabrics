from rest_framework.routers import APIRootView
from netbox.api.viewsets import NetBoxModelViewSet

from netbox_plant_graph import filtersets as filterset_module
from netbox_plant_graph.api.serializers import SERIALIZER_CLASS_MAP
from netbox_plant_graph.object_registry import API_OBJECT_SPECS


class RootView(APIRootView):
    def get_view_name(self):
        return 'plant-graph'


def build_viewset_class(spec):
    namespace = {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'serializer_class': SERIALIZER_CLASS_MAP[spec.registry_key],
        'filterset_class': getattr(filterset_module, spec.filterset.class_name),
    }
    if spec.api.read_only:
        namespace['http_method_names'] = ['get', 'head', 'options']
    return type(
        spec.api.viewset_name,
        (NetBoxModelViewSet,),
        namespace,
    )


VIEWSET_CLASS_MAP = {}
for object_spec in API_OBJECT_SPECS:
    viewset_class = build_viewset_class(object_spec)
    VIEWSET_CLASS_MAP[object_spec.registry_key] = viewset_class
    globals()[object_spec.api.viewset_name] = viewset_class
