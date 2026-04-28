from collections import defaultdict

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from dcim.models import FrontPort, Interface, RearPort

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, FabricPlane, FineEdge, LaneMap, PlaneMembership, PlantNode, SignalLane, TerminationPoint

from ...breakout_profiles import get_breakout_profile_for_cable, get_plugin_breakout_profile_for_cable
from ..netbox.adapters import build_object_reference
from .payloads import LaneSetAttachmentPayload, LaneSetPayload, ObjectReferencePayload
from .resolver import _normalize_attachment_targets


def _object_reference_payload(reference):
    return ObjectReferencePayload(
        app_label=reference['app_label'],
        model=reference['model'],
        pk=reference['pk'],
        display=reference['display'],
        registry_key=reference.get('registry_key'),
        url=reference.get('url'),
        path_resolver_url=reference.get('path_resolver_url'),
        blast_radius_url=reference.get('blast_radius_url'),
        lane_drilldown_url=reference.get('lane_drilldown_url'),
        lane_workspace_url=reference.get('lane_workspace_url'),
        signal_path_resolver_url=reference.get('signal_path_resolver_url'),
        signal_blast_radius_url=reference.get('signal_blast_radius_url'),
        health_url=reference.get('health_url'),
    )


def _expected_lane_count(attachment_unit: AttachmentUnit) -> int:
    if attachment_unit.topology_role == 'child-interface' and attachment_unit.speed_gbps:
        return max(1, int(round(attachment_unit.speed_gbps / 50)))
    return attachment_unit.signal_lanes.count()


def _lane_status(*, expected_lane_count: int, present_lane_count: int, mapped_lane_count: int, missing_lane_indexes: tuple[int, ...]) -> str:
    if expected_lane_count > 0 and present_lane_count == 0:
        return 'missing'
    if present_lane_count == 0:
        return 'empty'
    if mapped_lane_count == present_lane_count and not missing_lane_indexes:
        return 'complete'
    return 'partial'


def _lane_map_consistency(*, present_lane_total: int, mapped_lane_total: int, unmatched_peer_positions: tuple[int, ...]) -> str:
    if present_lane_total == 0:
        return 'empty'
    if mapped_lane_total == present_lane_total and not unmatched_peer_positions:
        return 'consistent'
    if mapped_lane_total == 0:
        return 'missing'
    return 'partial'


def _plane_consistency(members: tuple[LaneSetAttachmentPayload, ...]) -> str:
    if not members or all(not member.plane_ids for member in members):
        return 'unassigned'
    if any(len(member.plane_ids) > 1 for member in members):
        return 'mixed'
    return 'consistent'


def _attachment_units_from_parent_interface(interface: Interface) -> list[AttachmentUnit]:
    interface_type = ContentType.objects.get_for_model(interface, for_concrete_model=False)
    termination_point = TerminationPoint.objects.filter(
        source_type=interface_type,
        source_id=interface.pk,
    ).order_by('pk').first()
    if termination_point is None:
        return []

    child_units = list(
        AttachmentUnit.objects.filter(
            termination_point=termination_point,
            topology_role='child-interface',
        ).select_related('termination_point__plant_node')
    )
    if child_units:
        return child_units
    return list(
        AttachmentUnit.objects.filter(termination_point=termination_point).select_related('termination_point__plant_node')
    )


def _iter_path_positions(termination) -> tuple[int | None, ...]:
    cable_positions = tuple(getattr(termination, 'cable_positions', ()) or ())
    if cable_positions:
        return cable_positions
    if isinstance(termination, RearPort):
        return tuple(range(1, max(getattr(termination, 'positions', 1), 1) + 1))
    if isinstance(termination, FrontPort):
        return (1,)
    return (None,)


def _profile_position_summary(interface: Interface, attachment_units: list[AttachmentUnit]) -> tuple[set[int], tuple[int, ...]]:
    cable = getattr(interface, 'cable', None)
    profile = get_breakout_profile_for_cable(cable) if cable is not None else None
    if cable is None or profile is None or not interface.cable_end:
        return set(), ()

    left_positions = tuple(
        position
        for position in (getattr(interface, 'cable_positions', None) or ())
        if isinstance(position, int)
    )
    if not left_positions:
        left_positions = tuple(sorted(
            member.metadata.get('position')
            for member in attachment_units
            if isinstance(member.metadata.get('position'), int)
        ))

    if hasattr(profile, 'get_mapped_position'):
        peer_lookup = {}
        for peer in interface.link_peers:
            for peer_position in _iter_path_positions(peer):
                peer_lookup[(peer.cable_connector, peer_position)] = peer

        matched_positions = set()
        unmatched_positions = set()
        for left_position in left_positions:
            try:
                mapped_position = profile.get_mapped_position(
                    interface.cable_end,
                    interface.cable_connector,
                    left_position,
                )
            except (TypeError, ValueError):
                continue
            if mapped_position is None:
                continue
            if mapped_position in peer_lookup:
                matched_positions.add(left_position)
            else:
                unmatched_positions.add(left_position)
        return matched_positions, tuple(sorted(unmatched_positions))

    breakout_profile = get_plugin_breakout_profile_for_cable(cable)
    if breakout_profile is None:
        return set(), ()

    sorted_peers = sorted(interface.link_peers, key=lambda peer: (getattr(peer, 'name', ''), peer.pk))
    matched_positions = set()
    unmatched_positions = set()
    for left_position in left_positions:
        child_ordinal = breakout_profile.get_child_ordinal(left_position)
        if child_ordinal is None:
            continue
        if 0 <= child_ordinal < len(sorted_peers):
            matched_positions.add(left_position)
        else:
            unmatched_positions.add(left_position)
    return matched_positions, tuple(sorted(unmatched_positions))


def _attachment_units_from_coarse_edge(coarse_edge: CoarseEdge) -> list[AttachmentUnit]:
    attachment_ids = set()
    for fine_edge in coarse_edge.fine_edges.filter(granularity='attachment_unit').only('a_au_id', 'b_au_id'):
        if fine_edge.a_au_id:
            attachment_ids.add(fine_edge.a_au_id)
        if fine_edge.b_au_id:
            attachment_ids.add(fine_edge.b_au_id)
    queryset = AttachmentUnit.objects.filter(pk__in=attachment_ids).select_related('termination_point__plant_node')
    return list(queryset.order_by('termination_point__name', 'ordinal', 'pk'))


def _attachment_units_from_plane(plane: FabricPlane) -> list[AttachmentUnit]:
    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    attachment_ids = PlaneMembership.objects.filter(
        plane=plane,
        member_type=attachment_type,
    ).values_list('member_id', flat=True)
    return list(
        AttachmentUnit.objects.filter(pk__in=attachment_ids).select_related('termination_point__plant_node')
    )


def _build_lane_set(
    *,
    scope_kind: str,
    target,
    attachment_units: list[AttachmentUnit],
    plane=None,
    unmatched_peer_positions: tuple[int, ...] = (),
    mapped_lane_counts_by_attachment_id: dict[int, int] | None = None,
) -> LaneSetPayload:
    attachment_ids = [attachment_unit.pk for attachment_unit in attachment_units]
    lane_queryset = SignalLane.objects.filter(attachment_unit_id__in=attachment_ids).select_related(
        'attachment_unit__termination_point__plant_node'
    )
    lanes_by_attachment = defaultdict(list)
    lane_ids = []
    for signal_lane in lane_queryset.order_by('attachment_unit_id', 'lane_index', 'pk'):
        lanes_by_attachment[signal_lane.attachment_unit_id].append(signal_lane)
        lane_ids.append(signal_lane.pk)
    lane_id_set = set(lane_ids)

    mapped_lane_ids = set()
    for left_lane_id, right_lane_id in FineEdge.objects.filter(
        granularity='signal_lane'
    ).filter(
        Q(a_lane_id__in=lane_ids) | Q(b_lane_id__in=lane_ids)
    ).values_list('a_lane_id', 'b_lane_id'):
        if left_lane_id in lane_id_set:
            mapped_lane_ids.add(left_lane_id)
        if right_lane_id in lane_id_set:
            mapped_lane_ids.add(right_lane_id)
    for left_lane_id, right_lane_id in LaneMap.objects.filter(
        Q(src_lane_id__in=lane_ids) | Q(dst_lane_id__in=lane_ids)
    ).values_list('src_lane_id', 'dst_lane_id'):
        if left_lane_id in lane_id_set:
            mapped_lane_ids.add(left_lane_id)
        if right_lane_id in lane_id_set:
            mapped_lane_ids.add(right_lane_id)

    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    plane_ids_by_attachment = defaultdict(set)
    for member_id, plane_number in PlaneMembership.objects.filter(
        member_type=attachment_type,
        member_id__in=attachment_ids,
    ).values_list('member_id', 'plane__plane_number'):
        plane_ids_by_attachment[member_id].add(plane_number)

    members = []
    for attachment_unit in attachment_units:
        lanes = lanes_by_attachment.get(attachment_unit.pk, [])
        lane_indexes = tuple(sorted(lane.lane_index for lane in lanes))
        expected_lane_count = max(_expected_lane_count(attachment_unit), len(lane_indexes))
        missing_lane_indexes = tuple(index for index in range(expected_lane_count) if index not in lane_indexes)
        mapped_lane_count = sum(1 for lane in lanes if lane.pk in mapped_lane_ids)
        if mapped_lane_counts_by_attachment_id and attachment_unit.pk in mapped_lane_counts_by_attachment_id:
            mapped_lane_count = max(mapped_lane_count, mapped_lane_counts_by_attachment_id[attachment_unit.pk])
        plane_ids = tuple(sorted(plane_ids_by_attachment.get(attachment_unit.pk, set())))
        position = attachment_unit.metadata.get('position')
        members.append(
            LaneSetAttachmentPayload(
                attachment_unit=_object_reference_payload(build_object_reference(attachment_unit)),
                termination_point=_object_reference_payload(build_object_reference(attachment_unit.termination_point)),
                plant_node=_object_reference_payload(build_object_reference(attachment_unit.termination_point.plant_node)),
                expected_lane_count=expected_lane_count,
                present_lane_count=len(lane_indexes),
                mapped_lane_count=mapped_lane_count,
                lane_indexes=lane_indexes,
                missing_lane_indexes=missing_lane_indexes,
                plane_ids=plane_ids,
                topology_role=attachment_unit.topology_role or '',
                position=position if isinstance(position, int) else None,
                status=_lane_status(
                    expected_lane_count=expected_lane_count,
                    present_lane_count=len(lane_indexes),
                    mapped_lane_count=mapped_lane_count,
                    missing_lane_indexes=missing_lane_indexes,
                ),
            )
        )

    members = tuple(members)
    expected_lane_total = sum(member.expected_lane_count for member in members)
    present_lane_total = sum(member.present_lane_count for member in members)
    mapped_lane_total = sum(member.mapped_lane_count for member in members)
    plane_ids = tuple(sorted({plane_id for member in members for plane_id in member.plane_ids}))
    fabric = getattr(target, 'fabric', None)
    if isinstance(target, AttachmentUnit):
        fabric = target.termination_point.plant_node.fabric
    elif isinstance(target, PlantNode):
        fabric = target.fabric
    elif isinstance(target, CoarseEdge):
        fabric = target.a_tp.plant_node.fabric
    elif isinstance(target, Interface):
        fabric = attachment_units[0].termination_point.plant_node.fabric if attachment_units else None
    elif isinstance(target, FabricPlane):
        fabric = target.fabric

    return LaneSetPayload(
        scope_kind=scope_kind,
        target=_object_reference_payload(build_object_reference(target)),
        fabric=_object_reference_payload(build_object_reference(fabric)) if fabric is not None else None,
        plane=_object_reference_payload(build_object_reference(plane)) if plane is not None else None,
        attachment_units=members,
        total_attachment_units=len(members),
        expected_lane_total=expected_lane_total,
        present_lane_total=present_lane_total,
        mapped_lane_total=mapped_lane_total,
        missing_lane_total=max(expected_lane_total - present_lane_total, 0),
        unmatched_peer_positions=unmatched_peer_positions,
        plane_ids=plane_ids,
        lane_map_consistency=_lane_map_consistency(
            present_lane_total=present_lane_total,
            mapped_lane_total=mapped_lane_total,
            unmatched_peer_positions=unmatched_peer_positions,
        ),
        plane_consistency=_plane_consistency(members),
    )


def build_lane_set_for_attachment_unit(attachment_unit: AttachmentUnit) -> LaneSetPayload:
    attachment_unit = AttachmentUnit.objects.select_related('termination_point__plant_node').get(pk=attachment_unit.pk)
    return _build_lane_set(
        scope_kind='attachment_unit',
        target=attachment_unit,
        attachment_units=[attachment_unit],
    )


def build_lane_set_for_parent_interface(interface: Interface) -> LaneSetPayload:
    interface = Interface.objects.get(pk=interface.pk)
    attachment_units = _attachment_units_from_parent_interface(interface)
    matched_positions, unmatched_peer_positions = _profile_position_summary(interface, attachment_units)
    mapped_lane_counts_by_attachment_id = {
        member.pk: max(_expected_lane_count(member), member.signal_lanes.count())
        for member in attachment_units
        if member.metadata.get('position') in matched_positions
    }
    if not unmatched_peer_positions:
        unmatched_peer_positions = tuple(sorted(
            member.metadata.get('position')
            for member in attachment_units
            if isinstance(member.metadata.get('position'), int)
            and not FineEdge.objects.filter(
                granularity='attachment_unit',
                parent_coarse_edge__isnull=False,
            ).filter(
                Q(a_au=member) | Q(b_au=member)
            ).exists()
        ))
    return _build_lane_set(
        scope_kind='parent_interface',
        target=interface,
        attachment_units=attachment_units,
        unmatched_peer_positions=unmatched_peer_positions,
        mapped_lane_counts_by_attachment_id=mapped_lane_counts_by_attachment_id,
    )


def build_lane_set_for_coarse_edge(coarse_edge: CoarseEdge) -> LaneSetPayload:
    coarse_edge = CoarseEdge.objects.select_related('a_tp__plant_node__fabric', 'b_tp__plant_node').get(pk=coarse_edge.pk)
    return _build_lane_set(
        scope_kind='coarse_edge',
        target=coarse_edge,
        attachment_units=_attachment_units_from_coarse_edge(coarse_edge),
    )


def build_lane_set_for_plant_node(plant_node: PlantNode) -> LaneSetPayload:
    plant_node = PlantNode.objects.select_related('fabric').get(pk=plant_node.pk)
    attachment_units = list(
        AttachmentUnit.objects.filter(termination_point__plant_node=plant_node).select_related('termination_point__plant_node')
    )
    return _build_lane_set(
        scope_kind='plant_node',
        target=plant_node,
        attachment_units=attachment_units,
    )


def build_lane_set_for_plane(plane: FabricPlane) -> LaneSetPayload:
    plane = FabricPlane.objects.select_related('fabric').get(pk=plane.pk)
    return _build_lane_set(
        scope_kind='plane',
        target=plane,
        attachment_units=_attachment_units_from_plane(plane),
        plane=plane,
    )


def build_lane_set(target) -> LaneSetPayload:
    if isinstance(target, AttachmentUnit):
        return build_lane_set_for_attachment_unit(target)
    if isinstance(target, SignalLane):
        return build_lane_set_for_attachment_unit(target.attachment_unit)
    if isinstance(target, Interface):
        if target.parent_id:
            attachment_units = _normalize_attachment_targets(target)
            if attachment_units:
                return _build_lane_set(
                    scope_kind='attachment_unit',
                    target=target,
                    attachment_units=attachment_units,
                )
        return build_lane_set_for_parent_interface(target)
    if isinstance(target, CoarseEdge):
        return build_lane_set_for_coarse_edge(target)
    if isinstance(target, PlantNode):
        return build_lane_set_for_plant_node(target)
    if isinstance(target, FabricPlane):
        return build_lane_set_for_plane(target)
    attachment_units = _normalize_attachment_targets(target)
    return _build_lane_set(
        scope_kind='attachment_unit',
        target=target,
        attachment_units=attachment_units,
    )
