from unittest import TestCase

from netbox_plant_graph.navigation import OPERATIONAL_MENU_ITEMS, menus


class NavigationSmokeTestCase(TestCase):
    def test_menus_exist(self):
        self.assertTrue(menus)

    def test_operational_menu_items_are_registered(self):
        labels = {item.link_text for item in OPERATIONAL_MENU_ITEMS}

        self.assertIn('Graph Overview', labels)
        self.assertIn('Health', labels)
        self.assertIn('Path Resolver', labels)
        self.assertIn('Plane Audit', labels)
        self.assertIn('Lane Drilldown', labels)
        self.assertIn('Blast Radius', labels)
