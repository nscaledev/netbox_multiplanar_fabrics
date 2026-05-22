import ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_VERSION,
    build_roce_4plane_shuffle_architecture_schema,
    ensure_roce_4plane_shuffle_architecture,
    validate_roce_4plane_shuffle_architecture_fixture,
)
from netbox_plant_graph.services.architecture_schema import (
    ARCHITECTURE_COMPATIBILITY_COMPATIBLE,
    ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE,
    compare_persisted_architecture_compatibility,
    validate_architecture_schema,
    validate_persisted_architecture_schema,
)


class ArchitectureSchemaAssertionsMixin:
    def assertSchemaError(self, result, *, code, path=None, message_contains=None):
        matching_errors = result.errors_for_code(code)
        self.assertTrue(matching_errors, result.messages)
        if path is not None:
            self.assertTrue(
                any(error.path == path for error in matching_errors),
                [str(error) for error in matching_errors],
            )
        if message_contains is not None:
            self.assertTrue(
                any(message_contains in error.message for error in matching_errors),
                [str(error) for error in matching_errors],
            )


class V2ArchitectureSchemaTestCase(ArchitectureSchemaAssertionsMixin, SimpleTestCase):
    def test_documented_error_catalog_covers_emitted_codes(self):
        service_path = Path(__file__).parents[1] / 'services' / 'architecture_schema.py'
        docs_path = Path(__file__).parents[2] / 'docs' / 'v2_architecture_schema.md'
        service_tree = ast.parse(service_path.read_text())
        documented_contract = docs_path.read_text()

        emitted_codes = set()

        class ErrorCodeVisitor(ast.NodeVisitor):
            def visit_Call(self, node):
                if isinstance(node.func, ast.Attribute) and node.func.attr == 'add':
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        emitted_codes.add(node.args[0].value)
                if isinstance(node.func, ast.Name) and node.func.id == 'ArchitectureCompatibilityIssue':
                    for keyword in node.keywords:
                        if (
                            keyword.arg == 'code'
                            and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, str)
                        ):
                            emitted_codes.add(keyword.value.value)
                if isinstance(node.func, ast.Name) and node.func.id == '_append_positive_int_candidate':
                    if (
                        len(node.args) >= 4
                        and isinstance(node.args[3], ast.Constant)
                        and isinstance(node.args[3].value, str)
                    ):
                        emitted_codes.add(node.args[3].value)
                if isinstance(node.func, ast.Name) and node.func.id == '_unique_positive_int_candidate':
                    for keyword in node.keywords:
                        if (
                            keyword.arg in {'missing_code', 'conflict_code'}
                            and isinstance(keyword.value, ast.Constant)
                            and isinstance(keyword.value.value, str)
                        ):
                            emitted_codes.add(keyword.value.value)
                self.generic_visit(node)

        ErrorCodeVisitor().visit(service_tree)

        missing = sorted(code for code in emitted_codes if f'`{code}`' not in documented_contract)
        self.assertEqual(missing, [])
        self.assertIn('`schema.<schema-code>`', documented_contract)
        self.assertIn('`persisted_schema.<persisted-code>`', documented_contract)

    def test_roce_fixture_validates_against_schema(self):
        result = validate_roce_4plane_shuffle_architecture_fixture()

        self.assertTrue(result.is_valid, result.messages)
        self.assertEqual(result.errors, ())

    def test_duplicate_channel_map_lane_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        matrix = deepcopy(definition.channel_map_matrix)
        matrix[1]['positions'][0] = 1

        result = validate_architecture_schema(replace(definition, channel_map_matrix=tuple(matrix)))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='channel_map.duplicate_lane',
            path='channel_map_matrix[2].positions[1]',
            message_contains='duplicates MPO 1 position 1',
        )

    def test_active_and_dark_position_overlap_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        active_position_groups = deepcopy(definition.active_position_groups)
        active_position_groups['A'] = (1, 5, 2, 11)

        result = validate_architecture_schema(
            replace(definition, active_position_groups=active_position_groups)
        )

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='mpo_positions.active_dark_overlap',
            path='dark_positions[1]',
            message_contains='active in group A and dark',
        )

    def test_bad_shuffle_transfer_matrix_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        transfer_patterns = deepcopy(definition.transfer_patterns)
        shuffle_pattern = next(pattern for pattern in transfer_patterns if pattern['slug'] == 'shuffle_2x2')
        shuffle_pattern['rule']['groups'][0]['matrix'][1]['src_group'] = 'A'

        result = validate_architecture_schema(replace(definition, transfer_patterns=tuple(transfer_patterns)))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='shuffle_2x2.matrix_transform',
            path='transfer_patterns[shuffle_2x2].rule.groups[1].matrix[2]',
            message_contains='must map source group B to destination group A',
        )

    def test_bad_shuffle_transform_helper_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()

        def broken_shuffle_pairs(*, front_index, rear_index):
            return ()

        result = validate_architecture_schema(replace(definition, shuffle_pair_provider=broken_shuffle_pairs))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='shuffle_2x2.helper_mismatch',
            path='transfer_patterns[shuffle_2x2].rule.groups[1].matrix.shuffle_mpo_groups[1].front_1.rear_1',
            message_contains='expected key-down-roll pairs',
        )


class V2PersistedArchitectureSchemaTestCase(ArchitectureSchemaAssertionsMixin, TestCase):
    def assertCompatibilityIssue(self, result, *, code, path=None, severity=None):
        matching_issues = result.issues_for_code(code)
        self.assertTrue(matching_issues, result.issues)
        if path is not None:
            self.assertTrue(
                any(issue.path == path for issue in matching_issues),
                matching_issues,
            )
        if severity is not None:
            self.assertTrue(
                any(issue.severity == severity for issue in matching_issues),
                matching_issues,
            )

    def test_persisted_roce_fixture_validates_against_schema(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        result = validate_persisted_architecture_schema(fixture.architecture)

        self.assertTrue(result.is_valid, result.messages)
        self.assertEqual(result.errors, ())

    def test_missing_persisted_channel_map_returns_structured_error(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        fixture.allocation_rule_sets['channel_subinterface_mapping'].delete()

        result = validate_persisted_architecture_schema(fixture.architecture)

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='persisted_architecture.channel_map_rule_missing',
            path='allocation_rule_sets.channel_subinterface_mapping',
            message_contains='missing the channel_subinterface_mapping allocation rule set',
        )

    def test_persisted_architecture_compatibility_passes_for_builtin_fixture(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        result = compare_persisted_architecture_compatibility(
            fixture.architecture,
            build_roce_4plane_shuffle_architecture_schema(),
        )

        self.assertEqual(result.status, ARCHITECTURE_COMPATIBILITY_COMPATIBLE)
        self.assertTrue(result.is_compatible)
        self.assertEqual(result.issues, ())

    def test_persisted_architecture_compatibility_flags_channel_map_drift(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        rule_set = fixture.allocation_rule_sets['channel_subinterface_mapping']
        rule = deepcopy(rule_set.rule)
        rule['channel_map_matrix'][0]['positions'] = [1, 12, 2, 10]
        rule_set.rule = rule
        rule_set.save()

        result = compare_persisted_architecture_compatibility(
            fixture.architecture,
            build_roce_4plane_shuffle_architecture_schema(),
        )

        self.assertEqual(result.status, ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE)
        self.assertFalse(result.is_compatible)
        self.assertCompatibilityIssue(
            result,
            code='channel_map_matrix_mismatch',
            path='architecture.channel_map_matrix',
            severity='error',
        )

    def test_persisted_architecture_compatibility_flags_version_mismatch(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        fixture.architecture.version = f'{ARCHITECTURE_VERSION}-future'

        result = compare_persisted_architecture_compatibility(
            fixture.architecture,
            build_roce_4plane_shuffle_architecture_schema(),
        )

        self.assertEqual(result.status, ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE)
        self.assertCompatibilityIssue(
            result,
            code='architecture_version_mismatch',
            path='architecture.version',
            severity='error',
        )
