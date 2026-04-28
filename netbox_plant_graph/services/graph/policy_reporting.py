from collections import Counter

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from netbox_plant_graph.models import AuditFinding, Fabric, FabricPlane

from ..netbox.adapters import build_object_reference
from .payloads import (
    CountMetricPayload,
    ObjectReferencePayload,
    PolicyActiveFindingPayload,
    PolicyDashboardPayload,
    PolicyDomainReportPayload,
    PolicyPlanePairReportPayload,
    PolicySummaryPayload,
)
from .policy_exceptions import build_disjointness_exception_review
from .policy_evaluator import build_policy_evaluation


POLICY_RULE_CATALOG_VERSION = '1'


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


def _normalize_fabric(fabric=None):
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return Fabric.objects.order_by('pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _normalize_plane(plane=None):
    if isinstance(plane, FabricPlane):
        return plane
    if plane is None:
        return None
    return FabricPlane.objects.filter(pk=getattr(plane, 'pk', plane)).first()


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _plugin_config() -> dict:
    return getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})


def _policy_reporting_cache_enabled() -> bool:
    return bool(_plugin_config().get('policy_reporting_cache_enabled', False))


def _policy_reporting_cache_timeout() -> int:
    return int(_plugin_config().get('policy_reporting_cache_timeout', 300))


def _fabric_graph_revision(fabric: Fabric | None) -> str | None:
    if fabric is None:
        return None
    metadata = fabric.metadata or {}
    return metadata.get('graph_revision')


def _policy_cache_key(*, kind: str, fabric: Fabric, plane: FabricPlane | None = None) -> str | None:
    graph_revision = _fabric_graph_revision(fabric)
    if not graph_revision:
        return None
    plane_token = getattr(plane, 'pk', 'all') if plane is not None else 'all'
    return ':'.join((
        'netbox_plant_graph',
        'policy',
        kind,
        str(fabric.pk),
        str(plane_token),
        fabric.disjointness_policy or 'full',
        POLICY_RULE_CATALOG_VERSION,
        graph_revision,
    ))


def _cached_policy_evaluation(*, fabric: Fabric):
    cache_key = _policy_cache_key(kind='evaluation', fabric=fabric)
    if not _policy_reporting_cache_enabled() or cache_key is None:
        return build_policy_evaluation(fabric=fabric)

    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    payload = build_policy_evaluation(fabric=fabric)
    cache.set(cache_key, payload, timeout=_policy_reporting_cache_timeout())
    return payload


def build_policy_summary(*, fabric=None, plane=None) -> PolicySummaryPayload | None:
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        return None
    selected_plane = _normalize_plane(plane=plane)
    evaluation = _cached_policy_evaluation(fabric=fabric)
    contamination_domains = tuple(
        domain
        for domain in evaluation.contamination_domains
        if selected_plane is None or selected_plane.pk in domain.plane_ids
    )
    artifact_shares = tuple(
        item
        for item in evaluation.artifact_shares
        if selected_plane is None or selected_plane.pk in item.plane_ids
    )
    edge_bridges = tuple(
        item
        for item in evaluation.edge_bridges
        if selected_plane is None or selected_plane.pk in set(item.left_plane_ids) | set(item.right_plane_ids)
    )
    rule_counts = Counter(
        list(item.rule_id for item in artifact_shares) +
        list(item.rule_id for item in edge_bridges)
    )
    return PolicySummaryPayload(
        fabric=_object_reference_payload(build_object_reference(fabric)),
        policy_mode=evaluation.policy_mode,
        plane=_object_reference_payload(build_object_reference(selected_plane)) if selected_plane is not None else None,
        artifact_share_count=len(artifact_shares),
        edge_bridge_count=len(edge_bridges),
        contamination_domain_count=len(contamination_domains),
        plane_pair_count=len({
            plane_pair
            for domain in contamination_domains
            for plane_pair in domain.plane_pair_ids
        }),
        largest_domain_attachment_units=max((len(domain.attachment_units) for domain in contamination_domains), default=0),
        largest_domain_signal_lanes=max((domain.signal_lane_count for domain in contamination_domains), default=0),
        rule_counts=tuple(
            CountMetricPayload(name=name, value=value)
            for name, value in sorted(rule_counts.items())
        ),
    )


def list_contamination_domains(*, fabric=None, plane=None):
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        return ()
    selected_plane = _normalize_plane(plane=plane)
    evaluation = _cached_policy_evaluation(fabric=fabric)
    if selected_plane is None:
        return evaluation.contamination_domains
    return tuple(
        domain
        for domain in evaluation.contamination_domains
        if selected_plane.pk in domain.plane_ids
    )


def build_policy_dashboard(*, fabric=None, plane=None, domain_limit: int = 5, finding_limit: int = 5) -> PolicyDashboardPayload | None:
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        return None
    selected_plane = _normalize_plane(plane=plane)
    evaluation = _cached_policy_evaluation(fabric=fabric)
    exception_review = build_disjointness_exception_review(fabric=fabric, evaluation=evaluation)
    covered_exception_ids = exception_review['covered_exception_ids']
    domain_matches = exception_review['domain_matches']

    domains = tuple(
        domain
        for domain in evaluation.contamination_domains
        if selected_plane is None or selected_plane.pk in domain.plane_ids
    )

    pair_rollup = {}
    for domain in domains:
        matched_exceptions = domain_matches.get(domain.domain_key, ())
        uncovered = not matched_exceptions
        for plane_pair in domain.plane_pair_ids:
            row = pair_rollup.setdefault(plane_pair, {
                'domain_count': 0,
                'uncovered_domain_count': 0,
                'edge_bridge_count': 0,
                'uncovered_edge_bridge_count': 0,
                'artifact_share_count': 0,
                'uncovered_artifact_share_count': 0,
                'exception_ids': set(),
                'covered_exception_ids': set(),
            })
            row['domain_count'] += 1
            if uncovered:
                row['uncovered_domain_count'] += 1
            row['edge_bridge_count'] += len(domain.edge_bridges)
            row['artifact_share_count'] += len(domain.artifact_shares)
            if uncovered:
                row['uncovered_edge_bridge_count'] += len(domain.edge_bridges)
                row['uncovered_artifact_share_count'] += len(domain.artifact_shares)
            for exception in matched_exceptions:
                row['exception_ids'].add(exception.pk)
                if exception.pk in covered_exception_ids:
                    row['covered_exception_ids'].add(exception.pk)

    for exception in exception_review['active_exceptions']:
        if selected_plane is not None and selected_plane.pk not in exception.plane_pair_ids:
            continue
        if not exception.plane_pair_ids:
            continue
        row = pair_rollup.setdefault(exception.plane_pair_ids, {
            'domain_count': 0,
            'uncovered_domain_count': 0,
            'edge_bridge_count': 0,
            'uncovered_edge_bridge_count': 0,
            'artifact_share_count': 0,
            'uncovered_artifact_share_count': 0,
            'exception_ids': set(),
            'covered_exception_ids': set(),
        })
        row['exception_ids'].add(exception.pk)
        if exception.pk in covered_exception_ids:
            row['covered_exception_ids'].add(exception.pk)

    plane_pairs = tuple(
        PolicyPlanePairReportPayload(
            plane_pair_ids=plane_pair,
            domain_count=data['domain_count'],
            uncovered_domain_count=data['uncovered_domain_count'],
            edge_bridge_count=data['edge_bridge_count'],
            uncovered_edge_bridge_count=data['uncovered_edge_bridge_count'],
            artifact_share_count=data['artifact_share_count'],
            uncovered_artifact_share_count=data['uncovered_artifact_share_count'],
            active_exception_count=len(data['exception_ids']),
            covered_exception_count=len(data['covered_exception_ids']),
        )
        for plane_pair, data in sorted(
            pair_rollup.items(),
            key=lambda item: (
                -item[1]['uncovered_edge_bridge_count'],
                -item[1]['uncovered_artifact_share_count'],
                -item[1]['domain_count'],
                item[0],
            ),
        )
    )

    top_domains = tuple(
        PolicyDomainReportPayload(
            domain_key=domain.domain_key,
            plane_ids=domain.plane_ids,
            plane_pair_ids=domain.plane_pair_ids,
            artifact_count=len(domain.artifacts),
            attachment_unit_count=len(domain.attachment_units),
            signal_lane_count=domain.signal_lane_count,
            matched_exception_count=len(domain_matches.get(domain.domain_key, ())),
            coverage_status='covered' if domain_matches.get(domain.domain_key) else 'uncovered',
            highest_risk_severity=(
                'error' if domain.edge_bridges and not domain_matches.get(domain.domain_key)
                else 'warning' if domain.artifact_shares and not domain_matches.get(domain.domain_key)
                else 'info'
            ),
            artifacts=domain.artifacts,
            representative_targets=domain.representative_targets,
        )
        for domain in sorted(
            domains,
            key=lambda item: (
                0 if not domain_matches.get(item.domain_key) else 1,
                -len(item.edge_bridges),
                -item.signal_lane_count,
                -len(item.attachment_units),
                item.domain_key,
            ),
        )[:max(int(domain_limit), 0)]
    )

    now = timezone.now()
    oldest_active_findings = tuple(
        PolicyActiveFindingPayload(
            finding=_object_reference_payload(build_object_reference(finding)),
            affected_object=_object_reference_payload(build_object_reference(finding.object)) if finding.object is not None else None,
            plane=_object_reference_payload(build_object_reference(finding.plane)) if finding.plane is not None else None,
            finding_type=finding.finding_type,
            severity=finding.severity,
            status=finding.status,
            age_days=(now.date() - finding.first_seen_at.date()).days if finding.first_seen_at is not None else None,
            first_seen_at=_iso(finding.first_seen_at),
        )
        for finding in AuditFinding.objects.filter(
            fabric=fabric,
            active=True,
            finding_type__in=('shared_passive_artifact', 'cross_plane_fine_edge'),
        ).select_related('plane').order_by('first_seen_at', 'pk')[:max(int(finding_limit), 0)]
    )

    return PolicyDashboardPayload(
        fabric=_object_reference_payload(build_object_reference(fabric)),
        policy_mode=evaluation.policy_mode,
        active_exception_count=len(exception_review['active_exceptions']),
        covered_exception_count=len(covered_exception_ids),
        drifted_exception_count=len(exception_review['drifted_exceptions']),
        contamination_domain_count=len(domains),
        uncovered_domain_count=sum(1 for domain in domains if not domain_matches.get(domain.domain_key)),
        uncovered_edge_bridge_count=sum(
            len(domain.edge_bridges)
            for domain in domains
            if not domain_matches.get(domain.domain_key)
        ),
        uncovered_artifact_share_count=sum(
            len(domain.artifact_shares)
            for domain in domains
            if not domain_matches.get(domain.domain_key)
        ),
        plane_pairs=plane_pairs,
        top_domains=top_domains,
        oldest_active_findings=oldest_active_findings,
    )
