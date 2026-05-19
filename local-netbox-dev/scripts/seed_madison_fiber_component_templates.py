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
    AssemblyConnectorTemplate,
    AssemblyMappingTemplate,
    AssemblyTemplate,
    BreakoutProfile,
)


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


def upsert_breakout_profile(definition):
    return BreakoutProfile.objects.update_or_create(
        slug=definition['slug'],
        defaults={
            'name': definition['name'],
            'description': definition['description'],
            'parent_speed_gbps': definition.get('parent_speed_gbps'),
            'child_count': definition['child_count'],
            'child_speed_gbps': definition.get('child_speed_gbps'),
            'mapping_mode': definition.get('mapping_mode', 'sequential'),
            'position_map': definition.get('position_map', {}),
            'metadata': {
                'source': SOURCE,
                **definition.get('metadata', {}),
            },
        },
    )[0]


def upsert_assembly_template(definition):
    device_type = None
    if definition.get('device_type_slug'):
        device_type = DeviceType.objects.get(slug=definition['device_type_slug'])
    template, _ = AssemblyTemplate.objects.update_or_create(
        slug=definition['slug'],
        defaults={
            'name': definition['name'],
            'description': definition['description'],
            'assembly_type': definition.get('assembly_type', 'trunk_bundle'),
            'cable_profile_hint': definition.get('cable_profile_hint', ''),
            'device_type': device_type,
            'metadata': {
                'source': SOURCE,
                **definition.get('metadata', {}),
            },
        },
    )
    template.connectors.all().delete()

    side_a = []
    side_b = []
    for side, connectors, bucket in (
        ('A', definition['side_a'], side_a),
        ('B', definition['side_b'], side_b),
    ):
        for number, connector in enumerate(connectors, start=1):
            bucket.append(AssemblyConnectorTemplate.objects.create(
                template=template,
                side=side,
                connector_number=number,
                connector_type=connector.get('connector_type', 'mpo-8'),
                position_count=connector.get('position_count', 1),
                label=connector['label'],
                metadata=connector.get('metadata', {}),
            ))

    if definition.get('identity_mapping'):
        for a_connector, b_connector in zip(side_a, side_b):
            max_positions = min(a_connector.position_count, b_connector.position_count)
            for position in range(1, max_positions + 1):
                AssemblyMappingTemplate.objects.create(
                    template=template,
                    a_connector=a_connector,
                    a_position=position,
                    b_connector=b_connector,
                    b_position=position,
                    mapping_type='identity',
                    metadata={'source': SOURCE},
                )
    return template


def mpo_connectors(prefix, count, connector_type='mpo-8'):
    return [
        {
            'label': f'{prefix}-{index:02d}',
            'connector_type': connector_type,
            'position_count': 1,
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
        'comments': 'Base twin optic SKU from Madison BoM. Model exposes two 400G DR4 MPO line endpoints.',
        'interfaces': [
            {'name': 'line-1', 'type': '400gbase-x-osfp', 'description': 'First 400G DR4 MPO line side.'},
            {'name': 'line-2', 'type': '400gbase-x-osfp', 'description': 'Second 400G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS4X00-NM-T OSFP112 800G 2x400G 2DR4 Twin',
        'part_number': 'MMS4X00-NM-T',
        'description': 'MPO OSFP112 800G 2x400G 2DR4 twin optical transceiver, T variant.',
        'comments': 'Variant retained as a distinct module type until SKU suffix semantics are confirmed.',
        'interfaces': [
            {'name': 'line-1', 'type': '400gbase-x-osfp', 'description': 'First 400G DR4 MPO line side.'},
            {'name': 'line-2', 'type': '400gbase-x-osfp', 'description': 'Second 400G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS4X00-NM-FLT OSFP112 800G 2x400G 2DR4 Twin',
        'part_number': 'MMS4X00-NM-FLT',
        'description': 'MPO OSFP112 800G 2x400G 2DR4 twin optical transceiver, FLT/RHS variant.',
        'comments': 'Variant retained as a distinct module type until physical side/orientation semantics are confirmed.',
        'interfaces': [
            {'name': 'line-1', 'type': '400gbase-x-osfp', 'description': 'First 400G DR4 MPO line side.'},
            {'name': 'line-2', 'type': '400gbase-x-osfp', 'description': 'Second 400G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS4A20-XM800 OSFP224 800G DR4',
        'part_number': 'MMS4A20-XM800',
        'description': 'MPO OSFP224 800G DR4 single optical transceiver.',
        'comments': 'Madison network BoM description: MPO OSFP224-800G-DR4 Single. One MPO DR4 line endpoint.',
        'interfaces': [
            {'name': 'line', 'type': '800gbase-x-osfp', 'description': '800G DR4 MPO line side.'},
        ],
    },
    {
        'model': 'MMS4A00-XM OSFP224 1600G 2x800G 2DR4 Twin',
        'part_number': 'MMS4A00-XM',
        'description': 'MPO OSFP224 1600G 2x800G 2DR4 twin optical transceiver.',
        'comments': 'NetBox 4.2 has no native 1600G OSFP interface type, so this module exposes two 800G OSFP line endpoints.',
        'interfaces': [
            {'name': 'line-1', 'type': '800gbase-x-osfp', 'description': 'First 800G DR4 MPO line side.'},
            {'name': 'line-2', 'type': '800gbase-x-osfp', 'description': 'Second 800G DR4 MPO line side.'},
        ],
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
        'description': 'Passive 2x2 MPO shuffle cassette with two independent full-fanout shuffle groups.',
        'assembly_type': 'shuffle_board',
        'device_type_slug': 'shuffle-cassette-2x2-mpo',
        'side_a': mpo_connectors('rear-mpo', 4),
        'side_b': mpo_connectors('front-mpo', 4),
        'identity_mapping': False,
        'metadata': {
            'topology_authority': 'Reusable Madison shuffle cassette transfer policy',
            'transfer_policy': {
                'policy_type': 'two_independent_2x2_full_fanout',
                'groups': [
                    {
                        'name': 'shuffle-1',
                        'front_mpos': [1, 2],
                        'rear_mpos': [1, 2],
                        'transfer_maps': [
                            {'front_mpo': 1, 'rear_mpo': 1},
                            {'front_mpo': 1, 'rear_mpo': 2},
                            {'front_mpo': 2, 'rear_mpo': 1},
                            {'front_mpo': 2, 'rear_mpo': 2},
                        ],
                    },
                    {
                        'name': 'shuffle-2',
                        'front_mpos': [3, 4],
                        'rear_mpos': [3, 4],
                        'transfer_maps': [
                            {'front_mpo': 3, 'rear_mpo': 3},
                            {'front_mpo': 3, 'rear_mpo': 4},
                            {'front_mpo': 4, 'rear_mpo': 3},
                            {'front_mpo': 4, 'rear_mpo': 4},
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
    nvidia = get_manufacturer('nvidia', 'Nvidia', 'NVIDIA/Mellanox optical and network components.')
    get_manufacturer('nscale', 'Nscale Internal', 'Nscale-owned passive fiber plant definitions.')

    for definition in OPTIC_MODULE_TYPES:
        upsert_module_type(nvidia, definition)
        counters['module_types'] += 1

    for definition in BREAKOUT_PROFILES:
        upsert_breakout_profile(definition)
        counters['breakout_profiles'] += 1

    for definition in ASSEMBLY_TEMPLATES:
        upsert_assembly_template(definition)
        counters['assembly_templates'] += 1

    print('Seeded Madison fiber component templates:')
    for key, value in sorted(counters.items()):
        print(f'  {key}: {value}')


main()
