from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from .v2_registry import V2_OBJECT_SPECS


def _build_filterset(spec):
    meta = type(
        'Meta',
        (),
        {
            'model': spec.model,
            'fields': spec.resolved_filter_fields,
        },
    )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        query = Q()
        for lookup in spec.resolved_search_fields:
            query |= Q(**{lookup: value})
        if not query:
            return queryset
        return queryset.filter(query)

    return type(
        spec.filterset_name,
        (NetBoxModelFilterSet,),
        {
            '__module__': __name__,
            'Meta': meta,
            'search': search,
        },
    )


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.filterset_name] = _build_filterset(_spec)


__all__ = tuple(spec.filterset_name for spec in V2_OBJECT_SPECS)
