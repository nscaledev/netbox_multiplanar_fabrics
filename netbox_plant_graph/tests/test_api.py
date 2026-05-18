from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from dcim.models import Site

from netbox_plant_graph.models import AuditFinding, AuditSuppression, DisjointnessException, Fabric, PlantNode, UnresolvedStateSummary
from netbox_plant_graph.services import run_persistent_plane_audit, suppress_audit_finding
from netbox_plant_graph.services.sync import rebuild_graph

from .topology import PlantGraphTopologyMixin
from netbox_plant_graph.object_registry import API_OBJECT_SPECS


class ObjectRegistrySmokeTestCase(TestCase):
    def test_api_registry_is_populated(self):
        self.assertTrue(API_OBJECT_SPECS)

    def test_spatial_placement_api_is_read_only(self):
        user = get_user_model().objects.create_superuser(
            username='api-readonly-admin',
            email='api-readonly@example.com',
            password='password',
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse('plugins-api:netbox_plant_graph-api:spatial-placements-list'),
            {
                'position_x': '1.0',
                'position_y': '2.0',
                'position_z': '3.0',
                'coordinate_unit': 'meters',
            },
        )

        self.assertEqual(response.status_code, 405)

    def test_spatial_placement_reconcile_api_returns_summary(self):
        user = get_user_model().objects.create_superuser(
            username='api-reconcile-admin',
            email='api-reconcile@example.com',
            password='password',
        )
        self.client.force_login(user)
        site = Site.objects.create(name='API Reconcile Site', slug='api-reconcile-site')

        with patch(
            'netbox_plant_graph.services.floorplan_bridge.reconcile_floorplan_to_placements',
            return_value=SimpleNamespace(
                floorplan=SimpleNamespace(pk=42),
                updated_placements=2,
                created_placements=1,
                skipped_unmanaged=3,
                skipped_missing_rack=4,
                skipped_missing_placement=5,
                errors=[],
            ),
        ) as reconcile_floorplan:
            response = self.client.post(
                reverse('plugins-api:netbox_plant_graph-api:spatial-placement-reconcile'),
                {
                    'scope_type': 'site',
                    'scope_id': site.pk,
                    'create_missing': True,
                },
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['floorplan_id'], 42)
        self.assertEqual(response.json()['updated_placements'], 2)
        self.assertEqual(response.json()['created_placements'], 1)
        reconcile_floorplan.assert_called_once_with(site, create_missing=True)


class AuditWorkflowAPITestCase(PlantGraphTopologyMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_superuser(
            username='api-audit-admin',
            email='api-audit-admin@example.com',
            password='password',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _finding_action_url(self, action, finding):
        return reverse(f'plugins-api:netbox_plant_graph-api:audit-findings-{action}', kwargs={'pk': finding.pk})

    def _exception_action_url(self, action, exception):
        return reverse(f'plugins-api:netbox_plant_graph-api:disjointness-exceptions-{action}', kwargs={'pk': exception.pk})

    def test_audit_finding_viewset_exposes_workflow_actions(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit API Actions')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()

        response = self.client.post(self._finding_action_url('acknowledge', finding), {'note': 'API ack'})

        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'acknowledged')
        self.assertEqual(response.json()['status'], 'acknowledged')
        self.assertIsNone(response.json()['active_suppression'])

        response = self.client.post(self._finding_action_url('start-remediation', finding), {'note': 'API in progress'})

        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'in_progress')
        self.assertEqual(response.json()['status'], 'in_progress')

        response = self.client.post(self._finding_action_url('resolve', finding), {'note': 'Closed via API'})

        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'resolved')
        self.assertFalse(finding.active)
        self.assertEqual(response.json()['status'], 'resolved')

        response = self.client.post(self._finding_action_url('reopen', finding), {'note': 'Reopened via API'})

        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')
        self.assertTrue(finding.active)
        self.assertEqual(response.json()['status'], 'open')

    def test_audit_finding_suppress_and_unsuppress_actions_return_active_suppression(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit API Suppress')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()

        response = self.client.post(self._finding_action_url('suppress', finding), {'note': 'API suppression', 'days': 3})

        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'suppressed')
        self.assertTrue(AuditSuppression.objects.filter(finding=finding, active=True).exists())
        payload = response.json()
        self.assertEqual(payload['status'], 'suppressed')
        self.assertIsNotNone(payload['active_suppression'])
        self.assertEqual(payload['active_suppression']['reason'], 'API suppression')

        response = self.client.post(self._finding_action_url('unsuppress', finding), {'note': 'clear'})

        self.assertEqual(response.status_code, 200)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')
        self.assertFalse(AuditSuppression.objects.filter(finding=finding, active=True).exists())
        payload = response.json()
        self.assertEqual(payload['status'], 'open')
        self.assertIsNone(payload['active_suppression'])

    def test_audit_finding_suppress_action_validates_days(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit API Validation')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()

        response = self.client.post(self._finding_action_url('suppress', finding), {'days': -1})

        self.assertEqual(response.status_code, 400)
        self.assertIn('days', response.json())

    def test_audit_finding_list_filters_support_plane_and_object_identity(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit API Filters')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        plane_finding = AuditFinding.objects.filter(fabric=fabric).exclude(plane=None).first()
        object_finding = AuditFinding.objects.filter(fabric=fabric).first()

        plane_response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:audit-findings-list'),
            {'plane': plane_finding.plane_id, 'active': 'true'},
        )
        self.assertEqual(plane_response.status_code, 200)
        self.assertGreaterEqual(plane_response.json()['count'], 1)
        self.assertTrue(all(
            item['plane'] == plane_finding.plane_id
            for item in plane_response.json()['results']
        ))

        object_response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:audit-findings-list'),
            {'object_type': object_finding.object_type_id, 'object_id': object_finding.object_id},
        )
        self.assertEqual(object_response.status_code, 200)
        self.assertGreaterEqual(object_response.json()['count'], 1)
        self.assertTrue(any(item['id'] == object_finding.pk for item in object_response.json()['results']))

    def test_audit_finding_list_filters_support_suppressed_and_min_age_days(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit API Age Filters')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=9)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        suppress_audit_finding(finding=finding, actor=self.user, reason='Aged suppression', days=4)

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:audit-findings-list'),
            {'fabric': fabric.pk, 'suppressed': 'true', 'min_age_days': 7},
        )

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()['count'], 1)
        self.assertTrue(any(item['id'] == finding.pk for item in response.json()['results']))

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:audit-findings-list'),
            {'fabric': fabric.pk, 'suppressed': 'false', 'min_age_days': 7},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(item['id'] != finding.pk for item in response.json()['results']))

    def test_workflow_summary_endpoint_returns_counts_and_churn(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Workflow Summary API')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=8)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        suppress_audit_finding(finding=finding, actor=self.user, reason='Workflow API window', days=2)

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-summary'),
            {'fabric': fabric.pk},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['fabric']['display'], 'Fabric Workflow Summary API')
        self.assertGreaterEqual(payload['active_findings'], 1)
        self.assertEqual(payload['suppressed_findings'], 1)
        self.assertTrue(payload['churn_windows'])
        self.assertEqual(payload['churn_windows'][0]['days'], 7)

    def test_workflow_findings_endpoint_returns_filtered_records(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Workflow Finding Search API')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=9)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        suppress_audit_finding(finding=finding, actor=self.user, reason='Filtered finding', days=3)

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-findings'),
            {'fabric': fabric.pk, 'suppressed': 'true', 'min_age_days': 7},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload['count'], 1)
        self.assertEqual(payload['results'][0]['finding']['pk'], finding.pk)
        self.assertEqual(payload['results'][0]['active_suppression']['reason'], 'Filtered finding')

    def test_workflow_finding_detail_endpoint_returns_events(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Workflow Finding Detail API')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        suppress_audit_finding(finding=finding, actor=self.user, reason='Detail finding', days=3)

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-finding-detail', kwargs={'pk': finding.pk}),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['finding']['finding']['pk'], finding.pk)
        self.assertEqual(payload['finding']['active_suppression']['reason'], 'Detail finding')
        self.assertTrue(any(event['event_type'] == 'suppressed' for event in payload['recent_events']))

    def test_workflow_runs_endpoint_returns_run_timeline(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Workflow Runs API')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:workflow-runs'),
            {'fabric': fabric.pk, 'limit': 5},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload['count'], 1)
        self.assertTrue(payload['results'][0]['scope_label'])
        self.assertGreaterEqual(payload['results'][0]['finding_count'], 1)

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_unresolved_summary_list_filters_support_owner_object_and_cause(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Unresolved Summary API')
        rebuild_graph(scope={'fabric': fabric})

        owner_type = ContentType.objects.get_for_model(topology['front_port'], for_concrete_model=False)
        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:unresolved-state-summaries-list'),
            {
                'fabric': fabric.pk,
                'cause_code': 'missing_port_mapping',
                'owner_object_type': owner_type.pk,
                'owner_object_id': topology['front_port'].pk,
                'active': 'true',
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['results'][0]['cause_code'], 'missing_port_mapping')
        self.assertEqual(payload['results'][0]['owner_object_type'], owner_type.pk)
        self.assertEqual(payload['results'][0]['owner_object_id'], topology['front_port'].pk)
        self.assertTrue(payload['results'][0]['active'])

        summary = UnresolvedStateSummary.objects.get(pk=payload['results'][0]['id'])
        observation_response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:unresolved-state-observations-list'),
            {'summary': summary.pk},
        )
        self.assertEqual(observation_response.status_code, 200)
        self.assertGreaterEqual(observation_response.json()['count'], 1)
        self.assertEqual(observation_response.json()['results'][0]['summary'], summary.pk)

    def test_disjointness_exception_viewset_exposes_lifecycle_actions(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Exception API')
        rebuild_graph(scope={'fabric': fabric})
        shuffle_node = PlantNode.objects.get(fabric=fabric, name='Shuffle Module')
        exception = DisjointnessException.objects.create(
            fabric=fabric,
            policy_mode=fabric.disjointness_policy,
            exception_type='shared_passive_artifact',
            target_type=ContentType.objects.get_for_model(PlantNode, for_concrete_model=False),
            target_id=shuffle_node.pk,
            plane_a=fabric.planes.get(plane_number=1),
            plane_b=fabric.planes.get(plane_number=2),
            scope_kind='artifact',
            status='draft',
            active=False,
        )

        response = self.client.post(self._exception_action_url('approve', exception), {})

        self.assertEqual(response.status_code, 200)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'approved')
        self.assertTrue(exception.active)
        self.assertEqual(response.json()['status'], 'approved')

        response = self.client.post(self._exception_action_url('expire', exception), {})

        self.assertEqual(response.status_code, 200)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'expired')
        self.assertFalse(exception.active)
        self.assertEqual(response.json()['status'], 'expired')

        response = self.client.post(self._exception_action_url('reactivate', exception), {})

        self.assertEqual(response.status_code, 200)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'approved')
        self.assertTrue(exception.active)
        self.assertEqual(response.json()['status'], 'approved')


# ---------------------------------------------------------------------------
# Stamp action endpoint tests
# ---------------------------------------------------------------------------


class StampActionEndpointTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from dcim.models import DeviceRole, DeviceType, FrontPortTemplate, Location, Manufacturer, Rack, RackType, RearPortTemplate, Site

        cls.user = get_user_model().objects.create_superuser(
            username='stamp-api-admin',
            email='stamp-api-admin@example.com',
            password='password',
        )
        cls.site = Site.objects.create(name='Stamp API Site', slug='stamp-api-site')
        cls.manufacturer = Manufacturer.objects.create(name='StampAPIMfr', slug='stampapimfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='Stamp API DT')
        cls.device_role = DeviceRole.objects.create(name='Stamp API Role', slug='stamp-api-role')
        cls.rack_type = RackType.objects.create(manufacturer=cls.manufacturer, model='Stamp API RT', slug='stamp-api-rt')
        cls.rack = Rack.objects.create(name='Stamp API Rack', site=cls.site)

        rear_template = RearPortTemplate.objects.create(device_type=cls.device_type, name='A1', type='mpo', positions=4)
        front_template_kwargs = {'device_type': cls.device_type, 'name': 'B1', 'type': 'lc', 'positions': 4}
        front_field_names = {field.name for field in FrontPortTemplate._meta.fields}
        if 'positions' not in front_field_names:
            front_template_kwargs.pop('positions')
        if 'rear_port' in front_field_names:
            front_template_kwargs['rear_port'] = rear_template
            front_template_kwargs.setdefault('rear_port_position', 1)
        FrontPortTemplate.objects.create(**front_template_kwargs)

    def setUp(self):
        self.client.force_login(self.user)

    def _stamp_url(self, model_basename, pk):
        return reverse(
            f'plugins-api:netbox_plant_graph-api:{model_basename}-stamp',
            kwargs={'pk': pk},
        )

    def test_assembly_template_stamp_endpoint(self):
        from netbox_plant_graph.models import AssemblyConnectorTemplate, AssemblyMappingTemplate, AssemblyTemplate

        template = AssemblyTemplate.objects.create(
            name='API Asm Template', slug='api-asm-template',
            assembly_type='shuffle_board', device_type=self.device_type,
        )
        a_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=4, label='A1',
        )
        b_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='B', connector_number=1,
            connector_type='lc-duplex', position_count=4, label='B1',
        )
        for pos in range(1, 5):
            AssemblyMappingTemplate.objects.create(
                template=template, a_connector=a_conn, a_position=pos,
                b_connector=b_conn, b_position=pos, mapping_type='identity',
            )

        response = self.client.post(
            self._stamp_url('assembly-templates', template.pk),
            data={
                'site': self.site.pk,
                'device_role': self.device_role.pk,
                'name': 'API-Stamped-Device',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'stamped')
        self.assertIsNotNone(body['device_id'])
        self.assertEqual(body['port_mapping_count'], 4)

    def test_assembly_template_stamp_dry_run(self):
        from netbox_plant_graph.models import AssemblyTemplate

        template = AssemblyTemplate.objects.create(
            name='API Asm DryRun', slug='api-asm-dryrun',
            assembly_type='shuffle_board', device_type=self.device_type,
        )

        response = self.client.post(
            self._stamp_url('assembly-templates', template.pk),
            data={
                'site': self.site.pk,
                'device_role': self.device_role.pk,
                'name': 'Dry-Run-Device',
                'dry_run': True,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'dry_run')

    def test_spatial_template_stamp_endpoint(self):
        from netbox_plant_graph.models import SpatialTemplate, SpatialTemplateNode

        template = SpatialTemplate.objects.create(
            name='API Spatial Template', slug='api-spatial-template',
            root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='API-Rack-{index}',
            node_type='rack_position', quantity=2, sort_order=0,
            rack_type=self.rack_type,
        )

        floorplan_result = type(
            'FloorplanSyncResultStub',
            (),
            {
                'floorplan': object(),
                'created_floorplan': True,
                'created_objects': 2,
                'updated_objects': 0,
                'skipped_objects': 0,
                'errors': [],
            },
        )()

        with patch(
            'netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan',
            return_value=floorplan_result,
        ):
            response = self.client.post(
                self._stamp_url('spatial-templates', template.pk),
                data={'site': self.site.pk},
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'stamped')
        self.assertEqual(body['racks_created'], 2)
        self.assertEqual(body['floorplan_sync']['racks_synced_to_floorplan'], 2)
        self.assertEqual(body['floorplan_sync']['floorplans_touched'], 1)
        self.assertEqual(body['floorplan_sync']['results'][0]['created_floorplan'], True)

    def test_spatial_template_stamp_endpoint_can_disable_floorplan_sync(self):
        from netbox_plant_graph.models import SpatialTemplate, SpatialTemplateNode

        template = SpatialTemplate.objects.create(
            name='API Spatial No Sync', slug='api-spatial-no-sync',
            root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template,
            name_pattern='API-NoSync-Rack-{index}',
            node_type='rack_position',
            quantity=1,
            sort_order=0,
            rack_type=self.rack_type,
        )

        with patch('netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan') as sync_floorplan:
            response = self.client.post(
                self._stamp_url('spatial-templates', template.pk),
                data={'site': self.site.pk, 'sync_floorplan': False},
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['floorplan_sync']['sync_requested'], False)
        self.assertEqual(body['floorplan_sync']['sync_skipped_reason'], 'disabled')
        sync_floorplan.assert_not_called()

    def test_rack_population_template_stamp_endpoint(self):
        from netbox_plant_graph.models import RackPopulationSlot, RackPopulationTemplate

        template = RackPopulationTemplate.objects.create(
            name='API RackPop', slug='api-rackpop',
        )
        RackPopulationSlot.objects.create(
            template=template, u_position=1, face='front',
            device_type=self.device_type, device_role=self.device_role,
            name_pattern='{parent_name}-U{index}',
        )

        response = self.client.post(
            self._stamp_url('rack-population-templates', template.pk),
            data={'rack': self.rack.pk},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'stamped')
        self.assertEqual(body['devices_created'], 1)


class StampPreviewEndpointTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from dcim.models import DeviceType, Manufacturer, RackType

        cls.user = get_user_model().objects.create_superuser(
            username='preview-api-admin',
            email='preview-api-admin@example.com',
            password='password',
        )
        cls.manufacturer = Manufacturer.objects.create(name='PreviewMfr', slug='previewmfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='Preview DT')
        cls.rack_type = RackType.objects.create(manufacturer=cls.manufacturer, model='Preview RT', slug='preview-rt')

    def setUp(self):
        self.client.force_login(self.user)

    def _preview_url(self):
        return reverse('plugins-api:netbox_plant_graph-api:stamp-preview')

    def test_assembly_preview(self):
        from netbox_plant_graph.models import AssemblyConnectorTemplate, AssemblyMappingTemplate, AssemblyTemplate

        template = AssemblyTemplate.objects.create(
            name='Preview Asm', slug='preview-asm',
            assembly_type='shuffle_board', device_type=self.device_type,
        )
        a_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=4, label='A1',
        )
        b_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='B', connector_number=1,
            connector_type='lc-duplex', position_count=4, label='B1',
        )
        for pos in range(1, 5):
            AssemblyMappingTemplate.objects.create(
                template=template, a_connector=a_conn, a_position=pos,
                b_connector=b_conn, b_position=pos, mapping_type='identity',
            )

        response = self.client.post(
            self._preview_url(),
            data={'template_type': 'assembly', 'template_id': template.pk},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['template_type'], 'assembly')
        self.assertEqual(body['template_name'], 'Preview Asm')
        self.assertIsNotNone(body['device_type'])
        self.assertTrue(len(body['objects_to_create']) > 0)

    def test_spatial_preview(self):
        from netbox_plant_graph.models import SpatialTemplate, SpatialTemplateNode

        template = SpatialTemplate.objects.create(
            name='Preview Spatial', slug='preview-spatial', root_node_type='hall',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='Row-{index}', node_type='row',
            quantity=3, sort_order=0,
        )

        response = self.client.post(
            self._preview_url(),
            data={'template_type': 'spatial', 'template_id': template.pk},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['template_type'], 'spatial')
        self.assertEqual(body['expected_counts']['locations'], 3)

    def test_rack_population_preview(self):
        from dcim.models import DeviceRole

        from netbox_plant_graph.models import RackPopulationSlot, RackPopulationTemplate

        device_role = DeviceRole.objects.create(name='Preview Role', slug='preview-role')
        template = RackPopulationTemplate.objects.create(
            name='Preview RackPop', slug='preview-rackpop',
        )
        RackPopulationSlot.objects.create(
            template=template, u_position=1, face='front',
            device_type=self.device_type, device_role=device_role,
            name_pattern='Dev-{index}',
        )
        RackPopulationSlot.objects.create(
            template=template, u_position=2, face='front',
            device_type=self.device_type, device_role=device_role,
            name_pattern='Dev-{index}',
        )

        response = self.client.post(
            self._preview_url(),
            data={'template_type': 'rack_population', 'template_id': template.pk},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['template_type'], 'rack_population')
        self.assertEqual(body['slot_count'], 2)
        self.assertEqual(len(body['devices']), 2)

    def test_preview_invalid_template_type(self):
        response = self.client.post(
            self._preview_url(),
            data={'template_type': 'invalid', 'template_id': 1},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_preview_missing_template(self):
        response = self.client.post(
            self._preview_url(),
            data={'template_type': 'assembly', 'template_id': 999999},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)


class DeploymentPlanActionEndpointTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='plan-api-admin',
            email='plan-api-admin@example.com',
            password='password',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _plan_action_url(self, action, pk):
        return reverse(
            f'plugins-api:netbox_plant_graph-api:deployment-plans-{action}',
            kwargs={'pk': pk},
        )

    def test_execute_endpoint(self):
        from unittest.mock import patch

        from netbox_plant_graph.models import AssemblyTemplate, DeploymentPlan, StampRecord

        template = AssemblyTemplate.objects.create(
            name='API Exec Template', slug='api-exec-template', assembly_type='shuffle_board',
        )
        template_ct = ContentType.objects.get_for_model(template)
        plan = DeploymentPlan.objects.create(name='API Exec Plan', status='approved')
        StampRecord.objects.create(
            plan=plan, template_type=template_ct, template_id=template.pk, status='pending',
        )

        with patch('netbox_plant_graph.services.sync.rebuilder.rebuild_graph') as mock_rebuild, \
             patch('netbox_plant_graph.services.graph.persistent_audits.run_persistent_plane_audit') as mock_audit:
            mock_rebuild.return_value = {'build_run': 1}
            mock_audit.return_value = {}
            response = self.client.post(self._plan_action_url('execute', plan.pk), {}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'active')

    def test_rollback_endpoint(self):
        from netbox_plant_graph.models import AssemblyTemplate, DeploymentPlan, StampRecord

        template = AssemblyTemplate.objects.create(
            name='API Rollback Template', slug='api-rollback-template', assembly_type='shuffle_board',
        )
        template_ct = ContentType.objects.get_for_model(template)
        plan = DeploymentPlan.objects.create(name='API Rollback Plan', status='active')
        StampRecord.objects.create(
            plan=plan, template_type=template_ct, template_id=template.pk, status='stamped',
        )

        response = self.client.post(self._plan_action_url('rollback', plan.pk), {}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'rolled_back')

    def test_execute_rejects_draft(self):
        from netbox_plant_graph.models import DeploymentPlan

        plan = DeploymentPlan.objects.create(name='API Draft Plan', status='draft')
        response = self.client.post(self._plan_action_url('execute', plan.pk), {}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
