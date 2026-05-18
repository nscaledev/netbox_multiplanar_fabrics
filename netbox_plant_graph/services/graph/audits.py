from collections import defaultdict

from django.contrib.contenttypes.models import ContentType

from dcim.models import Cable, FrontPort, Interface, RearPort
from netbox_plant_graph.port_mapping_compat import PortMapping

from netbox_plant_graph.models import AttachmentUnit, Fabric, FineEdge, PlaneMembership, PlantNode, TransferMap

from ...breakout_profiles import (
    get_breakout_profile_for_cable,
    get_breakout_profile_name_for_cable,
    get_plugin_breakout_profile_for_cable,
)
from ..netbox.adapters import build_object_reference
from .policy_evaluator import build_policy_evaluation


def _scope_value(scope, name):
    if scope is None:
        return None
    if isinstance(scope, dict):
        return scope.get(name)
    return getattr(scope, name, None)


def _normalize_fabric(fabric=None, scope=None):
    fabric = fabric or _scope_value(scope, 'fabric')
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return Fabric.objects.order_by('pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _normalize_plane_set(fabric, plane_set):
    if plane_set:
        plane_ids = [getattr(plane, 'pk', plane) for plane in plane_set]
        return list(fabric.planes.filter(pk__in=plane_ids).order_by('plane_number'))
    return list(fabric.planes.order_by('plane_number'))


def _iter_path_positions(termination) -> tuple:
    cable_positions = tuple(getattr(termination, 'cable_positions', ()) or ())
    if cable_positions:
        return cable_positions
    if isinstance(termination, RearPort):
        return tuple(range(1, max(getattr(termination, 'positions', 1), 1) + 1))
    if isinstance(termination, FrontPort):
        return (1,)
    return (None,)


def _expected_profile_positions(termination) -> tuple:
    positions = _iter_path_positions(termination)
    if positions != (None,) or not isinstance(termination, Interface):
        return positions

    cable = getattr(termination, 'cable', None)
    breakout_profile = get_plugin_breakout_profile_for_cable(cable) if cable is not None else None
    if breakout_profile is not None and breakout_profile.child_count > 1:
        return tuple(range(1, breakout_profile.child_count + 1))
    return positions


def _cable_requires_explicit_profile(cable) -> bool:
    if get_breakout_profile_for_cable(cable) is not None:
        return False

    terminations_by_end = defaultdict(list)
    for cable_termination in cable.terminations.all():
        terminations_by_end[cable_termination.cable_end].append(cable_termination.termination)

    if len(terminations_by_end) != 2:
        return True

    sides = list(terminations_by_end.values())
    if any(len(side) != 1 for side in sides):
        return True

    return any(len(_expected_profile_positions(termination)) != 1 for side in sides for termination in side)


def _profile_mapping_diagnostics(cable):
    profile = get_breakout_profile_for_cable(cable)
    if profile is None:
        return None

    terminations_by_end = defaultdict(list)
    for cable_termination in cable.terminations.all():
        terminations_by_end[cable_termination.cable_end].append(cable_termination.termination)

    if len(terminations_by_end) != 2:
        return {
            'matched_positions': 0,
            'unresolved_positions': (),
        }

    if hasattr(profile, 'get_mapped_position'):
        position_maps = {}
        for cable_end, terminations in terminations_by_end.items():
            position_map = {}
            for termination in terminations:
                connector = getattr(termination, 'cable_connector', None)
                for position in _iter_path_positions(termination):
                    position_map[(connector, position)] = termination
            position_maps[cable_end] = position_map

        matched_positions = set()
        unresolved_positions = {}
        for cable_end, terminations in terminations_by_end.items():
            peer_end = 'B' if cable_end == 'A' else 'A'
            peer_position_map = position_maps.get(peer_end, {})
            for termination in terminations:
                for position in _iter_path_positions(termination):
                    unresolved_key = (cable_end, termination.pk, position)
                    try:
                        mapped_position = profile.get_mapped_position(
                            cable_end,
                            termination.cable_connector,
                            position,
                        )
                    except (TypeError, ValueError) as exc:
                        unresolved_positions[unresolved_key] = {
                            'cable_end': cable_end,
                            'termination': termination,
                            'position': position,
                            'reason': 'profile_error',
                            'detail': str(exc),
                        }
                        continue
                    if mapped_position is None:
                        unresolved_positions[unresolved_key] = {
                            'cable_end': cable_end,
                            'termination': termination,
                            'position': position,
                            'reason': 'profile_returned_none',
                            'detail': '',
                        }
                        continue
                    mapped_connector, peer_position = mapped_position
                    if (mapped_connector, peer_position) not in peer_position_map:
                        unresolved_positions[unresolved_key] = {
                            'cable_end': cable_end,
                            'termination': termination,
                            'position': position,
                            'reason': 'missing_peer_position',
                            'detail': f'{mapped_connector}:{peer_position}',
                        }
                        continue
                    matched_positions.add(unresolved_key)

        return {
            'matched_positions': len(matched_positions),
            'unresolved_positions': tuple(unresolved_positions.values()),
        }

    breakout_profile = get_plugin_breakout_profile_for_cable(cable)
    if breakout_profile is None:
        return None

    matched_positions = set()
    unresolved_positions = []
    for cable_end, terminations in terminations_by_end.items():
        peer_end = 'B' if cable_end == 'A' else 'A'
        peer_terminations = sorted(
            terminations_by_end.get(peer_end, ()),
            key=lambda item: (getattr(item, 'name', ''), item.pk),
        )
        for termination in terminations:
            raw_positions = _expected_profile_positions(termination)
            if len(peer_terminations) == 1:
                peer_termination = peer_terminations[0]
                peer_positions = set(_iter_path_positions(peer_termination))
                for position in raw_positions:
                    child_ordinal = breakout_profile.get_child_ordinal(position if position is not None else 1)
                    if child_ordinal is None:
                        unresolved_positions.append({
                            'cable_end': cable_end,
                            'termination': termination,
                            'position': position,
                            'reason': 'profile_returned_none',
                            'detail': '',
                        })
                        continue
                    peer_position = child_ordinal + 1
                    if peer_positions != {None} and peer_position not in peer_positions:
                        unresolved_positions.append({
                            'cable_end': cable_end,
                            'termination': termination,
                            'position': position,
                            'reason': 'missing_peer_position',
                            'detail': f'{getattr(peer_termination, "cable_connector", None)}:{peer_position}',
                        })
                        continue
                    matched_positions.add((cable_end, termination.pk, position))
                continue

            for position in raw_positions:
                child_ordinal = breakout_profile.get_child_ordinal(position if position is not None else 1)
                if child_ordinal is None:
                    unresolved_positions.append({
                        'cable_end': cable_end,
                        'termination': termination,
                        'position': position,
                        'reason': 'profile_returned_none',
                        'detail': '',
                    })
                    continue
                if not (0 <= child_ordinal < len(peer_terminations)):
                    unresolved_positions.append({
                        'cable_end': cable_end,
                        'termination': termination,
                        'position': position,
                        'reason': 'missing_peer_position',
                        'detail': f'ordinal:{child_ordinal}',
                    })
                    continue
                matched_positions.add((cable_end, termination.pk, position))

    return {
        'matched_positions': len(matched_positions),
        'unresolved_positions': tuple(unresolved_positions),
    }


def _relevant_cables_for_fabric(fabric):
    device_source_ids = _device_source_ids_for_fabric(fabric)
    if not device_source_ids:
        return ()

    relevant_cables = []
    cable_queryset = Cable.objects.all().prefetch_related('terminations__termination')
    for cable in cable_queryset:
        for cable_termination in cable.terminations.all():
            termination = cable_termination.termination
            if getattr(termination, 'device_id', None) in device_source_ids:
                relevant_cables.append(cable)
                break
    return tuple(relevant_cables)


def _device_source_ids_for_fabric(fabric):
    return set(
        PlantNode.objects.filter(fabric=fabric).exclude(source_id__isnull=True).values_list('source_id', flat=True)
    )


def _profile_requires_child_interfaces(interface) -> bool:
    return len(_expected_profile_positions(interface)) > 1


def _interface_has_explicit_child_interfaces(interface) -> bool:
    return Interface.objects.filter(parent_id=interface.pk).exists()


def _child_interface_count(interface) -> int:
    return Interface.objects.filter(parent_id=interface.pk).count()


def _make_finding(*, finding_type, severity, obj, message, metadata=None):
    return {
        'finding_type': finding_type,
        'severity': severity,
        'object': build_object_reference(obj),
        'message': message,
        'metadata': metadata or {},
    }


def run_plane_audit(*, fabric=None, plane_set=None, scope=None):
    fabric = _normalize_fabric(fabric=fabric, scope=scope)
    if fabric is None:
        return {
            'fabric': None,
            'plane_set': (),
            'scope': scope,
            'findings': [],
        }

    planes = _normalize_plane_set(fabric, plane_set)
    plane_ids = [plane.pk for plane in planes]
    attachment_units = list(
        AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric).select_related('termination_point__plant_node')
    )
    attachment_ids = [attachment_unit.pk for attachment_unit in attachment_units]
    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    memberships = list(
        PlaneMembership.objects.filter(member_type=attachment_type, member_id__in=attachment_ids, plane_id__in=plane_ids)
        .select_related('plane')
        .order_by('plane__plane_number')
    )

    membership_by_attachment = defaultdict(list)
    membership_count_by_plane = defaultdict(int)
    for membership in memberships:
        membership_by_attachment[membership.member_id].append(membership)
        membership_count_by_plane[membership.plane_id] += 1

    findings = []
    for attachment_unit in attachment_units:
        attached_memberships = membership_by_attachment.get(attachment_unit.pk, [])
        if not attached_memberships:
            findings.append(_make_finding(
                finding_type='missing_plane_membership',
                severity='warning',
                obj=attachment_unit,
                message='Attachment unit has no plane membership.',
            ))
            continue
        plane_numbers = {membership.plane.plane_number for membership in attached_memberships}
        if len(plane_numbers) > 1:
            findings.append(_make_finding(
                finding_type='multi_plane_attachment',
                severity='error',
                obj=attachment_unit,
                message='Attachment unit participates in multiple planes.',
                metadata={'plane_numbers': sorted(plane_numbers)},
            ))

    plane_sets_by_attachment = {
        attachment_unit.pk: {membership.plane_id for membership in membership_by_attachment.get(attachment_unit.pk, [])}
        for attachment_unit in attachment_units
    }
    connected_attachment_ids = set()
    for fine_edge in FineEdge.objects.filter(granularity='attachment_unit', a_au__termination_point__plant_node__fabric=fabric).select_related('a_au', 'b_au'):
        if not fine_edge.a_au_id or not fine_edge.b_au_id:
            continue
        connected_attachment_ids.add(fine_edge.a_au_id)
        connected_attachment_ids.add(fine_edge.b_au_id)

    policy_evaluation = build_policy_evaluation(
        fabric=fabric,
        attachment_units=attachment_units,
        plane_sets_by_attachment=plane_sets_by_attachment,
        plane_ids=plane_ids,
    )
    findings.extend(policy_evaluation.findings)

    for transfer_map in TransferMap.objects.filter(owner_node__fabric=fabric):
        connected_attachment_ids.add(transfer_map.src_attachment_unit_id)
        connected_attachment_ids.add(transfer_map.dst_attachment_unit_id)

    for attachment_unit in attachment_units:
        if attachment_unit.topology_role != 'child-interface':
            continue
        if attachment_unit.pk in connected_attachment_ids:
            continue
        findings.append(_make_finding(
            finding_type='orphaned_attachment_unit',
            severity='warning',
            obj=attachment_unit,
            message='Child-interface attachment unit is not connected to any derived graph path.',
        ))

    for plane in planes:
        if membership_count_by_plane.get(plane.pk, 0) == 0:
            findings.append(_make_finding(
                finding_type='plane_underpopulated',
                severity='warning',
                obj=plane,
                message='Plane has no registered members.',
            ))

    device_source_ids = _device_source_ids_for_fabric(fabric)
    front_mapping_positions = set()
    rear_mapping_positions = set()
    if device_source_ids:
        port_mappings = PortMapping.objects.filter(device_id__in=device_source_ids)
        front_mapping_positions = set(port_mappings.values_list('front_port_id', 'front_port_position'))
        rear_mapping_positions = set(port_mappings.values_list('rear_port_id', 'rear_port_position'))

    missing_port_mapping_positions = set()
    for cable in _relevant_cables_for_fabric(fabric):
        profile_diagnostics = _profile_mapping_diagnostics(cable)
        if profile_diagnostics is not None and profile_diagnostics['unresolved_positions']:
            finding_type = 'partial_profile_mapping' if profile_diagnostics['matched_positions'] > 0 else 'unresolved_profile_mapping'
            severity = 'warning' if profile_diagnostics['matched_positions'] > 0 else 'error'
            first_unresolved = profile_diagnostics['unresolved_positions'][0]
            findings.append(_make_finding(
                finding_type=finding_type,
                severity=severity,
                obj=cable,
                message=(
                    'Cable profile mapping resolved only partially against connected terminations.'
                    if profile_diagnostics['matched_positions'] > 0
                    else 'Cable profile mapping could not be resolved against connected terminations.'
                ),
                metadata={
                    'profile': get_breakout_profile_name_for_cable(cable),
                    'matched_positions': profile_diagnostics['matched_positions'],
                    'unresolved_positions': len(profile_diagnostics['unresolved_positions']),
                    'first_unresolved_termination': str(first_unresolved['termination']),
                    'first_unresolved_position': first_unresolved['position'],
                    'first_unresolved_reason': first_unresolved['reason'],
                    'first_unresolved_detail': first_unresolved['detail'],
                },
            ))

        for cable_termination in cable.terminations.all():
            termination = cable_termination.termination
            if not isinstance(termination, (FrontPort, RearPort)):
                continue
            for position in _iter_path_positions(termination):
                normalized_position = position or 1
                if isinstance(termination, FrontPort):
                    lookup_key = (termination.pk, normalized_position)
                    if lookup_key in front_mapping_positions:
                        continue
                    finding_key = ('frontport', termination.pk, normalized_position)
                    if finding_key in missing_port_mapping_positions:
                        continue
                    missing_port_mapping_positions.add(finding_key)
                    findings.append(_make_finding(
                        finding_type='missing_port_mapping',
                        severity='error',
                        obj=termination,
                        message='Cabled front-port position has no PortMapping row, so passive traversal is unresolved.',
                        metadata={
                            'cable_id': cable.pk,
                            'position': normalized_position,
                            'mapping_side': 'front',
                        },
                    ))
                    continue

                lookup_key = (termination.pk, normalized_position)
                if lookup_key in rear_mapping_positions:
                    continue
                finding_key = ('rearport', termination.pk, normalized_position)
                if finding_key in missing_port_mapping_positions:
                    continue
                missing_port_mapping_positions.add(finding_key)
                findings.append(_make_finding(
                    finding_type='missing_port_mapping',
                    severity='error',
                    obj=termination,
                    message='Cabled rear-port position has no PortMapping row, so passive traversal is unresolved.',
                    metadata={
                        'cable_id': cable.pk,
                        'position': normalized_position,
                        'mapping_side': 'rear',
                    },
                ))

        if not _cable_requires_explicit_profile(cable):
            for cable_termination in cable.terminations.all():
                termination = cable_termination.termination
                if not isinstance(termination, Interface):
                    continue
                if not _profile_requires_child_interfaces(termination):
                    continue
                expected_child_count = len(_expected_profile_positions(termination))
                actual_child_count = _child_interface_count(termination)
                if actual_child_count == 0:
                    findings.append(_make_finding(
                        finding_type='missing_child_interface',
                        severity='error',
                        obj=termination,
                        message='Channelized interface requires explicit child interfaces for profile-derived mapping.',
                        metadata={
                            'cable_id': cable.pk,
                            'profile': get_breakout_profile_name_for_cable(cable),
                            'position_count': expected_child_count,
                        },
                    ))
                    continue
                if actual_child_count < expected_child_count:
                    findings.append(_make_finding(
                        finding_type='incomplete_child_interface_set',
                        severity='error',
                        obj=termination,
                        message='Channelized interface exposes only part of the required child-interface set for profile-derived mapping.',
                        metadata={
                            'cable_id': cable.pk,
                            'profile': get_breakout_profile_name_for_cable(cable),
                            'expected_child_count': expected_child_count,
                            'actual_child_count': actual_child_count,
                        },
                    ))
            continue
        findings.append(_make_finding(
            finding_type='missing_cable_profile',
            severity='error',
            obj=cable,
            message='Cable requires an explicit profile for unambiguous derived mapping.',
            metadata={'profile': get_breakout_profile_name_for_cable(cable)},
        ))

    return {
        'fabric': fabric.pk,
        'plane_set': tuple(plane.pk for plane in planes),
        'scope': scope,
        'findings': findings,
    }


def run_tier_depth_audit(*, fabric=None, scope=None):
    """
    Verify that each PlantNode's recorded tier_level matches the fabric's
    tier_role_map, and that every expected tier level has at least one node.

    Findings produced:
    - ``tier_depth_mismatch`` (severity=warning) — a PlantNode whose
      ``metadata['tier_level']`` disagrees with ``tier_role_map[node.role]``.
      This typically means the map was changed after the last graph rebuild.
    - ``tier_level_missing_nodes`` (severity=warning) — a tier level defined
      in ``tier_role_map`` has no PlantNodes assigned to it in the current
      graph, indicating the fabric may be partially populated.

    Returns a dict with keys ``fabric``, ``tier_role_map``, and ``findings``.
    """
    fabric = _normalize_fabric(fabric=fabric, scope=scope)
    if fabric is None:
        return {'fabric': None, 'tier_role_map': {}, 'findings': []}

    tier_role_map = getattr(fabric, 'tier_role_map', None) or {}
    if not tier_role_map:
        # No map configured — tier-depth auditing is not applicable.
        return {'fabric': fabric.pk, 'tier_role_map': {}, 'findings': []}

    expected_levels = set(tier_role_map.values())
    nodes = list(PlantNode.objects.filter(fabric=fabric))

    findings = []
    levels_with_nodes = set()

    for node in nodes:
        node_role = node.role or ''
        if node_role not in tier_role_map:
            continue

        expected_level = tier_role_map[node_role]
        actual_level = (node.metadata or {}).get('tier_level')
        levels_with_nodes.add(actual_level)

        if actual_level != expected_level:
            findings.append(_make_finding(
                finding_type='tier_depth_mismatch',
                severity='warning',
                obj=node,
                message=(
                    f'PlantNode role {node_role!r} expects tier_level={expected_level} '
                    f'per fabric tier_role_map, but node.metadata["tier_level"]={actual_level!r}. '
                    'Re-run a full graph rebuild to refresh tier assignments.'
                ),
                metadata={
                    'role': node_role,
                    'expected_tier_level': expected_level,
                    'actual_tier_level': actual_level,
                },
            ))

    # Coverage check: every expected tier level should have at least one node.
    for level in sorted(expected_levels):
        if level not in levels_with_nodes:
            # Find which roles map to this level for a helpful message.
            roles_for_level = [r for r, v in tier_role_map.items() if v == level]
            findings.append(_make_finding(
                finding_type='tier_level_missing_nodes',
                severity='warning',
                obj=fabric,
                message=(
                    f'Tier level {level} (role(s): {roles_for_level}) has no PlantNodes in the '
                    'current graph. The fabric may be incompletely populated or the tier_role_map '
                    'references roles not present in the fabric scope.'
                ),
                metadata={
                    'missing_tier_level': level,
                    'roles': roles_for_level,
                },
            ))

    return {
        'fabric': fabric.pk,
        'tier_role_map': tier_role_map,
        'findings': findings,
    }
