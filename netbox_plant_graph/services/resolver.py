from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q

from netbox_plant_graph.models import ConnectorPosition, FiberStrand, OpticalLane, StrandTermination, TransferMap
from netbox_plant_graph.services.audit import (
    record_audit_event,
    suppression_for_lane,
    suppression_for_path_hop,
)


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


@dataclass(frozen=True)
class LaneSuppressionDetail:
    object_type: str
    object_id: int
    suppressed: bool
    reason: str = ''
    suppression_rule_id: int | None = None


@dataclass(frozen=True)
class LaneDrilldownReport:
    source: dict
    requested_destination: dict | None
    resolved_destination: dict | None
    resolved_path: OpticalLanePath
    lane_suppression: LaneSuppressionDetail
    hop_suppressions: tuple[LaneSuppressionDetail, ...]
    blocking_hop_suppression: LaneSuppressionDetail | None


@dataclass(frozen=True)
class LaneCompareReport:
    baseline: dict
    candidate: dict
    baseline_path: OpticalLanePath
    candidate_path: OpticalLanePath
    checks: dict
    deltas: dict


@dataclass(frozen=True)
class BlastRadiusImpact:
    source_lane: dict
    destination_lane: dict | None
    resolved_path: OpticalLanePath
    impacted: bool
    reason: str


@dataclass(frozen=True)
class BlastRadiusReport:
    unavailable: dict
    impacted_lanes: tuple[dict, ...]
    impacted_entities: dict
    impacts: tuple[BlastRadiusImpact, ...]


@dataclass(frozen=True)
class PathResolverRow:
    source_lane: dict
    destination_lane: dict | None
    resolved_path: OpticalLanePath
    path_found: bool
    error: str


def _lane_context(lane: OpticalLane | None) -> dict | None:
    if lane is None:
        return None
    return {
        'lane_id': lane.pk,
        'fabric_id': lane.fabric_id,
        'plane_id': lane.plane_id,
        'endpoint_id': lane.endpoint_id,
        'endpoint_label': str(lane.endpoint),
        'local_mpo_endpoint_id': lane.local_mpo_endpoint_id,
        'local_mpo_position_id': lane.local_mpo_position_id,
        'lane_index': lane.lane_index,
        'local_mpo_index': lane.local_mpo_index,
        'direction': lane.direction,
        'wavelength_nm': str(lane.wavelength_nm),
        'pair_key': lane.pair_key,
    }


def _path_hop_suppressions(*, lane: OpticalLane, path: OpticalLanePath) -> tuple[LaneSuppressionDetail, ...]:
    suppression_details = []
    for step in path.steps:
        if step.object_type not in {'transfer_map', 'fiber_strand', 'connector_position'}:
            continue
        hop_suppression = suppression_for_path_hop(
            lane=lane,
            object_type=step.object_type,
            object_id=step.object_id,
        )
        suppression_details.append(
            LaneSuppressionDetail(
                object_type=step.object_type,
                object_id=step.object_id,
                suppressed=hop_suppression.suppressed,
                reason=hop_suppression.reason,
                suppression_rule_id=getattr(hop_suppression.rule, 'pk', None),
            )
        )
    return tuple(suppression_details)


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
    actor=None,
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

    lane_suppression = suppression_for_lane(source)
    if lane_suppression.suppressed:
        path = OpticalLanePath(
            path_found=False,
            source_lane_id=source.pk,
            destination_lane_id=getattr(destination, 'pk', None),
            steps=(
                _lane_step(source, 'source_lane'),
            ),
            error=lane_suppression.reason,
        )
        record_audit_event(
            event_type='path_resolve',
            fabric=source.fabric,
            actor=actor,
            subject=source,
            outcome='suppressed',
            message=lane_suppression.reason,
            payload={
                'source_lane_id': source.pk,
                'destination_lane_id': getattr(destination, 'pk', None),
                'suppression_rule_id': getattr(lane_suppression.rule, 'pk', None),
            },
        )
        return path

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
                resolved_path = OpticalLanePath(
                    path_found=True,
                    source_lane_id=source.pk,
                    destination_lane_id=destination_lane.pk,
                    steps=steps
                    + (
                        _position_step(position, 'destination_position'),
                        _lane_step(destination_lane, 'destination_lane'),
                    ),
                )
                for step in resolved_path.steps:
                    if step.object_type not in {'transfer_map', 'fiber_strand', 'connector_position'}:
                        continue
                    hop_suppression = suppression_for_path_hop(
                        lane=source,
                        object_type=step.object_type,
                        object_id=step.object_id,
                    )
                    if hop_suppression.suppressed:
                        blocked_path = OpticalLanePath(
                            path_found=False,
                            source_lane_id=source.pk,
                            destination_lane_id=destination_lane.pk,
                            steps=resolved_path.steps,
                            error=hop_suppression.reason,
                        )
                        record_audit_event(
                            event_type='path_resolve',
                            fabric=source.fabric,
                            actor=actor,
                            subject=source,
                            outcome='suppressed',
                            message=hop_suppression.reason,
                            payload={
                                'source_lane_id': source.pk,
                                'destination_lane_id': destination_lane.pk,
                                'suppression_rule_id': getattr(hop_suppression.rule, 'pk', None),
                            },
                        )
                        return blocked_path
                record_audit_event(
                    event_type='path_resolve',
                    fabric=source.fabric,
                    actor=actor,
                    subject=source,
                    outcome='ok',
                    message='Optical lane path resolved.',
                    payload={
                        'source_lane_id': source.pk,
                        'destination_lane_id': destination_lane.pk,
                        'step_count': len(resolved_path.steps),
                    },
                )
                return resolved_path

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

    unresolved = OpticalLanePath(
        path_found=False,
        source_lane_id=source.pk,
        destination_lane_id=destination_id,
        error='No compatible optical-lane path found.',
    )
    record_audit_event(
        event_type='path_resolve',
        fabric=source.fabric,
        actor=actor,
        subject=source,
        outcome='failed',
        message=unresolved.error,
        payload={
            'source_lane_id': source.pk,
            'destination_lane_id': destination_id,
        },
    )
    return unresolved


def build_lane_drilldown_report(
    *,
    source: OpticalLane,
    destination: OpticalLane | None = None,
    max_depth: int = 64,
    actor=None,
) -> LaneDrilldownReport:
    lane_suppression = suppression_for_lane(source)
    lane_suppression_detail = LaneSuppressionDetail(
        object_type='optical_lane',
        object_id=source.pk,
        suppressed=lane_suppression.suppressed,
        reason=lane_suppression.reason,
        suppression_rule_id=getattr(lane_suppression.rule, 'pk', None),
    )
    resolved_path = resolve_optical_lane_path(
        source=source,
        destination=destination,
        max_depth=max_depth,
        actor=actor,
    )
    hop_suppressions = _path_hop_suppressions(lane=source, path=resolved_path)
    blocking_hop_suppression = next((row for row in hop_suppressions if row.suppressed), None)
    resolved_destination = None
    if resolved_path.destination_lane_id:
        resolved_destination = OpticalLane.objects.filter(pk=resolved_path.destination_lane_id).first()
    return LaneDrilldownReport(
        source=_lane_context(source),
        requested_destination=_lane_context(destination),
        resolved_destination=_lane_context(resolved_destination),
        resolved_path=resolved_path,
        lane_suppression=lane_suppression_detail,
        hop_suppressions=hop_suppressions,
        blocking_hop_suppression=blocking_hop_suppression,
    )


def _step_signature(path: OpticalLanePath) -> tuple[tuple[str, int], ...]:
    return tuple(
        (step.object_type, step.object_id)
        for step in path.steps
        if step.object_type in {'connector_position', 'fiber_strand', 'transfer_map'}
    )


def compare_optical_lane_paths(
    *,
    baseline: OpticalLane,
    candidate: OpticalLane,
    max_depth: int = 64,
    actor=None,
) -> LaneCompareReport:
    baseline_report = build_lane_drilldown_report(
        source=baseline,
        max_depth=max_depth,
        actor=actor,
    )
    candidate_report = build_lane_drilldown_report(
        source=candidate,
        max_depth=max_depth,
        actor=actor,
    )
    baseline_signature = set(_step_signature(baseline_report.resolved_path))
    candidate_signature = set(_step_signature(candidate_report.resolved_path))
    return LaneCompareReport(
        baseline=baseline_report.source,
        candidate=candidate_report.source,
        baseline_path=baseline_report.resolved_path,
        candidate_path=candidate_report.resolved_path,
        checks={
            'wavelength_match': _same_wavelength(baseline.wavelength_nm, candidate.wavelength_nm),
            'plane_match': baseline.plane_id == candidate.plane_id,
            'endpoint_match': baseline.endpoint_id == candidate.endpoint_id,
            'direction_match': baseline.direction == candidate.direction,
        },
        deltas={
            'reachability_delta': int(candidate_report.resolved_path.path_found) - int(baseline_report.resolved_path.path_found),
            'step_count_delta': len(candidate_report.resolved_path.steps) - len(baseline_report.resolved_path.steps),
            'destination_changed': baseline_report.resolved_path.destination_lane_id != candidate_report.resolved_path.destination_lane_id,
            'shared_hop_count': len(baseline_signature & candidate_signature),
            'baseline_only_hops': tuple(sorted(baseline_signature - candidate_signature)),
            'candidate_only_hops': tuple(sorted(candidate_signature - baseline_signature)),
        },
    )


def _normalize_unavailable_target(*, unavailable) -> tuple[str, int, int, str]:
    if isinstance(unavailable, OpticalLane):
        return ('optical_lane', unavailable.pk, unavailable.fabric_id, str(unavailable))
    if isinstance(unavailable, ConnectorPosition):
        return ('connector_position', unavailable.pk, unavailable.endpoint.fabric_id, str(unavailable))
    if isinstance(unavailable, FiberStrand):
        return ('fiber_strand', unavailable.pk, unavailable.segment.fabric_id, str(unavailable))
    if isinstance(unavailable, TransferMap):
        return ('transfer_map', unavailable.pk, unavailable.fabric_id, str(unavailable))
    raise ValueError('Unsupported unavailable object type for blast-radius analysis.')


def _path_contains_unavailable(
    *,
    path: OpticalLanePath,
    source_lane: OpticalLane,
    unavailable_type: str,
    unavailable_id: int,
) -> tuple[bool, str]:
    if unavailable_type == 'optical_lane':
        if source_lane.pk == unavailable_id:
            return (True, 'source_lane_unavailable')
        if path.destination_lane_id == unavailable_id:
            return (True, 'destination_lane_unavailable')
    for step in path.steps:
        if step.object_type == unavailable_type and step.object_id == unavailable_id:
            return (True, f'{unavailable_type}_unavailable')
    return (False, '')


def compute_unavailability_blast_radius(
    *,
    unavailable,
    max_depth: int = 64,
    actor=None,
) -> BlastRadiusReport:
    unavailable_type, unavailable_id, fabric_id, label = _normalize_unavailable_target(unavailable=unavailable)
    source_lanes = list(
        OpticalLane.objects.filter(fabric_id=fabric_id, direction=SEND)
        .select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
        .order_by('pk')
    )
    impacts = []
    impacted_lane_ids = set()
    impacted_endpoint_ids = set()
    impacted_position_ids = set()
    impacted_strand_ids = set()
    impacted_transfer_map_ids = set()

    for source_lane in source_lanes:
        resolved_path = resolve_optical_lane_path(
            source=source_lane,
            max_depth=max_depth,
            actor=actor,
        )
        impacted, reason = _path_contains_unavailable(
            path=resolved_path,
            source_lane=source_lane,
            unavailable_type=unavailable_type,
            unavailable_id=unavailable_id,
        )
        if not impacted:
            continue

        impacted_lane_ids.add(source_lane.pk)
        impacted_endpoint_ids.update({source_lane.endpoint_id, source_lane.local_mpo_endpoint_id})
        impacted_position_ids.add(source_lane.local_mpo_position_id)
        if resolved_path.destination_lane_id:
            impacted_lane_ids.add(resolved_path.destination_lane_id)

        for step in resolved_path.steps:
            if step.object_type == 'connector_position':
                impacted_position_ids.add(step.object_id)
            elif step.object_type == 'fiber_strand':
                impacted_strand_ids.add(step.object_id)
            elif step.object_type == 'transfer_map':
                impacted_transfer_map_ids.add(step.object_id)

        impacts.append({
            'source_lane_id': source_lane.pk,
            'destination_lane_id': resolved_path.destination_lane_id,
            'resolved_path': resolved_path,
            'reason': reason,
        })

    impacted_lanes = list(
        OpticalLane.objects.filter(pk__in=impacted_lane_ids)
        .select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
        .order_by('pk')
    )
    lane_lookup = {lane.pk: lane for lane in impacted_lanes}
    impact_rows = []
    for impact in impacts:
        source_lane = lane_lookup.get(impact['source_lane_id'])
        destination_lane = lane_lookup.get(impact['destination_lane_id'])
        impact_rows.append(
            BlastRadiusImpact(
                source_lane=_lane_context(source_lane),
                destination_lane=_lane_context(destination_lane),
                resolved_path=impact['resolved_path'],
                impacted=True,
                reason=impact['reason'],
            )
        )

    return BlastRadiusReport(
        unavailable={
            'object_type': unavailable_type,
            'object_id': unavailable_id,
            'fabric_id': fabric_id,
            'label': label,
        },
        impacted_lanes=tuple(_lane_context(lane) for lane in impacted_lanes),
        impacted_entities={
            'endpoint_ids': tuple(sorted(impacted_endpoint_ids)),
            'connector_position_ids': tuple(sorted(impacted_position_ids)),
            'fiber_strand_ids': tuple(sorted(impacted_strand_ids)),
            'transfer_map_ids': tuple(sorted(impacted_transfer_map_ids)),
        },
        impacts=tuple(impact_rows),
    )


def build_path_resolver_matrix(
    *,
    source_lanes=None,
    fabric_id: int | None = None,
    destination: OpticalLane | None = None,
    max_depth: int = 64,
    actor=None,
) -> tuple[PathResolverRow, ...]:
    if source_lanes is None:
        queryset = OpticalLane.objects.filter(direction=SEND)
        if fabric_id is not None:
            queryset = queryset.filter(fabric_id=fabric_id)
        lanes = list(
            queryset.select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
            .order_by('pk')
        )
    elif hasattr(source_lanes, 'select_related'):
        lanes = list(
            source_lanes.select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
            .order_by('pk')
        )
    else:
        lanes = list(source_lanes)
        lanes.sort(key=lambda lane: lane.pk)

    path_rows = []
    destination_lane_ids = set()
    for lane in lanes:
        resolved_path = resolve_optical_lane_path(
            source=lane,
            destination=destination,
            max_depth=max_depth,
            actor=actor,
        )
        if resolved_path.destination_lane_id:
            destination_lane_ids.add(resolved_path.destination_lane_id)
        path_rows.append({'lane': lane, 'path': resolved_path})

    destination_lookup = {
        lane.pk: lane
        for lane in OpticalLane.objects.filter(pk__in=destination_lane_ids)
        .select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
    }

    return tuple(
        PathResolverRow(
            source_lane=_lane_context(row['lane']),
            destination_lane=_lane_context(destination_lookup.get(row['path'].destination_lane_id)),
            resolved_path=row['path'],
            path_found=row['path'].path_found,
            error=row['path'].error,
        )
        for row in path_rows
    )
