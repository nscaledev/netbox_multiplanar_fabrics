import django_tables2 as tables
from netbox.tables import NetBoxTable
from netbox.tables.columns import ActionsColumn

from .object_registry import TABLE_OBJECT_SPECS


def _include_tenant(columns):
    if 'tenant' in columns:
        return columns
    return columns + ('tenant',)


def build_table_class(spec):
    row_actions = []
    if spec.view is not None and spec.view.supports_create:
        row_actions.insert(0, 'edit')
    if spec.view is not None and spec.view.supports_delete:
        insert_at = 1 if 'edit' in row_actions else 0
        row_actions.insert(insert_at, 'delete')

    meta_class = type('Meta', (NetBoxTable.Meta,), {
        'model': spec.model,
        'fields': _include_tenant(spec.table.fields),
        'default_columns': _include_tenant(spec.table.default_columns),
    })
    attrs = {
        '__module__': __name__,
        'Meta': meta_class,
        spec.table.linkify_field: tables.Column(linkify=True),
        'tenant': tables.Column(accessor='resolved_tenant', linkify=True, default='—', verbose_name='Tenant'),
        'actions': ActionsColumn(actions=tuple(row_actions)),
    }
    return type(spec.table.class_name, (NetBoxTable,), attrs)


for object_spec in TABLE_OBJECT_SPECS:
    globals()[object_spec.table.class_name] = build_table_class(object_spec)
