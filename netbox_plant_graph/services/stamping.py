from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from netbox_plant_graph.models import (
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StampRun,
    StrandTermination,
    TransferMap,
    TransportChannel,
)
from netbox_plant_graph.services.architecture import ArchitectureFixtureResult, ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.resolver import OpticalLanePath, resolve_optical_lane_path


DEFAULT_WAVELENGTHS_NM = {
    1: Decimal('1311.000'),
    2: Decimal('1313.000'),
    3: Decimal('1315.000'),
    4: Decimal('1317.000'),
}


@dataclass(frozen=True)
class MiniFabricStampResult:
    fabric: Fabric
    stamp_run: StampRun
    source_lanes: tuple[OpticalLane, ...]
    destination_lanes: tuple[OpticalLane, ...]
    resolved_paths: tuple[OpticalLanePath, ...]


@dataclass(frozen=True)
class StampExecutionContext:
    fixture: ArchitectureFixtureResult
    template: object
    template_spec: dict
    fabric_name: str
    fabric_slug: str
    source_bindings: dict


HYBRID_STAMP_EXECUTORS = {}


def register_stamp_executor(name):
    def decorator(func):
        HYBRID_STAMP_EXECUTORS[name] = func
        return func

    return decorator


def _stamp_executor_name(template_spec: dict) -> str:
    executor = template_spec.get('executor') or {}
    if executor.get('mode') != 'hybrid':
        raise ValueError('StampTemplate.template.executor.mode must be "hybrid".')
    primitive = executor.get('primitive')
    if not primitive:
        raise ValueError('StampTemplate.template.executor.primitive is required.')
    return primitive


def _source_defaults(source) -> dict:
    if source is None:
        return {
            'source_type': None,
            'source_id': None,
        }
    return {
        'source_type': ContentType.objects.get_for_model(source, for_concrete_model=False),
        'source_id': source.pk,
    }


def _source_binding(context: StampExecutionContext, binding_kind: str, address: str):
    return (context.source_bindings.get(binding_kind) or {}).get(address)


def _node(
    *,
    fabric: Fabric,
    role,
    name: str,
    address: str,
    node_kind: str,
    local_index: int | None = None,
    parent: FabricNode | None = None,
    source=None,
    metadata: dict | None = None,
) -> FabricNode:
    node, _ = FabricNode.objects.update_or_create(
        fabric=fabric,
        address=address,
        defaults={
            'role': role,
            'parent': parent,
            'name': name,
            'node_kind': node_kind,
            'local_index': local_index,
            **_source_defaults(source),
            'metadata': metadata or {},
        },
    )
    return node


def _endpoint(
    *,
    fabric: Fabric,
    node: FabricNode,
    name: str,
    address: str,
    endpoint_kind: str,
    connector_kind: str,
    position_count: int = 0,
    parent: Endpoint | None = None,
    source=None,
    metadata: dict | None = None,
) -> Endpoint:
    endpoint, _ = Endpoint.objects.update_or_create(
        fabric=fabric,
        address=address,
        defaults={
            'node': node,
            'parent': parent,
            'name': name,
            'endpoint_kind': endpoint_kind,
            'connector_kind': connector_kind,
            'position_count': position_count,
            **_source_defaults(source),
            'metadata': metadata or {},
        },
    )
    if position_count:
        for position_number in range(1, position_count + 1):
            ConnectorPosition.objects.update_or_create(
                endpoint=endpoint,
                position_number=position_number,
                defaults={'label': str(position_number)},
            )
    return endpoint


def _position(endpoint: Endpoint, position_number: int) -> ConnectorPosition:
    return ConnectorPosition.objects.get(endpoint=endpoint, position_number=position_number)


def _channel(
    *,
    fabric: Fabric,
    endpoint: Endpoint,
    plane: Plane,
    name: str,
    channel_index: int,
) -> TransportChannel:
    channel, _ = TransportChannel.objects.update_or_create(
        endpoint=endpoint,
        channel_index=channel_index,
        defaults={
            'fabric': fabric,
            'plane': plane,
            'name': name,
            'speed_gbps': 800,
            'metadata': {'fixture': True},
        },
    )
    return channel


def _fiber_strand(
    *,
    fabric: Fabric,
    name: str,
    a_endpoint: Endpoint,
    a_position: ConnectorPosition,
    b_endpoint: Endpoint,
    b_position: ConnectorPosition,
    wavelength_nm: Decimal,
    segment_kind: str = 'trunk',
) -> FiberStrand:
    segment, _ = FiberSegment.objects.update_or_create(
        fabric=fabric,
        name=name,
        defaults={
            'segment_kind': segment_kind,
            'a_endpoint': a_endpoint,
            'b_endpoint': b_endpoint,
            'metadata': {
                'fixture': True,
                'wavelengths_nm': [str(wavelength_nm)],
            },
        },
    )
    strand, _ = FiberStrand.objects.update_or_create(
        segment=segment,
        strand_index=1,
        defaults={
            'label': f'{name}.strand-1',
            'metadata': {
                'fixture': True,
                'wavelengths_nm': [str(wavelength_nm)],
            },
        },
    )
    StrandTermination.objects.update_or_create(
        mpo_position=a_position,
        defaults={
            'strand': strand,
            'mpo_endpoint': a_endpoint,
            'termination_index': 1,
            'label': f'{strand.label}:A',
            'metadata': {'fixture': True},
        },
    )
    StrandTermination.objects.update_or_create(
        mpo_position=b_position,
        defaults={
            'strand': strand,
            'mpo_endpoint': b_endpoint,
            'termination_index': 2,
            'label': f'{strand.label}:B',
            'metadata': {'fixture': True},
        },
    )
    return strand


def _optical_lane(
    *,
    fabric: Fabric,
    endpoint: Endpoint,
    channel: TransportChannel,
    plane: Plane,
    local_mpo_endpoint: Endpoint,
    local_mpo_position: ConnectorPosition,
    lane_index: int,
    local_mpo_index: int,
    direction: str,
    wavelength_nm: Decimal,
    pair_key: str,
) -> OpticalLane:
    lane, _ = OpticalLane.objects.update_or_create(
        endpoint=endpoint,
        lane_index=lane_index,
        direction=direction,
        defaults={
            'fabric': fabric,
            'channel': channel,
            'plane': plane,
            'local_mpo_endpoint': local_mpo_endpoint,
            'local_mpo_position': local_mpo_position,
            'local_mpo_index': local_mpo_index,
            'wavelength_nm': wavelength_nm,
            'pair_key': pair_key,
            'nominal_rate_gbps': 200,
            'metadata': {'fixture': True},
        },
    )
    return lane


def _path_summary(path: OpticalLanePath) -> dict:
    return {
        'path_found': path.path_found,
        'source_lane_id': path.source_lane_id,
        'destination_lane_id': path.destination_lane_id,
        'error': path.error,
        'steps': [
            {
                'step_type': step.step_type,
                'object_type': step.object_type,
                'object_id': step.object_id,
                'label': step.label,
                'metadata': step.metadata,
            }
            for step in path.steps
        ],
    }


def _managed_object_ids(fabric: Fabric) -> dict[str, list[int]]:
    return {
        'fabrics': [fabric.pk],
        'planes': list(
            Plane.objects.filter(fabric=fabric).order_by('plane_number').values_list('pk', flat=True)
        ),
        'nodes': list(
            FabricNode.objects.filter(fabric=fabric).order_by('address').values_list('pk', flat=True)
        ),
        'endpoints': list(
            Endpoint.objects.filter(fabric=fabric).order_by('address').values_list('pk', flat=True)
        ),
        'connector_positions': list(
            ConnectorPosition.objects.filter(endpoint__fabric=fabric)
            .order_by('endpoint__address', 'position_number')
            .values_list('pk', flat=True)
        ),
        'transport_channels': list(
            TransportChannel.objects.filter(fabric=fabric)
            .order_by('endpoint__address', 'channel_index')
            .values_list('pk', flat=True)
        ),
        'fiber_segments': list(
            FiberSegment.objects.filter(fabric=fabric).order_by('name').values_list('pk', flat=True)
        ),
        'fiber_strands': list(
            FiberStrand.objects.filter(segment__fabric=fabric)
            .order_by('segment__name', 'strand_index')
            .values_list('pk', flat=True)
        ),
        'strand_terminations': list(
            StrandTermination.objects.filter(strand__segment__fabric=fabric)
            .order_by('strand__segment__name', 'strand__strand_index', 'termination_index', 'pk')
            .values_list('pk', flat=True)
        ),
        'transfer_maps': list(
            TransferMap.objects.filter(fabric=fabric).order_by('pk').values_list('pk', flat=True)
        ),
        'optical_lanes': list(
            OpticalLane.objects.filter(fabric=fabric)
            .order_by('endpoint__address', 'lane_index', 'direction', 'pk')
            .values_list('pk', flat=True)
        ),
    }


def _managed_object_counts(managed_objects: dict[str, list[int]]) -> dict[str, int]:
    return {
        object_type: len(object_ids)
        for object_type, object_ids in managed_objects.items()
    }


@register_stamp_executor('roce_4plane_mini_proof')
def _execute_roce_4plane_mini_proof(context: StampExecutionContext) -> MiniFabricStampResult:
    fixture = context.fixture
    template = context.template
    template_spec = context.template_spec

    fabric_name = context.fabric_name
    fabric_slug = context.fabric_slug

    fabric, _ = Fabric.objects.update_or_create(
        slug=fabric_slug,
        defaults={
            'architecture': fixture.architecture,
            'name': fabric_name,
            'status': 'planned',
            'metadata': {
                'fixture': True,
                'template_slug': template.slug,
                'stamp_executor': _stamp_executor_name(template_spec),
                'netbox_cables': 'forbidden_for_modeled_fabric',
            },
        },
    )

    planes = {}
    for plane_number in template_spec['planes']:
        plane, _ = Plane.objects.update_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'label': f'Plane {plane_number}',
                'metadata': {'fixture': True},
            },
        )
        planes[plane_number] = plane

    gpu_tray = _node(
        fabric=fabric,
        role=fixture.roles['gpu_tray'],
        name='GB300-TRAY-1',
        address='GB300-TRAY-1',
        node_kind='active_device',
        local_index=1,
        source=_source_binding(context, 'nodes', 'GB300-TRAY-1'),
        metadata={'fixture': True},
    )

    gpu_osfps = {}
    gpu_mpos = {}
    gpu_spec = template_spec['gpu_tray']
    for osfp_index in range(1, gpu_spec['osfp_count'] + 1):
        osfp = _endpoint(
            fabric=fabric,
            node=gpu_tray,
            name=f'OSFP-{osfp_index}',
            address=f'{gpu_tray.address}.OSFP-{osfp_index}',
            endpoint_kind='plugin_port',
            connector_kind='osfp',
            source=_source_binding(context, 'endpoints', f'{gpu_tray.address}.OSFP-{osfp_index}'),
            metadata={'fixture': True, 'role_slug': 'gpu_osfp'},
        )
        gpu_osfps[osfp_index] = osfp
        for mpo_index in range(1, gpu_spec['mpo_per_osfp'] + 1):
            mpo = _endpoint(
                fabric=fabric,
                node=gpu_tray,
                parent=osfp,
                name=f'OSFP-{osfp_index}.MPO-{mpo_index}',
                address=f'{gpu_tray.address}.OSFP-{osfp_index}.MPO-{mpo_index}',
                endpoint_kind='subconnector',
                connector_kind='mpo-12',
                position_count=gpu_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'gpu_mpo', 'mpo_index': mpo_index},
            )
            gpu_mpos[(osfp_index, mpo_index)] = mpo

    shuffle_nodes = {}
    shuffle_front_mpos = {}
    shuffle_rear_mpos = {}
    shuffle_spec = template_spec['shuffle_cassettes']
    for cassette_index in range(1, shuffle_spec['count'] + 1):
        cassette = _node(
            fabric=fabric,
            role=fixture.roles['shuffle_cassette'],
            name=f'SHUFFLE-CASSETTE-{cassette_index}',
            address=f'SHUFFLE-CASSETTE-{cassette_index}',
            node_kind='passive_assembly',
            local_index=cassette_index,
            metadata={'fixture': True},
        )
        shuffle_nodes[cassette_index] = cassette
        for mpo_index in range(1, shuffle_spec['front_mpo_count'] + 1):
            shuffle_front_mpos[(cassette_index, mpo_index)] = _endpoint(
                fabric=fabric,
                node=cassette,
                name=f'FRONT.MPO-{mpo_index}',
                address=f'{cassette.address}.FRONT.MPO-{mpo_index}',
                endpoint_kind='connector',
                connector_kind='mpo-12',
                position_count=shuffle_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'shuffle_front_mpo', 'mpo_index': mpo_index},
            )
        for mpo_index in range(1, shuffle_spec['rear_mpo_count'] + 1):
            shuffle_rear_mpos[(cassette_index, mpo_index)] = _endpoint(
                fabric=fabric,
                node=cassette,
                name=f'REAR.MPO-{mpo_index}',
                address=f'{cassette.address}.REAR.MPO-{mpo_index}',
                endpoint_kind='connector',
                connector_kind='mpo-12',
                position_count=shuffle_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'shuffle_rear_mpo', 'mpo_index': mpo_index},
            )

    leaf_nodes = {}
    leaf_osfps = {}
    leaf_mpos = {}
    for leaf_index in range(1, template_spec['leaf_ports']['count'] + 1):
        leaf = _node(
            fabric=fabric,
            role=fixture.roles['leaf_switch'],
            name=f'LEAF-{leaf_index}',
            address=f'LEAF-{leaf_index}',
            node_kind='active_device',
            local_index=leaf_index,
            source=_source_binding(context, 'nodes', f'LEAF-{leaf_index}'),
            metadata={'fixture': True, 'plane_number': leaf_index},
        )
        leaf_nodes[leaf_index] = leaf
        osfp = _endpoint(
            fabric=fabric,
            node=leaf,
            name='OSFP-1',
            address=f'{leaf.address}.OSFP-1',
            endpoint_kind='plugin_port',
            connector_kind='osfp',
            source=_source_binding(context, 'endpoints', f'{leaf.address}.OSFP-1'),
            metadata={'fixture': True, 'role_slug': 'leaf_osfp'},
        )
        leaf_osfps[leaf_index] = osfp
        for mpo_index in range(1, gpu_spec['mpo_per_osfp'] + 1):
            leaf_mpos[(leaf_index, mpo_index)] = _endpoint(
                fabric=fabric,
                node=leaf,
                parent=osfp,
                name=f'OSFP-1.MPO-{mpo_index}',
                address=f'{leaf.address}.OSFP-1.MPO-{mpo_index}',
                endpoint_kind='subconnector',
                connector_kind='mpo-12',
                position_count=gpu_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'leaf_mpo', 'mpo_index': mpo_index},
            )

    source_lanes = []
    destination_lanes = []
    resolved_paths = []
    transfer_pattern = fixture.transfer_patterns['shuffle_2x2']

    for proof_path in template_spec['proof_paths']:
        plane_number = proof_path['plane']
        plane = planes[plane_number]
        wavelength_nm = DEFAULT_WAVELENGTHS_NM[plane_number]
        gpu_osfp = gpu_osfps[proof_path['gpu_osfp']]
        gpu_mpo = gpu_mpos[(proof_path['gpu_osfp'], 1)]
        cassette = shuffle_nodes[proof_path['cassette']]
        shuffle_front_mpo = shuffle_front_mpos[(proof_path['cassette'], 1)]
        shuffle_rear_mpo = shuffle_rear_mpos[(proof_path['cassette'], 1)]
        leaf_osfp = leaf_osfps[proof_path['leaf']]
        leaf_mpo = leaf_mpos[(proof_path['leaf'], 1)]

        gpu_position = _position(gpu_mpo, proof_path['front_position'])
        shuffle_front_position = _position(shuffle_front_mpo, proof_path['front_position'])
        shuffle_rear_position = _position(shuffle_rear_mpo, proof_path['rear_position'])
        leaf_position = _position(leaf_mpo, proof_path['rear_position'])

        pair_key = f'{fabric.slug}:plane-{plane_number}'
        gpu_channel = _channel(
            fabric=fabric,
            endpoint=gpu_osfp,
            plane=plane,
            name=f'GPU plane {plane_number}',
            channel_index=1,
        )
        leaf_channel = _channel(
            fabric=fabric,
            endpoint=leaf_osfp,
            plane=plane,
            name=f'Leaf plane {plane_number}',
            channel_index=1,
        )

        _fiber_strand(
            fabric=fabric,
            name=f'P{plane_number}-GPU-to-SHUFFLE',
            a_endpoint=gpu_mpo,
            a_position=gpu_position,
            b_endpoint=shuffle_front_mpo,
            b_position=shuffle_front_position,
            wavelength_nm=wavelength_nm,
        )
        TransferMap.objects.update_or_create(
            fabric=fabric,
            owner_node=cassette,
            src_position=shuffle_front_position,
            dst_position=shuffle_rear_position,
            defaults={
                'owner_segment': None,
                'pattern': transfer_pattern,
                'map_kind': 'shuffle_2x2',
                'bidirectional': True,
                'group_key': pair_key,
                'metadata': {
                    'fixture': True,
                    'plane_number': plane_number,
                    'wavelength_nm': str(wavelength_nm),
                },
            },
        )
        _fiber_strand(
            fabric=fabric,
            name=f'P{plane_number}-SHUFFLE-to-LEAF',
            a_endpoint=shuffle_rear_mpo,
            a_position=shuffle_rear_position,
            b_endpoint=leaf_mpo,
            b_position=leaf_position,
            wavelength_nm=wavelength_nm,
        )

        source_lane = _optical_lane(
            fabric=fabric,
            endpoint=gpu_osfp,
            channel=gpu_channel,
            plane=plane,
            local_mpo_endpoint=gpu_mpo,
            local_mpo_position=gpu_position,
            lane_index=1,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=wavelength_nm,
            pair_key=pair_key,
        )
        destination_lane = _optical_lane(
            fabric=fabric,
            endpoint=leaf_osfp,
            channel=leaf_channel,
            plane=plane,
            local_mpo_endpoint=leaf_mpo,
            local_mpo_position=leaf_position,
            lane_index=1,
            local_mpo_index=1,
            direction='receive',
            wavelength_nm=wavelength_nm,
            pair_key=pair_key,
        )
        path = resolve_optical_lane_path(source=source_lane, destination=destination_lane)

        source_lanes.append(source_lane)
        destination_lanes.append(destination_lane)
        resolved_paths.append(path)

    managed_objects = _managed_object_ids(fabric)
    stamp_run = StampRun.objects.create(
        template=template,
        fabric=fabric,
        status='completed',
        parameters={
            'fabric_name': fabric_name,
            'fabric_slug': fabric_slug,
            'template_slug': template.slug,
            'executor': _stamp_executor_name(template_spec),
            'source_binding_counts': {
                'nodes': len(context.source_bindings.get('nodes') or {}),
                'endpoints': len(context.source_bindings.get('endpoints') or {}),
            },
        },
        result={
            'fabric_id': fabric.pk,
            'managed_objects': managed_objects,
            'object_counts': _managed_object_counts(managed_objects),
            'source_lane_ids': [lane.pk for lane in source_lanes],
            'destination_lane_ids': [lane.pk for lane in destination_lanes],
            'resolved_paths': [_path_summary(path) for path in resolved_paths],
        },
        metadata={'fixture': True},
    )

    return MiniFabricStampResult(
        fabric=fabric,
        stamp_run=stamp_run,
        source_lanes=tuple(source_lanes),
        destination_lanes=tuple(destination_lanes),
        resolved_paths=tuple(resolved_paths),
    )


@transaction.atomic
def execute_stamp_template(
    *,
    template,
    fabric_name: str,
    fabric_slug: str,
    source_bindings: dict | None = None,
) -> MiniFabricStampResult:
    fixture = ensure_roce_4plane_shuffle_architecture()
    if template.architecture_id and template.architecture_id != fixture.architecture.pk:
        raise ValueError('StampTemplate belongs to an unsupported architecture for this V2 runner.')

    template_spec = template.template or {}
    executor_name = _stamp_executor_name(template_spec)
    executor = HYBRID_STAMP_EXECUTORS.get(executor_name)
    if executor is None:
        raise ValueError(f'Unknown hybrid stamp executor: {executor_name!r}.')

    context = StampExecutionContext(
        fixture=fixture,
        template=template,
        template_spec=template_spec,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings or {},
    )
    return executor(context)


def stamp_roce_4plane_mini_fabric(
    *,
    fabric_name: str = 'RoCE 4-plane mini proof',
    fabric_slug: str = 'roce-4-plane-mini-proof',
) -> MiniFabricStampResult:
    fixture = ensure_roce_4plane_shuffle_architecture()
    return execute_stamp_template(
        template=fixture.stamp_template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
    )
