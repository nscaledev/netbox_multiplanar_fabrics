from django.test import SimpleTestCase
from django.urls import reverse

from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


class V2URLContractTestCase(SimpleTestCase):
    def test_top_level_and_operational_urls_reverse(self):
        expected_urls = {
            'home': '/plugins/plant-graph/',
            'seed_v2_proof': '/plugins/plant-graph/seed-v2-proof/',
            'graph_overview': '/plugins/plant-graph/graph-overview/',
            'fabric_onboard': '/plugins/plant-graph/fabric/onboard/',
            'fabric_operations': '/plugins/plant-graph/fabrics/1/operations/',
            'fabric_assign_planes': '/plugins/plant-graph/fabrics/1/assign-planes/',
            'health': '/plugins/plant-graph/health/',
            'audit_dashboard': '/plugins/plant-graph/audit-dashboard/',
            'audit_triage': '/plugins/plant-graph/audit-triage/',
            'policy_review': '/plugins/plant-graph/policy-review/',
            'plane_audit': '/plugins/plant-graph/plane-audit/',
            'audit_finding_acknowledge': '/plugins/plant-graph/audit-findings/1/acknowledge/',
            'audit_finding_start_remediation': '/plugins/plant-graph/audit-findings/1/start-remediation/',
            'audit_finding_suppress': '/plugins/plant-graph/audit-findings/1/suppress/',
            'audit_finding_unsuppress': '/plugins/plant-graph/audit-findings/1/unsuppress/',
            'audit_finding_resolve': '/plugins/plant-graph/audit-findings/1/resolve/',
            'audit_finding_reopen': '/plugins/plant-graph/audit-findings/1/reopen/',
            'disjointness_exception_request': '/plugins/plant-graph/disjointness-exceptions/request/',
            'disjointness_exception_approve': '/plugins/plant-graph/disjointness-exceptions/1/approve/',
            'disjointness_exception_expire': '/plugins/plant-graph/disjointness-exceptions/1/expire/',
            'disjointness_exception_reactivate': '/plugins/plant-graph/disjointness-exceptions/1/reactivate/',
            'template_library': '/plugins/plant-graph/template-library/',
            'assembly_stamp_wizard': '/plugins/plant-graph/assembly-templates/1/stamp/',
            'assembly_graph_stamp_wizard': '/plugins/plant-graph/assembly-templates/1/stamp-graph/',
            'assembly_template_build': '/plugins/plant-graph/assembly-templates/1/build/',
            'breakout_stamp_wizard': '/plugins/plant-graph/device-breakout-templates/1/stamp/',
            'spatial_stamp_wizard': '/plugins/plant-graph/spatial-templates/1/stamp/',
            'spatial_template_compose': '/plugins/plant-graph/spatial-templates/1/compose/',
            'connection_template_builder': '/plugins/plant-graph/spatial-templates/1/connections/',
            'rack_population_stamp_wizard': '/plugins/plant-graph/rack-population-templates/1/stamp/',
            'deployment_plan_workflow': '/plugins/plant-graph/deployment-plans/1/workflow/',
            'deployment_plan_execute': '/plugins/plant-graph/deployment-plans/1/execute/',
            'deployment_plan_rollback': '/plugins/plant-graph/deployment-plans/1/rollback/',
            'architecture_list': '/plugins/plant-graph/architectures/',
            'fabricarchitecture_list': '/plugins/plant-graph/architectures/',
            'fabric_list': '/plugins/plant-graph/fabrics/',
            'stamptemplate_execute': '/plugins/plant-graph/stamp-templates/1/execute/',
            'path_resolver': '/plugins/plant-graph/path-resolver/',
            'path_query': '/plugins/plant-graph/path-query/',
            'interface_fanout_trace': '/plugins/plant-graph/interface-fanout-trace/',
            'lane_drilldown': '/plugins/plant-graph/lane-drilldown/',
            'lane_compare': '/plugins/plant-graph/lane-compare/',
            'blast_radius': '/plugins/plant-graph/blast-radius/',
            'lane_workspace': '/plugins/plant-graph/lane-workspace/',
            'policy_dashboard': '/plugins/plant-graph/policy-dashboard/',
            'coordinate_layout': '/plugins/plant-graph/coordinate-layout/',
            'operations_center': '/plugins/plant-graph/operations/',
            'graph-build-run_list': '/plugins/plant-graph/graph-build-runs/',
            'audit-run_list': '/plugins/plant-graph/audit-runs/',
            'suppressionrule_revoke': '/plugins/plant-graph/suppression-rules/1/revoke/',
        }

        for route_name, expected_url in expected_urls.items():
            with self.subTest(route_name=route_name):
                kwargs = {'pk': 1} if route_name in {
                    'stamptemplate_execute',
                    'suppressionrule_revoke',
                    'audit_finding_acknowledge',
                    'audit_finding_start_remediation',
                    'audit_finding_suppress',
                    'audit_finding_unsuppress',
                    'audit_finding_resolve',
                    'audit_finding_reopen',
                    'disjointness_exception_approve',
                    'disjointness_exception_expire',
                    'disjointness_exception_reactivate',
                    'assembly_stamp_wizard',
                    'assembly_graph_stamp_wizard',
                    'assembly_template_build',
                    'breakout_stamp_wizard',
                    'spatial_stamp_wizard',
                    'spatial_template_compose',
                    'connection_template_builder',
                    'rack_population_stamp_wizard',
                    'deployment_plan_workflow',
                    'deployment_plan_execute',
                    'deployment_plan_rollback',
                    'fabric_operations',
                    'fabric_assign_planes',
                } else None
                self.assertEqual(reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs=kwargs), expected_url)

    def test_standard_netbox_object_urls_reverse_for_every_v2_object(self):
        suffixes = {
            '': '/1/',
            '_edit': '/1/edit/',
            '_delete': '/1/delete/',
            '_changelog': '/1/changelog/',
            '_journal': '/1/journal/',
        }
        for spec in V2_OBJECT_SPECS:
            route_names = {spec.route_slug, spec.model._meta.model_name}
            for route_name in route_names:
                with self.subTest(route_name=route_name):
                    self.assertEqual(
                        reverse(f'plugins:netbox_plant_graph:{route_name}_list'),
                        f'/plugins/plant-graph/{spec.path_prefix}/',
                    )
                    self.assertEqual(
                        reverse(f'plugins:netbox_plant_graph:{route_name}_add'),
                        f'/plugins/plant-graph/{spec.path_prefix}/add/',
                    )
                    for suffix, expected_suffix in suffixes.items():
                        self.assertEqual(
                            reverse(f'plugins:netbox_plant_graph:{route_name}{suffix}', kwargs={'pk': 1}),
                            f'/plugins/plant-graph/{spec.path_prefix}{expected_suffix}',
                        )
