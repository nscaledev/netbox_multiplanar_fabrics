from collections import defaultdict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from dcim.models import Cable, Interface, Site

try:
    from dcim.choices import CableProfileChoices  # noqa: F401
    HAS_NATIVE_CABLE_PROFILES = True
except ImportError:
    HAS_NATIVE_CABLE_PROFILES = False

from netbox_plant_graph.models import AttachmentUnit, AuditFinding, AuditFindingEvent, AuditRun, AuditSuppression, CoarseEdge, DisjointnessException, Fabric, FabricPlane, GraphBuildRun, PlantNode, SignalLane, UnresolvedStateSummary
from netbox_plant_graph.port_mapping_compat import PortMapping
from netbox_plant_graph.services import (
    acknowledge_audit_finding,
    build_audit_run_timeline,
    apply_audit_retention,
    build_audit_finding_detail,
    build_audit_workflow_summary,
    build_unresolved_state_dashboard,
    build_contamination_domains,
    build_disjointness_exception_review,
    build_durable_audit_finding_detail,
    build_durable_audit_finding_search,
    build_lane_allocation_summary,
    build_lane_drilldown,
    build_lane_workspace,
    build_policy_dashboard,
    build_lane_set,
    build_lane_set_for_attachment_unit,
    build_lane_set_for_coarse_edge,
    build_lane_set_for_parent_interface,
    build_lane_set_for_plane,
    build_lane_set_for_plant_node,
    build_policy_summary,
    build_policy_evaluation,
    build_typed_lane_drilldown,
    compare_lane_allocations,
    compute_blast_radius,
    compute_fabric_health,
    compute_typed_fabric_health,
    approve_disjointness_exception,
    expire_disjointness_exception,
    list_related_unresolved_summaries,
    reactivate_disjointness_exception,
    reopen_audit_finding,
    normalize_lane_workspace_query,
    resolve_path,
    resolve_audit_finding,
    run_persistent_plane_audit,
    resolve_typed_lane_path,
    run_plane_audit,
    start_audit_finding_remediation,
    suppress_audit_finding,
    unsuppress_audit_finding,
    build_unresolved_state_overview,
)
from netbox_plant_graph.services.graph.finding_fingerprints import build_audit_finding_fingerprint
from netbox_plant_graph.services.graph.resolver import _build_attachment_adjacency
from netbox_plant_graph.services.sync import rebuild_graph

from .topology import PlantGraphTopologyMixin


class GraphServiceIntegrationTestCase(PlantGraphTopologyMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_superuser(
            username='resolver-admin',
            email='resolver-admin@example.com',
            password='password',
        )

    def test_resolve_path_traces_passthrough_graph(self):
        topology = self.build_passthrough_topology()
        fabric = Fabric.objects.create(name='Fabric Resolve')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_path(source=topology['interface_a'], destination=topology['interface_b'])

        self.assertTrue(result['path_found'])
        self.assertEqual(result['summary']['coarse_edges_crossed'], 3)
        self.assertEqual(result['summary']['transfer_maps_crossed'], 2)
        self.assertTrue(any(step.get('kind') == 'transfer_map' for step in result['path'] if isinstance(step, dict)))

    def test_attachment_resolver_scopes_adjacency_to_relevant_fabric(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Resolve Scoped')
        rebuild_graph(scope={'fabric': fabric})

        remote_site = Site.objects.create(name='Remote Resolver Site', slug='remote-resolver-site')
        remote_device_a = self.create_peer_device(name='Remote Device A', site=remote_site)
        remote_device_b = self.create_peer_device(name='Remote Device B', site=remote_site)
        remote_interface_a = Interface.objects.create(device=remote_device_a, name='Remote Interface A')
        remote_interface_b = Interface.objects.create(device=remote_device_b, name='Remote Interface B')
        remote_cable = Cable(a_terminations=[remote_interface_a], b_terminations=[remote_interface_b])
        remote_cable.clean()
        remote_cable.save()

        remote_fabric = Fabric.objects.create(name='Fabric Resolve Remote', scope_site=remote_site)
        rebuild_graph(scope={'fabric': remote_fabric})

        interface_type = ContentType.objects.get_for_model(Interface, for_concrete_model=False)
        source_node = AttachmentUnit.objects.get(source_type=interface_type, source_id=topology['interface_a'].pk)
        destination_node = AttachmentUnit.objects.get(source_type=interface_type, source_id=topology['interface_b'].pk)
        remote_node = AttachmentUnit.objects.get(source_type=interface_type, source_id=remote_interface_a.pk)

        adjacency = _build_attachment_adjacency(
            source_nodes=[source_node],
            destination_nodes=[destination_node],
        )
        scoped_ids = set(adjacency.keys())
        for neighbors in adjacency.values():
            scoped_ids.update(neighbor_id for neighbor_id, _, _ in neighbors)

        self.assertIn(source_node.pk, scoped_ids)
        self.assertIn(destination_node.pk, scoped_ids)
        self.assertNotIn(remote_node.pk, scoped_ids)

        result = resolve_path(source=topology['interface_a'], destination=topology['interface_b'])

        self.assertTrue(result['path_found'])

    def test_compute_blast_radius_returns_connected_units(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Blast Radius')
        rebuild_graph(scope={'fabric': fabric})

        result = compute_blast_radius(target=topology['interface_a'])

        self.assertGreaterEqual(len(result['impacted_objects']), 2)
        self.assertEqual(result['impacted_paths'][0]['distance'], 0)
        self.assertIn('Interface B', {item['object']['display'] for item in result['impacted_paths']})

    def test_plane_audit_reports_missing_memberships(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding_types = {finding['finding_type'] for finding in result['findings']}

        self.assertIn('missing_plane_membership', finding_types)
        self.assertIn('plane_underpopulated', finding_types)

    def test_audit_finding_fingerprint_ignores_message_wording(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Fingerprint Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding = next(item for item in result['findings'] if item['finding_type'] == 'missing_plane_membership')
        modified_finding = dict(finding)
        modified_finding['message'] = 'Attachment unit still has no plane membership, but the wording changed.'

        self.assertEqual(
            build_audit_finding_fingerprint(finding),
            build_audit_finding_fingerprint(modified_finding),
        )

    def test_persistent_plane_audit_creates_durable_run_and_reuses_findings(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Persistent Audit')
        rebuild_graph(scope={'fabric': fabric})

        first_result = run_persistent_plane_audit(fabric=fabric)
        first_run = AuditRun.objects.get(pk=first_result['audit_run'])
        first_findings = AuditFinding.objects.filter(fabric=fabric).order_by('pk')

        self.assertEqual(first_run.status, 'completed')
        self.assertEqual(first_run.finding_count, len(first_result['findings']))
        self.assertEqual(first_result['new_count'], len(first_result['findings']))
        self.assertTrue(first_findings.exists())
        self.assertTrue(all(finding.fingerprint for finding in first_findings))
        self.assertTrue(all(finding.first_seen_run_id == first_run.pk for finding in first_findings))

        second_result = run_persistent_plane_audit(fabric=fabric)
        second_run = AuditRun.objects.get(pk=second_result['audit_run'])

        self.assertEqual(second_run.status, 'completed')
        self.assertEqual(second_result['new_count'], 0)
        self.assertEqual(second_result['reopened_count'], 0)
        self.assertEqual(second_result['resolved_count'], 0)
        self.assertEqual(AuditFinding.objects.filter(fabric=fabric).count(), len(first_result['findings']))
        self.assertEqual(AuditFinding.objects.filter(fabric=fabric, active=True).count(), len(first_result['findings']))

    def test_persistent_plane_audit_marks_missing_port_mapping_findings_resolved(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Persistent Resolution Audit')
        rebuild_graph(scope={'fabric': fabric})

        first_result = run_persistent_plane_audit(fabric=fabric)

        self.assertEqual(
            AuditFinding.objects.filter(fabric=fabric, finding_type='missing_port_mapping', active=True).count(),
            2,
        )

        PortMapping.objects.create(
            device=self.device,
            front_port=topology['front_port'],
            front_port_position=1,
            rear_port=topology['rear_port'],
            rear_port_position=1,
        )

        second_result = run_persistent_plane_audit(fabric=fabric)

        self.assertGreaterEqual(first_result['new_count'], 2)
        self.assertEqual(second_result['resolved_count'], 2)
        self.assertEqual(
            AuditFinding.objects.filter(fabric=fabric, finding_type='missing_port_mapping', active=True).count(),
            0,
        )
        self.assertEqual(
            AuditFinding.objects.filter(fabric=fabric, finding_type='missing_port_mapping', status='resolved').count(),
            2,
        )

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_persistent_plane_audit_records_related_unresolved_summary_fingerprints(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Unresolved Linkage')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_port_mapping').order_by('pk').first()
        related_summaries = list_related_unresolved_summaries(fabric=fabric, audit_finding=finding, active_only=True)

        self.assertTrue(related_summaries)
        self.assertEqual(
            tuple(item.fingerprint for item in related_summaries),
            tuple(finding.metadata['related_unresolved_summary_fingerprints']),
        )

    def test_finding_state_transitions_record_events_and_assignment(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Finding Workflow Audit')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()

        acknowledge_audit_finding(finding=finding, actor=self.user)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'acknowledged')
        self.assertEqual(finding.acknowledged_by, self.user)
        self.assertIsNotNone(finding.acknowledged_at)

        start_audit_finding_remediation(finding=finding, actor=self.user)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'in_progress')
        self.assertEqual(finding.assigned_to, self.user)

        resolve_audit_finding(finding=finding, actor=self.user, note='Validated and cleared.')
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'resolved')
        self.assertFalse(finding.active)
        self.assertEqual(finding.resolution_summary, 'Validated and cleared.')

        reopen_audit_finding(finding=finding, actor=self.user)
        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')
        self.assertTrue(finding.active)
        self.assertIsNone(finding.resolved_at)

        event_types = list(finding.events.order_by('created', 'pk').values_list('event_type', flat=True))
        self.assertIn('opened', event_types)
        self.assertIn('status_changed', event_types)
        self.assertIn('resolved', event_types)
        self.assertIn('reopened', event_types)

    def test_persistent_plane_audit_preserves_acknowledged_state_for_active_findings(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Persistent Ack Preserve Audit')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        acknowledge_audit_finding(finding=finding, actor=self.user)

        run_persistent_plane_audit(fabric=fabric)

        finding.refresh_from_db()
        self.assertEqual(finding.status, 'acknowledged')
        self.assertTrue(finding.active)

    def test_persistent_plane_audit_preserves_suppressed_state_for_active_findings(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Persistent Suppress Preserve Audit')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        suppress_audit_finding(finding=finding, actor=self.user, reason='Known issue.')

        run_persistent_plane_audit(fabric=fabric)

        finding.refresh_from_db()
        self.assertEqual(finding.status, 'suppressed')
        self.assertTrue(finding.active)
        self.assertTrue(AuditSuppression.objects.filter(finding=finding, active=True).exists())

    def test_persistent_plane_audit_records_open_and_auto_resolved_events(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Event Lifecycle Audit')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        self.assertEqual(
            AuditFindingEvent.objects.filter(event_type='opened', finding__finding_type='missing_port_mapping').count(),
            2,
        )

        PortMapping.objects.create(
            device=self.device,
            front_port=topology['front_port'],
            front_port_position=1,
            rear_port=topology['rear_port'],
            rear_port_position=1,
        )

        run_persistent_plane_audit(fabric=fabric)

        self.assertEqual(
            AuditFindingEvent.objects.filter(event_type='auto_resolved', finding__finding_type='missing_port_mapping').count(),
            2,
        )

    def test_unsuppress_audit_finding_restores_open_state(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Unsuppress Audit')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        suppress_audit_finding(finding=finding, actor=self.user, reason='Temporary exception')

        unsuppress_audit_finding(finding=finding, actor=self.user)

        finding.refresh_from_db()
        self.assertEqual(finding.status, 'open')
        self.assertFalse(finding.suppressions.filter(active=True).exists())
        self.assertTrue(finding.events.filter(event_type='unsuppressed').exists())

    def test_audit_retention_expires_suppressions_and_reopens_findings(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Suppression Expiry Audit')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        suppression = suppress_audit_finding(finding=finding, actor=self.user, reason='Short-lived')
        suppression.expires_at = timezone.now() - timedelta(days=1)
        suppression.save(update_fields=('expires_at', 'last_updated'))

        result = apply_audit_retention(now=timezone.now())

        finding.refresh_from_db()
        suppression.refresh_from_db()
        self.assertEqual(result['expired_suppressions'], 1)
        self.assertFalse(suppression.active)
        self.assertEqual(finding.status, 'open')
        self.assertTrue(finding.events.filter(event_type='expired').exists())

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'top_level_menu': True, 'audit_run_retention_days': 1, 'audit_event_retention_days': 1, 'graph_build_run_retention_days': 1}})
    def test_audit_retention_prunes_old_inactive_runs_and_events(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Retention Audit')
        rebuild_graph(scope={'fabric': fabric})

        first_result = run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        resolve_audit_finding(finding=finding, actor=self.user, note='Closed for retention test.')
        old_run = AuditRun.objects.create(
            fabric=fabric,
            scope_type=finding.scope_type,
            scope_id=finding.scope_id,
            scope_label='Retention Test Scope',
            trigger_mode='manual',
            status='completed',
            started_at=timezone.now() - timedelta(days=10, minutes=1),
            completed_at=timezone.now() - timedelta(days=10),
        )
        old_event = AuditFindingEvent.objects.create(
            finding=finding,
            run=old_run,
            event_type='resolved',
            actor=self.user,
            old_status='open',
            new_status='resolved',
            message='Old retained event.',
        )
        old_event.created = timezone.now() - timedelta(days=10)
        old_event.save(update_fields=('created', 'last_updated'))
        old_build_run = GraphBuildRun.objects.create(
            fabric=fabric,
            scope_label='Retention Build Scope',
            trigger_mode='manual',
            status='completed',
            started_at=timezone.now() - timedelta(days=10, minutes=2),
            completed_at=timezone.now() - timedelta(days=10, minutes=1),
            stats={'nodes': 2},
        )

        result = apply_audit_retention(now=timezone.now())

        self.assertGreaterEqual(result['deleted_events'], 1)
        self.assertGreaterEqual(result['deleted_runs'], 1)
        self.assertGreaterEqual(result['deleted_build_runs'], 1)
        self.assertFalse(AuditRun.objects.filter(pk=old_run.pk).exists())
        self.assertFalse(AuditFindingEvent.objects.filter(pk=old_event.pk).exists())
        self.assertFalse(GraphBuildRun.objects.filter(pk=old_build_run.pk).exists())

    def test_audit_workflow_summary_includes_counts_runs_events_and_expiring_suppressions(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Workflow Summary')
        rebuild_graph(scope={'fabric': fabric})

        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=10)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        resolve_audit_finding(finding=finding, actor=self.user, note='Closed for churn test')
        reopen_audit_finding(finding=finding, actor=self.user, note='Recurrence observed')
        suppress_audit_finding(finding=finding, actor=self.user, reason='Short maintenance window', days=2)

        summary = build_audit_workflow_summary(fabric=fabric)

        self.assertEqual(summary.fabric.display, 'Fabric Audit Workflow Summary')
        self.assertGreaterEqual(summary.total_findings, 1)
        self.assertGreaterEqual(summary.active_findings, 1)
        self.assertEqual(summary.suppressed_findings, 1)
        self.assertGreaterEqual(summary.stale_findings_7d, 1)
        self.assertTrue(summary.recent_runs)
        self.assertTrue(any(event.event_type == 'suppressed' for event in summary.recent_events))
        self.assertEqual(summary.churn_windows[0].days, 7)
        self.assertGreaterEqual(summary.churn_windows[0].opened_count, 1)
        self.assertGreaterEqual(summary.churn_windows[0].reopened_count, 1)
        self.assertGreaterEqual(summary.churn_windows[0].resolved_count, 1)
        self.assertGreaterEqual(summary.churn_windows[0].suppressed_count, 1)
        self.assertTrue(summary.oldest_active_findings)
        self.assertEqual(summary.expiring_suppressions[0].reason, 'Short maintenance window')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_unresolved_state_dashboard_reports_oldest_and_reopened_summaries(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Unresolved Dashboard')
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

        dashboard = build_unresolved_state_dashboard(fabric=fabric)

        self.assertEqual(dashboard.active_total, 2)
        self.assertGreaterEqual(dashboard.stale_active_7d, 0)
        self.assertEqual(dashboard.recurring_total, 2)
        self.assertEqual(dashboard.reopened_total, 2)
        self.assertTrue(dashboard.oldest_active_summaries)
        self.assertEqual(dashboard.oldest_active_summaries[0].cause_code, 'missing_port_mapping')
        self.assertEqual(dashboard.oldest_active_summaries[0].reopen_count, 1)
        self.assertGreaterEqual(dashboard.oldest_active_summaries[0].build_count, 2)
        self.assertEqual(
            UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping', active=True).count(),
            2,
        )

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {
        'persist_unresolved_summaries': True,
        'unresolved_reporting_cache_enabled': True,
        'unresolved_reporting_cache_timeout': 60,
    }})
    def test_unresolved_reporting_cache_invalidates_on_graph_revision_change(self):
        cache.clear()
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Unresolved Cache Invalidation')
        rebuild_graph(scope={'fabric': fabric})
        fabric.refresh_from_db()
        first_revision = (fabric.metadata or {}).get('graph_revision')

        initial_overview = build_unresolved_state_overview(fabric=fabric)
        initial_dashboard = build_unresolved_state_dashboard(fabric=fabric)

        self.assertEqual(initial_overview.active_total, 2)
        self.assertEqual(initial_dashboard.active_total, 2)
        self.assertTrue(first_revision)

        PortMapping.objects.create(
            device=self.device,
            front_port=topology['front_port'],
            front_port_position=1,
            rear_port=topology['rear_port'],
            rear_port_position=1,
        )

        rebuild_graph(scope={'fabric': fabric})
        fabric.refresh_from_db()
        second_revision = (fabric.metadata or {}).get('graph_revision')

        refreshed_overview = build_unresolved_state_overview(fabric=fabric)
        refreshed_dashboard = build_unresolved_state_dashboard(fabric=fabric)

        self.assertNotEqual(first_revision, second_revision)
        self.assertEqual(refreshed_overview.active_total, 0)
        self.assertEqual(refreshed_overview.resolved_total, 2)
        self.assertEqual(refreshed_dashboard.active_total, 0)
        self.assertEqual(refreshed_dashboard.recurring_total, 0)

    def test_durable_audit_finding_search_filters_by_suppression_and_age(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Durable Search')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=12)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        suppress_audit_finding(finding=finding, actor=self.user, reason='Maintenance window', days=4)

        suppressed_results = build_durable_audit_finding_search(
            fabric=fabric,
            suppressed=True,
            min_age_days=7,
        )

        self.assertEqual(len(suppressed_results), 1)
        self.assertEqual(suppressed_results[0].finding.pk, finding.pk)
        self.assertEqual(suppressed_results[0].active_suppression.reason, 'Maintenance window')
        self.assertGreaterEqual(suppressed_results[0].age_days, 7)

        unsuppressed_results = build_durable_audit_finding_search(
            fabric=fabric,
            suppressed=False,
            min_age_days=7,
        )
        self.assertTrue(all(item.finding.pk != finding.pk for item in unsuppressed_results))

    def test_durable_audit_finding_detail_includes_recent_events(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Durable Detail')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        acknowledge_audit_finding(finding=finding, actor=self.user, note='Triaged')
        suppress_audit_finding(finding=finding, actor=self.user, reason='Documented exception', days=2)

        detail = build_durable_audit_finding_detail(finding=finding)

        self.assertEqual(detail.finding.finding.pk, finding.pk)
        self.assertEqual(detail.finding.status, 'suppressed')
        self.assertEqual(detail.finding.active_suppression.reason, 'Documented exception')
        self.assertTrue(any(event.event_type == 'suppressed' for event in detail.recent_events))
        self.assertTrue(any(event.new_status == 'suppressed' for event in detail.recent_events))

    def test_audit_run_timeline_returns_scope_labels(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Run Timeline')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        runs = build_audit_run_timeline(fabric=fabric, limit=5)

        self.assertTrue(runs)
        self.assertEqual(runs[0].run.display, str(AuditRun.objects.filter(fabric=fabric).first()))
        self.assertTrue(runs[0].scope_label)

    def test_resolve_path_traces_multiplane_shuffle_fixture_from_child_interface(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Multiplane Resolve')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_path(
            source=topology['host_children'][2],
            destination=topology['leaf_children'][3],
        )

        self.assertTrue(result['path_found'])
        self.assertEqual(result['summary']['coarse_edges_crossed'], 2)
        self.assertEqual(result['summary']['transfer_maps_crossed'], 1)
        self.assertEqual(result['summary']['shuffle_modules_crossed'], 1)
        self.assertEqual(result['summary']['planes_touched'], [2, 3] if HAS_NATIVE_CABLE_PROFILES else [1, 2, 3, 4])
        path_displays = {step['display'] for step in result['path'] if isinstance(step, dict) and 'display' in step}
        self.assertIn('nic0/plane2', path_displays)
        self.assertIn('Ethernet1/1/plane3', path_displays)

    def test_resolve_path_with_plane_filter_uses_propagated_passive_memberships(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Plane Filter Resolve')
        rebuild_graph(scope={'fabric': fabric})
        plane = FabricPlane.objects.get(fabric=fabric, plane_number=2)

        result = resolve_path(
            source=topology['host_children'][2],
            destination=topology['leaf_children'][3],
            plane=plane,
        )

        self.assertTrue(result['path_found'])

    def test_resolve_path_supports_signal_lane_resolution(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Signal Lane Resolve')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_path(
            source=topology['host_children'][2],
            destination=topology['leaf_children'][3],
            resolution='signal_lane',
        )

        self.assertTrue(result['path_found'])
        self.assertEqual(result['resolution'], 'signal_lane')
        self.assertTrue(any(step.get('kind') == 'lane_map' for step in result['path'] if isinstance(step, dict)))

    def test_plane_audit_reports_cross_plane_shuffle_contamination(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Multiplane Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding_types = {finding['finding_type'] for finding in result['findings']}
        shared_finding = next(finding for finding in result['findings'] if finding['finding_type'] == 'shared_passive_artifact')

        self.assertIn('multi_plane_attachment', finding_types)
        self.assertIn('shared_passive_artifact', finding_types)
        self.assertEqual(shared_finding['metadata']['rule_id'], 'shared_passive_artifact')
        self.assertTrue(shared_finding['metadata']['plane_pair_ids'])
        self.assertTrue(shared_finding['metadata']['contamination_domain_key'])

        shuffle_attachment_names = set(
            AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric,
                termination_point__plant_node__name='Shuffle Module',
            ).values_list('name', flat=True)
        )
        finding_messages = ' '.join(finding['message'] for finding in result['findings'])
        self.assertTrue(shuffle_attachment_names)
        self.assertIn('multiple planes', finding_messages)

    def test_plane_audit_reports_blank_profile_fanout_cable(self):
        topology = self.build_blank_profile_breakout_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Profile Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('missing_cable_profile', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_cable'].pk
            for finding in findings_by_type['missing_cable_profile']
        ))
        self.assertTrue(any(
            'explicit profile' in finding['message']
            for finding in findings_by_type['missing_cable_profile']
        ))

    def test_plane_audit_accepts_plugin_breakout_profile_on_cable_custom_field(self):
        self.build_profile_breakout_without_child_interfaces_topology()
        fabric = Fabric.objects.create(name='Fabric Plugin Breakout Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertNotIn('missing_cable_profile', findings_by_type)
        self.assertTrue(findings_by_type)

    def test_plane_audit_reports_profile_breakout_without_child_interfaces(self):
        topology = self.build_profile_breakout_without_child_interfaces_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Children Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('missing_child_interface', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_parent'].pk
            for finding in findings_by_type['missing_child_interface']
        ))
        self.assertTrue(any(
            'explicit child interfaces' in finding['message']
            for finding in findings_by_type['missing_child_interface']
        ))

    def test_plane_audit_reports_incomplete_child_interface_set(self):
        topology = self.build_profile_breakout_with_partial_child_interfaces_topology(child_count=2)
        fabric = Fabric.objects.create(name='Fabric Partial Children Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('incomplete_child_interface_set', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_parent'].pk
            for finding in findings_by_type['incomplete_child_interface_set']
        ))
        self.assertTrue(any(
            finding['metadata'].get('expected_child_count') == 4 and finding['metadata'].get('actual_child_count') == 2
            for finding in findings_by_type['incomplete_child_interface_set']
        ))

    def test_plane_audit_reports_orphaned_child_interface(self):
        topology = self.build_orphaned_child_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Orphaned Child Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('orphaned_attachment_unit', findings_by_type)
        self.assertTrue(any(
            finding['object']['display'] == topology['orphan_child'].name
            for finding in findings_by_type['orphaned_attachment_unit']
        ))

    def test_plane_audit_reports_missing_port_mapping_for_cabled_passive_ports(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Port Mapping Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('missing_port_mapping', findings_by_type)
        missing_objects = {finding['object']['display'] for finding in findings_by_type['missing_port_mapping']}
        self.assertIn(topology['front_port'].name, missing_objects)
        self.assertIn(topology['rear_port'].name, missing_objects)
        self.assertTrue(any(
            finding['metadata'].get('mapping_side') == 'front'
            for finding in findings_by_type['missing_port_mapping']
        ))
        self.assertTrue(any(
            finding['metadata'].get('mapping_side') == 'rear'
            for finding in findings_by_type['missing_port_mapping']
        ))

    def test_lane_drilldown_returns_lane_groups_for_core_child_interface(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Drilldown')
        rebuild_graph(scope={'fabric': fabric})

        result = build_lane_drilldown(target=topology['host_children'][2])

        self.assertEqual(result['total_attachment_units'], 1)
        self.assertEqual(result['total_signal_lanes'], 4)
        self.assertEqual(result['available_lane_indexes'], [0, 1, 2, 3])
        self.assertEqual(result['attachment_units'][0]['attachment_unit']['display'], topology['host_children'][2].name)
        self.assertEqual(len(result['attachment_units'][0]['lanes']), 4)

    def test_typed_lane_drilldown_wraps_lane_groups(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Typed Lane Drilldown')
        rebuild_graph(scope={'fabric': fabric})

        result = build_typed_lane_drilldown(target=topology['host_children'][2], lane_index=1)

        self.assertEqual(result.target.display, topology['host_children'][2].name)
        self.assertEqual(result.lane_index, 1)
        self.assertEqual(result.total_signal_lanes, 1)
        self.assertEqual(result.attachment_units[0].lanes[0].display, 'nic0/plane2:l1')

    def test_plane_audit_reports_partial_profile_mapping(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Partial Profile Mapping Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('partial_profile_mapping', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_cable'].pk
            for finding in findings_by_type['partial_profile_mapping']
        ))
        self.assertTrue(any(
            finding['metadata'].get('unresolved_positions') == 1
            for finding in findings_by_type['partial_profile_mapping']
        ))

    def test_compute_fabric_health_summarizes_plane_and_finding_state(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Health Summary')
        rebuild_graph(scope={'fabric': fabric})

        result = compute_fabric_health(fabric=fabric)

        self.assertEqual(result['fabric']['pk'], fabric.pk)
        self.assertEqual(result['summary']['planes_total'], 4)
        self.assertEqual(len(result['planes']), 4)
        self.assertIn(result['status'], {'warning', 'error'})

    def test_typed_fabric_health_wraps_summary(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Typed Health Summary')
        rebuild_graph(scope={'fabric': fabric})

        result = compute_typed_fabric_health(fabric=fabric)

        self.assertEqual(result.fabric.pk, fabric.pk)
        self.assertEqual(result.summary.planes_total, 4)
        self.assertEqual(len(result.planes), 4)

    def test_typed_lane_path_resolves_specific_lane_index(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Typed Lane Path')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_typed_lane_path(
            source=topology['host_children'][2],
            source_lane_index=2,
            destination=topology['leaf_children'][3],
            destination_lane_index=2,
        )

        self.assertTrue(result.path_found)
        self.assertEqual(result.source_lane_index, 2)
        self.assertEqual(result.destination_lane_index, 2)
        self.assertTrue(any(step.signal_lane and step.signal_lane.display == 'nic0/plane2:l2' for step in result.steps))
        self.assertTrue(any(step.owner_node and step.owner_node.display == 'Shuffle Module' for step in result.steps))

    def test_lane_set_for_attachment_unit_summarizes_lane_membership(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Set Attachment')
        rebuild_graph(scope={'fabric': fabric})
        attachment_unit = AttachmentUnit.objects.get(source_id=topology['host_children'][2].pk)

        result = build_lane_set_for_attachment_unit(attachment_unit)

        self.assertEqual(result.scope_kind, 'attachment_unit')
        self.assertEqual(result.total_attachment_units, 1)
        self.assertEqual(result.expected_lane_total, 4)
        self.assertEqual(result.present_lane_total, 4)
        self.assertEqual(result.mapped_lane_total, 4)
        self.assertEqual(result.plane_ids, (2, 3) if HAS_NATIVE_CABLE_PROFILES else (1, 2, 3, 4))
        self.assertEqual(result.lane_map_consistency, 'consistent')

    def test_lane_set_for_parent_interface_reports_unmatched_peer_positions(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Set Parent')
        rebuild_graph(scope={'fabric': fabric})

        result = build_lane_set_for_parent_interface(topology['host_parent'])

        self.assertEqual(result.scope_kind, 'parent_interface')
        self.assertEqual(result.total_attachment_units, 4)
        self.assertEqual(result.present_lane_total, 16)
        self.assertEqual(result.mapped_lane_total, 12)
        self.assertEqual(result.unmatched_peer_positions, (4,))
        self.assertEqual(result.lane_map_consistency, 'partial')

    def test_lane_set_for_coarse_edge_summarizes_participating_lanes(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Set Coarse Edge')
        rebuild_graph(scope={'fabric': fabric})
        coarse_edge = CoarseEdge.objects.order_by('pk').first()

        result = build_lane_set_for_coarse_edge(coarse_edge)

        self.assertEqual(result.scope_kind, 'coarse_edge')
        self.assertEqual(result.total_attachment_units, 2)
        self.assertEqual(result.present_lane_total, 8)
        self.assertEqual(result.mapped_lane_total, 8)

    def test_lane_set_for_plant_node_marks_cross_plane_passive_node_as_mixed(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Set Plant Node')
        rebuild_graph(scope={'fabric': fabric})
        plant_node = PlantNode.objects.get(fabric=fabric, name='Shuffle Module')

        result = build_lane_set_for_plant_node(plant_node)

        self.assertEqual(result.scope_kind, 'plant_node')
        self.assertEqual(result.total_attachment_units, 8)
        self.assertEqual(result.plane_consistency, 'mixed')
        self.assertGreaterEqual(len(result.plane_ids), 4)

    def test_lane_set_for_plane_summarizes_membership_scope(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Set Plane')
        rebuild_graph(scope={'fabric': fabric})
        plane = FabricPlane.objects.get(fabric=fabric, plane_number=2)

        result = build_lane_set_for_plane(plane)

        self.assertEqual(result.scope_kind, 'plane')
        self.assertEqual(result.plane.pk, plane.pk)
        self.assertGreaterEqual(result.total_attachment_units, 1)
        self.assertGreaterEqual(result.present_lane_total, 4)

    def test_generic_lane_set_and_allocation_summary_dispatch_by_target(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Allocation Summary')
        rebuild_graph(scope={'fabric': fabric})

        lane_set = build_lane_set(topology['host_parent'])
        summary = build_lane_allocation_summary(target=topology['host_parent'])

        self.assertEqual(lane_set.scope_kind, 'parent_interface')
        self.assertEqual(summary.scope_kind, 'parent_interface')
        self.assertEqual(summary.expected_lane_total, 16)
        self.assertEqual(summary.present_lane_total, 16)
        self.assertEqual(summary.mapped_lane_total, 12)
        self.assertEqual(summary.unmatched_peer_positions, (4,))
        self.assertEqual(summary.incomplete_attachment_units, 1)

    def test_lane_workspace_normalizes_invalid_group_by_and_builds_plane_groups(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Workspace Service')
        rebuild_graph(scope={'fabric': fabric})

        query = normalize_lane_workspace_query(
            target_registry_key='interface',
            target_id=topology['host_parent'].pk,
            group_by='invalid',
        )
        workspace = build_lane_workspace(target=topology['host_parent'], group_by='invalid')

        self.assertEqual(query.group_by, 'attachment')
        self.assertEqual(query.group_by_label, 'Attachment')
        self.assertEqual(workspace.group_by, 'attachment')
        self.assertTrue(workspace.node_groups)
        self.assertTrue(workspace.plane_groups)
        self.assertTrue(any(group.label == 'Plane 2' and group.reference is not None for group in workspace.plane_groups))

    def test_lane_workspace_normalizes_invalid_mode_focus_lane_and_plane_inputs(self):
        query = normalize_lane_workspace_query(
            target_registry_key='interface',
            target_id='123',
            group_by='invalid',
            mode='bogus',
            focus='not-real',
            group_key='node:9',
            lane_index='bad',
            path_lane_index='also-bad',
            plane_id='bad',
            source_finding_id='nope',
            export_format='yaml',
        )

        self.assertEqual(query.group_by, 'attachment')
        self.assertEqual(query.mode, 'grouped')
        self.assertEqual(query.focus, 'summary')
        self.assertEqual(query.group_key, 'node:9')
        self.assertIsNone(query.lane_index)
        self.assertIsNone(query.path_lane_index)
        self.assertIsNone(query.plane_id)
        self.assertIsNone(query.source_finding_id)
        self.assertIsNone(query.export_format)
        self.assertGreaterEqual(len(query.notes), 7)

    def test_lane_workspace_for_signal_lane_infers_lane_mode_and_selected_attachment_group(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Signal Lane Workspace Service')
        rebuild_graph(scope={'fabric': fabric})
        signal_lane = SignalLane.objects.filter(
            attachment_unit__source_id=topology['host_children'][2].pk
        ).order_by('lane_index', 'pk').first()

        workspace = build_lane_workspace(target=signal_lane)

        self.assertEqual(workspace.mode, 'lane')
        self.assertEqual(workspace.active_lane_index, signal_lane.lane_index)
        self.assertIsNotNone(workspace.lane_view)
        self.assertTrue(workspace.selected_group_rows)
        self.assertEqual(workspace.selected_group_rows[0].attachment_unit.pk, signal_lane.attachment_unit_id)

    def test_lane_workspace_groups_paths_deterministically(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Path Workspace Service')
        rebuild_graph(scope={'fabric': fabric})

        workspace = build_lane_workspace(target=topology['host_parent'], group_by='path')
        rebuilt_workspace = build_lane_workspace(target=topology['host_parent'], group_by='path')

        self.assertEqual(workspace.group_by, 'path')
        self.assertTrue(workspace.path_groups)
        self.assertIsNotNone(workspace.selected_path_group)
        self.assertIsNotNone(workspace.selected_path_view)
        self.assertEqual(
            tuple(group.key for group in workspace.path_groups),
            tuple(group.key for group in rebuilt_workspace.path_groups),
        )
        self.assertEqual(
            tuple(group.representative_lane_index for group in workspace.path_groups),
            tuple(group.representative_lane_index for group in rebuilt_workspace.path_groups),
        )
        self.assertTrue(all(group.representative_path is not None for group in workspace.path_groups))
        self.assertTrue(all(group.attachment_unit_ids for group in workspace.path_groups))

    def test_lane_workspace_honors_selected_representative_path_lane(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Path Lane Selection Workspace Service')
        rebuild_graph(scope={'fabric': fabric})

        base_workspace = build_lane_workspace(target=topology['host_parent'], group_by='path')
        path_group = next(
            (group for group in base_workspace.path_groups if len(group.lane_indexes) > 1),
            base_workspace.path_groups[0],
        )
        requested_lane = path_group.lane_indexes[-1]

        workspace = build_lane_workspace(
            target=topology['host_parent'],
            group_by='path',
            group_key=path_group.key,
            path_lane_index=requested_lane,
        )

        self.assertEqual(workspace.selected_path_group.key, path_group.key)
        self.assertEqual(workspace.active_path_lane_index, requested_lane)
        self.assertEqual(workspace.selected_path_group.representative_lane_index, requested_lane)

    def test_lane_workspace_builds_compare_context_actions_and_backlink(self):
        baseline = self.build_multiplane_shuffle_topology()
        candidate_site = Site.objects.create(name='Workspace Compare Candidate Site', slug='workspace-compare-candidate-site')
        candidate = self.build_profile_breakout_with_missing_peer_positions_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Workspace Compare Service'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Workspace Compare Service'
        candidate['shuffle_device'].save()
        fabric = Fabric.objects.create(name='Fabric Workspace Compare Service')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric).first()

        query = normalize_lane_workspace_query(
            target_registry_key='interface',
            target_id=baseline['host_parent'].pk,
            group_by='path',
            focus='compare',
            compare_registry_key='interface',
            compare_id=candidate['host_parent'].pk,
            source_finding_id=finding.pk,
        )
        workspace = build_lane_workspace(
            target=baseline['host_parent'],
            compare_target=candidate['host_parent'],
            source_finding=finding,
            query=query,
        )

        self.assertIsNotNone(workspace.compare_context)
        self.assertIsNotNone(workspace.compare_context.full_compare_action)
        self.assertIsNotNone(workspace.source_finding_backlink)
        self.assertTrue(workspace.next_actions)
        self.assertEqual(len(workspace.export_links), 2)

    def test_policy_evaluation_builds_contamination_domains_for_multiplane_shuffle(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Evaluation')
        rebuild_graph(scope={'fabric': fabric})

        evaluation = build_policy_evaluation(fabric=fabric)

        self.assertEqual(evaluation.policy_mode, fabric.disjointness_policy)
        self.assertTrue(evaluation.artifact_shares)
        self.assertTrue(evaluation.contamination_domains)
        domain = evaluation.contamination_domains[0]
        self.assertTrue(domain.plane_pair_ids)
        self.assertTrue(domain.evidence_keys)
        self.assertGreaterEqual(domain.signal_lane_count, 4)
        self.assertTrue(any(reference.display == 'Shuffle Module' for reference in domain.artifacts))

    def test_contamination_domains_group_related_policy_evidence_deterministically(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Contamination Domains')
        rebuild_graph(scope={'fabric': fabric})

        evaluation = build_policy_evaluation(fabric=fabric)
        rebuilt_domains = build_contamination_domains(
            fabric=fabric,
            artifact_shares=evaluation.artifact_shares,
            edge_bridges=evaluation.edge_bridges,
        )

        self.assertEqual(
            tuple(domain.domain_key for domain in evaluation.contamination_domains),
            tuple(domain.domain_key for domain in rebuilt_domains),
        )

    def test_policy_evaluation_marks_covered_artifact_shares_with_active_exception(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Covered Exception')
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
            status='approved',
            active=True,
        )

        evaluation = build_policy_evaluation(fabric=fabric)
        finding = next(
            item
            for item in evaluation.findings
            if item['finding_type'] == 'shared_passive_artifact' and item['object']['pk'] == shuffle_node.pk
        )

        self.assertEqual(finding['severity'], 'info')
        self.assertEqual(finding['metadata']['exception_status'], 'covered')
        self.assertIn(exception.pk, finding['metadata']['exception_ids'])
        self.assertIn('covered by an active disjointness exception', finding['message'])

    def test_disjointness_exception_review_reports_covered_and_drifted_exceptions(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Exception Review')
        rebuild_graph(scope={'fabric': fabric})
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

        evaluation = build_policy_evaluation(fabric=fabric)
        review = build_disjointness_exception_review(fabric=fabric, evaluation=evaluation)

        self.assertIn(covered_exception.pk, review['covered_exception_ids'])
        self.assertTrue(any(matches for matches in review['domain_matches'].values()))
        self.assertEqual([item.pk for item in review['drifted_exceptions']], [drifted_exception.pk])

    def test_disjointness_exception_lifecycle_transitions(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Exception Lifecycle')
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
            expires_at=timezone.now() - timedelta(days=1),
        )

        approve_disjointness_exception(exception=exception, actor=self.user)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'approved')
        self.assertTrue(exception.active)
        self.assertEqual(exception.approved_by, self.user)
        self.assertIsNone(exception.expires_at)

        expire_disjointness_exception(exception=exception, actor=self.user)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'expired')
        self.assertFalse(exception.active)
        self.assertIsNotNone(exception.expires_at)

        reactivate_disjointness_exception(exception=exception, actor=self.user)
        exception.refresh_from_db()
        self.assertEqual(exception.status, 'approved')
        self.assertTrue(exception.active)
        self.assertEqual(exception.approved_by, self.user)

    def test_policy_dashboard_reports_plane_pairs_domains_and_oldest_findings(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Dashboard')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
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
        finding = AuditFinding.objects.filter(
            fabric=fabric,
            finding_type__in=('shared_passive_artifact', 'cross_plane_fine_edge'),
            active=True,
        ).earliest('pk')
        finding.first_seen_at = timezone.now() - timedelta(days=12)
        finding.save(update_fields=('first_seen_at', 'last_updated'))

        dashboard = build_policy_dashboard(fabric=fabric)

        self.assertEqual(dashboard.fabric.display, 'Fabric Policy Dashboard')
        self.assertGreaterEqual(dashboard.contamination_domain_count, 1)
        self.assertEqual(dashboard.active_exception_count, 1)
        self.assertEqual(dashboard.covered_exception_count, 1)
        self.assertTrue(dashboard.plane_pairs)
        self.assertTrue(dashboard.top_domains)
        self.assertTrue(dashboard.oldest_active_findings)
        self.assertEqual(dashboard.oldest_active_findings[0].age_days, 12)
        self.assertEqual(dashboard.top_domains[0].coverage_status, 'covered')

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'policy_reporting_cache_enabled': True, 'policy_reporting_cache_timeout': 60}})
    def test_policy_reporting_cache_invalidates_on_graph_revision_change(self):
        cache.clear()
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Policy Cache Invalidation')
        rebuild_graph(scope={'fabric': fabric})
        fabric.refresh_from_db()
        first_revision = (fabric.metadata or {}).get('graph_revision')

        initial_summary = build_policy_summary(fabric=fabric)

        self.assertEqual(initial_summary.contamination_domain_count, 0)
        self.assertTrue(first_revision)

        self.build_multiplane_shuffle_topology()
        rebuild_graph(scope={'fabric': fabric})
        fabric.refresh_from_db()
        second_revision = (fabric.metadata or {}).get('graph_revision')

        refreshed_summary = build_policy_summary(fabric=fabric)

        self.assertNotEqual(first_revision, second_revision)
        self.assertGreaterEqual(refreshed_summary.contamination_domain_count, 1)

    def test_compare_lane_allocations_reports_policy_domain_regressions(self):
        baseline = self.build_profile_breakout_without_child_interfaces_topology()
        candidate_site = Site.objects.create(name='Compare Policy Candidate Site', slug='compare-policy-candidate-site')
        candidate = self.build_multiplane_shuffle_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Policy Candidate'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Policy Candidate'
        candidate['shuffle_device'].save()
        fabric = Fabric.objects.create(name='Fabric Policy Compare')
        rebuild_graph(scope={'fabric': fabric})

        result = compare_lane_allocations(
            baseline_target=baseline['host_parent'],
            candidate_target=candidate['host_parent'],
        )

        self.assertTrue(any(delta.status == 'added' for delta in result.policy_domain_deltas))
        self.assertTrue(any('Candidate introduces new contamination domains' in item for item in result.policy_regressions))
        self.assertTrue(any(
            'Shuffle Module' in artifact.display
            for delta in result.policy_domain_deltas
            for artifact in delta.artifacts
        ))

    def test_audit_finding_detail_for_missing_port_mapping_contains_guided_actions(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Port Mapping Detail')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding = next(finding for finding in result['findings'] if finding['finding_type'] == 'missing_port_mapping')

        detail = build_audit_finding_detail(finding)

        self.assertIn('PortMapping row', detail.summary)
        self.assertTrue(any('Create a PortMapping row' in hint for hint in detail.remediation_hints))
        self.assertTrue(any(action.label == 'Lane Workspace' for action in detail.action_links))
        self.assertEqual(detail.impact.present_lane_total, 0)

    def test_audit_finding_detail_for_partial_profile_mapping_links_related_targets(self):
        self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric Partial Profile Mapping Detail')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding = next(finding for finding in result['findings'] if finding['finding_type'] == 'partial_profile_mapping')

        detail = build_audit_finding_detail(finding)

        self.assertIn('Profile-derived mapping is incomplete', detail.summary)
        self.assertTrue(detail.impact.related_targets)
        self.assertIn(4, detail.impact.unmatched_peer_positions)
        self.assertTrue(any(action.label.startswith('Workspace:') for action in detail.action_links))

    def test_compare_lane_allocations_highlights_candidate_regressions(self):
        baseline = self.build_multiplane_shuffle_topology()
        candidate_site = Site.objects.create(name='Compare Candidate Site', slug='compare-candidate-site')
        candidate = self.build_profile_breakout_with_missing_peer_positions_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Candidate'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Candidate'
        candidate['shuffle_device'].save()

        fabric = Fabric.objects.create(name='Fabric Lane Compare')
        rebuild_graph(scope={'fabric': fabric})

        result = compare_lane_allocations(
            baseline_target=baseline['host_parent'],
            candidate_target=candidate['host_parent'],
        )

        self.assertEqual(result.baseline_summary.mapped_lane_total, 16)
        self.assertEqual(result.candidate_summary.mapped_lane_total, 12)
        self.assertTrue(any(metric.name == 'mapped_lane_total' and metric.status == 'regressed' for metric in result.metrics))
        self.assertIn('Mapping symmetry regressed: fewer lanes are mapped in the candidate scope.', result.regressions)
        self.assertTrue(any(diff.compare_key == 'position:4' and 'mapped_lane_count' in diff.changed_fields for diff in result.attachment_diffs))
        self.assertTrue(result.representative_reviews)
