import strawberry_django

from netbox.graphql.types import NetBoxObjectType

from netbox_plant_graph.object_registry import GRAPHQL_OBJECT_SPECS

from .filters import GRAPHQL_FILTER_CLASS_MAP


def build_graphql_type_class(spec):
	namespace = {
		'__module__': __name__,
		'__doc__': f'Generated GraphQL type for {spec.model.__name__}.',
		'__object_spec__': spec,
	}
	graphql_type = type(spec.graphql.type.class_name, (NetBoxObjectType,), namespace)
	return strawberry_django.type(
		spec.model,
		fields='__all__',
		filters=GRAPHQL_FILTER_CLASS_MAP[spec.registry_key],
	)(graphql_type)


GRAPHQL_TYPE_CLASS_MAP = {}
for object_spec in GRAPHQL_OBJECT_SPECS:
	graphql_type_class = build_graphql_type_class(object_spec)
	GRAPHQL_TYPE_CLASS_MAP[object_spec.registry_key] = graphql_type_class
	globals()[object_spec.graphql.type.class_name] = graphql_type_class
