import django_tables2 as tables
from netbox.tables import NetBoxTable
from netbox.tables.columns import ActionsColumn

from .object_registry import TABLE_OBJECT_SPECS


def build_table_class(spec):
    row_actions = []
    if spec.view is not None and spec.view.supports_create:
        row_actions.insert(0, 'edit')
    if spec.view is not None and spec.view.supports_delete:
        insert_at = 1 if 'edit' in row_actions else 0
        row_actions.insert(insert_at, 'delete')

    meta_class = type('Meta', (NetBoxTable.Meta,), {
        'model': spec.model,
        'fields': spec.table.fields,
        'default_columns': spec.table.default_columns,
    })
    attrs = {
        '__module__': __name__,
        'Meta': meta_class,
        spec.table.linkify_field: tables.Column(linkify=True),
        'actions': ActionsColumn(actions=tuple(row_actions)),
    }
    return type(spec.table.class_name, (NetBoxTable,), attrs)


for object_spec in TABLE_OBJECT_SPECS:
    globals()[object_spec.table.class_name] = build_table_class(object_spec)
