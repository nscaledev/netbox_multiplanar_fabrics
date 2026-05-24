import ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_VERSION,
    build_roce_4plane_h100_direct_attach_architecture_schema,
    build_roce_4plane_shuffle_architecture_schema,
    build_roce_8plane_gb300_shuffle_architecture_schema,
    ensure_roce_4plane_shuffle_architecture,
    validate_roce_4plane_h100_direct_attach_architecture_fixture,
    validate_roce_4plane_shuffle_architecture_fixture,
    validate_roce_8plane_gb300_shuffle_architecture_fixture,
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

    def test_expanded_builtin_architecture_definitions_validate(self):
        gb300_8plane_result = validate_roce_8plane_gb300_shuffle_architecture_fixture()
        h100_direct_attach_result = validate_roce_4plane_h100_direct_attach_architecture_fixture()

        self.assertTrue(gb300_8plane_result.is_valid, gb300_8plane_result.messages)
        self.assertTrue(h100_direct_attach_result.is_valid, h100_direct_attach_result.messages)

        gb300_8plane = build_roce_8plane_gb300_shuffle_architecture_schema()
        h100_direct_attach = build_roce_4plane_h100_direct_attach_architecture_schema()
        self.assertEqual(gb300_8plane.default_planes, 8)
        self.assertEqual(h100_direct_attach.fabric_class, 'roce_backend')
        self.assertEqual(h100_direct_attach.mpo_position_count, 8)

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

    def test_plane_range_default_outside_declared_range_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()

        result = validate_architecture_schema(
            replace(definition, min_planes=2, max_planes=8, default_planes=16)
        )

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='plane_range.default_out_of_range',
            path='default_planes',
            message_contains='must be within the declared range 2..8',
        )

    def test_active_port_channel_metadata_mismatch_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        roles = deepcopy(definition.roles)
        gpu_osfp = next(role for role in roles if role['slug'] == 'gpu_osfp')
        gpu_osfp['metadata']['channels_per_osfp'] = 8

        result = validate_architecture_schema(replace(definition, roles=tuple(roles)))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='roles.channels_per_osfp_mismatch',
            path='roles[2].metadata.channels_per_osfp',
            message_contains='must match channels_per_subinterface=4',
        )

    def test_direct_attach_provider_mismatch_fails_clearly(self):
        definition = build_roce_4plane_h100_direct_attach_architecture_schema()
        transfer_patterns = deepcopy(definition.transfer_patterns)
        direct_attach = next(pattern for pattern in transfer_patterns if pattern['slug'] == 'direct_attach')
        direct_attach['rule']['matrix'][0]['position_pairs'][0] = [1, 2]

        result = validate_architecture_schema(replace(definition, transfer_patterns=tuple(transfer_patterns)))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='transfer_patterns.matrix_pair_mismatch',
            path='transfer_patterns[direct_attach].rule.matrix[1].position_pairs',
            message_contains='provider output',
        )

    def test_custom_transfer_pattern_requires_validator_entrypoint(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        transfer_patterns = tuple(definition.transfer_patterns) + (
            {
                'slug': 'vendor_custom',
                'name': 'Vendor custom',
                'pattern_kind': 'custom',
                'rule': {},
                'metadata': {},
            },
        )

        result = validate_architecture_schema(replace(definition, transfer_patterns=transfer_patterns))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='custom.validator_entrypoint_required',
            path='transfer_patterns[vendor_custom].rule.validator_entrypoint',
        )

    def test_custom_transfer_pattern_noop_validator_passes(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        transfer_patterns = tuple(definition.transfer_patterns) + (
            {
                'slug': 'vendor_custom',
                'name': 'Vendor custom',
                'pattern_kind': 'custom',
                'rule': {
                    'validator_entrypoint': (
                        'netbox_plant_graph.services.architecture_schema.noop_custom_transfer_validator'
                    ),
                },
                'metadata': {},
            },
        )

        result = validate_architecture_schema(replace(definition, transfer_patterns=transfer_patterns))

        self.assertTrue(result.is_valid, result.messages)

    def test_custom_transfer_pattern_rejects_validator_outside_allowed_namespace(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        transfer_patterns = tuple(definition.transfer_patterns) + (
            {
                'slug': 'vendor_custom',
                'name': 'Vendor custom',
                'pattern_kind': 'custom',
                'rule': {'validator_entrypoint': 'math.sqrt'},
                'metadata': {},
            },
        )

        result = validate_architecture_schema(replace(definition, transfer_patterns=transfer_patterns))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='custom.validator_entrypoint_not_allowed',
            path='transfer_patterns[vendor_custom].rule.validator_entrypoint',
        )

    def test_architecture_validator_rejects_entrypoint_outside_allowed_namespace(self):
        definition = build_roce_4plane_shuffle_architecture_schema()

        result = validate_architecture_schema(replace(definition, custom_validator_entrypoints=('math.sqrt',)))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='architecture_validators.entrypoint_not_allowed',
            path='custom_validator_entrypoints[1]',
        )

    def test_mpo24_position_count_variant_validates_against_declared_count(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        roles = deepcopy(definition.roles)
        for role in roles:
            metadata = role.get('metadata', {})
            if metadata.get('connector_kind') == 'mpo-12':
                metadata['connector_kind'] = 'mpo-24'
                metadata['position_count'] = 24
        transfer_patterns = deepcopy(definition.transfer_patterns)
        for shuffle_pattern in (
            pattern for pattern in transfer_patterns if pattern.get('pattern_kind') == 'shuffle_2x2'
        ):
            for group in shuffle_pattern['rule']['groups']:
                group['rear_position_transform']['position_count'] = 24
        active_positions = set(definition.active_position_groups['A']) | set(definition.active_position_groups['B'])
        dark_positions = tuple(position for position in range(1, 25) if position not in active_positions)

        result = validate_architecture_schema(
            replace(
                definition,
                roles=tuple(roles),
                transfer_patterns=tuple(transfer_patterns),
                dark_positions=dark_positions,
                mpo_position_count=24,
                shuffle_pair_provider=None,
            )
        )

        self.assertTrue(result.is_valid, result.messages)

    def test_required_device_types_unknown_role_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()

        result = validate_architecture_schema(
            replace(definition, required_device_types={'missing_role': ('example-device-type',)})
        )

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='required_device_types.unknown_role',
            path='required_device_types.missing_role',
        )

    def test_cable_profile_fiber_count_mismatch_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        cable_profiles = deepcopy(definition.cable_profiles)
        cable_profiles[0]['fiber_count'] = 95

        result = validate_architecture_schema(replace(definition, cable_profiles=tuple(cable_profiles)))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='cable_profiles.fiber_count_mismatch',
            path='cable_profiles[trunk-96f-mpo8-sm-apc-unpinned-unpinned].fiber_count',
            message_contains='must equal mpo_connector_count * fibers_per_mpo',
        )

    def test_cable_profile_assignment_unknown_profile_fails_clearly(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        cable_profile_assignments = deepcopy(definition.cable_profile_assignments)
        cable_profile_assignments[0]['profile_slugs'] = ['missing-profile']

        result = validate_architecture_schema(
            replace(definition, cable_profile_assignments=tuple(cable_profile_assignments))
        )

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='cable_profile_assignments.unknown_profile',
            path='cable_profile_assignments[gb300-to-leaf-structured-trunk].profile_slugs[1]',
            message_contains='unknown cable profile',
        )

    def test_tier2_port_requires_matching_tier_device(self):
        definition = build_roce_4plane_shuffle_architecture_schema()
        roles = tuple(deepcopy(definition.roles)) + (
            {
                'slug': 'super_spine_uplink_osfp',
                'name': 'Super-spine uplink OSFP',
                'role_kind': 'active_tier_2_port',
                'description': 'Super-spine-facing uplink role without a matching super-spine device role.',
                'metadata': {
                    'fabric_tier': 'super_spine',
                    'connector_kind': 'osfp',
                    'channels': 4,
                    'channels_per_osfp': 4,
                    'channel_speed_gbps': 200,
                    'speed_gbps': 800,
                },
            },
        )

        result = validate_architecture_schema(replace(definition, roles=roles))

        self.assertFalse(result.is_valid)
        self.assertSchemaError(
            result,
            code='roles.fabric_tier_parent_missing',
            path='roles[16].metadata.fabric_tier',
            message_contains='without a matching tier device role',
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
