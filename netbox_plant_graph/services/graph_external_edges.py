from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, Fabric, FabricPlane, FineEdge, PlaneMembership, SignalLane


class GraphExternalEdgeError(ValueError):
    pass


@dataclass(frozen=True)
class GraphExternalSegmentSpec:
    a_endpoint: object
    b_endpoint: object
    plane_number: int | None = None
    lane_indexes: tuple[int, ...] | None = None
    plane_membership_granularity: str = 'attachment_unit'
    segment_role: str = 'fiber_segment'
    edge_key: str = ''
    cable_profile_name: str = ''
    source: object | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphExternalEdgeStampResult:
    coarse_edge: CoarseEdge | None = None
    fine_edge: FineEdge | None = None
    lane_edges: list[FineEdge] = field(default_factory=list)


@dataclass
class GraphExternalPathStampResult:
    path_key: str
    coarse_edges: list[CoarseEdge] = field(default_factory=list)
    fine_edges: list[FineEdge] = field(default_factory=list)


def resolve_attachment_unit(
    *,
    fabric: Fabric | None = None,
    plant_node_name: str,
    termination_point_name: str,
    ordinal: int = 1,
) -> AttachmentUnit:
    """Resolve a graph AttachmentUnit by its fabric/node/termination/ordinal address."""
    queryset = AttachmentUnit.objects.select_related('termination_point__plant_node').filter(
        termination_point__plant_node__name=plant_node_name,
        termination_point__name=termination_point_name,
        ordinal=ordinal,
    )
    if fabric is not None:
        queryset = queryset.filter(termination_point__plant_node__fabric=fabric)
    try:
        return queryset.get()
    except AttachmentUnit.DoesNotExist as exc:
        raise GraphExternalEdgeError(
            f'AttachmentUnit not found for node={plant_node_name!r}, '
            f'termination={termination_point_name!r}, ordinal={ordinal!r}.'
        ) from exc
    except AttachmentUnit.MultipleObjectsReturned as exc:
        raise GraphExternalEdgeError(
            f'AttachmentUnit address is ambiguous for node={plant_node_name!r}, '
            f'termination={termination_point_name!r}, ordinal={ordinal!r}; include fabric.'
        ) from exc


@transaction.atomic
def stamp_graph_external_edge(
    *,
    a_endpoint,
    b_endpoint,
    staging_key: str,
    fabric: Fabric | None = None,
    plane_number: int | None = None,
    lane_indexes: Iterable[int] | None = None,
    plane_membership_granularity: str = 'attachment_unit',
    path_key: str = '',
    segment_role: str = 'fiber_segment',
    cable_profile_name: str = '',
    source=None,
    replace_existing: bool = True,
    metadata: dict | None = None,
) -> GraphExternalEdgeStampResult:
    """
    Stamp one plugin-native external fiber segment.

    The segment is represented as one CoarseEdge between the endpoint
    TerminationPoints plus one attachment-unit FineEdge between the selected
    AttachmentUnits plus one signal-lane FineEdge per lane pair when both
    endpoints expose SignalLanes. No NetBox-native dcim.Cable rows are created.
    """
    if not staging_key:
        raise GraphExternalEdgeError('staging_key is required for graph external edge stamps.')
    if plane_number is None:
        raise GraphExternalEdgeError('plane_number is required for graph external edge stamps.')

    a_au = _coerce_attachment_unit(a_endpoint, fabric=fabric)
    b_au = _coerce_attachment_unit(b_endpoint, fabric=fabric)
    _validate_edge_endpoints(a_au, b_au, fabric=fabric)
    plane = _resolve_plane(fabric=fabric, fabric_id=a_au.termination_point.plant_node.fabric_id, plane_number=plane_number)
    normalized_lane_indexes = _normalize_lane_indexes(lane_indexes)
    if plane_membership_granularity not in {'attachment_unit', 'signal_lane'}:
        raise GraphExternalEdgeError('plane_membership_granularity must be "attachment_unit" or "signal_lane".')
    if plane_membership_granularity == 'attachment_unit':
        _assert_or_create_plane_membership(a_au, plane)
        _assert_or_create_plane_membership(b_au, plane)

    if replace_existing:
        _delete_managed_edges(edge_key=staging_key)

    common_metadata = {
        **(metadata or {}),
        'graph_external_edge_stamp': True,
        'staging_key': staging_key,
        'edge_key': staging_key,
        'path_key': path_key,
        'plane_id': plane.pk,
        'plane_number': plane.plane_number,
        'plane_membership_granularity': plane_membership_granularity,
        'segment_role': segment_role,
        'a_attachment_unit_id': a_au.pk,
        'b_attachment_unit_id': b_au.pk,
    }
    if normalized_lane_indexes is not None:
        common_metadata['lane_indexes'] = list(normalized_lane_indexes)
    source_fields = _generic_fk_fields('source', source)

    coarse_edge = CoarseEdge.objects.create(
        edge_type='cable',
        a_tp=a_au.termination_point,
        b_tp=b_au.termination_point,
        cable_profile_name=cable_profile_name,
        metadata=common_metadata,
        **source_fields,
    )
    fine_edge = FineEdge.objects.create(
        granularity='attachment_unit',
        edge_type='derived_cable_segment',
        a_au=a_au,
        b_au=b_au,
        parent_coarse_edge=coarse_edge,
        derived_from_profile=False,
        metadata=common_metadata,
    )
    lane_edges = _create_signal_lane_edges(
        a_au=a_au,
        b_au=b_au,
        plane=plane,
        parent_coarse_edge=coarse_edge,
        common_metadata=common_metadata,
        lane_indexes=normalized_lane_indexes,
        plane_membership_granularity=plane_membership_granularity,
    )
    return GraphExternalEdgeStampResult(coarse_edge=coarse_edge, fine_edge=fine_edge, lane_edges=lane_edges)


@transaction.atomic
def stamp_graph_external_path(
    *,
    path_key: str,
    segments: Iterable[GraphExternalSegmentSpec | dict],
    fabric: Fabric | None = None,
    replace_existing: bool = True,
    metadata: dict | None = None,
) -> GraphExternalPathStampResult:
    """
    Stamp a multi-segment plugin-native fiber path.

    Segments may be GraphExternalSegmentSpec instances or dictionaries using
    a_endpoint/a and b_endpoint/b keys. Endpoint dictionaries are resolved to
    AttachmentUnits by fabric, plant node name, termination point name, and
    ordinal.
    """
    if not path_key:
        raise GraphExternalEdgeError('path_key is required for graph external path stamps.')
    if replace_existing:
        _delete_managed_edges(path_key=path_key)

    result = GraphExternalPathStampResult(path_key=path_key)
    for index, raw_segment in enumerate(segments, start=1):
        segment = _normalize_segment(raw_segment, path_key=path_key, index=index)
        edge_result = stamp_graph_external_edge(
            a_endpoint=segment.a_endpoint,
            b_endpoint=segment.b_endpoint,
            staging_key=segment.edge_key,
            fabric=fabric,
            plane_number=segment.plane_number,
            lane_indexes=segment.lane_indexes,
            plane_membership_granularity=segment.plane_membership_granularity,
            path_key=path_key,
            segment_role=segment.segment_role,
            cable_profile_name=segment.cable_profile_name,
            source=segment.source,
            replace_existing=False,
            metadata={**(metadata or {}), **(segment.metadata or {})},
        )
        result.coarse_edges.append(edge_result.coarse_edge)
        result.fine_edges.append(edge_result.fine_edge)
        result.fine_edges.extend(edge_result.lane_edges)
    return result


def _normalize_segment(raw_segment, *, path_key: str, index: int) -> GraphExternalSegmentSpec:
    if isinstance(raw_segment, GraphExternalSegmentSpec):
        if raw_segment.edge_key:
            return raw_segment
        return GraphExternalSegmentSpec(
            a_endpoint=raw_segment.a_endpoint,
            b_endpoint=raw_segment.b_endpoint,
            plane_number=raw_segment.plane_number,
            lane_indexes=raw_segment.lane_indexes,
            plane_membership_granularity=raw_segment.plane_membership_granularity,
            segment_role=raw_segment.segment_role,
            edge_key=f'{path_key}:segment-{index:03d}',
            cable_profile_name=raw_segment.cable_profile_name,
            source=raw_segment.source,
            metadata=raw_segment.metadata,
        )
    if not isinstance(raw_segment, dict):
        raise GraphExternalEdgeError('Path segments must be dictionaries or GraphExternalSegmentSpec instances.')
    segment_role = raw_segment.get('segment_role') or raw_segment.get('role') or 'fiber_segment'
    plane_number = raw_segment.get('plane_number')
    plane = raw_segment.get('plane')
    if plane_number is None and plane is not None:
        plane_number = getattr(plane, 'plane_number', plane)
    return GraphExternalSegmentSpec(
        a_endpoint=raw_segment.get('a_endpoint') or raw_segment.get('a'),
        b_endpoint=raw_segment.get('b_endpoint') or raw_segment.get('b'),
        plane_number=int(plane_number) if plane_number is not None else None,
        lane_indexes=_normalize_lane_indexes(raw_segment.get('lane_indexes')),
        plane_membership_granularity=raw_segment.get('plane_membership_granularity', 'attachment_unit'),
        segment_role=segment_role,
        edge_key=raw_segment.get('edge_key') or f'{path_key}:segment-{index:03d}',
        cable_profile_name=raw_segment.get('cable_profile_name', ''),
        source=raw_segment.get('source'),
        metadata=raw_segment.get('metadata') or {},
    )


def _coerce_attachment_unit(endpoint, *, fabric: Fabric | None = None) -> AttachmentUnit:
    if isinstance(endpoint, AttachmentUnit):
        return endpoint
    if isinstance(endpoint, dict):
        if endpoint.get('attachment_unit') is not None:
            return _coerce_attachment_unit(endpoint['attachment_unit'], fabric=fabric)
        if endpoint.get('attachment_unit_id') is not None:
            queryset = AttachmentUnit.objects.select_related('termination_point__plant_node').filter(
                pk=endpoint['attachment_unit_id']
            )
            if fabric is not None:
                queryset = queryset.filter(termination_point__plant_node__fabric=fabric)
            try:
                return queryset.get()
            except AttachmentUnit.DoesNotExist as exc:
                raise GraphExternalEdgeError(f'AttachmentUnit id={endpoint["attachment_unit_id"]!r} not found.') from exc
        node_name = endpoint.get('plant_node_name') or endpoint.get('plant_node') or endpoint.get('node')
        termination_name = (
            endpoint.get('termination_point_name')
            or endpoint.get('termination_point')
            or endpoint.get('termination')
        )
        ordinal = int(endpoint.get('ordinal', 1))
        if node_name and termination_name:
            return resolve_attachment_unit(
                fabric=fabric,
                plant_node_name=node_name,
                termination_point_name=termination_name,
                ordinal=ordinal,
            )
    if isinstance(endpoint, (tuple, list)) and len(endpoint) in {2, 3}:
        return resolve_attachment_unit(
            fabric=fabric,
            plant_node_name=endpoint[0],
            termination_point_name=endpoint[1],
            ordinal=int(endpoint[2]) if len(endpoint) == 3 else 1,
        )
    raise GraphExternalEdgeError(f'Cannot resolve graph endpoint {endpoint!r} to an AttachmentUnit.')


def _validate_edge_endpoints(a_au: AttachmentUnit, b_au: AttachmentUnit, *, fabric: Fabric | None = None) -> None:
    a_fabric_id = a_au.termination_point.plant_node.fabric_id
    b_fabric_id = b_au.termination_point.plant_node.fabric_id
    if a_au.pk == b_au.pk:
        raise GraphExternalEdgeError('A graph external edge must connect two distinct AttachmentUnits.')
    if a_fabric_id != b_fabric_id:
        raise GraphExternalEdgeError('A graph external edge cannot cross fabrics.')
    if fabric is not None and a_fabric_id != fabric.pk:
        raise GraphExternalEdgeError('Endpoint fabric does not match the requested fabric.')
    if a_au.termination_point_id == b_au.termination_point_id:
        raise GraphExternalEdgeError('A graph external edge must connect two distinct TerminationPoints.')


def _resolve_plane(*, fabric: Fabric | None, fabric_id: int, plane_number: int) -> FabricPlane:
    queryset = FabricPlane.objects.filter(fabric_id=fabric_id, plane_number=plane_number)
    if fabric is not None:
        queryset = queryset.filter(fabric=fabric)
    try:
        return queryset.get()
    except FabricPlane.DoesNotExist as exc:
        raise GraphExternalEdgeError(
            f'FabricPlane not found for fabric_id={fabric_id!r}, plane_number={plane_number!r}.'
        ) from exc


def _assert_or_create_plane_membership(member, plane: FabricPlane) -> PlaneMembership:
    member_type = ContentType.objects.get_for_model(member.__class__)
    conflicting = PlaneMembership.objects.filter(
        plane__fabric=plane.fabric,
        member_type=member_type,
        member_id=member.pk,
    ).exclude(plane=plane).first()
    if conflicting is not None:
        raise GraphExternalEdgeError(
            f'{member.__class__.__name__} id={member.pk!r} is already assigned to '
            f'plane {conflicting.plane.plane_number}; cannot also assign it to plane {plane.plane_number}.'
        )
    membership, _ = PlaneMembership.objects.get_or_create(
        plane=plane,
        member_type=member_type,
        member_id=member.pk,
        membership_role='native',
        defaults={
            'metadata': {
                'graph_external_edge_stamp': True,
            },
        },
    )
    return membership


def _lanes_by_index(attachment_unit: AttachmentUnit) -> dict[int, SignalLane]:
    return {
        lane.lane_index: lane
        for lane in SignalLane.objects.filter(attachment_unit=attachment_unit).order_by('lane_index')
    }


def _create_signal_lane_edges(
    *,
    a_au: AttachmentUnit,
    b_au: AttachmentUnit,
    plane: FabricPlane,
    parent_coarse_edge: CoarseEdge,
    common_metadata: dict,
    lane_indexes: tuple[int, ...] | None = None,
    plane_membership_granularity: str = 'attachment_unit',
) -> list[FineEdge]:
    a_lanes = _lanes_by_index(a_au)
    b_lanes = _lanes_by_index(b_au)
    if not a_lanes and not b_lanes:
        return []
    if set(a_lanes) != set(b_lanes):
        raise GraphExternalEdgeError(
            f'SignalLane cardinality mismatch for AttachmentUnits {a_au.pk!r} and {b_au.pk!r}: '
            f'a_lanes={sorted(a_lanes)}, b_lanes={sorted(b_lanes)}.'
        )

    lane_edges = []
    selected_lane_indexes = lane_indexes or tuple(sorted(a_lanes))
    missing_a = sorted(set(selected_lane_indexes) - set(a_lanes))
    missing_b = sorted(set(selected_lane_indexes) - set(b_lanes))
    if missing_a or missing_b:
        raise GraphExternalEdgeError(
            f'SignalLane selection mismatch for AttachmentUnits {a_au.pk!r} and {b_au.pk!r}: '
            f'missing_a={missing_a}, missing_b={missing_b}.'
        )

    for lane_index in selected_lane_indexes:
        if plane_membership_granularity == 'signal_lane':
            _assert_or_create_plane_membership(a_lanes[lane_index], plane)
            _assert_or_create_plane_membership(b_lanes[lane_index], plane)
        metadata = {
            **common_metadata,
            'lane_index': lane_index,
            'strand_number': lane_index + 1,
        }
        lane_edges.append(FineEdge.objects.create(
            granularity='signal_lane',
            edge_type='derived_cable_segment',
            a_au=a_au,
            b_au=b_au,
            a_lane=a_lanes[lane_index],
            b_lane=b_lanes[lane_index],
            parent_coarse_edge=parent_coarse_edge,
            derived_from_profile=False,
            metadata=metadata,
        ))
    return lane_edges


def _normalize_lane_indexes(value) -> tuple[int, ...] | None:
    if value in (None, ''):
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(',') if part.strip()]
    else:
        parts = list(value)
    lane_indexes = tuple(sorted({int(part) for part in parts}))
    invalid = [index for index in lane_indexes if index < 0]
    if invalid:
        raise GraphExternalEdgeError(f'lane_indexes must be zero-based non-negative integers; got {invalid}.')
    return lane_indexes


def _delete_managed_edges(*, path_key: str = '', edge_key: str = '') -> None:
    if not path_key and not edge_key:
        return
    coarse_edge_ids = [
        edge.pk
        for edge in CoarseEdge.objects.all()
        if _managed_edge_matches(edge.metadata or {}, path_key=path_key, edge_key=edge_key)
    ]
    fine_edge_ids = [
        edge.pk
        for edge in FineEdge.objects.all()
        if _managed_edge_matches(edge.metadata or {}, path_key=path_key, edge_key=edge_key)
    ]
    if fine_edge_ids:
        FineEdge.objects.filter(pk__in=fine_edge_ids).delete()
    if coarse_edge_ids:
        CoarseEdge.objects.filter(pk__in=coarse_edge_ids).delete()


def _managed_edge_matches(metadata: dict, *, path_key: str = '', edge_key: str = '') -> bool:
    if metadata.get('graph_external_edge_stamp') is not True:
        return False
    if edge_key and metadata.get('edge_key') == edge_key:
        return True
    if path_key and metadata.get('path_key') == path_key:
        return True
    return False


def _generic_fk_fields(prefix: str, obj) -> dict[str, object]:
    if obj is None:
        return {f'{prefix}_type': None, f'{prefix}_id': None}
    return {
        f'{prefix}_type': ContentType.objects.get_for_model(obj, for_concrete_model=False),
        f'{prefix}_id': obj.pk,
    }
