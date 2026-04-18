from rest_framework import serializers
from rest_framework.relations import HyperlinkedIdentityField
from netbox.api.serializers import NetBoxModelSerializer

from netbox_plant_graph.object_registry import API_OBJECT_SPECS


def build_serializer_class(spec):
    meta_class = type(
        'Meta',
        (),
        {
            'model': spec.model,
            'fields': spec.api.fields,
            'brief_fields': spec.api.brief_fields,
        },
    )
    return type(
        spec.api.serializer_name,
        (NetBoxModelSerializer,),
        {
            '__module__': __name__,
            'url': HyperlinkedIdentityField(view_name=spec.api.detail_view_name),
            'Meta': meta_class,
        },
    )


SERIALIZER_CLASS_MAP = {}
for object_spec in API_OBJECT_SPECS:
    serializer_class = build_serializer_class(object_spec)
    SERIALIZER_CLASS_MAP[object_spec.registry_key] = serializer_class
    globals()[object_spec.api.serializer_name] = serializer_class
