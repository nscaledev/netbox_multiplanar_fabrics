from datetime import timedelta

import django_filters
from django.db.models import Q
from django.utils import timezone
from netbox.filtersets import NetBoxModelFilterSet

from .object_registry import FILTERSET_OBJECT_SPECS
from .models import Fabric


def build_filterset_class(spec):
    meta_class = type('Meta', (), {'model': spec.model, 'fields': spec.filterset.fields})

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        query = Q()
        for lookup in spec.filterset.search_fields:
            query |= Q(**{lookup: value})
        return queryset.filter(query)

    attrs = {
        '__module__': __name__,
        'Meta': meta_class,
        'search': search,
    }

    if spec.registry_key == 'auditfinding':
        def filter_min_age_days(self, queryset, name, value):
            if value in (None, ''):
                return queryset
            cutoff = timezone.now() - timedelta(days=max(int(value), 0))
            return queryset.filter(first_seen_at__isnull=False, first_seen_at__lte=cutoff)

        def filter_suppressed(self, queryset, name, value):
            if value in (None, ''):
                return queryset
            if value:
                return queryset.filter(suppressions__active=True).distinct()
            return queryset.exclude(suppressions__active=True).distinct()

        attrs.update({
            'min_age_days': django_filters.NumberFilter(method='filter_min_age_days', label='Minimum age (days)'),
            'suppressed': django_filters.BooleanFilter(method='filter_suppressed', label='Suppressed'),
            'filter_min_age_days': filter_min_age_days,
            'filter_suppressed': filter_suppressed,
        })

    if spec.registry_key == 'auditfindingevent':
        attrs.update({
            'fabric': django_filters.ModelChoiceFilter(
                field_name='finding__fabric',
                queryset=Fabric.objects.order_by('name', 'pk'),
                label='Fabric',
            ),
            'created_after': django_filters.DateTimeFilter(
                field_name='created',
                lookup_expr='gte',
                label='Created after',
            ),
            'created_before': django_filters.DateTimeFilter(
                field_name='created',
                lookup_expr='lte',
                label='Created before',
            ),
        })

    return type(spec.filterset.class_name, (NetBoxModelFilterSet,), attrs)


for object_spec in FILTERSET_OBJECT_SPECS:
    globals()[object_spec.filterset.class_name] = build_filterset_class(object_spec)
