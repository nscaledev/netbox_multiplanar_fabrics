import strawberry_django
from strawberry.scalars import ID
from strawberry_django import FilterLookup

try:
	from strawberry_django import StrFilterLookup
except ImportError:  # pragma: no cover
	StrFilterLookup = FilterLookup

from netbox.graphql.filters import NetBoxModelFilter

from netbox_plant_graph.object_registry import GRAPHQL_OBJECT_SPECS


def build_filter_annotation(field_spec):
	if field_spec.filter_kind == 'str':
		return StrFilterLookup[str] | None
	if field_spec.filter_kind == 'bool':
		return FilterLookup[bool] | None
	if field_spec.filter_kind == 'id':
		return ID | None
	raise ValueError(f'Unsupported GraphQL filter kind: {field_spec.filter_kind}')


def build_filter_namespace(spec):
	annotations = {}
	namespace = {
		'__module__': __name__,
		'__doc__': f'Generated GraphQL filter for {spec.model.__name__}.',
		'__object_spec__': spec,
	}
	for field_spec in spec.graphql.filter.fields:
		annotations[field_spec.field_name] = build_filter_annotation(field_spec)
		namespace[field_spec.field_name] = strawberry_django.filter_field()
	namespace['__annotations__'] = annotations
	return namespace


def build_graphql_filter_class(spec):
	filter_class = type(spec.graphql.filter.class_name, (NetBoxModelFilter,), build_filter_namespace(spec))
	return strawberry_django.filter_type(spec.model, lookups=True)(filter_class)


GRAPHQL_FILTER_CLASS_MAP = {}
for object_spec in GRAPHQL_OBJECT_SPECS:
	filter_class = build_graphql_filter_class(object_spec)
	GRAPHQL_FILTER_CLASS_MAP[object_spec.registry_key] = filter_class
	globals()[object_spec.graphql.filter.class_name] = filter_class
