from __future__ import annotations

from collections import Counter

from django.db import transaction

from dcim.models import (
    DeviceType,
    InterfaceTemplate,
    Manufacturer,
    ModuleType,
)

from netbox_plant_graph.models import (
    StampTemplate,
    TransferPattern,
)
from netbox_plant_graph.services.architecture import (
    CHANNEL_MAP_MATRIX,
    MPO_POSITION_COUNT,
    ensure_roce_4plane_shuffle_architecture,
)
from netbox_plant_graph.services.transceivers import ensure_builtin_transceiver_profiles


SOURCE = 'madison_fiber_component_template_staging_2026_05_19'


def get_manufacturer(slug, name, description=''):
    manufacturer, _ = Manufacturer.objects.update_or_create(
        slug=slug,
        defaults={
            'name': name,
            'description': description,
        },
    )
    return manufacturer


def upsert_interface_template(module_type, name, interface_type, description=''):
    InterfaceTemplate.objects.update_or_create(
        module_type=module_type,
        name=name,
        defaults={
            'type': interface_type,
            'description': description,
        },
    )


def upsert_module_type(manufacturer, definition):
    module_type, _ = ModuleType.objects.update_or_create(
        manufacturer=manufacturer,
        model=definition['model'],
        defaults={
            'part_number': definition['part_number'],
            'description': definition['description'],
            'comments': definition['comments'],
        },
    )
    desired_names = {interface['name'] for interface in definition['interfaces']}
    InterfaceTemplate.objects.filter(module_type=module_type).exclude(name__in=desired_names).delete()
    for interface in definition['interfaces']:
        upsert_interface_template(
            module_type,
            interface['name'],
            interface['type'],
            interface.get('description', ''),
        )
    return module_type


def osfp_4x200_interface_templates():
    return [
        {
            'name': f'{{module}}/{index}',
            'type': 'other',
            'description': (
                '200G OSFP logical channel. NetBox 4.2 has no exact 200G OSFP interface type; '
                'the plugin transceiver profile owns exact 4x200G semantics.'
            ),
        }
        for index in range(1, 5)
    ]


def connector_spec(connector):
    return {
        'label': connector['label'],
        'connector_kind': connector.get('connector_type', 'mpo-12'),
        'position_count': connector.get('position_count', 1),
        'metadata': connector.get('metadata', {}),
    }


def upsert_transfer_pattern(architecture, definition):
    rule = {
        'type': 'breakout',
        'parent_speed_gbps': definition.get('parent_speed_gbps'),
        'child_count': definition['child_count'],
        'child_speed_gbps': definition.get('child_speed_gbps'),
        'mapping_mode': definition.get('mapping_mode', 'sequential'),
        'position_map': definition.get('position_map', {}),
    }
    pattern, _ = TransferPattern.objects.update_or_create(
        architecture=architecture,
        slug=definition['slug'],
        defaults={
            'name': definition['name'],
            'pattern_kind': 'breakout',
            'rule': rule,
            'metadata': {
                'source': SOURCE,
                'legacy_model': 'BreakoutProfile',
                'v2_replacement': 'TransferPattern',
                'description': definition['description'],
                **definition.get('metadata', {}),
            },
        },
    )
    return pattern


def upsert_shuffle_transfer_pattern(architecture):
    rule = {
        'type': 'position_map',
        'bidirectional': True,
        'connector_kind': 'mpo-12',
        'positions_per_connector': MPO_POSITION_COUNT,
        'active_position_matrix': [dict(entry) for entry in CHANNEL_MAP_MATRIX],
        'rear_position_transform': {
            'type': 'key_down_roll',
            'position_count': MPO_POSITION_COUNT,
            'formula': 'dst_position = position_count + 1 - base_dst_position',
        },
        'position_groups': {
            'A': [1, 12, 2, 11],
            'B': [3, 10, 4, 9],
        },
        'active_position_groups': {
            'A': [1, 12, 2, 11],
            'B': [3, 10, 4, 9],
        },
        'groups': [
            {
                'name': 'shuffle-1',
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
            {
                'name': 'shuffle-2',
                'front_mpos': [3, 4],
                'rear_mpos': [3, 4],
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
                    {'front_mpo': 3, 'rear_mpo': 3, 'src_group': 'A', 'dst_group': 'A'},
                    {'front_mpo': 3, 'rear_mpo': 4, 'src_group': 'B', 'dst_group': 'A'},
                    {'front_mpo': 4, 'rear_mpo': 3, 'src_group': 'A', 'dst_group': 'B'},
                    {'front_mpo': 4, 'rear_mpo': 4, 'src_group': 'B', 'dst_group': 'B'},
                ],
            },
        ],
    }
    pattern, _ = TransferPattern.objects.update_or_create(
        architecture=architecture,
        slug='madison-shuffle-cassette-2x2-full-fanout',
        defaults={
            'name': 'Madison 2x2 MPO-12 shuffle cassette channel-group matrix with key-down roll',
            'pattern_kind': 'shuffle_2x2',
            'rule': rule,
            'metadata': {
                'source': SOURCE,
                'v2_replacement_for': 'AssemblyMappingTemplate rows on shuffle-cassette-2x2-mpo',
            },
        },
    )
    return pattern


def upsert_component_stamp_template(architecture, definition):
    device_type_slug = definition.get('device_type_slug')
    device_type_model = None
    if device_type_slug:
        device_type_model = DeviceType.objects.filter(slug=device_type_slug).values_list('model', flat=True).first()

    side_a = [connector_spec(connector) for connector in definition['side_a']]
    side_b = [connector_spec(connector) for connector in definition['side_b']]
    template_spec = {
        'kind': 'fiber_component_template',
        'schema_version': 2,
        'architecture_slug': architecture.slug,
        'architecture_version': architecture.version,
        'component_slug': definition['slug'],
        'component_type': definition.get('assembly_type', 'trunk_bundle'),
        'device_type_slug': device_type_slug,
        'device_type_model': device_type_model,
        'connectors': {
            'A': side_a,
            'B': side_b,
        },
        'transfer_policy': {
            'map_kind': 'identity' if definition.get('identity_mapping') else 'custom',
            'identity_mapping': bool(definition.get('identity_mapping')),
        },
        'v2_instantiation_targets': [
            'CableAssembly',
            'FiberSegment',
            'FiberStrand',
            'StrandTermination',
            'TransferMap',
        ],
        'metadata': definition.get('metadata', {}),
    }
    template, _ = StampTemplate.objects.update_or_create(
        slug=definition['slug'],
        defaults={
            'architecture': architecture,
            'name': definition['name'],
            'description': definition['description'],
            'template': template_spec,
            'metadata': {
                'source': SOURCE,
                'template_family': 'fiber_component',
                'legacy_model': 'AssemblyTemplate',
                'v2_replacement': 'StampTemplate',
                **definition.get('metadata', {}),
            },
        },
    )
    return template


def connector_position_count(connector_type: str) -> int:
    if connector_type.startswith(('mpo-', 'mtp-')):
        try:
            return int(connector_type.split('-', 1)[1])
        except (IndexError, TypeError, ValueError):
            return 1
    return 1


def mpo_connectors(prefix, count, connector_type='mpo-12'):
    return [
        {
            'label': f'{prefix}-{index:02d}',
            'connector_type': connector_type,
            'position_count': connector_position_count(connector_type),
        }
        for index in range(1, count + 1)
    ]


OPTIC_MODULE_TYPES = [
    {
        'model': 'MMS4X00-NS400 OSFP112 400G DR4',
        'part_number': 'MMS4X00-NS400',
        'description': 'MPO OSFP112 400G DR4 optical transceiver.',
        'comments': 'Madison network BoM description: MPO OSFP112-400G-DR4. One MPO DR4 line endpoint.',
        'interfaces': [
            {'name': 'line', 'type': '400gbase-x-osfp', 'description': '400G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS4X00-NM OSFP112 800G 2x400G 2DR4 Twin',
        'part_number': 'MMS4X00-NM',
        'description': 'MPO OSFP112 800G 2x400G 2DR4 twin optical transceiver.',
        'comments': (
            'Base twin optic SKU from Madison BoM. NetBox interface templates expose four operator-visible '
            '200G logical channels; plugin transceiver profiles own the exact MPO/lane map.'
        ),
        'interfaces': osfp_4x200_interface_templates(),
    },
    {
        'model': 'MMS4X00-NM-T OSFP112 800G 2x400G 2DR4 Twin',
        'part_number': 'MMS4X00-NM-T',
        'description': 'MPO OSFP112 800G 2x400G 2DR4 twin optical transceiver, T variant.',
        'comments': 'Variant retained as a distinct module type until SKU suffix semantics are confirmed.',
        'interfaces': osfp_4x200_interface_templates(),
    },
    {
        'model': 'MMS4X00-NM-FLT OSFP112 800G 2x400G 2DR4 Twin',
        'part_number': 'MMS4X00-NM-FLT',
        'description': 'MPO OSFP112 800G 2x400G 2DR4 twin optical transceiver, FLT/RHS variant.',
        'comments': 'Variant retained as a distinct module type until physical side/orientation semantics are confirmed.',
        'interfaces': osfp_4x200_interface_templates(),
    },
    {
        'model': 'MMS4A20-XM800 OSFP224 800G DR4',
        'part_number': 'MMS4A20-XM800',
        'description': 'MPO OSFP224 800G DR4 single optical transceiver.',
        'comments': 'Madison network BoM description: MPO OSFP224-800G-DR4 Single. One MPO DR4 line endpoint.',
        'interfaces': osfp_4x200_interface_templates(),
    },
    {
        'model': 'MMS4A00-XM OSFP224 1600G 2x800G 2DR4 Twin',
        'part_number': 'MMS4A00-XM',
        'description': 'MPO OSFP224 1600G 2x800G 2DR4 twin optical transceiver.',
        'comments': 'NetBox 4.2 has no native 1600G OSFP or 200G OSFP interface type; plugin profiles own exact operating modes.',
        'interfaces': osfp_4x200_interface_templates(),
    },
    {
        'model': 'MMS1X00-NS400 QSFP112 400G DR4',
        'part_number': 'MMS1X00-NS400',
        'description': 'MPO QSFP112 400G DR4 optical transceiver.',
        'comments': 'Madison network BoM description: MPO QSFP112-400G-DR4.',
        'interfaces': [
            {'name': 'line', 'type': '400gbase-x-qsfp112', 'description': '400G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS1V00-WM QSFP56DD 400G DR4',
        'part_number': 'MMS1V00-WM',
        'description': 'MPO QSFP56DD 400G DR4 optical transceiver.',
        'comments': 'Modeled with NetBox QSFP-DD 400G type; verify QSFP56-DD naming once vendor datasheet is attached.',
        'interfaces': [
            {'name': 'line', 'type': '400gbase-x-qsfpdd', 'description': '400G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS1V70-CM QSFP28 100G DR1',
        'part_number': 'MMS1V70-CM',
        'description': 'LC QSFP28 100G DR1 optical transceiver.',
        'comments': 'Madison network BoM description: LC QSFP28-100G-DR1. LC duplex line endpoint.',
        'interfaces': [
            {'name': 'line', 'type': '100gbase-x-qsfp28', 'description': '100G DR1 LC line side.'},
        ],
    },
]


BREAKOUT_PROFILES = [
    {
        'slug': 'dr4-400g-4x100g',
        'name': 'DR4 400G to 4x100G',
        'description': 'Informational DR4 profile: one 400G parent maps to four 100G lanes/groups.',
        'parent_speed_gbps': 400,
        'child_count': 4,
        'child_speed_gbps': 100,
    },
    {
        'slug': 'dr4-800g-4x200g',
        'name': 'DR4 800G to 4x200G',
        'description': 'Informational DR4 profile: one 800G parent maps to four 200G lanes/groups.',
        'parent_speed_gbps': 800,
        'child_count': 4,
        'child_speed_gbps': 200,
    },
    {
        'slug': '2dr4-800g-2x400g',
        'name': '2DR4 800G to 2x400G',
        'description': 'Twin 800G optic or cable profile exposing two 400G DR4 endpoints.',
        'parent_speed_gbps': 800,
        'child_count': 2,
        'child_speed_gbps': 400,
    },
    {
        'slug': '2dr4-1600g-2x800g',
        'name': '2DR4 1600G to 2x800G',
        'description': 'Twin 1600G optic profile exposing two 800G DR4 endpoints.',
        'parent_speed_gbps': 1600,
        'child_count': 2,
        'child_speed_gbps': 800,
    },
]


ASSEMBLY_TEMPLATES = [
    {
        'slug': 'shuffle-cassette-2x2-mpo',
        'name': '2x2 MPO Shuffle Cassette',
        'description': 'Passive 2x2 MPO-12 shuffle cassette with two independent channel-group shuffle matrices and rear-side key-down position roll.',
        'assembly_type': 'shuffle_board',
        'device_type_slug': 'shuffle-cassette-2x2-mpo',
        'side_a': mpo_connectors('rear-mpo', 4),
        'side_b': mpo_connectors('front-mpo', 4),
        'identity_mapping': False,
        'metadata': {
            'topology_authority': 'Reusable Madison shuffle cassette transfer policy',
            'transfer_policy': {
                'policy_type': 'two_independent_2x2_channel_group_matrix',
                'positions_per_connector': MPO_POSITION_COUNT,
                'active_position_matrix': [dict(entry) for entry in CHANNEL_MAP_MATRIX],
                'rear_position_transform': {
                    'type': 'key_down_roll',
                    'position_count': MPO_POSITION_COUNT,
                    'formula': 'dst_position = position_count + 1 - base_dst_position',
                },
                'groups': [
                    {
                        'name': 'shuffle-1',
                        'front_mpos': [1, 2],
                        'rear_mpos': [1, 2],
                        'transfer_maps': [
                            {'front_mpo': 1, 'rear_mpo': 1, 'src_group': 'A', 'dst_group': 'A'},
                            {'front_mpo': 1, 'rear_mpo': 2, 'src_group': 'B', 'dst_group': 'A'},
                            {'front_mpo': 2, 'rear_mpo': 1, 'src_group': 'A', 'dst_group': 'B'},
                            {'front_mpo': 2, 'rear_mpo': 2, 'src_group': 'B', 'dst_group': 'B'},
                        ],
                    },
                    {
                        'name': 'shuffle-2',
                        'front_mpos': [3, 4],
                        'rear_mpos': [3, 4],
                        'transfer_maps': [
                            {'front_mpo': 3, 'rear_mpo': 3, 'src_group': 'A', 'dst_group': 'A'},
                            {'front_mpo': 3, 'rear_mpo': 4, 'src_group': 'B', 'dst_group': 'A'},
                            {'front_mpo': 4, 'rear_mpo': 3, 'src_group': 'A', 'dst_group': 'B'},
                            {'front_mpo': 4, 'rear_mpo': 4, 'src_group': 'B', 'dst_group': 'B'},
                        ],
                    },
                ],
            },
        },
    },
    {
        'slug': 'madison-mpo8-smf-patch',
        'name': 'Madison MPO8 SMF Patch',
        'description': 'Singlemode MPO8 point-to-point patch cable template.',
        'side_a': mpo_connectors('A-mpo8', 1),
        'side_b': mpo_connectors('B-mpo8', 1),
        'identity_mapping': True,
        'metadata': {'netbox_cable_type': 'smf', 'fiber_mode': 'singlemode', 'connector': 'MPO8'},
    },
    {
        'slug': 'madison-96f-mpo8-smf-trunk',
        'name': 'Madison 96f MPO8 SMF Trunk',
        'description': '96-fiber singlemode MPO8 trunk; 12 MPO8 groups.',
        'side_a': mpo_connectors('A-mpo8', 12),
        'side_b': mpo_connectors('B-mpo8', 12),
        'identity_mapping': True,
        'metadata': {'fiber_count': 96, 'mpo8_groups': 12, 'active_groups_from_bom': 10, 'spare_groups_from_bom': 2},
    },
    {
        'slug': 'madison-72f-mpo8-smf-trunk',
        'name': 'Madison 72f MPO8 SMF Trunk',
        'description': '72-fiber singlemode MPO8 trunk; 9 MPO8 groups.',
        'side_a': mpo_connectors('A-mpo8', 9),
        'side_b': mpo_connectors('B-mpo8', 9),
        'identity_mapping': True,
        'metadata': {'fiber_count': 72, 'mpo8_groups': 9, 'active_groups_from_bom': 8, 'spare_groups_from_bom': 1},
    },
    {
        'slug': 'madison-64f-mpo8-smf-trunk',
        'name': 'Madison 64f MPO8 SMF Trunk',
        'description': '64-fiber singlemode MPO8 trunk; 8 MPO8 groups.',
        'side_a': mpo_connectors('A-mpo8', 8),
        'side_b': mpo_connectors('B-mpo8', 8),
        'identity_mapping': True,
        'metadata': {'fiber_count': 64, 'mpo8_groups': 8, 'active_groups_from_bom': 8, 'spare_groups_from_bom': 0},
    },
    {
        'slug': 'madison-48f-mpo8-smf-trunk',
        'name': 'Madison 48f MPO8 SMF Trunk',
        'description': '48-fiber singlemode MPO8 trunk; 6 MPO8 groups.',
        'side_a': mpo_connectors('A-mpo8', 6),
        'side_b': mpo_connectors('B-mpo8', 6),
        'identity_mapping': True,
        'metadata': {'fiber_count': 48, 'mpo8_groups': 6},
    },
    {
        'slug': 'madison-mpo12-apc-smf',
        'name': 'Madison MPO12/APC SMF Cable',
        'description': 'Singlemode MPO12/APC point-to-point cable template.',
        'side_a': mpo_connectors('A-mpo12', 1, connector_type='mpo-12'),
        'side_b': mpo_connectors('B-mpo12', 1, connector_type='mpo-12'),
        'identity_mapping': True,
        'metadata': {'netbox_cable_type': 'smf', 'connector': 'MPO12/APC'},
    },
    {
        'slug': 'madison-mpo12-apc-to-2x-mpo12-apc',
        'name': 'Madison MPO12/APC to 2xMPO12/APC Fanout',
        'description': 'Singlemode MPO12/APC to dual MPO12/APC fanout shell.',
        'side_a': mpo_connectors('A-mpo12', 1, connector_type='mpo-12'),
        'side_b': mpo_connectors('B-mpo12', 2, connector_type='mpo-12'),
        'identity_mapping': False,
        'metadata': {'mapping_status': 'unresolved', 'connector': 'MPO12/APC fanout'},
    },
    {
        'slug': 'madison-mpo12-apc-to-4x-lc',
        'name': 'Madison MPO12/APC to 4xLC Fanout',
        'description': 'Singlemode MPO12/APC to four LC duplex fanout shell.',
        'side_a': mpo_connectors('A-mpo12', 1, connector_type='mpo-12'),
        'side_b': mpo_connectors('B-lc', 4, connector_type='lc-duplex'),
        'identity_mapping': False,
        'metadata': {'mapping_status': 'unresolved', 'connector': 'MPO12/APC to LC duplex fanout'},
    },
    {
        'slug': 'madison-osfp112-800g-dac',
        'name': 'Madison OSFP112 800G DAC',
        'description': 'OSFP112-to-OSFP112 800G DAC assembly shell for MCP4Y10-N002/N003.',
        'side_a': [{'label': 'A-osfp112', 'connector_type': 'custom', 'position_count': 1}],
        'side_b': [{'label': 'B-osfp112', 'connector_type': 'custom', 'position_count': 1}],
        'identity_mapping': True,
        'metadata': {'netbox_cable_type': 'dac-passive', 'part_numbers': ['MCP4Y10-N002', 'MCP4Y10-N003']},
    },
]


@transaction.atomic
def main():
    counters = Counter()
    fixture = ensure_roce_4plane_shuffle_architecture()
    architecture = fixture.architecture
    nvidia = get_manufacturer('nvidia', 'Nvidia', 'NVIDIA/Mellanox optical and network components.')
    get_manufacturer('nscale', 'Nscale Internal', 'Nscale-owned passive fiber plant definitions.')

    for definition in OPTIC_MODULE_TYPES:
        upsert_module_type(nvidia, definition)
        counters['module_types'] += 1

    counters.update(ensure_builtin_transceiver_profiles(architecture=architecture))

    for definition in BREAKOUT_PROFILES:
        upsert_transfer_pattern(architecture, definition)
        counters['transfer_patterns'] += 1

    upsert_shuffle_transfer_pattern(architecture)
    counters['transfer_patterns'] += 1

    for definition in ASSEMBLY_TEMPLATES:
        upsert_component_stamp_template(architecture, definition)
        counters['stamp_templates'] += 1

    print('Seeded Madison fiber component templates for netbox_plant_graph v2:')
    for key, value in sorted(counters.items()):
        print(f'  {key}: {value}')


main()
