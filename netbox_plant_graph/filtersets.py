from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from .models import Fabric, FabricArchitecture


class FabricArchitectureFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = FabricArchitecture
        fields = ('id', 'name', 'slug', 'version', 'status')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value)
            | Q(slug__icontains=value)
            | Q(version__icontains=value)
            | Q(description__icontains=value)
        )


class FabricFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = Fabric
        fields = ('id', 'name', 'slug', 'status', 'tenant', 'scope_site', 'scope_location', 'architecture')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(slug__icontains=value))
