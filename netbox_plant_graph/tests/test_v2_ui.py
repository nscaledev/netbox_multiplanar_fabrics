from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from netbox_plant_graph.navigation import menu


@override_settings(ALLOWED_HOSTS=['localhost', 'testserver'])
class V2UITestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin',
            password='admin',
            email='admin@example.local',
        )
        self.client = Client(HTTP_HOST='localhost')
        self.client.force_login(self.user)

    def test_plugin_menu_is_registered_as_single_netbox_menu_resource(self):
        self.assertEqual(menu.label, 'Multiplanar Fabrics')

        group_labels = [group.label for group in menu.groups]
        self.assertIn('Multi-planar v2', group_labels)

        item_labels = [
            item.link_text
            for group in menu.groups
            for item in group.items
        ]
        self.assertEqual(item_labels, ['Overview', 'Fabrics', 'Architectures', 'Path Query'])

    def test_v2_ui_routes_render(self):
        route_names = (
            'home',
            'fabric_list',
            'architecture_list',
            'path_query',
        )

        for route_name in route_names:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(f'plugins:netbox_plant_graph:{route_name}'))
                self.assertEqual(response.status_code, 200)
                self.assertIn('text/html', response['content-type'])
