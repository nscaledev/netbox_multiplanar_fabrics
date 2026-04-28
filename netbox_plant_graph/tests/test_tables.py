from unittest import TestCase

from netbox_plant_graph import tables as tables_module
from netbox_plant_graph.object_registry import TABLE_OBJECT_SPECS


class TableRegistrySmokeTestCase(TestCase):
    def test_table_registry_is_populated(self):
        self.assertTrue(TABLE_OBJECT_SPECS)

    def test_generated_tables_expose_shared_tenant_column(self):
        for spec in TABLE_OBJECT_SPECS:
            table_class = getattr(tables_module, spec.table.class_name)
            with self.subTest(spec=spec.registry_key):
                self.assertIn('tenant', table_class.base_columns)
                self.assertEqual(str(table_class.base_columns['tenant'].accessor), 'resolved_tenant')
