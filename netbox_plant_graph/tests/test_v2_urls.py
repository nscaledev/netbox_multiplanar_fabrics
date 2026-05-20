from django.test import SimpleTestCase
from django.urls import reverse

from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


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

    def test_standard_netbox_object_urls_reverse_for_every_v2_object(self):
        suffixes = {
            '': '/1/',
            '_edit': '/1/edit/',
            '_delete': '/1/delete/',
            '_changelog': '/1/changelog/',
            '_journal': '/1/journal/',
        }
        for spec in V2_OBJECT_SPECS:
            route_names = {spec.route_slug, spec.model._meta.model_name}
            for route_name in route_names:
                with self.subTest(route_name=route_name):
                    self.assertEqual(
                        reverse(f'plugins:netbox_plant_graph:{route_name}_list'),
                        f'/plugins/plant-graph/{spec.path_prefix}/',
                    )
                    self.assertEqual(
                        reverse(f'plugins:netbox_plant_graph:{route_name}_add'),
                        f'/plugins/plant-graph/{spec.path_prefix}/add/',
                    )
                    for suffix, expected_suffix in suffixes.items():
                        self.assertEqual(
                            reverse(f'plugins:netbox_plant_graph:{route_name}{suffix}', kwargs={'pk': 1}),
                            f'/plugins/plant-graph/{spec.path_prefix}{expected_suffix}',
                        )
