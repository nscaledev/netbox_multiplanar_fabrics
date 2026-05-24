import json

from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Module, ModuleBay, ModuleType, Site

from netbox_plant_graph.models import (
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureSourceArtifact,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
    AuditEvent,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    OperationRun,
    OpticalLane,
    OnboardingDesignItem,
    OnboardingExecutionStage,
    OnboardingObjectLink,
    OnboardingPlan,
    OnboardingPrerequisite,
    OnboardingSourceArtifact,
    OnboardingWorkspace,
    PathIntent,
    Plane,
    StampRun,
    SuppressionRule,
    TransceiverConnector,
    TransceiverConnectorProfile,
    TransceiverLaneProfile,
    TransceiverProfile,
    TransceiverProfileModuleType,
)
from netbox_plant_graph.navigation import menu
from netbox_plant_graph.services.architecture import ARCHITECTURE_SLUG, ensure_roce_4plane_shuffle_architecture
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
        self._seed_registry_only_objects(result)
        return result

    def _seed_registry_only_objects(self, result):
        architecture_workspace = ArchitectureWorkspace.objects.create(
            name='UI Architecture Workspace',
            slug='ui-architecture-workspace',
            target_slug='ui-generated-architecture',
            target_version='v1',
            base_architecture=result.fabric.architecture,
            created_by=self.user,
            owner=self.user,
        )
        architecture_artifact = ArchitectureSourceArtifact.objects.create(
            workspace=architecture_workspace,
            name='UI Architecture Source',
            artifact_type='api_payload',
            raw_payload={'source': 'ui-test'},
        )
        architecture_component = ArchitectureDesignComponent.objects.create(
            workspace=architecture_workspace,
            source_artifact=architecture_artifact,
            kind='role',
            natural_key='role:gpu-endpoint',
            desired_state={'name': 'GPU endpoint'},
            provenance={'test': 'v2-ui'},
        )
        ArchitectureValidationRun.objects.create(
            workspace=architecture_workspace,
            source_artifact=architecture_artifact,
            status='completed',
            validation_kind='schema',
            executed_by=self.user,
            summary={'ok': True},
        )
        architecture_plan = ArchitecturePublishPlan.objects.create(
            workspace=architecture_workspace,
            status='generated',
            plan_hash='ui-architecture-plan',
            generated_by=self.user,
            publish_payload={'components': [architecture_component.natural_key]},
            validation_summary={'ok': True},
            import_plan={'actions': []},
        )
        architecture_workspace.current_plan = architecture_plan
        architecture_workspace.save(update_fields=['current_plan'])

        manufacturer = Manufacturer.objects.create(name='UI Transceiver Manufacturer', slug='ui-transceiver-mfg')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='UI Transceiver Host',
            slug='ui-transceiver-host',
        )
        module_type = ModuleType.objects.create(
            manufacturer=manufacturer,
            model='UI OSFP 800G DR4',
            part_number='UI-OSFP-800G-DR4',
        )
        role = DeviceRole.objects.create(name='UI Transceiver Host', slug='ui-transceiver-host', color='3366ff')
        site = Site.objects.create(name='UI Registry Site', slug='ui-registry-site', status='active')
        device = Device.objects.create(name='ui-transceiver-host-1', device_type=device_type, role=role, site=site)
        module_bay = ModuleBay.objects.create(device=device, name='osfp1', position='1')
        module = Module.objects.create(device=device, module_bay=module_bay, module_type=module_type)
        transceiver_profile = TransceiverProfile.objects.create(
            architecture=result.fabric.architecture,
            name='UI OSFP Dual MPO Profile',
            slug='ui-osfp-dual-mpo-profile',
            status='active',
            form_factor='osfp112',
            media_type='dr4',
            aggregate_rate_gbps=800,
            channel_count=4,
            channel_rate_gbps=200,
        )
        TransceiverProfileModuleType.objects.create(
            profile=transceiver_profile,
            module_type=module_type,
            is_default=True,
            role_hint='gb300',
        )
        connector_profile = TransceiverConnectorProfile.objects.create(
            profile=transceiver_profile,
            name='MPO-1',
            connector_index=1,
            connector_family='mpo-12',
            position_count=12,
            polish='apc',
        )
        TransceiverLaneProfile.objects.create(
            connector_profile=connector_profile,
            channel_index=1,
            lane_index=1,
            direction='send',
            mpo_position=1,
            wavelength_nm='1310.000',
            nominal_rate_gbps=100,
        )
        endpoint = Endpoint.objects.filter(
            fabric=result.fabric,
            connector_kind='mpo-12',
            position_count=12,
        ).first()
        TransceiverConnector.objects.create(
            module=module,
            connector_profile=connector_profile,
            endpoint=endpoint,
        )

        onboarding_workspace = OnboardingWorkspace.objects.create(
            name='UI Onboarding Workspace',
            slug='ui-onboarding-workspace',
            target_fabric_name='UI Onboarded Fabric',
            target_fabric_slug='ui-onboarded-fabric',
            fabric=result.fabric,
            architecture=result.fabric.architecture,
            site=site,
            created_by=self.user,
            owner=self.user,
        )
        onboarding_artifact = OnboardingSourceArtifact.objects.create(
            workspace=onboarding_workspace,
            name='UI Onboarding Source',
            artifact_type='api_payload',
            raw_payload={'source': 'ui-test'},
        )
        onboarding_item = OnboardingDesignItem.objects.create(
            workspace=onboarding_workspace,
            source_artifact=onboarding_artifact,
            kind='fabric_node',
            natural_key='fabric_node:ui',
            desired_state={'name': 'ui-node'},
            provenance={'test': 'v2-ui'},
            planned_object_type=ContentType.objects.get_for_model(result.fabric, for_concrete_model=False),
            planned_object_id=result.fabric.pk,
        )
        OnboardingPrerequisite.objects.create(
            workspace=onboarding_workspace,
            design_item=onboarding_item,
            requirement_key='device-type:ui-transceiver-host',
            object_model='dcim.devicetype',
            role='transceiver-host',
            desired_identity={'slug': device_type.slug},
            status='satisfied',
            resolved_object_type=ContentType.objects.get_for_model(device_type, for_concrete_model=False),
            resolved_object_id=device_type.pk,
        )
        onboarding_plan = OnboardingPlan.objects.create(
            workspace=onboarding_workspace,
            status='draft',
            plan_hash='ui-onboarding-plan',
            generated_by=self.user,
            prerequisite_plan={'satisfied': 1},
            plan_payload={'fabric': result.fabric.slug},
        )
        onboarding_stage = OnboardingExecutionStage.objects.create(
            plan=onboarding_plan,
            stage_key='stamp',
            stage_kind='stamp',
            status='pending',
        )
        OnboardingObjectLink.objects.create(
            workspace=onboarding_workspace,
            plan=onboarding_plan,
            stage=onboarding_stage,
            design_item=onboarding_item,
            source_artifact=onboarding_artifact,
            link_kind='fabric',
            object_type=ContentType.objects.get_for_model(result.fabric, for_concrete_model=False),
            object_id=result.fabric.pk,
            label=result.fabric.name,
        )
        onboarding_workspace.current_plan = onboarding_plan
        onboarding_workspace.save(update_fields=['current_plan'])

    def test_plugin_menu_is_registered_as_single_netbox_menu_resource(self):
        self.assertEqual(menu.label, 'Multi-planar v2')

        group_labels = [group.label for group in menu.groups]
        self.assertEqual(group_labels, ['Operate', 'Build & Run', 'Audit', 'Model Inventory'])

        item_labels = [
            item.link_text
            for group in menu.groups
            for item in group.items
        ]
        for expected_item in (
            'Fabric Overview',
            'Interface Fanout Trace',
            'Path Query',
            'Physical Cable Blast Radius',
            'Architecture Workspaces',
            'Onboarding Workspaces',
            'Onboard Fabric',
            'Operations Center',
            'Import Preview',
            'Impact Reports',
            'Audit Dashboard',
            'Audit Triage',
            'Exception Requests',
            'Fabrics',
            'Architectures',
            'Cable Assemblies',
            'Lane Inventory',
            'Model Catalog',
        ):
            with self.subTest(expected_item=expected_item):
                self.assertIn(expected_item, item_labels)
        for removed_item in (
            'Graph Overview',
            'Health',
            'Path Resolver',
            'Lane Workspace',
            'Lane Drilldown',
            'Lane Compare',
            'Policy Review',
            'Plane Audit',
            'Exception Request',
            'Policy Dashboard',
            'Overview',
            'Coordinate Layout',
        ):
            with self.subTest(removed_item=removed_item):
                self.assertNotIn(removed_item, item_labels)
        self.assertEqual(len(item_labels), len(set(item_labels)))

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
            ('import_preview', None),
            ('impact_reports', None),
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
        self.assertContains(response, 'End-to-End Path Tuples')
        self.assertContains(response, 'Front MPO Position')
        self.assertContains(response, 'Rear MPO Position')
        self.assertContains(response, 'Schema Validation & Compatibility')
        self.assertContains(response, 'Built-in Compatibility')
        self.assertContains(response, 'Channel Map Rows')

    def test_architecture_detail_page_renders_malformed_schema_errors(self):
        architecture = FabricArchitecture.objects.create(
            name='Malformed Architecture',
            slug='malformed-architecture',
            version='v9',
            status='active',
            plane_count=4,
            metadata={},
        )

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabricarchitecture', kwargs={'pk': architecture.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Schema Validation & Compatibility')
        self.assertContains(response, 'Invalid')
        self.assertContains(response, 'persisted_architecture')

    def test_audit_dashboard_can_persist_topology_integrity_run(self):
        stamped = stamp_roce_4plane_mini_fabric()
        source_lane = stamped.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=source_lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.create(
            fabric=stamped.fabric,
            endpoint=source_lane.endpoint,
            plane=source_lane.plane,
            local_mpo_endpoint=source_lane.local_mpo_endpoint,
            local_mpo_position=dark_position,
            lane_index=99,
            local_mpo_index=source_lane.local_mpo_index,
            direction='send',
            wavelength_nm=source_lane.wavelength_nm,
            pair_key='audit-dark-position',
        )

        response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_dashboard'),
            {'fabric_id': stamped.fabric.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Topology Integrity Status By Fabric')
        self.assertContains(response, 'Latest Topology Integrity Drilldown')
        self.assertTrue(OperationRun.objects.filter(fabric=stamped.fabric, profile='topology_integrity').exists())
        self.assertTrue(
            any(
                (event.metadata or {}).get('topology_integrity')
                for event in AuditEvent.objects.filter(fabric=stamped.fabric, event_type='policy_eval')
            )
        )
        self.assertContains(response, 'dark_mpo_position_usage')
        self.assertContains(response, 'Triage Queue')

    def test_import_preview_dry_run_renders_row_level_conflict(self):
        response = self.client.post(
            reverse('plugins:netbox_plant_graph:import_preview'),
            {
                'payload_json': json.dumps({'items': [{'kind': 'unsupported_kind', 'name': 'bad'}]}),
                'action': 'preview',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Plan Summary')
        self.assertContains(response, 'Row Diffs & Conflicts')
        self.assertContains(response, 'unsupported kind')

    def test_import_preview_renders_architecture_preflight_gate(self):
        response = self.client.post(
            reverse('plugins:netbox_plant_graph:import_preview'),
            {
                'payload_json': json.dumps(
                    {
                        'architecture_slug': ARCHITECTURE_SLUG,
                        'architecture_version': 'v9',
                        'items': [{'kind': 'cable_assembly', 'site': 'missing-site', 'cable_id': 'IGNORED'}],
                    }
                ),
                'action': 'preview',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Architecture Preflight Gate')
        self.assertContains(response, 'incompatible')
        self.assertContains(response, 'architecture_version_mismatch')
        self.assertContains(response, 'architecture preflight failed')
        self.assertContains(response, 'Preflight Conflict')
        self.assertContains(response, 'separate from row-level conflicts')
        self.assertContains(response, 'No row-level diffs were found in the payload.')

    def test_import_preview_renders_provenance_dependencies_and_apply_order(self):
        site = Site.objects.create(name='Import Test Site', slug='import-test-site')
        response = self.client.post(
            reverse('plugins:netbox_plant_graph:import_preview'),
            {
                'payload_json': json.dumps(
                    {
                        'payload_version': 'v2-test',
                        'source_label': 'ui-test-import',
                        'items': [
                            {
                                'kind': 'cable_assembly',
                                'site': site.slug,
                                'cable_id': 'TRUNK-001',
                                'source_document': 'worksheet-a',
                                'source_row': 10,
                            },
                            {
                                'kind': 'cable_assembly',
                                'site': site.slug,
                                'cable_id': 'JUMPER-001',
                                'parent_cable': 'TRUNK-001',
                                'source_document': 'worksheet-a',
                                'source_row': 11,
                            },
                        ],
                    }
                ),
                'action': 'preview',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Payload Provenance')
        self.assertContains(response, 'v2-test')
        self.assertContains(response, 'ui-test-import')
        self.assertContains(response, 'Dependency Summary')
        self.assertContains(response, 'Apply Order')
        self.assertContains(response, 'Row Provenance')
        self.assertContains(response, 'source_document')
        self.assertContains(response, 'worksheet-a')
        self.assertContains(response, 'Dependency / Apply-Order Detail')
        self.assertContains(response, 'planned')

    def test_import_preview_can_save_export_and_apply_replayable_reports(self):
        site = Site.objects.create(name='Saved Import Site', slug='saved-import-site')
        payload = {
            'payload_version': 'v2-report-test',
            'source_label': 'saved-report-test',
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': site.slug,
                    'cable_id': 'SAVE-001',
                    'manufacturer': 'Acme',
                },
            ],
        }

        save_response = self.client.post(
            reverse('plugins:netbox_plant_graph:import_preview'),
            {
                'payload_json': json.dumps(payload),
                'action': 'save_preview',
            },
        )

        self.assertEqual(save_response.status_code, 200)
        self.assertContains(save_response, 'Saved dry-run import report')
        self.assertContains(save_response, 'Saved Import Reconciliation Reports')
        report_run = OperationRun.objects.get(profile='import_reconciliation')
        self.assertEqual(report_run.result['summary']['create'], 1)
        self.assertEqual(report_run.parameters['payload']['items'][0]['cable_id'], 'SAVE-001')

        export_response = self.client.get(
            reverse('plugins:netbox_plant_graph:import_report_export', kwargs={'pk': report_run.pk})
        )
        self.assertEqual(export_response.status_code, 200)
        self.assertEqual(export_response.json()['summary']['create'], 1)
        self.assertIn('attachment; filename="import-report-', export_response['Content-Disposition'])

        detail_response = self.client.get(report_run.get_absolute_url())
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Import Reconciliation Report')
        self.assertContains(detail_response, 'Download JSON')
        self.assertContains(detail_response, 'Replay Dry Run')
        self.assertContains(detail_response, 'Apply Exact Payload')

        apply_response = self.client.post(
            reverse('plugins:netbox_plant_graph:import_report_apply', kwargs={'pk': report_run.pk}),
            {'confirm_apply': 'APPLY'},
            follow=True,
        )
        self.assertEqual(apply_response.status_code, 200)
        self.assertTrue(CableAssembly.objects.filter(site=site, cable_id='SAVE-001').exists())
        self.assertTrue(
            OperationRun.objects.filter(
                profile='import_reconciliation',
                result__action='apply_replay',
                result__committed=True,
            ).exists()
        )

    def test_stamp_template_execute_workflow_previews_and_stamps_fabric(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})

        get_response = self.client.get(url)

        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'roce_4plane_mini_proof')
        self.assertContains(get_response, 'optical_lane')
        self.assertContains(get_response, 'V2.5 Operator Preview')
        self.assertContains(get_response, 'Valid to apply')
        self.assertContains(get_response, 'Errors')
        self.assertContains(get_response, 'Warnings')
        self.assertContains(get_response, 'V2.5 Architecture Gate')
        self.assertContains(get_response, 'Action Counts')
        self.assertContains(get_response, 'Recovery Posture')
        self.assertContains(get_response, 'Validation Issues')
        self.assertContains(get_response, 'Change Plan')
        self.assertContains(get_response, 'Name Pattern Samples')
        self.assertContains(get_response, 'compatible')
        self.assertContains(get_response, '0 errors')
        self.assertContains(get_response, 'architecture_gate.missing_device_type')

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
        stamp_run.refresh_from_db()
        self.assertEqual(stamp_run.metadata.get('stamp_runner'), 'v2.5')
        self.assertTrue(stamp_run.result.get('v25', {}).get('applied'))

    def test_stamp_run_detail_exposes_v25_retry_and_rollback_actions(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        response = self.client.post(
            reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk}),
            {
                'fabric_name': 'Stamp Run Control Proof',
                'fabric_slug': 'stamp-run-control-proof',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        stamp_run = StampRun.objects.get(fabric__slug='stamp-run-control-proof')

        detail_response = self.client.get(stamp_run.get_absolute_url())

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'V2.5 Stamp Run Controls')
        self.assertContains(detail_response, 'Retry Classification')
        self.assertContains(detail_response, 'already-converged')
        self.assertContains(detail_response, 'Rollback Preview')
        self.assertContains(detail_response, reverse('plugins:netbox_plant_graph:stamprun_retry_v25', kwargs={'pk': stamp_run.pk}))
        self.assertContains(detail_response, reverse('plugins:netbox_plant_graph:stamprun_rollback_v25', kwargs={'pk': stamp_run.pk}))

        rollback_guard_response = self.client.post(
            reverse('plugins:netbox_plant_graph:stamprun_rollback_v25', kwargs={'pk': stamp_run.pk}),
            {'confirm_rollback': 'nope'},
            follow=True,
        )
        self.assertEqual(rollback_guard_response.status_code, 200)
        self.assertContains(rollback_guard_response, 'Type ROLLBACK to apply compensation rollback.')

    def test_stamp_template_execute_blocks_invalid_v25_preview(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        url = reverse('plugins:netbox_plant_graph:stamptemplate_execute', kwargs={'pk': fixture.stamp_template.pk})
        Fabric.objects.create(name='Already claimed', slug='blocked-stamp')

        response = self.client.post(
            url,
            {
                'fabric_name': 'Blocked Stamp',
                'fabric_slug': 'blocked-stamp',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stamp apply is blocked until V2.5 preview errors are resolved.')
        self.assertContains(response, 'Apply is blocked.')
        self.assertContains(response, 'name_collision_risk')
        self.assertFalse(StampRun.objects.filter(fabric__slug='blocked-stamp').exists())

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

    def test_fabric_readiness_surfaces_render_existing_gate_and_architecture_state(self):
        result = stamp_roce_4plane_mini_fabric()

        operations_response = self.client.get(reverse('plugins:netbox_plant_graph:operations_center'))

        self.assertEqual(operations_response.status_code, 200)
        self.assertContains(operations_response, 'data-operations-readiness-summary')
        self.assertContains(operations_response, 'Fabric Readiness')
        self.assertContains(operations_response, result.fabric.name)
        self.assertContains(operations_response, 'Topology Integrity')
        self.assertContains(operations_response, 'Architecture Contract')
        self.assertContains(operations_response, 'Not run')
        self.assertContains(operations_response, 'compatible')
        self.assertContains(operations_response, reverse('plugins:netbox_plant_graph:audit_dashboard'))
        self.assertContains(operations_response, 'Workflow Surface Support Matrix')
        self.assertContains(operations_response, 'legacy_hidden')
        self.assertContains(operations_response, 'Run Audit')
        self.assertContains(operations_response, 'Triage')

        fabric_response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabric', kwargs={'pk': result.fabric.pk})
        )

        self.assertEqual(fabric_response.status_code, 200)
        self.assertContains(fabric_response, 'data-fabric-readiness-summary')
        self.assertContains(fabric_response, 'No persisted topology integrity run yet.')
        self.assertContains(fabric_response, 'Path Query')
        self.assertContains(fabric_response, result.fabric.architecture.get_absolute_url())

        fabric_operations_response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': result.fabric.pk})
        )

        self.assertEqual(fabric_operations_response.status_code, 200)
        self.assertContains(fabric_operations_response, 'data-fabric-readiness-summary')
        self.assertContains(fabric_operations_response, 'Architecture Contract')

        run_audit_response = self.client.post(
            reverse('plugins:netbox_plant_graph:operations_center'),
            {'action': 'run_integrity', 'fabric_id': result.fabric.pk},
            follow=True,
        )
        self.assertEqual(run_audit_response.status_code, 200)
        self.assertContains(run_audit_response, 'Persisted topology integrity run')
        self.assertTrue(OperationRun.objects.filter(fabric=result.fabric, profile='topology_integrity').exists())

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
        self.assertContains(response, 'Visual Path Trace')
        self.assertContains(response, 'data-visual-trace-component="mpf-visual-trace"')
        self.assertContains(response, 'data-fanout-schematic')
        self.assertContains(response, 'data-fanout-schematic-data')
        self.assertContains(response, 'data-fanout-schematic-stages')
        self.assertContains(response, 'data-fanout-fixture-key="path-query"')
        self.assertContains(response, 'data-fanout-toggle-visual')
        self.assertContains(response, 'data-fanout-export-svg')
        self.assertContains(response, 'Visual Trace Diagnostics')
        self.assertContains(response, 'data-fanout-diagnostics')
        self.assertContains(response, 'data-fanout-diagnostic-value="render-signature"')
        self.assertContains(response, 'data-fanout-diagnostic-value="path-count"')
        self.assertContains(response, 'data-fanout-diagnostic-value="stage-count"')
        self.assertContains(response, 'data-fanout-diagnostic-value="cable-span-count"')
        self.assertContains(response, 'CI golden regression harness pending')
        self.assertContains(response, 'fanout_trace.css?v=20260522-visual-trace-component')
        self.assertContains(response, 'fanout_trace.js?v=20260522-visual-trace-component')
        self.assertContains(response, f'"source_lane_id": {source.pk}')
        self.assertContains(response, f'"destination_lane_id": {destination.pk}')
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
        self.assertContains(response, 'data-visual-trace-component="mpf-visual-trace"')
        self.assertContains(response, 'OSFP-1/1')
        self.assertContains(response, 'Path found')
        self.assertContains(response, 'shuffle_2x2')
        self.assertContains(response, 'Remote Device-Interface')
        self.assertContains(response, 'LEAF-1-OSFP-1')
        self.assertContains(response, 'Consolidated (per 200G)')
        self.assertContains(response, 'data-fanout-schematic-stages')
        self.assertContains(response, 'data-fanout-fixture-key="interface-fanout"')
        self.assertContains(response, 'data-fanout-trace-mode="expanded"')
        self.assertContains(response, 'data-fanout-source-title="Source: GPU-FANOUT-1-OSFP-1"')
        self.assertContains(response, 'aria-controls="fanout-child-subinterfaces-body"')
        self.assertContains(response, 'aria-controls="fanout-subinterface-matrix-body"')
        self.assertContains(response, 'data-fanout-toggle-section', count=2)
        self.assertContains(response, 'data-fanout-collapsible-section-body', count=2)
        self.assertContains(response, 'data-fanout-toggle-visual')
        self.assertContains(response, 'data-fanout-visual-body')
        self.assertContains(response, 'data-fanout-export-svg')
        self.assertContains(response, 'Export SVG')
        self.assertContains(response, 'Visual Trace Diagnostics')
        self.assertContains(response, 'data-fanout-diagnostics')
        self.assertContains(response, 'data-fanout-diagnostic-value="render-signature"')
        self.assertContains(response, 'data-fanout-diagnostic-value="path-count"')
        self.assertContains(response, 'data-fanout-diagnostic-value="stage-count"')
        self.assertContains(response, 'data-fanout-diagnostic-value="cable-span-count"')
        self.assertContains(response, 'data-fanout-details-section')
        self.assertContains(response, 'Per-Strand Trace Details')
        self.assertContains(response, 'fanout_trace.css?v=20260522-visual-trace-component')
        self.assertContains(response, 'fanout_trace.js?v=20260522-visual-trace-component')
        response_html = response.content.decode()
        self.assertLess(
            response_html.index('data-fanout-details-section'),
            response_html.index('data-fanout-path-canvas'),
        )

        schematic_paths = json.loads(response.context['aggregate_schematic_paths_json'])
        self.assertTrue(schematic_paths[0]['source_subinterface_label'])
        self.assertTrue(schematic_paths[0]['source_lane_url'])
        self.assertTrue(schematic_paths[0]['source_subinterface_url'])
        self.assertTrue(schematic_paths[0]['destination_interface_layer_label'])
        self.assertTrue(schematic_paths[0]['destination_lane_url'])
        self.assertTrue(schematic_paths[0]['destination_interface_layer_url'])
        self.assertTrue(schematic_paths[0]['cable_spans'])
        self.assertTrue(schematic_paths[0]['cable_spans'][0]['cable_assembly']['label'])
        self.assertTrue(schematic_paths[0]['cable_spans'][0]['cable_assembly']['url'])
        self.assertTrue(schematic_paths[0]['connector_hops'][0]['endpoint_url'])
        self.assertTrue(schematic_paths[0]['connector_hops'][0]['position_url'])
        stage_payload = json.loads(response.context['aggregate_schematic_stage_connectors_json'])
        self.assertTrue(stage_payload[0]['connectors'][0]['endpoint_url'])
        connector_with_positions = next(
            connector for stage in stage_payload for connector in stage['connectors'] if connector['positions']
        )
        self.assertTrue(connector_with_positions['positions'][0]['url'])
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
        self.assertContains(response, 'Per-Strand Trace Details (Consolidated By 200G Sub-interface)')
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

    def test_blast_radius_supports_operator_device_cable_workflow(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-blast-operator')
        gpu_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Tray',
            slug='gb300-tray-blast-operator',
        )
        leaf_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='Leaf Switch',
            slug='leaf-switch-blast-operator',
        )
        gpu_role = DeviceRole.objects.create(name='GPU Tray Blast', slug='gpu-tray-blast', color='ff0000')
        leaf_role = DeviceRole.objects.create(name='Leaf Switch Blast', slug='leaf-role-blast', color='00ff00')
        site = Site.objects.create(name='Blast Site', slug='blast-site', status='active')

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Blast Operator Fabric',
            fabric_slug='blast-operator-fabric',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': gpu_device_type,
                'gpu_role': gpu_role,
                'leaf_device_type': leaf_device_type,
                'leaf_role': leaf_role,
                'name_prefix': 'blast-operator',
            },
        )
        source_lane = result.source_lanes[0]
        source_interface = source_lane.endpoint.source
        self.assertIsInstance(source_interface, Interface)
        device = source_interface.device
        termination = source_lane.local_mpo_position.strand_terminations.select_related('strand').first()
        cable_assembly = CableAssembly.objects.get(
            site_id=termination.strand.cable_site_id,
            cable_id=termination.strand.cable_id,
        )

        selector_response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'site': site.pk,
                'device_type': gpu_device_type.pk,
                'device_role': gpu_role.pk,
                'device_q': device.name.split('-')[0],
                'device_id': device.pk,
            },
        )

        self.assertEqual(selector_response.status_code, 200)
        self.assertContains(selector_response, 'Operator Failure Scenario Selector')
        self.assertContains(selector_response, 'Cable Assembly Disconnected/Cut')
        self.assertContains(selector_response, 'Select Cable Assemblies')
        self.assertContains(selector_response, device.name)
        self.assertContains(selector_response, source_interface.name)
        self.assertContains(selector_response, cable_assembly.cable_id)
        self.assertContains(selector_response, f'device-cable-{cable_assembly.pk}')
        self.assertContains(selector_response, 'Calculate Blast Radius')

        blast_response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'site': site.pk,
                'device_id': device.pk,
                'cable_assembly_ids': [str(cable_assembly.pk)],
            },
        )

        self.assertEqual(blast_response.status_code, 200)
        self.assertContains(blast_response, 'Impacted Device Hierarchy')
        self.assertContains(blast_response, 'Compute / endpoint devices')
        self.assertContains(blast_response, 'Leaf tier')
        self.assertContains(blast_response, device.name)
        self.assertContains(blast_response, 'FAILED')
        self.assertContains(blast_response, cable_assembly.cable_id)
        self.assertContains(blast_response, 'Save Report Snapshot')

        save_response = self.client.post(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'action': 'save_impact_report',
                'failure_mode': 'cable_cut',
                'site': site.pk,
                'device_id': device.pk,
                'cable_assembly_ids': [str(cable_assembly.pk)],
                'report_name': 'Operator cable save proof',
            },
            follow=True,
        )

        self.assertEqual(save_response.status_code, 200)
        self.assertContains(save_response, 'Impact Reports')
        self.assertContains(save_response, 'Operator cable save proof')
        self.assertTrue(
            OperationRun.objects.filter(
                profile='operational_impact',
                result__report_name='Operator cable save proof',
            ).exists()
        )

    def test_blast_radius_supports_operator_osfp_unseat_workflow(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-blast-osfp')
        gpu_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Tray',
            slug='gb300-tray-blast-osfp',
        )
        leaf_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='Leaf Switch',
            slug='leaf-switch-blast-osfp',
        )
        gpu_role = DeviceRole.objects.create(name='GPU Tray OSFP Blast', slug='gpu-tray-osfp-blast', color='ff0000')
        leaf_role = DeviceRole.objects.create(name='Leaf Switch OSFP Blast', slug='leaf-role-osfp-blast', color='00ff00')
        site = Site.objects.create(name='Blast OSFP Site', slug='blast-osfp-site', status='active')

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Blast OSFP Fabric',
            fabric_slug='blast-osfp-fabric',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': gpu_device_type,
                'gpu_role': gpu_role,
                'leaf_device_type': leaf_device_type,
                'leaf_role': leaf_role,
                'name_prefix': 'blast-osfp',
            },
        )
        source_lane = result.source_lanes[0]
        source_interface = source_lane.endpoint.source
        self.assertIsInstance(source_interface, Interface)
        device = source_interface.device

        selector_response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'failure_mode': 'osfp_transceiver_unseat',
                'site': site.pk,
                'device_id': device.pk,
            },
        )

        self.assertEqual(selector_response.status_code, 200)
        self.assertContains(selector_response, 'Unseated OSFP Transceiver')
        self.assertContains(selector_response, 'Select OSFP Transceivers')
        self.assertContains(selector_response, f'device-interface-{source_interface.pk}')
        self.assertContains(selector_response, source_interface.name)
        self.assertContains(selector_response, source_lane.local_mpo_endpoint.address)

        blast_response = self.client.get(
            reverse('plugins:netbox_plant_graph:blast_radius'),
            {
                'failure_mode': 'osfp_transceiver_unseat',
                'site': site.pk,
                'device_id': device.pk,
                'interface_ids': [str(source_interface.pk)],
            },
        )

        self.assertEqual(blast_response.status_code, 200)
        self.assertContains(blast_response, 'Impacted Device Hierarchy')
        self.assertContains(blast_response, 'Unseated OSFP Transceiver')
        self.assertContains(blast_response, source_interface.name)
        self.assertContains(blast_response, 'FAILED')

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
