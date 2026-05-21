from __future__ import annotations

from dataclasses import dataclass

from netbox_plant_graph.models import (
    AllocationRuleSet,
    ArchitectureRole,
    FabricArchitecture,
    StampTemplate,
    TransferPattern,
)


ARCHITECTURE_SLUG = 'roce-4-plane-gb300-2x2-shuffle'
ARCHITECTURE_VERSION = 'v2'
STAMP_TEMPLATE_SLUG = 'roce-4-plane-mini-proof'

MPO_POSITION_COUNT = 12
MPO_DARK_POSITIONS = (5, 6, 7, 8)


CHANNEL_MAP_MATRIX = (
    {
        'subinterface_index': 1,
        'mpo_index': 1,
        'positions': [1, 12, 2, 11],
    },
    {
        'subinterface_index': 2,
        'mpo_index': 1,
        'positions': [3, 10, 4, 9],
    },
    {
        'subinterface_index': 3,
        'mpo_index': 2,
        'positions': [1, 12, 2, 11],
    },
    {
        'subinterface_index': 4,
        'mpo_index': 2,
        'positions': [3, 10, 4, 9],
    },
)

ACTIVE_POSITION_GROUP_A = tuple(CHANNEL_MAP_MATRIX[0]['positions'])
ACTIVE_POSITION_GROUP_B = tuple(CHANNEL_MAP_MATRIX[1]['positions'])
ACTIVE_POSITION_GROUPS = (ACTIVE_POSITION_GROUP_A, ACTIVE_POSITION_GROUP_B)
SHUFFLE_MPO_GROUPS = ((1, 2), (3, 4))


def key_down_roll_position(position_number: int, *, position_count: int = MPO_POSITION_COUNT) -> int:
    return int(position_count) + 1 - int(position_number)


def key_down_roll_position_pairs(src_positions, base_dst_positions) -> tuple[tuple[int, int], ...]:
    return tuple(
        (int(src_position), key_down_roll_position(dst_position))
        for src_position, dst_position in zip(src_positions, base_dst_positions, strict=True)
    )


def shuffle_2x2_transfer_position_pairs(*, front_index: int, rear_index: int) -> tuple[tuple[int, int], ...]:
    """
    Return the active-position transfer pairs for one front/rear MPO crossing.

    The shuffle first splits each front MPO across the two rear MPOs, then
    applies the key-down MPO roll on the rear side. This models the cassette as
    a true transform, not a straight-through group fanout.
    """
    for first_front, second_front in SHUFFLE_MPO_GROUPS:
        if front_index not in {first_front, second_front}:
            continue
        first_rear, second_rear = first_front, second_front
        if rear_index not in {first_rear, second_rear}:
            return ()
        if front_index == first_front and rear_index == first_rear:
            return key_down_roll_position_pairs(ACTIVE_POSITION_GROUP_A, ACTIVE_POSITION_GROUP_A)
        if front_index == first_front and rear_index == second_rear:
            return key_down_roll_position_pairs(ACTIVE_POSITION_GROUP_B, ACTIVE_POSITION_GROUP_A)
        if front_index == second_front and rear_index == first_rear:
            return key_down_roll_position_pairs(ACTIVE_POSITION_GROUP_A, ACTIVE_POSITION_GROUP_B)
        if front_index == second_front and rear_index == second_rear:
            return key_down_roll_position_pairs(ACTIVE_POSITION_GROUP_B, ACTIVE_POSITION_GROUP_B)
    return ()


ROLE_DEFINITIONS = (
    {
        'slug': 'gpu_tray',
        'name': 'GB300 tray',
        'role_kind': 'active_device_group',
        'description': 'Compute tray containing four fabric-facing OSFP endpoints.',
        'metadata': {'endpoint_model': {'osfp_count': 4, 'mpo_per_osfp': 2}},
    },
    {
        'slug': 'gpu_osfp',
        'name': 'GPU OSFP',
        'role_kind': 'active_port',
        'description': 'Plugin-owned OSFP endpoint anchored to a GPU tray device port.',
        'metadata': {'connector_kind': 'osfp', 'mpo_children': 2, 'channels': 4},
    },
    {
        'slug': 'gpu_mpo',
        'name': 'GPU OSFP MPO',
        'role_kind': 'active_subconnector',
        'description': 'MPO child connector on a GPU OSFP.',
        'metadata': {'connector_kind': 'mpo-12', 'position_count': 12},
    },
    {
        'slug': 'leaf_switch',
        'name': 'Leaf switch',
        'role_kind': 'active_device',
        'description': 'Leaf switch participating in one or more fabric planes.',
        'metadata': {'plane_scoped': True},
    },
    {
        'slug': 'leaf_osfp',
        'name': 'Leaf OSFP',
        'role_kind': 'active_port',
        'description': 'Plugin-owned OSFP endpoint anchored to a leaf device port.',
        'metadata': {'connector_kind': 'osfp', 'mpo_children': 2, 'channels': 4},
    },
    {
        'slug': 'leaf_mpo',
        'name': 'Leaf OSFP MPO',
        'role_kind': 'active_subconnector',
        'description': 'MPO child connector on a leaf OSFP.',
        'metadata': {'connector_kind': 'mpo-12', 'position_count': 12},
    },
    {
        'slug': 'shuffle_cassette',
        'name': 'Shuffle cassette',
        'role_kind': 'passive_assembly',
        'description': 'Passive cassette containing internal position-to-position shuffle maps.',
        'metadata': {'front_mpo_count': 4, 'rear_mpo_count': 4, 'shuffle_groups': 2},
    },
    {
        'slug': 'shuffle_front_mpo',
        'name': 'Shuffle front MPO',
        'role_kind': 'passive_connector',
        'description': 'Front-side MPO connector on a shuffle cassette.',
        'metadata': {'connector_kind': 'mpo-12', 'position_count': 12},
    },
    {
        'slug': 'shuffle_rear_mpo',
        'name': 'Shuffle rear MPO',
        'role_kind': 'passive_connector',
        'description': 'Rear-side MPO connector on a shuffle cassette.',
        'metadata': {'connector_kind': 'mpo-12', 'position_count': 12},
    },
)


TRANSFER_PATTERN_DEFINITIONS = (
    {
        'slug': 'identity',
        'name': 'Identity',
        'pattern_kind': 'identity',
        'rule': {'type': 'position_map', 'mode': 'identity'},
        'metadata': {},
    },
    {
        'slug': 'shuffle_2x2',
        'name': '2x2 shuffle',
        'pattern_kind': 'shuffle_2x2',
        'rule': {
            'type': 'position_map',
            'groups': [
                {
                    'front_mpos': [1, 2],
                    'rear_mpos': [1, 2],
                    'rear_position_transform': {
                        'type': 'key_down_roll',
                        'position_count': MPO_POSITION_COUNT,
                        'formula': 'dst_position = position_count + 1 - base_dst_position',
                    },
                    'active_position_groups': {
                        'A': [1, 12, 2, 11],
                        'B': [3, 10, 4, 9],
                    },
                    'matrix': [
                        {'front_mpo': 1, 'rear_mpo': 1, 'src_group': 'A', 'dst_group': 'A'},
                        {'front_mpo': 1, 'rear_mpo': 2, 'src_group': 'B', 'dst_group': 'A'},
                        {'front_mpo': 2, 'rear_mpo': 1, 'src_group': 'A', 'dst_group': 'B'},
                        {'front_mpo': 2, 'rear_mpo': 2, 'src_group': 'B', 'dst_group': 'B'},
                    ],
                },
            ],
            'bidirectional': True,
        },
        'metadata': {
            'description': (
                '2x2 channel-group matrix: each front MPO fans out across both rear MPOs, '
                'then applies the key-down MPO position roll on the rear side.'
            )
        },
    },
    {
        'slug': 'second_third_mpo_stagger',
        'name': 'Second/third MPO stagger',
        'pattern_kind': 'stagger',
        'rule': {
            'type': 'allocation_transform',
            'scope': 'groups_of_four_mpos',
            'staggered_members': [2, 3],
        },
        'metadata': {'description': 'Captures the Notion shuffle stagger as a named transform.'},
    },
)


ALLOCATION_RULE_DEFINITIONS = (
    {
        'slug': 'gb300_osfp_mpo_order',
        'name': 'GB300 OSFP/MPO order',
        'rule': {
            'osfp_count': 4,
            'mpo_per_osfp': 2,
            'positions_per_mpo': 12,
            'order': 'osfp_ascending_then_mpo_ascending',
        },
        'metadata': {},
    },
    {
        'slug': 'shuffle_cassette_fill_order',
        'name': 'Shuffle cassette fill order',
        'rule': {
            'cassette_count_per_mini_proof': 2,
            'front_mpos_per_cassette': 4,
            'rear_mpos_per_cassette': 4,
            'order': 'cassette_then_side_then_mpo',
        },
        'metadata': {},
    },
    {
        'slug': 'leaf_plane_striping',
        'name': 'Leaf plane striping',
        'rule': {
            'plane_count': 4,
            'assignment': 'one_leaf_port_per_plane_in_mini_proof',
        },
        'metadata': {},
    },
    {
        'slug': 'channel_subinterface_mapping',
        'name': 'Channel sub-interface mapping',
        'rule': {
            'speed_gbps': 200,
            'parent_interface_scope': 'physical_osfp',
            'child_name_pattern': '{parent_name}/{channel_index}',
            'channel_map_matrix': [dict(entry) for entry in CHANNEL_MAP_MATRIX],
        },
        'metadata': {},
    },
)


STAMP_TEMPLATE = {
    'kind': 'mini_proof',
    'executor': {
        'mode': 'hybrid',
        'primitive': 'roce_4plane_mini_proof',
        'version': 1,
    },
    'architecture_slug': ARCHITECTURE_SLUG,
    'architecture_version': ARCHITECTURE_VERSION,
    'planes': [1, 2, 3, 4],
    'gpu_tray': {
        'count': 1,
        'osfp_count': 4,
        'mpo_per_osfp': 2,
        'positions_per_mpo': 12,
    },
    'shuffle_cassettes': {
        'count': 2,
        'front_mpo_count': 4,
        'rear_mpo_count': 4,
        'positions_per_mpo': 12,
    },
    'leaf_ports': {
        'count': 4,
        'plane_assignment': {'1': 1, '2': 2, '3': 3, '4': 4},
    },
    'channel_subinterfaces': {
        'enabled': True,
        'name_pattern': '{parent_name}/{channel_index}',
        'type': 'virtual',
        'speed_gbps': 200,
        'channel_map_matrix': [dict(entry) for entry in CHANNEL_MAP_MATRIX],
    },
    'source_bindings': [
        {
            'kind': 'node',
            'address': 'GB300-TRAY-1',
            'field_name': 'gpu_tray_device',
            'label': 'GPU Tray Device',
            'model': 'dcim.device',
            'required': False,
            'help_text': 'Optional NetBox Device to anchor the stamped GB300 tray node.',
        },
        {
            'kind': 'endpoint',
            'address': 'GB300-TRAY-1.OSFP-1',
            'field_name': 'gpu_osfp_1_interface',
            'label': 'GPU OSFP-1 Interface',
            'model': 'dcim.interface',
            'required': False,
            'device_binding_address': 'GB300-TRAY-1',
            'help_text': 'Optional NetBox Interface to anchor the stamped GB300 OSFP-1 endpoint.',
        },
    ],
    'proof_paths': [
        {'plane': 1, 'gpu_osfp': 1, 'cassette': 1, 'front_position': 1, 'rear_position': 9, 'leaf': 1},
        {'plane': 2, 'gpu_osfp': 2, 'cassette': 1, 'front_position': 2, 'rear_position': 10, 'leaf': 2},
        {'plane': 3, 'gpu_osfp': 3, 'cassette': 2, 'front_position': 1, 'rear_position': 9, 'leaf': 3},
        {'plane': 4, 'gpu_osfp': 4, 'cassette': 2, 'front_position': 2, 'rear_position': 10, 'leaf': 4},
    ],
}


@dataclass(frozen=True)
class ArchitectureFixtureResult:
    architecture: FabricArchitecture
    roles: dict[str, ArchitectureRole]
    transfer_patterns: dict[str, TransferPattern]
    allocation_rule_sets: dict[str, AllocationRuleSet]
    stamp_template: StampTemplate


def ensure_roce_4plane_shuffle_architecture() -> ArchitectureFixtureResult:
    architecture, _ = FabricArchitecture.objects.update_or_create(
        slug=ARCHITECTURE_SLUG,
        version=ARCHITECTURE_VERSION,
        defaults={
            'name': 'RoCE 4-plane GB300 2x2 shuffle',
            'status': 'active',
            'plane_count': 4,
            'description': 'Executable V2 architecture fixture for the four-plane GB300 shuffle proof.',
            'metadata': {
                'semantics': {
                    'optical_lane_scope': 'transceiver_local',
                    'fiber_path_scope': 'connector_position_graph',
                    'netbox_cables': 'forbidden_for_modeled_fabric',
                },
            },
        },
    )

    roles = {}
    for definition in ROLE_DEFINITIONS:
        role, _ = ArchitectureRole.objects.update_or_create(
            architecture=architecture,
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'role_kind': definition['role_kind'],
                'description': definition['description'],
                'metadata': definition['metadata'],
            },
        )
        roles[definition['slug']] = role

    transfer_patterns = {}
    for definition in TRANSFER_PATTERN_DEFINITIONS:
        pattern, _ = TransferPattern.objects.update_or_create(
            architecture=architecture,
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'pattern_kind': definition['pattern_kind'],
                'rule': definition['rule'],
                'metadata': definition['metadata'],
            },
        )
        transfer_patterns[definition['slug']] = pattern

    allocation_rule_sets = {}
    for definition in ALLOCATION_RULE_DEFINITIONS:
        rule_set, _ = AllocationRuleSet.objects.update_or_create(
            architecture=architecture,
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'rule': definition['rule'],
                'metadata': definition['metadata'],
            },
        )
        allocation_rule_sets[definition['slug']] = rule_set

    stamp_template, _ = StampTemplate.objects.update_or_create(
        slug=STAMP_TEMPLATE_SLUG,
        defaults={
            'architecture': architecture,
            'name': 'RoCE 4-plane mini proof',
            'description': 'One GB300 tray, two shuffle cassettes, and four leaf ports for V2 resolver proof.',
            'template': STAMP_TEMPLATE,
            'metadata': {'fixture': True},
        },
    )

    return ArchitectureFixtureResult(
        architecture=architecture,
        roles=roles,
        transfer_patterns=transfer_patterns,
        allocation_rule_sets=allocation_rule_sets,
        stamp_template=stamp_template,
    )
