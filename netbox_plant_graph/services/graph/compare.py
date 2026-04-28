from collections import OrderedDict

from django.contrib.contenttypes.models import ContentType

from netbox_plant_graph.models import AttachmentUnit, PlaneMembership, TerminationPoint

from .blast_radius import compute_blast_radius
from .lane_allocation import build_lane_allocation_summary
from .lane_sets import build_lane_set
from .policy_reporting import list_contamination_domains
from .payloads import (
    ActionLinkPayload,
    ComparisonMetricPayload,
    LaneComparePayload,
    LaneComparisonAttachmentPayload,
    LaneComparisonNodePayload,
    LaneComparisonReviewPayload,
    PolicyComparisonDomainDeltaPayload,
)
from .resolver import _normalize_attachment_targets


LANE_CONSISTENCY_RANK = {
    'empty': 0,
    'missing': 1,
    'partial': 2,
    'consistent': 3,
}

PLANE_CONSISTENCY_RANK = {
    'unassigned': 0,
    'mixed': 1,
    'consistent': 2,
}


def _member_key(member) -> str:
    if member.position is not None:
        return f'position:{member.position}'
    return member.attachment_unit.display


def _status_for_delta(*, baseline_value: int, candidate_value: int, higher_is_better: bool | None) -> str:
    if candidate_value == baseline_value:
        return 'unchanged'
    if higher_is_better is None:
        return 'changed'
    if higher_is_better:
        return 'improved' if candidate_value > baseline_value else 'regressed'
    return 'improved' if candidate_value < baseline_value else 'regressed'


def _metric(name: str, baseline_value: int, candidate_value: int, higher_is_better: bool | None) -> ComparisonMetricPayload:
    return ComparisonMetricPayload(
        name=name,
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        delta=candidate_value - baseline_value,
        status=_status_for_delta(
            baseline_value=baseline_value,
            candidate_value=candidate_value,
            higher_is_better=higher_is_better,
        ),
    )


def _attachment_lookup(lane_set):
    lookup = OrderedDict()
    for member in lane_set.attachment_units:
        lookup[_member_key(member)] = member
    return lookup


def _node_group_lookup(lane_set):
    lookup = OrderedDict()
    for member in lane_set.attachment_units:
        group = lookup.setdefault(member.plant_node.display, {
            'reference': member.plant_node,
            'attachment_units': 0,
            'present_lane_total': 0,
            'mapped_lane_total': 0,
        })
        group['attachment_units'] += 1
        group['present_lane_total'] += member.present_lane_count
        group['mapped_lane_total'] += member.mapped_lane_count
    return lookup


def _changed_fields(baseline_member, candidate_member):
    changed = []
    if baseline_member is None or candidate_member is None:
        return ('attachment_presence',)
    if baseline_member.status != candidate_member.status:
        changed.append('status')
    if baseline_member.plane_ids != candidate_member.plane_ids:
        changed.append('plane_ids')
    if baseline_member.present_lane_count != candidate_member.present_lane_count:
        changed.append('present_lane_count')
    if baseline_member.mapped_lane_count != candidate_member.mapped_lane_count:
        changed.append('mapped_lane_count')
    if baseline_member.expected_lane_count != candidate_member.expected_lane_count:
        changed.append('expected_lane_count')
    return tuple(changed)


def _review_action(member, lane_index):
    if member is None:
        return None
    if lane_index is None and member.attachment_unit.lane_workspace_url:
        return ActionLinkPayload(label='Lane Workspace', url=member.attachment_unit.lane_workspace_url)
    if member.attachment_unit.lane_drilldown_url:
        url = member.attachment_unit.lane_drilldown_url
        if lane_index is not None:
            joiner = '&' if '?' in url else '?'
            url = f'{url}{joiner}lane_index={lane_index}'
        return ActionLinkPayload(label='Lane Drilldown', url=url)
    if member.attachment_unit.lane_workspace_url:
        return ActionLinkPayload(label='Lane Workspace', url=member.attachment_unit.lane_workspace_url)
    return None


def _target_fabric(target):
    fabric = getattr(target, 'fabric', None)
    if fabric is not None:
        return fabric
    lane_set = build_lane_set(target)
    if lane_set.fabric is not None:
        from netbox_plant_graph.models import Fabric
        return Fabric.objects.filter(pk=lane_set.fabric.pk).first()
    return None


def _domain_target_keys(domain):
    return {reference.pk for reference in domain.attachment_units}


def _attachment_ids_for_object(obj) -> set[int]:
    attachment_ids = {attachment_unit.pk for attachment_unit in _normalize_attachment_targets(obj)}
    if attachment_ids or not hasattr(obj, '_meta'):
        return attachment_ids
    source_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    termination_ids = TerminationPoint.objects.filter(
        source_type=source_type,
        source_id=obj.pk,
    ).values_list('pk', flat=True)
    attachment_ids.update(
        AttachmentUnit.objects.filter(
            termination_point_id__in=termination_ids,
        ).values_list('pk', flat=True)
    )
    return attachment_ids


def _scoped_attachment_ids_for_target(target) -> set[int]:
    attachment_blast_radius = compute_blast_radius(target=target, resolution='attachment_unit')
    signal_blast_radius = compute_blast_radius(target=target, resolution='signal_lane')
    scoped_attachment_ids = {
        impacted['pk']
        for impacted in attachment_blast_radius.get('impacted_objects', ())
        if impacted.get('model') == 'attachmentunit'
    }
    scoped_attachment_ids.update(
        impacted['attachment_unit_id']
        for impacted in signal_blast_radius.get('impacted_objects', ())
        if impacted.get('model') == 'signallane' and impacted.get('attachment_unit_id')
    )
    direct_peers = list(getattr(target, 'link_peers', ()) or ())
    cable = getattr(target, 'cable', None)
    cable_end = getattr(target, 'cable_end', None)
    if cable is not None and cable_end:
        termination_attribute = 'b_terminations' if cable_end == 'A' else 'a_terminations'
        direct_peers.extend(getattr(cable, termination_attribute, ()) or ())
    scoped_attachment_ids.update(
        attachment_id
        for peer in direct_peers
        for attachment_id in _attachment_ids_for_object(peer)
    )
    scoped_attachment_ids.update(
        member.attachment_unit.pk
        for member in build_lane_set(target).attachment_units
    )
    if scoped_attachment_ids:
        return scoped_attachment_ids
    lane_set = build_lane_set(target)
    return {member.attachment_unit.pk for member in lane_set.attachment_units}


def _scoped_plane_ids_for_target(target) -> set[int]:
    attachment_ids = [member.attachment_unit.pk for member in build_lane_set(target).attachment_units]
    if not attachment_ids:
        return set()
    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    return set(
        PlaneMembership.objects.filter(
            member_type=attachment_type,
            member_id__in=attachment_ids,
        ).values_list('plane_id', flat=True)
    )


def _filter_domains_for_target(target):
    fabric = _target_fabric(target)
    if fabric is None:
        return ()
    target_attachment_ids = _scoped_attachment_ids_for_target(target)
    target_plane_ids = _scoped_plane_ids_for_target(target)
    if not target_attachment_ids:
        if not target_plane_ids:
            return ()
        return tuple(
            domain
            for domain in list_contamination_domains(fabric=fabric)
            if target_plane_ids & set(domain.plane_ids)
        )
    attachment_scoped_domains = tuple(
        domain
        for domain in list_contamination_domains(fabric=fabric)
        if _domain_target_keys(domain) & target_attachment_ids
    )
    if attachment_scoped_domains or not target_plane_ids:
        return attachment_scoped_domains
    return tuple(
        domain
        for domain in list_contamination_domains(fabric=fabric)
        if target_plane_ids & set(domain.plane_ids)
    )


def _domain_compare_key(domain) -> str:
    artifact_keys = tuple(
        sorted((artifact.model, artifact.display) for artifact in domain.artifacts)
    )
    return f'{tuple(domain.plane_ids)}:{artifact_keys}'


def _policy_domain_delta(domain, *, status: str) -> PolicyComparisonDomainDeltaPayload:
    return PolicyComparisonDomainDeltaPayload(
        compare_key=_domain_compare_key(domain),
        status=status,
        plane_ids=domain.plane_ids,
        artifact_count=len(domain.artifacts),
        attachment_unit_count=len(domain.attachment_units),
        signal_lane_count=domain.signal_lane_count,
        artifacts=domain.artifacts,
        representative_targets=domain.representative_targets,
    )


def compare_lane_allocations(*, baseline_target, candidate_target) -> LaneComparePayload:
    baseline_lane_set = build_lane_set(baseline_target)
    candidate_lane_set = build_lane_set(candidate_target)
    baseline_summary = build_lane_allocation_summary(target=baseline_target)
    candidate_summary = build_lane_allocation_summary(target=candidate_target)

    metrics = (
        _metric('attachment_units', baseline_lane_set.total_attachment_units, candidate_lane_set.total_attachment_units, None),
        _metric('expected_lane_total', baseline_summary.expected_lane_total, candidate_summary.expected_lane_total, None),
        _metric('present_lane_total', baseline_summary.present_lane_total, candidate_summary.present_lane_total, True),
        _metric('mapped_lane_total', baseline_summary.mapped_lane_total, candidate_summary.mapped_lane_total, True),
        _metric('missing_lane_total', baseline_summary.missing_lane_total, candidate_summary.missing_lane_total, False),
        _metric('incomplete_attachment_units', baseline_summary.incomplete_attachment_units, candidate_summary.incomplete_attachment_units, False),
        _metric(
            'unmatched_peer_positions',
            len(baseline_summary.unmatched_peer_positions),
            len(candidate_summary.unmatched_peer_positions),
            False,
        ),
    )

    baseline_members = _attachment_lookup(baseline_lane_set)
    candidate_members = _attachment_lookup(candidate_lane_set)
    attachment_diffs = []
    review_pairs = []
    for compare_key in sorted(set(baseline_members) | set(candidate_members)):
        baseline_member = baseline_members.get(compare_key)
        candidate_member = candidate_members.get(compare_key)
        changed_fields = _changed_fields(baseline_member, candidate_member)
        attachment_diffs.append(
            LaneComparisonAttachmentPayload(
                compare_key=compare_key,
                baseline_attachment=baseline_member.attachment_unit if baseline_member is not None else None,
                candidate_attachment=candidate_member.attachment_unit if candidate_member is not None else None,
                baseline_status=baseline_member.status if baseline_member is not None else None,
                candidate_status=candidate_member.status if candidate_member is not None else None,
                baseline_plane_ids=baseline_member.plane_ids if baseline_member is not None else (),
                candidate_plane_ids=candidate_member.plane_ids if candidate_member is not None else (),
                baseline_present_lane_count=baseline_member.present_lane_count if baseline_member is not None else 0,
                candidate_present_lane_count=candidate_member.present_lane_count if candidate_member is not None else 0,
                baseline_mapped_lane_count=baseline_member.mapped_lane_count if baseline_member is not None else 0,
                candidate_mapped_lane_count=candidate_member.mapped_lane_count if candidate_member is not None else 0,
                changed_fields=changed_fields,
            )
        )
        lane_index = None
        if baseline_member is not None and baseline_member.lane_indexes:
            lane_index = baseline_member.lane_indexes[0]
        if lane_index is None and candidate_member is not None and candidate_member.lane_indexes:
            lane_index = candidate_member.lane_indexes[0]
        if changed_fields:
            review_pairs.append(
                LaneComparisonReviewPayload(
                    compare_key=compare_key,
                    lane_index=lane_index,
                    baseline_attachment=baseline_member.attachment_unit if baseline_member is not None else None,
                    candidate_attachment=candidate_member.attachment_unit if candidate_member is not None else None,
                    baseline_action=_review_action(baseline_member, lane_index),
                    candidate_action=_review_action(candidate_member, lane_index),
                    reason=', '.join(changed_fields),
                )
            )

    baseline_nodes = _node_group_lookup(baseline_lane_set)
    candidate_nodes = _node_group_lookup(candidate_lane_set)
    node_diffs = []
    for node_label in sorted(set(baseline_nodes) | set(candidate_nodes)):
        baseline_group = baseline_nodes.get(node_label)
        candidate_group = candidate_nodes.get(node_label)
        node_diffs.append(
            LaneComparisonNodePayload(
                node_label=node_label,
                baseline_reference=baseline_group['reference'] if baseline_group else None,
                candidate_reference=candidate_group['reference'] if candidate_group else None,
                baseline_attachment_units=baseline_group['attachment_units'] if baseline_group else 0,
                candidate_attachment_units=candidate_group['attachment_units'] if candidate_group else 0,
                baseline_present_lane_total=baseline_group['present_lane_total'] if baseline_group else 0,
                candidate_present_lane_total=candidate_group['present_lane_total'] if candidate_group else 0,
                baseline_mapped_lane_total=baseline_group['mapped_lane_total'] if baseline_group else 0,
                candidate_mapped_lane_total=candidate_group['mapped_lane_total'] if candidate_group else 0,
                changed=baseline_group != candidate_group,
            )
        )

    plane_ids_added = tuple(sorted(set(candidate_lane_set.plane_ids) - set(baseline_lane_set.plane_ids)))
    plane_ids_removed = tuple(sorted(set(baseline_lane_set.plane_ids) - set(candidate_lane_set.plane_ids)))
    baseline_domains = {
        _domain_compare_key(domain): domain
        for domain in _filter_domains_for_target(baseline_target)
    }
    candidate_domains = {
        _domain_compare_key(domain): domain
        for domain in _filter_domains_for_target(candidate_target)
    }
    policy_domain_deltas = []
    for compare_key in sorted(set(baseline_domains) | set(candidate_domains)):
        baseline_domain = baseline_domains.get(compare_key)
        candidate_domain = candidate_domains.get(compare_key)
        if baseline_domain is None and candidate_domain is not None:
            policy_domain_deltas.append(_policy_domain_delta(candidate_domain, status='added'))
        elif candidate_domain is None and baseline_domain is not None:
            policy_domain_deltas.append(_policy_domain_delta(baseline_domain, status='removed'))

    regressions = []
    if candidate_summary.missing_lane_total > baseline_summary.missing_lane_total:
        regressions.append('Completeness regressed: more lanes are missing in the candidate scope.')
    if candidate_summary.mapped_lane_total < baseline_summary.mapped_lane_total:
        regressions.append('Mapping symmetry regressed: fewer lanes are mapped in the candidate scope.')
    if LANE_CONSISTENCY_RANK.get(candidate_summary.lane_map_consistency, -1) < LANE_CONSISTENCY_RANK.get(baseline_summary.lane_map_consistency, -1):
        regressions.append('Lane consistency regressed in the candidate scope.')
    if PLANE_CONSISTENCY_RANK.get(candidate_summary.plane_consistency, -1) < PLANE_CONSISTENCY_RANK.get(baseline_summary.plane_consistency, -1):
        regressions.append('Plane isolation regressed in the candidate scope.')
    if len(candidate_summary.unmatched_peer_positions) > len(baseline_summary.unmatched_peer_positions):
        regressions.append('Unresolved peer positions increased in the candidate scope.')
    if plane_ids_added and plane_ids_removed:
        regressions.append('Plane assignments changed between baseline and candidate scopes.')
    policy_regressions = []
    if len(candidate_domains) > len(baseline_domains):
        policy_regressions.append('Policy risk increased: more contamination domains are present in the candidate scope.')
    if any(delta.status == 'added' for delta in policy_domain_deltas):
        policy_regressions.append('Candidate introduces new contamination domains not present in the baseline scope.')

    return LaneComparePayload(
        baseline_target=baseline_lane_set.target,
        candidate_target=candidate_lane_set.target,
        baseline_lane_set=baseline_lane_set,
        candidate_lane_set=candidate_lane_set,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        metrics=metrics,
        attachment_diffs=tuple(attachment_diffs),
        node_diffs=tuple(node_diffs),
        representative_reviews=tuple(review_pairs),
        plane_ids_added=plane_ids_added,
        plane_ids_removed=plane_ids_removed,
        regressions=tuple(regressions),
        policy_domain_deltas=tuple(policy_domain_deltas),
        policy_regressions=tuple(policy_regressions),
    )
