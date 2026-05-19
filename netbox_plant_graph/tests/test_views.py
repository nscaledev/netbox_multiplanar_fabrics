from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from tenancy.models import Tenant

from dcim.models import Site

from netbox_plant_graph.models import AttachmentUnit, AuditFinding, AuditFindingEvent, AuditRun, AuditSuppression, CoarseEdge, DisjointnessException, Fabric, FabricPlane, PlaneMembership, PlantNode, SignalLane, TerminationPoint, UnresolvedStateSummary
from netbox_plant_graph.models import AssemblyConnectorTemplate, AssemblyTemplate, DeploymentPlan, RackPopulationTemplate, SpatialPlacement, SpatialTemplate, StampRecord
from netbox_plant_graph.object_registry import get_object_spec
from netbox_plant_graph.object_registry import VIEW_OBJECT_SPECS
from netbox_plant_graph.port_mapping_compat import PortMapping
from netbox_plant_graph.services import build_lane_workspace, run_persistent_plane_audit
from netbox_plant_graph.services.sync import rebuild_graph
from netbox_plant_graph import views as view_module

from .topology import PlantGraphTopologyMixin


class ViewRegistrySmokeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='view-admin',
            email='view-admin@example.com',
            password='password',
        )

    def test_view_registry_is_populated(self):
        self.assertTrue(VIEW_OBJECT_SPECS)

    def test_generated_list_views_render_successfully(self):
        self.client.force_login(self.user)

        for spec in VIEW_OBJECT_SPECS:
            url = reverse(spec.list_url_name)
            with self.subTest(spec=spec.registry_key, url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_generated_list_views_render_with_rows(self):
        self.client.force_login(self.user)

        tenant = Tenant.objects.create(name='Table Tenant', slug='table-tenant')
        fabric = Fabric.objects.create(name='Fabric Rows', tenant=tenant)
        plane = FabricPlane.objects.create(fabric=fabric, plane_number=1)
        PlaneMembership.objects.create(
            plane=plane,
            member_type=ContentType.objects.get_for_model(Fabric),
            member_id=fabric.pk,
            membership_role='native',
        )

        urls = [
            reverse('plugins:netbox_plant_graph:fabric_list'),
            reverse('plugins:netbox_plant_graph:plane-membership_list'),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Table Tenant')

    def test_read_only_list_views_do_not_render_broken_add_links(self):
        self.client.force_login(self.user)

        for registry_key in ('signallane', 'fineedge', 'lanemap', 'graphbuildrun', 'unresolvedstatesummary', 'unresolvedstateobservation', 'auditrun', 'auditfinding', 'auditfindingevent', 'auditsuppression', 'spatialplacement'):
            spec = get_object_spec(registry_key)
            view_class = getattr(view_module, spec.view.list_class_name)
            url = reverse(spec.list_url_name)

            with self.subTest(spec=registry_key, url=url):
                self.assertFalse(spec.view.supports_create)
                if view_module.HAS_OBJECT_ACTIONS:
                    self.assertNotIn('AddObject', {action.__name__ for action in view_class.actions})
                else:
                    self.assertNotIn('add', view_class.actions)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b'href="None', response.content)

    def test_writable_list_views_render_add_links(self):
        self.client.force_login(self.user)

        for spec in VIEW_OBJECT_SPECS:
            if not spec.view.supports_create:
                continue
            view_class = getattr(view_module, spec.view.list_class_name)
            url = reverse(spec.list_url_name)
            add_url = reverse(spec.add_url_name)

            with self.subTest(spec=spec.registry_key, url=url):
                if view_module.HAS_OBJECT_ACTIONS:
                    self.assertIn('AddObject', {action.__name__ for action in view_class.actions})
                else:
                    self.assertIn('add', view_class.actions)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, add_url)


class OperationalViewIntegrationTestCase(PlantGraphTopologyMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_superuser(
            username='operational-view-admin',
            email='operational-view-admin@example.com',
            password='password',
        )

    def test_graph_overview_renders_fabric_counts(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Overview')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:graph_overview'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Graph Overview')
        self.assertContains(response, 'Fabric Overview')
        self.assertContains(response, 'Attachment Units')
        self.assertContains(response, 'Signal Lanes')

    def test_path_resolver_view_renders_resolved_path(self):
        topology = self.build_passthrough_topology()
        fabric = Fabric.objects.create(name='Fabric Path View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:path_resolver'), {
            'source_registry_key': 'interface',
            'source_id': topology['interface_a'].pk,
            'destination_registry_key': 'interface',
            'destination_id': topology['interface_b'].pk,
            'resolution': 'attachment_unit',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Path Found')
        self.assertContains(response, 'Interface A')
        self.assertContains(response, 'Interface B')
        self.assertContains(response, 'Transfer Maps Crossed')
        attachment_unit = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['interface_a'], for_concrete_model=False),
            source_id=topology['interface_a'].pk,
        )
        self.assertContains(response, f'href="{attachment_unit.get_absolute_url()}"', html=False)
        self.assertContains(response, 'name="source_interface"', html=False)
        self.assertContains(response, 'name="destination_interface"', html=False)
        self.assertContains(response, 'data-path-resolver-form', html=False)
        self.assertNotContains(response, '<input class="form-control" id="source_id" name="source_id"', html=False)
        self.assertNotContains(response, '<input class="form-control" id="destination_id" name="destination_id"', html=False)

    def test_path_resolver_view_accepts_url_identifiers_for_signal_lane_resolution(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Path URL View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:path_resolver'), {
            'source_registry_key': 'interface',
            'source_id': f'http://localhost:8000/dcim/interfaces/{topology["host_children"][2].pk}/',
            'destination_registry_key': 'interface',
            'destination_id': f'http://localhost:8000/dcim/interfaces/{topology["leaf_children"][3].pk}/',
            'resolution': 'signal_lane',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Path Found')
        self.assertContains(response, 'Yes')

    def test_path_resolver_view_rejects_parent_interfaces_for_signal_lane_resolution(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Parent Path View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:path_resolver'), {
            'source_registry_key': 'interface',
            'source_id': topology['host_parent'].pk,
            'destination_registry_key': 'interface',
            'destination_id': topology['leaf_parent'].pk,
            'resolution': 'signal_lane',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Optical-lane path resolution requires a channelized child interface')
        self.assertNotContains(response, 'No path was found for the requested inputs.')

    def test_plane_audit_view_renders_findings(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Plane Audit')
        self.assertContains(response, 'missing_plane_membership')
        self.assertContains(response, 'plane_underpopulated')

    def test_blast_radius_view_renders_impacted_objects(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Blast Radius View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:blast_radius'), {
            'target_registry_key': 'interface',
            'target_id': topology['interface_a'].pk,
            'resolution': 'attachment_unit',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Physical Cable Blast Radius')
        self.assertContains(response, 'Interface A')
        self.assertContains(response, 'Interface B')

    def test_plane_audit_view_links_missing_port_mapping_objects(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Missing Port Mapping View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'missing_port_mapping')
        self.assertContains(response, f'href="{topology["front_port"].get_absolute_url()}"', html=False)
        self.assertContains(response, f'href="{topology["rear_port"].get_absolute_url()}"', html=False)
        self.assertContains(response, f'target_registry_key=frontport&amp;target_id={topology["front_port"].pk}', html=False)
        self.assertContains(response, f'source_registry_key=frontport&amp;source_id={topology["front_port"].pk}', html=False)
        self.assertContains(response, 'Create a PortMapping row')

    def test_plane_audit_view_renders_partial_profile_mapping_guidance(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Partial Profile Guidance View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'partial_profile_mapping')
        self.assertContains(response, 'Profile-derived mapping is incomplete across the cable positions.')
        self.assertContains(response, 'Workspace: nic0')

    def test_plane_audit_view_surfaces_matching_durable_workflow_state(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Plane Audit Durable Bridge View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[finding.pk]),
            {'next': finding.get_absolute_url(), 'note': 'Durable bridge window'},
        )

        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Durable State')
        self.assertContains(response, 'Durable Detail')
        self.assertContains(response, 'suppressed')
        self.assertContains(response, 'Durable bridge window')
        self.assertContains(response, 'Unsuppress')
        self.assertContains(response, f'source_finding_id={finding.pk}', html=False)

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_plane_audit_view_links_related_unresolved_summaries(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Plane Audit Unresolved Link View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        summary = UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').first()
        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Related Unresolved Summaries')
        self.assertContains(response, summary.get_absolute_url(), html=False)

    def test_lane_drilldown_view_renders_lane_rows(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Drilldown View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_drilldown'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_children'][2].pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Drilldown')
        self.assertContains(response, 'nic0/plane2')
        self.assertContains(response, 'nic0/plane2:l0')
        self.assertContains(response, 'Optical Path')
        self.assertContains(response, 'Open In Workspace')

    def test_lane_workspace_view_renders_grouped_lane_summaries(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Workspace View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Workspace')
        self.assertContains(response, 'Query Bar')
        self.assertContains(response, 'Summary Rail')
        self.assertContains(response, 'Main Workspace Pane')
        self.assertContains(response, 'Context Rail')
        self.assertContains(response, 'Primary Grouping: By Attachment')
        self.assertContains(response, 'Supporting View: By Passive Artifact / Node')
        self.assertContains(response, 'Supporting View: By Plane')
        self.assertContains(response, 'Visible Filters')
        self.assertContains(response, 'Selected Group Coverage')
        self.assertContains(response, 'Skip To Main Workspace')
        self.assertContains(response, 'lane-workspace-shell', html=False)
        self.assertContains(response, 'aria-current="page"', html=False)
        self.assertContains(response, 'lane-workspace-table-wrap', html=False)

    def test_lane_workspace_view_accepts_group_by_plane_and_falls_back_when_invalid(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Workspace Group By View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        plane = fabric.planes.get(plane_number=2)

        plane_response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'fabricplane',
            'target_id': plane.pk,
            'group_by': 'plane',
        })
        fallback_response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'fabricplane',
            'target_id': plane.pk,
            'group_by': 'bogus',
        })

        self.assertEqual(plane_response.status_code, 200)
        self.assertContains(plane_response, 'Primary Grouping: By Plane')
        self.assertContains(plane_response, 'Grouping')
        self.assertContains(plane_response, '<option value="plane" selected>', html=False)
        self.assertContains(plane_response, f'baseline_registry_key=fabricplane&baseline_id={plane.pk}', html=False)

        self.assertEqual(fallback_response.status_code, 200)
        self.assertContains(fallback_response, 'Invalid grouping selection; using attachment view.')
        self.assertContains(fallback_response, 'Primary Grouping: By Attachment')
        self.assertContains(fallback_response, '<option value="attachment" selected>', html=False)

    def test_lane_workspace_view_supports_lane_mode_and_invalid_lane_fallback(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Workspace Lane Mode View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        lane_response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'plane',
            'group_key': 'plane:2',
            'plane_id': 2,
            'mode': 'lane',
            'lane_index': 0,
            'focus': 'paths',
        })
        fallback_response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'mode': 'lane',
            'lane_index': 99,
        })

        self.assertEqual(lane_response.status_code, 200)
        self.assertContains(lane_response, 'Lane Mode')
        self.assertContains(lane_response, 'Return To Grouped View')
        self.assertContains(lane_response, 'Current Focus: Paths')
        self.assertContains(lane_response, 'Lane 0')
        self.assertContains(lane_response, '#lane-workspace-main', html=False)

        self.assertEqual(fallback_response.status_code, 200)
        self.assertContains(fallback_response, 'Selected lane is not present in the current workspace scope; staying in grouped coverage.')
        self.assertContains(fallback_response, 'Primary Grouping: By Attachment')

    def test_lane_workspace_view_defaults_signal_lane_target_to_lane_mode(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Signal Lane Workspace View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        signal_lane = SignalLane.objects.filter(
            attachment_unit__source_id=topology['host_children'][2].pk
        ).order_by('lane_index', 'pk').first()

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'signallane',
            'target_id': signal_lane.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Mode')
        self.assertContains(response, 'Exact Lane')
        self.assertContains(response, f'Lane {signal_lane.lane_index}')

    def test_lane_workspace_view_supports_group_by_path_with_representative_details(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Path Workspace View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'path',
            'focus': 'paths',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Primary Grouping: By Path')
        self.assertContains(response, 'Representative Path Details')
        self.assertContains(response, 'Representative Lane')
        self.assertContains(response, 'Shuffle Module')
        self.assertContains(response, 'Representative Path Stage Sequence')
        self.assertContains(response, 'data-workspace-path-canvas', html=False)
        self.assertContains(response, 'data-path-stage-item', html=False)

    def test_lane_workspace_view_supports_representative_path_lane_selection(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Path Lane Selection View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        workspace = build_lane_workspace(target=topology['host_parent'], group_by='path')
        path_group = next(
            (group for group in workspace.path_groups if len(group.lane_indexes) > 1),
            workspace.path_groups[0],
        )
        requested_lane = path_group.lane_indexes[-1]

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'path',
            'group_key': path_group.key,
            'path_lane_index': requested_lane,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Representative Path Lane {requested_lane}')
        self.assertContains(response, f'Representative Lane {requested_lane}')

    def test_lane_workspace_view_renders_path_grouping_for_partial_mapping_scope(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Partial Mapping Path Workspace View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'path',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Primary Grouping: By Path')
        self.assertContains(response, 'Representative Path Details')

    def test_lane_workspace_view_renders_compare_context_and_backlink(self):
        baseline = self.build_multiplane_shuffle_topology()
        candidate_site = Site.objects.create(name='Workspace Compare View Candidate Site', slug='workspace-compare-view-candidate-site')
        candidate = self.build_profile_breakout_with_missing_peer_positions_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Workspace Compare View'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Workspace Compare View'
        candidate['shuffle_device'].save()
        fabric = Fabric.objects.create(name='Fabric Workspace Compare View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric).first()
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': baseline['host_parent'].pk,
            'group_by': 'path',
            'focus': 'compare',
            'compare_registry_key': 'interface',
            'compare_id': candidate['host_parent'].pk,
            'source_finding_id': finding.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Compare Context')
        self.assertContains(response, 'Return To Audit Finding')
        self.assertContains(response, 'Open Full Compare')

    def test_lane_workspace_view_lane_mode_preserves_compare_and_finding_context(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Workspace Lane Return View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric).first()
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'plane',
            'group_key': 'plane:2',
            'plane_id': 2,
            'mode': 'lane',
            'lane_index': 0,
            'focus': 'paths',
            'compare_registry_key': 'interface',
            'compare_id': topology['host_parent'].pk,
            'source_finding_id': finding.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'compare_registry_key=interface&amp;compare_id={topology["host_parent"].pk}', html=False)
        self.assertContains(response, f'source_finding_id={finding.pk}', html=False)
        self.assertContains(response, '#lane-workspace-main', html=False)

    def test_lane_workspace_view_exports_json_for_visible_scope(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Workspace Export JSON View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'path',
            'export': 'json',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        payload = response.json()
        self.assertEqual(payload['group_by'], 'path')
        self.assertIn('path_groups', payload)

    def test_lane_workspace_view_exports_csv_for_visible_scope(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Workspace Export CSV View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
            'group_by': 'plane',
            'plane_id': 2,
            'export': 'csv',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertContains(response, 'group_key,label,attachment_units,expected_lanes,present_lanes,mapped_lanes,selected')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_lane_workspace_view_renders_target_unresolved_summaries(self):
        topology = self.build_profile_breakout_without_child_interfaces_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Workspace Unresolved View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_workspace'), {
            'target_registry_key': 'interface',
            'target_id': topology['host_parent'].pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Active Unresolved Summaries')
        self.assertContains(response, 'missing_child_interface')
        self.assertContains(response, f'owner_object_id={topology["host_parent"].pk}', html=False)

    def test_lane_compare_view_renders_regression_summary(self):
        baseline = self.build_multiplane_shuffle_topology()
        candidate_site = Site.objects.create(name='Compare View Candidate Site', slug='compare-view-candidate-site')
        candidate = self.build_profile_breakout_with_missing_peer_positions_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Candidate View'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Candidate View'
        candidate['shuffle_device'].save()

        fabric = Fabric.objects.create(name='Fabric Lane Compare View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_compare'), {
            'baseline_registry_key': 'interface',
            'baseline_id': baseline['host_parent'].pk,
            'candidate_registry_key': 'interface',
            'candidate_id': candidate['host_parent'].pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Compare')
        self.assertContains(response, 'Metric Deltas')
        self.assertContains(response, 'Mapping symmetry regressed')
        self.assertContains(response, 'Representative Review Pairs')
        self.assertContains(response, 'position:4')

    def test_attachment_unit_detail_view_renders_lane_overview_card(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Attachment Detail View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        attachment_unit = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_children'][2], for_concrete_model=False),
            source_id=topology['host_children'][2].pk,
        )

        response = self.client.get(reverse('plugins:netbox_plant_graph:attachment-unit', args=[attachment_unit.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Overview')
        self.assertContains(response, 'Signal lanes: 4')

    def test_audit_finding_detail_view_renders_workflow_card_and_events(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Finding Detail View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        response = self.client.get(reverse('plugins:netbox_plant_graph:audit-finding', args=[finding.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Workflow State')
        self.assertContains(response, 'Acknowledge')
        self.assertContains(response, 'Resolve')
        self.assertContains(response, 'Recent Events')
        self.assertContains(response, f'source_finding_id={finding.pk}', html=False)

    def test_audit_finding_detail_view_renders_active_suppression_card(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Suppression Detail View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[finding.pk]),
            {'next': finding.get_absolute_url(), 'note': 'Documented exception.'},
        )

        response = self.client.get(reverse('plugins:netbox_plant_graph:audit-finding', args=[finding.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Active Suppression')
        self.assertContains(response, 'Documented exception.')
        self.assertContains(response, 'Unsuppress')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_audit_finding_detail_view_renders_related_unresolved_summaries_card(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Finding Unresolved Detail View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_port_mapping').first()
        summary = UnresolvedStateSummary.objects.filter(
            fabric=fabric,
            cause_code='missing_port_mapping',
            owner_object_type=finding.object_type,
            owner_object_id=finding.object_id,
        ).first()
        response = self.client.get(reverse('plugins:netbox_plant_graph:audit-finding', args=[finding.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Related Unresolved Summaries')
        self.assertContains(response, summary.get_absolute_url(), html=False)

    def test_audit_finding_action_views_transition_status(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Finding Action View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()

        response = self.client.post(reverse('plugins:netbox_plant_graph:audit_finding_acknowledge', args=[finding.pk]), {'next': finding.get_absolute_url()})
        self.assertEqual(response.status_code, 302)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'acknowledged')

        response = self.client.post(reverse('plugins:netbox_plant_graph:audit_finding_start_remediation', args=[finding.pk]), {'next': finding.get_absolute_url()})
        self.assertEqual(response.status_code, 302)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'in_progress')

        response = self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[finding.pk]),
            {'next': finding.get_absolute_url(), 'note': 'Windowed exception.'},
        )
        self.assertEqual(response.status_code, 302)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'suppressed')
        self.assertTrue(AuditSuppression.objects.filter(finding=finding, active=True).exists())

        response = self.client.post(reverse('plugins:netbox_plant_graph:audit_finding_unsuppress', args=[finding.pk]), {'next': finding.get_absolute_url()})
        self.assertEqual(response.status_code, 302)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')

        response = self.client.post(reverse('plugins:netbox_plant_graph:audit_finding_resolve', args=[finding.pk]), {'next': finding.get_absolute_url(), 'note': 'Closed in view test.'})
        self.assertEqual(response.status_code, 302)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'resolved')
        self.assertEqual(finding.resolution_summary, 'Closed in view test.')

        response = self.client.post(reverse('plugins:netbox_plant_graph:audit_finding_reopen', args=[finding.pk]), {'next': finding.get_absolute_url()})
        self.assertEqual(response.status_code, 302)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')

    def test_health_view_renders_fabric_summary(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Health View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:health'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Health')
        self.assertContains(response, 'Fabric Health View')
        self.assertContains(response, 'Plane Health')
        self.assertContains(response, 'Audit Dashboard')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_health_view_renders_durable_unresolved_state_section(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Health Unresolved View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:health'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Durable Unresolved State')
        self.assertContains(response, 'missing_port_mapping')
        self.assertContains(response, f'fabric={fabric.pk}&amp;active=true', html=False)

    def test_audit_dashboard_view_renders_durable_workflow_sections(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Dashboard View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=8)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[finding.pk]),
            {'next': finding.get_absolute_url(), 'note': 'Windowed change freeze'},
        )

        response = self.client.get(reverse('plugins:netbox_plant_graph:audit_dashboard'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Audit Dashboard')
        self.assertContains(response, 'Fabric Audit Dashboard View')
        self.assertContains(response, 'Recent Runs')
        self.assertContains(response, 'Recent Events')
        self.assertContains(response, 'Expiring Suppressions')
        self.assertContains(response, 'Oldest Active Findings')
        self.assertContains(response, 'Recent Churn')
        self.assertContains(response, 'Last 7d')
        self.assertContains(response, 'Windowed change freeze')
        self.assertContains(response, f'fabric={fabric.pk}&amp;active=true', html=False)
        self.assertContains(response, f'fabric={fabric.pk}&amp;event_type=suppressed', html=False)
        self.assertContains(response, 'All Fabric Events')
        self.assertContains(response, 'Age 7d+')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_audit_dashboard_view_renders_unresolved_reporting_sections(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Dashboard Unresolved View')
        rebuild_graph(scope={'fabric': fabric})
        PortMapping.objects.create(
            device=self.device,
            front_port=topology['front_port'],
            front_port_position=1,
            rear_port=topology['rear_port'],
            rear_port_position=1,
        )
        rebuild_graph(scope={'fabric': fabric})
        PortMapping.objects.filter(
            device=self.device,
            front_port=topology['front_port'],
            rear_port=topology['rear_port'],
        ).delete()
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:audit_dashboard'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Unresolved Active')
        self.assertContains(response, 'Seen In 2+ Builds')
        self.assertContains(response, 'Reopened Summaries')
        self.assertContains(response, 'Active Unresolved By Cause')
        self.assertContains(response, 'Oldest Active Unresolved Summaries')
        self.assertContains(response, 'missing_port_mapping')
        self.assertContains(response, f'fabric={fabric.pk}&amp;cause_code=missing_port_mapping&amp;active=true', html=False)

    def test_audit_dashboard_view_renders_policy_reporting_sections(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Dashboard Policy View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        shuffle_node = PlantNode.objects.get(fabric=fabric, name='Shuffle Module')
        DisjointnessException.objects.create(
            fabric=fabric,
            policy_mode=fabric.disjointness_policy,
            exception_type='shared_passive_artifact',
            target_type=ContentType.objects.get_for_model(PlantNode, for_concrete_model=False),
            target_id=shuffle_node.pk,
            plane_a=fabric.planes.get(plane_number=1),
            plane_b=fabric.planes.get(plane_number=2),
            scope_kind='artifact',
            status='approved',
            active=True,
        )

        response = self.client.get(reverse('plugins:netbox_plant_graph:audit_dashboard'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Policy Domains')
        self.assertContains(response, 'Plane Pair Risk')
        self.assertContains(response, 'Highest-Risk Domains')
        self.assertContains(response, 'Oldest Active Policy Findings')
        self.assertContains(response, 'Cross-Plane Bridges')
        self.assertContains(response, 'Artifact Shares')
        self.assertContains(response, 'Exceptions')
        self.assertContains(response, 'Policy Review')
        self.assertContains(response, 'Lane Workspace')

    def test_fabric_detail_view_renders_health_card(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Detail Health Card')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:fabric', args=[fabric.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fabric Health')
        self.assertContains(response, 'Health Page')
        self.assertContains(response, 'Audit Dashboard')
        self.assertContains(response, 'Policy Review')
        self.assertContains(response, 'Largest domain attachments')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_fabric_detail_view_renders_unresolved_state_card(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Detail Unresolved Card')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:fabric', args=[fabric.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Durable Unresolved State')
        self.assertContains(response, 'missing_port_mapping')
        self.assertContains(response, f'fabric={fabric.pk}&amp;active=true', html=False)

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_unresolved_summary_detail_view_renders_lifecycle_card(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Unresolved Summary Detail View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        summary = UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').first()
        response = self.client.get(reverse('plugins:netbox_plant_graph:unresolved-state-summary', args=[summary.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Summary Lifecycle')
        self.assertContains(response, 'Observations')
        self.assertContains(response, 'All Active Summaries')

    def test_policy_review_view_renders_contamination_domains(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Review View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        plane = fabric.planes.get(plane_number=2)

        response = self.client.get(reverse('plugins:netbox_plant_graph:policy_review'), {
            'fabric_id': fabric.pk,
            'plane_id': plane.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Policy Review')
        self.assertContains(response, 'Policy Summary')
        self.assertContains(response, 'Contamination Domains')
        self.assertContains(response, 'Shuffle Module')
        self.assertContains(response, 'Plane 2 selected for focused review.')

    def test_health_view_links_plane_lane_workspace(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Health Workspace Links')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        plane = fabric.planes.get(plane_number=2)

        response = self.client.get(reverse('plugins:netbox_plant_graph:health'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'target_registry_key=fabricplane&amp;target_id={plane.pk}&amp;group_by=plane',
            html=False,
        )
        self.assertContains(
            response,
            f'plane={plane.pk}&amp;active=true',
            html=False,
        )

    def test_lane_compare_view_links_to_durable_findings(self):
        baseline = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Compare Durable Findings')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_compare'), {
            'baseline_registry_key': 'interface',
            'baseline_id': baseline['host_parent'].pk,
            'candidate_registry_key': 'interface',
            'candidate_id': baseline['leaf_parent'].pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Active Findings')
        self.assertContains(response, f'fabric={fabric.pk}&amp;active=true', html=False)

    def test_lane_compare_view_renders_policy_domain_deltas(self):
        baseline = self.build_profile_breakout_without_child_interfaces_topology()
        candidate_site = Site.objects.create(name='Compare Policy View Candidate Site', slug='compare-policy-view-candidate-site')
        candidate = self.build_multiplane_shuffle_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Policy View Candidate'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Policy View Candidate'
        candidate['shuffle_device'].save()
        fabric = Fabric.objects.create(name='Fabric Lane Compare Policy View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:lane_compare'), {
            'baseline_registry_key': 'interface',
            'baseline_id': baseline['host_parent'].pk,
            'candidate_registry_key': 'interface',
            'candidate_id': candidate['host_parent'].pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Policy Regression Summary')
        self.assertContains(response, 'Policy Domain Deltas')
        self.assertContains(response, 'Candidate introduces new contamination domains')
        self.assertContains(response, 'Shuffle Module')

    def test_policy_review_view_renders_exception_coverage_and_drift(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Review Exceptions')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        shuffle_node = PlantNode.objects.get(fabric=fabric, name='Shuffle Module')

        covered_exception = DisjointnessException.objects.create(
            fabric=fabric,
            policy_mode=fabric.disjointness_policy,
            exception_type='shared_passive_artifact',
            target_type=ContentType.objects.get_for_model(PlantNode, for_concrete_model=False),
            target_id=shuffle_node.pk,
            plane_a=fabric.planes.get(plane_number=1),
            plane_b=fabric.planes.get(plane_number=2),
            scope_kind='artifact',
            status='approved',
            active=True,
        )
        drifted_exception = DisjointnessException.objects.create(
            fabric=fabric,
            policy_mode=fabric.disjointness_policy,
            exception_type='cross_plane_fine_edge',
            target_type=ContentType.objects.get_for_model(topology['host_parent'], for_concrete_model=False),
            target_id=topology['host_parent'].pk,
            plane_a=fabric.planes.get(plane_number=1),
            plane_b=fabric.planes.get(plane_number=2),
            scope_kind='edge',
            status='approved',
            active=True,
        )

        response = self.client.get(reverse('plugins:netbox_plant_graph:policy_review'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Active Exceptions')
        self.assertContains(response, 'Covered')
        self.assertContains(response, 'Drifted')
        self.assertContains(response, covered_exception.get_absolute_url(), html=False)
        self.assertContains(response, drifted_exception.get_absolute_url(), html=False)
        self.assertContains(response, 'Exception List')
        self.assertContains(response, 'Add Exception')

    def test_disjointness_exception_detail_and_actions_render_lifecycle(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Exception Detail View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
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

        response = self.client.get(exception.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Exception Lifecycle')
        self.assertContains(response, 'Approve')
        self.assertContains(response, shuffle_node.get_absolute_url(), html=False)

        approve_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_approve', args=[exception.pk]),
            {'next': exception.get_absolute_url()},
        )
        self.assertEqual(approve_response.status_code, 302)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'approved')

        expire_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_expire', args=[exception.pk]),
            {'next': exception.get_absolute_url()},
        )
        self.assertEqual(expire_response.status_code, 302)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'expired')

        reactivate_response = self.client.post(
            reverse('plugins:netbox_plant_graph:disjointness_exception_reactivate', args=[exception.pk]),
            {'next': exception.get_absolute_url()},
        )
        self.assertEqual(reactivate_response.status_code, 302)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'approved')

    def test_audit_run_detail_view_links_related_events(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Run Detail View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        run = AuditRun.objects.filter(fabric=fabric).first()
        response = self.client.get(run.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Run Summary')
        self.assertContains(response, 'Run Events')
        self.assertContains(response, f'run={run.pk}', html=False)

    def test_audit_suppression_detail_view_renders_unsuppress_action(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Suppression Card View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        self.client.post(
            reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[finding.pk]),
            {'next': finding.get_absolute_url(), 'note': 'Window exception.'},
        )
        suppression = AuditSuppression.objects.filter(finding=finding, active=True).first()

        response = self.client.get(suppression.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Suppression Summary')
        self.assertContains(response, 'Unsuppress')
        self.assertContains(response, finding.get_absolute_url(), html=False)

    def test_audit_finding_event_detail_view_links_finding_and_run(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Event Detail View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        event = AuditFindingEvent.objects.filter(finding__fabric=fabric, run__isnull=False).first()
        response = self.client.get(event.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Event Summary')
        self.assertContains(response, event.finding.get_absolute_url(), html=False)
        self.assertContains(response, event.run.get_absolute_url(), html=False)

    def test_audit_finding_event_list_supports_fabric_and_time_filters(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Event Filter View')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:audit-finding-event_list'), {
            'fabric': fabric.pk,
            'event_type': 'opened',
            'created_after': (timezone.now() - timedelta(days=1)).isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'opened')

    def test_coarse_edge_detail_view_renders_lane_coverage_card(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Coarse Edge Detail View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        coarse_edge = CoarseEdge.objects.order_by('pk').first()

        response = self.client.get(coarse_edge.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Coverage')
        self.assertContains(response, 'Lane Workspace')
        self.assertContains(response, 'Mapped lanes')

    def test_plant_node_detail_view_renders_lane_coverage_card(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Plant Node Detail View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        plant_node = PlantNode.objects.get(fabric=fabric, name='Shuffle Module')

        response = self.client.get(plant_node.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Coverage')
        self.assertContains(response, 'Plane consistency')
        self.assertContains(response, 'Lane Workspace')
        self.assertContains(response, 'Policy Contamination')
        self.assertContains(response, 'Domains touching node')

    def test_termination_point_detail_view_renders_lane_coverage_card(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Termination Detail View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        termination_point = TerminationPoint.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_parent'], for_concrete_model=False),
            source_id=topology['host_parent'].pk,
        )

        response = self.client.get(termination_point.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lane Coverage')
        self.assertContains(response, 'Lane Workspace')

    def test_fabric_plane_detail_view_renders_lane_coverage_card(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Plane Detail Workspace Card')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)
        plane = fabric.planes.get(plane_number=2)

        response = self.client.get(plane.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Plane Health')
        self.assertContains(response, 'Lane Coverage')
        self.assertContains(response, 'Lane Workspace')


# ---------------------------------------------------------------------------
# Planning view rendering tests — Phase 6
# ---------------------------------------------------------------------------


class PlanningViewRenderingTestCase(TestCase):
    """Smoke-tests that all planning UI pages return HTTP 200."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='planning-view-admin',
            email='planning-view-admin@example.com',
            password='password',
        )
        cls.tenant = Tenant.objects.create(name='Planning Tenant', slug='planning-tenant')
        cls.assembly_template = AssemblyTemplate.objects.create(
            name='View Test Assembly',
            slug='view-test-assembly',
            assembly_type='shuffle_trunk',
            tenant=cls.tenant,
        )
        AssemblyConnectorTemplate.objects.create(
            template=cls.assembly_template,
            side='A',
            connector_number=1,
            connector_type='mpo-12',
            position_count=8,
        )
        cls.spatial_template = SpatialTemplate.objects.create(
            name='View Test Spatial',
            slug='view-test-spatial',
            root_node_type='location',
            tenant=cls.tenant,
        )
        cls.rack_population_template = RackPopulationTemplate.objects.create(
            name='View Test RPT',
            slug='view-test-rpt',
            tenant=cls.tenant,
        )
        cls.deployment_plan = DeploymentPlan.objects.create(
            name='View Test Plan',
            tenant=cls.tenant,
        )

    def setUp(self):
        self.client.force_login(self.user)

    # --- Template Library ---

    def test_template_library_renders(self):
        response = self.client.get(reverse('plugins:netbox_plant_graph:template_library'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Template Library')
        self.assertContains(response, 'Assembly Templates')
        self.assertContains(response, 'Planning Tenant')

    # --- Coordinate Layout ---

    def test_coordinate_layout_renders_deprecation_notice(self):
        response = self.client.get(reverse('plugins:netbox_plant_graph:coordinate_layout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Coordinate Layout (Deprecated)')
        self.assertContains(response, 'netbox-floorplan-plugin')
        self.assertContains(response, 'read-only')
        self.assertContains(response, 'Browse Spatial Placement Metadata')

    def test_coordinate_layout_redirects_site_scope_to_floorplan_plugin(self):
        site = Site.objects.create(name='Placement Site', slug='placement-site', tenant=self.tenant)
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:coordinate_layout'),
            {'frame_type': 'site', 'frame_id': site.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/plugins/floorplan/floorplans/add/?site={site.pk}')

    # --- Assembly Template detail (custom view) ---

    def test_assembly_template_detail_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:assembly-template', kwargs={'pk': self.assembly_template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test Assembly')
        self.assertContains(response, 'Planning Tenant')

    # --- Assembly Stamp Wizard ---

    def test_assembly_stamp_wizard_get_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:assembly_stamp_wizard', kwargs={'pk': self.assembly_template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test Assembly')

    # --- Spatial Template detail ---

    def test_spatial_template_detail_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:spatial-template', kwargs={'pk': self.spatial_template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test Spatial')
        self.assertContains(response, 'Planning Tenant')

    # --- Spatial Stamp Wizard ---

    def test_spatial_stamp_wizard_get_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:spatial_stamp_wizard', kwargs={'pk': self.spatial_template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test Spatial')
        self.assertContains(response, 'netbox-floorplan-plugin')

    def test_spatial_stamp_wizard_success_includes_floorplan_link(self):
        site = Site.objects.create(name='Spatial Stamp View Site', slug='spatial-stamp-view-site')

        response = self.client.post(
            reverse('plugins:netbox_plant_graph:spatial_stamp_wizard', kwargs={'pk': self.spatial_template.pk}),
            {
                'site': site.pk,
                'parent_location': '',
                'plan': '',
                'action': 'execute_now',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Floorplan')
        self.assertContains(response, f'/plugins/floorplan/floorplans/add/?site={site.pk}')

    # --- Rack Population Template detail ---

    def test_rack_population_template_detail_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:rack-population-template', kwargs={'pk': self.rack_population_template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test RPT')
        self.assertContains(response, 'Planning Tenant')

    # --- Rack Population Stamp Wizard ---

    def test_rack_population_stamp_wizard_get_renders(self):
        response = self.client.get(
            reverse(
                'plugins:netbox_plant_graph:rack_population_stamp_wizard',
                kwargs={'pk': self.rack_population_template.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test RPT')

    # --- Deployment Plan detail (custom view) ---

    def test_deployment_plan_detail_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment-plan', kwargs={'pk': self.deployment_plan.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View Test Plan')
        self.assertContains(response, 'Planning Tenant')

    def test_deployment_plan_detail_renders_floorplan_sync_summary(self):
        site = Site.objects.create(name='Plan Detail Floorplan Site', slug='plan-detail-floorplan-site')
        StampRecord.objects.create(
            plan=self.deployment_plan,
            template_type=ContentType.objects.get_for_model(self.spatial_template),
            template_id=self.spatial_template.pk,
            result_type=ContentType.objects.get_for_model(site),
            result_id=site.pk,
            status='stamped',
            metadata={
                'floorplan_sync': {
                    'sync_requested': True,
                    'sync_skipped_reason': None,
                    'rack_placement_count': 2,
                    'floorplans_touched': 1,
                    'racks_synced_to_floorplan': 2,
                    'floorplan_errors': [],
                    'results': [
                        {
                            'created_floorplan': True,
                            'created_objects': 2,
                            'updated_objects': 0,
                            'skipped_objects': 0,
                            'errors': [],
                        }
                    ],
                }
            },
        )

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment-plan', kwargs={'pk': self.deployment_plan.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Floorplan sync:')
        self.assertContains(response, '2 rack(s) synced across 1 floorplan(s)')

    def test_deployment_plan_workflow_renders_floorplan_sync_summary(self):
        site = Site.objects.create(name='Plan Workflow Floorplan Site', slug='plan-workflow-floorplan-site')
        StampRecord.objects.create(
            plan=self.deployment_plan,
            template_type=ContentType.objects.get_for_model(self.spatial_template),
            template_id=self.spatial_template.pk,
            result_type=ContentType.objects.get_for_model(site),
            result_id=site.pk,
            status='stamped',
            metadata={
                'floorplan_sync': {
                    'sync_requested': True,
                    'sync_skipped_reason': None,
                    'rack_placement_count': 1,
                    'floorplans_touched': 1,
                    'racks_synced_to_floorplan': 1,
                    'floorplan_errors': [],
                    'results': [
                        {
                            'created_floorplan': False,
                            'created_objects': 0,
                            'updated_objects': 1,
                            'skipped_objects': 0,
                            'errors': [],
                        }
                    ],
                }
            },
        )

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': self.deployment_plan.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Floorplan Sync')
        self.assertContains(response, '1 rack(s) synced across 1 floorplan(s).')
        self.assertContains(response, 'Reused floorplan;')

    # --- Supplementary card links appear ---

    def test_assembly_template_detail_has_stamp_link(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:assembly-template', kwargs={'pk': self.assembly_template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stamp Passive Device')

    def test_deployment_plan_detail_has_template_library_link(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment-plan', kwargs={'pk': self.deployment_plan.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Template Library')


class WorkflowViewTestCase(TestCase):
    """Smoke-tests and basic POST tests for workflow UX pages."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='workflow-admin',
            email='workflow-admin@example.com',
            password='testpass',
        )
        cls.tenant = Tenant.objects.create(name='Workflow Tenant', slug='workflow-tenant')
        cls.fabric = Fabric.objects.create(
            name='WF Test Fabric',
            expected_plane_count=4,
            tier_depth=3,
            tenant=cls.tenant,
        )
        for i in range(1, 5):
            FabricPlane.objects.create(fabric=cls.fabric, plane_number=i)

    def setUp(self):
        self.client.force_login(self.user)

    def test_fabric_onboard_get_renders(self):
        response = self.client.get(reverse('plugins:netbox_plant_graph:fabric_onboard'))
        self.assertEqual(response.status_code, 200)

    def test_fabric_onboard_post_creates_fabric_and_planes(self):
        response = self.client.post(
            reverse('plugins:netbox_plant_graph:fabric_onboard'),
            {
                'name': 'New Fabric WF',
                'tenant': self.tenant.pk,
                'expected_plane_count': 3,
                'tier_depth': 2,
                'disjointness_policy': 'full',
            },
        )
        self.assertIn(response.status_code, (200, 302))
        if response.status_code == 302:
            self.assertTrue(Fabric.objects.filter(name='New Fabric WF', tenant=self.tenant).exists())
            self.assertEqual(FabricPlane.objects.filter(fabric__name='New Fabric WF').count(), 3)

    def test_fabric_operations_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': self.fabric.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Workflow Tenant')

    def test_audit_triage_get_renders_empty(self):
        response = self.client.get(reverse('plugins:netbox_plant_graph:audit_triage'))
        self.assertEqual(response.status_code, 200)

    def test_audit_triage_get_renders_with_finding(self):
        ct = ContentType.objects.get_for_model(self.fabric)
        finding = AuditFinding.objects.create(
            fabric=self.fabric,
            finding_type='test_finding',
            severity='warning',
            status='open',
            active=True,
            message='Test finding for triage',
            object_type=ct,
            object_id=self.fabric.pk,
        )
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:audit_triage'),
            {'fabric_id': self.fabric.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test finding for triage')
        self.assertContains(response, 'Workflow Tenant')

    def test_audit_triage_with_index(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:audit_triage'),
            {'index': 0},
        )
        self.assertEqual(response.status_code, 200)

    # --- Assembly Template Builder ---

    def test_assembly_template_build_renders(self):
        template = AssemblyTemplate.objects.create(
            name='WF Builder Template',
            slug='wf-builder-template',
            tenant=self.tenant,
        )
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': template.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Workflow Tenant')

    def test_assembly_template_build_404_for_missing(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)

    def test_assembly_template_build_add_connector_post(self):
        template = AssemblyTemplate.objects.create(name='Build Post Test Template', slug='build-post-test-template')
        response = self.client.post(
            reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': template.pk}),
            {'action': 'add_connector', 'side': 'A', 'connector_number': '1', 'position_count': '8'},
        )
        self.assertIn(response.status_code, [200, 302])
        self.assertTrue(
            AssemblyConnectorTemplate.objects.filter(template=template, side='A', connector_number=1).exists()
        )

    # --- Spatial Template Composer ---

    def test_spatial_template_compose_renders(self):
        from netbox_plant_graph.models import SpatialTemplate
        st = SpatialTemplate.objects.create(name='WF Compose Test Template', slug='wf-compose-test-template')
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:spatial_template_compose', kwargs={'pk': st.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_spatial_template_compose_404_for_missing(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:spatial_template_compose', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)

    # --- Fabric Plane Assignment ---

    def test_fabric_assign_planes_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': self.fabric.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_fabric_assign_planes_404_for_missing(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)

    # --- Disjointness Exception Request ---

    def test_disjointness_exception_request_renders(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:disjointness_exception_request')
        )
        self.assertEqual(response.status_code, 200)

    def test_disjointness_exception_request_with_fabric_prepopulated(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:disjointness_exception_request'),
            {'fabric_id': self.fabric.pk}
        )
        self.assertEqual(response.status_code, 200)

    # --- Deployment Plan Workflow ---

    def test_deployment_plan_workflow_renders(self):
        from netbox_plant_graph.models import DeploymentPlan
        plan = DeploymentPlan.objects.create(
            name='WF Test Plan',
            fabric=self.fabric,
        )
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': plan.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_deployment_plan_workflow_404_for_missing(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': 99999})
        )
        self.assertEqual(response.status_code, 404)

    def test_deployment_plan_workflow_step_param(self):
        from netbox_plant_graph.models import DeploymentPlan
        plan = DeploymentPlan.objects.create(
            name='WF Step Test Plan',
            fabric=self.fabric,
        )
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': plan.pk}),
            {'step': 0}
        )
        self.assertEqual(response.status_code, 200)
