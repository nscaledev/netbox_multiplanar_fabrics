from django.test import TestCase

from netbox_plant_graph.graphql import schema as graphql_schema_module
from netbox_plant_graph.graphql.schema import (
    _audit_finding_detail,
    _audit_finding_search,
    _audit_run_timeline,
    _audit_workflow_summary,
    _audit_events,
    _blast_radius,
    _contamination_domains,
    _deployment_workflow_summary,
    _fabrics,
    _graphql_contract_version,
    _lane_compare,
    _lane_drilldown,
    _operation_runs,
    _optical_lanes,
    _policy_dashboard,
    _policy_summary,
    _stamp_template_preview,
    _stamp_runs,
    _suppression_rules,
)
from netbox_plant_graph.models import AuditEvent, OperationRun, SuppressionRule
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.audit import acknowledge_audit_finding
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric


class V2GraphQLQuerySurfaceTestCase(TestCase):
    def test_schema_exports_primary_query(self):
        self.assertTrue(graphql_schema_module)
        self.assertEqual(_graphql_contract_version(), '2.0.0')

    def _seed_operational_context(self):
        result = stamp_roce_4plane_mini_fabric()
        SuppressionRule.objects.create(
            fabric=result.fabric,
            optical_lane=result.source_lanes[0],
            plane=result.source_lanes[0].plane,
            status='active',
            reason='GraphQL suppression',
            policy_key='path_resolution',
        )
        finding = AuditEvent.objects.create(
            fabric=result.fabric,
            event_type='policy_eval',
            outcome='warning',
            message='Cross-plane contamination domain detected.',
            payload={
                'finding_type': 'cross_plane_overlap',
                'severity': 'error',
                'metadata': {
                    'plane_ids': [result.source_lanes[0].plane_id, result.destination_lanes[0].plane_id],
                    'contamination_domain_key': 'domain-alpha',
                },
            },
            metadata={},
        )
        acknowledge_audit_finding(finding=finding, note='Acknowledged from GraphQL test.')
        AuditEvent.objects.create(
            fabric=result.fabric,
            event_type='stamp',
            outcome='ok',
            message='GraphQL event',
            payload={},
        )
        OperationRun.objects.create(
            profile='generic_roce',
            status='completed',
            fabric=result.fabric,
            parameters={},
            result={'ok': True},
        )
        SuppressionRule.objects.create(
            fabric=result.fabric,
            plane=result.source_lanes[0].plane,
            status='active',
            reason='Temporary disjointness exception',
            policy_key='disjointness_exception',
            metadata={'kind': 'disjointness_exception'},
        )
        return result, finding

    def test_existing_expanded_graphql_resolvers_return_data(self):
        result, _finding = self._seed_operational_context()

        self.assertGreaterEqual(len(_fabrics()), 1)
        self.assertGreaterEqual(len(_optical_lanes(fabric_id=result.fabric.pk)), 1)
        self.assertGreaterEqual(len(_stamp_runs(fabric_id=result.fabric.pk)), 1)
        self.assertGreaterEqual(len(_suppression_rules(fabric_id=result.fabric.pk)), 1)
        self.assertGreaterEqual(len(_audit_events(fabric_id=result.fabric.pk)), 1)
        self.assertGreaterEqual(len(_operation_runs(fabric_id=result.fabric.pk)), 1)

    def test_lane_operational_queries_return_v2_payloads(self):
        result, _finding = self._seed_operational_context()
        source_lane = result.source_lanes[0]
        candidate_lane = result.source_lanes[1]

        drilldown = _lane_drilldown(source_lane_id=source_lane.pk)
        self.assertEqual(drilldown['mode'], 'path')
        self.assertEqual(drilldown['source']['lane_id'], source_lane.pk)
        self.assertIn('resolved_path', drilldown)

        inventory = _lane_drilldown(
            target_registry_key='endpoint',
            target_id=source_lane.endpoint_id,
            lane_index=source_lane.lane_index,
        )
        self.assertEqual(inventory['mode'], 'inventory')
        self.assertGreaterEqual(inventory['total_attachment_units'], 1)
        self.assertIn(source_lane.lane_index, inventory['available_lane_indexes'])

        comparison = _lane_compare(
            baseline_source_lane_id=source_lane.pk,
            candidate_source_lane_id=candidate_lane.pk,
        )
        self.assertIn('baseline_path', comparison)
        self.assertIn('candidate_path', comparison)
        self.assertIn('checks', comparison)
        self.assertIn('deltas', comparison)

        blast = _blast_radius(
            target_registry_key='optical_lane',
            target_id=source_lane.pk,
            resolution='signal_lane',
        )
        self.assertEqual(blast['mode'], 'path_impact')
        self.assertGreaterEqual(blast['impacted_lane_count'], 1)

    def test_audit_workflow_queries_return_summary_search_detail_and_timeline(self):
        result, finding = self._seed_operational_context()

        summary = _audit_workflow_summary(fabric_id=result.fabric.pk)
        self.assertGreaterEqual(summary['total_findings'], 1)
        self.assertGreaterEqual(summary['active_findings'], 1)
        self.assertGreaterEqual(len(summary['recent_runs']), 1)

        rows = _audit_finding_search(
            fabric_id=result.fabric.pk,
            finding_type='cross_plane_overlap',
            limit=10,
        )
        self.assertGreaterEqual(len(rows), 1)
        self.assertIn(finding.pk, [row['id'] for row in rows])

        detail = _audit_finding_detail(finding_id=finding.pk)
        self.assertEqual(detail['finding']['id'], finding.pk)
        self.assertGreaterEqual(detail['timeline_event_count'], 1)

        timeline = _audit_run_timeline(fabric_id=result.fabric.pk, limit=10)
        self.assertGreaterEqual(len(timeline), 1)
        self.assertTrue(any(row['run_type'] == 'operation' for row in timeline))

    def test_policy_and_planning_queries_return_v2_backed_data(self):
        result, _finding = self._seed_operational_context()
        architecture_fixture = ensure_roce_4plane_shuffle_architecture()

        policy_summary = _policy_summary(
            fabric_id=result.fabric.pk,
            plane_id=result.source_lanes[0].plane_id,
        )
        self.assertGreaterEqual(policy_summary['finding_count'], 1)
        self.assertGreaterEqual(policy_summary['contamination_domain_count'], 1)

        policy_dashboard = _policy_dashboard(fabric_id=result.fabric.pk)
        self.assertIn('summary', policy_dashboard)
        self.assertIn('policy_groups', policy_dashboard)

        domains = _contamination_domains(fabric_id=result.fabric.pk, limit=10)
        self.assertGreaterEqual(len(domains), 1)
        self.assertIn('domain_key', domains[0])

        deployment_summary = _deployment_workflow_summary(fabric_id=result.fabric.pk, limit=10)
        self.assertGreaterEqual(deployment_summary['total_runs'], 1)
        self.assertGreaterEqual(len(deployment_summary['recent_runs']), 1)

        preview = _stamp_template_preview(
            template_id=architecture_fixture.stamp_template.pk,
            fabric_name='GraphQL Preview Fabric',
            fabric_slug='graphql-preview-fabric',
        )
        self.assertEqual(preview['template_type'], 'v2_stamp_template')
        self.assertEqual(preview['template_id'], architecture_fixture.stamp_template.pk)
