from __future__ import annotations

from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

from dcim.models import Device, FrontPort, Interface, RearPort
from tenancy.models import Tenant

from netbox_plant_graph.models import AttachmentUnit, Fabric, PlantNode, TerminationPoint


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
FABRIC_NAME = 'MAD-1 RoCE Fabric'
SOURCE_MARKER = 'madison_fabric_endpoint_units_v1'
PASSIVE_MPO_DEVICE_TYPE_SLUGS = {'shuffle-cassette-2x2-mpo'}
BATCH_SIZE = 5000


def chunks(items, size=BATCH_SIZE):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def plant_node_type_for(device: Device) -> str:
    slug = device.device_type.slug
    if slug == 'shuffle-cassette-2x2-mpo':
        return 'cassette'
    return 'device'


def plant_node_role_for(device: Device) -> str:
    if device.role_id:
        return device.role.slug
    return device.device_type.slug


def target_devices():
    return (
        Device.objects.filter(site__slug=MAD_SITE_SLUG)
        .filter(Q(interfaces__type__icontains='osfp') | Q(device_type__slug__in=PASSIVE_MPO_DEVICE_TYPE_SLUGS))
        .select_related('role', 'device_type', 'rack', 'site')
        .distinct()
        .order_by('name')
    )


def ensure_plant_nodes(fabric: Fabric, tenant: Tenant, device_ct: ContentType, counters: Counter) -> dict[int, PlantNode]:
    devices = list(target_devices())
    device_ids = [device.pk for device in devices]
    existing = {
        node.source_id: node
        for node in PlantNode.objects.filter(
            fabric=fabric,
            source_type=device_ct,
            source_id__in=device_ids,
        )
    }

    to_create = []
    for device in devices:
        if device.pk in existing:
            counters['plant_nodes_unchanged'] += 1
            continue
        to_create.append(
            PlantNode(
                fabric=fabric,
                name=device.name,
                node_type=plant_node_type_for(device),
                role=plant_node_role_for(device),
                status='planned',
                tenant=tenant,
                source_type=device_ct,
                source_id=device.pk,
                metadata={
                    SOURCE_MARKER: True,
                    'source_model': 'dcim.Device',
                    'device_type_slug': device.device_type.slug,
                    'rack': device.rack.name if device.rack_id else '',
                    'modeled_status': 'planned',
                },
            )
        )

    for batch in chunks(to_create):
        PlantNode.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['plant_nodes_created'] += len(batch)

    return {
        node.source_id: node
        for node in PlantNode.objects.filter(
            fabric=fabric,
            source_type=device_ct,
            source_id__in=device_ids,
        )
    }


def osfp_endpoint_rows(device_nodes: dict[int, PlantNode], interface_ct: ContentType):
    interfaces = (
        Interface.objects.filter(device_id__in=device_nodes.keys(), type__icontains='osfp')
        .select_related('device')
        .order_by('device__name', 'name')
    )
    for interface in interfaces:
        yield {
            'plant_node_id': device_nodes[interface.device_id].pk,
            'name': interface.name,
            'tp_type': 'interface',
            'connector_type': 'osfp',
            'channel_capacity': 2,
            'speed_gbps': 800,
            'source_type': interface_ct,
            'source_id': interface.pk,
            'metadata': {
                SOURCE_MARKER: True,
                'source_model': 'dcim.Interface',
                'netbox_interface_type': interface.type,
                'attachment_model': '2x MPO8 attachment units per 800G OSFP',
                'modeled_status': 'planned',
            },
        }


def front_port_endpoint_rows(device_nodes: dict[int, PlantNode], front_port_ct: ContentType):
    ports = (
        FrontPort.objects.filter(device_id__in=device_nodes.keys(), device__device_type__slug__in=PASSIVE_MPO_DEVICE_TYPE_SLUGS)
        .select_related('device', 'rear_port')
        .order_by('device__name', 'name')
    )
    for port in ports:
        yield {
            'plant_node_id': device_nodes[port.device_id].pk,
            'name': port.name,
            'tp_type': 'front_port',
            'connector_type': 'mpo-8',
            'channel_capacity': 1,
            'speed_gbps': None,
            'source_type': front_port_ct,
            'source_id': port.pk,
            'metadata': {
                SOURCE_MARKER: True,
                'source_model': 'dcim.FrontPort',
                'netbox_port_type': port.type,
                'rear_port_name': port.rear_port.name if port.rear_port_id else '',
                'modeled_status': 'planned',
            },
        }


def rear_port_endpoint_rows(device_nodes: dict[int, PlantNode], rear_port_ct: ContentType):
    ports = (
        RearPort.objects.filter(device_id__in=device_nodes.keys(), device__device_type__slug__in=PASSIVE_MPO_DEVICE_TYPE_SLUGS)
        .select_related('device')
        .order_by('device__name', 'name')
    )
    for port in ports:
        yield {
            'plant_node_id': device_nodes[port.device_id].pk,
            'name': port.name,
            'tp_type': 'rear_port',
            'connector_type': 'mpo-8',
            'channel_capacity': 1,
            'speed_gbps': None,
            'source_type': rear_port_ct,
            'source_id': port.pk,
            'metadata': {
                SOURCE_MARKER: True,
                'source_model': 'dcim.RearPort',
                'netbox_port_type': port.type,
                'positions': port.positions,
                'modeled_status': 'planned',
            },
        }


def ensure_termination_points(fabric: Fabric, endpoint_rows, counters: Counter) -> dict[tuple[int, str], TerminationPoint]:
    endpoint_rows = list(endpoint_rows)
    plant_node_ids = sorted({row['plant_node_id'] for row in endpoint_rows})
    existing_keys = set(
        TerminationPoint.objects.filter(plant_node_id__in=plant_node_ids).values_list('plant_node_id', 'name')
    )
    to_create = []
    for row in endpoint_rows:
        key = (row['plant_node_id'], row['name'])
        if key in existing_keys:
            counters['termination_points_unchanged'] += 1
            continue
        to_create.append(
            TerminationPoint(
                plant_node_id=row['plant_node_id'],
                name=row['name'],
                tp_type=row['tp_type'],
                connector_type=row['connector_type'],
                channel_capacity=row['channel_capacity'],
                speed_gbps=row['speed_gbps'],
                source_type=row['source_type'],
                source_id=row['source_id'],
                metadata=row['metadata'],
            )
        )

    for batch in chunks(to_create):
        TerminationPoint.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['termination_points_created'] += len(batch)

    return {
        (tp.plant_node_id, tp.name): tp
        for tp in TerminationPoint.objects.filter(
            plant_node__fabric=fabric,
            plant_node_id__in=plant_node_ids,
        ).only('id', 'plant_node_id', 'name', 'connector_type')
    }


def ensure_attachment_units(termination_points: dict[tuple[int, str], TerminationPoint], counters: Counter) -> None:
    tp_ids = [tp.pk for tp in termination_points.values()]
    existing_keys = set(
        AttachmentUnit.objects.filter(termination_point_id__in=tp_ids).values_list('termination_point_id', 'ordinal')
    )
    to_create = []

    for tp in termination_points.values():
        if tp.connector_type == 'osfp':
            specs = [
                (1, 'mpo-1', 'logical_slice', 400, 1),
                (2, 'mpo-2', 'logical_slice', 400, 2),
            ]
        else:
            specs = [
                (1, 'mpo', 'passive_group', None, 1),
            ]

        for ordinal, name, unit_type, speed_gbps, mpo_index in specs:
            key = (tp.pk, ordinal)
            if key in existing_keys:
                counters['attachment_units_unchanged'] += 1
                continue
            to_create.append(
                AttachmentUnit(
                    termination_point=tp,
                    name=name,
                    ordinal=ordinal,
                    unit_type=unit_type,
                    speed_gbps=speed_gbps,
                    topology_role='mpo',
                    active=True,
                    source_type=tp.source_type,
                    source_id=tp.source_id,
                    metadata={
                        SOURCE_MARKER: True,
                        'mpo_index': mpo_index,
                        'connector_type': tp.connector_type,
                        'modeled_status': 'planned',
                    },
                )
            )

    for batch in chunks(to_create):
        AttachmentUnit.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['attachment_units_created'] += len(batch)


@transaction.atomic
def main() -> None:
    counters = Counter()
    fabric = Fabric.objects.get(name=FABRIC_NAME)
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    device_ct = ContentType.objects.get_for_model(Device)
    interface_ct = ContentType.objects.get_for_model(Interface)
    front_port_ct = ContentType.objects.get_for_model(FrontPort)
    rear_port_ct = ContentType.objects.get_for_model(RearPort)

    device_nodes = ensure_plant_nodes(fabric, tenant, device_ct, counters)
    endpoint_rows = []
    endpoint_rows.extend(osfp_endpoint_rows(device_nodes, interface_ct))
    endpoint_rows.extend(front_port_endpoint_rows(device_nodes, front_port_ct))
    endpoint_rows.extend(rear_port_endpoint_rows(device_nodes, rear_port_ct))
    termination_points = ensure_termination_points(fabric, endpoint_rows, counters)
    ensure_attachment_units(termination_points, counters)

    print('Madison fabric endpoint unit seeding complete.')
    for key, value in sorted(counters.items()):
        print(f'{key}={value}')
    print(f'fabric={fabric.name}')
    print(f'plant_nodes_total={PlantNode.objects.filter(fabric=fabric).count()}')
    print(f'endpoint_plant_nodes={PlantNode.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'endpoint_termination_points={TerminationPoint.objects.filter(plant_node__fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'endpoint_attachment_units={AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')


if __name__ == '__main__':
    main()
