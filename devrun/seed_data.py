#!/usr/bin/env python3
import argparse
import decimal
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault('NETBOX_PLANT_GRAPH_ENABLE', '1')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'netbox.settings')

import django

django.setup()

from dcim.choices import (
    CableLengthUnitChoices,
    CableProfileChoices,
    CableTypeChoices,
    DeviceFaceChoices,
    DeviceStatusChoices,
    InterfaceTypeChoices,
    PortTypeChoices,
    RackFormFactorChoices,
    RackStatusChoices,
    SiteStatusChoices,
)
from dcim.models import (
    Cable,
    Device,
    DeviceRole,
    DeviceType,
    FrontPort,
    Interface,
    Location,
    Manufacturer,
    Rack,
    RackRole,
    RearPort,
    Site,
)
from netbox_plant_graph.port_mapping_compat import PortMapping
from netbox_plant_graph.models import Fabric
from netbox_plant_graph.services.sync import rebuild_graph


LANES_PER_BREAKOUT = 4
GPU_SHUFFLE_MAPPING = {1: 1, 2: 3, 3: 2, 4: 4}
DEFAULT_PLANES = 4
DEFAULT_LEAVES = 64
DEFAULT_SPINES = 32
DEFAULT_GPU_FACING_PORTS = 32
DEFAULT_SPINE_FACING_PORTS = 32
RACK_U_HEIGHT = 52
NETWORK_SWITCHES_PER_RACK = 16
COMPUTE_PAIR_U_HEIGHT = 3
COMPUTE_PAIRS_PER_RACK = RACK_U_HEIGHT // COMPUTE_PAIR_U_HEIGHT
PATCH_PANELS_PER_RACK = RACK_U_HEIGHT
SWITCH_U_HEIGHT = 2
COMPUTE_NODE_U_HEIGHT = 2
PATCH_PANEL_U_HEIGHT = 1
MANAGEMENT_SWITCH_PORTS = 48
MANAGEMENT_SWITCH_U_HEIGHT = 1
MANAGEMENT_SWITCHES_PER_RACK = RACK_U_HEIGHT // MANAGEMENT_SWITCH_U_HEIGHT
INTERFACE_SPEED_800G = 800_000_000
INTERFACE_SPEED_200G = 200_000_000
INTERFACE_SPEED_1G = 1_000_000
SHORT_PATCH_LENGTH_M = decimal.Decimal('3')
ROW_PATCH_LENGTH_M = decimal.Decimal('15')
OOB_PATCH_LENGTH_M = decimal.Decimal('5')
DELETE_BATCH_SIZE = 200
GENERATED_DEVICE_TYPE_SLUGS = (
    'sn5600',
    '7060x6-64pe',
    '8-gpu-compute-node-connectx-8',
    'dedicated-4x200g-mpo-patch-panel',
    '48x1g-oob-management-switch',
)
GENERATED_DEVICE_ROLE_SLUGS = (
    'leaf-switch',
    'spine-switch',
    'compute-node',
    'patch-panel',
    'bmc-oob-switch',
    'host-oob-switch',
)
GENERATED_RACK_ROLE_SLUGS = (
    'leaf-rack',
    'spine-rack',
    'compute-rack',
    'patch-rack',
    'management-rack',
)
INTERRUPT_SIGNAL = None


@dataclass(frozen=True)
class FabricBlueprint:
    key: str
    fabric_name: str
    site_name: str
    site_slug: str
    switch_manufacturer_name: str
    switch_manufacturer_slug: str
    switch_model: str
    switch_slug: str
    switch_part_number: str
    host_manufacturer_name: str
    host_manufacturer_slug: str
    host_model: str
    host_slug: str
    host_part_number: str

    def switch_parent_name(self, port_number: int) -> str:
        if self.key == 'nvidia':
            return f'Ethernet1/{port_number}'
        return f'Ethernet{port_number}'

    def switch_child_name(self, port_number: int, lane_number: int) -> str:
        return f'{self.switch_parent_name(port_number)}/{lane_number}'

    def compute_child_name(self, lane_number: int) -> str:
        return f'gpu{lane_number:02d}/cx8-fabric0'

    def compute_bmc_name(self) -> str:
        return 'bmc0'

    def compute_management_name(self) -> str:
        return 'mgmt0'


def handle_interrupt(signum, _frame) -> None:
    global INTERRUPT_SIGNAL

    INTERRUPT_SIGNAL = signal.Signals(signum).name


def check_for_interrupt() -> None:
    if INTERRUPT_SIGNAL:
        raise KeyboardInterrupt(f'Seeding interrupted by {INTERRUPT_SIGNAL}.')


FABRICS = {
    'nvidia': FabricBlueprint(
        key='nvidia',
        fabric_name='NVIDIA SN5600 Four-Plane AI Fabric',
        site_name='NVIDIA SN5600 Four-Plane AI Fabric',
        site_slug='nvidia-sn5600-four-plane-ai-fabric',
        switch_manufacturer_name='NVIDIA',
        switch_manufacturer_slug='nvidia',
        switch_model='SN5600',
        switch_slug='sn5600',
        switch_part_number='920-9N42F-00RI-7C0',
        host_manufacturer_name='NVIDIA',
        host_manufacturer_slug='nvidia',
        host_model='8-GPU Compute Node with ConnectX-8',
        host_slug='8-gpu-compute-node-connectx-8',
        host_part_number='compute-node-connectx-8-800g',
    ),
    'arista': FabricBlueprint(
        key='arista',
        fabric_name='Arista 7060X6-64PE Four-Plane AI Fabric',
        site_name='Arista 7060X6-64PE Four-Plane AI Fabric',
        site_slug='arista-7060x6-64pe-four-plane-ai-fabric',
        switch_manufacturer_name='Arista',
        switch_manufacturer_slug='arista',
        switch_model='7060X6-64PE',
        switch_slug='7060x6-64pe',
        switch_part_number='7060X6-64PE',
        host_manufacturer_name='NVIDIA',
        host_manufacturer_slug='nvidia',
        host_model='8-GPU Compute Node with ConnectX-8',
        host_slug='8-gpu-compute-node-connectx-8',
        host_part_number='compute-node-connectx-8-800g',
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Populate the local NetBox instance with large, realistic sample data for the netbox_plant_graph plugin.',
    )
    parser.add_argument(
        '--fabric',
        choices=('all', 'nvidia', 'arista'),
        default='all',
        help='Which fabric dataset to build. Defaults to all.',
    )
    parser.add_argument('--planes', type=int, default=DEFAULT_PLANES, help='Plane count per fabric. Defaults to 4.')
    parser.add_argument('--leaves', type=int, default=DEFAULT_LEAVES, help='Leaf switches per plane. Defaults to 64.')
    parser.add_argument('--spines', type=int, default=DEFAULT_SPINES, help='Spine switches per plane. Defaults to 32.')
    parser.add_argument(
        '--gpu-facing-ports',
        type=int,
        default=DEFAULT_GPU_FACING_PORTS,
        help='GPU-facing 800G ports per leaf. Defaults to 32.',
    )
    parser.add_argument(
        '--spine-facing-ports',
        type=int,
        default=DEFAULT_SPINE_FACING_PORTS,
        help='Spine-facing 800G ports per leaf. Defaults to 32.',
    )
    parser.add_argument(
        '--skip-rebuild',
        action='store_true',
        help='Do not run the plugin graph rebuild after seeding NetBox objects.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show the projected object counts without writing to the database.',
    )
    parser.add_argument(
        '--cleanup-only',
        action='store_true',
        help='Delete the generated sample dataset for the selected fabric(s) and exit.',
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.planes <= 0:
        raise SystemExit('--planes must be greater than zero.')
    if args.leaves <= 0:
        raise SystemExit('--leaves must be greater than zero.')
    if args.spines <= 0:
        raise SystemExit('--spines must be greater than zero.')
    if args.gpu_facing_ports <= 0:
        raise SystemExit('--gpu-facing-ports must be greater than zero.')
    if args.spine_facing_ports <= 0:
        raise SystemExit('--spine-facing-ports must be greater than zero.')
    if args.gpu_facing_ports + args.spine_facing_ports > 64:
        raise SystemExit('The requested per-leaf 800G port split exceeds 64 ports.')
    if args.spine_facing_ports > args.spines:
        raise SystemExit('spine-facing-ports cannot exceed the number of spines because each uplink targets a distinct spine.')


def ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def rack_counts_for_plane(args: argparse.Namespace) -> dict[str, int]:
    compute_count = args.leaves * args.gpu_facing_ports
    management_switches_per_network = ceil_div(compute_count, MANAGEMENT_SWITCH_PORTS)
    return {
        'leaf': ceil_div(args.leaves, NETWORK_SWITCHES_PER_RACK),
        'spine': ceil_div(args.spines, NETWORK_SWITCHES_PER_RACK),
        'compute': ceil_div(compute_count, COMPUTE_PAIRS_PER_RACK),
        'patch': ceil_div(args.leaves * args.spine_facing_ports, PATCH_PANELS_PER_RACK),
        'management': ceil_div(management_switches_per_network * 2, MANAGEMENT_SWITCHES_PER_RACK),
    }


def print_projection(blueprints: list[FabricBlueprint], args: argparse.Namespace) -> None:
    for blueprint in blueprints:
        bundles_per_plane = args.leaves * (args.gpu_facing_ports + args.spine_facing_ports)
        host_count = args.leaves * args.gpu_facing_ports
        gpu_patch_panel_count = host_count
        spine_patch_panel_count = args.leaves * args.spine_facing_ports
        management_switch_count = ceil_div(host_count, MANAGEMENT_SWITCH_PORTS) * 2
        devices_per_plane = (
            args.leaves
            + args.spines
            + host_count
            + gpu_patch_panel_count
            + spine_patch_panel_count
            + management_switch_count
        )
        breakout_parent_interfaces_per_plane = (
            args.leaves * (args.gpu_facing_ports + args.spine_facing_ports)
            + args.spines * args.leaves
            + host_count
        )
        parent_interfaces_per_plane = (
            breakout_parent_interfaces_per_plane
            + (host_count * 2)
            + (host_count * 2)
        )
        child_interfaces_per_plane = breakout_parent_interfaces_per_plane * LANES_PER_BREAKOUT
        ports_per_plane = (gpu_patch_panel_count + spine_patch_panel_count) * 8
        cables_per_plane = (bundles_per_plane * 2) + (host_count * 2)
        racks_per_plane = sum(rack_counts_for_plane(args).values())
        print(
            f'{blueprint.fabric_name}: '
            f'planes={args.planes}, devices={devices_per_plane * args.planes}, '
            f'racks={racks_per_plane * args.planes}, '
            f'parent_interfaces={parent_interfaces_per_plane * args.planes}, '
            f'child_interfaces={child_interfaces_per_plane * args.planes}, '
            f'front_rear_ports={ports_per_plane * args.planes}, '
            f'port_mappings={(gpu_patch_panel_count + spine_patch_panel_count) * LANES_PER_BREAKOUT * args.planes}, '
            f'cables={cables_per_plane * args.planes}'
        )


def get_or_update_role(name: str, slug: str) -> DeviceRole:
    role, created = DeviceRole.objects.get_or_create(name=name, defaults={'slug': slug})
    if not created and role.slug != slug:
        role.slug = slug
        role.save(update_fields=['slug'])
    return role


def get_or_update_rack_role(name: str, slug: str) -> RackRole:
    rack_role, created = RackRole.objects.get_or_create(name=name, defaults={'slug': slug})
    if not created and rack_role.slug != slug:
        rack_role.slug = slug
        rack_role.save(update_fields=['slug'])
    return rack_role


def get_or_update_manufacturer(name: str, slug: str) -> Manufacturer:
    manufacturer, created = Manufacturer.objects.get_or_create(name=name, defaults={'slug': slug})
    if not created and manufacturer.slug != slug:
        manufacturer.slug = slug
        manufacturer.save(update_fields=['slug'])
    return manufacturer


def get_or_update_device_type(
    *,
    manufacturer: Manufacturer,
    model: str,
    slug: str,
    part_number: str,
    **extra_fields,
) -> DeviceType:
    device_type, created = DeviceType.objects.get_or_create(
        manufacturer=manufacturer,
        model=model,
        defaults={
            'slug': slug,
            'part_number': part_number,
            **extra_fields,
        },
    )
    update_fields = []
    desired_fields = {
        'slug': slug,
        'part_number': part_number,
        **extra_fields,
    }
    for field_name, value in desired_fields.items():
        if not created and getattr(device_type, field_name) != value:
            setattr(device_type, field_name, value)
            update_fields.append(field_name)
    if update_fields:
        device_type.save(update_fields=update_fields)
    return device_type


def ensure_site(blueprint: FabricBlueprint) -> Site:
    site, _ = Site.objects.update_or_create(
        slug=blueprint.site_slug,
        defaults={
            'name': blueprint.site_name,
            'status': SiteStatusChoices.STATUS_ACTIVE,
            'description': f'Generated sample data site for {blueprint.fabric_name}',
        },
    )
    return site


def site_inventory_counts(site: Site) -> dict[str, int]:
    return {
        'devices': Device.objects.filter(site=site).count(),
        'interfaces': Interface.objects.filter(device__site=site).count(),
        'front_ports': FrontPort.objects.filter(device__site=site).count(),
        'rear_ports': RearPort.objects.filter(device__site=site).count(),
        'cables': Cable.objects.filter(terminations___site=site).distinct().count(),
        'racks': Rack.objects.filter(site=site).count(),
        'locations': Location.objects.filter(site=site).count(),
    }


def delete_queryset_in_batches(queryset, *, batch_size: int = DELETE_BATCH_SIZE) -> int:
    total_deleted = 0
    model = queryset.model

    while True:
        check_for_interrupt()
        pks = list(queryset.order_by('pk').values_list('pk', flat=True)[:batch_size])
        if not pks:
            break
        deleted, _ = model.objects.filter(pk__in=pks).delete()
        total_deleted += deleted

    return total_deleted


def prune_generated_reference_objects() -> dict[str, dict[str, int]]:
    deleted = {
        'device_types': {},
        'device_roles': {},
        'rack_roles': {},
    }

    for slug in GENERATED_DEVICE_TYPE_SLUGS:
        removed = 0
        for device_type in DeviceType.objects.filter(slug=slug):
            if Device.objects.filter(device_type=device_type).exists():
                continue
            device_type.delete()
            removed += 1
        deleted['device_types'][slug] = removed

    for slug in GENERATED_DEVICE_ROLE_SLUGS:
        removed = 0
        for role in DeviceRole.objects.filter(slug=slug):
            if Device.objects.filter(role=role).exists():
                continue
            role.delete()
            removed += 1
        deleted['device_roles'][slug] = removed

    for slug in GENERATED_RACK_ROLE_SLUGS:
        removed = 0
        for rack_role in RackRole.objects.filter(slug=slug):
            if Rack.objects.filter(role=rack_role).exists():
                continue
            rack_role.delete()
            removed += 1
        deleted['rack_roles'][slug] = removed

    return deleted


def cleanup_existing_dataset(blueprint: FabricBlueprint) -> dict[str, object]:
    check_for_interrupt()
    site = Site.objects.filter(slug=blueprint.site_slug).first()
    fabric_qs = Fabric.objects.filter(name=blueprint.fabric_name)
    fabric_count = fabric_qs.count()
    if fabric_count:
        print(f'Removing plant-graph records for {blueprint.fabric_name}...')
        fabric_qs.delete()

    summary: dict[str, object] = {
        'fabric_records_deleted': fabric_count,
        'site': None,
    }
    if site is None:
        summary['pruned_reference_objects'] = prune_generated_reference_objects()
        return summary

    print(f'Cleaning existing generated objects for {blueprint.fabric_name}...')
    before_counts = site_inventory_counts(site)
    deleted_counts = {
        'cables': delete_queryset_in_batches(Cable.objects.filter(terminations___site=site).distinct()),
        'devices': delete_queryset_in_batches(Device.objects.filter(site=site)),
        'racks': delete_queryset_in_batches(Rack.objects.filter(site=site)),
        'locations': delete_queryset_in_batches(Location.objects.filter(site=site)),
    }
    site.delete()

    summary['site'] = {
        'slug': blueprint.site_slug,
        'before': before_counts,
        'deleted': deleted_counts,
    }
    summary['pruned_reference_objects'] = prune_generated_reference_objects()
    return summary


def ensure_fabric(blueprint: FabricBlueprint, site: Site, args: argparse.Namespace) -> Fabric:
    fabric, _ = Fabric.objects.update_or_create(
        name=blueprint.fabric_name,
        defaults={
            'description': f'{blueprint.switch_model}-based AI fabric sample data',
            'expected_plane_count': args.planes,
            'tier_depth': 2,
            'disjointness_policy': 'full',
            'scope_site': site,
            'metadata': {
                'generator': 'devrun/seed_data.py',
                'switch_model': blueprint.switch_model,
                'switch_part_number': blueprint.switch_part_number,
                'gpu_interface_model': 'NVIDIA ConnectX-8',
                'leafs_per_plane': args.leaves,
                'spines_per_plane': args.spines,
                'gpu_facing_ports_per_leaf': args.gpu_facing_ports,
                'spine_facing_ports_per_leaf': args.spine_facing_ports,
                'lanes_per_breakout': LANES_PER_BREAKOUT,
                'rack_u_height': RACK_U_HEIGHT,
                'leaf_switches_per_rack': NETWORK_SWITCHES_PER_RACK,
                'compute_node_u_height': COMPUTE_NODE_U_HEIGHT,
                'patch_panel_u_height': PATCH_PANEL_U_HEIGHT,
            },
        },
    )
    return fabric


def create_locations(site: Site, planes: int) -> dict[int, Location]:
    locations = {}
    for plane_number in range(1, planes + 1):
        locations[plane_number], _ = Location.objects.update_or_create(
            site=site,
            slug=f'plane-{plane_number:02d}',
            defaults={
                'name': f'Plane {plane_number}',
                'description': f'Generated location for plane {plane_number}',
            },
        )
    return locations


def create_racks_for_plane(
    *,
    blueprint: FabricBlueprint,
    plane_number: int,
    site: Site,
    location: Location,
    roles: dict[str, RackRole],
    args: argparse.Namespace,
) -> dict[str, dict[int, Rack]]:
    rack_counts = rack_counts_for_plane(args)
    rack_map = {
        'leaf': {},
        'spine': {},
        'compute': {},
        'patch': {},
        'management': {},
    }
    rack_specs = (
        ('leaf', 'leaf-rack', roles['leaf']),
        ('spine', 'spine-rack', roles['spine']),
        ('compute', 'compute-rack', roles['compute']),
        ('patch', 'patch-rack', roles['patch']),
        ('management', 'management-rack', roles['management']),
    )
    racks = []
    for kind, label, role in rack_specs:
        for rack_index in range(1, rack_counts[kind] + 1):
            rack = Rack(
                name=f'{blueprint.key}-p{plane_number:02d}-{label}{rack_index:02d}',
                site=site,
                location=location,
                role=role,
                status=RackStatusChoices.STATUS_ACTIVE,
                form_factor=RackFormFactorChoices.TYPE_CABINET,
                u_height=RACK_U_HEIGHT,
                desc_units=False,
            )
            racks.append(rack)
            rack_map[kind][rack_index] = rack

    Rack.objects.bulk_create(racks, batch_size=1000)
    return rack_map


def build_devices_for_plane(
    *,
    blueprint: FabricBlueprint,
    plane_number: int,
    location: Location,
    racks: dict[str, dict[int, Rack]],
    switch_device_type: DeviceType,
    host_device_type: DeviceType,
    patch_panel_device_type: DeviceType,
    management_switch_device_type: DeviceType,
    leaf_role: DeviceRole,
    spine_role: DeviceRole,
    compute_role: DeviceRole,
    patch_panel_role: DeviceRole,
    bmc_switch_role: DeviceRole,
    host_mgmt_switch_role: DeviceRole,
    site: Site,
    args: argparse.Namespace,
) -> dict[str, dict]:
    devices = []
    logical = {
        'leaf': {},
        'spine': {},
        'compute': {},
        'gpu_patch_panel': {},
        'spine_patch_panel': {},
        'bmc_switch': {},
        'host_mgmt_switch': {},
    }

    fabric_prefix = blueprint.key
    for leaf_index in range(1, args.leaves + 1):
        rack_index = ceil_div(leaf_index, NETWORK_SWITCHES_PER_RACK)
        slot_index = (leaf_index - 1) % NETWORK_SWITCHES_PER_RACK
        leaf_device = Device(
            name=f'{fabric_prefix}-p{plane_number:02d}-leaf{leaf_index:02d}',
            site=site,
            location=location,
            rack=racks['leaf'][rack_index],
            position=decimal.Decimal(1 + (slot_index * SWITCH_U_HEIGHT)),
            face=DeviceFaceChoices.FACE_FRONT,
            device_type=switch_device_type,
            role=leaf_role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        devices.append(leaf_device)
        logical['leaf'][leaf_index] = leaf_device

    for spine_index in range(1, args.spines + 1):
        rack_index = ceil_div(spine_index, NETWORK_SWITCHES_PER_RACK)
        slot_index = (spine_index - 1) % NETWORK_SWITCHES_PER_RACK
        spine_device = Device(
            name=f'{fabric_prefix}-p{plane_number:02d}-spine{spine_index:02d}',
            site=site,
            location=location,
            rack=racks['spine'][rack_index],
            position=decimal.Decimal(1 + (slot_index * SWITCH_U_HEIGHT)),
            face=DeviceFaceChoices.FACE_FRONT,
            device_type=switch_device_type,
            role=spine_role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        devices.append(spine_device)
        logical['spine'][spine_index] = spine_device

    management_switches_per_network = ceil_div(args.leaves * args.gpu_facing_ports, MANAGEMENT_SWITCH_PORTS)
    for switch_index in range(1, management_switches_per_network + 1):
        rack_index = ceil_div(switch_index, MANAGEMENT_SWITCHES_PER_RACK)
        rack_slot = (switch_index - 1) % MANAGEMENT_SWITCHES_PER_RACK
        bmc_switch = Device(
            name=f'{fabric_prefix}-p{plane_number:02d}-bmc-oob-sw{switch_index:02d}',
            site=site,
            location=location,
            rack=racks['management'][rack_index],
            position=decimal.Decimal(1 + rack_slot),
            face=DeviceFaceChoices.FACE_FRONT,
            device_type=management_switch_device_type,
            role=bmc_switch_role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        devices.append(bmc_switch)
        logical['bmc_switch'][switch_index] = bmc_switch

        host_switch_ordinal = management_switches_per_network + switch_index
        host_rack_index = ceil_div(host_switch_ordinal, MANAGEMENT_SWITCHES_PER_RACK)
        host_rack_slot = (host_switch_ordinal - 1) % MANAGEMENT_SWITCHES_PER_RACK
        host_mgmt_switch = Device(
            name=f'{fabric_prefix}-p{plane_number:02d}-host-oob-sw{switch_index:02d}',
            site=site,
            location=location,
            rack=racks['management'][host_rack_index],
            position=decimal.Decimal(1 + host_rack_slot),
            face=DeviceFaceChoices.FACE_FRONT,
            device_type=management_switch_device_type,
            role=host_mgmt_switch_role,
            status=DeviceStatusChoices.STATUS_ACTIVE,
        )
        devices.append(host_mgmt_switch)
        logical['host_mgmt_switch'][switch_index] = host_mgmt_switch

    for leaf_index in range(1, args.leaves + 1):
        for bundle_index in range(1, args.gpu_facing_ports + 1):
            compute_ordinal = ((leaf_index - 1) * args.gpu_facing_ports) + bundle_index
            compute_rack_index = ceil_div(compute_ordinal, COMPUTE_PAIRS_PER_RACK)
            compute_slot_index = (compute_ordinal - 1) % COMPUTE_PAIRS_PER_RACK
            panel_position = decimal.Decimal(1 + (compute_slot_index * COMPUTE_PAIR_U_HEIGHT))
            compute_position = panel_position + decimal.Decimal(1)

            compute_device = Device(
                name=f'{fabric_prefix}-p{plane_number:02d}-leaf{leaf_index:02d}-compute{bundle_index:02d}',
                site=site,
                location=location,
                rack=racks['compute'][compute_rack_index],
                position=compute_position,
                face=DeviceFaceChoices.FACE_FRONT,
                device_type=host_device_type,
                role=compute_role,
                status=DeviceStatusChoices.STATUS_ACTIVE,
            )
            devices.append(compute_device)
            logical['compute'][(leaf_index, bundle_index)] = compute_device

            gpu_patch_panel = Device(
                name=f'{fabric_prefix}-p{plane_number:02d}-leaf{leaf_index:02d}-gpu-patch-panel{bundle_index:02d}',
                site=site,
                location=location,
                rack=racks['compute'][compute_rack_index],
                position=panel_position,
                face=DeviceFaceChoices.FACE_FRONT,
                device_type=patch_panel_device_type,
                role=patch_panel_role,
                status=DeviceStatusChoices.STATUS_ACTIVE,
            )
            devices.append(gpu_patch_panel)
            logical['gpu_patch_panel'][(leaf_index, bundle_index)] = gpu_patch_panel

        for uplink_index in range(1, args.spine_facing_ports + 1):
            patch_panel_ordinal = ((leaf_index - 1) * args.spine_facing_ports) + uplink_index
            patch_rack_index = ceil_div(patch_panel_ordinal, PATCH_PANELS_PER_RACK)
            patch_slot_index = (patch_panel_ordinal - 1) % PATCH_PANELS_PER_RACK
            spine_patch_panel = Device(
                name=f'{fabric_prefix}-p{plane_number:02d}-leaf{leaf_index:02d}-fabric-patch-panel{uplink_index:02d}',
                site=site,
                location=location,
                rack=racks['patch'][patch_rack_index],
                position=decimal.Decimal(1 + patch_slot_index),
                face=DeviceFaceChoices.FACE_FRONT,
                device_type=patch_panel_device_type,
                role=patch_panel_role,
                status=DeviceStatusChoices.STATUS_ACTIVE,
            )
            devices.append(spine_patch_panel)
            logical['spine_patch_panel'][(leaf_index, uplink_index)] = spine_patch_panel

    Device.objects.bulk_create(devices, batch_size=1000)
    return logical


def build_interfaces_for_plane(
    *,
    blueprint: FabricBlueprint,
    plane_number: int,
    devices: dict[str, dict],
    args: argparse.Namespace,
) -> dict[str, dict]:
    parents = []
    parent_map = {
        'leaf': {},
        'spine': {},
        'compute': {},
        'compute_bmc': {},
        'compute_mgmt': {},
        'bmc_switch': {},
        'host_mgmt_switch': {},
    }

    for leaf_index, device in devices['leaf'].items():
        for port_number in range(1, args.gpu_facing_ports + args.spine_facing_ports + 1):
            parent = Interface(
                device=device,
                name=blueprint.switch_parent_name(port_number),
                type=InterfaceTypeChoices.TYPE_800GE_OSFP,
                speed=INTERFACE_SPEED_800G,
            )
            parents.append(parent)
            parent_map['leaf'][(leaf_index, port_number)] = parent

    for spine_index, device in devices['spine'].items():
        for port_number in range(1, args.leaves + 1):
            parent = Interface(
                device=device,
                name=blueprint.switch_parent_name(port_number),
                type=InterfaceTypeChoices.TYPE_800GE_OSFP,
                speed=INTERFACE_SPEED_800G,
            )
            parents.append(parent)
            parent_map['spine'][(spine_index, port_number)] = parent

    for compute_key, device in devices['compute'].items():
        fabric_parent = Interface(
            device=device,
            name='cx8/1',
            type=InterfaceTypeChoices.TYPE_800GE_OSFP,
            speed=INTERFACE_SPEED_800G,
        )
        parents.append(fabric_parent)
        parent_map['compute'][compute_key] = fabric_parent
        bmc_parent = Interface(
            device=device,
            name=blueprint.compute_bmc_name(),
            type=InterfaceTypeChoices.TYPE_1GE_FIXED,
            speed=INTERFACE_SPEED_1G,
            mgmt_only=True,
        )
        parents.append(bmc_parent)
        parent_map['compute_bmc'][compute_key] = bmc_parent
        mgmt_parent = Interface(
            device=device,
            name=blueprint.compute_management_name(),
            type=InterfaceTypeChoices.TYPE_1GE_FIXED,
            speed=INTERFACE_SPEED_1G,
            mgmt_only=True,
        )
        parents.append(mgmt_parent)
        parent_map['compute_mgmt'][compute_key] = mgmt_parent

    for switch_index, device in devices['bmc_switch'].items():
        for port_number in range(1, MANAGEMENT_SWITCH_PORTS + 1):
            parent = Interface(
                device=device,
                name=f'Ethernet{port_number}',
                type=InterfaceTypeChoices.TYPE_1GE_FIXED,
                speed=INTERFACE_SPEED_1G,
            )
            parents.append(parent)
            parent_map['bmc_switch'][(switch_index, port_number)] = parent

    for switch_index, device in devices['host_mgmt_switch'].items():
        for port_number in range(1, MANAGEMENT_SWITCH_PORTS + 1):
            parent = Interface(
                device=device,
                name=f'Ethernet{port_number}',
                type=InterfaceTypeChoices.TYPE_1GE_FIXED,
                speed=INTERFACE_SPEED_1G,
            )
            parents.append(parent)
            parent_map['host_mgmt_switch'][(switch_index, port_number)] = parent

    Interface.objects.bulk_create(parents, batch_size=1000)

    children = []
    child_map = {
        'leaf': {},
        'spine': {},
        'compute': {},
    }
    for (leaf_index, port_number), parent in parent_map['leaf'].items():
        for lane_number in range(1, LANES_PER_BREAKOUT + 1):
            child = Interface(
                device=parent.device,
                parent=parent,
                name=blueprint.switch_child_name(port_number, lane_number),
                type=InterfaceTypeChoices.TYPE_VIRTUAL,
                speed=INTERFACE_SPEED_200G,
                custom_field_data={'fabric_plane': plane_number},
            )
            children.append(child)
            child_map['leaf'][(leaf_index, port_number, lane_number)] = child

    for (spine_index, port_number), parent in parent_map['spine'].items():
        for lane_number in range(1, LANES_PER_BREAKOUT + 1):
            child = Interface(
                device=parent.device,
                parent=parent,
                name=blueprint.switch_child_name(port_number, lane_number),
                type=InterfaceTypeChoices.TYPE_VIRTUAL,
                speed=INTERFACE_SPEED_200G,
                custom_field_data={'fabric_plane': plane_number},
            )
            children.append(child)
            child_map['spine'][(spine_index, port_number, lane_number)] = child

    for compute_key, parent in parent_map['compute'].items():
        for lane_number in range(1, LANES_PER_BREAKOUT + 1):
            child = Interface(
                device=parent.device,
                parent=parent,
                name=blueprint.compute_child_name(lane_number),
                type=InterfaceTypeChoices.TYPE_VIRTUAL,
                speed=INTERFACE_SPEED_200G,
                custom_field_data={'fabric_plane': plane_number},
            )
            children.append(child)
            child_map['compute'][(*compute_key, lane_number)] = child

    Interface.objects.bulk_create(children, batch_size=1000)
    return {
        'parent': parent_map,
        'child': child_map,
    }


def build_patch_panel_ports_for_plane(*, devices: dict[str, dict], args: argparse.Namespace) -> dict[str, dict]:
    front_ports = []
    rear_ports = []
    port_map = {
        'gpu_patch_panel_front': {},
        'gpu_patch_panel_rear': {},
        'spine_patch_panel_front': {},
        'spine_patch_panel_rear': {},
    }

    for patch_panel_key, device in devices['gpu_patch_panel'].items():
        for port_number in range(1, LANES_PER_BREAKOUT + 1):
            front = FrontPort(device=device, name=f'front{port_number}', type=PortTypeChoices.TYPE_MPO)
            rear = RearPort(device=device, name=f'rear{port_number}', type=PortTypeChoices.TYPE_MPO, positions=1)
            front_ports.append(front)
            rear_ports.append(rear)
            port_map['gpu_patch_panel_front'][(*patch_panel_key, port_number)] = front
            port_map['gpu_patch_panel_rear'][(*patch_panel_key, port_number)] = rear

    for patch_panel_key, device in devices['spine_patch_panel'].items():
        for port_number in range(1, LANES_PER_BREAKOUT + 1):
            front = FrontPort(device=device, name=f'front{port_number}', type=PortTypeChoices.TYPE_MPO)
            rear = RearPort(device=device, name=f'rear{port_number}', type=PortTypeChoices.TYPE_MPO, positions=1)
            front_ports.append(front)
            rear_ports.append(rear)
            port_map['spine_patch_panel_front'][(*patch_panel_key, port_number)] = front
            port_map['spine_patch_panel_rear'][(*patch_panel_key, port_number)] = rear

    FrontPort.objects.bulk_create(front_ports, batch_size=1000)
    RearPort.objects.bulk_create(rear_ports, batch_size=1000)

    mappings = []
    for patch_panel_key, device in devices['gpu_patch_panel'].items():
        for front_position, rear_position in GPU_SHUFFLE_MAPPING.items():
            mappings.append(
                PortMapping(
                    device=device,
                    front_port=port_map['gpu_patch_panel_front'][(*patch_panel_key, front_position)],
                    front_port_position=1,
                    rear_port=port_map['gpu_patch_panel_rear'][(*patch_panel_key, rear_position)],
                    rear_port_position=1,
                )
            )

    for patch_panel_key, device in devices['spine_patch_panel'].items():
        for front_position, rear_position in GPU_SHUFFLE_MAPPING.items():
            mappings.append(
                PortMapping(
                    device=device,
                    front_port=port_map['spine_patch_panel_front'][(*patch_panel_key, front_position)],
                    front_port_position=1,
                    rear_port=port_map['spine_patch_panel_rear'][(*patch_panel_key, rear_position)],
                    rear_port_position=1,
                )
            )

    PortMapping.objects.bulk_create(mappings, batch_size=1000)
    return port_map


def create_breakout_cable(
    left_termination,
    right_terminations: list,
    *,
    label: str,
    cable_type: str,
    length_m: decimal.Decimal,
) -> None:
    cable = Cable(
        a_terminations=[left_termination],
        b_terminations=right_terminations,
        type=cable_type,
        profile=CableProfileChoices.BREAKOUT_1C4P_4C1P,
        label=label,
        length=length_m,
        length_unit=CableLengthUnitChoices.UNIT_METER,
    )
    cable.save()


def create_direct_cable(
    left_termination,
    right_termination,
    *,
    label: str,
    cable_type: str,
    length_m: decimal.Decimal,
) -> None:
    cable = Cable(
        a_terminations=[left_termination],
        b_terminations=[right_termination],
        type=cable_type,
        label=label,
        length=length_m,
        length_unit=CableLengthUnitChoices.UNIT_METER,
    )
    cable.save()


def create_cables_for_plane(
    *,
    interfaces: dict[str, dict],
    ports: dict[str, dict],
    args: argparse.Namespace,
) -> int:
    cable_count = 0
    total_bundles = args.leaves * (args.gpu_facing_ports + args.spine_facing_ports)
    emitted = 0

    for leaf_index in range(1, args.leaves + 1):
        check_for_interrupt()
        for bundle_index in range(1, args.gpu_facing_ports + 1):
            compute_parent = interfaces['parent']['compute'][(leaf_index, bundle_index)]
            leaf_parent = interfaces['parent']['leaf'][(leaf_index, bundle_index)]
            gpu_fronts = [ports['gpu_patch_panel_front'][(leaf_index, bundle_index, lane)] for lane in range(1, LANES_PER_BREAKOUT + 1)]
            gpu_rears = [ports['gpu_patch_panel_rear'][(leaf_index, bundle_index, lane)] for lane in range(1, LANES_PER_BREAKOUT + 1)]

            create_breakout_cable(
                compute_parent,
                gpu_fronts,
                label=f'compute-patch-p{leaf_index:02d}-{bundle_index:02d}',
                cable_type=CableTypeChoices.TYPE_AOC,
                length_m=SHORT_PATCH_LENGTH_M,
            )
            create_breakout_cable(
                leaf_parent,
                gpu_rears,
                label=f'leaf-gpu-panel-p{leaf_index:02d}-{bundle_index:02d}',
                cable_type=CableTypeChoices.TYPE_MMF_OM4,
                length_m=ROW_PATCH_LENGTH_M,
            )
            cable_count += 2
            emitted += 1
            if emitted % 500 == 0:
                check_for_interrupt()
                print(f'  Created {emitted} of {total_bundles} bundle pairs for the current plane...')

        for uplink_index in range(1, args.spine_facing_ports + 1):
            leaf_port_number = args.gpu_facing_ports + uplink_index
            spine_index = uplink_index
            spine_port_number = leaf_index
            leaf_parent = interfaces['parent']['leaf'][(leaf_index, leaf_port_number)]
            spine_parent = interfaces['parent']['spine'][(spine_index, spine_port_number)]
            spine_fronts = [ports['spine_patch_panel_front'][(leaf_index, uplink_index, lane)] for lane in range(1, LANES_PER_BREAKOUT + 1)]
            spine_rears = [ports['spine_patch_panel_rear'][(leaf_index, uplink_index, lane)] for lane in range(1, LANES_PER_BREAKOUT + 1)]

            create_breakout_cable(
                leaf_parent,
                spine_fronts,
                label=f'leaf-fabric-panel-p{leaf_index:02d}-{uplink_index:02d}',
                cable_type=CableTypeChoices.TYPE_MMF_OM4,
                length_m=ROW_PATCH_LENGTH_M,
            )
            create_breakout_cable(
                spine_parent,
                spine_rears,
                label=f'spine-fabric-panel-p{spine_index:02d}-{spine_port_number:02d}',
                cable_type=CableTypeChoices.TYPE_MMF_OM4,
                length_m=ROW_PATCH_LENGTH_M,
            )
            cable_count += 2
            emitted += 1
            if emitted % 500 == 0:
                check_for_interrupt()
                print(f'  Created {emitted} of {total_bundles} bundle pairs for the current plane...')

    compute_count = args.leaves * args.gpu_facing_ports
    for compute_ordinal in range(1, compute_count + 1):
        if compute_ordinal % 500 == 0:
            check_for_interrupt()
        leaf_index = ceil_div(compute_ordinal, args.gpu_facing_ports)
        bundle_index = ((compute_ordinal - 1) % args.gpu_facing_ports) + 1
        switch_index = ceil_div(compute_ordinal, MANAGEMENT_SWITCH_PORTS)
        switch_port = ((compute_ordinal - 1) % MANAGEMENT_SWITCH_PORTS) + 1

        bmc_interface = interfaces['parent']['compute_bmc'][(leaf_index, bundle_index)]
        bmc_switch_interface = interfaces['parent']['bmc_switch'][(switch_index, switch_port)]
        create_direct_cable(
            bmc_interface,
            bmc_switch_interface,
            label=f'bmc-oob-p{leaf_index:02d}-{bundle_index:02d}',
            cable_type=CableTypeChoices.TYPE_CAT6A,
            length_m=OOB_PATCH_LENGTH_M,
        )

        mgmt_interface = interfaces['parent']['compute_mgmt'][(leaf_index, bundle_index)]
        mgmt_switch_interface = interfaces['parent']['host_mgmt_switch'][(switch_index, switch_port)]
        create_direct_cable(
            mgmt_interface,
            mgmt_switch_interface,
            label=f'host-oob-p{leaf_index:02d}-{bundle_index:02d}',
            cable_type=CableTypeChoices.TYPE_CAT6A,
            length_m=OOB_PATCH_LENGTH_M,
        )
        cable_count += 2

    return cable_count


def seed_fabric(blueprint: FabricBlueprint, args: argparse.Namespace) -> dict[str, int]:
    check_for_interrupt()
    cleanup_existing_dataset(blueprint)
    site = ensure_site(blueprint)
    locations = create_locations(site, args.planes)

    switch_manufacturer = get_or_update_manufacturer(blueprint.switch_manufacturer_name, blueprint.switch_manufacturer_slug)
    host_manufacturer = get_or_update_manufacturer(blueprint.host_manufacturer_name, blueprint.host_manufacturer_slug)
    passive_manufacturer = get_or_update_manufacturer('Passive Components', 'passive-components')
    management_manufacturer = get_or_update_manufacturer('Generic', 'generic')

    switch_device_type = get_or_update_device_type(
        manufacturer=switch_manufacturer,
        model=blueprint.switch_model,
        slug=blueprint.switch_slug,
        part_number=blueprint.switch_part_number,
        u_height=decimal.Decimal(SWITCH_U_HEIGHT),
    )
    compute_device_type = get_or_update_device_type(
        manufacturer=host_manufacturer,
        model=blueprint.host_model,
        slug=blueprint.host_slug,
        part_number=blueprint.host_part_number,
        u_height=decimal.Decimal(COMPUTE_NODE_U_HEIGHT),
    )
    patch_panel_device_type = get_or_update_device_type(
        manufacturer=passive_manufacturer,
        model='Dedicated 4x200G MPO Patch Panel',
        slug='dedicated-4x200g-mpo-patch-panel',
        part_number='patch-panel-4x200g-mpo',
        u_height=decimal.Decimal(PATCH_PANEL_U_HEIGHT),
        is_full_depth=False,
    )
    management_switch_device_type = get_or_update_device_type(
        manufacturer=management_manufacturer,
        model='48x1G OOB Management Switch',
        slug='48x1g-oob-management-switch',
        part_number='oob-switch-48x1g',
        u_height=decimal.Decimal(MANAGEMENT_SWITCH_U_HEIGHT),
        is_full_depth=False,
    )

    leaf_role = get_or_update_role('Leaf Switch', 'leaf-switch')
    spine_role = get_or_update_role('Spine Switch', 'spine-switch')
    compute_role = get_or_update_role('Compute Node', 'compute-node')
    patch_panel_role = get_or_update_role('Patch Panel', 'patch-panel')
    bmc_switch_role = get_or_update_role('BMC OOB Switch', 'bmc-oob-switch')
    host_mgmt_switch_role = get_or_update_role('Host OOB Switch', 'host-oob-switch')
    leaf_rack_role = get_or_update_rack_role('Leaf Rack', 'leaf-rack')
    spine_rack_role = get_or_update_rack_role('Spine Rack', 'spine-rack')
    compute_rack_role = get_or_update_rack_role('Compute Rack', 'compute-rack')
    patch_rack_role = get_or_update_rack_role('Patch Rack', 'patch-rack')
    management_rack_role = get_or_update_rack_role('Management Rack', 'management-rack')

    fabric = ensure_fabric(blueprint, site, args)

    total_cables = 0
    total_racks = 0
    for plane_number in range(1, args.planes + 1):
        check_for_interrupt()
        print(f'Seeding {blueprint.fabric_name}: plane {plane_number} of {args.planes}...')
        plane_racks = create_racks_for_plane(
            blueprint=blueprint,
            plane_number=plane_number,
            site=site,
            location=locations[plane_number],
            roles={
                'leaf': leaf_rack_role,
                'spine': spine_rack_role,
                'compute': compute_rack_role,
                'patch': patch_rack_role,
                'management': management_rack_role,
            },
            args=args,
        )
        total_racks += sum(len(rack_group) for rack_group in plane_racks.values())
        plane_devices = build_devices_for_plane(
            blueprint=blueprint,
            plane_number=plane_number,
            location=locations[plane_number],
            racks=plane_racks,
            switch_device_type=switch_device_type,
            host_device_type=compute_device_type,
            patch_panel_device_type=patch_panel_device_type,
            management_switch_device_type=management_switch_device_type,
            leaf_role=leaf_role,
            spine_role=spine_role,
            compute_role=compute_role,
            patch_panel_role=patch_panel_role,
            bmc_switch_role=bmc_switch_role,
            host_mgmt_switch_role=host_mgmt_switch_role,
            site=site,
            args=args,
        )
        plane_interfaces = build_interfaces_for_plane(
            blueprint=blueprint,
            plane_number=plane_number,
            devices=plane_devices,
            args=args,
        )
        plane_ports = build_patch_panel_ports_for_plane(devices=plane_devices, args=args)
        total_cables += create_cables_for_plane(
            interfaces=plane_interfaces,
            ports=plane_ports,
            args=args,
        )

    if args.skip_rebuild:
        return {
            'cables': total_cables,
            'devices': Device.objects.filter(site=site).count(),
            'racks': Rack.objects.filter(site=site).count(),
        }

    print(f'Rebuilding graph objects for {blueprint.fabric_name}...')
    result = rebuild_graph(scope={'fabric': fabric})
    result['seeded_cables'] = total_cables
    result['seeded_devices'] = Device.objects.filter(site=site).count()
    result['seeded_racks'] = total_racks
    return result


def select_blueprints(args: argparse.Namespace) -> list[FabricBlueprint]:
    if args.fabric == 'all':
        return [FABRICS['nvidia'], FABRICS['arista']]
    return [FABRICS[args.fabric]]


def main() -> int:
    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    args = parse_args()
    validate_args(args)
    blueprints = select_blueprints(args)

    if args.dry_run:
        print_projection(blueprints, args)
        return 0

    try:
        if args.cleanup_only:
            for blueprint in blueprints:
                result = cleanup_existing_dataset(blueprint)
                print(f'{blueprint.fabric_name} cleanup complete: {result}')
            return 0

        for blueprint in blueprints:
            result = seed_fabric(blueprint, args)
            print(f'{blueprint.fabric_name} complete: {result}')
    except KeyboardInterrupt as exc:
        print(exc, file=sys.stderr)
        print('Run ./devrun/seed-data.sh --cleanup-only [--fabric nvidia|arista|all] to remove any partial sample data.', file=sys.stderr)
        return 130

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
