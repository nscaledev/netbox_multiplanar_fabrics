from collections import defaultdict

from django.contrib.contenttypes.models import ContentType

from dcim.models import FrontPort, Interface, RearPort
from netbox_plant_graph.port_mapping_compat import PortMapping

from netbox_plant_graph.models import AttachmentUnit, Fabric, FineEdge, PlaneMembership, TransferMap

from ...breakout_profiles import get_breakout_profile_name_for_cable
from .audits import (
    _cable_requires_explicit_profile,
    _child_interface_count,
    _device_source_ids_for_fabric,
    _expected_profile_positions,
    _iter_path_positions,
    _profile_mapping_diagnostics,
    _profile_requires_child_interfaces,
    _relevant_cables_for_fabric,
)
from .payloads import UnresolvedCandidatePayload


def _normalize_plane_numbers(value) -> tuple[int, ...]:
    if value in (None, '', []):
        return ()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = (value,)
    plane_numbers = []
    for item in items:
        try:
            plane_numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(plane_numbers)))


def _plane_field_value(interface) -> tuple[int, ...]:
    return _normalize_plane_numbers((getattr(interface, 'custom_field_data', None) or {}).get('fabric_plane'))


def _attachment_plane_map(fabric: Fabric) -> dict[tuple[int, int], tuple[int, ...]]:
    attachment_units = list(
        AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric)
        .select_related('source_type')
    )
    attachment_ids = [attachment.pk for attachment in attachment_units]
    if not attachment_ids:
        return {}

    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    memberships = PlaneMembership.objects.filter(
        member_type=attachment_type,
        member_id__in=attachment_ids,
    ).select_related('plane')

    membership_by_attachment = defaultdict(set)
    for membership in memberships:
        membership_by_attachment[membership.member_id].add(membership.plane.plane_number)

    plane_map = {}
    for attachment in attachment_units:
        if not attachment.source_type_id or attachment.source_id is None:
            continue
        plane_map[(attachment.source_type_id, attachment.source_id)] = tuple(
            sorted(membership_by_attachment.get(attachment.pk, set()))
        )
    return plane_map


def _plane_numbers_for_object(obj, plane_map: dict[tuple[int, int], tuple[int, ...]]) -> tuple[int, ...]:
    if obj is None:
        return ()

    if isinstance(obj, Interface):
        child_planes = set()
        for child_interface in Interface.objects.filter(parent_id=obj.pk).only('custom_field_data'):
            child_planes.update(_plane_field_value(child_interface))
        if child_planes:
            return tuple(sorted(child_planes))

    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    return plane_map.get((content_type.pk, obj.pk), ())


def _plane_numbers_for_cable(cable, plane_map: dict[tuple[int, int], tuple[int, ...]]) -> tuple[int, ...]:
    plane_numbers = set()
    for cable_termination in cable.terminations.all():
        plane_numbers.update(_plane_numbers_for_object(cable_termination.termination, plane_map))
    return tuple(sorted(plane_numbers))


def _object_anchor(obj, **extra) -> dict | None:
    if obj is None:
        return None
    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    anchor = {
        'app_label': content_type.app_label,
        'model': content_type.model,
        'object_id': obj.pk,
        'display': str(obj),
    }
    anchor.update({key: value for key, value in extra.items() if value not in (None, '', (), [], {})})
    return anchor


def _cable_terminals(cable) -> tuple[dict, ...]:
    terminals = []
    for cable_termination in cable.terminations.all():
        termination = cable_termination.termination
        terminals.append({
            'termination': _object_anchor(
                termination,
                cable_end=cable_termination.cable_end,
                positions=tuple(_iter_path_positions(termination)),
            ),
        })
    return tuple(sorted(terminals, key=lambda item: (
        item['termination']['app_label'],
        item['termination']['model'],
        item['termination']['object_id'],
    )))


def _missing_cable_profile_candidate(*, fabric, cable, plane_map):
    plane_ids = _plane_numbers_for_cable(cable, plane_map)
    terminals = _cable_terminals(cable)
    return UnresolvedCandidatePayload(
        fabric_id=fabric.pk,
        summary_kind='segment',
        cause_code='missing_cable_profile',
        scope_object=cable,
        scope_label=str(cable),
        owner_object=cable,
        representative_object=cable,
        plane_ids=plane_ids,
        selector={
            'unresolved_reason': 'missing_cable_profile',
            'terminal_count': cable.terminations.count(),
            'terminals': terminals,
        },
        anchors={
            'left_anchor': terminals[0]['termination'] if terminals else None,
            'right_anchor': terminals[1]['termination'] if len(terminals) > 1 else None,
        },
        impact={
            'affected_attachment_units': len(plane_ids) or cable.terminations.count(),
            'affected_planes': len(plane_ids),
            'missing_positions': (),
        },
        metadata={
            'cable_id': cable.pk,
            'profile': get_breakout_profile_name_for_cable(cable),
        },
    )


def _child_interface_positions(interface) -> tuple[int, ...]:
    child_interfaces = list(Interface.objects.filter(parent_id=interface.pk).order_by('name', 'pk'))
    positions = []
    for ordinal, child_interface in enumerate(child_interfaces, start=1):
        child_plane_positions = _plane_field_value(child_interface)
        positions.append(child_plane_positions[0] if len(child_plane_positions) == 1 else ordinal)
    return tuple(sorted(set(positions)))


def _child_interface_candidate(*, fabric, interface, cable, cause_code, expected_child_count, actual_child_count, plane_map):
    expected_positions = tuple(range(1, expected_child_count + 1))
    known_positions = _child_interface_positions(interface)
    missing_positions = tuple(position for position in expected_positions if position not in set(known_positions))
    plane_ids = _plane_numbers_for_object(interface, plane_map) or known_positions

    return UnresolvedCandidatePayload(
        fabric_id=fabric.pk,
        summary_kind='lane_group',
        cause_code=cause_code,
        scope_object=interface,
        scope_label=str(interface),
        owner_object=interface,
        representative_object=interface,
        plane_ids=tuple(sorted(set(int(position) for position in plane_ids if position not in (None, '')))),
        selector={
            'expected_position_set': expected_positions,
            'known_child_positions': known_positions,
            'missing_position_set': missing_positions,
            'expected_child_count': expected_child_count,
            'known_child_count': actual_child_count,
            'channel_count': expected_child_count,
            'cable_id': cable.pk,
        },
        anchors={},
        impact={
            'affected_attachment_units': len(missing_positions) or expected_child_count,
            'affected_planes': len(plane_ids),
            'expected_lane_total': expected_child_count,
            'present_lane_total': actual_child_count,
            'mapped_lane_total': actual_child_count,
            'missing_positions': missing_positions or expected_positions,
        },
        metadata={
            'cable_id': cable.pk,
            'profile': get_breakout_profile_name_for_cable(cable),
        },
    )


def _peer_termination_for_cable(cable, termination):
    termination_type_id = ContentType.objects.get_for_model(termination, for_concrete_model=False).pk
    peer_termination = None
    for cable_termination in cable.terminations.all():
        if cable_termination.termination_id == termination.pk and cable_termination.termination_type_id == termination_type_id:
            continue
        peer_termination = cable_termination.termination
        break
    return peer_termination


def _missing_port_mapping_candidate(*, fabric, cable, termination, position, mapping_side, plane_map):
    normalized_position = position or 1
    plane_ids = _plane_numbers_for_object(termination, plane_map) or _plane_numbers_for_cable(cable, plane_map)
    peer_termination = _peer_termination_for_cable(cable, termination)

    return UnresolvedCandidatePayload(
        fabric_id=fabric.pk,
        summary_kind='segment',
        cause_code='missing_port_mapping',
        scope_object=termination,
        scope_label=str(termination),
        owner_object=termination,
        representative_object=termination,
        plane_ids=plane_ids,
        selector={
            'mapping_side': mapping_side,
            'port_position': normalized_position,
            'cable_identity': {
                'app_label': cable._meta.app_label,
                'model': cable._meta.model_name,
                'object_id': cable.pk,
            },
        },
        anchors={
            'left_anchor': _object_anchor(termination, position=normalized_position, mapping_side=mapping_side),
            'right_anchor': _object_anchor(peer_termination),
        },
        impact={
            'affected_attachment_units': 1,
            'affected_planes': len(plane_ids),
            'missing_positions': (normalized_position,),
        },
        metadata={
            'cable_id': cable.pk,
            'mapping_side': mapping_side,
            'position': normalized_position,
        },
    )


def _profile_mapping_candidates(*, fabric, cable, diagnostics, plane_map):
    groups = defaultdict(list)
    for unresolved in diagnostics['unresolved_positions']:
        groups[(unresolved['cable_end'], unresolved['reason'], unresolved['detail'])].append(unresolved)

    candidates = []
    plane_ids = _plane_numbers_for_cable(cable, plane_map)
    for (cable_end, reason, detail), unresolved_group in groups.items():
        unresolved_group = sorted(
            unresolved_group,
            key=lambda item: (
                item['termination']._meta.label_lower,
                item['termination'].pk,
                item['position'] or 0,
            ),
        )
        position_set = tuple(item['position'] for item in unresolved_group)
        member_identities = tuple(
            _object_anchor(item['termination'])
            for item in unresolved_group
        )
        first_unresolved = unresolved_group[0]
        candidates.append(UnresolvedCandidatePayload(
            fabric_id=fabric.pk,
            summary_kind='segment',
            cause_code=reason,
            scope_object=cable,
            scope_label=str(cable),
            owner_object=cable,
            representative_object=cable,
            plane_ids=plane_ids,
            selector={
                'cable_end': cable_end,
                'unresolved_position_set': position_set,
                'unresolved_reason': reason,
                'mapped_detail': detail,
                'member_identities': member_identities,
            },
            anchors={
                'left_anchor': _object_anchor(
                    first_unresolved['termination'],
                    cable_end=cable_end,
                    position=first_unresolved['position'],
                ),
                'right_anchor': {
                    'mapped_detail': detail,
                } if detail else None,
            },
            impact={
                'affected_attachment_units': len(unresolved_group),
                'affected_planes': len(plane_ids),
                'unmatched_peer_positions': position_set,
                'missing_positions': position_set,
                'mapped_lane_total': diagnostics['matched_positions'],
            },
            metadata={
                'cable_id': cable.pk,
                'profile': get_breakout_profile_name_for_cable(cable),
                'matched_positions': diagnostics['matched_positions'],
            },
        ))
    return tuple(candidates)


def _orphaned_attachment_candidates(*, fabric, plane_map):
    attachment_units = list(
        AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric, topology_role='child-interface')
        .select_related('source_type', 'termination_point__source_type')
    )
    connected_attachment_ids = set()
    for fine_edge in FineEdge.objects.filter(
        granularity='attachment_unit',
        a_au__termination_point__plant_node__fabric=fabric,
    ).only('a_au_id', 'b_au_id'):
        if fine_edge.a_au_id:
            connected_attachment_ids.add(fine_edge.a_au_id)
        if fine_edge.b_au_id:
            connected_attachment_ids.add(fine_edge.b_au_id)
    for transfer_map in TransferMap.objects.filter(owner_node__fabric=fabric).only('src_attachment_unit_id', 'dst_attachment_unit_id'):
        connected_attachment_ids.add(transfer_map.src_attachment_unit_id)
        connected_attachment_ids.add(transfer_map.dst_attachment_unit_id)

    candidates = []
    for attachment_unit in attachment_units:
        if attachment_unit.pk in connected_attachment_ids:
            continue
        owner_object = attachment_unit.source or attachment_unit.termination_point.source or attachment_unit.termination_point
        plane_ids = plane_map.get((attachment_unit.source_type_id, attachment_unit.source_id), ())
        candidates.append(UnresolvedCandidatePayload(
            fabric_id=fabric.pk,
            summary_kind='lane_group',
            cause_code='orphaned_attachment_unit',
            scope_object=owner_object,
            scope_label=str(owner_object),
            owner_object=owner_object,
            representative_object=owner_object,
            plane_ids=plane_ids,
            selector={
                'attachment_ordinal': attachment_unit.ordinal,
                'position': (attachment_unit.metadata or {}).get('position'),
                'topology_role': attachment_unit.topology_role,
            },
            anchors={},
            impact={
                'affected_attachment_units': 1,
                'affected_planes': len(plane_ids),
                'present_lane_total': attachment_unit.signal_lanes.count(),
                'mapped_lane_total': 0,
            },
            metadata={
                'termination_point_id': attachment_unit.termination_point_id,
                'attachment_unit_name': attachment_unit.name,
            },
        ))
    return tuple(candidates)


def collect_unresolved_candidates(*, fabric, build_run=None) -> tuple[UnresolvedCandidatePayload, ...]:
    if not isinstance(fabric, Fabric):
        fabric = Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()
    if fabric is None:
        return ()

    plane_map = _attachment_plane_map(fabric)
    candidates = []
    missing_port_mapping_positions = set()
    device_source_ids = _device_source_ids_for_fabric(fabric)
    front_mapping_positions = set()
    rear_mapping_positions = set()
    if device_source_ids:
        port_mappings = PortMapping.objects.filter(device_id__in=device_source_ids)
        front_mapping_positions = set(port_mappings.values_list('front_port_id', 'front_port_position'))
        rear_mapping_positions = set(port_mappings.values_list('rear_port_id', 'rear_port_position'))

    for cable in _relevant_cables_for_fabric(fabric):
        profile_diagnostics = _profile_mapping_diagnostics(cable)
        if profile_diagnostics is not None and profile_diagnostics['unresolved_positions']:
            candidates.extend(_profile_mapping_candidates(
                fabric=fabric,
                cable=cable,
                diagnostics=profile_diagnostics,
                plane_map=plane_map,
            ))

        for cable_termination in cable.terminations.all():
            termination = cable_termination.termination
            if not isinstance(termination, (FrontPort, RearPort)):
                continue
            for position in _iter_path_positions(termination):
                normalized_position = position or 1
                finding_key = (termination._meta.model_name, termination.pk, normalized_position)
                if isinstance(termination, FrontPort):
                    lookup_exists = (termination.pk, normalized_position) in front_mapping_positions
                    mapping_side = 'front'
                else:
                    lookup_exists = (termination.pk, normalized_position) in rear_mapping_positions
                    mapping_side = 'rear'
                if lookup_exists or finding_key in missing_port_mapping_positions:
                    continue
                missing_port_mapping_positions.add(finding_key)
                candidates.append(_missing_port_mapping_candidate(
                    fabric=fabric,
                    cable=cable,
                    termination=termination,
                    position=normalized_position,
                    mapping_side=mapping_side,
                    plane_map=plane_map,
                ))

        if _cable_requires_explicit_profile(cable):
            candidates.append(_missing_cable_profile_candidate(
                fabric=fabric,
                cable=cable,
                plane_map=plane_map,
            ))
            continue

        for cable_termination in cable.terminations.all():
            termination = cable_termination.termination
            if not isinstance(termination, Interface):
                continue
            if not _profile_requires_child_interfaces(termination):
                continue
            expected_child_count = len(_expected_profile_positions(termination))
            actual_child_count = _child_interface_count(termination)
            if actual_child_count == 0:
                candidates.append(_child_interface_candidate(
                    fabric=fabric,
                    interface=termination,
                    cable=cable,
                    cause_code='missing_child_interface',
                    expected_child_count=expected_child_count,
                    actual_child_count=actual_child_count,
                    plane_map=plane_map,
                ))
                continue
            if actual_child_count < expected_child_count:
                candidates.append(_child_interface_candidate(
                    fabric=fabric,
                    interface=termination,
                    cable=cable,
                    cause_code='incomplete_child_interface_set',
                    expected_child_count=expected_child_count,
                    actual_child_count=actual_child_count,
                    plane_map=plane_map,
                ))

    candidates.extend(_orphaned_attachment_candidates(fabric=fabric, plane_map=plane_map))
    return tuple(candidates)
