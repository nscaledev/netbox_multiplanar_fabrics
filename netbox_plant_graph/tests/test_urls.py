from unittest import TestCase

from django.urls import reverse

from netbox_plant_graph.urls import urlpatterns


class URLSmokeTestCase(TestCase):
    def test_urlpatterns_exist(self):
        self.assertTrue(urlpatterns)

    def test_operational_urls_reverse(self):
        self.assertEqual(reverse('plugins:netbox_plant_graph:graph_overview'), '/plugins/plant-graph/graph-overview/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:health'), '/plugins/plant-graph/health/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:path_resolver'), '/plugins/plant-graph/path-resolver/')
