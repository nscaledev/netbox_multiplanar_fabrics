from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from dcim.models import DeviceType, Manufacturer

from netbox_plant_graph.models import StampTemplate, TransferMap
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.blueprint_registry import (
    BLUEPRINT_LIFECYCLE_DEPRECATED,
    BLUEPRINT_LIFECYCLE_RETIRED,
    BlueprintRegistry,
    build_builtin_blueprint_entries,
    check_blueprint_device_type_compatibility,
    check_blueprint_lifecycle,
    get_default_blueprint_registry,
    validate_blueprint_parameters,
    validate_parameter_schema,
)
from netbox_plant_graph.services.architecture_schema import validate_architecture_schema
from netbox_plant_graph.services.stamp_template_validation import resolve_stamp_template_spec, validate_stamp_template_spec
from netbox_plant_graph.services.stamping_v25 import apply_stamp_template_v25, preview_stamp_template_v25


class V2BlueprintRegistryTestCase(SimpleTestCase):
    def test_builtin_registry_contains_valid_blueprints(self):
        registry = get_default_blueprint_registry()
        entries = registry.list_blueprints()

        self.assertEqual(
            {entry.slug for entry in entries},
            {
                'roce-4-plane-gb300-2x2-shuffle',
                'roce-4-plane-h100-direct-attach',
                'roce-8-plane-gb300-2x2-shuffle',
            },
        )
        for entry in entries:
            with self.subTest(slug=entry.slug, version=entry.version):
                result = validate_architecture_schema(entry.definition)
                self.assertTrue(result.is_valid, result.messages)
                self.assertEqual(validate_parameter_schema(entry.parameter_schema), ())

    def test_h100_blueprint_uses_real_direct_attach_schema(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-h100-direct-attach', 'v2')
        transfer_patterns = {pattern['slug']: pattern for pattern in entry.definition.transfer_patterns}

        self.assertNotIn('integration_seam', entry.metadata)
        self.assertEqual(entry.metadata['transfer_geometry'], 'direct_attach')
        self.assertIn('direct_attach', transfer_patterns)
        self.assertEqual(transfer_patterns['direct_attach']['pattern_kind'], 'direct_attach')
        self.assertIn('direct_attach', entry.definition.transfer_pair_providers)
        self.assertEqual(entry.definition.mpo_position_count, 8)
        self.assertEqual(set(entry.required_device_types), {'h100_node', 'leaf_switch'})
        self.assertIn('roce-4-plane-h100-direct-attach-mini-proof', entry.stamp_templates)

    def test_builtin_bundled_stamp_templates_validate(self):
        entries = get_default_blueprint_registry().list_blueprints()

        self.assertTrue(all(entry.stamp_templates for entry in entries))
        for entry in entries:
            with self.subTest(slug=entry.slug):
                self.assertNotIn('integration_seam', entry.metadata)
                for template_slug, template_payload in entry.stamp_templates.items():
                    template_spec = template_payload['template']
                    self.assertEqual(template_spec['architecture_slug'], entry.slug)
                    self.assertEqual(template_spec['architecture_version'], entry.version)
                    validate_stamp_template_spec(template_spec)
                    self.assertEqual(template_slug, template_payload['slug'])

    def test_gb300_8plane_template_declares_parameterized_topology(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-8-plane-gb300-2x2-shuffle', 'v2')
        template_spec = entry.stamp_templates['roce-8-plane-gb300-mini-proof']['template']

        resolved = resolve_stamp_template_spec(template_spec)

        self.assertNotIn('integration_seam', entry.metadata)
        self.assertEqual(resolved['planes'], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(resolved['leaf_ports']['count'], 8)
        self.assertEqual(resolved['gpu_tray']['count'], 2)
        self.assertEqual(resolved['_resolved']['topology_parameters']['plane_count'], 8)
        self.assertEqual(resolved['_resolved']['topology_parameters']['leaf_count_per_plane'], 1)

    def test_parameter_validation_reports_blueprint_parameter_codes(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')

        issues = validate_blueprint_parameters(
            entry.parameter_schema,
            {
                'topology_parameters': {'plane_count': 99},
                'unexpected': True,
            },
        )

        self.assertIn('blueprint_parameter.maximum', {issue.code for issue in issues})
        self.assertIn('blueprint_parameter.additional_property', {issue.code for issue in issues})
        self.assertTrue(all(issue.code.startswith('blueprint_parameter.') for issue in issues))

    def test_lifecycle_transitions_emit_structured_issues(self):
        registry = BlueprintRegistry(build_builtin_blueprint_entries())

        deprecated = registry.deprecate('roce-4-plane-gb300-2x2-shuffle', 'v2', 'v3')
        retired = registry.retire('roce-4-plane-gb300-2x2-shuffle', 'v2')

        self.assertEqual(deprecated.lifecycle, BLUEPRINT_LIFECYCLE_DEPRECATED)
        self.assertEqual(retired.lifecycle, BLUEPRINT_LIFECYCLE_RETIRED)
        self.assertEqual(check_blueprint_lifecycle(deprecated)[0].severity, 'warning')
        self.assertEqual(check_blueprint_lifecycle(retired)[0].severity, 'error')


class V2BlueprintCompatibilityTestCase(TestCase):
    def test_required_device_type_compatibility_reports_missing_types(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')

        result = check_blueprint_device_type_compatibility(entry)

        self.assertFalse(result.is_compatible)
        self.assertIn('architecture_gate.missing_device_type', {issue.code for issue in result.issues})

    def test_management_command_checks_registered_blueprints(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('mpf_check_blueprint_compatibility', stdout=output)
        self.assertIn('MPF blueprint compatibility: checked=3', output.getvalue())

        self._create_required_device_types()
        output = StringIO()
        call_command('mpf_check_blueprint_compatibility', stdout=output)

        self.assertIn('MPF blueprint compatibility: checked=3 errors=0 warnings=0', output.getvalue())

    def _create_required_device_types(self):
        manufacturer = Manufacturer.objects.create(name='Blueprint Test Manufacturer', slug='blueprint-test-mfg')
        required_slugs = {
            slug
            for entry in get_default_blueprint_registry().list_blueprints()
            for slugs in entry.required_device_types.values()
            for slug in slugs
        }
        for slug in sorted(required_slugs):
            DeviceType.objects.create(
                manufacturer=manufacturer,
                model=slug.replace('-', ' ').title(),
                slug=slug,
            )


class V2BlueprintStampHarnessTestCase(TestCase):
    def test_bundled_stamp_template_previews_without_error_issues(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        entries = [
            entry
            for entry in get_default_blueprint_registry().list_blueprints()
            if entry.stamp_templates
        ]

        self.assertEqual(
            [entry.slug for entry in entries],
            [
                'roce-4-plane-gb300-2x2-shuffle',
                'roce-4-plane-h100-direct-attach',
                'roce-8-plane-gb300-2x2-shuffle',
            ],
        )
        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Blueprint preview fabric',
            fabric_slug='blueprint-preview-fabric',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues])

    def test_non_gb300_bundled_templates_reach_current_v25_preview_gate(self):
        registry = get_default_blueprint_registry()
        cases = (
            ('roce-4-plane-h100-direct-attach', 'roce-4-plane-h100-direct-attach-mini-proof'),
            ('roce-8-plane-gb300-2x2-shuffle', 'roce-8-plane-gb300-mini-proof'),
        )

        for blueprint_slug, template_slug in cases:
            entry = registry.get_blueprint(blueprint_slug, 'v2')
            template_payload = entry.stamp_templates[template_slug]
            template = SimpleNamespace(
                pk=None,
                slug=template_payload['slug'],
                template=template_payload['template'],
                architecture_id=None,
            )

            preview = preview_stamp_template_v25(
                template=template,
                fabric_name=f'{template_slug} preview',
                fabric_slug=f'{template_slug}-preview',
            )

            issue_codes = {issue.code for issue in preview.issues}
            self.assertNotIn('template_spec_invalid', issue_codes)
            self.assertNotIn('executor_unknown', issue_codes)
            self.assertNotIn('unsupported_architecture', issue_codes)
            self.assertNotIn('unsupported_architecture_version', issue_codes)
            self.assertEqual(preview.architecture_gate.blueprint_source, 'registry')
            self.assertEqual(preview.architecture_gate.blueprint_slug, blueprint_slug)

    def test_bundled_stamp_template_stamps_resolvable_lane_path(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        result = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Blueprint stamp fabric',
            fabric_slug='blueprint-stamp-fabric',
        )

        self.assertGreaterEqual(len(result.execution.resolved_paths), 1)
        self.assertTrue(all(path.path_found for path in result.execution.resolved_paths))

    def test_h100_direct_attach_template_applies_without_shuffle_transfer_maps(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-h100-direct-attach', 'v2')
        template_payload = entry.stamp_templates['roce-4-plane-h100-direct-attach-mini-proof']
        template = StampTemplate.objects.create(
            slug=template_payload['slug'],
            name=template_payload['name'],
            template=template_payload['template'],
            metadata=template_payload['metadata'],
        )

        result = apply_stamp_template_v25(
            template=template,
            fabric_name='H100 direct attach apply',
            fabric_slug='h100-direct-attach-apply',
        )

        self.assertEqual(result.execution.fabric.architecture.slug, entry.slug)
        self.assertEqual(len(result.execution.resolved_paths), 4)
        self.assertTrue(all(path.path_found for path in result.execution.resolved_paths))
        self.assertFalse(TransferMap.objects.filter(fabric=result.execution.fabric).exists())

    def test_gb300_8plane_template_applies_with_eight_resolved_paths(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-8-plane-gb300-2x2-shuffle', 'v2')
        template_payload = entry.stamp_templates['roce-8-plane-gb300-mini-proof']
        template = StampTemplate.objects.create(
            slug=template_payload['slug'],
            name=template_payload['name'],
            template=template_payload['template'],
            metadata=template_payload['metadata'],
        )

        result = apply_stamp_template_v25(
            template=template,
            fabric_name='GB300 8-plane apply',
            fabric_slug='gb300-8plane-apply',
        )

        self.assertEqual(result.execution.fabric.architecture.slug, entry.slug)
        self.assertEqual(len(result.execution.resolved_paths), 8)
        self.assertTrue(all(path.path_found for path in result.execution.resolved_paths))
