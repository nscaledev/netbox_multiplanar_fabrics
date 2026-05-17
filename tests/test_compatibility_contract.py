import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / 'netbox_plant_graph' / 'compatibility.py'
MODULE_SPEC = spec_from_file_location('netbox_plant_graph_compatibility_contract', MODULE_PATH)
compatibility = module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
sys.modules[MODULE_SPEC.name] = compatibility
MODULE_SPEC.loader.exec_module(compatibility)

FLOORPLAN_MODULE_PATH = Path(__file__).resolve().parents[1] / 'netbox_plant_graph' / 'floorplan_compat.py'
FLOORPLAN_MODULE_SPEC = spec_from_file_location('netbox_plant_graph_floorplan_compat_contract', FLOORPLAN_MODULE_PATH)
floorplan_compat = module_from_spec(FLOORPLAN_MODULE_SPEC)
assert FLOORPLAN_MODULE_SPEC.loader is not None
sys.modules[FLOORPLAN_MODULE_SPEC.name] = floorplan_compat
FLOORPLAN_MODULE_SPEC.loader.exec_module(floorplan_compat)


class CompatibilityClassificationTestCase(TestCase):
    # --- 4.5.x (existing GA line) ---

    def test_ga_runtime_is_reported_for_release_gated_combo(self):
        assessment = compatibility.classify_runtime(netbox_version='4.5.7', python_version=(3, 12))
        self.assertEqual(assessment.status, 'ga')

    def test_supported_python_on_untested_patch_is_beta(self):
        assessment = compatibility.classify_runtime(netbox_version='4.5.3', python_version=(3, 12))
        self.assertEqual(assessment.status, 'beta')

    def test_newer_python_inside_documented_range_is_best_effort(self):
        assessment = compatibility.classify_runtime(netbox_version='4.5.7', python_version=(3, 13))
        self.assertEqual(assessment.status, 'best_effort')

    # --- 4.2.x (new GA line) ---

    def test_ga_runtime_is_reported_for_netbox_4_2_3(self):
        assessment = compatibility.classify_runtime(netbox_version='4.2.3', python_version=(3, 12))
        self.assertEqual(assessment.status, 'ga')

    def test_untested_patch_on_4_2_line_with_supported_python_is_beta(self):
        assessment = compatibility.classify_runtime(netbox_version='4.2.5', python_version=(3, 12))
        self.assertEqual(assessment.status, 'beta')

    def test_beta_message_includes_correct_minor_line_for_4_2(self):
        assessment = compatibility.classify_runtime(netbox_version='4.2.5', python_version=(3, 12))
        self.assertIn('4.2.x', assessment.message)
        self.assertNotIn('4.5.x', assessment.message)

    def test_best_effort_runtime_on_4_2_line_with_newer_python(self):
        assessment = compatibility.classify_runtime(netbox_version='4.2.3', python_version=(3, 13))
        self.assertEqual(assessment.status, 'best_effort')

    # --- Versions outside supported lines ---

    def test_netbox_version_in_gap_between_lines_is_unsupported(self):
        """4.3.x and 4.4.x sit between the two supported lines and are unsupported."""
        for version in ('4.3.0', '4.3.5', '4.4.0', '4.4.9'):
            with self.subTest(netbox_version=version):
                assessment = compatibility.classify_runtime(netbox_version=version, python_version=(3, 12))
                self.assertEqual(assessment.status, 'unsupported')

    def test_unsupported_message_lists_both_supported_lines(self):
        assessment = compatibility.classify_runtime(netbox_version='4.3.0', python_version=(3, 12))
        self.assertIn('4.2.x', assessment.message)
        self.assertIn('4.5.x', assessment.message)

    def test_older_netbox_line_is_unsupported(self):
        assessment = compatibility.classify_runtime(netbox_version='4.1.0', python_version=(3, 12))
        self.assertEqual(assessment.status, 'unsupported')

    def test_future_major_netbox_version_is_unsupported(self):
        assessment = compatibility.classify_runtime(netbox_version='5.0.0', python_version=(3, 12))
        self.assertEqual(assessment.status, 'unsupported')

    def test_unsupported_python_version_is_unsupported(self):
        assessment = compatibility.classify_runtime(netbox_version='4.5.7', python_version=(3, 9))
        self.assertEqual(assessment.status, 'unsupported')


class FloorplanCompatibilityTestCase(TestCase):
    def test_assert_floorplan_available_raises_when_package_missing(self):
        with patch.object(floorplan_compat, 'find_spec', return_value=None):
            with self.assertRaises(floorplan_compat.FloorplanAvailabilityError) as ctx:
                floorplan_compat.assert_floorplan_available()

        self.assertIn('netbox-floorplan-plugin is required', str(ctx.exception))

    def test_assert_floorplan_available_raises_when_plugin_not_enabled(self):
        with patch.object(floorplan_compat, 'find_spec', return_value=object()), \
             patch.object(floorplan_compat, '_get_enabled_plugins', return_value=('netbox_plant_graph',)), \
             patch.object(floorplan_compat, 'get_floorplan_version', return_value='0.9.1'):
            with self.assertRaises(floorplan_compat.FloorplanAvailabilityError) as ctx:
                floorplan_compat.assert_floorplan_available()

        self.assertIn("installed but 'netbox_floorplan' is not enabled", str(ctx.exception))

    def test_assert_floorplan_available_returns_availability_when_ready(self):
        with patch.object(floorplan_compat, 'find_spec', return_value=object()), \
             patch.object(floorplan_compat, '_get_enabled_plugins', return_value=('netbox_floorplan', 'netbox_plant_graph')), \
             patch.object(floorplan_compat, 'get_floorplan_version', return_value='0.9.1'):
            availability = floorplan_compat.assert_floorplan_available()

        self.assertTrue(availability.ready)
        self.assertEqual(availability.version, '0.9.1')
