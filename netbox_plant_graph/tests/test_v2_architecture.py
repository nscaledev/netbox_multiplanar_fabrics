from django.test import TestCase

from netbox_plant_graph.models import AllocationRuleSet, ArchitectureRole, FabricArchitecture, StampTemplate, TransferPattern
from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_SLUG,
    ARCHITECTURE_VERSION,
    STAMP_TEMPLATE_SLUG,
    ensure_roce_4plane_shuffle_architecture,
)


class V2ArchitectureFixtureTestCase(TestCase):
    def test_roce_4plane_fixture_is_executable_architecture_metadata(self):
        result = ensure_roce_4plane_shuffle_architecture()

        self.assertEqual(result.architecture.slug, ARCHITECTURE_SLUG)
        self.assertEqual(result.architecture.version, ARCHITECTURE_VERSION)
        self.assertEqual(result.architecture.status, 'active')
        self.assertEqual(result.architecture.plane_count, 4)
        self.assertEqual(
            result.architecture.metadata['semantics']['optical_lane_scope'],
            'transceiver_local',
        )
        self.assertEqual(
            result.architecture.metadata['semantics']['fiber_path_scope'],
            'connector_position_graph',
        )

        self.assertEqual(set(result.roles), {
            'gpu_tray',
            'gpu_osfp',
            'gpu_mpo',
            'leaf_switch',
            'leaf_osfp',
            'leaf_mpo',
            'shuffle_cassette',
            'shuffle_front_mpo',
            'shuffle_rear_mpo',
        })
        self.assertEqual(set(result.transfer_patterns), {
            'identity',
            'shuffle_2x2',
            'second_third_mpo_stagger',
        })
        self.assertEqual(set(result.allocation_rule_sets), {
            'gb300_osfp_mpo_order',
            'shuffle_cassette_fill_order',
            'leaf_plane_striping',
        })
        self.assertEqual(result.stamp_template.slug, STAMP_TEMPLATE_SLUG)
        self.assertEqual(len(result.stamp_template.template['proof_paths']), 4)

    def test_roce_4plane_fixture_is_idempotent(self):
        ensure_roce_4plane_shuffle_architecture()
        ensure_roce_4plane_shuffle_architecture()

        architecture = FabricArchitecture.objects.get(slug=ARCHITECTURE_SLUG, version=ARCHITECTURE_VERSION)
        self.assertEqual(ArchitectureRole.objects.filter(architecture=architecture).count(), 9)
        self.assertEqual(TransferPattern.objects.filter(architecture=architecture).count(), 3)
        self.assertEqual(AllocationRuleSet.objects.filter(architecture=architecture).count(), 3)
        self.assertEqual(StampTemplate.objects.filter(slug=STAMP_TEMPLATE_SLUG).count(), 1)
