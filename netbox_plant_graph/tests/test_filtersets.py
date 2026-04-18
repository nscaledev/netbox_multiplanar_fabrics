from unittest import TestCase

from netbox_plant_graph.object_registry import FILTERSET_OBJECT_SPECS


class FilterSetRegistrySmokeTestCase(TestCase):
    def test_filterset_registry_is_populated(self):
        self.assertTrue(FILTERSET_OBJECT_SPECS)
