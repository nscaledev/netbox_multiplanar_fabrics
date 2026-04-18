from unittest import TestCase

from netbox_plant_graph.navigation import menus


class NavigationSmokeTestCase(TestCase):
    def test_menus_exist(self):
        self.assertTrue(menus)
