from unittest import TestCase

from netbox_plant_graph.object_registry import TABLE_OBJECT_SPECS


class TableRegistrySmokeTestCase(TestCase):
    def test_table_registry_is_populated(self):
        self.assertTrue(TABLE_OBJECT_SPECS)
