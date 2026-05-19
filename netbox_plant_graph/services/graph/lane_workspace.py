from dataclasses import replace
import hashlib
from urllib.parse import urlencode

from django.urls import reverse

from netbox_plant_graph.models import AttachmentUnit, AuditFinding, CoarseEdge, Fabric, FabricPlane, PlantNode, SignalLane

from ..netbox.adapters import build_object_reference
from ..netbox.lookup import get_registry_key_for_object
from .audit_reporting import build_durable_audit_finding_search
from .audits import run_plane_audit
from .compare import compare_lane_allocations
from .finding_details import build_audit_finding_details
from .lane_allocation import build_lane_allocation_summary
from .lane_sets import build_lane_set
from .payloads import (
    ActionLinkPayload,
    LaneAttachmentGroupPayload,
    LaneWorkspaceComparePayload,
    LaneWorkspaceFindingPayload,
    LaneDrilldownPayload,
    LanePathPayload,
    LaneSetAttachmentPayload,
    LaneWorkspaceFilterChipPayload,
    LaneWorkspaceGroupPayload,
    LaneWorkspacePathGroupPayload,
    LaneWorkspacePayload,
    LaneWorkspaceQueryPayload,
    ObjectReferencePayload,
)
from .resolver import build_representative_typed_lane_path


LANE_WORKSPACE_GROUP_LABELS = {
    'attachment': 'Attachment',
    'node': 'Passive Artifact / Node',
    'plane': 'Plane',
    'path': 'Representative Path',
}

LANE_WORKSPACE_MODE_LABELS = {
    'grouped': 'Grouped Coverage',
    'lane': 'Exact Lane',
}

LANE_WORKSPACE_FOCUS_LABELS = {
    'summary': 'Summary',
    'paths': 'Paths',
    'findings': 'Findings',
    'actions': 'Actions',
    'compare': 'Compare',
}


def _build_workspace_url(params: dict[str, object]) -> str:
    normalized = {
        key: value
        for key, value in params.items()
        if value not in (None, '', ())
    }
    return f"{reverse('plugins:netbox_plant_graph:lane_workspace')}?{urlencode(normalized)}"


def _parse_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_lane_workspace_group_by(group_by: str | None) -> str:
    if group_by in LANE_WORKSPACE_GROUP_LABELS:
        return group_by
    return 'attachment'


def _normalize_lane_workspace_mode(mode: str | None) -> str:
    if mode in LANE_WORKSPACE_MODE_LABELS:
        return mode
    return 'grouped'


def _normalize_lane_workspace_focus(focus: str | None) -> str:
    if focus in LANE_WORKSPACE_FOCUS_LABELS:
        return focus
    return 'summary'


def _normalize_export_format(export_format: str | None) -> str | None:
    if export_format in {'csv', 'json'}:
        return export_format
    return None


def normalize_lane_workspace_query(
    *,
    target_registry_key: str | None,
    target_id,
    group_by: str | None,
    mode: str | None = None,
    focus: str | None = None,
    group_key: str | None = None,
    lane_index=None,
    path_lane_index=None,
    plane_id=None,
    compare_registry_key: str | None = None,
    compare_id='',
    source_finding_id=None,
    export_format: str | None = None,
    target=None,
) -> LaneWorkspaceQueryPayload:
    notes = []
    normalized_group_by = normalize_lane_workspace_group_by(group_by)
    if group_by not in (None, '', normalized_group_by):
        notes.append('Invalid grouping selection; using attachment view.')

    normalized_mode = _normalize_lane_workspace_mode(mode)
    if mode not in (None, '', normalized_mode):
        notes.append('Invalid workspace mode; using grouped coverage.')

    normalized_focus = _normalize_lane_workspace_focus(focus)
    if focus not in (None, '', normalized_focus):
        notes.append('Invalid focus selection; using summary focus.')

    normalized_lane_index = _parse_int(lane_index)
    if lane_index not in (None, '') and normalized_lane_index is None:
        notes.append('Invalid lane index; staying in grouped coverage.')

    normalized_path_lane_index = _parse_int(path_lane_index)
    if path_lane_index not in (None, '') and normalized_path_lane_index is None:
        notes.append('Invalid representative path lane selection; clearing path lane selection.')

    normalized_plane_id = _parse_int(plane_id)
    if plane_id not in (None, '') and normalized_plane_id is None:
        notes.append('Invalid plane filter; clearing plane selection.')

    normalized_source_finding_id = _parse_int(source_finding_id)
    if source_finding_id not in (None, '') and normalized_source_finding_id is None:
        notes.append('Invalid finding backlink; clearing finding context.')

    normalized_export_format = _normalize_export_format(export_format)
    if export_format not in (None, '', normalized_export_format):
        notes.append('Invalid export format; clearing export selection.')

    normalized_group_key = group_key or None
    normalized_target_registry_key = target_registry_key or 'attachmentunit'

    if target is not None:
        normalized_target_registry_key = get_registry_key_for_object(target) or normalized_target_registry_key
        if isinstance(target, SignalLane):
            normalized_mode = 'lane'
            if normalized_lane_index is None:
                normalized_lane_index = target.lane_index

    if normalized_lane_index is not None:
        normalized_mode = 'lane'

    return LaneWorkspaceQueryPayload(
        target_registry_key=normalized_target_registry_key,
        target_id='' if target_id in (None, '') else str(target_id),
        group_by=normalized_group_by,
        group_by_label=LANE_WORKSPACE_GROUP_LABELS[normalized_group_by],
        mode=normalized_mode,
        mode_label=LANE_WORKSPACE_MODE_LABELS[normalized_mode],
        focus=normalized_focus,
        focus_label=LANE_WORKSPACE_FOCUS_LABELS[normalized_focus],
        group_key=normalized_group_key,
        lane_index=normalized_lane_index,
        path_lane_index=normalized_path_lane_index,
        plane_id=normalized_plane_id,
        compare_registry_key=compare_registry_key or None,
        compare_id='' if compare_id in (None, '') else str(compare_id),
        source_finding_id=normalized_source_finding_id,
        export_format=normalized_export_format,
        notes=tuple(notes),
    )


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
        endpoint_label=reference.get('endpoint_label'),
        endpoint_context=reference.get('endpoint_context'),
        endpoint_device=reference.get('endpoint_device'),
        endpoint_device_type=reference.get('endpoint_device_type'),
        endpoint_role=reference.get('endpoint_role'),
        endpoint_rack=reference.get('endpoint_rack'),
        endpoint_source=reference.get('endpoint_source'),
        endpoint_module=reference.get('endpoint_module'),
        wavelength_nm=reference.get('wavelength_nm'),
    )


def _fabric_id_for_target(target, lane_set=None) -> int | None:
    if isinstance(target, FabricPlane):
        return target.fabric_id
    if isinstance(target, PlantNode):
        return target.fabric_id
    if isinstance(target, AttachmentUnit):
        return target.termination_point.plant_node.fabric_id
    if isinstance(target, CoarseEdge):
        return target.a_tp.plant_node.fabric_id
    if isinstance(target, SignalLane):
        return target.attachment_unit.termination_point.plant_node.fabric_id
    fabric_id = getattr(target, 'fabric_id', None)
    if isinstance(fabric_id, int):
        return fabric_id
    fabric = getattr(target, 'fabric', None)
    if getattr(fabric, 'pk', None) is not None:
        return fabric.pk

    if lane_set and lane_set.attachment_units:
        first_node_pk = lane_set.attachment_units[0].plant_node.pk
        return PlantNode.objects.filter(pk=first_node_pk).values_list('fabric_id', flat=True).first()
    return None


def _plane_object_for_number(target, lane_set, plane_number: int | None):
    if plane_number is None:
        return None
    fabric_id = _fabric_id_for_target(target, lane_set=lane_set)
    if fabric_id is None:
        return None
    return FabricPlane.objects.filter(fabric_id=fabric_id, plane_number=plane_number).order_by('pk').first()


def _plane_references_by_number(target, lane_set) -> dict[int, ObjectReferencePayload]:
    fabric_id = _fabric_id_for_target(target, lane_set=lane_set)
    if fabric_id is None:
        return {}

    plane_numbers = {plane_id for plane_id in lane_set.plane_ids if isinstance(plane_id, int)}
    if not plane_numbers:
        return {}

    planes = FabricPlane.objects.filter(
        fabric_id=fabric_id,
        plane_number__in=plane_numbers,
    ).order_by('plane_number', 'pk')
    return {
        plane.plane_number: _object_reference_payload(build_object_reference(plane))
        for plane in planes
    }


def _available_lane_indexes(rows: tuple[LaneSetAttachmentPayload, ...]) -> tuple[int, ...]:
    return tuple(sorted({lane_index for row in rows for lane_index in row.lane_indexes}))


def _filter_attachment_rows(
    rows: tuple[LaneSetAttachmentPayload, ...],
    *,
    plane_id: int | None,
) -> tuple[LaneSetAttachmentPayload, ...]:
    if plane_id is None:
        return rows
    return tuple(
        row for row in rows
        if plane_id in row.plane_ids
    )


def _group_workspace_params(query: LaneWorkspaceQueryPayload, **updates) -> dict[str, object]:
    params = {
        'target_registry_key': query.target_registry_key,
        'target_id': query.target_id,
        'group_by': query.group_by,
        'mode': query.mode,
        'focus': query.focus,
        'group_key': query.group_key,
        'lane_index': query.lane_index,
        'path_lane_index': query.path_lane_index,
        'plane_id': query.plane_id,
        'compare_registry_key': query.compare_registry_key,
        'compare_id': query.compare_id,
        'source_finding_id': query.source_finding_id,
    }
    params.update(updates)
    return params


def _attachment_groups(rows, query: LaneWorkspaceQueryPayload) -> tuple[LaneWorkspaceGroupPayload, ...]:
    groups = []
    for row in rows:
        key = f'attachment:{row.attachment_unit.pk}'
        groups.append(
            LaneWorkspaceGroupPayload(
                key=key,
                label=row.attachment_unit.display,
                reference=row.attachment_unit,
                attachment_unit_count=1,
                expected_lane_total=row.expected_lane_count,
                present_lane_total=row.present_lane_count,
                mapped_lane_total=row.mapped_lane_count,
                selected=query.group_key == key,
                workspace_url=_build_workspace_url(_group_workspace_params(
                    query,
                    group_by='attachment',
                    group_key=key,
                    mode='grouped',
                )),
            )
        )
    return tuple(groups)


def _node_groups(rows, query: LaneWorkspaceQueryPayload) -> tuple[LaneWorkspaceGroupPayload, ...]:
    groups = {}
    for member in rows:
        group = groups.setdefault(member.plant_node.pk, {
            'key': f'node:{member.plant_node.pk}',
            'label': member.plant_node.display,
            'reference': member.plant_node,
            'attachment_unit_count': 0,
            'expected_lane_total': 0,
            'present_lane_total': 0,
            'mapped_lane_total': 0,
        })
        group['attachment_unit_count'] += 1
        group['expected_lane_total'] += member.expected_lane_count
        group['present_lane_total'] += member.present_lane_count
        group['mapped_lane_total'] += member.mapped_lane_count
    return tuple(
        LaneWorkspaceGroupPayload(
            **groups[key],
            selected=query.group_key == groups[key]['key'],
            workspace_url=_build_workspace_url(_group_workspace_params(
                query,
                group_by='node',
                group_key=groups[key]['key'],
                mode='grouped',
            )),
        )
        for key in sorted(groups, key=lambda item: groups[item]['label'])
    )


def _plane_groups(target, lane_set, rows, query: LaneWorkspaceQueryPayload) -> tuple[LaneWorkspaceGroupPayload, ...]:
    plane_references = _plane_references_by_number(target, lane_set)
    groups = {}
    for member in rows:
        plane_keys = member.plane_ids or ('unassigned',)
        for plane_key in plane_keys:
            label = f'Plane {plane_key}' if plane_key != 'unassigned' else 'Unassigned'
            group = groups.setdefault(plane_key, {
                'key': f'plane:{plane_key}',
                'label': label,
                'reference': plane_references.get(plane_key) if isinstance(plane_key, int) else None,
                'attachment_unit_count': 0,
                'expected_lane_total': 0,
                'present_lane_total': 0,
                'mapped_lane_total': 0,
            })
            group['attachment_unit_count'] += 1
            group['expected_lane_total'] += member.expected_lane_count
            group['present_lane_total'] += member.present_lane_count
            group['mapped_lane_total'] += member.mapped_lane_count
    return tuple(
        LaneWorkspaceGroupPayload(
            **groups[key],
            selected=query.group_key == groups[key]['key'],
            workspace_url=_build_workspace_url(_group_workspace_params(
                query,
                group_by='plane',
                group_key=groups[key]['key'],
                plane_id=key if isinstance(key, int) else None,
                mode='grouped',
            )),
        )
        for key in sorted(groups, key=lambda item: (item == 'unassigned', item))
    )


def _path_signature(path: LanePathPayload) -> str:
    tokens = []
    for step in path.steps:
        token = [step.step_kind]
        if step.edge_type:
            token.append(step.edge_type)
        if step.plant_node is not None:
            token.append(f'node:{step.plant_node.display}')
        elif step.object is not None:
            token.append(f'object:{step.object.model}')
        tokens.append('|'.join(token))
    summary = path.summary
    tokens.extend((
        f'coarse:{summary.coarse_edges_crossed}',
        f'transfer:{summary.transfer_maps_crossed}',
        f'shuffle:{summary.shuffle_modules_crossed}',
        f'planes:{",".join(str(item) for item in summary.planes_touched)}',
    ))
    return '>'.join(tokens)


def _path_group_key(signature: str) -> str:
    return f"path:{hashlib.sha1(signature.encode('utf-8')).hexdigest()[:12]}"


def _path_group_label(path: LanePathPayload) -> str:
    start = next((step.plant_node.display for step in path.steps if step.plant_node is not None), path.source.display)
    end = next((step.plant_node.display for step in reversed(path.steps) if step.plant_node is not None), path.source.display)
    if start == end:
        return start
    return f'{start} -> {end}'


def _path_group_summary(path: LanePathPayload) -> str:
    summary = path.summary
    planes = ', '.join(str(item) for item in summary.planes_touched) or 'unassigned'
    return (
        f'{summary.coarse_edges_crossed} coarse edge(s), '
        f'{summary.transfer_maps_crossed} transfer map(s), '
        f'{summary.lane_maps_crossed} optical lane map(s), '
        f'{summary.shuffle_modules_crossed} shuffle module(s), '
        f'planes {planes}'
    )


def _path_groups(
    *,
    target,
    lane_set,
    rows: tuple[LaneSetAttachmentPayload, ...],
    query: LaneWorkspaceQueryPayload,
) -> tuple[LaneWorkspacePathGroupPayload, ...]:
    plane = _plane_object_for_number(target, lane_set, query.plane_id)
    attachment_lookup = AttachmentUnit.objects.select_related('termination_point__plant_node').in_bulk(
        [row.attachment_unit.pk for row in rows]
    )
    grouped = {}
    for row in rows:
        attachment = attachment_lookup.get(row.attachment_unit.pk)
        if attachment is None:
            continue
        for lane_index in row.lane_indexes:
            path = build_representative_typed_lane_path(
                source=attachment,
                source_lane_index=lane_index,
                plane=plane,
            )
            signature = _path_signature(path)
            group = grouped.setdefault(signature, {
                'attachment_ids': set(),
                'plane_ids': set(),
                'lane_indexes': set(),
                'candidates': [],
            })
            group['attachment_ids'].add(row.attachment_unit.pk)
            group['plane_ids'].update(path.summary.planes_touched or row.plane_ids)
            group['lane_indexes'].add(lane_index)
            group['candidates'].append((lane_index, row.attachment_unit.pk, row.attachment_unit, path))

    path_groups = []
    for signature, data in sorted(grouped.items(), key=lambda item: item[0]):
        candidates = sorted(item for item in data['candidates'])
        preferred_candidate = None
        if query.path_lane_index is not None:
            preferred_candidate = next((item for item in candidates if item[0] == query.path_lane_index), None)
        representative_candidate = preferred_candidate or candidates[0]
        representative_lane_index, _, representative_attachment, representative_path = representative_candidate
        key = _path_group_key(signature)
        path_groups.append(
            LaneWorkspacePathGroupPayload(
                key=key,
                label=_path_group_label(representative_path),
                signature=signature,
                selected=query.group_key == key,
                attachment_unit_count=len(data['attachment_ids']),
                lane_count=len(candidates),
                attachment_unit_ids=tuple(sorted(data['attachment_ids'])),
                lane_indexes=tuple(sorted(data['lane_indexes'])),
                representative_lane_index=representative_lane_index,
                representative_attachment=representative_attachment,
                path_summary=_path_group_summary(representative_path),
                plane_ids=tuple(sorted(data['plane_ids'])),
                representative_path=representative_path,
                workspace_url=_build_workspace_url(_group_workspace_params(
                    query,
                    group_by='path',
                    group_key=key,
                    path_lane_index=representative_lane_index,
                    mode='grouped',
                )),
            )
        )
    return tuple(sorted(
        path_groups,
        key=lambda group: (
            group.label,
            group.representative_attachment.display if group.representative_attachment is not None else '',
            -1 if group.representative_lane_index is None else group.representative_lane_index,
            group.signature,
        ),
    ))


def _selected_group_rows(
    rows: tuple[LaneSetAttachmentPayload, ...],
    *,
    group_by: str,
    group_key: str | None,
    path_groups: tuple[LaneWorkspacePathGroupPayload, ...] = (),
) -> tuple[LaneSetAttachmentPayload, ...]:
    if not group_key:
        return rows
    if group_by == 'attachment' and group_key.startswith('attachment:'):
        attachment_pk = _parse_int(group_key.split(':', 1)[1])
        return tuple(row for row in rows if row.attachment_unit.pk == attachment_pk)
    if group_by == 'node' and group_key.startswith('node:'):
        node_pk = _parse_int(group_key.split(':', 1)[1])
        return tuple(row for row in rows if row.plant_node.pk == node_pk)
    if group_by == 'plane' and group_key.startswith('plane:'):
        plane_key = group_key.split(':', 1)[1]
        if plane_key == 'unassigned':
            return tuple(row for row in rows if not row.plane_ids)
        plane_number = _parse_int(plane_key)
        return tuple(row for row in rows if plane_number in row.plane_ids)
    if group_by == 'path' and group_key.startswith('path:'):
        path_group = next((group for group in path_groups if group.key == group_key), None)
        if path_group is None:
            return rows
        return tuple(row for row in rows if row.attachment_unit.pk in path_group.attachment_unit_ids)
    return rows


def _filter_lane_view(
    *,
    target,
    selected_rows: tuple[LaneSetAttachmentPayload, ...],
    lane_index: int,
) -> LaneDrilldownPayload:
    attachment_ids = [row.attachment_unit.pk for row in selected_rows]
    signal_lanes = SignalLane.objects.filter(
        attachment_unit_id__in=attachment_ids,
        lane_index=lane_index,
    ).select_related('attachment_unit')

    lanes_by_attachment = {}
    for signal_lane in signal_lanes.order_by('attachment_unit_id', 'lane_index', 'pk'):
        lanes_by_attachment.setdefault(signal_lane.attachment_unit_id, []).append(signal_lane)

    attachment_groups = []
    for row in selected_rows:
        lanes = tuple(
            _object_reference_payload(build_object_reference(signal_lane))
            for signal_lane in lanes_by_attachment.get(row.attachment_unit.pk, ())
        )
        if not lanes:
            continue
        attachment_groups.append(
            LaneAttachmentGroupPayload(
                attachment_unit=row.attachment_unit,
                lane_count=len(lanes),
                lanes=lanes,
            )
        )

    return LaneDrilldownPayload(
        target=_object_reference_payload(build_object_reference(target)),
        lane_index=lane_index,
        attachment_units=tuple(attachment_groups),
        total_attachment_units=len(attachment_groups),
        total_signal_lanes=sum(group.lane_count for group in attachment_groups),
        available_lane_indexes=_available_lane_indexes(selected_rows),
    )


def _build_filter_chips(query: LaneWorkspaceQueryPayload) -> tuple[LaneWorkspaceFilterChipPayload, ...]:
    chips = []
    if query.mode == 'lane' and query.lane_index is not None:
        chips.append(LaneWorkspaceFilterChipPayload(
            label=f'Lane {query.lane_index}',
            clear_url=_build_workspace_url(_group_workspace_params(query, mode='grouped', lane_index=None)),
        ))
    if query.path_lane_index is not None:
        chips.append(LaneWorkspaceFilterChipPayload(
            label=f'Representative Path Lane {query.path_lane_index}',
            clear_url=_build_workspace_url(_group_workspace_params(query, path_lane_index=None)),
        ))
    if query.plane_id is not None:
        chips.append(LaneWorkspaceFilterChipPayload(
            label=f'Plane {query.plane_id}',
            clear_url=_build_workspace_url(_group_workspace_params(query, plane_id=None, group_key=None)),
        ))
    if query.group_key:
        label = f'Selected {query.group_by_label}: {query.group_key.split(":", 1)[1]}'
        if query.group_by == 'path':
            label = 'Selected Representative Path'
        chips.append(LaneWorkspaceFilterChipPayload(
            label=label,
            clear_url=_build_workspace_url(_group_workspace_params(query, group_key=None)),
        ))
    if query.focus != 'summary':
        chips.append(LaneWorkspaceFilterChipPayload(
            label=f'Focus: {query.focus_label}',
            clear_url=_build_workspace_url(_group_workspace_params(query, focus='summary')),
        ))
    if query.compare_registry_key and query.compare_id:
        chips.append(LaneWorkspaceFilterChipPayload(
            label='Compare Context',
            clear_url=_build_workspace_url(_group_workspace_params(
                query,
                compare_registry_key=None,
                compare_id=None,
                focus='summary' if query.focus == 'compare' else query.focus,
            )),
        ))
    if query.source_finding_id is not None:
        chips.append(LaneWorkspaceFilterChipPayload(
            label=f'Finding {query.source_finding_id}',
            clear_url=_build_workspace_url(_group_workspace_params(query, source_finding_id=None)),
        ))
    return tuple(chips)


def _fabric_for_target(target, lane_set=None):
    fabric_id = _fabric_id_for_target(target, lane_set=lane_set)
    if fabric_id is None:
        return None
    return Fabric.objects.filter(pk=fabric_id).first()


def _selected_plane_numbers(*, query: LaneWorkspaceQueryPayload, rows, path_group) -> tuple[int, ...]:
    plane_numbers = set()
    if query.plane_id is not None:
        plane_numbers.add(query.plane_id)
    for row in rows:
        plane_numbers.update(row.plane_ids)
    if path_group is not None:
        plane_numbers.update(path_group.plane_ids)
    return tuple(sorted(number for number in plane_numbers if isinstance(number, int)))


def _selected_plane_pks(*, target, lane_set, plane_numbers: tuple[int, ...]) -> set[int]:
    plane_pks = set()
    for plane_number in plane_numbers:
        plane = _plane_object_for_number(target, lane_set, plane_number)
        if plane is not None:
            plane_pks.add(plane.pk)
    return plane_pks


def _reference_matches_target(reference: ObjectReferencePayload | None, target_reference: ObjectReferencePayload) -> bool:
    return (
        reference is not None
        and reference.app_label == target_reference.app_label
        and reference.model == target_reference.model
        and reference.pk == target_reference.pk
    )


def _workspace_scope_relevance(
    *,
    object_reference: ObjectReferencePayload | None,
    target_reference: ObjectReferencePayload,
    selected_attachment_ids: set[int],
    selected_node_ids: set[int],
    selected_plane_pks: set[int],
    plane_reference: ObjectReferencePayload | None,
) -> tuple[int, str]:
    if _reference_matches_target(object_reference, target_reference):
        return (0, 'Target')
    if plane_reference is not None and _reference_matches_target(object_reference, plane_reference):
        return (1, 'Selected Plane')
    if object_reference is not None and object_reference.model == 'attachmentunit' and object_reference.pk in selected_attachment_ids:
        return (2, 'Selected Scope')
    if object_reference is not None and object_reference.model == 'plantnode' and object_reference.pk in selected_node_ids:
        return (2, 'Selected Scope')
    if object_reference is not None and object_reference.model == 'fabricplane' and object_reference.pk in selected_plane_pks:
        return (1, 'Selected Plane')
    return (3, 'Fabric')


def _workspace_finding_rows(
    *,
    target,
    lane_set,
    query: LaneWorkspaceQueryPayload,
    selected_rows: tuple[LaneSetAttachmentPayload, ...],
    selected_path_group: LaneWorkspacePathGroupPayload | None,
) -> tuple[LaneWorkspaceFindingPayload, ...]:
    if query.focus != 'findings':
        return ()

    fabric = _fabric_for_target(target, lane_set=lane_set)
    if fabric is None:
        return ()

    target_reference = _object_reference_payload(build_object_reference(target))
    plane_numbers = _selected_plane_numbers(query=query, rows=selected_rows, path_group=selected_path_group)
    plane_pks = _selected_plane_pks(target=target, lane_set=lane_set, plane_numbers=plane_numbers)
    selected_attachment_ids = {row.attachment_unit.pk for row in selected_rows}
    selected_node_ids = {row.plant_node.pk for row in selected_rows}
    selected_plane_reference = next(
        (
            _object_reference_payload(build_object_reference(_plane_object_for_number(target, lane_set, query.plane_id)))
            for _ in (0,)
            if query.plane_id is not None and _plane_object_for_number(target, lane_set, query.plane_id) is not None
        ),
        None,
    )

    finding_rows: list[tuple[tuple[int, str, str, int], LaneWorkspaceFindingPayload]] = []
    durable_findings = build_durable_audit_finding_search(
        fabric=fabric,
        active=True,
        limit=25,
    )
    for record in durable_findings:
        rank, label = _workspace_scope_relevance(
            object_reference=record.affected_object,
            target_reference=target_reference,
            selected_attachment_ids=selected_attachment_ids,
            selected_node_ids=selected_node_ids,
            selected_plane_pks=plane_pks,
            plane_reference=selected_plane_reference,
        )
        if rank > 2:
            continue
        actions = [ActionLinkPayload(label='Durable Detail', url=record.finding.url)] if record.finding.url else []
        if record.affected_object is not None and record.affected_object.lane_workspace_url:
            actions.append(ActionLinkPayload(label='Lane Workspace', url=record.affected_object.lane_workspace_url))
        finding_rows.append((
            (rank, record.finding_type, record.severity, record.finding.pk),
            LaneWorkspaceFindingPayload(
                finding_kind='durable',
                finding_type=record.finding_type,
                severity=record.severity,
                status=record.status,
                directness=label,
                object=record.affected_object,
                summary=record.message,
                message=record.message,
                action_links=tuple(actions),
            ),
        ))

    live_findings = run_plane_audit(fabric=fabric).get('findings', [])
    live_details = build_audit_finding_details(live_findings, source_finding_id=query.source_finding_id)
    for finding, detail in zip(live_findings, live_details):
        rank, label = _workspace_scope_relevance(
            object_reference=detail.object,
            target_reference=target_reference,
            selected_attachment_ids=selected_attachment_ids,
            selected_node_ids=selected_node_ids,
            selected_plane_pks=plane_pks,
            plane_reference=selected_plane_reference,
        )
        if rank > 2 and not (set(detail.impact.plane_ids) & set(plane_numbers)):
            continue
        finding_rows.append((
            (rank, detail.finding_type, detail.severity, detail.object.pk),
            LaneWorkspaceFindingPayload(
                finding_kind='live',
                finding_type=detail.finding_type,
                severity=detail.severity,
                status='live',
                directness='Selected Plane' if rank > 2 and set(detail.impact.plane_ids) & set(plane_numbers) else label,
                object=detail.object,
                summary=detail.summary,
                message=detail.message,
                action_links=detail.action_links,
            ),
        ))

    finding_rows.sort(key=lambda item: item[0])
    return tuple(row for _, row in finding_rows[:10])


def _lane_compare_url(*, baseline_target, candidate_target=None) -> str:
    params = {
        'baseline_registry_key': get_registry_key_for_object(baseline_target),
        'baseline_id': getattr(baseline_target, 'pk', ''),
    }
    if candidate_target is not None:
        params.update({
            'candidate_registry_key': get_registry_key_for_object(candidate_target),
            'candidate_id': getattr(candidate_target, 'pk', ''),
        })
    return f"{reverse('plugins:netbox_plant_graph:lane_compare')}?{urlencode(params)}"


def _workspace_compare_context(*, target, compare_target) -> LaneWorkspaceComparePayload | None:
    if compare_target is None:
        return None
    compare_result = compare_lane_allocations(
        baseline_target=target,
        candidate_target=compare_target,
    )
    regressions = compare_result.regressions + compare_result.policy_regressions
    compare_reference = _object_reference_payload(build_object_reference(compare_target))
    return LaneWorkspaceComparePayload(
        compare_target=compare_reference,
        compare_summary=(
            f'{len(regressions)} regression(s), '
            f'{len(compare_result.representative_reviews)} review pair(s), '
            f'{len(compare_result.policy_domain_deltas)} policy delta(s).'
        ),
        metrics=tuple(
            metric
            for metric in compare_result.metrics
            if metric.name in {'present_lane_total', 'mapped_lane_total', 'missing_lane_total', 'unmatched_peer_positions'}
        ),
        regressions=regressions[:5],
        review_count=len(compare_result.representative_reviews),
        full_compare_action=ActionLinkPayload(
            label='Open Full Compare',
            url=_lane_compare_url(baseline_target=target, candidate_target=compare_target),
        ),
    )


def _workspace_next_actions(
    *,
    query: LaneWorkspaceQueryPayload,
    target,
    lane_set,
    target_reference: ObjectReferencePayload,
    selected_path_group: LaneWorkspacePathGroupPayload | None,
    selected_path_view: LanePathPayload | None,
    compare_context: LaneWorkspaceComparePayload | None,
) -> tuple[ActionLinkPayload, ...]:
    actions: list[tuple[str, str | None]] = []
    if query.mode == 'lane':
        actions.append((
            'Grouped Coverage',
            _build_workspace_url(_group_workspace_params(query, mode='grouped', lane_index=None)),
        ))
    elif selected_path_group is not None and selected_path_group.representative_lane_index is not None:
        actions.append((
            'Open Representative Lane',
            _build_workspace_url(_group_workspace_params(
                query,
                mode='lane',
                lane_index=selected_path_group.representative_lane_index,
            )),
        ))
    if selected_path_view is not None and selected_path_view.source.signal_path_resolver_url:
        actions.append(('Representative Path', selected_path_view.source.signal_path_resolver_url))
    if target_reference.lane_drilldown_url:
        actions.append(('Lane Drilldown', target_reference.lane_drilldown_url))
    if target_reference.signal_blast_radius_url:
        actions.append(('Optical Radius', target_reference.signal_blast_radius_url))
    elif target_reference.blast_radius_url:
        actions.append(('Physical Cable Blast Radius', target_reference.blast_radius_url))
    if compare_context is not None and compare_context.full_compare_action is not None:
        actions.append((compare_context.full_compare_action.label, compare_context.full_compare_action.url))
    else:
        actions.append(('Compare From Here', _lane_compare_url(baseline_target=target)))
    fabric = _fabric_for_target(target, lane_set=lane_set)
    if fabric is not None:
        actions.append(('Plane Audit', f"{reverse('plugins:netbox_plant_graph:plane_audit')}?fabric_id={fabric.pk}"))
        actions.append(('Audit Dashboard', f"{reverse('plugins:netbox_plant_graph:audit_dashboard')}?fabric_id={fabric.pk}"))
    deduped = {}
    for label, url in actions:
        if not url:
            continue
        deduped[url] = ActionLinkPayload(label=label, url=url)
    return tuple(deduped.values())


def _workspace_export_links(query: LaneWorkspaceQueryPayload) -> tuple[ActionLinkPayload, ...]:
    return (
        ActionLinkPayload(
            label='Export CSV',
            url=_build_workspace_url(_group_workspace_params(query, export='csv')),
        ),
        ActionLinkPayload(
            label='Export JSON',
            url=_build_workspace_url(_group_workspace_params(query, export='json')),
        ),
    )


def build_lane_workspace(
    *,
    target,
    compare_target=None,
    source_finding=None,
    query: LaneWorkspaceQueryPayload | None = None,
    group_by: str | None = None,
    mode: str | None = None,
    focus: str | None = None,
    group_key: str | None = None,
    lane_index=None,
    path_lane_index=None,
    plane_id=None,
    compare_registry_key: str | None = None,
    compare_id='',
    source_finding_id=None,
    export_format: str | None = None,
) -> LaneWorkspacePayload:
    workspace_query = query or normalize_lane_workspace_query(
        target_registry_key=get_registry_key_for_object(target),
        target_id=getattr(target, 'pk', ''),
        group_by=group_by,
        mode=mode,
        focus=focus,
        group_key=group_key,
        lane_index=lane_index,
        path_lane_index=path_lane_index,
        plane_id=plane_id,
        compare_registry_key=compare_registry_key,
        compare_id=compare_id,
        source_finding_id=source_finding_id,
        export_format=export_format,
        target=target,
    )
    lane_set = build_lane_set(target)
    allocation_summary = build_lane_allocation_summary(target=target)
    all_attachment_rows = tuple(lane_set.attachment_units)
    filtered_rows = _filter_attachment_rows(all_attachment_rows, plane_id=workspace_query.plane_id)
    notes = list(workspace_query.notes)
    if workspace_query.compare_id and compare_target is None:
        notes.append('Selected compare target is not valid; clearing compare context.')
    if workspace_query.source_finding_id is not None and source_finding is None:
        notes.append('Selected finding backlink is not valid; clearing finding context.')
    if workspace_query.plane_id is not None and not filtered_rows:
        notes.append(f'Plane {workspace_query.plane_id} is not present in the current lane scope.')

    attachment_groups = _attachment_groups(filtered_rows, workspace_query)
    node_groups = _node_groups(filtered_rows, workspace_query)
    plane_groups = _plane_groups(target, lane_set, filtered_rows, workspace_query)
    path_groups = _path_groups(
        target=target,
        lane_set=lane_set,
        rows=filtered_rows,
        query=workspace_query,
    ) if workspace_query.group_by == 'path' else ()

    selected_group = None
    selected_path_group = None
    if workspace_query.group_by == 'path':
        selected_path_group = next((group for group in path_groups if group.selected), None)
        if selected_path_group is None and path_groups:
            selected_path_group = path_groups[0]
            if workspace_query.group_key is not None:
                notes.append('Selected path group is not valid for the current scope; showing the first available group.')
            workspace_query = replace(workspace_query, group_key=selected_path_group.key)
            path_groups = _path_groups(
                target=target,
                lane_set=lane_set,
                rows=filtered_rows,
                query=workspace_query,
            )
            selected_path_group = path_groups[0]
    else:
        primary_groups = attachment_groups
        if workspace_query.group_by == 'node':
            primary_groups = node_groups
        elif workspace_query.group_by == 'plane':
            primary_groups = plane_groups
        selected_group = next((group for group in primary_groups if group.selected), None)
        if selected_group is None and primary_groups:
            selected_group = primary_groups[0]
            if workspace_query.group_key is not None:
                notes.append('Selected group is not valid for the current scope; showing the first available group.')
            workspace_query = replace(workspace_query, group_key=selected_group.key)
            if workspace_query.group_by == 'attachment':
                attachment_groups = _attachment_groups(filtered_rows, workspace_query)
                selected_group = attachment_groups[0]
            elif workspace_query.group_by == 'node':
                node_groups = _node_groups(filtered_rows, workspace_query)
                selected_group = node_groups[0]
            else:
                plane_groups = _plane_groups(target, lane_set, filtered_rows, workspace_query)
                selected_group = plane_groups[0]

    selected_group_rows = _selected_group_rows(
        filtered_rows,
        group_by=workspace_query.group_by,
        group_key=workspace_query.group_key,
        path_groups=path_groups,
    ) if (selected_group is not None or selected_path_group is not None) else filtered_rows

    selected_path_view = selected_path_group.representative_path if selected_path_group is not None else None
    if selected_path_group is not None and workspace_query.path_lane_index is not None:
        if selected_path_group.representative_lane_index != workspace_query.path_lane_index:
            notes.append('Selected representative path lane is not available for this path group; using the default representative lane.')

    active_lane_indexes = (
        selected_path_group.lane_indexes
        if selected_path_group is not None
        else _available_lane_indexes(selected_group_rows or filtered_rows)
    )
    lane_view = None
    normalized_mode = workspace_query.mode
    active_lane_index = workspace_query.lane_index
    if normalized_mode == 'lane':
        if active_lane_index is None:
            normalized_mode = 'grouped'
            notes.append('Lane mode requires a lane index; staying in grouped coverage.')
        elif active_lane_index not in active_lane_indexes:
            normalized_mode = 'grouped'
            notes.append('Selected lane is not present in the current workspace scope; staying in grouped coverage.')
        else:
            lane_view = _filter_lane_view(
                target=target,
                selected_rows=selected_group_rows or filtered_rows,
                lane_index=active_lane_index,
            )
            if lane_view.total_attachment_units == 0:
                normalized_mode = 'grouped'
                lane_view = None
                notes.append('Selected lane did not match the current workspace scope; staying in grouped coverage.')

    if normalized_mode != workspace_query.mode:
        workspace_query = replace(
            workspace_query,
            mode=normalized_mode,
            mode_label=LANE_WORKSPACE_MODE_LABELS[normalized_mode],
            compare_registry_key=workspace_query.compare_registry_key if compare_target is not None else None,
            compare_id=workspace_query.compare_id if compare_target is not None else '',
            source_finding_id=workspace_query.source_finding_id if source_finding is not None else None,
            notes=tuple(notes),
        )
    elif tuple(notes) != workspace_query.notes:
        workspace_query = replace(
            workspace_query,
            compare_registry_key=workspace_query.compare_registry_key if compare_target is not None else None,
            compare_id=workspace_query.compare_id if compare_target is not None else '',
            source_finding_id=workspace_query.source_finding_id if source_finding is not None else None,
            notes=tuple(notes),
        )

    target_reference = _object_reference_payload(build_object_reference(target))
    compare_context = _workspace_compare_context(target=target, compare_target=compare_target) if compare_target is not None else None
    source_finding_backlink = None
    if source_finding is not None:
        source_reference = _object_reference_payload(build_object_reference(source_finding))
        if source_reference.url:
            source_finding_backlink = ActionLinkPayload(label='Return To Audit Finding', url=source_reference.url)
    related_findings = _workspace_finding_rows(
        target=target,
        lane_set=lane_set,
        query=workspace_query,
        selected_rows=selected_group_rows or filtered_rows,
        selected_path_group=selected_path_group,
    )
    next_actions = _workspace_next_actions(
        query=workspace_query,
        target=target,
        lane_set=lane_set,
        target_reference=target_reference,
        selected_path_group=selected_path_group,
        selected_path_view=selected_path_view,
        compare_context=compare_context,
    )
    export_links = _workspace_export_links(workspace_query)

    return LaneWorkspacePayload(
        query=workspace_query,
        target=lane_set.target,
        lane_set=lane_set,
        allocation_summary=allocation_summary,
        group_by=workspace_query.group_by,
        group_by_label=workspace_query.group_by_label,
        mode=workspace_query.mode,
        mode_label=workspace_query.mode_label,
        focus=workspace_query.focus,
        focus_label=workspace_query.focus_label,
        attachment_groups=attachment_groups,
        attachment_rows=filtered_rows,
        node_groups=node_groups,
        plane_groups=plane_groups,
        path_groups=path_groups,
        selected_group=selected_group,
        selected_group_rows=selected_group_rows,
        selected_path_group=selected_path_group,
        selected_path_view=selected_path_view,
        lane_view=lane_view,
        active_lane_index=active_lane_index if workspace_query.mode == 'lane' else None,
        active_path_lane_index=selected_path_group.representative_lane_index if selected_path_group is not None else None,
        available_lane_indexes=active_lane_indexes,
        filter_chips=_build_filter_chips(workspace_query),
        related_findings=related_findings,
        next_actions=next_actions,
        compare_context=compare_context,
        export_links=export_links,
        source_finding_backlink=source_finding_backlink,
    )
