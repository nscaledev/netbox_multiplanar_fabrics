from unittest import TestCase

from django.urls import reverse

from netbox_plant_graph.urls import urlpatterns


class URLSmokeTestCase(TestCase):
    def test_urlpatterns_exist(self):
        self.assertTrue(urlpatterns)

    def test_operational_urls_reverse(self):
        self.assertEqual(reverse('plugins:netbox_plant_graph:graph_overview'), '/plugins/plant-graph/graph-overview/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:health'), '/plugins/plant-graph/health/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_dashboard'), '/plugins/plant-graph/audit-dashboard/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:policy_review'), '/plugins/plant-graph/policy-review/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:path_resolver'), '/plugins/plant-graph/path-resolver/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:lane_workspace'), '/plugins/plant-graph/lane-workspace/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:lane_compare'), '/plugins/plant-graph/lane-compare/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:graph-build-run_list'), '/plugins/plant-graph/graph-build-runs/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:unresolved-state-summary_list'), '/plugins/plant-graph/unresolved-state-summarys/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:unresolved-state-observation_list'), '/plugins/plant-graph/unresolved-state-observations/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_finding_acknowledge', args=[1]), '/plugins/plant-graph/audit-findings/1/acknowledge/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_finding_start_remediation', args=[1]), '/plugins/plant-graph/audit-findings/1/start-remediation/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[1]), '/plugins/plant-graph/audit-findings/1/suppress/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_finding_unsuppress', args=[1]), '/plugins/plant-graph/audit-findings/1/unsuppress/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_finding_resolve', args=[1]), '/plugins/plant-graph/audit-findings/1/resolve/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:audit_finding_reopen', args=[1]), '/plugins/plant-graph/audit-findings/1/reopen/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:disjointness_exception_approve', args=[1]), '/plugins/plant-graph/disjointness-exceptions/1/approve/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:disjointness_exception_expire', args=[1]), '/plugins/plant-graph/disjointness-exceptions/1/expire/')
        self.assertEqual(reverse('plugins:netbox_plant_graph:disjointness_exception_reactivate', args=[1]), '/plugins/plant-graph/disjointness-exceptions/1/reactivate/')
