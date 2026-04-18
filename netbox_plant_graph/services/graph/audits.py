from collections import defaultdict

from django.contrib.contenttypes.models import ContentType

from dcim.models import Cable, FrontPort, Interface, PortMapping, RearPort

from netbox_plant_graph.models import AttachmentUnit, AuditFinding, Fabric, FineEdge, PlaneMembership, PlantNode, TransferMap

from ..netbox.adapters import build_object_reference


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


def _cable_requires_explicit_profile(cable) -> bool:
    if getattr(cable, 'profile', ''):
        return False

    terminations_by_end = defaultdict(list)
    for cable_termination in cable.terminations.all():
        terminations_by_end[cable_termination.cable_end].append(cable_termination.termination)

    if len(terminations_by_end) != 2:
        return True

    sides = list(terminations_by_end.values())
    if any(len(side) != 1 for side in sides):
        return True

    return any(len(_iter_path_positions(termination)) != 1 for side in sides for termination in side)


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
    return len(_iter_path_positions(interface)) > 1


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
        left_planes = plane_sets_by_attachment.get(fine_edge.a_au_id, set())
        right_planes = plane_sets_by_attachment.get(fine_edge.b_au_id, set())
        if left_planes and right_planes and left_planes.isdisjoint(right_planes):
            findings.append(_make_finding(
                finding_type='cross_plane_fine_edge',
                severity='error',
                obj=fine_edge,
                message='Fine edge bridges two disjoint planes.',
                metadata={
                    'left_plane_ids': sorted(left_planes),
                    'right_plane_ids': sorted(right_planes),
                },
            ))

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

    passive_types = {'patch_panel', 'shuffle_module', 'cassette', 'passive_device'}
    passive_nodes = PlantNode.objects.filter(fabric=fabric, node_type__in=passive_types)
    for plant_node in passive_nodes:
        node_attachment_ids = list(plant_node.termination_points.values_list('attachment_units__pk', flat=True))
        node_plane_ids = set()
        for attachment_id in node_attachment_ids:
            node_plane_ids.update(plane_sets_by_attachment.get(attachment_id, set()))
        if len(node_plane_ids) > 1:
            findings.append(_make_finding(
                finding_type='shared_passive_artifact',
                severity='warning',
                obj=plant_node,
                message='Passive plant node is shared across multiple planes.',
                metadata={'plane_ids': sorted(node_plane_ids)},
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
                expected_child_count = len(_iter_path_positions(termination))
                actual_child_count = _child_interface_count(termination)
                if actual_child_count == 0:
                    findings.append(_make_finding(
                        finding_type='missing_child_interface',
                        severity='error',
                        obj=termination,
                        message='Channelized interface requires explicit child interfaces for profile-derived mapping.',
                        metadata={
                            'cable_id': cable.pk,
                            'profile': getattr(cable, 'profile', '') or '',
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
                            'profile': getattr(cable, 'profile', '') or '',
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
            metadata={'profile': getattr(cable, 'profile', '') or ''},
        ))

    AuditFinding.objects.filter(
        object_type__in=ContentType.objects.get_for_models(FineEdge, AttachmentUnit, PlantNode).values(),
        object_id__in=[attachment.pk for attachment in attachment_units],
    ).delete()

    return {
        'fabric': fabric.pk,
        'plane_set': tuple(plane.pk for plane in planes),
        'scope': scope,
        'findings': findings,
    }
