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
            'spine_switch',
            'spine_osfp',
            'spine_mpo',
            'shuffle_cassette',
            'shuffle_front_mpo',
            'shuffle_rear_mpo',
            'spine_shuffle_cassette',
            'spine_shuffle_rear_mpo',
            'spine_shuffle_front_mpo',
        })
        self.assertEqual(set(result.transfer_patterns), {
            'identity',
            'shuffle_2x2',
            'leaf_spine_shuffle_2x2',
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
            'backend_leaf_spine_shuffle',
        })
        leaf_spine_rule = result.allocation_rule_sets['backend_leaf_spine_shuffle'].rule
        self.assertEqual(leaf_spine_rule['source_device_role'], 'leaf_switch')
        self.assertEqual(leaf_spine_rule['destination_device_role'], 'spine_switch')
        self.assertEqual(leaf_spine_rule['transfer_pattern'], 'leaf_spine_shuffle_2x2')
        self.assertEqual(leaf_spine_rule['leaf_spine_osfp_cages_per_leaf'], 32)
        self.assertEqual(leaf_spine_rule['spine_switches_per_plane'], 126)
        self.assertEqual(leaf_spine_rule['available_spine_interfaces_per_leaf'], 128)
        self.assertEqual(
            leaf_spine_rule['cable_path'][0]['assumption'],
            'trunk_mpos_terminate_directly_on_shuffle_cassette',
        )
        self.assertEqual(
            leaf_spine_rule['cable_path'][0]['cable_profile_assignment'],
            'leaf-to-spine-structured-trunk',
        )
        cable_profiles = {
            profile['slug']: profile
            for profile in result.architecture.metadata['cable_profiles']
        }
        self.assertEqual(
            set(cable_profiles),
            {
                'trunk-96f-mpo8-sm-apc-unpinned-unpinned',
                'trunk-96f-sm-mpo8-unpinned-unpinned',
                'trunk-72f-sm-mpo8-unpinned-unpinned',
            },
        )
        self.assertEqual(cable_profiles['trunk-96f-mpo8-sm-apc-unpinned-unpinned']['fiber_count'], 96)
        self.assertEqual(cable_profiles['trunk-96f-mpo8-sm-apc-unpinned-unpinned']['mpo_connector_count'], 12)
        self.assertEqual(cable_profiles['trunk-72f-sm-mpo8-unpinned-unpinned']['fiber_count'], 72)
        self.assertEqual(cable_profiles['trunk-72f-sm-mpo8-unpinned-unpinned']['mpo_connector_count'], 9)
        cable_assignments = {
            assignment['slug']: assignment
            for assignment in result.architecture.metadata['cable_profile_assignments']
        }
        self.assertEqual(
            cable_assignments['leaf-to-spine-structured-trunk']['profile_slugs'],
            [
                'trunk-96f-mpo8-sm-apc-unpinned-unpinned',
                'trunk-96f-sm-mpo8-unpinned-unpinned',
                'trunk-72f-sm-mpo8-unpinned-unpinned',
            ],
        )
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
        self.assertEqual(ArchitectureRole.objects.filter(architecture=architecture).count(), 15)
        self.assertEqual(TransferPattern.objects.filter(architecture=architecture).count(), 4)
        self.assertEqual(AllocationRuleSet.objects.filter(architecture=architecture).count(), 5)
        self.assertEqual(StampTemplate.objects.filter(slug=STAMP_TEMPLATE_SLUG).count(), 1)
