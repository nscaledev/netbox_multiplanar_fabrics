from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from netbox_plant_graph.models import Fabric, OpticalLane
from netbox_plant_graph.navigation import menu
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric


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

    def test_v2_object_list_pages_render_table_action_routes_with_rows(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        stamp = stamp_roce_4plane_mini_fabric()

        route_objects = (
            ('architecture_list', 'fabricarchitecture', fixture.architecture),
            ('fabric_list', 'fabric', stamp.fabric),
        )
        for list_route_name, object_route_name, obj in route_objects:
            with self.subTest(list_route_name=list_route_name):
                response = self.client.get(reverse(f'plugins:netbox_plant_graph:{list_route_name}'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    reverse(f'plugins:netbox_plant_graph:{object_route_name}_changelog', kwargs={'pk': obj.pk}),
                )
                self.assertContains(
                    response,
                    reverse(f'plugins:netbox_plant_graph:{object_route_name}_delete', kwargs={'pk': obj.pk}),
                )

    def test_v2_object_detail_pages_render_standard_netbox_actions(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        stamp = stamp_roce_4plane_mini_fabric()

        route_objects = (
            ('fabricarchitecture', fixture.architecture),
            ('fabric', stamp.fabric),
        )
        for route_name, obj in route_objects:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs={'pk': obj.pk}))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, reverse(f'plugins:netbox_plant_graph:{route_name}_delete', kwargs={'pk': obj.pk}))

    def test_home_seed_action_stamps_v2_proof_fabric(self):
        response = self.client.post(reverse('plugins:netbox_plant_graph:seed_v2_proof'), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Fabric.objects.filter(slug='roce-4-plane-mini-proof').exists())
        self.assertContains(response, 'Stamped RoCE 4-plane mini proof with 4 resolved paths.')

    def test_path_query_resolves_selected_lanes(self):
        result = stamp_roce_4plane_mini_fabric()
        source = result.source_lanes[0]
        destination = result.destination_lanes[0]

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:path_query'),
            {
                'source_lane': source.pk,
                'destination_lane': destination.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Path Found')
        self.assertContains(response, 'transfer_map')
        self.assertEqual(OpticalLane.objects.filter(fabric=result.fabric).count(), 8)
