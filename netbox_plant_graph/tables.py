import django_tables2 as tables
from netbox.tables import NetBoxTable

from .v2_registry import V2_OBJECT_SPECS


def _build_table(spec):
    meta = type(
        'Meta',
        (NetBoxTable.Meta,),
        {
            'model': spec.model,
            'fields': spec.resolved_table_fields,
            'default_columns': spec.resolved_default_columns,
        },
    )
    attrs = {
        '__module__': __name__,
        'Meta': meta,
        spec.resolved_linkify_field: tables.Column(linkify=True),
    }
    return type(spec.table_name, (NetBoxTable,), attrs)


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.table_name] = _build_table(_spec)


__all__ = tuple(spec.table_name for spec in V2_OBJECT_SPECS)
