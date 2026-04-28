from importlib import reload

from django.test import SimpleTestCase, override_settings

import netbox_plant_graph.navigation as navigation


def reload_navigation():
    return reload(navigation)


def group_item_labels(menu):
    return {group.label: [item.link_text for item in group.items] for group in menu.groups}


class NavigationSmokeTestCase(SimpleTestCase):
    def test_top_level_menus_are_split_into_workflows_and_tables(self):
        nav = reload_navigation()

        self.assertEqual([menu.label for menu in nav.menus], ['Plant Graph Workflows', 'Plant Graph Tables'])

    def test_workflow_menu_groups_pages_by_topic(self):
        nav = reload_navigation()
        workflow_groups = group_item_labels(nav.menus[0])

        self.assertEqual(
            workflow_groups,
            {
                'Fabric Visibility': ['Graph Overview', 'Health', 'Onboard Fabric'],
                'Lane Analysis': ['Path Resolver', 'Lane Workspace', 'Lane Drilldown', 'Lane Compare', 'Physical Cable Blast Radius'],
                'Policy & Audit': ['Policy Review', 'Plane Audit', 'Audit Dashboard', 'Audit Triage', 'Exception Request'],
            },
        )

    def test_table_menu_groups_registry_pages_by_topic(self):
        nav = reload_navigation()
        table_groups = group_item_labels(nav.menus[1])

        self.assertEqual(
            list(table_groups),
            [
                'Fabric Model',
                'Physical Topology',
                'Connectivity Mapping',
                'Policy Control',
                'Audit Records',
                'Assembly Templates',
                'Spatial Planning',
                'Deployment Planning',
                'Rack Population',
            ],
        )
        self.assertEqual(table_groups['Fabric Model'], ['Fabrics', 'Fabric Planes'])
        self.assertIn('Audit Findings', table_groups['Audit Records'])
        self.assertIn('Assembly Templates', table_groups['Assembly Templates'])
        self.assertIn('Deployment Plans', table_groups['Deployment Planning'])

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'top_level_menu': False}})
    def test_single_menu_fallback_prefixes_group_labels(self):
        nav = reload_navigation()

        self.assertEqual([menu.label for menu in nav.menus], ['Plant Graph'])
        group_labels = [group.label for group in nav.menus[0].groups]

        self.assertIn('Workflows / Fabric Visibility', group_labels)
        self.assertIn('Workflows / Lane Analysis', group_labels)
        self.assertIn('Tables / Fabric Model', group_labels)
        self.assertIn('Tables / Audit Records', group_labels)
