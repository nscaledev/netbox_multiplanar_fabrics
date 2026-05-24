from __future__ import annotations

from dataclasses import dataclass

from netbox_plant_graph.models import (
    AllocationRuleSet,
    ArchitectureRole,
    FabricArchitecture,
    StampTemplate,
    TransferPattern,
)
from netbox_plant_graph.services.architecture_schema import (
    ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
    ArchitectureSchemaDefinition,
    ArchitectureSchemaValidationResult,
    validate_architecture_schema,
)


ARCHITECTURE_SLUG = 'roce-4-plane-gb300-2x2-shuffle'
ARCHITECTURE_VERSION = 'v2'
STAMP_TEMPLATE_SLUG = 'roce-4-plane-mini-proof'
H100_DIRECT_ATTACH_ARCHITECTURE_SLUG = 'roce-4-plane-h100-direct-attach'
GB300_8PLANE_ARCHITECTURE_SLUG = 'roce-8-plane-gb300-2x2-shuffle'
H100_DIRECT_ATTACH_STAMP_TEMPLATE_SLUG = 'roce-4-plane-h100-direct-attach-mini-proof'
GB300_8PLANE_STAMP_TEMPLATE_SLUG = 'roce-8-plane-gb300-mini-proof'

MPO_POSITION_COUNT = 12
MPO_DARK_POSITIONS = (5, 6, 7, 8)
SUPPORTED_GB300_PLANE_RANGE = (2, 16)


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


def key_down_roll_position_pairs(
    src_positions,
    base_dst_positions,
    *,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (int(src_position), key_down_roll_position(dst_position, position_count=position_count))
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


def direct_attach_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) != int(rear_index):
        return ()
    return tuple((position, position) for position in range(1, int(position_count) + 1))


def polarity_type_b_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) != int(rear_index):
        return ()
    return tuple(
        (position, key_down_roll_position(position, position_count=position_count))
        for position in range(1, int(position_count) + 1)
    )


def polarity_type_c_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) != int(rear_index):
        return ()
    pairs: list[tuple[int, int]] = []
    for position in range(1, int(position_count) + 1, 2):
        if position == int(position_count):
            pairs.append((position, position))
        else:
            pairs.append((position, position + 1))
            pairs.append((position + 1, position))
    return tuple(pairs)


def shuffle_1x4_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) != 1 or int(rear_index) not in {1, 2, 3, 4}:
        return ()
    chunk_size = int(position_count) // 4
    start = (int(rear_index) - 1) * chunk_size + 1
    end = int(position_count) if int(rear_index) == 4 else start + chunk_size - 1
    return tuple((position, position) for position in range(start, end + 1))


def shuffle_2x2_mpo24_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = 24,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) not in {1, 2} or int(rear_index) not in {1, 2}:
        return ()
    offset = 0 if int(front_index) == int(rear_index) else int(position_count) // 2
    src_positions = tuple(range(1 + offset, 1 + offset + int(position_count) // 2))
    return tuple(
        (position, key_down_roll_position(position, position_count=position_count))
        for position in src_positions
    )


def shuffle_4x4_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) not in {1, 2, 3, 4} or int(rear_index) not in {1, 2, 3, 4}:
        return ()
    offset = ((int(front_index) + int(rear_index) - 2) % 4) * (int(position_count) // 4)
    width = int(position_count) // 4
    return tuple((position, position) for position in range(offset + 1, offset + width + 1))


def shuffle_nxm_position_pairs(
    *,
    front_index: int,
    rear_index: int,
    position_count: int = MPO_POSITION_COUNT,
) -> tuple[tuple[int, int], ...]:
    if int(front_index) < 1 or int(rear_index) < 1:
        return ()
    return tuple((position, position) for position in range(1, int(position_count) + 1))


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
        'metadata': {
            'connector_kind': 'osfp',
            'mpo_children': 2,
            'channels': 4,
            'channels_per_osfp': 4,
            'channel_speed_gbps': 200,
            'speed_gbps': 800,
        },
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
        'metadata': {'plane_scoped': True, 'fabric_tier': 'leaf'},
    },
    {
        'slug': 'leaf_osfp',
        'name': 'Leaf OSFP',
        'role_kind': 'active_port',
        'description': 'Plugin-owned OSFP endpoint anchored to a leaf device port.',
        'metadata': {
            'connector_kind': 'osfp',
            'mpo_children': 2,
            'channels': 4,
            'channels_per_osfp': 4,
            'channel_speed_gbps': 200,
            'speed_gbps': 800,
        },
    },
    {
        'slug': 'leaf_mpo',
        'name': 'Leaf OSFP MPO',
        'role_kind': 'active_subconnector',
        'description': 'MPO child connector on a leaf OSFP.',
        'metadata': {'connector_kind': 'mpo-12', 'position_count': 12},
    },
    {
        'slug': 'spine_switch',
        'name': 'Backend spine switch',
        'role_kind': 'active_tier_2_device',
        'description': 'Spine switch participating in a backend RoCE fabric plane.',
        'metadata': {'plane_scoped': True, 'fabric_tier': 'spine'},
    },
    {
        'slug': 'spine_osfp',
        'name': 'Spine OSFP',
        'role_kind': 'active_tier_2_port',
        'description': 'Plugin-owned OSFP endpoint anchored to a backend spine switch port.',
        'metadata': {
            'connector_kind': 'osfp',
            'mpo_children': 2,
            'channels': 4,
            'channels_per_osfp': 4,
            'channel_speed_gbps': 200,
            'speed_gbps': 800,
            'fabric_tier': 'spine',
        },
    },
    {
        'slug': 'spine_mpo',
        'name': 'Spine OSFP MPO',
        'role_kind': 'active_subconnector',
        'description': 'MPO child connector on a backend spine OSFP.',
        'metadata': {'connector_kind': 'mpo-12', 'position_count': 12, 'fabric_tier': 'spine'},
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
    {
        'slug': 'spine_shuffle_cassette',
        'name': 'Spine-side shuffle cassette',
        'role_kind': 'passive_assembly',
        'description': 'Passive spine-side cassette that shuffles leaf trunks into spine switch jumpers.',
        'metadata': {
            'front_mpo_count': 4,
            'rear_mpo_count': 4,
            'shuffle_groups': 2,
            'placement': 'spine_rack_above_backend_spines',
            'fabric_segment': 'leaf_to_spine_backend',
        },
    },
    {
        'slug': 'spine_shuffle_rear_mpo',
        'name': 'Spine shuffle rear MPO',
        'role_kind': 'passive_connector',
        'description': 'Rear-side MPO on a spine-side shuffle cassette receiving direct leaf trunks.',
        'metadata': {
            'connector_kind': 'mpo-12',
            'position_count': 12,
            'cable_side': 'leaf_trunk',
            'fabric_segment': 'leaf_to_spine_backend',
        },
    },
    {
        'slug': 'spine_shuffle_front_mpo',
        'name': 'Spine shuffle front MPO',
        'role_kind': 'passive_connector',
        'description': 'Front-side MPO on a spine-side shuffle cassette patched to backend spine switches.',
        'metadata': {
            'connector_kind': 'mpo-12',
            'position_count': 12,
            'cable_side': 'spine_jumper',
            'fabric_segment': 'leaf_to_spine_backend',
        },
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
        'slug': 'leaf_spine_shuffle_2x2',
        'name': 'Leaf-to-spine 2x2 shuffle',
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
            'fabric_segment': 'leaf_to_spine_backend',
            'description': (
                'Spine-side cassette transform for folded-Clos leaf-to-spine reach: leaf trunks '
                'terminate directly on cassette rear MPOs, shuffle internally, then patch from '
                'front MPOs into backend spine switch OSFPs.'
            ),
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


GB300_CABLE_PROFILE_DEFINITIONS = (
    {
        'slug': 'trunk-96f-mpo8-sm-apc-unpinned-unpinned',
        'name': '96f MPO8 SM APC unpinned/unpinned trunk',
        'assembly_kind': 'trunk',
        'fiber_count': 96,
        'connector_family': 'MPO8',
        'mpo_connector_count': 12,
        'fibers_per_mpo': 8,
        'fiber_mode': 'single-mode',
        'polish': 'APC',
        'side_a_pinning': 'unpinned',
        'side_b_pinning': 'unpinned',
        'metadata': {
            'source_label': '96f MPO8 SM APC Unpinned/Unpinned',
            'modeling_target': 'CableAssembly',
            'strand_resolution': 'FiberStrand.cable_site + FiberStrand.cable_id',
            'notes': 'Purchased trunk assembly; site design/BOM supplies instance length and cable ID.',
        },
    },
    {
        'slug': 'trunk-96f-sm-mpo8-unpinned-unpinned',
        'name': '96f SM MPO8 unpinned/unpinned trunk',
        'assembly_kind': 'trunk',
        'fiber_count': 96,
        'connector_family': 'MPO8',
        'mpo_connector_count': 12,
        'fibers_per_mpo': 8,
        'fiber_mode': 'single-mode',
        'polish': 'not_specified',
        'side_a_pinning': 'unpinned',
        'side_b_pinning': 'unpinned',
        'metadata': {
            'source_label': '96f SM MPO8 Unpinned/Unpinned',
            'modeling_target': 'CableAssembly',
            'strand_resolution': 'FiberStrand.cable_site + FiberStrand.cable_id',
            'notes': 'Purchased trunk assembly; polish is not explicit in the observed source label.',
        },
    },
    {
        'slug': 'trunk-72f-sm-mpo8-unpinned-unpinned',
        'name': '72f SM MPO8 unpinned/unpinned trunk',
        'assembly_kind': 'trunk',
        'fiber_count': 72,
        'connector_family': 'MPO8',
        'mpo_connector_count': 9,
        'fibers_per_mpo': 8,
        'fiber_mode': 'single-mode',
        'polish': 'not_specified',
        'side_a_pinning': 'unpinned',
        'side_b_pinning': 'unpinned',
        'metadata': {
            'source_label': '72f SM MPO8 Unpinned/Unpinned',
            'modeling_target': 'CableAssembly',
            'strand_resolution': 'FiberStrand.cable_site + FiberStrand.cable_id',
            'notes': 'Purchased trunk assembly; site design/BOM selects where this lower-count trunk is used.',
        },
    },
)


GB300_CABLE_PROFILE_ASSIGNMENTS = (
    {
        'slug': 'gb300-to-leaf-structured-trunk',
        'topology_segment': 'gb300_to_leaf_shuffle',
        'segment_kind': 'structured_trunk',
        'source_role': 'shuffle_rear_mpo',
        'destination_role': 'leaf_mpo',
        'profile_slugs': [
            'trunk-96f-mpo8-sm-apc-unpinned-unpinned',
            'trunk-96f-sm-mpo8-unpinned-unpinned',
            'trunk-72f-sm-mpo8-unpinned-unpinned',
        ],
        'selection_rule': 'site_design_or_import_resolves_profile_and_length',
        'metadata': {
            'description': (
                'Structured trunk options between the GB300/leaf shuffle cassette rear MPOs '
                'and backend leaf switch MPO endpoints.'
            ),
        },
    },
    {
        'slug': 'leaf-to-spine-structured-trunk',
        'topology_segment': 'leaf_to_spine_shuffle',
        'segment_kind': 'structured_trunk',
        'source_role': 'leaf_mpo',
        'destination_role': 'spine_shuffle_rear_mpo',
        'profile_slugs': [
            'trunk-96f-mpo8-sm-apc-unpinned-unpinned',
            'trunk-96f-sm-mpo8-unpinned-unpinned',
            'trunk-72f-sm-mpo8-unpinned-unpinned',
        ],
        'selection_rule': 'site_design_or_import_resolves_profile_and_length',
        'metadata': {
            'description': (
                'Structured trunk options from backend leaf switch MPO endpoints directly '
                'to spine-side shuffle cassette rear MPOs.'
            ),
            'assumption': 'trunk_mpos_terminate_directly_on_shuffle_cassette',
        },
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
    {
        'slug': 'backend_leaf_spine_shuffle',
        'name': 'Backend leaf-spine shuffle topology',
        'rule': {
            'plane_count': 4,
            'fabric_segment': 'leaf_to_spine_backend',
            'source_device_role': 'leaf_switch',
            'source_port_role': 'leaf_osfp',
            'source_mpo_role': 'leaf_mpo',
            'destination_device_role': 'spine_switch',
            'destination_port_role': 'spine_osfp',
            'destination_mpo_role': 'spine_mpo',
            'shuffle_cassette_role': 'spine_shuffle_cassette',
            'shuffle_rear_mpo_role': 'spine_shuffle_rear_mpo',
            'shuffle_front_mpo_role': 'spine_shuffle_front_mpo',
            'transfer_pattern': 'leaf_spine_shuffle_2x2',
            'leaf_spine_osfp_cages_per_leaf': 32,
            'channels_per_osfp': 4,
            'spine_switches_per_plane': 126,
            'available_spine_interfaces_per_leaf': 128,
            'planned_spare_spine_interfaces_per_leaf': 2,
            'requires_shuffle': True,
            'cable_path': [
                {
                    'order': 1,
                    'medium': 'structured_trunk',
                    'source': 'leaf_mpo',
                    'destination': 'spine_shuffle_rear_mpo',
                    'cable_profile_assignment': 'leaf-to-spine-structured-trunk',
                    'cable_profile_candidates': [
                        'trunk-96f-mpo8-sm-apc-unpinned-unpinned',
                        'trunk-96f-sm-mpo8-unpinned-unpinned',
                        'trunk-72f-sm-mpo8-unpinned-unpinned',
                    ],
                    'assumption': 'trunk_mpos_terminate_directly_on_shuffle_cassette',
                },
                {
                    'order': 2,
                    'medium': 'passive_transfer',
                    'source': 'spine_shuffle_rear_mpo',
                    'destination': 'spine_shuffle_front_mpo',
                    'transfer_pattern': 'leaf_spine_shuffle_2x2',
                },
                {
                    'order': 3,
                    'medium': 'short_jumper',
                    'source': 'spine_shuffle_front_mpo',
                    'destination': 'spine_mpo',
                    'nominal_length_m': 2,
                },
            ],
        },
        'metadata': {
            'description': (
                'Folded-Clos backend uplink rule: each plane-local leaf uses 32 OSFP cages '
                'in 4x200Gbps mode to reach 126 spines, leaving two child interfaces spare.'
            ),
            'evidence': (
                'Fiber BOM contains leaf-to-shuffle trunks and shuffle-to-spine 2m jumpers, '
                'with no separate patch-panel or extra jumper population.'
            ),
        },
    },
)

GB300_PARAMETER_SCHEMA = {
    'type': 'object',
    'properties': {
        'plane_count': {
            'type': 'integer',
            'minimum': SUPPORTED_GB300_PLANE_RANGE[0],
            'maximum': SUPPORTED_GB300_PLANE_RANGE[1],
            'default': 4,
        },
        'topology_parameters': {
            'type': 'object',
            'properties': {
                'gpu_tray_count': {'type': 'integer', 'minimum': 1},
                'leaf_count_per_plane': {'type': 'integer', 'minimum': 1},
                'spine_count_per_plane': {'type': 'integer', 'minimum': 1, 'default': 126},
                'leaf_spine_osfp_cages_per_leaf': {'type': 'integer', 'minimum': 1, 'default': 32},
                'racks_per_pod': {'type': 'integer', 'minimum': 1},
                'pods_per_fabric': {'type': 'integer', 'minimum': 1},
            },
        },
        'wavelength_plan': {
            'type': 'object',
            'properties': {
                'band': {'type': 'string'},
                'channel_count': {'type': 'integer', 'minimum': 1},
                'channels': {'type': 'array', 'items': {'type': 'number'}},
            },
        },
        'name_patterns': {'type': 'object'},
        'allocation_rule_override': {'type': 'string'},
    },
    'additionalProperties': True,
}

GB300_REQUIRED_DEVICE_TYPES = {
    'gpu_tray': ('nvidia-gb300-nvl72-tray',),
    'leaf_switch': ('nvidia-spectrum-x-leaf',),
    'spine_switch': ('nvidia-spectrum-x-spine', 'roce-spine-200g-plane', 'sn5610'),
}

H100_MPO_POSITION_COUNT = 8
H100_MPO_DARK_POSITIONS = (3, 4, 5, 6)
H100_CHANNEL_MAP_MATRIX = (
    {
        'subinterface_index': 1,
        'mpo_index': 1,
        'positions': [1, 8],
    },
    {
        'subinterface_index': 2,
        'mpo_index': 1,
        'positions': [2, 7],
    },
)
H100_ACTIVE_POSITION_GROUP_A = tuple(H100_CHANNEL_MAP_MATRIX[0]['positions'])
H100_ACTIVE_POSITION_GROUP_B = tuple(H100_CHANNEL_MAP_MATRIX[1]['positions'])
H100_DIRECT_ATTACH_POSITION_PAIRS = tuple(
    (position, position) for position in range(1, H100_MPO_POSITION_COUNT + 1)
)

H100_ROLE_DEFINITIONS = (
    {
        'slug': 'h100_node',
        'name': 'H100 node',
        'role_kind': 'active_device_group',
        'description': 'Compute node with direct-attached 400G OSFP endpoints.',
        'metadata': {'endpoint_model': {'osfp_count': 2, 'mpo_per_osfp': 1}},
    },
    {
        'slug': 'h100_osfp',
        'name': 'H100 OSFP',
        'role_kind': 'active_port',
        'description': '400G OSFP endpoint anchored to an H100 compute node.',
        'metadata': {
            'connector_kind': 'osfp',
            'mpo_children': 1,
            'channels': 2,
            'channels_per_osfp': 2,
            'channel_speed_gbps': 200,
            'speed_gbps': 400,
        },
    },
    {
        'slug': 'h100_mpo',
        'name': 'H100 OSFP MPO',
        'role_kind': 'active_subconnector',
        'description': 'MPO child connector on an H100 OSFP.',
        'metadata': {'connector_kind': 'mpo-8', 'position_count': H100_MPO_POSITION_COUNT},
    },
    {
        'slug': 'leaf_switch',
        'name': 'Leaf switch',
        'role_kind': 'active_device',
        'description': 'Leaf switch participating in one or more H100 fabric planes.',
        'metadata': {'plane_scoped': True, 'fabric_tier': 'leaf'},
    },
    {
        'slug': 'leaf_osfp',
        'name': 'Leaf OSFP',
        'role_kind': 'active_port',
        'description': '400G OSFP leaf endpoint for H100 direct attach.',
        'metadata': {
            'connector_kind': 'osfp',
            'mpo_children': 1,
            'channels': 2,
            'channels_per_osfp': 2,
            'channel_speed_gbps': 200,
            'speed_gbps': 400,
        },
    },
    {
        'slug': 'leaf_mpo',
        'name': 'Leaf OSFP MPO',
        'role_kind': 'active_subconnector',
        'description': 'MPO child connector on a leaf OSFP.',
        'metadata': {'connector_kind': 'mpo-8', 'position_count': H100_MPO_POSITION_COUNT},
    },
)

H100_TRANSFER_PATTERN_DEFINITIONS = (
    {
        'slug': 'identity',
        'name': 'Identity',
        'pattern_kind': 'identity',
        'rule': {'type': 'position_map', 'mode': 'identity'},
        'metadata': {},
    },
    {
        'slug': 'direct_attach',
        'name': 'Direct attach',
        'pattern_kind': 'direct_attach',
        'rule': {
            'type': 'position_map',
            'front_mpos': [1],
            'rear_mpos': [1],
            'position_count': H100_MPO_POSITION_COUNT,
            'matrix': [
                {
                    'front_mpo': 1,
                    'rear_mpo': 1,
                    'position_pairs': [list(pair) for pair in H100_DIRECT_ATTACH_POSITION_PAIRS],
                },
            ],
            'bidirectional': True,
        },
        'metadata': {'description': 'Straight-through direct attach from compute OSFP to leaf OSFP.'},
    },
)

H100_ALLOCATION_RULE_DEFINITIONS = (
    {
        'slug': 'h100_osfp_mpo_order',
        'name': 'H100 OSFP/MPO order',
        'rule': {
            'osfp_count': 2,
            'mpo_per_osfp': 1,
            'positions_per_mpo': H100_MPO_POSITION_COUNT,
            'order': 'osfp_ascending_then_mpo_ascending',
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
            'channel_map_matrix': [dict(entry) for entry in H100_CHANNEL_MAP_MATRIX],
        },
        'metadata': {},
    },
)

H100_PARAMETER_SCHEMA = {
    'type': 'object',
    'properties': {
        'plane_count': {'type': 'integer', 'minimum': 1, 'maximum': 4, 'default': 4},
        'topology_parameters': {'type': 'object'},
        'name_patterns': {'type': 'object'},
    },
    'additionalProperties': True,
}

H100_REQUIRED_DEVICE_TYPES = {
    'h100_node': ('nvidia-h100-node',),
    'leaf_switch': ('nvidia-spectrum-x-leaf',),
}


STAMP_TEMPLATE = {
    'kind': 'mini_proof',
    'connection_geometry': 'shuffle_2x2',
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


GB300_8PLANE_STAMP_TEMPLATE = {
    'kind': 'mini_proof',
    'connection_geometry': 'shuffle_2x2',
    'executor': {
        'mode': 'hybrid',
        'primitive': 'roce_gb300_shuffle_mini_proof',
        'version': 1,
    },
    'architecture_slug': GB300_8PLANE_ARCHITECTURE_SLUG,
    'architecture_version': ARCHITECTURE_VERSION,
    'planes': [1, 2, 3, 4, 5, 6, 7, 8],
    'topology_parameters': {
        'plane_count': {'value': 8, 'min': 8, 'max': 8},
        'gpu_tray_count': 2,
        'leaf_count_per_plane': 1,
    },
    'gpu_tray': {
        'count': 2,
        'address_prefix': 'GB300-TRAY',
        'osfp_count': 4,
        'mpo_per_osfp': 2,
        'positions_per_mpo': 12,
    },
    'shuffle_cassettes': {
        'count': 4,
        'front_mpo_count': 4,
        'rear_mpo_count': 4,
        'positions_per_mpo': 12,
    },
    'leaf_ports': {
        'count': 8,
        'address_prefix': 'LEAF',
        'plane_assignment': {
            '1': 1,
            '2': 2,
            '3': 3,
            '4': 4,
            '5': 5,
            '6': 6,
            '7': 7,
            '8': 8,
        },
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
            'field_name': 'gpu_tray_1_device',
            'label': 'GPU Tray 1 Device',
            'model': 'dcim.device',
            'required': False,
            'help_text': 'Optional NetBox Device to anchor the first stamped GB300 tray node.',
        },
        {
            'kind': 'endpoint',
            'address': 'GB300-TRAY-1.OSFP-1',
            'field_name': 'gpu_tray_1_osfp_1_interface',
            'label': 'GPU Tray 1 OSFP-1 Interface',
            'model': 'dcim.interface',
            'required': False,
            'device_binding_address': 'GB300-TRAY-1',
            'help_text': 'Optional NetBox Interface to anchor the first stamped GB300 OSFP endpoint.',
        },
    ],
    'proof_paths': [
        {'plane': 1, 'gpu_tray': 1, 'gpu_osfp': 1, 'cassette': 1, 'front_position': 1, 'rear_position': 9, 'leaf': 1},
        {'plane': 2, 'gpu_tray': 1, 'gpu_osfp': 2, 'cassette': 1, 'front_position': 2, 'rear_position': 10, 'leaf': 2},
        {'plane': 3, 'gpu_tray': 1, 'gpu_osfp': 3, 'cassette': 2, 'front_position': 1, 'rear_position': 9, 'leaf': 3},
        {'plane': 4, 'gpu_tray': 1, 'gpu_osfp': 4, 'cassette': 2, 'front_position': 2, 'rear_position': 10, 'leaf': 4},
        {'plane': 5, 'gpu_tray': 2, 'gpu_osfp': 1, 'cassette': 3, 'front_position': 1, 'rear_position': 9, 'leaf': 5},
        {'plane': 6, 'gpu_tray': 2, 'gpu_osfp': 2, 'cassette': 3, 'front_position': 2, 'rear_position': 10, 'leaf': 6},
        {'plane': 7, 'gpu_tray': 2, 'gpu_osfp': 3, 'cassette': 4, 'front_position': 1, 'rear_position': 9, 'leaf': 7},
        {'plane': 8, 'gpu_tray': 2, 'gpu_osfp': 4, 'cassette': 4, 'front_position': 2, 'rear_position': 10, 'leaf': 8},
    ],
}


H100_DIRECT_ATTACH_STAMP_TEMPLATE = {
    'kind': 'mini_proof',
    'connection_geometry': 'direct_attach',
    'executor': {
        'mode': 'hybrid',
        'primitive': 'roce_direct_attach_mini_proof',
        'version': 1,
    },
    'architecture_slug': H100_DIRECT_ATTACH_ARCHITECTURE_SLUG,
    'architecture_version': ARCHITECTURE_VERSION,
    'planes': [1, 2, 3, 4],
    'topology_parameters': {
        'plane_count': {'value': 4, 'min': 1, 'max': 4},
        'gpu_tray_count': 1,
        'leaf_count_per_plane': 1,
    },
    'gpu_tray': {
        'count': 1,
        'address_prefix': 'H100-NODE',
        'osfp_count': 2,
        'mpo_per_osfp': 1,
        'positions_per_mpo': H100_MPO_POSITION_COUNT,
    },
    'leaf_ports': {
        'count': 4,
        'address_prefix': 'LEAF',
        'plane_assignment': {'1': 1, '2': 2, '3': 3, '4': 4},
    },
    'channel_subinterfaces': {
        'enabled': True,
        'name_pattern': '{parent_name}/{channel_index}',
        'type': 'virtual',
        'speed_gbps': 200,
        'channel_map_matrix': [dict(entry) for entry in H100_CHANNEL_MAP_MATRIX],
    },
    'source_bindings': [
        {
            'kind': 'node',
            'address': 'H100-NODE-1',
            'field_name': 'h100_node_device',
            'label': 'H100 Node Device',
            'model': 'dcim.device',
            'required': False,
            'help_text': 'Optional NetBox Device to anchor the stamped H100 node.',
        },
        {
            'kind': 'endpoint',
            'address': 'H100-NODE-1.OSFP-1',
            'field_name': 'h100_osfp_1_interface',
            'label': 'H100 OSFP-1 Interface',
            'model': 'dcim.interface',
            'required': False,
            'device_binding_address': 'H100-NODE-1',
            'help_text': 'Optional NetBox Interface to anchor the first stamped H100 OSFP endpoint.',
        },
    ],
    'proof_paths': [
        {'plane': 1, 'gpu_osfp': 1, 'front_position': 1, 'rear_position': 1, 'leaf': 1},
        {'plane': 2, 'gpu_osfp': 1, 'front_position': 2, 'rear_position': 2, 'leaf': 2},
        {'plane': 3, 'gpu_osfp': 2, 'front_position': 1, 'rear_position': 1, 'leaf': 3},
        {'plane': 4, 'gpu_osfp': 2, 'front_position': 2, 'rear_position': 2, 'leaf': 4},
    ],
}


def _gb300_allocation_rule_definitions(*, plane_count: int) -> tuple[dict, ...]:
    definitions = []
    for definition in ALLOCATION_RULE_DEFINITIONS:
        rule = dict(definition['rule'])
        if definition['slug'] in {'leaf_plane_striping', 'backend_leaf_spine_shuffle'}:
            rule['plane_count'] = plane_count
        if 'channel_map_matrix' in rule:
            rule['channel_map_matrix'] = [dict(entry) for entry in rule['channel_map_matrix']]
        definitions.append({**definition, 'rule': rule})
    return tuple(definitions)


def build_roce_4plane_shuffle_architecture_schema() -> ArchitectureSchemaDefinition:
    return ArchitectureSchemaDefinition(
        slug=ARCHITECTURE_SLUG,
        version=ARCHITECTURE_VERSION,
        plane_count=4,
        roles=ROLE_DEFINITIONS,
        transfer_patterns=TRANSFER_PATTERN_DEFINITIONS,
        allocation_rule_sets=_gb300_allocation_rule_definitions(plane_count=4),
        channel_map_matrix=CHANNEL_MAP_MATRIX,
        active_position_groups={
            'A': ACTIVE_POSITION_GROUP_A,
            'B': ACTIVE_POSITION_GROUP_B,
        },
        dark_positions=MPO_DARK_POSITIONS,
        mpo_position_count=MPO_POSITION_COUNT,
        shuffle_mpo_groups=SHUFFLE_MPO_GROUPS,
        shuffle_pair_provider=shuffle_2x2_transfer_position_pairs,
        channels_per_subinterface=4,
        mpo_count_per_osfp=2,
        min_planes=SUPPORTED_GB300_PLANE_RANGE[0],
        max_planes=SUPPORTED_GB300_PLANE_RANGE[1],
        default_planes=4,
        fabric_class='roce_backend',
        parameter_schema=GB300_PARAMETER_SCHEMA,
        required_device_types=GB300_REQUIRED_DEVICE_TYPES,
        cable_profiles=GB300_CABLE_PROFILE_DEFINITIONS,
        cable_profile_assignments=GB300_CABLE_PROFILE_ASSIGNMENTS,
        status='active',
    )


def validate_roce_4plane_shuffle_architecture_fixture() -> ArchitectureSchemaValidationResult:
    return validate_architecture_schema(build_roce_4plane_shuffle_architecture_schema())


def build_roce_8plane_gb300_shuffle_architecture_schema() -> ArchitectureSchemaDefinition:
    return ArchitectureSchemaDefinition(
        slug=GB300_8PLANE_ARCHITECTURE_SLUG,
        version=ARCHITECTURE_VERSION,
        plane_count=8,
        roles=ROLE_DEFINITIONS,
        transfer_patterns=TRANSFER_PATTERN_DEFINITIONS,
        allocation_rule_sets=_gb300_allocation_rule_definitions(plane_count=8),
        channel_map_matrix=CHANNEL_MAP_MATRIX,
        active_position_groups={
            'A': ACTIVE_POSITION_GROUP_A,
            'B': ACTIVE_POSITION_GROUP_B,
        },
        dark_positions=MPO_DARK_POSITIONS,
        mpo_position_count=MPO_POSITION_COUNT,
        shuffle_mpo_groups=SHUFFLE_MPO_GROUPS,
        shuffle_pair_provider=shuffle_2x2_transfer_position_pairs,
        channels_per_subinterface=4,
        mpo_count_per_osfp=2,
        min_planes=SUPPORTED_GB300_PLANE_RANGE[0],
        max_planes=SUPPORTED_GB300_PLANE_RANGE[1],
        default_planes=8,
        fabric_class='roce_backend',
        parameter_schema={
            **GB300_PARAMETER_SCHEMA,
            'properties': {
                **GB300_PARAMETER_SCHEMA['properties'],
                'plane_count': {
                    **GB300_PARAMETER_SCHEMA['properties']['plane_count'],
                    'default': 8,
                },
            },
        },
        required_device_types=GB300_REQUIRED_DEVICE_TYPES,
        cable_profiles=GB300_CABLE_PROFILE_DEFINITIONS,
        cable_profile_assignments=GB300_CABLE_PROFILE_ASSIGNMENTS,
        status='active',
    )


def validate_roce_8plane_gb300_shuffle_architecture_fixture() -> ArchitectureSchemaValidationResult:
    return validate_architecture_schema(build_roce_8plane_gb300_shuffle_architecture_schema())


def build_roce_4plane_h100_direct_attach_architecture_schema() -> ArchitectureSchemaDefinition:
    return ArchitectureSchemaDefinition(
        slug=H100_DIRECT_ATTACH_ARCHITECTURE_SLUG,
        version=ARCHITECTURE_VERSION,
        plane_count=4,
        roles=H100_ROLE_DEFINITIONS,
        transfer_patterns=H100_TRANSFER_PATTERN_DEFINITIONS,
        allocation_rule_sets=H100_ALLOCATION_RULE_DEFINITIONS,
        channel_map_matrix=H100_CHANNEL_MAP_MATRIX,
        active_position_groups={
            'A': H100_ACTIVE_POSITION_GROUP_A,
            'B': H100_ACTIVE_POSITION_GROUP_B,
        },
        dark_positions=H100_MPO_DARK_POSITIONS,
        mpo_position_count=H100_MPO_POSITION_COUNT,
        shuffle_mpo_groups=(),
        channels_per_subinterface=2,
        mpo_count_per_osfp=1,
        min_planes=1,
        max_planes=4,
        default_planes=4,
        fabric_class='roce_backend',
        parameter_schema=H100_PARAMETER_SCHEMA,
        required_device_types=H100_REQUIRED_DEVICE_TYPES,
        status='active',
        transfer_pair_providers={'direct_attach': direct_attach_position_pairs},
    )


def validate_roce_4plane_h100_direct_attach_architecture_fixture() -> ArchitectureSchemaValidationResult:
    return validate_architecture_schema(build_roce_4plane_h100_direct_attach_architecture_schema())


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
                'schema_contract_version': ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
                'fabric_class': 'roce_backend',
                'plane_range': {
                    'min_planes': SUPPORTED_GB300_PLANE_RANGE[0],
                    'max_planes': SUPPORTED_GB300_PLANE_RANGE[1],
                    'default_planes': 4,
                },
                'parameter_schema': GB300_PARAMETER_SCHEMA,
                'required_device_types': GB300_REQUIRED_DEVICE_TYPES,
                'cable_profiles': GB300_CABLE_PROFILE_DEFINITIONS,
                'cable_profile_assignments': GB300_CABLE_PROFILE_ASSIGNMENTS,
                'semantics': {
                    'optical_lane_scope': 'transceiver_local',
                    'fiber_path_scope': 'connector_position_graph',
                    'netbox_cables': 'forbidden_for_modeled_fabric',
                },
                'topology_segments': [
                    {
                        'slug': 'gb300_to_leaf_shuffle',
                        'source_role': 'gpu_osfp',
                        'destination_role': 'leaf_osfp',
                        'shuffle_role': 'shuffle_cassette',
                        'transfer_pattern': 'shuffle_2x2',
                        'cable_profile_assignments': ['gb300-to-leaf-structured-trunk'],
                    },
                    {
                        'slug': 'leaf_to_spine_shuffle',
                        'source_role': 'leaf_osfp',
                        'destination_role': 'spine_osfp',
                        'shuffle_role': 'spine_shuffle_cassette',
                        'transfer_pattern': 'leaf_spine_shuffle_2x2',
                        'cable_profile_assignments': ['leaf-to-spine-structured-trunk'],
                        'cable_path': [
                            'leaf_mpo',
                            'structured_trunk',
                            'spine_shuffle_rear_mpo',
                            'spine_shuffle_front_mpo',
                            'short_spine_jumper',
                            'spine_mpo',
                        ],
                        'assumptions': {
                            'trunks_terminate_directly_on_shuffle_cassettes': True,
                            'intermediate_patch_panels': False,
                        },
                    },
                ],
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
