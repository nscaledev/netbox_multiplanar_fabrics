import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Site

from netbox_plant_graph.models import (
    AuditEvent,
    CableAssembly,
    Endpoint,
    Fabric,
    FabricNode,
    OperationRun,
    OpticalLane,
    PathIntent,
    Plane,
    StampRun,
    SuppressionRule,
)
from netbox_plant_graph.navigation import menu
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.resolver import resolve_optical_lane_path
from netbox_plant_graph.services.stamping import execute_stamp_template, stamp_roce_4plane_mini_fabric
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
        SuppressionRule.objects.create(
            fabric=result.fabric,
            optical_lane=result.source_lanes[-1],
            plane=result.source_lanes[-1].plane,
            status='active',
            reason='UI test suppression',
            policy_key='',
        )
        AuditEvent.objects.create(
            fabric=result.fabric,
            event_type='stamp',
            outcome='ok',
            message='UI test event',
            payload={},
        )
        OperationRun.objects.create(
            profile='generic_roce',
            status='completed',
            fabric=result.fabric,
            parameters={'source': 'ui-test'},
            result={'ok': True},
        )
        return result

    def test_plugin_menu_is_registered_as_single_netbox_menu_resource(self):
        self.assertEqual(menu.label, 'Multi-planar v2')

        group_labels = [group.label for group in menu.groups]
        self.assertIn('Multi-planar v2', group_labels)

        item_labels = [
            item.link_text
            for group in menu.groups
            for item in group.items
        ]
        for expected_item in (
            'Overview',
            'Fabrics',
            'Architectures',
            'Cable Assemblies',
            'Interface Fanout Trace',
            'Lane Workspace',
            'Policy Dashboard',
            'Coordinate Layout',
            'Operations Center',
        ):
            with self.subTest(expected_item=expected_item):
                self.assertIn(expected_item, item_labels)

    def test_v2_ui_routes_render(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        stamp_run = stamp_roce_4plane_mini_fabric()

        routes = (
            ('home', None),
            ('graph_overview', None),
            ('fabric_onboard', None),
            ('fabric_list', None),
            ('architecture_list', None),
            ('audit_dashboard', None),
            ('audit_triage', None),
            ('policy_review', None),
            ('plane_audit', None),
            ('disjointness_exception_request', None),
            ('template_library', None),
            ('path_resolver', None),
            ('path_query', None),
            ('interface_fanout_trace', None),
            ('lane_drilldown', None),
            ('lane_compare', None),
            ('blast_radius', None),
            ('lane_workspace', None),
            ('policy_dashboard', None),
            ('coordinate_layout', None),
            ('operations_center', None),
            ('fabric_operations', {'pk': stamp_run.fabric.pk}),
            ('fabric_assign_planes', {'pk': stamp_run.fabric.pk}),
            ('assembly_stamp_wizard', {'pk': fixture.stamp_template.pk}),
            ('assembly_graph_stamp_wizard', {'pk': fixture.stamp_template.pk}),
            ('assembly_template_build', {'pk': fixture.stamp_template.pk}),
            ('breakout_stamp_wizard', {'pk': fixture.stamp_template.pk}),
            ('spatial_stamp_wizard', {'pk': fixture.stamp_template.pk}),
            ('spatial_template_compose', {'pk': fixture.stamp_template.pk}),
            ('connection_template_builder', {'pk': fixture.stamp_template.pk}),
            ('rack_population_stamp_wizard', {'pk': fixture.stamp_template.pk}),
            ('deployment_plan_workflow', {'pk': stamp_run.stamp_run.pk}),
        )

        for route_name, kwargs in routes:
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs=kwargs))
                self.assertEqual(response.status_code, 200)
                self.assertIn('text/html', response['content-type'])

    def test_fabric_visibility_workflows_submit_paths(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        onboard_response = self.client.post(
            reverse('plugins:netbox_plant_graph:fabric_onboard'),
            {
                'name': 'Onboarded Fabric',
                'slug': 'onboarded-fabric',
                'architecture': fixture.architecture.pk,
                'expected_plane_count': 4,
                'tier_depth': 3,
                'disjointness_policy': 'strict',
                'default_stamp_template': fixture.stamp_template.pk,
                'activate_fabric': 'on',
            },
            follow=True,
        )
        self.assertEqual(onboard_response.status_code, 200)
        onboarded = Fabric.objects.get(slug='onboarded-fabric')
        self.assertEqual(onboarded.status, 'active')
        self.assertEqual(Plane.objects.filter(fabric=onboarded).count(), 4)

        stamped = stamp_roce_4plane_mini_fabric(
            fabric_name='Plane Assignment Fabric',
            fabric_slug='plane-assignment-fabric',
        )
        plane = Plane.objects.filter(fabric=stamped.fabric).order_by('plane_number', 'pk').first()
        endpoint = (
            Endpoint.objects.filter(
                fabric=stamped.fabric,
                parent__isnull=True,
                optical_lanes__isnull=False,
            )
            .exclude(optical_lanes__plane=plane)
            .order_by('pk')
            .distinct()
            .first()
        )
        self.assertIsNotNone(endpoint)
        self.assertIsNotNone(plane)
        assignment_response = self.client.post(
            reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': stamped.fabric.pk}),
            {
                'action': 'toggle',
                'unit_pk': endpoint.pk,
                'plane_pk': plane.pk,
            },
            follow=True,
        )
        self.assertEqual(assignment_response.status_code, 200)
        self.assertTrue(
            OpticalLane.objects.filter(fabric=stamped.fabric, endpoint=endpoint, plane=plane).exists()
        )

        operations_response = self.client.post(
            reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': stamped.fabric.pk}),
            {'action': 'rebuild'},
            follow=True,
        )
        self.assertEqual(operations_response.status_code, 200)
        self.assertTrue(
            OperationRun.objects.filter(
                fabric=stamped.fabric,
                profile='generic_roce',
            ).exists()
        )

    def test_interface_page_renders_multiplanar_extension_when_interface_is_anchored(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-interface-ext')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray-interface-ext')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray-interface-ext', color='ff0000')
        site = Site.objects.create(name='Interface Extension Site', slug='interface-extension-site', status='active')
        device = Device.objects.create(name='GPU-IFACE-EXT-1', device_type=device_type, role=role, site=site)
        interface = Interface.objects.create(device=device, name='OSFP-1', type='800gbase-x-osfp')

        execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Interface Extension Fabric',
            fabric_slug='interface-extension-fabric',
            source_bindings={
                'nodes': {
                    'GB300-TRAY-1': device,
                },
                'endpoints': {
                    'GB300-TRAY-1.OSFP-1': interface,
                },
            },
        )

        response = self.client.get(reverse('dcim:interface', kwargs={'pk': interface.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Multiplanar Fabrics')
        self.assertContains(response, 'Linked Endpoints')
        self.assertContains(response, 'Resolve Path')
        self.assertContains(response, 'Lane Drilldown')
        self.assertContains(response, 'Blast Radius')
        self.assertContains(response, reverse('plugins:netbox_plant_graph:path_resolver'))
        self.assertContains(response, 'source_registry_key=interface')
        self.assertContains(response, f'source_id={interface.pk}')
        self.assertContains(response, 'GB300-TRAY-1.OSFP-1')

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

    def test_architecture_detail_page_exposes_v2_semantics_sections(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabricarchitecture', kwargs={'pk': fixture.architecture.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Role Taxonomy')
        self.assertContains(response, 'Transfer Map Primitives')
        self.assertContains(response, 'Allocation & Inference Rules')
        self.assertContains(response, 'Stamping & Path Intent Semantics')
        self.assertContains(response, 'Architecture Semantic Metadata')
        self.assertContains(response, 'shuffle_2x2')
        self.assertContains(response, 'Ingress MPO Position')
        self.assertContains(response, 'Egress MPO Position')
        self.assertContains(response, 'End-to-End Path Tuples')
        self.assertContains(response, 'Front MPO Position')
        self.assertContains(response, 'Rear MPO Position')

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
        self.assertEqual(Interface.objects.count(), 40)

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
        self.assertContains(response, 'Path found')
        self.assertContains(response, 'transfer_map')
        self.assertContains(response, 'Cable Assembly')
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

    def test_interface_fanout_trace_renders_visual_path_for_selected_osfp(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-fanout-trace')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Tray',
            slug='gb300-tray-fanout-trace',
        )
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray-fanout-trace', color='ff0000')
        site = Site.objects.create(name='Fanout Trace Site', slug='fanout-trace-site', status='active')
        device = Device.objects.create(
            name='GPU-FANOUT-1',
            device_type=device_type,
            role=role,
            site=site,
        )
        interface = Interface.objects.create(
            device=device,
            name='OSFP-1',
            type='800gbase-x-osfp',
        )

        execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Fanout Trace Fabric',
            fabric_slug='fanout-trace-fabric',
            source_bindings={
                'nodes': {
                    'GB300-TRAY-1': device,
                },
                'endpoints': {
                    'GB300-TRAY-1.OSFP-1': interface,
                },
            },
        )

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:interface_fanout_trace'),
            {
                'device': device.pk,
                'interface': interface.pk,
                'trace': '1',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visual Fanout Trace')
        self.assertContains(response, 'OSFP-1/1')
        self.assertContains(response, 'Path found')
        self.assertContains(response, 'shuffle_2x2')
        self.assertContains(response, 'Remote Device-Interface')
        self.assertContains(response, 'LEAF-1-OSFP-1')
        self.assertContains(response, 'Consolidated (per 200G)')
        self.assertContains(response, 'data-fanout-schematic-stages')
        self.assertContains(response, 'data-fanout-trace-mode="expanded"')
        self.assertContains(response, 'data-fanout-source-title="Source: GPU-FANOUT-1-OSFP-1"')
        self.assertContains(response, 'fanout_trace.js?v=20260521-interface-group-spacing')

        schematic_paths = json.loads(response.context['aggregate_schematic_paths_json'])
        self.assertTrue(schematic_paths[0]['source_subinterface_label'])
        self.assertTrue(schematic_paths[0]['destination_interface_layer_label'])
        stage_payload = json.loads(response.context['aggregate_schematic_stage_connectors_json'])
        stage_labels = [
            [connector['endpoint_label'] for connector in stage['connectors']]
            for stage in stage_payload
        ]
        def stage_contains(*needles):
            return any(
                any(needle in label.lower() for needle in needles)
                for labels in stage_labels
                for label in labels
            )

        self.assertEqual(len(stage_labels[0]), 2)
        self.assertTrue(any(label.endswith('.MPO-2') for label in stage_labels[0]))
        self.assertTrue(stage_contains('front-mpo-02', 'front.mpo-2'))
        self.assertTrue(stage_contains('rear-mpo-02', 'rear.mpo-2'))

    def test_interface_fanout_trace_can_consolidate_by_200g_subinterface(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-fanout-consolidated')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Tray',
            slug='gb300-tray-fanout-consolidated',
        )
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray-fanout-consolidated', color='ff0000')
        site = Site.objects.create(name='Fanout Consolidated Site', slug='fanout-consolidated-site', status='active')
        device = Device.objects.create(
            name='GPU-FANOUT-CONS-1',
            device_type=device_type,
            role=role,
            site=site,
        )
        interface = Interface.objects.create(
            device=device,
            name='OSFP-1',
            type='800gbase-x-osfp',
        )

        execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Fanout Consolidated Fabric',
            fabric_slug='fanout-consolidated-fabric',
            source_bindings={
                'nodes': {
                    'GB300-TRAY-1': device,
                },
                'endpoints': {
                    'GB300-TRAY-1.OSFP-1': interface,
                },
            },
        )

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:interface_fanout_trace'),
            {
                'device': device.pk,
                'interface': interface.pk,
                'trace': '1',
                'trace_mode': 'consolidated',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visual Fanout Trace (Consolidated By 200G Sub-interface)')
        self.assertContains(response, 'Consolidated Member Paths')
        self.assertContains(response, 'lanes')

    def test_path_resolver_alias_accepts_legacy_registry_params(self):
        result = stamp_roce_4plane_mini_fabric()
        source = result.source_lanes[0]
        destination = result.destination_lanes[0]

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:path_resolver'),
            {
                'source_registry_key': 'opticallane',
                'source_id': source.pk,
                'destination_registry_key': 'opticallane',
                'destination_id': destination.pk,
                'resolution': 'signal_lane',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Path found')
        self.assertContains(response, 'transfer_map')

    def test_lane_analysis_routes_render_with_v2_lane_context(self):
        result = stamp_roce_4plane_mini_fabric()
        source = result.source_lanes[0]
        destination = result.destination_lanes[0]

        drilldown_response = self.client.get(
            reverse('plugins:netbox_plant_graph:lane_drilldown'),
            {
                'target_registry_key': 'endpoint',
                'target_id': source.endpoint.pk,
            },
        )
        self.assertEqual(drilldown_response.status_code, 200)
        self.assertContains(drilldown_response, 'Endpoints And Optical Lanes')
        self.assertContains(drilldown_response, 'Cables:')

        compare_response = self.client.get(
            reverse('plugins:netbox_plant_graph:lane_compare'),
            {
                'baseline_registry_key': 'endpoint',
                'baseline_id': source.endpoint.pk,
                'candidate_registry_key': 'endpoint',
                'candidate_id': destination.endpoint.pk,
            },
        )
        self.assertEqual(compare_response.status_code, 200)
        self.assertContains(compare_response, 'Metric Deltas')

        blast_response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'target_registry_key': 'opticallane',
                'target_id': source.pk,
                'resolution': 'signal_lane',
            },
        )
        self.assertEqual(blast_response.status_code, 200)
        self.assertContains(blast_response, 'Impacted Objects')
        self.assertContains(blast_response, 'Cables:')

        workspace_response = self.client.get(
            reverse('plugins:netbox_plant_graph:lane_workspace'),
            {
                'fabric': source.fabric.pk,
            },
        )
        self.assertEqual(workspace_response.status_code, 200)
        self.assertContains(workspace_response, 'Cable Assemblies')

    def test_blast_radius_supports_connector_unplug_failure_scenario(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'failure_mode': 'connector_unplug',
                'connector_endpoint_id': source_lane.local_mpo_endpoint_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Failure Scenario')
        self.assertContains(response, 'Impacted Endpoints')
        self.assertContains(response, 'Impacted End-To-End Paths')
        self.assertContains(response, source_lane.endpoint.address)

    def test_blast_radius_supports_cable_cut_failure_scenario(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]
        termination = source_lane.local_mpo_position.strand_terminations.select_related('strand').first()
        self.assertIsNotNone(termination)
        strand = termination.strand
        self.assertTrue(strand.cable_site_id)
        self.assertTrue(strand.cable_id)
        cable_assembly = CableAssembly.objects.get(
            site_id=strand.cable_site_id,
            cable_id=strand.cable_id,
        )

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'failure_mode': 'cable_cut',
                'cable_assembly_id': cable_assembly.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Impacted Endpoints')
        self.assertContains(response, 'Impacted End-To-End Paths')
        self.assertContains(response, cable_assembly.cable_id)

    def test_acceptance_gate_mini_fabric_workflow(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        gpu_device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        leaf_device_type = DeviceType.objects.create(manufacturer=manufacturer, model='Leaf Switch', slug='leaf-switch')
        gpu_role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        leaf_role = DeviceRole.objects.create(name='Leaf Switch', slug='leaf-role', color='00ff00')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        execute_url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        response = self.client.post(
            execute_url,
            {
                'fabric_name': 'Acceptance Fabric',
                'fabric_slug': 'acceptance-fabric',
                'create_active_devices': 'on',
                'create_site': site.pk,
                'create_gpu_device_type': gpu_device_type.pk,
                'create_gpu_role': gpu_role.pk,
                'create_leaf_device_type': leaf_device_type.pk,
                'create_leaf_role': leaf_role.pk,
                'create_name_prefix': 'acceptance-fabric',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        fabric = Fabric.objects.get(slug='acceptance-fabric')
        stamp_run = StampRun.objects.filter(fabric=fabric).latest('created')
        self.assertEqual(Cable.objects.count(), 0)
        self.assertTrue(stamp_run.result['managed_objects']['optical_lanes'])
        self.assertEqual(len(stamp_run.result['netbox_created_objects']['devices']), 5)
        self.assertEqual(len(stamp_run.result['netbox_created_objects']['interfaces']), 8)

        for source_lane in OpticalLane.objects.filter(fabric=fabric, direction='send').order_by('pk'):
            path = resolve_optical_lane_path(source=source_lane)
            self.assertTrue(path.path_found, path.error)
            self.assertIsNotNone(path.destination_lane_id)
            self.assertIn('transfer_map', [step.step_type for step in path.steps])

    def test_audit_finding_action_routes_drive_metadata_lifecycle(self):
        fabric = Fabric.objects.create(name='Audit Lifecycle Fabric', slug='audit-lifecycle-fabric')
        finding = AuditEvent.objects.create(
            fabric=fabric,
            event_type='policy_eval',
            outcome='error',
            message='Lifecycle test finding',
            payload={},
            metadata={
                'finding_status': 'open',
                'finding_type': 'cross_plane_fine_edge',
                'severity': 'error',
            },
        )

        baseline_events = AuditEvent.objects.count()

        acknowledge_response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_acknowledge', kwargs={'pk': finding.pk}),
            {'note': 'ack'},
            follow=True,
        )
        self.assertEqual(acknowledge_response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.metadata.get('finding_status'), 'acknowledged')

        remediation_response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_start_remediation', kwargs={'pk': finding.pk}),
            {'note': 'in progress'},
            follow=True,
        )
        self.assertEqual(remediation_response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.metadata.get('finding_status'), 'in_progress')

        suppress_response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_suppress', kwargs={'pk': finding.pk}),
            {'reason': 'temporary suppress', 'expires_at': '2099-01-01'},
            follow=True,
        )
        self.assertEqual(suppress_response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.metadata.get('finding_status'), 'suppressed')
        self.assertTrue(
            SuppressionRule.objects.filter(
                fabric=fabric,
                policy_key='audit_finding',
                status='active',
                revoked_at__isnull=True,
            ).exists()
        )

        unsuppress_response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_unsuppress', kwargs={'pk': finding.pk}),
            {'note': 'restored'},
            follow=True,
        )
        self.assertEqual(unsuppress_response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.metadata.get('finding_status'), 'open')
        self.assertFalse(
            SuppressionRule.objects.filter(
                fabric=fabric,
                policy_key='audit_finding',
                status='active',
                revoked_at__isnull=True,
            ).exists()
        )

        resolve_response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_resolve', kwargs={'pk': finding.pk}),
            {'resolution_summary': 'done'},
            follow=True,
        )
        self.assertEqual(resolve_response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.metadata.get('finding_status'), 'resolved')

        reopen_response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_reopen', kwargs={'pk': finding.pk}),
            {'note': 'recurred'},
            follow=True,
        )
        self.assertEqual(reopen_response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.metadata.get('finding_status'), 'open')
        self.assertGreater(AuditEvent.objects.count(), baseline_events)

    def test_disjointness_exception_request_and_lifecycle_routes(self):
        fabric = Fabric.objects.create(name='Disjointness Fabric', slug='disjointness-fabric')

        request_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_request'),
            {
                'fabric': fabric.pk,
                'exception_type': 'cross_plane_fine_edge',
                'scope_kind': 'plane_pair',
                'reason': 'Intentional crossover',
                'expires_at': '2099-01-01',
            },
            follow=True,
        )
        self.assertEqual(request_response.status_code, 200)

        rule = SuppressionRule.objects.filter(
            fabric=fabric,
            policy_key='disjointness_exception',
        ).latest('created')
        self.assertEqual(rule.status, 'pending')
        self.assertEqual((rule.metadata or {}).get('kind'), 'disjointness_exception')

        approve_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_approve', kwargs={'pk': rule.pk}),
            follow=True,
        )
        self.assertEqual(approve_response.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.status, 'active')

        expire_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_expire', kwargs={'pk': rule.pk}),
            follow=True,
        )
        self.assertEqual(expire_response.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.status, 'expired')

        reactivate_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_reactivate', kwargs={'pk': rule.pk}),
            follow=True,
        )
        self.assertEqual(reactivate_response.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.status, 'active')

    def test_assembly_template_build_workflow_persists_builder_state(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        url = reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': fixture.stamp_template.pk})

        add_a_response = self.client.post(
            url,
            {
                'action': 'add_connector',
                'side': 'A',
                'connector_number': 1,
                'label': 'A1',
                'connector_type': 'mpo-12',
                'position_count': 12,
            },
            follow=True,
        )
        self.assertEqual(add_a_response.status_code, 200)

        add_b_response = self.client.post(
            url,
            {
                'action': 'add_connector',
                'side': 'B',
                'connector_number': 1,
                'label': 'B1',
                'connector_type': 'mpo-12',
                'position_count': 12,
            },
            follow=True,
        )
        self.assertEqual(add_b_response.status_code, 200)

        fixture.stamp_template.refresh_from_db()
        builder = (fixture.stamp_template.template or {}).get('builder') or {}
        a_id = builder['a_connectors'][0]['id']
        b_id = builder['b_connectors'][0]['id']

        toggle_response = self.client.post(
            url,
            {
                'action': 'toggle_mapping',
                'a_connector_pk': a_id,
                'b_connector_pk': b_id,
            },
            follow=True,
        )
        self.assertEqual(toggle_response.status_code, 200)
        fixture.stamp_template.refresh_from_db()
        builder = (fixture.stamp_template.template or {}).get('builder') or {}
        self.assertEqual(len(builder.get('a_connectors') or []), 1)
        self.assertEqual(len(builder.get('b_connectors') or []), 1)
        self.assertEqual(len(builder.get('mappings') or []), 1)
        self.assertContains(toggle_response, 'Mapping Matrix')

    def test_stamp_wizard_routes_execute_v2_templates(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        route_slugs = (
            ('assembly_stamp_wizard', 'Wizard Assembly Fabric', 'wizard-assembly-fabric'),
            ('assembly_graph_stamp_wizard', 'Wizard Graph Fabric', 'wizard-graph-fabric'),
            ('breakout_stamp_wizard', 'Wizard Breakout Fabric', 'wizard-breakout-fabric'),
            ('spatial_stamp_wizard', 'Wizard Spatial Fabric', 'wizard-spatial-fabric'),
            ('rack_population_stamp_wizard', 'Wizard Rack Fabric', 'wizard-rack-fabric'),
        )

        for route_name, fabric_name, fabric_slug in route_slugs:
            with self.subTest(route_name=route_name):
                response = self.client.post(
                    reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs={'pk': fixture.stamp_template.pk}),
                    {
                        'fabric_name': fabric_name,
                        'fabric_slug': fabric_slug,
                    },
                    follow=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(Fabric.objects.filter(slug=fabric_slug).exists())

    def test_deployment_plan_execute_and_rollback_routes(self):
        seed = stamp_roce_4plane_mini_fabric(
            fabric_name='Deployment Plan Seed',
            fabric_slug='deployment-plan-seed',
        )
        seed_run = seed.stamp_run
        workflow_url = reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': seed_run.pk})

        workflow_response = self.client.get(workflow_url)
        self.assertEqual(workflow_response.status_code, 200)
        self.assertContains(workflow_response, 'Deployment Plan Workflow')

        execute_response = self.client.post(
            reverse('plugins:netbox_plant_graph:deployment_plan_execute', kwargs={'pk': seed_run.pk}),
            follow=True,
        )
        self.assertEqual(execute_response.status_code, 200)
        rerun = StampRun.objects.exclude(pk=seed_run.pk).latest('created')
        self.assertContains(execute_response, f'#{rerun.pk}')

        rollback_response = self.client.post(
            reverse('plugins:netbox_plant_graph:deployment_plan_rollback', kwargs={'pk': rerun.pk}),
            follow=True,
        )
        self.assertEqual(rollback_response.status_code, 200)
        rerun.refresh_from_db()
        self.assertEqual(rerun.status, 'failed')
        self.assertIn('rollback', rerun.metadata or {})
