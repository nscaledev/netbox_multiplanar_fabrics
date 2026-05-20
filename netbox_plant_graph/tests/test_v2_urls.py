from django.test import SimpleTestCase
from django.urls import reverse


class V2URLContractTestCase(SimpleTestCase):
    def test_top_level_and_operational_urls_reverse(self):
        expected_urls = {
            'home': '/plugins/plant-graph/',
            'seed_v2_proof': '/plugins/plant-graph/seed-v2-proof/',
            'architecture_list': '/plugins/plant-graph/architectures/',
            'fabricarchitecture_list': '/plugins/plant-graph/architectures/',
            'fabric_list': '/plugins/plant-graph/fabrics/',
            'path_query': '/plugins/plant-graph/path-query/',
        }

        for route_name, expected_url in expected_urls.items():
            with self.subTest(route_name=route_name):
                self.assertEqual(reverse(f'plugins:netbox_plant_graph:{route_name}'), expected_url)

    def test_standard_netbox_object_urls_reverse_for_exposed_v2_objects(self):
        expected_urls = {
            'fabricarchitecture': '/plugins/plant-graph/architectures/1/',
            'fabricarchitecture_edit': '/plugins/plant-graph/architectures/1/edit/',
            'fabricarchitecture_delete': '/plugins/plant-graph/architectures/1/delete/',
            'fabricarchitecture_changelog': '/plugins/plant-graph/architectures/1/changelog/',
            'fabricarchitecture_journal': '/plugins/plant-graph/architectures/1/journal/',
            'fabric': '/plugins/plant-graph/fabrics/1/',
            'fabric_edit': '/plugins/plant-graph/fabrics/1/edit/',
            'fabric_delete': '/plugins/plant-graph/fabrics/1/delete/',
            'fabric_changelog': '/plugins/plant-graph/fabrics/1/changelog/',
            'fabric_journal': '/plugins/plant-graph/fabrics/1/journal/',
        }

        for route_name, expected_url in expected_urls.items():
            with self.subTest(route_name=route_name):
                self.assertEqual(reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs={'pk': 1}), expected_url)
