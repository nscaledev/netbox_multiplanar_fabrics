from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q

from netbox_plant_graph.models import ConnectorPosition, OpticalLane, StrandTermination, TransferMap


SEND = 'send'
RECEIVE = 'receive'
OPPOSITE_DIRECTION = {
    SEND: RECEIVE,
    RECEIVE: SEND,
}


@dataclass(frozen=True)
class PathStep:
    step_type: str
    object_type: str
    object_id: int
    label: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OpticalLanePath:
    path_found: bool
    source_lane_id: int
    destination_lane_id: int | None = None
    steps: tuple[PathStep, ...] = ()
    error: str = ''


def _lane_step(lane: OpticalLane, step_type: str) -> PathStep:
    return PathStep(
        step_type=step_type,
        object_type='optical_lane',
        object_id=lane.pk,
        label=str(lane),
        metadata={
            'endpoint_id': lane.endpoint_id,
            'local_mpo_endpoint_id': lane.local_mpo_endpoint_id,
            'local_mpo_position_id': lane.local_mpo_position_id,
            'lane_index': lane.lane_index,
            'direction': lane.direction,
            'wavelength_nm': str(lane.wavelength_nm),
            'plane_id': lane.plane_id,
            'channel_id': lane.channel_id,
            'pair_key': lane.pair_key,
        },
    )


def _position_step(position: ConnectorPosition, step_type: str) -> PathStep:
    return PathStep(
        step_type=step_type,
        object_type='connector_position',
        object_id=position.pk,
        label=str(position),
        metadata={
            'endpoint_id': position.endpoint_id,
            'position_number': position.position_number,
        },
    )


def _strand_step(edge: StrandTermination, peer: StrandTermination) -> PathStep:
    return PathStep(
        step_type='fiber_strand',
        object_type='fiber_strand',
        object_id=edge.strand_id,
        label=str(edge.strand),
        metadata={
            'from_termination_id': edge.pk,
            'to_termination_id': peer.pk,
            'from_position_id': edge.mpo_position_id,
            'to_position_id': peer.mpo_position_id,
            'segment_id': edge.strand.segment_id,
        },
    )


def _transfer_step(transfer_map: TransferMap) -> PathStep:
    return PathStep(
        step_type='transfer_map',
        object_type='transfer_map',
        object_id=transfer_map.pk,
        label=str(transfer_map),
        metadata={
            'map_kind': transfer_map.map_kind,
            'owner_node_id': transfer_map.owner_node_id,
            'owner_segment_id': transfer_map.owner_segment_id,
            'src_position_id': transfer_map.src_position_id,
            'dst_position_id': transfer_map.dst_position_id,
            'bidirectional': transfer_map.bidirectional,
            'group_key': transfer_map.group_key,
        },
    )


def _strand_neighbors(position: ConnectorPosition) -> list[tuple[ConnectorPosition, PathStep]]:
    neighbors = []
    terminations = StrandTermination.objects.filter(mpo_position=position).select_related('strand', 'strand__segment')
    for termination in terminations:
        peers = (
            termination.strand.terminations.exclude(pk=termination.pk)
            .select_related('mpo_position', 'mpo_position__endpoint')
            .order_by('termination_index', 'pk')
        )
        for peer in peers:
            neighbors.append((peer.mpo_position, _strand_step(termination, peer)))
    return neighbors


def _transfer_neighbors(position: ConnectorPosition, *, fabric_id: int) -> list[tuple[ConnectorPosition, PathStep]]:
    neighbors = []
    transfer_maps = (
        TransferMap.objects.filter(
            Q(src_position=position) | Q(dst_position=position, bidirectional=True),
            fabric_id=fabric_id,
        )
        .select_related('src_position', 'dst_position')
        .order_by('pk')
    )
    for transfer_map in transfer_maps:
        if transfer_map.src_position_id == position.pk:
            neighbors.append((transfer_map.dst_position, _transfer_step(transfer_map)))
        elif transfer_map.bidirectional:
            neighbors.append((transfer_map.src_position, _transfer_step(transfer_map)))
    return neighbors


def _neighbors(position: ConnectorPosition, *, fabric_id: int) -> list[tuple[ConnectorPosition, PathStep]]:
    return _strand_neighbors(position) + _transfer_neighbors(position, fabric_id=fabric_id)


def _same_wavelength(left: Decimal, right: Decimal) -> bool:
    return left == right


def _matching_destination_lanes(
    *,
    source: OpticalLane,
    position: ConnectorPosition,
    destination: OpticalLane | None,
) -> list[OpticalLane]:
    expected_direction = OPPOSITE_DIRECTION.get(source.direction)
    if expected_direction is None:
        return []

    queryset = OpticalLane.objects.filter(
        fabric_id=source.fabric_id,
        local_mpo_position_id=position.pk,
        direction=expected_direction,
    ).select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position')
    if destination is not None:
        queryset = queryset.filter(pk=destination.pk)

    matches = []
    for candidate in queryset:
        if candidate.pk == source.pk:
            continue
        if not _same_wavelength(candidate.wavelength_nm, source.wavelength_nm):
            continue
        if source.plane_id and candidate.plane_id and candidate.plane_id != source.plane_id:
            continue
        matches.append(candidate)
    return matches


def resolve_optical_lane_path(
    *,
    source: OpticalLane,
    destination: OpticalLane | None = None,
    max_depth: int = 64,
) -> OpticalLanePath:
    """
    Resolve an optical lane by walking the plugin-native plant graph.

    Optical lanes are endpoint-local signaling constructs. The path itself is
    computed from connector-position adjacency: fiber strands join terminated
    MPO positions, and transfer maps join positions inside passive assemblies.
    """
    if source.pk is None:
        return OpticalLanePath(path_found=False, source_lane_id=0, error='Source lane must be saved before resolving.')
    if source.local_mpo_position_id is None:
        return OpticalLanePath(
            path_found=False,
            source_lane_id=source.pk,
            error='Source lane must have a local MPO position before resolving.',
        )
    if destination is not None and destination.pk is None:
        return OpticalLanePath(
            path_found=False,
            source_lane_id=source.pk,
            error='Destination lane must be saved before resolving.',
        )

    destination_id = getattr(destination, 'pk', None)
    source_position = source.local_mpo_position
    initial_steps = (
        _lane_step(source, 'source_lane'),
        _position_step(source_position, 'source_position'),
    )
    queue = deque([(source_position, initial_steps, 0)])
    seen_positions = {source_position.pk}

    while queue:
        position, steps, depth = queue.popleft()

        if depth > 0:
            for destination_lane in _matching_destination_lanes(
                source=source,
                position=position,
                destination=destination,
            ):
                return OpticalLanePath(
                    path_found=True,
                    source_lane_id=source.pk,
                    destination_lane_id=destination_lane.pk,
                    steps=steps
                    + (
                        _position_step(position, 'destination_position'),
                        _lane_step(destination_lane, 'destination_lane'),
                    ),
                )

        if depth >= max_depth:
            continue

        for next_position, edge_step in _neighbors(position, fabric_id=source.fabric_id):
            if next_position.pk in seen_positions:
                continue
            seen_positions.add(next_position.pk)
            queue.append(
                (
                    next_position,
                    steps + (edge_step, _position_step(next_position, 'connector_position')),
                    depth + 1,
                )
            )

    return OpticalLanePath(
        path_found=False,
        source_lane_id=source.pk,
        destination_lane_id=destination_id,
        error='No compatible optical-lane path found.',
    )
