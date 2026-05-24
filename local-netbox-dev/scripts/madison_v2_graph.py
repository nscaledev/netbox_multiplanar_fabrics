from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path
import sys

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device, FrontPort, Interface, RearPort, Site
from tenancy.models import Tenant

from netbox_plant_graph.models import (
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StrandTermination,
    TransferMap,
    TransferPattern,
    TransportChannel,
    TransportChannelPositionMap,
)
from netbox_plant_graph.services.architecture import (
    ACTIVE_POSITION_GROUP_A,
    ACTIVE_POSITION_GROUP_B,
    CHANNEL_MAP_MATRIX,
    MPO_DARK_POSITIONS,
    MPO_POSITION_COUNT,
    SHUFFLE_MPO_GROUPS,
    ensure_roce_4plane_shuffle_architecture,
    shuffle_2x2_transfer_position_pairs,
)
from netbox_plant_graph.services.transceivers import bind_transceiver_for_osfp_endpoint

from madison_nvl72_appliance import device_effective_rack


MAD_SITE_SLUG = 'gs001'
NSCALE_TENANT_SLUG = 'nscale'
FABRIC_NAME = 'GS001 RoCE Fabric'
FABRIC_SLUG = 'gs001-roce-fabric'
DEFAULT_WAVELENGTH_NM = Decimal('1310.000')
ACTIVE_MPO_POSITIONS_BY_MPO = {
    mpo_index: tuple(
        position
        for entry in CHANNEL_MAP_MATRIX
        if int(entry['mpo_index']) == mpo_index
        for position in entry['positions']
    )
    for mpo_index in sorted({int(entry['mpo_index']) for entry in CHANNEL_MAP_MATRIX})
}
ACTIVE_MPO_POSITION_SET = frozenset(
    position
    for positions in ACTIVE_MPO_POSITIONS_BY_MPO.values()
    for position in positions
)
ACTIVE_MPO_LANE_COUNT = len(ACTIVE_MPO_POSITIONS_BY_MPO[1])
ACTIVE_MPO_LANE_INDEXES = tuple(range(ACTIVE_MPO_LANE_COUNT))
ACTIVE_POSITION_GROUPS = (ACTIVE_POSITION_GROUP_A, ACTIVE_POSITION_GROUP_B)


def shuffle_transfer_position_pairs(*, front_index: int, rear_index: int) -> tuple[tuple[int, int], ...]:
    """
    Return the active-position transfer pairs for one front/rear MPO crossing.

    A 2x2 cassette first splits each front MPO across the two rear MPOs, then
    applies the key-down MPO roll on the rear side.  This preserves the 2x2
    fanout while transforming positions as 12->1, 11->2, ... 1->12.
    """
    return shuffle_2x2_transfer_position_pairs(front_index=front_index, rear_index=rear_index)


def active_positions_for_mpo(mpo_index: int) -> tuple[int, ...]:
    return ACTIVE_MPO_POSITIONS_BY_MPO[int(mpo_index)]


def shuffle_group_mpos(mpo_index: int) -> tuple[int, int]:
    mpo_index = int(mpo_index)
    for group in SHUFFLE_MPO_GROUPS:
        if mpo_index in group:
            return group
    raise ValueError(f'Unsupported shuffle MPO index {mpo_index!r}; expected one of {SHUFFLE_MPO_GROUPS!r}.')


def source_position_plane_numbers_for_shuffle_front(
    *,
    front_mpo: int,
    first_plane: int,
    second_plane: int,
) -> dict[int, int]:
    shuffle_group_mpos(front_mpo)
    return {
        **{position: int(first_plane) for position in ACTIVE_POSITION_GROUP_A},
        **{position: int(second_plane) for position in ACTIVE_POSITION_GROUP_B},
    }


def position_number_for_lane_index(raw_index: int) -> int:
    lane_index = int(raw_index)
    if lane_index < 0 or lane_index >= ACTIVE_MPO_LANE_COUNT:
        raise ValueError(
            f'lane_indexes entries must be zero-based active-lane indexes 0..{ACTIVE_MPO_LANE_COUNT - 1}; '
            f'got {raw_index!r}.'
        )
    return ACTIVE_MPO_POSITIONS_BY_MPO[1][lane_index]


def position_numbers_for_lane_indexes(raw_indexes) -> list[int]:
    return sorted({position_number_for_lane_index(value) for value in raw_indexes})


def normalized_position_plane_numbers(raw_map) -> dict[int, int]:
    if not raw_map:
        return {}
    return {
        int(position): int(plane_number)
        for position, plane_number in dict(raw_map).items()
        if plane_number is not None
    }


def add_script_dir_to_path() -> None:
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))


def chunks(items, size=5000):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def object_ct(obj):
    return ContentType.objects.get_for_model(obj, for_concrete_model=False)


def source_fields(obj) -> dict[str, object]:
    if obj is None:
        return {'source_type': None, 'source_id': None}
    return {'source_type': object_ct(obj), 'source_id': obj.pk}


def get_site() -> Site:
    return Site.objects.get(slug=MAD_SITE_SLUG)


def get_tenant() -> Tenant:
    return Tenant.objects.get(slug=NSCALE_TENANT_SLUG)


def ensure_fabric() -> Fabric:
    fixture = ensure_roce_4plane_shuffle_architecture()
    site = get_site()
    tenant = get_tenant()
    fabric, _ = Fabric.objects.update_or_create(
        slug=FABRIC_SLUG,
        defaults={
            'architecture': fixture.architecture,
            'name': FABRIC_NAME,
            'status': 'planned',
            'tenant': tenant,
            'scope_site': site,
            'metadata': {
                'madison_v2_graph': True,
                'modeled_status': 'planned',
                'netbox_cables': 'forbidden_for_modeled_fabric',
            },
        },
    )
    for plane_number in range(1, 5):
        Plane.objects.update_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'label': f'Plane {plane_number}',
                'metadata': {'madison_v2_graph': True, 'modeled_status': 'planned'},
            },
        )
    return fabric


def get_fabric() -> Fabric:
    return Fabric.objects.get(slug=FABRIC_SLUG)


def site_for_endpoint(endpoint: Endpoint) -> Site | None:
    source = endpoint.source
    device = getattr(source, 'device', None)
    site = getattr(device, 'site', None) or getattr(source, 'site', None)
    if isinstance(site, Site):
        return site
    node_source = endpoint.node.source
    node_site = getattr(node_source, 'site', None)
    if isinstance(node_site, Site):
        return node_site
    return None


def cable_site_for_segment(fabric: Fabric, a_endpoint: Endpoint, b_endpoint: Endpoint) -> Site:
    if fabric.scope_site_id:
        return fabric.scope_site
    return site_for_endpoint(a_endpoint) or site_for_endpoint(b_endpoint) or get_site()


def node_kind_for(device: Device) -> str:
    if device.device_type.slug == 'shuffle-cassette-2x2-mpo':
        return 'passive_assembly'
    return 'active_device'


def ensure_device_node(fabric: Fabric, device: Device, *, marker: str, counters: Counter | None = None) -> FabricNode:
    rack = device_effective_rack(device)
    node, created = FabricNode.objects.update_or_create(
        fabric=fabric,
        address=device.name,
        defaults={
            'name': device.name,
            'node_kind': node_kind_for(device),
            'local_index': None,
            'metadata': {
                marker: True,
                'modeled_status': 'planned',
                'device_type_slug': device.device_type.slug,
                'role_slug': device.role.slug if device.role_id else '',
                'rack': rack.name if rack is not None else '',
            },
            **source_fields(device),
        },
    )
    if counters is not None:
        counters['fabric_nodes_created' if created else 'fabric_nodes_updated'] += 1
    return node


def ensure_endpoint(
    fabric: Fabric,
    node: FabricNode,
    *,
    name: str,
    address: str,
    endpoint_kind: str,
    connector_kind: str,
    position_count: int = 0,
    parent: Endpoint | None = None,
    source_obj=None,
    marker: str,
    metadata: dict | None = None,
    counters: Counter | None = None,
) -> Endpoint:
    endpoint, created = Endpoint.objects.update_or_create(
        fabric=fabric,
        address=address,
        defaults={
            'node': node,
            'parent': parent,
            'name': name,
            'endpoint_kind': endpoint_kind,
            'connector_kind': connector_kind,
            'position_count': position_count,
            'metadata': {
                marker: True,
                'modeled_status': 'planned',
                **(metadata or {}),
            },
            **source_fields(source_obj),
        },
    )
    if counters is not None:
        counters['endpoints_created' if created else 'endpoints_updated'] += 1
    return endpoint


def ensure_positions(endpoint: Endpoint, count: int = MPO_POSITION_COUNT, *, marker: str, counters: Counter | None = None) -> list[ConnectorPosition]:
    positions = []
    for position_number in range(1, count + 1):
        position, created = ConnectorPosition.objects.update_or_create(
            endpoint=endpoint,
            position_number=position_number,
            defaults={
                'label': f'{position_number}',
                'metadata': {marker: True, 'modeled_status': 'planned'},
            },
        )
        positions.append(position)
        if counters is not None:
            counters['connector_positions_created' if created else 'connector_positions_updated'] += 1
    return positions


def lane_index_for(*, mpo_index: int, position_number: int) -> int:
    active_positions = active_positions_for_mpo(mpo_index)
    if position_number not in active_positions:
        raise ValueError(
            f'Position {position_number} is not an active optical lane position for MPO {mpo_index}; '
            f'active positions are {active_positions}.'
        )
    return ((mpo_index - 1) * ACTIVE_MPO_LANE_COUNT) + active_positions.index(position_number) + 1


def transport_channel_index_for(*, mpo_index: int, position_number: int) -> int:
    for entry in CHANNEL_MAP_MATRIX:
        if int(entry['mpo_index']) != int(mpo_index):
            continue
        if position_number in entry['positions']:
            return int(entry['subinterface_index'])
    raise ValueError(
        f'No transport channel mapping for MPO {mpo_index} position {position_number}; '
        f'dark positions are {MPO_DARK_POSITIONS}.'
    )


def ensure_channel_subinterface(
    parent_endpoint: Endpoint,
    *,
    channel_index: int,
    marker: str,
    counters: Counter | None = None,
) -> Interface | None:
    source = parent_endpoint.source
    if not isinstance(source, Interface):
        return None
    child_name = (
        f'{source.name}s{channel_index}'
        if source.name.startswith('swp')
        else f'{source.name}/{channel_index}'
    )
    child, created = Interface.objects.update_or_create(
        device=source.device,
        name=child_name,
        defaults={
            'type': 'virtual',
            'parent': source,
            'enabled': source.enabled,
            'speed': 200_000_000,
            'description': f'Stamped 200G transport channel {channel_index} for {source.name}.',
            'custom_field_data': getattr(source, 'custom_field_data', {}) or {},
        },
    )
    if counters is not None:
        counters['subinterfaces_created' if created else 'subinterfaces_updated'] += 1
    return child


def ensure_transport_channel(
    fabric: Fabric,
    parent_endpoint: Endpoint,
    *,
    channel_index: int,
    marker: str,
    counters: Counter | None = None,
) -> TransportChannel:
    source_subinterface = ensure_channel_subinterface(
        parent_endpoint,
        channel_index=channel_index,
        marker=marker,
        counters=counters,
    )
    channel, created = TransportChannel.objects.update_or_create(
        fabric=fabric,
        endpoint=parent_endpoint,
        channel_index=channel_index,
        defaults={
            'name': f'channel-{channel_index}',
            'speed_gbps': 200,
            'source_subinterface': source_subinterface,
            'metadata': {
                marker: True,
                'modeled_status': 'planned',
                'source': 'madison_v2_graph',
                'channel_semantics': '200G transport channel',
            },
        },
    )
    if counters is not None:
        counters['transport_channels_created' if created else 'transport_channels_updated'] += 1
    return channel


def ensure_transport_channel_position_map(
    *,
    channel: TransportChannel,
    mpo_endpoint: Endpoint,
    position: ConnectorPosition,
    marker: str,
    counters: Counter | None = None,
) -> TransportChannelPositionMap:
    position_map, created = TransportChannelPositionMap.objects.update_or_create(
        channel=channel,
        mpo_position=position,
        defaults={
            'mpo_endpoint': mpo_endpoint,
            'metadata': {
                marker: True,
                'modeled_status': 'planned',
                'source': 'madison_v2_graph',
            },
        },
    )
    if counters is not None:
        counters['transport_channel_position_maps_created' if created else 'transport_channel_position_maps_updated'] += 1
    return position_map


def ensure_optical_lanes(
    fabric: Fabric,
    parent_endpoint: Endpoint,
    mpo_endpoint: Endpoint,
    *,
    mpo_index: int,
    marker: str,
    counters: Counter | None = None,
) -> None:
    positions = ensure_positions(mpo_endpoint, MPO_POSITION_COUNT, marker=marker, counters=counters)
    active_positions = active_positions_for_mpo(mpo_index)
    stale_lanes, _ = OpticalLane.objects.filter(
        fabric=fabric,
        endpoint=parent_endpoint,
        local_mpo_endpoint=mpo_endpoint,
    ).exclude(local_mpo_position__position_number__in=active_positions).delete()
    stale_maps, _ = TransportChannelPositionMap.objects.filter(
        channel__fabric=fabric,
        channel__endpoint=parent_endpoint,
        mpo_endpoint=mpo_endpoint,
    ).delete()
    if counters is not None:
        counters['stale_dark_position_optical_lanes_deleted'] += stale_lanes
        counters['stale_transport_channel_position_maps_deleted'] += stale_maps
    for position in positions:
        if position.position_number not in active_positions:
            continue
        lane_index = lane_index_for(mpo_index=mpo_index, position_number=position.position_number)
        channel_index = transport_channel_index_for(mpo_index=mpo_index, position_number=position.position_number)
        channel = ensure_transport_channel(
            fabric,
            parent_endpoint,
            channel_index=channel_index,
            marker=marker,
            counters=counters,
        )
        ensure_transport_channel_position_map(
            channel=channel,
            mpo_endpoint=mpo_endpoint,
            position=position,
            marker=marker,
            counters=counters,
        )
        for direction in ('send', 'receive'):
            _, created = OpticalLane.objects.update_or_create(
                fabric=fabric,
                endpoint=parent_endpoint,
                lane_index=lane_index,
                direction=direction,
                defaults={
                    'channel': channel,
                    'local_mpo_endpoint': mpo_endpoint,
                    'local_mpo_position': position,
                    'local_mpo_index': mpo_index,
                    'wavelength_nm': DEFAULT_WAVELENGTH_NM,
                    'pair_key': f'{parent_endpoint.address}:mpo-{mpo_index}:lane-{position.position_number}',
                    'metadata': {
                        marker: True,
                        'modeled_status': 'planned',
                        'optical_lane_semantics': 'single_fiber_strand',
                        'active_mpo12_position': True,
                        'strand_position': position.position_number,
                    },
                },
            )
            if counters is not None:
                counters['optical_lanes_created' if created else 'optical_lanes_updated'] += 1


def ensure_osfp_interface_surface(
    fabric: Fabric,
    interface: Interface,
    *,
    marker: str,
    counters: Counter | None = None,
) -> Endpoint:
    node = ensure_device_node(fabric, interface.device, marker=marker, counters=counters)
    osfp = ensure_endpoint(
        fabric,
        node,
        name=interface.name,
        address=f'{interface.device.name}.{interface.name}',
        endpoint_kind='netbox_port',
        connector_kind='osfp',
        parent=None,
        source_obj=interface,
        marker=marker,
        metadata={'netbox_interface_type': interface.type},
        counters=counters,
    )
    mpo_endpoints = {}
    for mpo_index in (1, 2):
        mpo = ensure_endpoint(
            fabric,
            node,
            name=f'{interface.name}.mpo-{mpo_index}',
            address=f'{interface.device.name}.{interface.name}.MPO-{mpo_index}',
            endpoint_kind='subconnector',
            connector_kind='mpo-12',
            position_count=MPO_POSITION_COUNT,
            parent=osfp,
            marker=marker,
            metadata={'mpo_index': mpo_index},
            counters=counters,
        )
        mpo_endpoints[mpo_index] = mpo
        ensure_optical_lanes(fabric, osfp, mpo, mpo_index=mpo_index, marker=marker, counters=counters)
    binding = bind_transceiver_for_osfp_endpoint(
        endpoint=osfp,
        mpo_endpoints=mpo_endpoints,
        module_type_part_number=transceiver_module_part_number_for_interface(interface),
        role_hint=transceiver_role_hint_for_interface(interface),
        create_module=True,
    )
    if counters is not None:
        counters[f'transceiver_bindings_{binding["status"]}'] += 1
        for key, values in binding.get('created', {}).items():
            counters[f'transceiver_{key}_created'] += len(values)
    return osfp


def transceiver_role_hint_for_interface(interface: Interface) -> str:
    role_slug = interface.device.role.slug if interface.device.role_id else ''
    device_type_slug = interface.device.device_type.slug
    if device_type_slug == 'gb300ct':
        return 'gb300_compute_osfp'
    if device_type_slug == 'sn5610':
        if role_slug == 'be-leaf-switch':
            return 'backend_leaf_osfp'
        if role_slug == 'be-spine-switch':
            return 'backend_spine_osfp'
    return role_slug


def transceiver_module_part_number_for_interface(interface: Interface) -> str | None:
    device_type_slug = interface.device.device_type.slug
    if device_type_slug == 'gb300ct':
        return 'MMS4X00-NM'
    if device_type_slug == 'sn5610':
        return 'MMS4X00-NM'
    if device_type_slug == 'sn5750x1200':
        return 'MMS4A00-XM'
    return None


def ensure_cassette_surface(
    fabric: Fabric,
    cassette: Device,
    *,
    marker: str,
    counters: Counter | None = None,
) -> FabricNode:
    node = ensure_device_node(fabric, cassette, marker=marker, counters=counters)
    front_ports = FrontPort.objects.filter(device=cassette).order_by('name')
    rear_ports = RearPort.objects.filter(device=cassette).order_by('name')
    for port in front_ports:
        endpoint = ensure_endpoint(
            fabric,
            node,
            name=port.name,
            address=f'{cassette.name}.{port.name}',
            endpoint_kind='connector',
            connector_kind='mpo-12',
            position_count=MPO_POSITION_COUNT,
            source_obj=port,
            marker=marker,
            metadata={'side': 'front', 'netbox_port_type': port.type},
            counters=counters,
        )
        ensure_positions(endpoint, MPO_POSITION_COUNT, marker=marker, counters=counters)
    for port in rear_ports:
        endpoint = ensure_endpoint(
            fabric,
            node,
            name=port.name,
            address=f'{cassette.name}.{port.name}',
            endpoint_kind='connector',
            connector_kind='mpo-12',
            position_count=MPO_POSITION_COUNT,
            source_obj=port,
            marker=marker,
            metadata={'side': 'rear', 'netbox_port_type': port.type},
            counters=counters,
        )
        ensure_positions(endpoint, MPO_POSITION_COUNT, marker=marker, counters=counters)
    return node


def endpoint_for_device_port(fabric: Fabric, device_name: str, port_name: str) -> Endpoint:
    return Endpoint.objects.get(fabric=fabric, address=f'{device_name}.{port_name}')


def mpo_endpoint_for_device_port(fabric: Fabric, device_name: str, port_name: str, mpo_index: int) -> Endpoint:
    return Endpoint.objects.get(fabric=fabric, address=f'{device_name}.{port_name}.MPO-{mpo_index}')


def position_for(endpoint: Endpoint, position_number: int) -> ConnectorPosition:
    return ConnectorPosition.objects.get(endpoint=endpoint, position_number=position_number)


def optical_lane_for(endpoint: Endpoint, *, mpo_index: int, position_number: int, direction: str) -> OpticalLane:
    return OpticalLane.objects.get(
        endpoint=endpoint,
        lane_index=lane_index_for(mpo_index=mpo_index, position_number=position_number),
        direction=direction,
    )


def segment_position_numbers(segment: dict) -> list[int]:
    if segment.get('position_numbers'):
        return sorted({int(value) for value in segment['position_numbers']})

    lane_indexes = segment.get('lane_indexes')
    if lane_indexes is None:
        lane_indexes = (segment.get('metadata') or {}).get('lane_indexes')
    if lane_indexes:
        return position_numbers_for_lane_indexes(lane_indexes)

    return list(active_positions_for_mpo(1))


def delete_marked_connectivity(marker: str, *, fabric: Fabric, counters: Counter | None = None) -> None:
    managed_cable_ids = set(
        FiberStrand.objects.filter(segment__fabric=fabric, segment__metadata__has_key=marker)
        .exclude(cable_site__isnull=True)
        .exclude(cable_id='')
        .values_list('cable_site_id', 'cable_id')
    )
    segments = FiberSegment.objects.filter(fabric=fabric, metadata__has_key=marker)
    strand_ids = list(FiberStrand.objects.filter(segment__in=segments).values_list('pk', flat=True))
    deleted_terminations, _ = StrandTermination.objects.filter(strand_id__in=strand_ids).delete()
    deleted_strands, _ = FiberStrand.objects.filter(pk__in=strand_ids).delete()
    deleted_segments, _ = segments.delete()
    stale_assemblies = CableAssembly.objects.filter(metadata__has_key=marker)
    if managed_cable_ids:
        from django.db.models import Q

        owned_query = Q()
        for site_id, cable_id in managed_cable_ids:
            owned_query |= Q(site_id=site_id, cable_id=cable_id)
        stale_assemblies = stale_assemblies | CableAssembly.objects.filter(owned_query)
    deleted_cable_assemblies, _ = stale_assemblies.distinct().delete()
    if counters is not None:
        counters['stale_strand_terminations_deleted'] += deleted_terminations
        counters['stale_fiber_strands_deleted'] += deleted_strands
        counters['stale_fiber_segments_deleted'] += deleted_segments
        counters['stale_cable_assemblies_deleted'] += deleted_cable_assemblies


def cable_assembly_for_segment(
    fabric: Fabric,
    *,
    name: str,
    a_endpoint: Endpoint,
    b_endpoint: Endpoint,
    marker: str,
    segment_kind: str,
    position_numbers: list[int],
    metadata: dict | None = None,
    counters: Counter | None = None,
) -> CableAssembly:
    metadata = metadata or {}
    site = cable_site_for_segment(fabric, a_endpoint, b_endpoint)
    cable_id = str(metadata.get('cable_id') or f'{fabric.slug}:{name}')
    parent_cable = None
    parent_cable_id = metadata.get('parent_cable_id')
    if parent_cable_id:
        parent_cable = CableAssembly.objects.filter(site=site, cable_id=str(parent_cable_id)).first()
    cable, created = CableAssembly.objects.update_or_create(
        site=site,
        cable_id=cable_id,
        defaults={
            'manufacturer': str(metadata.get('cable_manufacturer') or metadata.get('manufacturer') or 'Unknown'),
            'serial_number': str(metadata.get('serial_number') or ''),
            'model_id': str(metadata.get('cable_model') or metadata.get('model_id') or segment_kind),
            'description': str(metadata.get('cable_description') or f'{segment_kind} cable assembly for {name}'),
            'parent_cable': parent_cable,
            'metadata': {
                marker: True,
                'modeled_status': 'planned',
                'fabric_id': fabric.pk,
                'fabric_slug': fabric.slug,
                'segment_name': name,
                'segment_kind': segment_kind,
                'path_key': metadata.get('path_key', ''),
                'segment_role': metadata.get('segment_role', ''),
                'a_endpoint': metadata.get('a_endpoint') or a_endpoint.address,
                'b_endpoint': metadata.get('b_endpoint') or b_endpoint.address,
                'a_endpoint_address': a_endpoint.address,
                'b_endpoint_address': b_endpoint.address,
                'a_connector_kind': a_endpoint.connector_kind,
                'b_connector_kind': b_endpoint.connector_kind,
                'connector_type': metadata.get('connector_type') or a_endpoint.connector_kind or b_endpoint.connector_kind,
                'fiber_count': len(position_numbers),
                'strand_indexes': position_numbers,
                **({
                    'bundle_id': metadata.get('bundle_id'),
                } if metadata.get('bundle_id') is not None else {}),
                **({
                    'bundle_position': metadata.get('bundle_position'),
                } if metadata.get('bundle_position') is not None else {}),
            },
        },
    )
    if counters is not None:
        counters['cable_assemblies_created' if created else 'cable_assemblies_updated'] += 1
    return cable


def connect_mpo_endpoints(
    fabric: Fabric,
    *,
    name: str,
    a_endpoint: Endpoint,
    b_endpoint: Endpoint,
    marker: str,
    segment_kind: str = 'jumper',
    position_numbers: list[int] | None = None,
    metadata: dict | None = None,
    counters: Counter | None = None,
) -> FiberSegment:
    selected_positions = position_numbers or list(active_positions_for_mpo(1))
    position_plane_numbers = normalized_position_plane_numbers((metadata or {}).get('position_plane_numbers'))
    cable_assembly = cable_assembly_for_segment(
        fabric,
        name=name,
        a_endpoint=a_endpoint,
        b_endpoint=b_endpoint,
        marker=marker,
        segment_kind=segment_kind,
        position_numbers=selected_positions,
        metadata=metadata,
        counters=counters,
    )
    segment, created = FiberSegment.objects.update_or_create(
        fabric=fabric,
        name=name,
        defaults={
            'segment_kind': segment_kind,
            'a_endpoint': a_endpoint,
            'b_endpoint': b_endpoint,
            'metadata': {
                marker: True,
                'modeled_status': 'planned',
                **(metadata or {}),
            },
        },
    )
    if counters is not None:
        counters['fiber_segments_created' if created else 'fiber_segments_updated'] += 1
    for position_number in selected_positions:
        position_plane_number = position_plane_numbers.get(position_number, (metadata or {}).get('plane_number'))
        strand, strand_created = FiberStrand.objects.update_or_create(
            segment=segment,
            strand_index=position_number,
            defaults={
                'cable_site': cable_assembly.site,
                'cable_id': cable_assembly.cable_id,
                'label': f'{segment.name}:strand-{position_number:02d}',
                'metadata': {
                    marker: True,
                    'modeled_status': 'planned',
                    'mpo_position_number': position_number,
                    'cable_id': cable_assembly.cable_id,
                    **({
                        'plane_number': position_plane_number,
                    } if position_plane_number is not None else {}),
                },
            },
        )
        StrandTermination.objects.update_or_create(
            strand=strand,
            mpo_position=position_for(a_endpoint, position_number),
            defaults={
                'mpo_endpoint': a_endpoint,
                'termination_index': 1,
                'label': f'A{position_number}',
                'metadata': {marker: True, 'side': 'A'},
            },
        )
        StrandTermination.objects.update_or_create(
            strand=strand,
            mpo_position=position_for(b_endpoint, position_number),
            defaults={
                'mpo_endpoint': b_endpoint,
                'termination_index': 2,
                'label': f'B{position_number}',
                'metadata': {marker: True, 'side': 'B'},
            },
        )
        if counters is not None:
            counters['fiber_strands_created' if strand_created else 'fiber_strands_updated'] += 1
            counters['strand_terminations_upserted'] += 2
    return segment


def apply_plane_to_optical_lanes(
    fabric: Fabric,
    *,
    mpo_endpoint: Endpoint,
    plane_number: int | None,
    position_numbers: list[int],
    counters: Counter | None = None,
) -> None:
    if plane_number is None or mpo_endpoint.parent_id is None:
        return
    plane = Plane.objects.get(fabric=fabric, plane_number=plane_number)
    position_ids = list(
        ConnectorPosition.objects.filter(
            endpoint=mpo_endpoint,
            position_number__in=position_numbers,
        ).values_list('pk', flat=True)
    )
    updated = OpticalLane.objects.filter(
        fabric=fabric,
        endpoint=mpo_endpoint.parent,
        local_mpo_endpoint=mpo_endpoint,
        local_mpo_position_id__in=position_ids,
    ).update(plane=plane)
    if counters is not None:
        counters['optical_lanes_plane_assigned'] += updated


def apply_planes_to_optical_lanes(
    fabric: Fabric,
    *,
    mpo_endpoint: Endpoint,
    position_plane_numbers: dict[int, int],
    counters: Counter | None = None,
) -> None:
    if not position_plane_numbers or mpo_endpoint.parent_id is None:
        return
    plane_by_number = {
        plane.plane_number: plane
        for plane in Plane.objects.filter(fabric=fabric, plane_number__in=set(position_plane_numbers.values()))
    }
    for position_number, plane_number in position_plane_numbers.items():
        plane = plane_by_number.get(plane_number)
        if plane is None:
            continue
        position_ids = list(
            ConnectorPosition.objects.filter(
                endpoint=mpo_endpoint,
                position_number=position_number,
            ).values_list('pk', flat=True)
        )
        updated = OpticalLane.objects.filter(
            fabric=fabric,
            endpoint=mpo_endpoint.parent,
            local_mpo_endpoint=mpo_endpoint,
            local_mpo_position_id__in=position_ids,
        ).update(plane=plane)
        if counters is not None:
            counters['optical_lanes_plane_assigned'] += updated


def transfer_pattern(architecture, slug='madison-shuffle-cassette-2x2-full-fanout') -> TransferPattern | None:
    return TransferPattern.objects.filter(architecture=architecture, slug=slug).first()


def ensure_shuffle_transfer_maps(
    fabric: Fabric,
    cassette: Device,
    *,
    marker: str,
    counters: Counter | None = None,
) -> None:
    node = FabricNode.objects.get(fabric=fabric, address=cassette.name)
    pattern = transfer_pattern(fabric.architecture)
    stale_maps, _ = TransferMap.objects.filter(
        fabric=fabric,
        owner_node=node,
        owner_segment__isnull=True,
        map_kind='shuffle_2x2',
    ).delete()
    if counters is not None:
        counters['stale_shuffle_transfer_maps_deleted'] += stale_maps
    for assembly_index, mpos in enumerate(SHUFFLE_MPO_GROUPS, start=1):
        for front_index in mpos:
            front = endpoint_for_device_port(fabric, cassette.name, f'front-mpo-{front_index:02d}')
            for rear_index in mpos:
                rear = endpoint_for_device_port(fabric, cassette.name, f'rear-mpo-{rear_index:02d}')
                for src_position_number, dst_position_number in shuffle_transfer_position_pairs(
                    front_index=front_index,
                    rear_index=rear_index,
                ):
                    _, created = TransferMap.objects.update_or_create(
                        fabric=fabric,
                        owner_node=node,
                        owner_segment=None,
                        src_position=position_for(front, src_position_number),
                        dst_position=position_for(rear, dst_position_number),
                        defaults={
                            'pattern': pattern,
                            'map_kind': 'shuffle_2x2',
                            'bidirectional': True,
                            'group_key': (
                                f'{cassette.name}:shuffle-{assembly_index}:'
                                f'front-{front_index}:rear-{rear_index}:'
                                f'pos-{src_position_number}-to-{dst_position_number}'
                            ),
                            'metadata': {
                                marker: True,
                                'modeled_status': 'planned',
                                'assembly_index': assembly_index,
                                'front_mpo': front_index,
                                'rear_mpo': rear_index,
                                'src_position': src_position_number,
                                'dst_position': dst_position_number,
                                'active_mpo12_position': True,
                                'shuffle_semantics': '2x2_channel_group_matrix_with_key_down_roll',
                                'rear_position_transform': 'key_down_roll_12',
                            },
                        },
                    )
                    if counters is not None:
                        counters['transfer_maps_created' if created else 'transfer_maps_updated'] += 1


def ensure_surfaces_for_devices(fabric: Fabric, devices, *, marker: str, counters: Counter | None = None) -> None:
    for device in devices:
        if device.device_type.slug == 'shuffle-cassette-2x2-mpo':
            ensure_cassette_surface(fabric, device, marker=marker, counters=counters)
            ensure_shuffle_transfer_maps(fabric, device, marker=marker, counters=counters)
            continue
        for interface in Interface.objects.filter(device=device, type__icontains='osfp').order_by('name'):
            ensure_osfp_interface_surface(fabric, interface, marker=marker, counters=counters)


def resolve_path_endpoint(fabric: Fabric, endpoint_tuple: tuple[str, str, int]) -> Endpoint:
    device_name, port_name, mpo_index = endpoint_tuple
    if port_name.startswith('front-mpo-') or port_name.startswith('rear-mpo-'):
        return endpoint_for_device_port(fabric, device_name, port_name)
    return mpo_endpoint_for_device_port(fabric, device_name, port_name, mpo_index)


def path_endpoint_label(endpoint_tuple: tuple[str, str, int]) -> str:
    device_name, port_name, mpo_index = endpoint_tuple
    if port_name.startswith('front-mpo-') or port_name.startswith('rear-mpo-'):
        return f'{device_name}:{port_name}'
    return f'{device_name}:{port_name}:mpo-{mpo_index}'


@transaction.atomic
def stamp_path_segments(
    fabric: Fabric,
    *,
    marker: str,
    path_key: str,
    segments: list[dict],
    replace_existing: bool = True,
) -> Counter:
    counters = Counter()
    if replace_existing:
        delete_marked_connectivity(marker, fabric=fabric, counters=counters)
    for index, segment in enumerate(segments, start=1):
        a_endpoint = resolve_path_endpoint(fabric, segment['a'])
        b_endpoint = resolve_path_endpoint(fabric, segment['b'])
        position_numbers = segment_position_numbers(segment)
        metadata = {
            marker: True,
            'path_key': path_key,
            'segment_role': segment.get('segment_role', ''),
            'a_endpoint': path_endpoint_label(segment['a']),
            'b_endpoint': path_endpoint_label(segment['b']),
            'position_numbers': position_numbers,
            **(segment.get('metadata') or {}),
        }
        if segment.get('plane_number') is not None and metadata.get('plane_number') is None:
            metadata['plane_number'] = segment['plane_number']
        position_plane_numbers = normalized_position_plane_numbers(metadata.get('position_plane_numbers'))
        connect_mpo_endpoints(
            fabric,
            name=f'{path_key}-{index:06d}',
            a_endpoint=a_endpoint,
            b_endpoint=b_endpoint,
            marker=marker,
            position_numbers=position_numbers,
            segment_kind=segment.get('segment_kind', 'jumper'),
            metadata=metadata,
            counters=counters,
        )
        if position_plane_numbers:
            apply_planes_to_optical_lanes(
                fabric,
                mpo_endpoint=a_endpoint,
                position_plane_numbers=position_plane_numbers,
                counters=counters,
            )
            apply_planes_to_optical_lanes(
                fabric,
                mpo_endpoint=b_endpoint,
                position_plane_numbers=position_plane_numbers,
                counters=counters,
            )
        else:
            apply_plane_to_optical_lanes(
                fabric,
                mpo_endpoint=a_endpoint,
                plane_number=metadata.get('plane_number'),
                position_numbers=position_numbers,
                counters=counters,
            )
            apply_plane_to_optical_lanes(
                fabric,
                mpo_endpoint=b_endpoint,
                plane_number=metadata.get('plane_number'),
                position_numbers=position_numbers,
                counters=counters,
            )
    return counters
