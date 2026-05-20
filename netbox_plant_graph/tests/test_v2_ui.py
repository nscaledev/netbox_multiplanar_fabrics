from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site

from netbox_plant_graph.models import Endpoint, Fabric, FabricNode, OpticalLane, PathIntent, StampRun
from netbox_plant_graph.navigation import menu
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
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

    def test_stamp_template_detail_links_to_execute_workflow(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:stamptemplate', kwargs={'pk': fixture.stamp_template.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk}),
        )

    def test_stamp_template_execute_workflow_previews_and_stamps_fabric(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        get_response = self.client.get(url)

        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'roce_4plane_mini_proof')
        self.assertContains(get_response, 'optical_lane')

        post_response = self.client.post(
            url,
            {
                'fabric_name': 'Workflow stamped proof',
                'fabric_slug': 'workflow-stamped-proof',
            },
            follow=True,
        )

        self.assertEqual(post_response.status_code, 200)
        fabric = Fabric.objects.get(slug='workflow-stamped-proof')
        self.assertEqual(post_response.redirect_chain[-1][0], fabric.get_absolute_url())
        self.assertContains(post_response, 'Stamped Workflow stamped proof with 4 resolved paths.')
        stamp_run = StampRun.objects.filter(fabric=fabric).latest('created')
        self.assertContains(post_response, 'Stamp run')
        self.assertContains(post_response, f'>#{stamp_run.pk}<')
        self.assertContains(post_response, stamp_run.get_absolute_url())
        self.assertContains(post_response, fabric.get_absolute_url())

    def test_stamp_template_execute_workflow_accepts_netbox_source_anchors(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        device = Device.objects.create(name='GPU-REAL-1', device_type=device_type, role=role, site=site)
        osfp = Interface.objects.create(device=device, name='OSFP-1', type='800gbase-x-osfp')
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        response = self.client.post(
            url,
            {
                'fabric_name': 'Workflow anchored proof',
                'fabric_slug': 'workflow-anchored-proof',
                'gpu_tray_device': device.pk,
                'gpu_osfp_1_interface': osfp.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        fabric = Fabric.objects.get(slug='workflow-anchored-proof')
        node = FabricNode.objects.get(fabric=fabric, address='GB300-TRAY-1')
        endpoint = Endpoint.objects.get(fabric=fabric, address='GB300-TRAY-1.OSFP-1')
        self.assertEqual(node.source, device)
        self.assertEqual(endpoint.source, osfp)
        node_response = self.client.get(node.get_absolute_url())
        endpoint_response = self.client.get(endpoint.get_absolute_url())
        self.assertContains(node_response, 'Source')
        self.assertContains(node_response, device.name)
        self.assertContains(endpoint_response, 'Source')
        self.assertContains(endpoint_response, osfp.name)

    def test_stamp_template_execute_rejects_interface_anchor_from_different_device(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        selected_device = Device.objects.create(
            name='GPU-REAL-1',
            device_type=device_type,
            role=role,
            site=site,
        )
        other_device = Device.objects.create(
            name='GPU-REAL-2',
            device_type=device_type,
            role=role,
            site=site,
        )
        wrong_osfp = Interface.objects.create(device=other_device, name='OSFP-1', type='800gbase-x-osfp')
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        response = self.client.post(
            url,
            {
                'fabric_name': 'Invalid anchored proof',
                'fabric_slug': 'invalid-anchored-proof',
                'gpu_tray_device': selected_device.pk,
                'gpu_osfp_1_interface': wrong_osfp.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select an interface that belongs to GPU-REAL-1.')
        self.assertFalse(Fabric.objects.filter(slug='invalid-anchored-proof').exists())

    def test_stamp_template_execute_workflow_can_create_active_netbox_devices(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        gpu_device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        leaf_device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Leaf Switch', slug='leaf-switch')
        gpu_role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        leaf_role = DeviceRole.objects.create(name='Leaf Switch', slug='leaf-role', color='00ff00')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        response = self.client.post(
            url,
            {
                'fabric_name': 'Workflow created proof',
                'fabric_slug': 'workflow-created-proof',
                'create_active_devices': 'on',
                'create_site': site.pk,
                'create_gpu_device_type': gpu_device_type.pk,
                'create_gpu_role': gpu_role.pk,
                'create_leaf_device_type': leaf_device_type.pk,
                'create_leaf_role': leaf_role.pk,
                'create_name_prefix': 'workflow-created-proof',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        fabric = Fabric.objects.get(slug='workflow-created-proof')
        gpu_node = FabricNode.objects.get(fabric=fabric, address='GB300-TRAY-1')
        leaf_node = FabricNode.objects.get(fabric=fabric, address='LEAF-1')
        self.assertEqual(gpu_node.source.name, 'workflow-created-proof-gb300-tray-1')
        self.assertEqual(leaf_node.source.name, 'workflow-created-proof-leaf-1')
        self.assertEqual(Device.objects.count(), 5)
        self.assertEqual(Interface.objects.count(), 8)

    def test_stamp_template_execute_workflow_requires_creation_fields_when_enabled(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        response = self.client.post(
            url,
            {
                'fabric_name': 'Workflow invalid create proof',
                'fabric_slug': 'workflow-invalid-create-proof',
                'create_active_devices': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required when active device creation is enabled.')
        self.assertFalse(Fabric.objects.filter(slug='workflow-invalid-create-proof').exists())

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

    def test_path_query_can_filter_by_fabric(self):
        first = stamp_roce_4plane_mini_fabric(fabric_name='First Fabric', fabric_slug='first-fabric')
        second = stamp_roce_4plane_mini_fabric(fabric_name='Second Fabric', fabric_slug='second-fabric')

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:path_query'),
            {'fabric': first.fabric.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'First Fabric')
        self.assertNotContains(response, f'value="{second.source_lanes[0].pk}"')
        self.assertNotContains(response, f'value="{second.destination_lanes[0].pk}"')
