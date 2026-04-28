from collections import OrderedDict
from urllib.parse import urlencode

from django.apps import apps
from django.urls import reverse

from dcim.models import Cable, FrontPort, Interface, RearPort

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, FabricPlane, FineEdge, PlantNode, TerminationPoint

from ..netbox.adapters import build_object_reference
from ..netbox.lookup import get_registry_key_for_object
from .lane_sets import build_lane_set
from .payloads import (
    ActionLinkPayload,
    AuditFindingDetailPayload,
    AuditFindingImpactPayload,
    ObjectReferencePayload,
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
    )


def _resolve_object(reference):
    app_label = reference.get('app_label')
    model_name = reference.get('model')
    pk = reference.get('pk')
    if not app_label or not model_name or pk is None:
        return None
    model = apps.get_model(app_label, model_name)
    if model is None:
        return None
    return model.objects.filter(pk=pk).first()


def _combine_consistency(values):
    normalized = {value for value in values if value}
    if not normalized:
        return None
    if len(normalized) == 1:
        return normalized.pop()
    return 'mixed'


def _impact_from_targets(*, targets=(), plane_ids=(), related_targets=(), unmatched_peer_positions=()):
    members_by_pk = OrderedDict()
    lane_consistency_values = []
    plane_consistency_values = []
    for target in targets:
        if target is None:
            continue
        try:
            lane_set = build_lane_set(target)
        except Exception:
            continue
        lane_consistency_values.append(lane_set.lane_map_consistency)
        plane_consistency_values.append(lane_set.plane_consistency)
        for member in lane_set.attachment_units:
            members_by_pk[member.attachment_unit.pk] = member
        plane_ids = tuple(sorted(set(plane_ids) | set(lane_set.plane_ids)))
        unmatched_peer_positions = tuple(sorted(set(unmatched_peer_positions) | set(lane_set.unmatched_peer_positions)))

    members = tuple(members_by_pk.values())
    return AuditFindingImpactPayload(
        plane_ids=tuple(sorted(set(plane_ids))),
        affected_attachment_units=len(members) if members else None,
        expected_lane_total=sum(member.expected_lane_count for member in members) if members else None,
        present_lane_total=sum(member.present_lane_count for member in members) if members else None,
        mapped_lane_total=sum(member.mapped_lane_count for member in members) if members else None,
        unmatched_peer_positions=tuple(sorted(set(unmatched_peer_positions))),
        lane_map_consistency=_combine_consistency(lane_consistency_values),
        plane_consistency=_combine_consistency(plane_consistency_values),
        related_targets=tuple(_object_reference_payload(build_object_reference(target)) for target in related_targets if target is not None),
    )


def _unique_action_links(actions):
    links = OrderedDict()
    for label, url in actions:
        if not url:
            continue
        links[url] = ActionLinkPayload(label=label, url=url)
    return tuple(links.values())


def _primary_action_links(reference):
    actions = []
    if reference.lane_workspace_url:
        actions.append(('Lane Workspace', reference.lane_workspace_url))
    if reference.lane_drilldown_url:
        actions.append(('Lane Drilldown', reference.lane_drilldown_url))
    if reference.signal_path_resolver_url:
        actions.append(('Signal Path', reference.signal_path_resolver_url))
    elif reference.path_resolver_url:
        actions.append(('Path Resolver', reference.path_resolver_url))
    if reference.signal_blast_radius_url:
        actions.append(('Signal Radius', reference.signal_blast_radius_url))
    elif reference.blast_radius_url:
        actions.append(('Physical Cable Blast Radius', reference.blast_radius_url))
    if reference.health_url:
        actions.append(('Health', reference.health_url))
    return actions


def _related_target_actions(targets):
    actions = []
    for target in targets:
        reference = _object_reference_payload(build_object_reference(target))
        if reference.lane_workspace_url:
            actions.append((f'Workspace: {reference.display}', reference.lane_workspace_url))
        elif reference.url:
            actions.append((f'Inspect: {reference.display}', reference.url))
        if reference.signal_path_resolver_url:
            actions.append((f'Path: {reference.display}', reference.signal_path_resolver_url))
        elif reference.path_resolver_url:
            actions.append((f'Path: {reference.display}', reference.path_resolver_url))
    return actions


def _cable_related_targets(cable):
    targets = []
    for cable_termination in cable.terminations.all():
        termination = cable_termination.termination
        if isinstance(termination, (Interface, FrontPort, RearPort)):
            targets.append(termination)
    return tuple(targets)


def _finding_detail_for_cable(*, reference, obj, finding, metadata):
    related_targets = _cable_related_targets(obj)
    impact = _impact_from_targets(
        targets=related_targets,
        related_targets=related_targets,
    )
    is_partial = finding['finding_type'] == 'partial_profile_mapping'
    summary = (
        'Profile-derived mapping is incomplete across the cable positions.'
        if is_partial
        else 'Profile-derived mapping could not be validated against connected peer positions.'
    )
    hints = [
        'Validate the cable profile, connector orientation, and populated positions on both cable ends.',
        'Inspect lane workspaces for the related terminations to confirm which lane groups are expected.',
    ]
    if metadata.get('first_unresolved_reason') == 'missing_peer_position':
        hints.append(
            f"Add or correct the peer termination position for {metadata.get('first_unresolved_detail') or 'the unresolved mapping'}."
        )
    return summary, tuple(hints), _unique_action_links(
        _primary_action_links(reference) + _related_target_actions(related_targets)
    ), impact


def _finding_detail_for_interface(*, reference, obj, finding, metadata):
    impact = _impact_from_targets(targets=(obj,), related_targets=(obj,))
    if finding['finding_type'] == 'missing_child_interface':
        summary = 'Parent interface cannot materialize its expected lane groups because no child interfaces exist.'
        hints = [
            f"Create {metadata.get('position_count', 0)} child interfaces under this parent interface.",
            'Populate per-plane child-interface metadata before rebuilding the graph.',
        ]
    else:
        summary = 'Parent interface exposes only part of the child-interface set required for lane-aware mapping.'
        hints = [
            f"Create the missing child interfaces so the set reaches {metadata.get('expected_child_count', 0)} total children.",
            'Verify each child interface has the intended plane metadata and speed before rebuilding the graph.',
        ]
    hints.append('Re-run the audit after the child-interface set is complete.')
    return summary, tuple(hints), _unique_action_links(_primary_action_links(reference)), impact


def _finding_detail_for_passive_port(*, reference, obj, metadata):
    impact = _impact_from_targets(targets=(obj,), related_targets=(obj,))
    mapping_side = metadata.get('mapping_side', 'port')
    position = metadata.get('position', '?')
    summary = 'Passive traversal stops at this port position because no PortMapping row exists.'
    hints = (
        f"Create a PortMapping row for the {mapping_side} position {position}.",
        'Validate the peer passive termination and then rerun the audit.',
    )
    return summary, hints, _unique_action_links(_primary_action_links(reference)), impact


def _finding_detail_for_passive_node(*, reference, obj, metadata):
    impact = _impact_from_targets(
        targets=(obj,),
        plane_ids=tuple(metadata.get('plane_ids', ())),
        related_targets=(obj,),
    )
    summary = 'A passive artifact is shared across multiple planes and needs explicit validation.'
    hints = (
        'Use the lane workspace grouped by plane to confirm whether this shared passive path is intentional.',
        'If the sharing is accidental, split the cabling or mapping so each plane remains isolated.',
    )
    return summary, hints, _unique_action_links(_primary_action_links(reference)), impact


def _finding_detail_for_fine_edge(*, reference, obj, metadata):
    related_targets = tuple(
        target
        for target in (getattr(obj, 'a_au', None), getattr(obj, 'b_au', None), getattr(obj, 'parent_coarse_edge', None))
        if target is not None
    )
    impact = _impact_from_targets(
        targets=tuple(target for target in related_targets if not isinstance(target, CoarseEdge)),
        plane_ids=tuple(metadata.get('left_plane_ids', ())) + tuple(metadata.get('right_plane_ids', ())),
        related_targets=related_targets,
    )
    summary = 'A derived attachment edge bridges disjoint plane memberships.'
    hints = (
        'Validate plane assignments on both sides of the edge and inspect the related passive/coarse path.',
        'Use the related lane workspaces to confirm whether the bridge is a real cross-plane contamination or a metadata error.',
    )
    return summary, hints, _unique_action_links(_primary_action_links(reference) + _related_target_actions(related_targets)), impact


def _generic_finding_detail(*, reference, obj, finding, metadata):
    impact_targets = (obj,) if obj is not None else ()
    impact = _impact_from_targets(
        targets=impact_targets,
        plane_ids=tuple(metadata.get('plane_ids', ())) + tuple(metadata.get('plane_numbers', ())),
        related_targets=impact_targets,
    )
    summary = 'Review the affected object and validate the surrounding lane and plane state.'
    hints = ['Use the available lane and path actions to inspect the affected graph neighborhood.']
    return summary, tuple(hints), _unique_action_links(_primary_action_links(reference)), impact


def _lane_workspace_url_for_target(target, *, group_by=None, focus='findings', plane_id=None, lane_index=None, source_finding_id=None):
    registry_key = get_registry_key_for_object(target)
    if not registry_key:
        return None
    params = {
        'target_registry_key': registry_key,
        'target_id': getattr(target, 'pk', ''),
        'focus': focus,
    }
    if group_by:
        params['group_by'] = group_by
    if plane_id is not None:
        params['plane_id'] = plane_id
    if lane_index is not None:
        params['lane_index'] = lane_index
    if source_finding_id is not None:
        params['source_finding_id'] = source_finding_id
    return f"{reverse('plugins:netbox_plant_graph:lane_workspace')}?{urlencode(params)}"


def _workspace_action_for_finding(*, obj, finding, metadata, source_finding_id=None):
    plane_hint = next(
        (
            plane_number
            for plane_number in (
                metadata.get('plane_id'),
                next(iter(metadata.get('plane_ids', ())), None),
                next(iter(metadata.get('plane_numbers', ())), None),
            )
            if plane_number is not None
        ),
        None,
    )
    lane_hint = metadata.get('lane_index')

    if isinstance(obj, Interface) and finding['finding_type'] in {'missing_child_interface', 'incomplete_child_interface_set'}:
        return _lane_workspace_url_for_target(
            obj,
            group_by='attachment',
            focus='findings',
            source_finding_id=source_finding_id,
        )
    if isinstance(obj, (FrontPort, RearPort)) and finding['finding_type'] == 'missing_port_mapping':
        return _lane_workspace_url_for_target(
            obj,
            group_by='node',
            focus='findings',
            source_finding_id=source_finding_id,
        )
    if isinstance(obj, PlantNode) and finding['finding_type'] == 'shared_passive_artifact':
        return _lane_workspace_url_for_target(
            obj,
            group_by='plane',
            focus='findings',
            plane_id=plane_hint,
            source_finding_id=source_finding_id,
        )
    if isinstance(obj, FineEdge) and finding['finding_type'] == 'cross_plane_fine_edge':
        target = obj.parent_coarse_edge or obj.a_au or obj.b_au
        if target is not None:
            return _lane_workspace_url_for_target(
                target,
                group_by='path',
                focus='findings',
                plane_id=plane_hint,
                source_finding_id=source_finding_id,
            )
    if isinstance(obj, Cable):
        related_targets = _cable_related_targets(obj)
        target = related_targets[0] if related_targets else None
        if target is not None:
            return _lane_workspace_url_for_target(
                target,
                group_by='path',
                focus='findings',
                plane_id=plane_hint,
                lane_index=lane_hint,
                source_finding_id=source_finding_id,
            )
    if obj is not None:
        return _lane_workspace_url_for_target(
            obj,
            focus='findings',
            plane_id=plane_hint,
            lane_index=lane_hint,
            source_finding_id=source_finding_id,
        )
    return None


def _replace_lane_workspace_action(action_links, workspace_url):
    if not workspace_url:
        return action_links
    replaced = []
    workspace_replaced = False
    for action in action_links:
        if action.label == 'Lane Workspace' and not workspace_replaced:
            replaced.append(ActionLinkPayload(label='Lane Workspace', url=workspace_url))
            workspace_replaced = True
        else:
            replaced.append(action)
    if not workspace_replaced:
        replaced.insert(0, ActionLinkPayload(label='Lane Workspace', url=workspace_url))
    return tuple(replaced)


def build_audit_finding_detail(finding, *, source_finding_id=None) -> AuditFindingDetailPayload:
    metadata = finding.get('metadata') or {}
    reference = _object_reference_payload(finding['object'])
    obj = _resolve_object(finding['object'])
    summary = 'Review this finding in the surrounding lane context.'
    hints = ()
    actions = _unique_action_links(_primary_action_links(reference))
    impact = _impact_from_targets(targets=(obj,) if obj is not None else ())

    if isinstance(obj, Cable):
        summary, hints, actions, impact = _finding_detail_for_cable(
            reference=reference,
            obj=obj,
            finding=finding,
            metadata=metadata,
        )
    elif isinstance(obj, Interface) and finding['finding_type'] in {'missing_child_interface', 'incomplete_child_interface_set'}:
        summary, hints, actions, impact = _finding_detail_for_interface(
            reference=reference,
            obj=obj,
            finding=finding,
            metadata=metadata,
        )
    elif isinstance(obj, (FrontPort, RearPort)) and finding['finding_type'] == 'missing_port_mapping':
        summary, hints, actions, impact = _finding_detail_for_passive_port(
            reference=reference,
            obj=obj,
            metadata=metadata,
        )
    elif isinstance(obj, PlantNode) and finding['finding_type'] == 'shared_passive_artifact':
        summary, hints, actions, impact = _finding_detail_for_passive_node(
            reference=reference,
            obj=obj,
            metadata=metadata,
        )
    elif isinstance(obj, FineEdge) and finding['finding_type'] == 'cross_plane_fine_edge':
        summary, hints, actions, impact = _finding_detail_for_fine_edge(
            reference=reference,
            obj=obj,
            metadata=metadata,
        )
    else:
        summary, hints, actions, impact = _generic_finding_detail(
            reference=reference,
            obj=obj,
            finding=finding,
            metadata=metadata,
        )
    actions = _replace_lane_workspace_action(
        actions,
        _workspace_action_for_finding(
            obj=obj,
            finding=finding,
            metadata=metadata,
            source_finding_id=source_finding_id,
        ),
    )

    return AuditFindingDetailPayload(
        finding_type=finding['finding_type'],
        severity=finding['severity'],
        object=reference,
        message=finding['message'],
        metadata=metadata,
        summary=summary,
        remediation_hints=tuple(hints),
        action_links=actions,
        impact=impact,
    )


def build_audit_finding_details(findings, *, source_finding_id=None) -> tuple[AuditFindingDetailPayload, ...]:
    return tuple(build_audit_finding_detail(finding, source_finding_id=source_finding_id) for finding in findings)
