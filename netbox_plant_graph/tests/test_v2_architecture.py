from django.test import TestCase

from netbox_plant_graph.models import AllocationRuleSet, ArchitectureRole, FabricArchitecture, StampTemplate, TransferPattern
from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_SLUG,
    ARCHITECTURE_VERSION,
    STAMP_TEMPLATE_SLUG,
    ensure_roce_4plane_shuffle_architecture,
    shuffle_2x2_transfer_position_pairs,
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
        shuffle_rule = result.transfer_patterns['shuffle_2x2'].rule
        self.assertEqual(
            shuffle_rule['groups'][0]['rear_position_transform'],
            {
                'type': 'key_down_roll',
                'position_count': 12,
                'formula': 'dst_position = position_count + 1 - base_dst_position',
            },
        )
        self.assertEqual(
            shuffle_rule['groups'][0]['active_position_groups'],
            {'A': [1, 12, 2, 11], 'B': [3, 10, 4, 9]},
        )
        self.assertEqual(
            shuffle_rule['groups'][0]['matrix'],
            [
                {'front_mpo': 1, 'rear_mpo': 1, 'src_group': 'A', 'dst_group': 'A'},
                {'front_mpo': 1, 'rear_mpo': 2, 'src_group': 'B', 'dst_group': 'A'},
                {'front_mpo': 2, 'rear_mpo': 1, 'src_group': 'A', 'dst_group': 'B'},
                {'front_mpo': 2, 'rear_mpo': 2, 'src_group': 'B', 'dst_group': 'B'},
            ],
        )
        self.assertEqual(
            shuffle_2x2_transfer_position_pairs(front_index=1, rear_index=1),
            ((1, 12), (12, 1), (2, 11), (11, 2)),
        )
        self.assertEqual(
            shuffle_2x2_transfer_position_pairs(front_index=1, rear_index=2),
            ((3, 12), (10, 1), (4, 11), (9, 2)),
        )
        self.assertEqual(set(result.allocation_rule_sets), {
            'gb300_osfp_mpo_order',
            'shuffle_cassette_fill_order',
            'leaf_plane_striping',
            'channel_subinterface_mapping',
        })
        self.assertEqual(result.stamp_template.slug, STAMP_TEMPLATE_SLUG)
        self.assertEqual(result.stamp_template.template['executor']['mode'], 'hybrid')
        self.assertEqual(result.stamp_template.template['executor']['primitive'], 'roce_4plane_mini_proof')
        self.assertEqual(
            result.stamp_template.template['channel_subinterfaces']['channel_map_matrix'],
            [
                {'subinterface_index': 1, 'mpo_index': 1, 'positions': [1, 12, 2, 11]},
                {'subinterface_index': 2, 'mpo_index': 1, 'positions': [3, 10, 4, 9]},
                {'subinterface_index': 3, 'mpo_index': 2, 'positions': [1, 12, 2, 11]},
                {'subinterface_index': 4, 'mpo_index': 2, 'positions': [3, 10, 4, 9]},
            ],
        )
        self.assertEqual(
            {binding['field_name'] for binding in result.stamp_template.template['source_bindings']},
            {'gpu_tray_device', 'gpu_osfp_1_interface'},
        )
        self.assertEqual(len(result.stamp_template.template['proof_paths']), 4)

    def test_roce_4plane_fixture_is_idempotent(self):
        ensure_roce_4plane_shuffle_architecture()
        ensure_roce_4plane_shuffle_architecture()

        architecture = FabricArchitecture.objects.get(slug=ARCHITECTURE_SLUG, version=ARCHITECTURE_VERSION)
        self.assertEqual(ArchitectureRole.objects.filter(architecture=architecture).count(), 9)
        self.assertEqual(TransferPattern.objects.filter(architecture=architecture).count(), 3)
        self.assertEqual(AllocationRuleSet.objects.filter(architecture=architecture).count(), 4)
        self.assertEqual(StampTemplate.objects.filter(slug=STAMP_TEMPLATE_SLUG).count(), 1)
