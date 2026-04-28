from django.contrib.contenttypes.models import ContentType

from netbox_plant_graph.models import AttachmentUnit, Fabric, PlaneMembership

from ..netbox.adapters import build_object_reference
from .contamination import build_contamination_domains
from .policy_exceptions import (
    build_disjointness_exception_review,
    matching_disjointness_exceptions_for_finding,
)
from .payloads import ObjectReferencePayload, PolicyEvaluationPayload
from .policy_rules import evaluate_cross_plane_edge_bridge_evidence, evaluate_shared_passive_artifact_evidence


def _object_reference_payload(reference: dict) -> ObjectReferencePayload:
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


def _reference_dict(reference: ObjectReferencePayload) -> dict:
    return {
        'app_label': reference.app_label,
        'model': reference.model,
        'pk': reference.pk,
        'display': reference.display,
        'registry_key': reference.registry_key,
        'url': reference.url,
        'path_resolver_url': reference.path_resolver_url,
        'blast_radius_url': reference.blast_radius_url,
        'lane_drilldown_url': reference.lane_drilldown_url,
        'lane_workspace_url': reference.lane_workspace_url,
        'signal_path_resolver_url': reference.signal_path_resolver_url,
        'signal_blast_radius_url': reference.signal_blast_radius_url,
        'health_url': reference.health_url,
    }


def _normalize_fabric(fabric=None):
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return Fabric.objects.order_by('pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _plane_sets_by_attachment(*, fabric, attachment_units, plane_ids=None):
    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    queryset = PlaneMembership.objects.filter(
        member_type=attachment_type,
        member_id__in=[attachment_unit.pk for attachment_unit in attachment_units],
    )
    if plane_ids:
        queryset = queryset.filter(plane_id__in=plane_ids)
    plane_sets = {attachment_unit.pk: set() for attachment_unit in attachment_units}
    for member_id, plane_id in queryset.values_list('member_id', 'plane_id'):
        plane_sets.setdefault(member_id, set()).add(plane_id)
    return plane_sets


def _domain_lookup(domains):
    lookup = {}
    for domain in domains:
        for evidence_key in domain.evidence_keys:
            lookup[evidence_key] = domain.domain_key
    return lookup


def _policy_finding_for_artifact_share(evidence, *, domain_key, matched_exceptions=()):
    covered = bool(matched_exceptions)
    return {
        'finding_type': 'shared_passive_artifact',
        'severity': 'info' if covered else 'warning',
        'object': _reference_dict(evidence.artifact),
        'message': (
            'Passive plant node is shared across multiple planes, but the overlap is covered by an active '
            'disjointness exception.'
            if covered else
            'Passive plant node is shared across multiple planes.'
        ),
        'metadata': {
            'rule_id': evidence.rule_id,
            'plane_ids': list(evidence.plane_ids),
            'plane_pair_ids': [list(pair) for pair in evidence.plane_pair_ids],
            'artifact_kind': evidence.artifact_kind,
            'attachment_unit_count': evidence.attachment_unit_count,
            'signal_lane_count': evidence.signal_lane_count,
            'contamination_domain_key': domain_key,
            'exception_ids': [exception.pk for exception in matched_exceptions],
            'exception_status': 'covered' if covered else 'uncovered',
        },
    }


def _policy_finding_for_edge_bridge(evidence, *, domain_key, matched_exceptions=()):
    covered = bool(matched_exceptions)
    return {
        'finding_type': 'cross_plane_fine_edge',
        'severity': 'info' if covered else 'error',
        'object': _reference_dict(evidence.edge),
        'message': (
            'Fine edge bridges two disjoint planes, but the overlap is covered by an active disjointness '
            'exception.'
            if covered else
            'Fine edge bridges two disjoint planes.'
        ),
        'metadata': {
            'rule_id': evidence.rule_id,
            'left_plane_ids': list(evidence.left_plane_ids),
            'right_plane_ids': list(evidence.right_plane_ids),
            'plane_pair_ids': [list(pair) for pair in evidence.plane_pair_ids],
            'edge_kind': evidence.edge_kind,
            'contamination_domain_key': domain_key,
            'exception_ids': [exception.pk for exception in matched_exceptions],
            'exception_status': 'covered' if covered else 'uncovered',
        },
    }


def build_policy_evaluation(
    *,
    fabric=None,
    attachment_units=None,
    plane_sets_by_attachment=None,
    plane_ids=None,
) -> PolicyEvaluationPayload:
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        raise ValueError('A fabric is required for policy evaluation.')

    attachment_units = list(attachment_units) if attachment_units is not None else list(
        AttachmentUnit.objects.filter(
            termination_point__plant_node__fabric=fabric
        ).select_related('termination_point__plant_node')
    )
    if plane_sets_by_attachment is None:
        plane_sets_by_attachment = _plane_sets_by_attachment(
            fabric=fabric,
            attachment_units=attachment_units,
            plane_ids=plane_ids,
        )

    artifact_shares = evaluate_shared_passive_artifact_evidence(
        attachment_units=attachment_units,
        plane_sets_by_attachment=plane_sets_by_attachment,
    )
    edge_bridges = evaluate_cross_plane_edge_bridge_evidence(
        fabric=fabric,
        plane_sets_by_attachment=plane_sets_by_attachment,
    )
    contamination_domains = build_contamination_domains(
        fabric=fabric,
        artifact_shares=artifact_shares,
        edge_bridges=edge_bridges,
    )
    domain_by_evidence_key = _domain_lookup(contamination_domains)
    exception_review = build_disjointness_exception_review(
        fabric=fabric,
        evaluation=PolicyEvaluationPayload(
            fabric=_object_reference_payload(build_object_reference(fabric)),
            policy_mode=fabric.disjointness_policy,
            artifact_shares=artifact_shares,
            edge_bridges=edge_bridges,
            contamination_domains=contamination_domains,
            findings=(),
        ),
    )
    active_exceptions = exception_review['active_exceptions']
    domain_by_key = {domain.domain_key: domain for domain in contamination_domains}

    findings = []
    for evidence in artifact_shares:
        evidence_key = f'artifact:{evidence.rule_id}:{evidence.artifact.app_label}:{evidence.artifact.model}:{evidence.artifact.pk}'
        domain_key = domain_by_evidence_key.get(evidence_key)
        matched_exceptions = matching_disjointness_exceptions_for_finding(
            exceptions=active_exceptions,
            finding_type='shared_passive_artifact',
            target_reference=evidence.artifact,
            plane_pair_ids=evidence.plane_pair_ids,
            domain=domain_by_key.get(domain_key),
        )
        findings.append(
            _policy_finding_for_artifact_share(
                evidence,
                domain_key=domain_key,
                matched_exceptions=matched_exceptions,
            )
        )
    for evidence in edge_bridges:
        evidence_key = f'bridge:{evidence.rule_id}:{evidence.edge.app_label}:{evidence.edge.model}:{evidence.edge.pk}'
        domain_key = domain_by_evidence_key.get(evidence_key)
        matched_exceptions = matching_disjointness_exceptions_for_finding(
            exceptions=active_exceptions,
            finding_type='cross_plane_fine_edge',
            target_reference=evidence.edge,
            plane_pair_ids=evidence.plane_pair_ids,
            domain=domain_by_key.get(domain_key),
        )
        findings.append(
            _policy_finding_for_edge_bridge(
                evidence,
                domain_key=domain_key,
                matched_exceptions=matched_exceptions,
            )
        )

    return PolicyEvaluationPayload(
        fabric=_object_reference_payload(build_object_reference(fabric)),
        policy_mode=fabric.disjointness_policy,
        artifact_shares=artifact_shares,
        edge_bridges=edge_bridges,
        contamination_domains=contamination_domains,
        findings=tuple(findings),
    )
