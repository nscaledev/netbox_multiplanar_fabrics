from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from django.db import transaction

from dcim.models import (
    ConsolePortTemplate,
    ConsoleServerPortTemplate,
    Device,
    DeviceBayTemplate,
    DeviceRole,
    DeviceType,
    FrontPortTemplate,
    InterfaceTemplate,
    Manufacturer,
    ModuleBayTemplate,
    PowerPortTemplate,
    RackRole,
    RearPortTemplate,
)


SOURCE_MARKER_BEGIN = '<!-- madison-device-definition-staging:start -->'
SOURCE_MARKER_END = '<!-- madison-device-definition-staging:end -->'

SOURCE_SUMMARY = """\
Madison staged definition source:
- META US NC Data Hall Layout 18k GB300 v2.0_5.01.2026, Google Drive file 1Fhp2cvSCivnXlCEFrxmA9ZJ7sJBxk5DVVOqV2_bzkkE, modified 2026-05-22.
- ROCE 4-plane Shuffle Cabling Patterns, Notion page 35ecaf6bfadc80c1a77ac54a8e8de19f, last edited 2026-05-15.
- Nscale NC 18k Fiber BOM release 1.2 workbook, Google Drive file 1W_5uZuxB7r9aYZ2fOfbDZeOa6kbZtofN, modified 2026-04-22.

This local-dev seed intentionally creates definition/catalog objects only. It does not create rack-mounted Device instances.
"""

MANUFACTURERS = {
    'dell': ('Dell', 'Dell equipment referenced by the Madison GB300/NVL72 source material.'),
    'nvidia': ('Nvidia', 'NVIDIA/Nvidia equipment referenced by the Madison GB300 and RoCE source material.'),
    'nokia': ('Nokia', 'Nokia routing and breakout equipment referenced in Madison MMR/edge racks.'),
    'arista': ('Arista', 'Arista edge switch equipment referenced in Madison MMR racks.'),
    'palo': ('Palo Alto Networks', 'Palo Alto firewall equipment referenced in Madison MMR racks.'),
    'opengear': ('Opengear', 'Opengear console equipment referenced in Madison control and MMR racks.'),
    'apc': ('APC', 'APC rack/PDU equipment referenced in Madison rack-elevation worksheets.'),
    'nscale': ('Nscale', 'Nscale-owned passive plant and generic Madison definitions.'),
    'generic': ('Generic', 'Generic placeholders for Madison definitions whose final OEM/SKU is not yet known.'),
}

ROLES = [
    ('gb300ct', 'PowerEdge XE9712 GB300 Compute Tray', '00bcd4', 'GB300 GPU tray endpoint; workbook pattern sys1-gso1-p-phy-gpu#-su# with 126 trays per SU.'),
    ('gb300ps', 'PS33 33kW Power Shelf', 'ffc107', '1RU GB300 power shelf; workbook pattern sys1-gso1-p-phy-pwt(1-56)-su#.'),
    ('gb300st', 'GB300 NVL72 NVLink Switch Tray', '2196f3', 'GB300 NVL72 NVLink switch tray; workbook pattern sys1-gso1-p-phy-nvs(1-63)-su#.'),
    ('nvl72-appliance', 'NVL72 Rack-Scale Appliance', '00acc1', 'Parent device for one 48RU GB300/NVL72 rack-scale appliance; child trays/shelves/switches install into NetBox DeviceBays.'),
    ('oob-leaf', 'OOB Leaf', '9c27b0', 'Out-of-band leaf switch; includes GPU, row, storage, and management-row OOB leaves.'),
    ('oob-spine', 'OOB Spine', '7b1fa2', 'Out-of-band spine switch; workbook patterns obs1/obs2.'),
    ('oob-core', 'OOB Core', '6a1b9a', 'Out-of-band core switch; workbook pattern obc.'),
    ('oob-edge', 'OOB Edge', '8e24aa', 'Out-of-band edge device; workbook pattern obe.'),
    ('oob-border-leaf', 'OOB Border Leaf', 'ab47bc', 'Out-of-band border leaf; workbook pattern obb.'),
    ('console-server', 'Console Server', '607d8b', 'Console server or console aggregation appliance; workbook patterns con and cos.'),
    ('leak-detection', 'Leak Detection', '795548', 'Leak detection monitoring device shown in Madison row-elevation worksheets.'),
    ('be-leaf-switch', 'BE Leaf Switch', '1e88e5', 'Back-end/East-West leaf switch; workbook patterns bel*-su#-pl#-nc*.'),
    ('be-spine-switch', 'BE Spine Switch', '1565c0', 'Back-end/East-West spine switch; workbook patterns bes*-pl#-er#.'),
    ('fe-leaf-switch', 'FE Leaf Switch', '43a047', 'Front-end/North-South leaf switch; workbook pattern fel.'),
    ('fe-spine-switch', 'FE Spine Switch', '2e7d32', 'Front-end/North-South spine switch; workbook pattern fes.'),
    ('fe-border-leaf-switch', 'FE Border Leaf Switch', '00897b', 'Front-end border leaf; workbook pattern feb.'),
    ('fe-core-switch', 'FE Core Switch', '00695c', 'Front-end super-spine/core switch; workbook pattern fec and note changing Core to FEC.'),
    ('edge-switch', 'Edge Switch', '795548', 'Customer/Nscale edge switch; workbook patterns esw and edge internet router labels.'),
    ('nscale-firewall', 'Nscale Firewall', 'd32f2f', 'Nscale firewall equipment in MMR/control racks; workbook pattern nfw/efw.'),
    ('management-server', 'Management Server', '00acc1', 'Management server in Madison MMR/control racks.'),
    ('passive-breakout', 'Passive Breakout', '90a4ae', 'Passive breakout or demarc assembly in Madison MMR/edge racks.'),
    ('control-spine', 'Control Spine', '5e35b1', 'Control network spine switch in central control racks.'),
    ('control-leaf', 'Control Leaf', '512da8', 'Control network leaf switch in central control racks.'),
    ('openstack-controller', 'OpenStack Controller', '0288d1', 'OpenStack control node; workbook pattern osc.'),
    ('control-node', 'Control Node', '039be5', 'Tenant/platform control node; workbook pattern csc and CPU Control Node rows.'),
    ('ceph-storage-node', 'Ceph Storage Node', 'ef6c00', 'Ceph storage node; workbook pattern cpo.'),
    ('meta-storage-node', 'Meta Storage Node', 'f57c00', 'Meta storage server; workbook pattern mst.'),
    ('data-storage-node', 'Data Storage Node', 'fb8c00', 'Data storage server; workbook pattern dst.'),
    ('nmx-server', 'NMX Server', '00acc1', 'NMX server; workbook pattern nmx.'),
    ('storage-spine', 'Storage Spine', '3949ab', 'Storage spine switch in central storage racks.'),
    ('storage-leaf', 'Storage Leaf', '303f9f', 'Storage leaf switch in central storage racks.'),
    ('vast-control-node', 'Vast Control Node', 'ff9800', 'VAST CBox/control role staged from workbook device summary.'),
    ('vast-data-node', 'Vast Data Node', 'fb8c00', 'VAST DBox/data role staged from workbook device summary.'),
    ('vast-spine-switch', 'VAST Spine Switch', '283593', 'VAST storage spine switch staged from workbook device summary.'),
    ('vast-leaf-switch', 'VAST Leaf Switch', '3f51b5', 'VAST storage leaf switch staged from workbook device summary.'),
    ('ufm-server', 'UFM Server', '00bfa5', 'UFM server staged from workbook device summary.'),
    ('pdu', 'PDU', 'ffb300', 'Rack PDU; workbook indicates A/B PDU pairs with 415V 60A 560P6 feeds.'),
    ('fiber-panel', 'Fiber Panel', '90a4ae', 'Passive fiber panel for Madison fiber plant.'),
    ('sb', 'Shuffle Box', '4dd0e1', 'Passive RoCE shuffle box containing trays/cassettes.'),
    ('shuffle-cassette', 'Shuffle Cassette', '26c6da', '2x2 RoCE shuffle cassette; internal transfer map belongs in plant graph.'),
]

OBSOLETE_DEVICE_TYPE_SLUGS = (
    'shuffle-tray-6cassette',
)

OBSOLETE_ROLE_SLUGS = (
    'shuffle-tray',
)

RACK_ROLES = [
    (
        'nvl72_poweredgexe9712',
        'NVL72_PowerEdgeXE9712',
        '00acc1',
        'NVL72/GB300 rack role for Madison GPU racks; workbook composition includes 18 GB300 GPU trays, 8 power shelves, 9 NVLink switches, and 2 SN2201 OOB TORs per rack.',
    ),
    (
        'madison-t1-ew-plane-1-2',
        'GS001 T1 E-W Plane 1-2',
        '1e88e5',
        'Madison T1 East/West BE leaf rack layout for planes 1 and 2; workbook BE Leaf Rack 1 patterns bel*-su#-pl1/pl2-nc*a.',
    ),
    (
        'madison-t1-ew-plane-3-4',
        'GS001 T1 E-W Plane 3-4',
        '42a5f5',
        'Madison T1 East/West BE leaf rack layout for planes 3 and 4; workbook BE Leaf Rack 2 patterns bel*-su#-pl3/pl4-nc*b.',
    ),
    (
        'madison-t1-t2-ns',
        'GS001 T1/T2 N-S',
        '43a047',
        'Madison North/South FE leaf/spine rack layout; workbook headers identify paired T1/T2 N-S racks with fes/fel and row OOB/console equipment.',
    ),
    (
        'madison-t2-ew',
        'GS001 T2 E-W',
        '1565c0',
        'Madison East/West spine/shuffle rack layout; workbook T2 E-W elevations include E-W spine switches, fiber panels, and 2x2 shuffle assemblies.',
    ),
    (
        'madison-t3-ns',
        'GS001 T3 N-S',
        '2e7d32',
        'Madison T3 North/South spine/core rack layout; workbook T3 N-S elevations include N-S spine/FEC group equipment.',
    ),
    (
        'madison-be-spine-plane-1',
        'GS001 BE Spine Plane 1',
        '0d47a1',
        'Madison BE spine rack block for plane 1; workbook maps full/half racks across ER10/J and ER9/I positions.',
    ),
    (
        'madison-be-spine-plane-2',
        'GS001 BE Spine Plane 2',
        '1976d2',
        'Madison BE spine rack block for plane 2; workbook maps full/half racks across ER12/L and ER11/K positions.',
    ),
    (
        'madison-be-spine-plane-3',
        'GS001 BE Spine Plane 3',
        '5e35b1',
        'Madison BE spine rack block for plane 3; workbook maps full/half racks across ER13/M and ER14/N positions.',
    ),
    (
        'madison-be-spine-plane-4',
        'GS001 BE Spine Plane 4',
        '7e57c2',
        'Madison BE spine rack block for plane 4; workbook maps full/half racks across ER15/O and ER16/P positions.',
    ),
    (
        'madison-fe-core-edge',
        'GS001 FE Core + Edge',
        '00897b',
        'Madison front-end core/super-spine plus edge rack layout; workbook Fe-Core + Edge section includes esw, feb, and fec group patterns.',
    ),
    (
        'madison-control',
        'GS001 Control',
        '00acc1',
        'Madison central control rack layout; workbook central control racks include OOB, console, control spine/leaf, NMX, OpenStack, Ceph, and control nodes.',
    ),
    (
        'madison-t1-t2-storage',
        'GS001 T1/T2 Storage',
        'fb8c00',
        'Madison central storage rack layout; workbook T1/T2 storage racks include storage spines, storage leaves, Meta Storage, and Data Storage nodes.',
    ),
    (
        'madison-nscale-edge-mmr',
        'GS001 Nscale Edge / MMR',
        '795548',
        'Madison Nscale edge/MMR rack layout; workbook section includes Nokia, Palo Alto, Opengear, edge switch, and MMR-facing fiber panel equipment.',
    ),
]


def osfp_interfaces(count, *, prefix='osfp', description='800G OSFP cage'):
    return [
        {
            'name': f'{prefix}{index}',
            'type': '800gbase-x-osfp',
            'description': description,
        }
        for index in range(1, count + 1)
    ]


def osfp_module_bays(count, *, prefix='osfp', description='OSFP transceiver cage'):
    return [
        {
            'name': f'{prefix}{index}',
            'label': f'{prefix.upper()} {index}',
            'position': str(index),
            'description': description,
        }
        for index in range(1, count + 1)
    ]


def qsfpdd_interfaces(count, *, prefix='swp', description='400G QSFP-DD cage'):
    return [
        {
            'name': f'{prefix}{index}',
            'type': '400gbase-x-qsfpdd',
            'description': description,
        }
        for index in range(1, count + 1)
    ]


def sfp28_interfaces(count, *, start=1, prefix='swp', description='1/10/25G SFP28 cage'):
    return [
        {
            'name': f'{prefix}{index}',
            'type': '25gbase-x-sfp28',
            'description': description,
        }
        for index in range(start, start + count)
    ]


def basic_mgmt_interfaces():
    return [
        {'name': 'mgmt0', 'type': '1000base-t', 'mgmt_only': True, 'description': 'Out-of-band management interface.'},
    ]


def eth0_mgmt_interface():
    return [
        {'name': 'eth0', 'type': '1000base-t', 'mgmt_only': True, 'description': 'Out-of-band management interface.'},
    ]


def basic_console_port():
    return [{'name': 'console', 'type': 'rj-45', 'description': 'Console port.'}]


def redundant_power_ports(maximum_draw=None):
    draw = None if maximum_draw is None else int(maximum_draw / 2)
    return [
        {'name': 'PSU1', 'type': 'iec-60320-c14', 'maximum_draw': draw, 'description': 'A-side power input.'},
        {'name': 'PSU2', 'type': 'iec-60320-c14', 'maximum_draw': draw, 'description': 'B-side power input.'},
    ]


def numbered_power_ports(count, port_type, *, maximum_draw=None):
    draw = None if maximum_draw is None else int(maximum_draw / count)
    return [
        {
            'name': f'PSU{index}',
            'type': port_type,
            'maximum_draw': draw,
            'description': f'AC power input PSU{index}.',
        }
        for index in range(1, count + 1)
    ]


def single_power_port(name, port_type, description, maximum_draw=None):
    return [
        {
            'name': name,
            'type': port_type,
            'maximum_draw': maximum_draw,
            'description': description,
        },
    ]


def shuffle_box_cassette_bays():
    return [
        {
            'name': f'cassette-{tray}.{slot}',
            'label': f'Cassette {tray}.{slot}',
            'description': f'Shuffle cassette position: tray {tray}, cassette slot {slot}.',
        }
        for tray in range(1, 4)
        for slot in range(1, 7)
    ]


def nvl72_rackscale_device_bays():
    bays = []
    for index, label in enumerate(('BMC-01', 'MGMT-01'), start=1):
        bays.append(
            {
                'name': label,
                'label': label,
                'description': f'FRSD NVL72 rackscale appliance management switch bay {index}.',
            }
        )
    for index in range(1, 9):
        label = f'PWR-SHLF-{index:02d}'
        bays.append(
            {
                'name': label,
                'label': label,
                'description': f'FRSD NVL72 rackscale appliance PS33 power shelf bay {index}.',
            }
        )
    for index in range(1, 19):
        label = f'GPU-NODE-{index:02d}'
        bays.append(
            {
                'name': label,
                'label': label,
                'description': f'FRSD NVL72 rackscale appliance XE9712 GB300 GPU node bay {index}.',
            }
        )
    for index in range(1, 10):
        label = f'NVL-SW-{index:02d}'
        bays.append(
            {
                'name': label,
                'label': label,
                'description': f'FRSD NVL72 rackscale appliance NVLink switch tray bay {index}.',
            }
        )
    return bays


DEVICE_TYPES = [
    {
        'slug': 'nvl72-rackscale-appliance',
        'manufacturer': 'nvidia',
        'model': 'GB300 NVL72 Rack-Scale Appliance',
        'u_height': Decimal('48.0'),
        'description': 'Parent inventory container for one 48RU GB300/NVL72 rack-scale appliance.',
        'comments': """\
FRSD authority: one 48RU MGX rack-scale appliance contains 18 XE9712 GB300 GPU/server nodes, 9 NVL72 NVLink switch trays, 8 PS33_1L60 power shelves, and 2 SN2201 management/BMC switches.
Madison staging uses this device type as the NetBox-native parent container. The rack itself remains the site/space object; this parent device owns child DeviceBays named from the FRSD labels.
Child devices continue to carry workbook-derived names and local context, but their inventory containment is represented by DeviceBay membership rather than direct rack-unit mounting.
""",
        'subdevice_role': 'parent',
        'device_bays': nvl72_rackscale_device_bays(),
    },
    {
        'slug': 'gb300ct',
        'manufacturer': 'dell',
        'model': 'PowerEdge XE9712 GB300 Compute Tray',
        'u_height': Decimal('1.0'),
        'description': 'GB300 GPU tray endpoint for Madison NVL72/GB300 racks.',
        'comments': """\
Workbook naming: sys1-gso1-p-phy-gpu#-su#.
Workbook count convention: GB300 GPU trays 1-126 per SU.
Workbook row elevations place GPU-Node labels as 1RU rows, with 18 GPU nodes per NVL72/GB300 rack.
Notion fabric model: each GB300 tray has four 800G OSFPs; each OSFP expands to four 200G logical channels for the four-plane RoCE model.
Power model: GPU trays consume rack-internal NVL72 busbar power from the GB300 power shelf complex; exact harness details are deferred.
""",
        'interfaces': basic_mgmt_interfaces() + osfp_interfaces(
            4,
            description='800G GPU-facing OSFP; plant graph expands to two MPO attachment units and four 200G plane channels.',
        ),
        'module_bays': osfp_module_bays(
            4,
            description='Physical OSFP transceiver cage; plugin transceiver profiles own MPO and 4x200G lane semantics.',
        ),
        'power_ports': single_power_port(
            'nvl72-busbar',
            'nvl72-busbar',
            'Rack-internal NVL72 busbar input from the GB300 power shelf complex; exact harness details deferred.',
        ),
    },
    {
        'slug': 'gb300ps',
        'manufacturer': 'dell',
        'model': 'PS33 33kW Power Shelf',
        'u_height': Decimal('1.0'),
        'description': '1RU PS33 33kW power shelf for NVL72 rack-internal busbar power.',
        'comments': """\
Workbook naming: sys1-gso1-p-phy-pwt(1-56)-su#.
Workbook count convention: 8 power shelves per GPU rack across 7 racks, 56 per SU.
Rack elevations identify these as "1RU POWER SHELF".
Each shelf sources the rack-internal NVL72 busbar; facility-side delivery remains in netbox_power_plant.
Native NetBox power cables/outlets are intentionally not inserted into the NVL72 internal power path.
""",
        'interfaces': [{'name': 'pmc-mgmt0', 'type': '1000base-t', 'mgmt_only': True, 'description': 'PS33 shelf management interface.'}],
        'power_ports': [
            {
                'name': 'facility-input',
                'type': 'iec-60309-560p6',
                'maximum_draw': 33000,
                'description': 'Facility-side 415V 60A input from the GS001 electrical plant.',
            },
            {
                'name': 'busbar-output-1',
                'type': 'nvl72-busbar',
                'maximum_draw': 33000,
                'description': 'Rack-internal NVL72 busbar source output from the PS33 power shelf.',
            },
        ],
    },
    {
        'slug': 'gb300st',
        'manufacturer': 'nvidia',
        'model': 'GB300 NVL72 NVLink Switch Tray',
        'u_height': Decimal('1.0'),
        'description': 'GB300/NVL72 NVLink switch.',
        'comments': """\
Workbook naming: sys1-gso1-p-phy-nvs(1-63)-su#.
Workbook count convention: 9 NVLswitches per rack across 7 racks, 63 per SU.
Exact front-panel port template is deferred; source workbook identifies the role/count but not a verified connector map.
Power model: NVLink switches consume rack-internal NVL72 busbar power from the GB300 power shelf complex; exact harness details are deferred.
""",
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': single_power_port(
            'nvl72-busbar',
            'nvl72-busbar',
            'Rack-internal NVL72 busbar input from the GB300 power shelf complex; exact harness details deferred.',
        ),
    },
    {
        'slug': 'sn2201',
        'manufacturer': 'nvidia',
        'model': 'SN2201',
        'u_height': Decimal('1.0'),
        'description': 'NVIDIA SN2201 management/OOB switch.',
        'comments': """\
Workbook labels include GPU OOB TOR #1/#2, Mgmt Row OOB Leaf, MgmtRow OOB SPINE, Nvidia SN2201 Matrix, and Nvidia SN2201 Nscale.
Workbook power summary: SN2201 appears as 3 units, 315 W total and 22.5 kg total in the sampled summary, implying 105 W and 7.5 kg per unit for planning.
Existing imported NetBox definition already includes SN2201 interface templates; this seed preserves those templates.
""",
        'weight': Decimal('7.5'),
        'weight_unit': 'kg',
        'skip_templates': True,
    },
    {
        'slug': 'sn2201_m',
        'manufacturer': 'nvidia',
        'model': 'SN2201_M',
        'u_height': Decimal('1.0'),
        'description': 'NVIDIA SN2201_M management/OOB switch variant.',
        'comments': """\
Modeled as a distinct Madison device type whose component template shape is cloned from the imported SN2201 definition.
Use when source material calls out SN2201_M specifically. Interfaces and console are cloned from SN2201; power differs because SN2201_M is busbar-powered.
""",
        'weight': Decimal('7.5'),
        'weight_unit': 'kg',
        'clone_templates_from': {
            'manufacturer': 'nvidia',
            'slug': 'sn2201',
        },
        'power_ports': single_power_port(
            'busbar-input-1',
            'nvl72-busbar',
            'Power-plant load attachment to NVL72 internal busbar; SN2201_M is busbar-powered and does not use dual C14 PSUs.',
        ),
    },
    {
        'slug': 'sn4700',
        'manufacturer': 'nvidia',
        'model': 'SN4700',
        'u_height': Decimal('1.0'),
        'description': 'NVIDIA SN4700 OOB spine/core/border-leaf switch.',
        'comments': """\
Workbook row elevations include SN4700 GPU OOB Spine, OOB Core, MgmtRow OOB Spine, and OOB Border Leaf labels.
NVIDIA SN4000 documentation identifies the SN4700 as a 1U Spectrum-3 400GbE switch with 32 QSFP-DD cages.
Ordering data exposes P2C and C2P airflow SKUs; Madison source workbooks do not yet identify the exact airflow SKU.
Workbook Power Lookup gives planning values of 1100 W and 11.6 kg per SN4700.
""",
        'interfaces': eth0_mgmt_interface() + qsfpdd_interfaces(
            32,
            description='400G QSFP-DD cage; ports 1-32 per NVIDIA SN4700 documentation.',
        ),
        'console_ports': basic_console_port(),
        'power_ports': numbered_power_ports(2, 'iec-60320-c16', maximum_draw=1100),
        'weight': Decimal('11.6'),
        'weight_unit': 'kg',
    },
    {
        'slug': 'sn5610',
        'manufacturer': 'nvidia',
        'model': 'SN5610',
        'u_height': Decimal('2.0'),
        'description': 'NVIDIA SN5610 front-end, back-end, and storage fabric switch.',
        'comments': """\
Workbook row elevations include SN5610 BE leaf, FE leaf/spine, storage leaf, and storage spine placements.
The Madison workbook row-elevation labels span 2RU for every SN5610 placement.
NVIDIA SN5000 documentation identifies the SN5610 as a 2U Spectrum-4 800GbE switch with 64 OSFP cages and 2 SFP28 cages.
NVIDIA ordering data identifies SKU 920-9N42F-00RI-3C1 as the 64-OSFP/2-SFP28, 4-AC-PSU, C2P-airflow SN5610 variant.
Madison source workbooks identify SN5610 generically and do not yet prove that exact airflow SKU.
NVIDIA published hardware weight is 26.9 kg. Workbook Power Lookup gives a planning draw of 2092 W per SN5610.
""",
        'interfaces': eth0_mgmt_interface()
        + osfp_interfaces(
            64,
            prefix='swp',
            description='800G OSFP cage; ports 1-64 per NVIDIA SN5610 documentation.',
        )
        + sfp28_interfaces(
            2,
            start=65,
            description='1/10/25G SFP28 cage; ports 65-66 per NVIDIA SN5610 documentation.',
        ),
        'module_bays': osfp_module_bays(
            64,
            prefix='swp',
            description='Physical OSFP transceiver cage on SN5610; plugin transceiver profiles own MPO and 4x200G lane semantics.',
        ),
        'console_ports': basic_console_port(),
        'power_ports': numbered_power_ports(4, 'iec-60320-c20', maximum_draw=2092),
        'weight': Decimal('26.9'),
        'weight_unit': 'kg',
    },
    {
        'slug': 'cm8148',
        'manufacturer': 'opengear',
        'model': 'CM8148',
        'u_height': Decimal('1.0'),
        'description': 'CM8148 console manager placeholder from Madison row-elevation worksheets.',
        'comments': 'Workbook row elevations include CM8148 GPU, network, management, EW spine, and storage console placements. Exact OEM/SKU and serial-port template are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_server_ports': [
            {'name': f'console-{index:02d}', 'type': 'rj-45', 'description': 'Console-server port placeholder; confirm exact CM8148 port count/template.'}
            for index in range(1, 49)
        ],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'sn5750x1200',
        'manufacturer': 'nvidia',
        'model': 'SN5750x1200',
        'u_height': Decimal('1.0'),
        'description': 'NVIDIA SN5750x1200 Matrix management/core/spine switch.',
        'comments': """\
Workbook labels include Nvidia SN5750x1200 Matrix MGMT Core, Matrix MGMT Spine, and SU# Matrix MGMT Spine.
Workbook power summary: SN5750x1200 appears as 26 units, 39,000 W total and 611 kg total, implying 1,500 W and 23.5 kg per unit for planning.
The 64 OSFP template below follows the Notion leaf-switch architecture where leaf switches expose 64 OSFP cages; confirm the exact SN5750x1200 SKU before production import.
""",
        'weight': Decimal('23.5'),
        'weight_unit': 'kg',
        'interfaces': basic_mgmt_interfaces()
        + osfp_interfaces(64, description='800G OSFP cage; confirm exact SN5750x1200 SKU port count before production import.'),
        'module_bays': osfp_module_bays(
            64,
            description='Physical OSFP transceiver cage; exact SN5750x1200 operating mode remains staged.',
        ),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(1500),
    },
    {
        'slug': 'xdr-q3750x1200-ra',
        'manufacturer': 'nvidia',
        'model': 'XDR Q3750x1200-RA',
        'part_number': 'Q3750x1200-RA',
        'u_height': Decimal('1.0'),
        'description': 'NVIDIA XDR Q3750x1200-RA RoCE/core switch.',
        'comments': """\
Workbook labels show "Nvidia XDR Q3750x1200-RA CORE" across FE/core rack elevations.
Workbook power summary: Q3750x1200 appears as 48 units, 336,000 W total and 2,880 kg total, implying 7,000 W and 60 kg per unit for planning.
Exact port template is deferred because the workbook identifies the model and deployment count but not a verified front-panel connector map.
""",
        'weight': Decimal('60'),
        'weight_unit': 'kg',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(7000),
    },
    {
        'slug': 'nokia-sr1',
        'manufacturer': 'nokia',
        'model': 'SR1',
        'u_height': Decimal('1.0'),
        'description': 'Nokia SR1 routing platform referenced in Madison MMR racks.',
        'comments': 'Workbook rack elevations include Nokia SR1 entries in MMR/edge racks. Exact module and port templates are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'nokia-ixr-d5',
        'manufacturer': 'nokia',
        'model': 'IXR-D5',
        'u_height': Decimal('1.0'),
        'description': 'Nokia IXR-D5 routing platform referenced in Madison MMR racks.',
        'comments': 'Workbook rack elevations include Nokia IXR-D5 entries in MMR/edge racks. Exact module and port templates are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'nokia-breakout-40x10',
        'manufacturer': 'nokia',
        'model': 'Nokia 40G to 4x10G Breakout',
        'u_height': Decimal('0.0'),
        'is_full_depth': False,
        'airflow': 'passive',
        'description': 'Nokia breakout assembly referenced in Madison rack elevations.',
        'comments': 'Workbook label: Nokia Breakout (40 > 4x10). Treat as passive/zero-U until the final module/SKU is confirmed.',
    },
    {
        'slug': 'nokia-ixs-a1',
        'manufacturer': 'nokia',
        'model': 'IXS-A1',
        'u_height': Decimal('1.0'),
        'description': 'Nokia IXS-A1 copper management switch referenced in Madison MMR racks.',
        'comments': 'Workbook MMR sheet label: Nokia IXS-A1 (copper mgt). Exact port template is deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'arista-7280',
        'manufacturer': 'arista',
        'model': '7280',
        'u_height': Decimal('2.0'),
        'description': 'Arista 7280 edge switch.',
        'comments': 'Workbook labels include Customer EDGE inetRtr (Arista 7280) as 2RU placements. Exact SKU and port template are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'palo-alto-pa-1410',
        'manufacturer': 'palo',
        'model': 'PA-1410',
        'u_height': Decimal('1.0'),
        'description': 'Palo Alto PA-1410 firewall.',
        'comments': 'Workbook rack elevations include Palo Alto 1410 as an MMR/edge firewall candidate. Existing imported NetBox definition is preserved.',
        'skip_templates': True,
    },
    {
        'slug': 'palo-alto-pa-1420',
        'manufacturer': 'palo',
        'model': 'PA-1420',
        'u_height': Decimal('1.0'),
        'description': 'Palo Alto PA-1420 firewall candidate.',
        'comments': 'Workbook power summary includes PaloAlto 1420; keep staged separately from the PA-1410 rack-elevation label until the intended firewall SKU is confirmed.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'palo-alto-pa-550',
        'manufacturer': 'palo',
        'model': 'PA-550',
        'u_height': Decimal('1.0'),
        'description': 'Palo Alto PA-550 firewall.',
        'comments': 'Workbook rack elevations include Palo Alto 550 in the Nscale Edge/MMR rack section.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'opengear-om2224-24e-l',
        'manufacturer': 'opengear',
        'model': 'OM2224-24E-L-EU',
        'u_height': Decimal('1.0'),
        'description': 'Opengear OM2224 console server.',
        'comments': 'Workbook rack elevations include Opengear OM2224 and 2224 Console Core. Existing imported NetBox definition is preserved.',
        'skip_templates': True,
    },
    {
        'slug': 'apc-750x1200-pdu-pair',
        'manufacturer': 'apc',
        'model': 'APC 750x1200 Rack with 2x PDU',
        'u_height': Decimal('0.0'),
        'is_full_depth': False,
        'airflow': 'passive',
        'description': 'Rack-level APC/PDU pair placeholder from Madison workbook.',
        'comments': """\
Workbook rack elevations repeat "APC 750 x 1200 $ 2x PDU".
Workbook PDU notes identify 2 PDUs per rack, A/B redundancy, 560P6 connector, and 415V 60A feeds.
Model as a definition placeholder only; rack types and individual PDU devices can be split later when the physical SKU is known.
""",
    },
    {
        'slug': 'generic-rack-pdu-415v-60a',
        'manufacturer': 'generic',
        'model': 'Rack PDU 415V 60A 560P6',
        'u_height': Decimal('0.0'),
        'is_full_depth': False,
        'airflow': 'passive',
        'description': 'Generic rack PDU definition for Madison A/B PDU placeholders.',
        'comments': 'Workbook notes: 2 PDUs per rack, A/B redundancy, 560P6 connector, 415V 60A.',
        'power_ports': [{'name': 'input', 'type': 'nema-l22-30p', 'description': 'Placeholder input; confirm exact 560P6 representation in NetBox before production use.'}],
    },
    {
        'slug': 'generic-cpu-control-node',
        'manufacturer': 'generic',
        'model': 'CPU Control Node',
        'u_height': Decimal('2.0'),
        'description': 'Generic CPU/platform control node.',
        'comments': 'Workbook device summary includes CPU Control Node and central control rack patterns csc(16-42). Row elevations place these as 2RU devices. Exact server SKU is deferred.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '10gbase-x-sfpp', 'description': 'Data/control network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-openstack-control-node',
        'manufacturer': 'generic',
        'model': 'OpenStack Control Node',
        'u_height': Decimal('1.0'),
        'description': 'Generic OpenStack control node.',
        'comments': 'Workbook patterns include sys1-gso1-p-phy-osc(1-15)-er13 for OpenStack Control Nodes.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '10gbase-x-sfpp', 'description': 'Data/control network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-ceph-storage-node',
        'manufacturer': 'generic',
        'model': 'Ceph Storage Node',
        'u_height': Decimal('2.0'),
        'description': 'Generic Ceph storage node.',
        'comments': 'Workbook patterns include sys1-gso1-p-phy-cpo(1-9)-er13 for Ceph Storage nodes.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '25gbase-x-sfp28', 'description': 'Storage network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-meta-storage-node',
        'manufacturer': 'generic',
        'model': 'Meta Storage Node',
        'u_height': Decimal('1.0'),
        'description': 'Generic Meta storage node.',
        'comments': 'Workbook patterns include sys1-gso1-p-mst(1-42)-er10/er12 for Meta Storage.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '25gbase-x-sfp28', 'description': 'Storage network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-data-storage-node',
        'manufacturer': 'generic',
        'model': 'Data Storage Node',
        'u_height': Decimal('2.0'),
        'description': 'Generic Data storage node.',
        'comments': 'Workbook patterns include sys1-gso1-p-dst(1-30)-er10/er12 for Data Storage.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '25gbase-x-sfp28', 'description': 'Storage network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-nmx-server',
        'manufacturer': 'generic',
        'model': 'NMX Server',
        'u_height': Decimal('2.0'),
        'description': 'Generic NMX server.',
        'comments': 'Workbook patterns include sys1-gso1-p-phy-nmx1..3-er13/15. Row elevations place these as 2RU devices.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '10gbase-x-sfpp', 'description': 'Control network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-proxmox-node',
        'manufacturer': 'generic',
        'model': 'Proxmox Management Node',
        'u_height': Decimal('1.0'),
        'description': 'Generic Proxmox management node from the Madison MMR sheet.',
        'comments': 'Workbook MMR sheet labels Proxmox Node 1 and Proxmox Node 2. The final server SKU and height are deferred.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '10gbase-x-sfpp', 'description': 'Management/service network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-vast-cbox',
        'manufacturer': 'generic',
        'model': 'VAST CBox',
        'u_height': Decimal('1.0'),
        'description': 'Generic VAST CBox/control node.',
        'comments': 'Workbook device summary includes VAST CBox.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '25gbase-x-sfp28', 'description': 'Storage/control network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-vast-dbox',
        'manufacturer': 'generic',
        'model': 'VAST DBox',
        'u_height': Decimal('2.0'),
        'description': 'Generic VAST DBox/data node.',
        'comments': 'Workbook device summary includes VAST DBox.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '25gbase-x-sfp28', 'description': 'Storage network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-vast-spine-switch',
        'manufacturer': 'generic',
        'model': 'VAST Spine Switch',
        'u_height': Decimal('1.0'),
        'description': 'Generic VAST spine switch.',
        'comments': 'Workbook device summary includes VAST Spine Switch. Exact SKU and port template are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-vast-leaf-switch',
        'manufacturer': 'generic',
        'model': 'VAST Leaf Switch',
        'u_height': Decimal('1.0'),
        'description': 'Generic VAST leaf switch.',
        'comments': 'Workbook device summary includes VAST Leaf Switch. Exact SKU and port template are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'console_ports': basic_console_port(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-ufm-server',
        'manufacturer': 'generic',
        'model': 'UFM Server',
        'u_height': Decimal('1.0'),
        'description': 'Generic UFM server.',
        'comments': 'Workbook device summary includes UFM server.',
        'interfaces': basic_mgmt_interfaces() + [{'name': 'eth1', 'type': '10gbase-x-sfpp', 'description': 'Fabric management network placeholder.'}],
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'generic-leak-detection',
        'manufacturer': 'generic',
        'model': 'Leak Detection Monitor',
        'u_height': Decimal('1.0'),
        'description': 'Generic leak detection monitor shown in Madison row-elevation worksheets.',
        'comments': 'Workbook row elevations show Leak Detection devices in Row X. Exact OEM/SKU and power/interface templates are deferred.',
        'interfaces': basic_mgmt_interfaces(),
        'power_ports': redundant_power_ports(),
    },
    {
        'slug': 'shuffle-cassette-2x2-mpo',
        'manufacturer': 'nscale',
        'model': '2x2 MPO Shuffle Cassette',
        'u_height': Decimal('0.0'),
        'is_full_depth': False,
        'airflow': 'passive',
        'description': 'Passive 2x2 shuffle cassette for RoCE cable plant.',
        'comments': """\
Notion architecture: each shuffle cassette has 4 front MPOs and 4 rear MPOs with two internal 2x2 shuffles.
Physical tray position is encoded by the parent sb device bay/index, e.g. cassette-1.1 through cassette-3.6.
NetBox front/rear ports stage physical terminations only; the non-1:1 internal shuffle map belongs in netbox_plant_graph TransferPattern and TransferMap definitions.
""",
        'subdevice_role': 'child',
        'mpo_pairs': 4,
    },
    {
        'slug': 'sb',
        'manufacturer': 'nscale',
        'model': '3-Tray 18-Cassette Shuffle Box',
        'u_height': Decimal('1.0'),
        'is_full_depth': False,
        'airflow': 'passive',
        'description': 'Passive shuffle box with 3 trays and 18 total cassette capacity.',
        'comments': """\
Notion architecture: one shuffle box contains 3 trays; each tray contains 6 cassettes; each cassette exposes 4 front MPOs and 4 rear MPOs.
NetBox cannot represent a three-level device hierarchy here, so trays are modeled as positional groups instead of Device objects.
The box has direct child cassette bays named cassette-1.1 through cassette-3.6; the first number is tray position and the second is cassette slot.
Only populated cassette positions should be instantiated under a box. MPO endpoints live on child cassette devices; internal shuffle behavior remains graph-model data.
""",
        'subdevice_role': 'parent',
        'device_bays': shuffle_box_cassette_bays(),
    },
]


def merge_staged_comments(existing, staged):
    section = f'{SOURCE_MARKER_BEGIN}\n{SOURCE_SUMMARY}\n{staged.strip()}\n{SOURCE_MARKER_END}'
    if not existing:
        return section
    pattern = re.compile(
        rf'\n*{re.escape(SOURCE_MARKER_BEGIN)}.*?{re.escape(SOURCE_MARKER_END)}',
        re.DOTALL,
    )
    if SOURCE_MARKER_BEGIN in existing:
        return pattern.sub(f'\n\n{section}', existing).strip()
    return f'{existing.rstrip()}\n\n{section}'


def clean_and_save(obj):
    obj.full_clean()
    obj.save()
    return obj


def upsert_model(Model, lookup, defaults, counters, counter_name):
    obj, created = Model.objects.get_or_create(**lookup, defaults=defaults)
    changed = False
    if not created:
        for key, value in defaults.items():
            if getattr(obj, key) != value:
                setattr(obj, key, value)
                changed = True
    if created or changed:
        clean_and_save(obj)
    counters[f'{counter_name}_created' if created else f'{counter_name}_updated' if changed else f'{counter_name}_unchanged'] += 1
    return obj


def upsert_manufacturers(counters):
    manufacturers = {}
    for slug, (name, description) in MANUFACTURERS.items():
        existing = Manufacturer.objects.filter(slug=slug).first() or Manufacturer.objects.filter(name=name).first()
        if existing:
            changed = False
            if existing.slug != slug and not Manufacturer.objects.filter(slug=slug).exclude(pk=existing.pk).exists():
                existing.slug = slug
                changed = True
            if not existing.description:
                existing.description = description
                changed = True
            if changed:
                clean_and_save(existing)
                counters['manufacturers_updated'] += 1
            else:
                counters['manufacturers_unchanged'] += 1
            manufacturers[slug] = existing
            continue
        manufacturers[slug] = upsert_model(
            Manufacturer,
            {'slug': slug},
            {'name': name, 'description': description},
            counters,
            'manufacturers',
        )
    return manufacturers


def upsert_roles(counters):
    for slug, name, color, description in ROLES:
        role = DeviceRole.objects.filter(slug=slug).first() or DeviceRole.objects.filter(name=name).first()
        if role is None:
            role = DeviceRole(slug=slug)
        created = role.pk is None
        changed = created
        values = {'name': name, 'color': color, 'description': description, 'vm_role': False}
        for key, value in values.items():
            if getattr(role, key) != value:
                setattr(role, key, value)
                changed = True
        if changed:
            clean_and_save(role)
        counters['roles_created' if created else 'roles_updated' if changed else 'roles_unchanged'] += 1


def upsert_rack_roles(counters):
    for slug, name, color, description in RACK_ROLES:
        role = RackRole.objects.filter(slug=slug).first() or RackRole.objects.filter(name=name).first()
        if role is None:
            role = RackRole(slug=slug)
        created = role.pk is None
        changed = created
        values = {'name': name, 'color': color, 'description': description}
        for key, value in values.items():
            if getattr(role, key) != value:
                setattr(role, key, value)
                changed = True
        if changed:
            clean_and_save(role)
        counters['rack_roles_created' if created else 'rack_roles_updated' if changed else 'rack_roles_unchanged'] += 1


def upsert_template(Model, device_type, name, defaults, counters, key):
    obj, created = Model.objects.get_or_create(device_type=device_type, name=name, defaults=defaults)
    changed = False
    if not created:
        for field, value in defaults.items():
            if getattr(obj, field) != value:
                setattr(obj, field, value)
                changed = True
    if created or changed:
        clean_and_save(obj)
    counters[f'{key}_created' if created else f'{key}_updated' if changed else f'{key}_unchanged'] += 1
    return obj


def upsert_interfaces(device_type, interfaces, counters):
    for interface in interfaces:
        defaults = {
            'type': interface['type'],
            'description': interface.get('description', ''),
            'mgmt_only': interface.get('mgmt_only', False),
            'enabled': True,
        }
        upsert_template(InterfaceTemplate, device_type, interface['name'], defaults, counters, 'interfaces')


def upsert_console_ports(device_type, ports, counters):
    for port in ports:
        defaults = {
            'type': port.get('type'),
            'description': port.get('description', ''),
        }
        upsert_template(ConsolePortTemplate, device_type, port['name'], defaults, counters, 'console_ports')


def upsert_console_server_ports(device_type, ports, counters):
    for port in ports:
        defaults = {
            'type': port.get('type'),
            'description': port.get('description', ''),
        }
        upsert_template(ConsoleServerPortTemplate, device_type, port['name'], defaults, counters, 'console_server_ports')


def upsert_power_ports(device_type, ports, counters):
    desired_names = {port['name'] for port in ports}
    for port in ports:
        defaults = {
            'type': port.get('type'),
            'description': port.get('description', ''),
            'maximum_draw': port.get('maximum_draw'),
            'allocated_draw': port.get('allocated_draw'),
        }
        upsert_template(PowerPortTemplate, device_type, port['name'], defaults, counters, 'power_ports')
    if ports:
        stale = PowerPortTemplate.objects.filter(device_type=device_type).exclude(name__in=desired_names)
        deleted_count, _ = stale.delete()
        counters['power_ports_deleted'] += deleted_count


def upsert_device_bays(device_type, bays, counters, *, prune=False):
    desired_names = {bay['name'] for bay in bays}
    for bay in bays:
        defaults = {
            'label': bay.get('label', ''),
            'description': bay.get('description', ''),
        }
        upsert_template(DeviceBayTemplate, device_type, bay['name'], defaults, counters, 'device_bays')
    if prune:
        stale = DeviceBayTemplate.objects.filter(device_type=device_type).exclude(name__in=desired_names)
        deleted_count, _ = stale.delete()
        counters['device_bays_deleted'] += deleted_count


def upsert_module_bays(device_type, bays, counters, *, prune=False):
    desired_names = {bay['name'] for bay in bays}
    for bay in bays:
        defaults = {
            'label': bay.get('label', ''),
            'description': bay.get('description', ''),
            'position': bay.get('position', ''),
            'module_type': bay.get('module_type'),
        }
        upsert_template(ModuleBayTemplate, device_type, bay['name'], defaults, counters, 'module_bays')
    if prune:
        stale = ModuleBayTemplate.objects.filter(device_type=device_type).exclude(name__in=desired_names)
        deleted_count, _ = stale.delete()
        counters['module_bays_deleted'] += deleted_count


def upsert_mpo_pairs(device_type, count, counters, *, prefix=''):
    for index in range(1, count + 1):
        rear_name = f'{prefix}rear-mpo-{index:02d}'
        front_name = f'{prefix}front-mpo-{index:02d}'
        rear = upsert_template(
            RearPortTemplate,
            device_type,
            rear_name,
            {
                'type': 'mpo',
                'positions': 1,
                'description': 'MPO rear-side termination staged from Madison passive plant sources.',
                'color': '2196f3',
            },
            counters,
            'rear_ports',
        )
        upsert_template(
            FrontPortTemplate,
            device_type,
            front_name,
            {
                'type': 'mpo',
                'rear_port': rear,
                'rear_port_position': 1,
                'description': 'MPO front-side termination staged from Madison passive plant sources.',
                'color': '4caf50',
            },
            counters,
            'front_ports',
        )


def upsert_shuffle_box_ports(device_type, counters):
    for tray in range(1, 4):
        for cassette in range(1, 7):
            prefix = f't{tray}-c{cassette}-'
            upsert_mpo_pairs(device_type, 4, counters, prefix=prefix)


def upsert_cloned_template(Model, source_template, target_device_type, counters, key, fields):
    defaults = {
        field: getattr(source_template, field)
        for field in fields
        if hasattr(source_template, field)
    }
    upsert_template(Model, target_device_type, source_template.name, defaults, counters, key)


def clone_templates_from_device_type(source_device_type, target_device_type, counters):
    for template in source_device_type.interfacetemplates.all():
        upsert_cloned_template(
            InterfaceTemplate,
            template,
            target_device_type,
            counters,
            'interfaces',
            ('type', 'label', 'description', 'mgmt_only', 'enabled'),
        )
    for template in source_device_type.consoleporttemplates.all():
        upsert_cloned_template(
            ConsolePortTemplate,
            template,
            target_device_type,
            counters,
            'console_ports',
            ('type', 'label', 'description'),
        )
    for template in source_device_type.consoleserverporttemplates.all():
        upsert_cloned_template(
            ConsoleServerPortTemplate,
            template,
            target_device_type,
            counters,
            'console_server_ports',
            ('type', 'label', 'description'),
        )
    for template in source_device_type.powerporttemplates.all():
        upsert_cloned_template(
            PowerPortTemplate,
            template,
            target_device_type,
            counters,
            'power_ports',
            ('type', 'label', 'description', 'maximum_draw', 'allocated_draw'),
        )


def clone_templates_from_spec(target_device_type, clone_spec, manufacturers, counters):
    source_manufacturer = manufacturers[clone_spec['manufacturer']]
    source_device_type = DeviceType.objects.filter(
        manufacturer=source_manufacturer,
        slug=clone_spec['slug'],
    ).first()
    if source_device_type is None:
        raise RuntimeError(f'Missing source device type for template clone: {clone_spec}')
    clone_templates_from_device_type(source_device_type, target_device_type, counters)


def upsert_device_types(manufacturers, counters):
    for spec in DEVICE_TYPES:
        manufacturer = manufacturers[spec['manufacturer']]
        existing = DeviceType.objects.filter(manufacturer=manufacturer, slug=spec['slug']).first()
        created = existing is None
        device_type = existing or DeviceType(manufacturer=manufacturer, slug=spec['slug'])
        staged_comments = merge_staged_comments(device_type.comments, spec.get('comments', ''))

        values = {
            'model': spec['model'],
            'part_number': spec.get('part_number', ''),
            'u_height': spec.get('u_height', Decimal('1.0')),
            'is_full_depth': spec.get('is_full_depth', True),
            'airflow': spec.get('airflow'),
            'subdevice_role': spec.get('subdevice_role'),
            'description': spec.get('description', ''),
            'comments': staged_comments,
            'weight': spec.get('weight'),
            'weight_unit': spec.get('weight_unit'),
        }

        changed = created
        for key, value in values.items():
            if getattr(device_type, key) != value:
                setattr(device_type, key, value)
                changed = True
        if changed:
            clean_and_save(device_type)
        counters['device_types_created' if created else 'device_types_updated' if changed else 'device_types_unchanged'] += 1

        if spec.get('skip_templates'):
            continue
        if spec.get('clone_templates_from'):
            clone_templates_from_spec(device_type, spec['clone_templates_from'], manufacturers, counters)
            if 'power_ports' in spec:
                upsert_power_ports(device_type, spec['power_ports'], counters)
            continue
        upsert_interfaces(device_type, spec.get('interfaces', []), counters)
        upsert_console_ports(device_type, spec.get('console_ports', []), counters)
        upsert_console_server_ports(device_type, spec.get('console_server_ports', []), counters)
        upsert_power_ports(device_type, spec.get('power_ports', []), counters)
        if 'device_bays' in spec:
            upsert_device_bays(device_type, spec['device_bays'], counters, prune=True)
        if 'module_bays' in spec:
            upsert_module_bays(device_type, spec['module_bays'], counters, prune=True)
        if spec.get('mpo_pairs'):
            upsert_mpo_pairs(device_type, spec['mpo_pairs'], counters)
        if spec.get('shuffle_box_ports'):
            upsert_shuffle_box_ports(device_type, counters)


def retire_obsolete_device_types(counters):
    for slug in OBSOLETE_DEVICE_TYPE_SLUGS:
        device_type = DeviceType.objects.filter(slug=slug).first()
        if device_type is None:
            counters['obsolete_device_types_absent'] += 1
            continue
        if Device.objects.filter(device_type=device_type).exists():
            counters['obsolete_device_types_retained_in_use'] += 1
            continue
        device_type.delete()
        counters['obsolete_device_types_deleted'] += 1


def retire_obsolete_roles(counters):
    for slug in OBSOLETE_ROLE_SLUGS:
        role = DeviceRole.objects.filter(slug=slug).first()
        if role is None:
            counters['obsolete_roles_absent'] += 1
            continue
        if Device.objects.filter(role=role).exists():
            counters['obsolete_roles_retained_in_use'] += 1
            continue
        role.delete()
        counters['obsolete_roles_deleted'] += 1


def print_counter_group(counters, base):
    created = counters[f'{base}_created']
    updated = counters[f'{base}_updated']
    unchanged = counters[f'{base}_unchanged']
    print(f'{base}: created={created} updated={updated} unchanged={unchanged}')


def main():
    before_devices = Device.objects.count()
    counters = Counter()
    with transaction.atomic():
        manufacturers = upsert_manufacturers(counters)
        upsert_roles(counters)
        upsert_rack_roles(counters)
        upsert_device_types(manufacturers, counters)
        retire_obsolete_device_types(counters)
        retire_obsolete_roles(counters)
    after_devices = Device.objects.count()

    print('Madison device definition staging complete.')
    for base in [
        'manufacturers',
        'roles',
        'rack_roles',
        'device_types',
        'interfaces',
        'console_ports',
        'console_server_ports',
        'power_ports',
        'device_bays',
        'rear_ports',
        'front_ports',
    ]:
        print_counter_group(counters, base)
    print(f'devices_before={before_devices} devices_after={after_devices}')
    if before_devices != after_devices:
        raise RuntimeError('Device count changed; this seed must not create individual Device instances.')


main()
