from netbox.api.serializers import NetBoxModelSerializer

from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


def _build_serializer(spec):
    meta = type(
        'Meta',
        (),
        {
            'model': spec.model,
            'fields': spec.api_fields,
            'brief_fields': spec.brief_fields,
        },
    )
    return type(spec.serializer_name, (NetBoxModelSerializer,), {'Meta': meta})


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.serializer_name] = _build_serializer(_spec)


__all__ = tuple(spec.serializer_name for spec in V2_OBJECT_SPECS)
