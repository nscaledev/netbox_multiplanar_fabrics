from datetime import timedelta
from collections import Counter

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.utils import timezone

from netbox_plant_graph.models import Fabric, FabricPlane, UnresolvedStateSummary
from netbox_plant_graph.services.netbox.adapters import build_object_reference

from .payloads import (
    CountMetricPayload,
    UnresolvedStateAgingPayload,
    UnresolvedStateDashboardPayload,
    UnresolvedStateOverviewPayload,
    UnresolvedStateRecordPayload,
)

UNRESOLVED_REPORTING_CACHE_VERSION = '1'


def _plugin_config() -> dict:
    return getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})


def _unresolved_reporting_cache_enabled() -> bool:
    return bool(_plugin_config().get('unresolved_reporting_cache_enabled', False))


def _unresolved_reporting_cache_timeout() -> int:
    return int(_plugin_config().get('unresolved_reporting_cache_timeout', 300))


def _fabric_graph_revision(fabric: Fabric | None) -> str | None:
    if fabric is None:
        return None
    metadata = fabric.metadata or {}
    return metadata.get('graph_revision')


def _latest_summary_update_token(*, fabric: Fabric) -> str:
    latest = (
        UnresolvedStateSummary.objects.filter(fabric=fabric)
        .aggregate(last_updated=Max('last_updated'))
        .get('last_updated')
    )
    return latest.isoformat() if latest is not None else 'none'


def _unresolved_cache_key(*, kind: str, fabric: Fabric, plane=None, target=None, limit: int | None = None) -> str | None:
    graph_revision = _fabric_graph_revision(fabric)
    if not graph_revision:
        return None

    plane_token = str(getattr(plane, 'pk', 'all') if plane is not None else 'all')
    target_type_token = 'none'
    target_id_token = 'none'
    if target is not None and hasattr(target, '_meta') and hasattr(target, 'pk'):
        target_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
        target_type_token = f'{target_type.app_label}.{target_type.model}'
        target_id_token = str(target.pk)

    return ':'.join((
        'netbox_plant_graph',
        'unresolved',
        kind,
        str(fabric.pk),
        plane_token,
        target_type_token,
        target_id_token,
        str(limit if limit is not None else 'all'),
        UNRESOLVED_REPORTING_CACHE_VERSION,
        graph_revision,
        _latest_summary_update_token(fabric=fabric),
    ))


def _cached_unresolved_payload(*, kind: str, fabric: Fabric | None, plane=None, target=None, limit: int | None = None, builder):
    if fabric is None:
        return builder()

    cache_key = _unresolved_cache_key(
        kind=kind,
        fabric=fabric,
        plane=plane,
        target=target,
        limit=limit,
    )
    if not _unresolved_reporting_cache_enabled() or cache_key is None:
        return builder()

    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return cached_payload

    payload = builder()
    cache.set(cache_key, payload, timeout=_unresolved_reporting_cache_timeout())
    return payload


def _normalize_plane_ids(value) -> tuple[int, ...]:
    if value in (None, '', []):
        return ()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = (value,)
    plane_ids = []
    for item in items:
        try:
            plane_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(plane_ids)))


def _serialize_summary(summary: UnresolvedStateSummary) -> UnresolvedStateRecordPayload:
    impact = summary.impact or {}
    return UnresolvedStateRecordPayload(
        summary=build_object_reference(summary),
        fingerprint=summary.fingerprint,
        fabric=build_object_reference(summary.fabric) if summary.fabric_id else None,
        plane=build_object_reference(summary.plane) if summary.plane_id else None,
        owner_object=build_object_reference(summary.owner_object) if summary.owner_object is not None else None,
        representative_object=build_object_reference(summary.representative_object) if summary.representative_object is not None else None,
        scope_label=summary.scope_label,
        summary_kind=summary.summary_kind,
        cause_code=summary.cause_code,
        active=summary.active,
        plane_ids=_normalize_plane_ids(summary.plane_ids),
        affected_attachment_units=impact.get('affected_attachment_units'),
        affected_signal_lanes=impact.get('affected_signal_lanes'),
        expected_lane_total=impact.get('expected_lane_total'),
        present_lane_total=impact.get('present_lane_total'),
        mapped_lane_total=impact.get('mapped_lane_total'),
        missing_positions=_normalize_plane_ids(impact.get('missing_positions')),
        unmatched_peer_positions=_normalize_plane_ids(impact.get('unmatched_peer_positions')),
        first_seen_at=summary.first_seen_at.isoformat() if summary.first_seen_at is not None else None,
        last_seen_at=summary.last_seen_at.isoformat() if summary.last_seen_at is not None else None,
        resolved_at=summary.resolved_at.isoformat() if summary.resolved_at is not None else None,
    )


AUDIT_FINDING_TO_UNRESOLVED_CAUSE = {
    'missing_cable_profile': ('missing_cable_profile',),
    'missing_child_interface': ('missing_child_interface',),
    'incomplete_child_interface_set': ('incomplete_child_interface_set',),
    'missing_port_mapping': ('missing_port_mapping',),
    'orphaned_attachment_unit': ('orphaned_attachment_unit',),
}


def build_unresolved_state_overview(
    *,
    fabric=None,
    plane=None,
    target=None,
    limit: int = 5,
) -> UnresolvedStateOverviewPayload:
    if isinstance(target, Fabric):
        fabric = target
        target = None
    elif isinstance(target, FabricPlane):
        plane = target
        fabric = target.fabric
        target = None

    if isinstance(plane, FabricPlane):
        fabric = plane.fabric

    if fabric is not None and not isinstance(fabric, Fabric):
        fabric = Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()

    def builder():
        queryset = UnresolvedStateSummary.objects.select_related('fabric', 'plane').order_by(
            '-active',
            'cause_code',
            '-last_seen_at',
            '-pk',
        )

        if fabric is not None:
            queryset = queryset.filter(fabric=fabric)

        if target is not None:
            content_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
            queryset = queryset.filter(
                Q(owner_object_type=content_type, owner_object_id=target.pk)
                | Q(representative_object_type=content_type, representative_object_id=target.pk)
            )

        summaries = list(queryset)
        if isinstance(plane, FabricPlane):
            plane_number = plane.plane_number
            summaries = [
                summary for summary in summaries
                if summary.plane_id == plane.pk or plane_number in _normalize_plane_ids(summary.plane_ids)
            ]

        active_summaries = [summary for summary in summaries if summary.active]
        cause_counts = Counter(summary.cause_code for summary in active_summaries)

        return UnresolvedStateOverviewPayload(
            fabric=build_object_reference(fabric) if isinstance(fabric, Fabric) else None,
            plane=build_object_reference(plane) if isinstance(plane, FabricPlane) else None,
            target=build_object_reference(target) if target is not None else None,
            active_total=len(active_summaries),
            resolved_total=max(len(summaries) - len(active_summaries), 0),
            cause_counts=tuple(
                CountMetricPayload(name=name, value=value)
                for name, value in sorted(cause_counts.items())
            ),
            summaries=tuple(
                _serialize_summary(summary)
                for summary in active_summaries[:max(limit, 0)]
            ),
        )

    return _cached_unresolved_payload(
        kind='overview',
        fabric=fabric,
        plane=plane,
        target=target,
        limit=limit,
        builder=builder,
    )


def _finding_object_identity(*, finding=None, audit_finding=None):
    if audit_finding is not None:
        return audit_finding.object_type_id, audit_finding.object_id
    if finding is None:
        return None, None
    obj = finding.get('object') or {}
    if not obj:
        return None, None
    content_type = ContentType.objects.get(app_label=obj['app_label'], model=obj['model'])
    return content_type.pk, obj.get('pk')


def _candidate_cause_codes(*, finding=None, audit_finding=None) -> tuple[str, ...]:
    finding_type = audit_finding.finding_type if audit_finding is not None else (finding or {}).get('finding_type')
    metadata = audit_finding.metadata if audit_finding is not None else ((finding or {}).get('metadata') or {})
    cause_codes = list(AUDIT_FINDING_TO_UNRESOLVED_CAUSE.get(finding_type, ()))
    if finding_type in {'partial_profile_mapping', 'unresolved_profile_mapping'}:
        reason = metadata.get('first_unresolved_reason')
        if reason:
            cause_codes.append(reason)
    return tuple(sorted(set(cause_codes)))


def _finding_metadata(*, finding=None, audit_finding=None) -> dict:
    if audit_finding is not None:
        return audit_finding.metadata or {}
    return (finding or {}).get('metadata') or {}


def _summary_matches_metadata(summary: UnresolvedStateSummary, metadata: dict) -> bool:
    selector = summary.selector or {}
    summary_metadata = summary.metadata or {}
    cause_code = summary.cause_code
    if cause_code == 'missing_port_mapping':
        return (
            summary_metadata.get('cable_id') == metadata.get('cable_id')
            and summary_metadata.get('mapping_side') == metadata.get('mapping_side')
            and int(summary_metadata.get('position') or 0) == int(metadata.get('position') or 0)
        )
    if cause_code in {'missing_child_interface', 'incomplete_child_interface_set'}:
        if metadata.get('cable_id') is not None and summary_metadata.get('cable_id') != metadata.get('cable_id'):
            return False
        if metadata.get('expected_child_count') is not None and selector.get('expected_child_count') != metadata.get('expected_child_count'):
            return False
        return True
    if cause_code == 'missing_cable_profile':
        if metadata.get('profile') not in (None, '') and summary_metadata.get('profile') != metadata.get('profile'):
            return False
        return True
    if cause_code in {'profile_error', 'profile_returned_none', 'missing_peer_position'}:
        if metadata.get('cable_id') is not None and summary_metadata.get('cable_id') != metadata.get('cable_id'):
            return False
        if metadata.get('matched_positions') is not None and summary_metadata.get('matched_positions') != metadata.get('matched_positions'):
            return False
        reason = metadata.get('first_unresolved_reason')
        if reason and selector.get('unresolved_reason') != reason:
            return False
        detail = metadata.get('first_unresolved_detail')
        if detail not in (None, '') and selector.get('mapped_detail') != detail:
            return False
        position = metadata.get('first_unresolved_position')
        position_set = tuple(selector.get('unresolved_position_set') or ())
        if position is not None and position not in position_set:
            return False
        return True
    return True


def list_related_unresolved_summaries(*, fabric=None, finding=None, audit_finding=None, active_only=True, limit=3):
    if fabric is None and audit_finding is not None:
        fabric = audit_finding.fabric
    if not isinstance(fabric, Fabric):
        fabric = Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()
    if fabric is None:
        return ()

    metadata = _finding_metadata(finding=finding, audit_finding=audit_finding)
    linked_fingerprints = tuple(metadata.get('related_unresolved_summary_fingerprints') or ())
    if linked_fingerprints:
        queryset = UnresolvedStateSummary.objects.filter(fabric=fabric, fingerprint__in=linked_fingerprints)
        if active_only:
            queryset = queryset.filter(active=True)
        summaries = list(queryset.order_by('cause_code', '-last_seen_at', '-pk')[:max(limit, 0)])
        return tuple(_serialize_summary(summary) for summary in summaries)

    cause_codes = _candidate_cause_codes(finding=finding, audit_finding=audit_finding)
    if not cause_codes:
        return ()

    object_type_id, object_id = _finding_object_identity(finding=finding, audit_finding=audit_finding)
    queryset = UnresolvedStateSummary.objects.filter(
        fabric=fabric,
        cause_code__in=cause_codes,
    )
    if active_only:
        queryset = queryset.filter(active=True)
    if object_type_id is not None and object_id is not None:
        queryset = queryset.filter(
            Q(owner_object_type_id=object_type_id, owner_object_id=object_id)
            | Q(representative_object_type_id=object_type_id, representative_object_id=object_id)
        )

    summaries = [
        summary
        for summary in queryset.order_by('cause_code', '-last_seen_at', '-pk')
        if _summary_matches_metadata(summary, metadata)
    ][:max(limit, 0)]
    return tuple(_serialize_summary(summary) for summary in summaries)


def _serialize_aging_summary(summary: UnresolvedStateSummary, *, now) -> UnresolvedStateAgingPayload:
    first_seen_at = summary.first_seen_at
    age_days = None
    if first_seen_at is not None:
        age_days = max((now - first_seen_at).days, 0)
    metadata = summary.metadata or {}
    return UnresolvedStateAgingPayload(
        summary=build_object_reference(summary),
        owner_object=build_object_reference(summary.owner_object) if summary.owner_object is not None else None,
        representative_object=build_object_reference(summary.representative_object) if summary.representative_object is not None else None,
        cause_code=summary.cause_code,
        summary_kind=summary.summary_kind,
        age_days=age_days,
        build_count=getattr(summary, 'observation_count', 0),
        reopen_count=int(metadata.get('reopen_count') or 0),
        first_seen_at=summary.first_seen_at.isoformat() if summary.first_seen_at is not None else None,
        last_seen_at=summary.last_seen_at.isoformat() if summary.last_seen_at is not None else None,
    )


def build_unresolved_state_dashboard(*, fabric=None, limit: int = 10) -> UnresolvedStateDashboardPayload:
    if not isinstance(fabric, Fabric):
        fabric = Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()

    def builder():
        queryset = UnresolvedStateSummary.objects.none()
        if fabric is not None:
            queryset = UnresolvedStateSummary.objects.filter(fabric=fabric).annotate(
                observation_count=Count('observations')
            )

        active_summaries = list(
            queryset.filter(active=True).order_by('first_seen_at', 'cause_code', 'pk')
        )
        now = timezone.now()
        cause_counts = Counter(summary.cause_code for summary in active_summaries)
        stale_active_7d = sum(
            1
            for summary in active_summaries
            if summary.first_seen_at is not None and summary.first_seen_at <= now - timedelta(days=7)
        )
        recurring_total = sum(1 for summary in active_summaries if getattr(summary, 'observation_count', 0) >= 2)
        reopened_total = sum(1 for summary in active_summaries if int((summary.metadata or {}).get('reopen_count') or 0) > 0)

        return UnresolvedStateDashboardPayload(
            fabric=build_object_reference(fabric) if fabric is not None else None,
            active_total=len(active_summaries),
            stale_active_7d=stale_active_7d,
            recurring_total=recurring_total,
            reopened_total=reopened_total,
            cause_counts=tuple(
                CountMetricPayload(name=name, value=value)
                for name, value in sorted(cause_counts.items())
            ),
            oldest_active_summaries=tuple(
                _serialize_aging_summary(summary, now=now)
                for summary in active_summaries[:max(limit, 0)]
            ),
        )

    return _cached_unresolved_payload(
        kind='dashboard',
        fabric=fabric,
        limit=limit,
        builder=builder,
    )
