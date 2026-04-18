from unittest import TestCase

from netbox_plant_graph.object_registry import API_OBJECT_SPECS


class ObjectRegistrySmokeTestCase(TestCase):
    def test_api_registry_is_populated(self):
        self.assertTrue(API_OBJECT_SPECS)
