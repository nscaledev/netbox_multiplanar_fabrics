import json
import subprocess
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import reverse

from netbox_plant_graph.api.serializers import (
    OperationalImpactReportResponseSerializer,
    PathQueryResponseSerializer,
    PathQueryStepSerializer,
)
from netbox_plant_graph.graphql.schema import GRAPHQL_CONTRACT_VERSION, NetBoxPlantGraphQuery
from netbox_plant_graph.management.commands.mpf_audit_integrity import Command as AuditIntegrityCommand
from netbox_plant_graph.management.commands.mpf_import_reconcile import Command as ImportReconcileCommand
from netbox_plant_graph.services.imports.reconciliation import ImportPlan
from netbox_plant_graph.services.topology_integrity import TopologyIntegrityReport
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DOC = REPO_ROOT / 'docs' / 'v2_external_contracts.md'
EXAMPLES_DIR = REPO_ROOT / 'docs' / 'examples'


def _parser_option_strings(command, command_name):
    parser = command.create_parser('manage.py', command_name)
    return {
        option_string
        for action in parser._actions
        for option_string in action.option_strings
    }


def _parser_destinations(command, command_name):
    parser = command.create_parser('manage.py', command_name)
    return {action.dest for action in parser._actions}


def _graphql_field_names():
    annotations = set(getattr(NetBoxPlantGraphQuery, '__annotations__', {}))
    if annotations:
        return annotations
    strawberry_definition = getattr(NetBoxPlantGraphQuery, '__strawberry_definition__', None)
    if strawberry_definition is None:
        return set()
    return {field.name for field in strawberry_definition.fields}


class V2ExternalContractSmokeTestCase(SimpleTestCase):
    maxDiff = None

    def test_json_examples_parse_and_stay_on_documented_stable_import_kinds(self):
        json_paths = sorted(EXAMPLES_DIR.glob('*.json'))
        self.assertGreaterEqual(len(json_paths), 3)

        parsed_examples = {}
        for path in json_paths:
            with self.subTest(path=path.name):
                parsed_examples[path.name] = json.loads(path.read_text())

        import_payload = parsed_examples['sample_import_reconcile.json']
        self.assertEqual(import_payload['payload_version'], 'v2.import_reconcile/1')
        self.assertEqual(
            {item['kind'] for item in import_payload['items']},
            {'cable_assembly', 'fiber_strand_cable'},
        )

    def test_shell_examples_are_syntax_checkable(self):
        shell_paths = sorted(EXAMPLES_DIR.glob('*.sh'))
        self.assertGreaterEqual(len(shell_paths), 1)

        for path in shell_paths:
            with self.subTest(path=path.name):
                result = subprocess.run(
                    ['bash', '-n', str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_documented_registry_resources_match_local_api_registry(self):
        contract_text = CONTRACT_DOC.read_text()

        documented_resources = {
            'architectures',
            'architecture-roles',
            'transfer-patterns',
            'allocation-rule-sets',
            'fabrics',
            'planes',
            'nodes',
            'endpoints',
            'connector-positions',
            'transport-channels',
            'transport-channel-position-maps',
            'fiber-segments',
            'cable-assemblies',
            'fiber-strands',
            'strand-terminations',
            'optical-lanes',
            'transfer-maps',
            'path-intents',
            'stamp-templates',
            'stamp-runs',
            'suppression-rules',
            'audit-events',
            'operation-runs',
        }
        self.assertEqual({spec.api_basename for spec in V2_OBJECT_SPECS}, documented_resources)

        for spec in V2_OBJECT_SPECS:
            with self.subTest(resource=spec.api_basename):
                self.assertIn(f'`{spec.api_basename}`', contract_text)
                self.assertEqual(
                    reverse(spec.api_list_url_name),
                    f'/api/plugins/plant-graph/{spec.api_basename}/',
                )
                self.assertEqual(
                    reverse(spec.api_detail_url_name, kwargs={'pk': 1}),
                    f'/api/plugins/plant-graph/{spec.api_basename}/1/',
                )

    def test_documented_stable_api_routes_reverse(self):
        contract_text = CONTRACT_DOC.read_text()
        route_contracts = (
            ('path-query', None, '/api/plugins/plant-graph/path-query/', '/path-query/'),
            (
                'impact-cable-assembly-cut',
                None,
                '/api/plugins/plant-graph/impact/cable-assembly-cut/',
                '/impact/cable-assembly-cut/',
            ),
            (
                'impact-mpo-connector-unplug',
                None,
                '/api/plugins/plant-graph/impact/mpo-connector-unplug/',
                '/impact/mpo-connector-unplug/',
            ),
            (
                'impact-osfp-transceiver-unseat',
                None,
                '/api/plugins/plant-graph/impact/osfp-transceiver-unseat/',
                '/impact/osfp-transceiver-unseat/',
            ),
            ('suppression-summary', None, '/api/plugins/plant-graph/suppression-summary/', '/suppression-summary/'),
            ('audit-timeline', None, '/api/plugins/plant-graph/audit-timeline/', '/audit-timeline/'),
            ('workflow-summary', None, '/api/plugins/plant-graph/workflow/summary/', '/workflow/summary/'),
            ('workflow-findings', None, '/api/plugins/plant-graph/workflow/findings/', '/workflow/findings/'),
            (
                'workflow-finding-detail',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/',
                '/workflow/findings/<id>/',
            ),
            ('workflow-runs', None, '/api/plugins/plant-graph/workflow/runs/', '/workflow/runs/'),
            ('operation-runs', None, '/api/plugins/plant-graph/operation-runs/', '/operation-runs/'),
            (
                'stamp-template-execute',
                {'pk': 1},
                '/api/plugins/plant-graph/stamp-templates/1/execute/',
                '/stamp-templates/<id>/execute/',
            ),
            (
                'stamp-run-rollback',
                {'pk': 1},
                '/api/plugins/plant-graph/stamp-runs/1/rollback/',
                '/stamp-runs/<id>/rollback/',
            ),
            ('stamp-preview', None, '/api/plugins/plant-graph/stamps/preview/', '/stamps/preview/'),
            ('stamps-preview', None, '/api/plugins/plant-graph/stamps/preview/', '/stamps/preview/'),
            (
                'workflow-finding-acknowledge',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/acknowledge/',
                '/workflow/findings/<id>/acknowledge/',
            ),
            (
                'workflow-finding-start-remediation',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/start-remediation/',
                '/workflow/findings/<id>/start-remediation/',
            ),
            (
                'workflow-finding-suppress',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/suppress/',
                '/workflow/findings/<id>/suppress/',
            ),
            (
                'workflow-finding-unsuppress',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/unsuppress/',
                '/workflow/findings/<id>/unsuppress/',
            ),
            (
                'workflow-finding-resolve',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/resolve/',
                '/workflow/findings/<id>/resolve/',
            ),
            (
                'workflow-finding-reopen',
                {'pk': 1},
                '/api/plugins/plant-graph/workflow/findings/1/reopen/',
                '/workflow/findings/<id>/reopen/',
            ),
            (
                'disjointness-exception-request',
                None,
                '/api/plugins/plant-graph/disjointness-exceptions/request/',
                '/disjointness-exceptions/request/',
            ),
            (
                'disjointness-exception-approve',
                {'pk': 1},
                '/api/plugins/plant-graph/disjointness-exceptions/1/approve/',
                '/disjointness-exceptions/<id>/approve/',
            ),
            (
                'disjointness-exception-expire',
                {'pk': 1},
                '/api/plugins/plant-graph/disjointness-exceptions/1/expire/',
                '/disjointness-exceptions/<id>/expire/',
            ),
            (
                'disjointness-exception-reactivate',
                {'pk': 1},
                '/api/plugins/plant-graph/disjointness-exceptions/1/reactivate/',
                '/disjointness-exceptions/<id>/reactivate/',
            ),
        )

        for route_name, kwargs, expected_url, doc_fragment in route_contracts:
            with self.subTest(route_name=route_name):
                self.assertIn(doc_fragment, contract_text)
                self.assertEqual(
                    reverse(f'plugins-api:netbox_plant_graph-api:{route_name}', kwargs=kwargs),
                    expected_url,
                )

    def test_documented_command_options_exist_in_local_commands(self):
        docs_and_examples = '\n'.join(
            [
                CONTRACT_DOC.read_text(),
                (EXAMPLES_DIR / 'README.md').read_text(),
                (EXAMPLES_DIR / 'curl_workflows.sh').read_text(),
            ]
        )

        audit_options = _parser_option_strings(AuditIntegrityCommand(), 'mpf_audit_integrity')
        for option in ('--fabric', '--format', '--fail-on'):
            with self.subTest(command='mpf_audit_integrity', option=option):
                self.assertIn(option, docs_and_examples)
                self.assertIn(option, audit_options)

        import_command = ImportReconcileCommand()
        import_options = _parser_option_strings(import_command, 'mpf_import_reconcile')
        import_destinations = _parser_destinations(import_command, 'mpf_import_reconcile')
        self.assertIn('json_file', import_destinations)
        for option in ('--apply', '--json', '--fail-on-conflict'):
            with self.subTest(command='mpf_import_reconcile', option=option):
                self.assertIn(option, docs_and_examples)
                self.assertIn(option, import_options)

    def test_graphql_contract_version_and_stable_query_fields_are_present(self):
        self.assertEqual(GRAPHQL_CONTRACT_VERSION, '2.0.0')
        self.assertEqual(
            {
                'graphql_contract_version',
                'v2_status',
                'fabrics',
                'optical_lanes',
                'optical_lane_path',
                'stamp_runs',
                'suppression_rules',
                'audit_events',
                'operation_runs',
            }
            - _graphql_field_names(),
            set(),
        )

    def test_stable_envelope_keys_are_present_in_local_shapes_and_docs(self):
        contract_text = CONTRACT_DOC.read_text()

        path_keys = {'path_found', 'source_lane_id', 'destination_lane_id', 'error', 'steps'}
        self.assertEqual(path_keys - set(PathQueryResponseSerializer().fields), set())
        self.assertEqual(
            {'step_type', 'object_type', 'object_id', 'label', 'metadata'}
            - set(PathQueryStepSerializer().fields),
            set(),
        )
        for key in path_keys:
            self.assertIn(f'`{key}`', contract_text)

        impact_keys = {
            'scenario',
            'scope',
            'summary',
            'simulated_components',
            'impacted_paths',
            'impacted_lanes',
            'impacted_channels',
            'impacted_endpoints',
            'impacted_devices',
            'hierarchy',
        }
        self.assertEqual(impact_keys - set(OperationalImpactReportResponseSerializer().fields), set())
        for key in impact_keys:
            self.assertIn(f'`{key}`', contract_text)

        import_payload = ImportPlan(diffs=()).to_dict()
        for key in ('applied', 'summary', 'diffs'):
            self.assertIn(key, import_payload)
            self.assertIn(f'`{key}`', contract_text)

        audit_payload = TopologyIntegrityReport(scope={}, checked={}, findings=()).as_dict()
        for key in ('scope', 'ok', 'summary', 'checked', 'findings'):
            self.assertIn(key, audit_payload)
            self.assertIn(f'`{key}`', contract_text)
