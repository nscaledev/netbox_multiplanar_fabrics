import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleBay, ModuleType, Site
from extras.events import serialize_for_event
from utilities.api import get_serializer_for_model

from netbox_plant_graph.models import (
    AuditEvent,
    OperationRun,
    AllocationRuleSet,
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureRole,
    ArchitectureSourceArtifact,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
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
    StampTemplate,
    StrandTermination,
    SuppressionRule,
    TransceiverConnector,
    TransceiverConnectorProfile,
    TransceiverLaneProfile,
    TransceiverProfile,
    TransceiverProfileModuleType,
    TransferMap,
    TransferPattern,
    TransportChannel,
    TransportChannelPositionMap,
)
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


V2_MODELS = (
    FabricArchitecture,
    ArchitectureRole,
    TransferPattern,
    AllocationRuleSet,
    ArchitectureWorkspace,
    ArchitectureSourceArtifact,
    ArchitectureDesignComponent,
    ArchitectureValidationRun,
    ArchitecturePublishPlan,
    StampTemplate,
    Fabric,
    Plane,
    FabricNode,
    Endpoint,
    ConnectorPosition,
    TransceiverProfile,
    TransceiverProfileModuleType,
    TransceiverConnectorProfile,
    TransceiverLaneProfile,
    TransceiverConnector,
    TransportChannel,
    TransportChannelPositionMap,
    FiberSegment,
    CableAssembly,
    FiberStrand,
    StrandTermination,
    OpticalLane,
    TransferMap,
    StampRun,
    PathIntent,
    SuppressionRule,
    AuditEvent,
    OperationRun,
    OnboardingWorkspace,
    OnboardingSourceArtifact,
    OnboardingDesignItem,
    OnboardingPrerequisite,
    OnboardingPlan,
    OnboardingExecutionStage,
    OnboardingObjectLink,
)


class V2APISerializerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='api-admin',
            password='admin',
            email='api-admin@example.local',
        )

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
            reason='Test suppression',
            policy_key='',
        )
        stamp_run = StampRun.objects.filter(fabric=result.fabric).latest('created')
        AuditEvent.objects.create(
            fabric=result.fabric,
            event_type='stamp',
            outcome='ok',
            subject_type=None,
            subject_id=None,
            payload={'stamp_run_id': stamp_run.pk},
        )
        OperationRun.objects.create(
            profile='generic_roce',
            status='completed',
            fabric=result.fabric,
            parameters={'test': True},
            result={'ok': True},
        )
        architecture_workspace = ArchitectureWorkspace.objects.create(
            name='API Architecture Workspace',
            slug='api-architecture-workspace',
            target_slug='api-architecture-target',
            target_version='v1',
            base_architecture=result.fabric.architecture,
        )
        architecture_source = ArchitectureSourceArtifact.objects.create(
            workspace=architecture_workspace,
            artifact_type='api_payload',
            name='API architecture source',
            parser_key='manual_component',
            raw_payload={'kind': 'manual_note', 'name': 'API architecture note'},
        )
        ArchitectureDesignComponent.objects.create(
            workspace=architecture_workspace,
            source_artifact=architecture_source,
            kind='manual_note',
            natural_key='manual_note:api-architecture-note',
            desired_state={'kind': 'manual_note', 'name': 'API architecture note'},
            validation_status='valid',
        )
        ArchitectureValidationRun.objects.create(
            workspace=architecture_workspace,
            source_artifact=architecture_source,
            status='passed',
            validation_kind='publish_preflight',
            workspace_revision='api-architecture-revision',
            summary={'component_counts': {'manual_note': 1}},
            import_plan={'summary': {'total': 0}},
        )
        architecture_plan = ArchitecturePublishPlan.objects.create(
            workspace=architecture_workspace,
            status='generated',
            plan_hash='api-architecture-plan-hash',
            workspace_revision='api-architecture-revision',
            publish_payload={'items': []},
            validation_summary={'status': 'passed', 'issues': []},
            import_plan={'summary': {'total': 0}},
        )
        architecture_workspace.current_plan = architecture_plan
        architecture_workspace.save(update_fields=('current_plan', 'last_updated'))
        manufacturer = Manufacturer.objects.create(name='API Transceiver Manufacturer', slug='api-transceiver-mfg')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='API Transceiver Host',
            slug='api-transceiver-host',
        )
        module_type = ModuleType.objects.create(
            manufacturer=manufacturer,
            model='API OSFP 800G DR4',
            part_number='API-OSFP-800G-DR4',
        )
        role = DeviceRole.objects.create(name='API Transceiver Host', slug='api-transceiver-host', color='3366ff')
        site = Site.objects.create(name='API Registry Site', slug='api-registry-site', status='active')
        device = Device.objects.create(name='api-transceiver-host-1', device_type=device_type, role=role, site=site)
        module_bay = ModuleBay.objects.create(device=device, name='osfp1', position='1')
        module = Module.objects.create(device=device, module_bay=module_bay, module_type=module_type)
        transceiver_profile = TransceiverProfile.objects.create(
            architecture=result.fabric.architecture,
            name='API OSFP Dual MPO Profile',
            slug='api-osfp-dual-mpo-profile',
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
        TransceiverConnector.objects.create(
            module=module,
            connector_profile=connector_profile,
            endpoint=Endpoint.objects.filter(
                fabric=result.fabric,
                connector_kind='mpo-12',
                position_count=12,
            ).first(),
        )
        workspace = OnboardingWorkspace.objects.create(
            name='API Onboarding Workspace',
            slug='api-onboarding-workspace',
            target_fabric_name='API Onboarding Fabric',
            target_fabric_slug='api-onboarding-fabric',
            fabric=result.fabric,
            architecture=result.fabric.architecture,
        )
        source = OnboardingSourceArtifact.objects.create(
            workspace=workspace,
            artifact_type='api_payload',
            name='API source',
            parser_key='manual_design_item',
            raw_payload={'kind': 'manual_note', 'name': 'API note'},
        )
        design_item = OnboardingDesignItem.objects.create(
            workspace=workspace,
            source_artifact=source,
            kind='manual_note',
            natural_key='manual_note:api-note',
            desired_state={'kind': 'manual_note', 'name': 'API note'},
            validation_status='valid',
        )
        prereq = OnboardingPrerequisite.objects.create(
            workspace=workspace,
            design_item=design_item,
            requirement_key='site:target',
            object_model='dcim.site',
            role='site',
            status='deferred',
            resolution_mode='defer',
            defer_reason='API serializer fixture',
        )
        plan = OnboardingPlan.objects.create(
            workspace=workspace,
            status='generated',
            plan_hash='api-plan-hash',
            workspace_revision='api-revision',
            plan_payload={'schema': 'v2.onboarding.plan/1'},
        )
        workspace.current_plan = plan
        workspace.save(update_fields=('current_plan', 'last_updated'))
        stage = OnboardingExecutionStage.objects.create(
            plan=plan,
            stage_key='prerequisites',
            stage_kind='prerequisites',
            status='completed',
        )
        OnboardingObjectLink.objects.create(
            workspace=workspace,
            plan=plan,
            stage=stage,
            design_item=design_item,
            source_artifact=source,
            link_kind='manual_reference',
            label='API onboarding link',
        )
        return result

    def _create_workflow_finding(
        self,
        *,
        fabric,
        plane=None,
        status='open',
        severity='warning',
        finding_type='cross_plane_overlap',
        message='Workflow finding',
    ):
        now = timezone.now()
        lifecycle = {
            'status': status,
            'suppressed': status == 'suppressed',
            'first_seen_at': (now - timedelta(days=1)).isoformat(),
            'last_seen_at': now.isoformat(),
        }
        if plane is not None:
            lifecycle['plane_id'] = plane.pk
        payload = {
            'finding_type': finding_type,
            'severity': severity,
        }
        if plane is not None:
            payload['plane_id'] = plane.pk
        return AuditEvent.objects.create(
            fabric=fabric,
            event_type='policy_eval',
            outcome='warning',
            message=message,
            payload=payload,
            metadata={'finding_lifecycle': lifecycle},
        )

    def test_every_v2_model_has_event_safe_serializer(self):
        self._stamp_with_path_intent()

        for model in V2_MODELS:
            with self.subTest(model=model.__name__):
                instance = model.objects.first()
                self.assertIsNotNone(instance)
                serializer_class = get_serializer_for_model(model)
                self.assertEqual(serializer_class.Meta.model, model)

                payload = serialize_for_event(instance)

                self.assertEqual(payload['id'], instance.pk)
                self.assertIn('display', payload)

    def test_every_v2_registered_api_endpoint_renders_list_and_detail(self):
        self.client.force_login(self.user)
        self._stamp_with_path_intent()

        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                instance = spec.model.objects.first()
                self.assertIsNotNone(instance)

                list_response = self.client.get(reverse(spec.api_list_url_name))
                self.assertEqual(list_response.status_code, 200)

                detail_response = self.client.get(
                    reverse(spec.api_detail_url_name, kwargs={'pk': instance.pk})
                )
                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(detail_response.json()['id'], instance.pk)

    def test_fabric_architecture_api_exposes_fabric_class(self):
        self.client.force_login(self.user)
        architecture = FabricArchitecture.objects.create(
            name='Management Architecture',
            slug='management-architecture',
            version='v1',
            fabric_class='management',
        )

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:fabricarchitecture-detail', kwargs={'pk': architecture.pk})
        )

        self.assertEqual(response.status_code, 200)
        fabric_class = response.json()['fabric_class']
        if isinstance(fabric_class, dict):
            fabric_class = fabric_class['value']
        self.assertEqual(fabric_class, 'management')

    def test_path_query_api_resolves_selected_lanes(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        source = result.source_lanes[0]
        destination = result.destination_lanes[0]

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:path-query'),
            {
                'source_lane': source.pk,
                'destination_lane': destination.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['path_found'], payload['error'])
        self.assertEqual(payload['source_lane_id'], source.pk)
        self.assertEqual(payload['destination_lane_id'], destination.pk)
        self.assertIn('transfer_map', [step['step_type'] for step in payload['steps']])

    def test_path_query_api_requires_source_lane(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins-api:netbox_plant_graph-api:path-query'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'source_lane query parameter is required.')

    def test_post_mvp_summary_endpoints_render(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()

        suppression = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:suppression-summary'),
            {'fabric': result.fabric.pk},
        )
        timeline = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:audit-timeline'),
            {'fabric': result.fabric.pk},
        )
        operations = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:operation-runs'),
            {'fabric': result.fabric.pk},
        )

        self.assertEqual(suppression.status_code, 200)
        self.assertEqual(timeline.status_code, 200)
        self.assertEqual(operations.status_code, 200)
        self.assertGreaterEqual(len(suppression.json()), 1)
        self.assertGreaterEqual(len(timeline.json()), 1)
        self.assertGreaterEqual(len(operations.json()), 1)

    def test_workflow_summary_endpoint_filters_by_fabric_and_plane(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        planes = list(result.fabric.planes.order_by('plane_number', 'pk'))
        self.assertGreaterEqual(len(planes), 2)
        selected_plane = planes[0]
        other_plane = planes[1]

        self._create_workflow_finding(
            fabric=result.fabric,
            plane=selected_plane,
            status='open',
            severity='error',
            finding_type='cross_plane_overlap',
            message='Selected plane open finding',
        )
        self._create_workflow_finding(
            fabric=result.fabric,
            plane=selected_plane,
            status='suppressed',
            severity='warning',
            finding_type='partial_profile_mapping',
            message='Selected plane suppressed finding',
        )
        self._create_workflow_finding(
            fabric=result.fabric,
            plane=other_plane,
            status='resolved',
            severity='info',
            finding_type='resolved_probe',
            message='Other plane resolved finding',
        )

        other_fabric = Fabric.objects.create(
            name='API Workflow Other Fabric',
            slug='api-workflow-other-fabric',
            status='active',
        )
        other_fabric_plane = Plane.objects.create(fabric=other_fabric, plane_number=1, label='Other Fabric Plane 1')
        self._create_workflow_finding(
            fabric=other_fabric,
            plane=other_fabric_plane,
            status='open',
            message='Other fabric finding',
        )

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-summary'),
            {'fabric': result.fabric.pk, 'plane': selected_plane.pk},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['fabric_id'], result.fabric.pk)
        self.assertEqual(payload['plane_id'], selected_plane.pk)
        self.assertEqual(payload['total_findings'], 2)
        self.assertEqual(payload['active_findings'], 2)
        self.assertEqual(payload['resolved_findings'], 0)
        self.assertEqual(payload['suppressed_findings'], 1)

    def test_workflow_summary_endpoint_rejects_unknown_plane(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-summary'),
            {'plane': 999999},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('plane', response.json())

    def test_workflow_findings_endpoint_filters_and_validates(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        plane = result.fabric.planes.order_by('plane_number', 'pk').first()

        suppressed_event = self._create_workflow_finding(
            fabric=result.fabric,
            plane=plane,
            status='suppressed',
            message='Suppressed finding',
        )
        self._create_workflow_finding(
            fabric=result.fabric,
            plane=plane,
            status='open',
            message='Open finding',
        )

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-findings'),
            {'fabric': result.fabric.pk, 'status': 'suppressed'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['id'], suppressed_event.pk)
        self.assertEqual(payload[0]['status'], 'suppressed')

        invalid = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-findings'),
            {'suppressed': 'definitely-not-bool'},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('suppressed', invalid.json())

    def test_workflow_finding_detail_endpoint_renders_and_handles_not_found(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        plane = result.fabric.planes.order_by('plane_number', 'pk').first()
        finding_event = self._create_workflow_finding(
            fabric=result.fabric,
            plane=plane,
            status='open',
            message='Detail target finding',
        )
        AuditEvent.objects.create(
            fabric=result.fabric,
            event_type='suppression_change',
            outcome='ok',
            message='Acknowledged by operator.',
            payload={
                'finding_id': finding_event.pk,
                'finding_action': 'acknowledge',
                'old_status': 'open',
                'new_status': 'acknowledged',
            },
            metadata={},
        )

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-detail', kwargs={'pk': finding_event.pk})
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['finding']['id'], finding_event.pk)
        self.assertGreaterEqual(len(payload['transitions']), 1)
        self.assertEqual(payload['transitions'][0]['finding_id'], finding_event.pk)

        missing = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-detail', kwargs={'pk': 999999})
        )
        self.assertEqual(missing.status_code, 404)

    def test_workflow_runs_endpoint_renders_and_validates(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        other_fabric = Fabric.objects.create(
            name='API Workflow Runs Other Fabric',
            slug='api-workflow-runs-other-fabric',
            status='active',
        )
        OperationRun.objects.create(
            profile='generic_roce',
            status='completed',
            fabric=other_fabric,
            parameters={'test': 'other'},
            result={'ok': True},
        )

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-runs'),
            {'fabric': result.fabric.pk, 'limit': 1},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['fabric_id'], result.fabric.pk)

        invalid = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-runs'),
            {'limit': 0},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('limit', invalid.json())

    def test_stamps_preview_endpoint_supports_v2_stamp_template(self):
        self.client.force_login(self.user)
        self._stamp_with_path_intent()
        template = StampTemplate.objects.order_by('pk').first()
        self.assertIsNotNone(template)

        response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamps-preview'),
            data=json.dumps(
                {
                    'template_type': 'stamp_template',
                    'template_id': template.pk,
                    'parameters': {
                        'fabric_name': 'Preview Fabric',
                        'fabric_slug': 'preview-fabric',
                    },
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['template_id'], template.pk)
        self.assertEqual(payload['template_type'], 'v2_stamp_template')
        self.assertGreater(payload['proof_path_count'], 0)
        self.assertGreaterEqual(len(payload['objects_to_create']), 1)

    def test_stamps_preview_endpoint_rejects_unsupported_template_type_and_missing_template(self):
        self.client.force_login(self.user)
        self._stamp_with_path_intent()

        unsupported = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamps-preview'),
            data=json.dumps(
                {
                    'template_type': 'assembly',
                    'template_id': 1,
                    'parameters': {},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(unsupported.status_code, 400)
        self.assertIn('template_type', unsupported.json())

        missing = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamps-preview'),
            data=json.dumps(
                {
                    'template_type': 'stamp_template',
                    'template_id': 999999,
                    'parameters': {},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(missing.status_code, 404)

    def test_workflow_finding_mutation_endpoints_cover_full_lifecycle(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        plane = result.fabric.planes.order_by('plane_number', 'pk').first()
        finding = self._create_workflow_finding(
            fabric=result.fabric,
            plane=plane,
            status='open',
            message='Lifecycle target finding',
        )

        acknowledge = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-acknowledge', kwargs={'pk': finding.pk}),
            data=json.dumps({'note': 'ack'}),
            content_type='application/json',
        )
        self.assertEqual(acknowledge.status_code, 200)
        self.assertEqual(acknowledge.json()['finding']['status'], 'acknowledged')

        start = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-start-remediation', kwargs={'pk': finding.pk}),
            data=json.dumps({'note': 'start remediation'}),
            content_type='application/json',
        )
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.json()['finding']['status'], 'in_progress')

        resolve_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-resolve', kwargs={'pk': finding.pk}),
            data=json.dumps({'resolution_summary': 'fixed'}),
            content_type='application/json',
        )
        self.assertEqual(resolve_response.status_code, 200)
        self.assertEqual(resolve_response.json()['finding']['status'], 'resolved')

        reopen = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-reopen', kwargs={'pk': finding.pk}),
            data=json.dumps({'note': 'regressed'}),
            content_type='application/json',
        )
        self.assertEqual(reopen.status_code, 200)
        self.assertEqual(reopen.json()['finding']['status'], 'open')

        suppress = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-suppress', kwargs={'pk': finding.pk}),
            data=json.dumps({'reason': 'maintenance window', 'days': 1}),
            content_type='application/json',
        )
        self.assertEqual(suppress.status_code, 200)
        self.assertEqual(suppress.json()['finding']['status'], 'suppressed')

        unsuppress = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-unsuppress', kwargs={'pk': finding.pk}),
            data=json.dumps({'reason': 'maintenance complete'}),
            content_type='application/json',
        )
        self.assertEqual(unsuppress.status_code, 200)
        self.assertEqual(unsuppress.json()['finding']['status'], 'open')

    def test_workflow_finding_mutations_reject_invalid_transition(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        finding = self._create_workflow_finding(
            fabric=result.fabric,
            status='resolved',
            message='Already resolved finding',
        )

        response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-resolve', kwargs={'pk': finding.pk}),
            data=json.dumps({'resolution_summary': 'still fixed'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('detail', response.json())

    def test_disjointness_exception_mutation_endpoints(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        lane = result.source_lanes[0]

        create_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:disjointness-exception-request'),
            data=json.dumps(
                {
                    'fabric': result.fabric.pk,
                    'plane': lane.plane_id,
                    'optical_lane': lane.pk,
                    'policy_key': 'disjointness',
                    'reason': 'Operator exception request',
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(create_response.status_code, 201)
        exception_id = create_response.json()['id']
        self.assertEqual(create_response.json()['status'], 'pending')

        approve_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:disjointness-exception-approve', kwargs={'pk': exception_id}),
            data=json.dumps({'comment': 'approved'}),
            content_type='application/json',
        )
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()['status'], 'active')

        expire_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:disjointness-exception-expire', kwargs={'pk': exception_id}),
            data=json.dumps({'comment': 'expired'}),
            content_type='application/json',
        )
        self.assertEqual(expire_response.status_code, 200)
        self.assertEqual(expire_response.json()['status'], 'expired')

        reactivate_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:disjointness-exception-reactivate', kwargs={'pk': exception_id}),
            data=json.dumps({'comment': 'reactivated'}),
            content_type='application/json',
        )
        self.assertEqual(reactivate_response.status_code, 200)
        self.assertEqual(reactivate_response.json()['status'], 'active')

    def test_stamp_execute_and_rollback_mutation_endpoints(self):
        self.client.force_login(self.user)
        fixture = self._stamp_with_path_intent()
        template = StampTemplate.objects.order_by('pk').first()
        self.assertIsNotNone(template)
        slug = f'api-exec-{timezone.now().strftime("%H%M%S%f")}'

        execute_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamp-template-execute', kwargs={'pk': template.pk}),
            data=json.dumps(
                {
                    'fabric_name': 'API Execute Fabric',
                    'fabric_slug': slug,
                    'source_bindings': {},
                    'creation_options': {},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(execute_response.status_code, 201)
        execute_payload = execute_response.json()
        self.assertEqual(execute_payload['template_id'], template.pk)
        self.assertEqual(execute_payload['status'], 'completed')
        self.assertTrue(execute_payload['rollback_eligible'])

        rollback_response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamp-run-rollback', kwargs={'pk': execute_payload['stamp_run_id']}),
            data=json.dumps({'delete_fabric_as_primitive': True}),
            content_type='application/json',
        )
        self.assertEqual(rollback_response.status_code, 200)
        rollback_payload = rollback_response.json()
        self.assertEqual(rollback_payload['stamp_run_id'], execute_payload['stamp_run_id'])
        self.assertIn(rollback_payload['rollback_mode'], {'fabric_delete', 'managed_objects', 'none'})
        self.assertGreaterEqual(rollback_payload['deleted_total'], 0)

        idempotent_rollback = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamp-run-rollback', kwargs={'pk': execute_payload['stamp_run_id']}),
            data=json.dumps({'delete_fabric_as_primitive': True}),
            content_type='application/json',
        )
        self.assertEqual(idempotent_rollback.status_code, 200)
        self.assertTrue(idempotent_rollback.json()['already_rolled_back'])
        self.assertEqual(fixture.fabric.slug, 'roce-4-plane-mini-proof')

    def test_stamp_preview_alias_name_resolves_and_executes(self):
        self.client.force_login(self.user)
        self._stamp_with_path_intent()
        template = StampTemplate.objects.order_by('pk').first()
        self.assertIsNotNone(template)

        response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:stamp-preview'),
            data=json.dumps(
                {
                    'template_type': 'stamp_template',
                    'template_id': template.pk,
                    'parameters': {
                        'fabric_name': 'Alias Preview Fabric',
                        'fabric_slug': 'alias-preview-fabric',
                    },
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['template_id'], template.pk)
