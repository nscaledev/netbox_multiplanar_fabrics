from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from netbox_plant_graph.models import Fabric, OpticalLane, PathIntent
from netbox_plant_graph.navigation import menu
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


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

    def _stamp_with_path_intent(self):
        result = stamp_roce_4plane_mini_fabric()
        PathIntent.objects.create(
            fabric=result.fabric,
            name='Proof path intent',
            plane=result.source_lanes[0].plane,
            source_channel=result.source_lanes[0].channel,
            destination_channel=result.destination_lanes[0].channel,
            source_endpoint=result.source_lanes[0].endpoint,
            destination_endpoint=result.destination_lanes[0].endpoint,
            selector={'pair_key': result.source_lanes[0].pair_key},
        )
        return result

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

    def test_every_v2_object_list_page_renders_table_action_routes_with_rows(self):
        self._stamp_with_path_intent()

        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                obj = spec.model.objects.first()
                object_route_name = spec.model._meta.model_name
                response = self.client.get(reverse(f'plugins:netbox_plant_graph:{object_route_name}_list'))
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    reverse(f'plugins:netbox_plant_graph:{object_route_name}_changelog', kwargs={'pk': obj.pk}),
                )
                self.assertContains(
                    response,
                    reverse(f'plugins:netbox_plant_graph:{object_route_name}_delete', kwargs={'pk': obj.pk}),
                )

    def test_every_v2_object_detail_page_renders_standard_netbox_actions(self):
        self._stamp_with_path_intent()

        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                obj = spec.model.objects.first()
                route_name = spec.model._meta.model_name
                response = self.client.get(reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs={'pk': obj.pk}))
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    reverse(f'plugins:netbox_plant_graph:{route_name}_delete', kwargs={'pk': obj.pk}),
                )

    def test_every_v2_object_add_and_edit_page_renders(self):
        self._stamp_with_path_intent()

        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                obj = spec.model.objects.first()
                route_name = spec.model._meta.model_name

                add_response = self.client.get(reverse(f'plugins:netbox_plant_graph:{route_name}_add'))
                self.assertEqual(add_response.status_code, 200)

                edit_response = self.client.get(
                    reverse(f'plugins:netbox_plant_graph:{route_name}_edit', kwargs={'pk': obj.pk})
                )
                self.assertEqual(edit_response.status_code, 200)

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
