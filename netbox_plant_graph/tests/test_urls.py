from unittest import TestCase

from netbox_plant_graph.urls import urlpatterns


class URLSmokeTestCase(TestCase):
    def test_urlpatterns_exist(self):
        self.assertTrue(urlpatterns)
