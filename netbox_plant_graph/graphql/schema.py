import strawberry
import strawberry_django
from strawberry.scalars import JSON
from strawberry.types import Info

from netbox_plant_graph.models import Fabric, FabricPlane
from netbox_plant_graph.object_registry import GRAPHQL_OBJECT_SPECS
from netbox_plant_graph.services import compute_blast_radius, resolve_path, run_plane_audit
from netbox_plant_graph.services.netbox.lookup import resolve_registry_object

from .types import GRAPHQL_TYPE_CLASS_MAP


def _resolve_path_query(
    info: Info,
    source_registry_key: str,
    source_id: strawberry.ID,
    destination_registry_key: str | None = None,
    destination_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    resolution: str = 'attachment_unit',
    max_depth: int = 128,
) -> JSON:
    source = resolve_registry_object(source_registry_key, source_id)
    destination = None
    if destination_registry_key and destination_id is not None:
        destination = resolve_registry_object(destination_registry_key, destination_id)
    plane = FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None
    if source is None:
        return {'error': 'source_not_found'}
    if destination_registry_key and destination is None:
        return {'error': 'destination_not_found'}
    return resolve_path(source=source, destination=destination, plane=plane, resolution=resolution, max_depth=max_depth)


def _plane_audit_query(info: Info, fabric_id: strawberry.ID | None = None, plane_ids: list[strawberry.ID] | None = None) -> JSON:
    fabric = Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None
    plane_set = [FabricPlane.objects.get(pk=int(plane_id)) for plane_id in plane_ids or ()]
    return run_plane_audit(fabric=fabric, plane_set=plane_set)


def _blast_radius_query(
    info: Info,
    target_registry_key: str,
    target_id: strawberry.ID,
    resolution: str = 'attachment_unit',
) -> JSON:
    target = resolve_registry_object(target_registry_key, target_id)
    if target is None:
        return {'error': 'target_not_found'}
    return compute_blast_radius(target=target, resolution=resolution)


def build_query_type() -> type:
    annotations = {}
    namespace = {
        '__module__': __name__,
        'resolve_path': strawberry.field(resolver=_resolve_path_query),
        'plane_audit': strawberry.field(resolver=_plane_audit_query),
        'blast_radius': strawberry.field(resolver=_blast_radius_query),
    }
    annotations['resolve_path'] = JSON
    annotations['plane_audit'] = JSON
    annotations['blast_radius'] = JSON
    for spec in GRAPHQL_OBJECT_SPECS:
        type_class = GRAPHQL_TYPE_CLASS_MAP[spec.registry_key]
        annotations[spec.graphql.detail_field_name] = type_class | None
        annotations[spec.graphql.list_field_name] = list[type_class]
        namespace[spec.graphql.detail_field_name] = strawberry_django.field()
        namespace[spec.graphql.list_field_name] = strawberry_django.field()
    namespace['__annotations__'] = annotations
    query_class = type('NetBoxPlantGraphQuery', (), namespace)
    return strawberry.type(query_class, name='Query')


NetBoxPlantGraphQuery = build_query_type()
schema = [NetBoxPlantGraphQuery]
