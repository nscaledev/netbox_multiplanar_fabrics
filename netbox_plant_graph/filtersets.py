from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from .object_registry import FILTERSET_OBJECT_SPECS


def build_filterset_class(spec):
    meta_class = type('Meta', (), {'model': spec.model, 'fields': spec.filterset.fields})

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        query = Q()
        for lookup in spec.filterset.search_fields:
            query |= Q(**{lookup: value})
        return queryset.filter(query)

    return type(spec.filterset.class_name, (NetBoxModelFilterSet,), {
        '__module__': __name__,
        'Meta': meta_class,
        'search': search,
    })


for object_spec in FILTERSET_OBJECT_SPECS:
    globals()[object_spec.filterset.class_name] = build_filterset_class(object_spec)
