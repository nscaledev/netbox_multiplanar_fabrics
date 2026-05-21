from __future__ import annotations

from collections import Counter

from django.db import transaction

from dcim.models import DeviceType, InterfaceTemplate, ModuleBayTemplate

from netbox_plant_graph.models import StampTemplate, TransferPattern
from netbox_plant_graph.services.architecture import CHANNEL_MAP_MATRIX, ensure_roce_4plane_shuffle_architecture


SOURCE = 'madison_network_endpoint_template_staging_2026_05_19'
MPO12_CHANNEL_MAP_MATRIX = [dict(entry) for entry in CHANNEL_MAP_MATRIX]


DEVICE_ENDPOINTS = [
    {
        'device_type_slug': 'poweredge-xe9712-gb300-compute-tray',
        'breakout_slug': 'poweredge-xe9712-gb300-4xosfp-4plane',
        'breakout_name': 'PowerEdge XE9712 GB300 4xOSFP 4-Plane Breakout',
        'ports': [
            {
                'name': f'osfp{index}',
                'type': '800gbase-x-osfp',
                'module_bay': f'osfp{index}',
                'module_bay_label': f'OSFP {index}',
                'child_count': 4,
                'child_speed_kbps': 200_000_000,
                'breakout_profile_slug': 'dr4-800g-4x200g',
            }
            for index in range(1, 5)
        ],
        'description': (
            'GB300 compute tray endpoint model: four 800G OSFP cages. '
            'Each OSFP is modeled as two MPO attachment units in the plant graph '
            'and four 200G logical plane channels in breakout specs.'
        ),
    },
    {
        'device_type_slug': 'sn5610',
        'breakout_slug': 'sn5610-64xosfp-4plane',
        'breakout_name': 'SN5610 64xOSFP 4-Plane Breakout',
        'ports': [
            {
                'name': f'swp{index}',
                'type': '800gbase-x-osfp',
                'module_bay': f'swp{index}',
                'module_bay_label': f'SWP {index}',
                'child_count': 4,
                'child_speed_kbps': 200_000_000,
                'breakout_profile_slug': 'dr4-800g-4x200g',
            }
            for index in range(1, 65)
        ],
        'aux_ports': [
            {
                'name': f'swp{index}',
                'type': '25gbase-x-sfp28',
                'module_bay': f'swp{index}',
                'module_bay_label': f'SWP {index}',
            }
            for index in range(65, 67)
        ],
        'description': (
            'SN5610 fabric-facing endpoint model: 64 800G OSFP cages plus '
            'two 1/10/25G SFP28 cages. The four-plane breakout template applies '
            'only to the 64 OSFP fabric ports.'
        ),
    },
    {
        'device_type_slug': 'sn4700',
        'breakout_slug': 'sn4700-32x400g-4x100g',
        'breakout_name': 'SN4700 32x400G 4x100G Breakout',
        'ports': [
            {
                'name': f'swp{index}',
                'type': '400gbase-x-qsfpdd',
                'module_bay': f'swp{index}',
                'module_bay_label': f'SWP {index}',
                'child_count': 4,
                'child_speed_kbps': 100_000_000,
                'breakout_profile_slug': 'dr4-400g-4x100g',
            }
            for index in range(1, 33)
        ],
        'description': (
            'SN4700 OOB/core endpoint model: 32 400G QSFP-DD cages. '
            'This is a reusable template only; role-specific use is resolved at instance staging.'
        ),
    },
]


def get_device_type(slug):
    try:
        return DeviceType.objects.get(slug=slug)
    except DeviceType.DoesNotExist:
        return None


def upsert_interface_template(device_type, port):
    description = (
        f"{port['type']} cage staged for Madison fiber endpoint modeling. "
        "Plant graph attachment/lane semantics are supplied by netbox_plant_graph templates."
    )
    interface, created = InterfaceTemplate.objects.get_or_create(
        device_type=device_type,
        name=port['name'],
        defaults={
            'type': port['type'],
            'description': description,
        },
    )
    changed = False
    if interface.type != port['type']:
        interface.type = port['type']
        changed = True
    if not interface.description:
        interface.description = description
        changed = True
    if created or changed:
        interface.full_clean()
        interface.save()


def upsert_module_bay_template(device_type, port):
    ModuleBayTemplate.objects.update_or_create(
        device_type=device_type,
        name=port['module_bay'],
        defaults={
            'label': port['module_bay_label'],
            'position': str(port['module_bay'].removeprefix('swp').removeprefix('osfp')),
            'description': (
                f"Optic module bay corresponding to host interface {port['name']}. "
                "The host interface remains the plant graph endpoint anchor."
            ),
        },
    )


def ensure_transfer_pattern(architecture, port):
    slug = port['breakout_profile_slug']
    child_speed_gbps = port['child_speed_kbps'] // 1_000_000
    parent_speed_gbps = 800 if port['type'].startswith('800g') else 400
    pattern, _ = TransferPattern.objects.update_or_create(
        architecture=architecture,
        slug=slug,
        defaults={
            'name': slug.replace('-', ' ').upper(),
            'pattern_kind': 'breakout',
            'rule': {
                'type': 'device_endpoint_breakout',
                'parent_interface_type': port['type'],
                'parent_speed_gbps': parent_speed_gbps,
                'child_count': port['child_count'],
                'child_speed_gbps': child_speed_gbps,
                'mapping_mode': 'sequential',
            },
            'metadata': {
                'source': SOURCE,
                'legacy_model': 'BreakoutProfile',
                'v2_replacement': 'TransferPattern',
            },
        },
    )
    return pattern


def upsert_endpoint_stamp_template(architecture, device_type, definition):
    ports = []
    for sort_order, port in enumerate(definition['ports'], start=1):
        ports.append(
            {
                'name': port['name'],
                'interface_type': port['type'],
                'module_bay': port['module_bay'],
                'child_name_pattern': '{parent}.plane{plane}',
                'child_count': port['child_count'],
                'child_speed_kbps': port['child_speed_kbps'],
                'fabric_plane_start': 1,
                'transfer_pattern_slug': port['breakout_profile_slug'],
                'sort_order': sort_order,
                'v2_targets': [
                    'Endpoint',
                    'ConnectorPosition',
                    'TransportChannel',
                    'OpticalLane',
                ],
            }
        )
    template_spec = {
        'kind': 'network_endpoint_template',
        'schema_version': 2,
        'architecture_slug': architecture.slug,
        'architecture_version': architecture.version,
        'device_type_slug': device_type.slug,
        'device_type_model': device_type.model,
        'ports': ports,
        'channel_subinterfaces': {
            'enabled': True,
            'name_pattern': '{parent_name}/{channel_index}',
            'type': 'virtual',
            'speed_gbps': 200,
            'channel_map_matrix': MPO12_CHANNEL_MAP_MATRIX,
        },
        'aux_ports': [
            {
                'name': port['name'],
                'interface_type': port['type'],
                'module_bay': port['module_bay'],
            }
            for port in definition.get('aux_ports', [])
        ],
        'metadata': {
            'description': definition['description'],
            'source': SOURCE,
        },
    }
    stamp_template, _ = StampTemplate.objects.update_or_create(
        slug=definition['breakout_slug'],
        defaults={
            'architecture': architecture,
            'name': definition['breakout_name'],
            'description': definition['description'],
            'template': template_spec,
            'metadata': {
                'source': SOURCE,
                'template_family': 'network_endpoint',
                'endpoint_template_target': device_type.slug,
                'legacy_model': 'DeviceBreakoutTemplate',
                'v2_replacement': 'StampTemplate',
            },
        },
    )
    return stamp_template


@transaction.atomic
def main():
    counters = Counter()
    fixture = ensure_roce_4plane_shuffle_architecture()
    architecture = fixture.architecture
    for definition in DEVICE_ENDPOINTS:
        device_type = get_device_type(definition['device_type_slug'])
        if device_type is None:
            counters['device_types_missing'] += 1
            print(f"SKIP missing device type: {definition['device_type_slug']}")
            continue

        for port in definition['ports']:
            upsert_interface_template(device_type, port)
            upsert_module_bay_template(device_type, port)
            ensure_transfer_pattern(architecture, port)
            counters['interfaces'] += 1
            counters['module_bays'] += 1
            counters['transfer_patterns'] += 1

        for port in definition.get('aux_ports', []):
            upsert_interface_template(device_type, port)
            upsert_module_bay_template(device_type, port)
            counters['aux_interfaces'] += 1
            counters['aux_module_bays'] += 1

        upsert_endpoint_stamp_template(architecture, device_type, definition)
        counters['stamp_templates'] += 1

    print('Seeded Madison network endpoint templates for netbox_plant_graph v2:')
    for key, value in sorted(counters.items()):
        print(f'  {key}: {value}')


main()
