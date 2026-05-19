from __future__ import annotations

import os
from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device, FrontPort, Interface, RearPort, Site
from tenancy.models import Tenant

from netbox_plant_graph.models import (
    AssemblyTemplate,
    AttachmentUnit,
    CoarseEdge,
    Fabric,
    FabricPlane,
    FineEdge,
    LaneMap,
    PlaneMembership,
    PlantNode,
    SignalLane,
    TerminationPoint,
    TransferMap,
)
from netbox_plant_graph.services.assembly_topology import stamp_assembly_transfer_policy
from netbox_plant_graph.services.graph_external_edges import stamp_graph_external_path


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
FABRIC_NAME = 'MAD-1 RoCE Fabric'
SOURCE_MARKER = 'madison_graph_endpoint_test_subset_v1'

ACTIVE_ENDPOINT_DEVICE_TYPE_SLUGS = {
    'poweredge-xe9712-gb300-compute-tray',
    'sn5610',
}

SHUFFLE_RACKS = {'A9', 'A10'}
CASSETTE_TEMPLATE_SLUG = 'shuffle-cassette-2x2-mpo'
SAMPLE_PATH_KEY = 'madison-test-a2-gpu01-four-plane-leaf1-via-cassettes'

SAMPLE_PATHS = [
    {
        'plane_number': 1,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 1),
        'cassette': 'mad1-a9-u16-shuffle-box-cassette-1.1',
        'leaf_endpoint': ('mad1-a9-u12-13-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL1',
    },
    {
        'plane_number': 2,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 2),
        'cassette': 'mad1-a9-u16-shuffle-box-cassette-1.2',
        'leaf_endpoint': ('mad1-a9-u14-15-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL2',
    },
    {
        'plane_number': 3,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 1),
        'cassette': 'mad1-a10-u16-shuffle-box-cassette-1.1',
        'leaf_endpoint': ('mad1-a10-u12-13-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL3',
    },
    {
        'plane_number': 4,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 2),
        'cassette': 'mad1-a10-u16-shuffle-box-cassette-1.2',
        'leaf_endpoint': ('mad1-a10-u14-15-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL4',
    },
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_GRAPH_TEST_APPLY') == '1'


def get_fabric(tenant: Tenant) -> Fabric:
    site = Site.objects.get(slug=MAD_SITE_SLUG)
    fabric, _ = Fabric.objects.update_or_create(
        name=FABRIC_NAME,
        defaults={
            'description': 'Madison, NC RoCE fabric staging graph.',
            'expected_plane_count': 4,
            'tier_depth': 3,
            'disjointness_policy': 'full',
            'tenant': tenant,
            'scope_site': site,
            'metadata': {
                SOURCE_MARKER: True,
                'scope': 'Madison endpoint graph staging',
            },
        },
    )
    for plane_number in range(1, fabric.expected_plane_count + 1):
        FabricPlane.objects.get_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'description': f'MAD-1 RoCE plane {plane_number}',
                'metadata': {
                    SOURCE_MARKER: True,
                    'scope': 'Madison endpoint graph staging',
                },
            },
        )
    return fabric


def active_endpoint_devices():
    return (
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            local_context_data__madison_active_endpoint_test_subset=True,
            device_type__slug__in=ACTIVE_ENDPOINT_DEVICE_TYPE_SLUGS,
        )
        .select_related('device_type', 'role', 'rack', 'site', 'tenant')
        .order_by('name')
    )


def cassette_devices():
    return (
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            device_type__slug='shuffle-cassette-2x2-mpo',
            parent_bay__device__rack__name__in=SHUFFLE_RACKS,
        )
        .select_related('device_type', 'role', 'rack', 'site', 'tenant', 'parent_bay__device__rack')
        .order_by('name')
    )


def generic_fk_fields(prefix: str, obj) -> dict[str, object]:
    if obj is None:
        return {f'{prefix}_type': None, f'{prefix}_id': None}
    return {
        f'{prefix}_type': ContentType.objects.get_for_model(obj, for_concrete_model=False),
        f'{prefix}_id': obj.pk,
    }


def node_type_for(device: Device) -> str:
    if device.device_type.slug == 'shuffle-cassette-2x2-mpo':
        return 'cassette'
    return 'device'


def upsert_plant_node(fabric: Fabric, device: Device, tenant: Tenant, counters: Counter) -> PlantNode:
    location = getattr(device, 'location', None) or getattr(device, 'rack', None) or getattr(device, 'site', None)
    node, created = PlantNode.objects.update_or_create(
        fabric=fabric,
        name=device.name,
        defaults={
            'node_type': node_type_for(device),
            'role': device.role.slug if device.role_id else device.device_type.slug,
            'status': device.status or 'planned',
            'tenant': device.tenant or tenant,
            'metadata': {
                SOURCE_MARKER: True,
                'source_model': 'dcim.Device',
                'device_type_slug': device.device_type.slug,
                'rack': device.rack.name if device.rack_id else '',
                'active_test_subset': bool((device.local_context_data or {}).get('madison_active_endpoint_test_subset')),
            },
            **generic_fk_fields('source', device),
            **generic_fk_fields('location', location),
        },
    )
    counters['plant_nodes_created' if created else 'plant_nodes_updated'] += 1
    return node


def upsert_termination(node: PlantNode, source_obj, name: str, tp_type: str, connector_type: str, counters: Counter) -> TerminationPoint:
    if connector_type == 'osfp':
        channel_capacity = 2
        speed_gbps = 800
    else:
        channel_capacity = 1
        speed_gbps = None
    tp, created = TerminationPoint.objects.update_or_create(
        plant_node=node,
        name=name,
        defaults={
            'tp_type': tp_type,
            'connector_type': connector_type,
            'channel_capacity': channel_capacity,
            'speed_gbps': speed_gbps,
            'metadata': {
                SOURCE_MARKER: True,
                'source_model': f'{source_obj._meta.app_label}.{source_obj._meta.model_name}',
            },
            **generic_fk_fields('source', source_obj),
        },
    )
    counters['termination_points_created' if created else 'termination_points_updated'] += 1
    return tp


def upsert_attachment_unit(
    termination: TerminationPoint,
    source_obj,
    *,
    ordinal: int,
    name: str,
    unit_type: str,
    speed_gbps: int | None,
    counters: Counter,
) -> AttachmentUnit:
    au, created = AttachmentUnit.objects.update_or_create(
        termination_point=termination,
        ordinal=ordinal,
        defaults={
            'name': name,
            'unit_type': unit_type,
            'speed_gbps': speed_gbps,
            'topology_role': 'mpo',
            'active': True,
            'metadata': {
                SOURCE_MARKER: True,
                'mpo_index': ordinal,
                'connector_type': termination.connector_type,
            },
            **generic_fk_fields('source', source_obj),
        },
    )
    counters['attachment_units_created' if created else 'attachment_units_updated'] += 1
    return au


def upsert_signal_lanes(attachment_unit: AttachmentUnit, counters: Counter) -> None:
    for lane_index in range(8):
        _, created = SignalLane.objects.update_or_create(
            attachment_unit=attachment_unit,
            lane_index=lane_index,
            defaults={
                'name': f'{attachment_unit.name}:strand-{lane_index + 1:02d}',
                'lane_kind': 'optical_tx',
                'signaling': 'unknown',
                'nominal_rate_gbps': None,
                'direction_role': 'external_mpo8_strand',
                'wavelength_nm': 1310,
                'wavelength_group': '',
                'source_anchor': f'{attachment_unit.termination_point.name}:{attachment_unit.ordinal}:{lane_index + 1}',
                'metadata': {
                    SOURCE_MARKER: True,
                    'optical_lane_semantics': 'single_fiber_strand',
                    'external_endpoint_lane': True,
                    'lane_index': lane_index,
                    'strand_number': lane_index + 1,
                },
            },
        )
        counters['active_signal_lanes_created' if created else 'active_signal_lanes_updated'] += 1


def stamp_active_endpoint_surfaces(fabric: Fabric, tenant: Tenant, counters: Counter) -> None:
    devices = list(active_endpoint_devices())
    for device in devices:
        node = upsert_plant_node(fabric, device, tenant, counters)
        for interface in Interface.objects.filter(device=device, type__icontains='osfp').order_by('name'):
            termination = upsert_termination(
                node,
                interface,
                interface.name,
                'interface',
                'osfp',
                counters,
            )
            first = upsert_attachment_unit(
                termination,
                interface,
                ordinal=1,
                name='mpo-1',
                unit_type='logical_slice',
                speed_gbps=400,
                counters=counters,
            )
            upsert_signal_lanes(first, counters)
            second = upsert_attachment_unit(
                termination,
                interface,
                ordinal=2,
                name='mpo-2',
                unit_type='logical_slice',
                speed_gbps=400,
                counters=counters,
            )
            upsert_signal_lanes(second, counters)


def stamp_cassette_surfaces(fabric: Fabric, tenant: Tenant, counters: Counter) -> None:
    template = AssemblyTemplate.objects.get(slug=CASSETTE_TEMPLATE_SLUG)
    for cassette in cassette_devices():
        result = stamp_assembly_transfer_policy(template, cassette, fabric, tenant=tenant, prune_existing=True)
        counters['cassettes_stamped'] += 1
        counters['cassette_termination_points'] += result.termination_points
        counters['cassette_attachment_units'] += result.attachment_units
        counters['cassette_signal_lanes'] += result.signal_lanes
        counters['cassette_transfer_maps'] += result.transfer_maps
        counters['cassette_lane_maps'] += result.lane_maps


def create_sample_paths(fabric: Fabric, counters: Counter) -> None:
    FineEdge.objects.filter(metadata__graph_external_edge_stamp=True).delete()
    CoarseEdge.objects.filter(metadata__graph_external_edge_stamp=True).delete()
    PlaneMembership.objects.filter(
        plane__fabric=fabric,
        metadata__graph_external_edge_stamp=True,
    ).delete()
    metadata = {
        SOURCE_MARKER: True,
        'test_scope': 'one representative GB300 MPO endpoint through one shuffle cassette to one BE leaf MPO endpoint per plane',
    }
    segments = []
    for path in SAMPLE_PATHS:
        cassette_port = path['cassette_port']
        common = {
            'plane_number': path['plane_number'],
            'cable_profile_name': 'madison-mpo8-smf-patch',
        }
        segments.extend([
            {
                'a': path['gb300_endpoint'],
                'b': (path['cassette'], f'rear-mpo-{cassette_port}', 1),
                'segment_role': 'gb300_to_shuffle_mpo8',
                'metadata': {
                    'segment_kind': 'gb300_to_shuffle',
                    'plane_label': path['plane_label'],
                },
                **common,
            },
            {
                'a': (path['cassette'], f'front-mpo-{cassette_port}', 1),
                'b': path['leaf_endpoint'],
                'segment_role': 'shuffle_to_leaf_mpo8',
                'metadata': {
                    'segment_kind': 'shuffle_to_leaf',
                    'plane_label': path['plane_label'],
                },
                **common,
            },
        ])
    result = stamp_graph_external_path(
        path_key=SAMPLE_PATH_KEY,
        fabric=fabric,
        replace_existing=True,
        metadata=metadata,
        segments=segments,
    )
    counters['sample_path_coarse_edges'] += len(result.coarse_edges)
    counters['sample_path_fine_edges'] += len(result.fine_edges)


def print_dry_run() -> None:
    active = list(active_endpoint_devices())
    cassettes = list(cassette_devices())
    active_osfps = Interface.objects.filter(device__in=active, type__icontains='osfp').count()
    cassette_ports = (
        FrontPort.objects.filter(device__in=cassettes).count()
        + RearPort.objects.filter(device__in=cassettes).count()
    )
    print('Madison graph endpoint test subset dry run')
    print(f'active_endpoint_devices={len(active)}')
    print(f'active_osfp_termination_points={active_osfps}')
    print(f'active_osfp_attachment_units={active_osfps * 2}')
    print(f'shuffle_racks={sorted(SHUFFLE_RACKS)}')
    print(f'shuffle_cassettes={len(cassettes)}')
    print(f'cassette_mpo_termination_points={cassette_ports}')
    print(f'cassette_mpo_attachment_units={cassette_ports}')
    print(f'cassette_signal_lanes={cassette_ports * 8}')
    print(f'cassette_transfer_maps={len(cassettes) * 8}')
    print(f'cassette_lane_maps={len(cassettes) * 64}')
    print(f'sample_path_key={SAMPLE_PATH_KEY}')
    print(f'sample_paths={len(SAMPLE_PATHS)}')
    print(f'sample_path_segments={len(SAMPLE_PATHS) * 2}')


def main() -> None:
    print_dry_run()
    if not apply_enabled():
        print('apply=false; set MADISON_GRAPH_TEST_APPLY=1 to stamp graph endpoint surfaces')
        return

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    fabric = get_fabric(tenant)
    counters = Counter()
    with transaction.atomic():
        stamp_active_endpoint_surfaces(fabric, tenant, counters)
        stamp_cassette_surfaces(fabric, tenant, counters)
        create_sample_paths(fabric, counters)

    print('Madison graph endpoint test subset seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'fabric={fabric.name}')
    print(f'plant_nodes={PlantNode.objects.filter(fabric=fabric).count()}')
    print(f'termination_points={TerminationPoint.objects.filter(plant_node__fabric=fabric).count()}')
    print(f'attachment_units={AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric).count()}')
    print(f'signal_lanes={SignalLane.objects.filter(attachment_unit__termination_point__plant_node__fabric=fabric).count()}')
    print(f'transfer_maps={TransferMap.objects.filter(owner_node__fabric=fabric).count()}')
    print(f'lane_maps={LaneMap.objects.filter(owner_node__fabric=fabric).count()}')
    print(f'sample_coarse_edges={CoarseEdge.objects.filter(metadata__path_key=SAMPLE_PATH_KEY).count()}')
    print(f'sample_fine_edges={FineEdge.objects.filter(metadata__path_key=SAMPLE_PATH_KEY).count()}')


main()
