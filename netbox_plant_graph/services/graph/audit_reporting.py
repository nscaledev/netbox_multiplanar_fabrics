from collections import Counter
from datetime import timedelta

from django.utils import timezone

from netbox_plant_graph.models import AuditFinding, AuditFindingEvent, AuditRun, AuditSuppression, Fabric

from ..netbox.adapters import build_object_reference
from .payloads import (
    AuditWorkflowChurnWindowPayload,
    AuditWorkflowFindingDetailPayload,
    AuditWorkflowEventPayload,
    AuditWorkflowFindingRecordPayload,
    AuditWorkflowFindingPayload,
    AuditWorkflowRunPayload,
    AuditWorkflowSummaryPayload,
    AuditWorkflowSuppressionPayload,
    CountMetricPayload,
    ObjectReferencePayload,
)
from .suppressions import active_suppression_for_finding


def _object_reference_payload(reference: dict | None) -> ObjectReferencePayload | None:
    if reference is None:
        return None
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
        return Fabric.objects.order_by('name', 'pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _metric_payloads(counter: Counter) -> tuple[CountMetricPayload, ...]:
    return tuple(
        CountMetricPayload(name=name, value=count)
        for name, count in sorted(counter.items())
    )


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _suppression_payload(suppression: AuditSuppression | None) -> AuditWorkflowSuppressionPayload | None:
    if suppression is None:
        return None
    now = timezone.now()
    return AuditWorkflowSuppressionPayload(
        suppression=_object_reference_payload(build_object_reference(suppression)),
        finding=_object_reference_payload(build_object_reference(suppression.finding)),
        expires_at=_iso(suppression.expires_at),
        remaining_days=(suppression.expires_at.date() - now.date()).days if suppression.expires_at is not None else None,
        reason=suppression.reason,
    )


def _finding_record_payload(finding: AuditFinding, *, now=None) -> AuditWorkflowFindingRecordPayload:
    now = now or timezone.now()
    suppression = active_suppression_for_finding(finding)
    return AuditWorkflowFindingRecordPayload(
        finding=_object_reference_payload(build_object_reference(finding)),
        affected_object=_object_reference_payload(build_object_reference(finding.object)) if finding.object is not None else None,
        fabric=_object_reference_payload(build_object_reference(finding.fabric)) if finding.fabric is not None else None,
        plane=_object_reference_payload(build_object_reference(finding.plane)) if finding.plane is not None else None,
        status=finding.status,
        severity=finding.severity,
        active=finding.active,
        finding_type=finding.finding_type,
        message=finding.message,
        age_days=(now.date() - finding.first_seen_at.date()).days if finding.first_seen_at is not None else None,
        first_seen_at=_iso(finding.first_seen_at),
        last_seen_at=_iso(finding.last_seen_at),
        resolved_at=_iso(finding.resolved_at),
        assigned_to_display=str(finding.assigned_to) if finding.assigned_to is not None else None,
        acknowledged_by_display=str(finding.acknowledged_by) if finding.acknowledged_by is not None else None,
        active_suppression=_suppression_payload(suppression),
    )


def _event_payload(event: AuditFindingEvent) -> AuditWorkflowEventPayload:
    return AuditWorkflowEventPayload(
        finding=_object_reference_payload(build_object_reference(event.finding)),
        event_type=event.event_type,
        actor_display=str(event.actor) if event.actor is not None else None,
        created_at=_iso(event.created),
        message=event.message,
        run=_object_reference_payload(build_object_reference(event.run)) if event.run is not None else None,
        old_status=event.old_status or None,
        new_status=event.new_status or None,
    )


def _run_payload(run: AuditRun) -> AuditWorkflowRunPayload:
    return AuditWorkflowRunPayload(
        run=_object_reference_payload(build_object_reference(run)),
        scope_label=run.scope_label,
        status=run.status,
        trigger_mode=run.trigger_mode,
        started_at=_iso(run.started_at),
        completed_at=_iso(run.completed_at),
        finding_count=run.finding_count,
        new_count=run.new_count,
        reopened_count=run.reopened_count,
        resolved_count=run.resolved_count,
    )


def _churn_window_payload(*, fabric: Fabric, now, days: int) -> AuditWorkflowChurnWindowPayload:
    counts = Counter(
        AuditFindingEvent.objects.filter(
            finding__fabric=fabric,
            created__gte=now - timedelta(days=days),
        ).values_list('event_type', flat=True)
    )
    return AuditWorkflowChurnWindowPayload(
        label=f'Last {days}d',
        days=days,
        opened_count=counts.get('opened', 0),
        reopened_count=counts.get('reopened', 0),
        resolved_count=counts.get('resolved', 0),
        auto_resolved_count=counts.get('auto_resolved', 0),
        suppressed_count=counts.get('suppressed', 0),
    )


def _normalize_user_id(user=None):
    if user in (None, ''):
        return None
    try:
        return int(getattr(user, 'pk', user))
    except (TypeError, ValueError):
        return None


def _apply_finding_filters(
    queryset,
    *,
    fabric=None,
    plane=None,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    active: bool | None = None,
    assigned_to=None,
    acknowledged_by=None,
    object_type=None,
    object_id=None,
    suppressed: bool | None = None,
    min_age_days: int | None = None,
    search: str | None = None,
):
    if fabric is not None:
        queryset = queryset.filter(fabric=_normalize_fabric(fabric=fabric))
    if plane is not None:
        queryset = queryset.filter(plane_id=getattr(plane, 'pk', plane))
    if status:
        queryset = queryset.filter(status=status)
    if severity:
        queryset = queryset.filter(severity=severity)
    if finding_type:
        queryset = queryset.filter(finding_type=finding_type)
    if active is not None:
        queryset = queryset.filter(active=active)
    assigned_to_id = _normalize_user_id(assigned_to)
    if assigned_to_id is not None:
        queryset = queryset.filter(assigned_to_id=assigned_to_id)
    acknowledged_by_id = _normalize_user_id(acknowledged_by)
    if acknowledged_by_id is not None:
        queryset = queryset.filter(acknowledged_by_id=acknowledged_by_id)
    if object_type not in (None, ''):
        queryset = queryset.filter(object_type_id=getattr(object_type, 'pk', object_type))
    if object_id not in (None, ''):
        queryset = queryset.filter(object_id=getattr(object_id, 'pk', object_id))
    if suppressed is True:
        queryset = queryset.filter(suppressions__active=True)
    elif suppressed is False:
        queryset = queryset.exclude(suppressions__active=True)
    if min_age_days not in (None, ''):
        cutoff = timezone.now() - timedelta(days=max(int(min_age_days), 0))
        queryset = queryset.filter(first_seen_at__isnull=False, first_seen_at__lte=cutoff)
    if search:
        queryset = queryset.filter(message__icontains=search.strip())
    return queryset.distinct()


def build_audit_workflow_summary(*, fabric=None) -> AuditWorkflowSummaryPayload:
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        return AuditWorkflowSummaryPayload(
            fabric=None,
            total_findings=0,
            active_findings=0,
            resolved_findings=0,
            suppressed_findings=0,
            stale_findings_7d=0,
            stale_findings_30d=0,
            status_counts=(),
            severity_counts=(),
            type_counts=(),
            churn_windows=(),
            recent_runs=(),
            recent_events=(),
            oldest_active_findings=(),
            expiring_suppressions=(),
        )

    now = timezone.now()
    finding_qs = AuditFinding.objects.filter(fabric=fabric).select_related('object_type').order_by('pk')
    active_qs = finding_qs.filter(active=True)

    status_counter = Counter(active_qs.values_list('status', flat=True))
    severity_counter = Counter(active_qs.values_list('severity', flat=True))
    type_counter = Counter(active_qs.values_list('finding_type', flat=True))

    recent_runs = tuple(
        _run_payload(run)
        for run in AuditRun.objects.filter(fabric=fabric).order_by('-started_at', '-pk')[:5]
    )

    recent_events = tuple(
        _event_payload(event)
        for event in AuditFindingEvent.objects.filter(finding__fabric=fabric).select_related('finding', 'actor', 'run').order_by('-created', '-pk')[:10]
    )

    oldest_active_findings = tuple(
        AuditWorkflowFindingPayload(
            finding=_object_reference_payload(build_object_reference(finding)),
            affected_object=_object_reference_payload(build_object_reference(finding.object)) if finding.object is not None else None,
            status=finding.status,
            severity=finding.severity,
            age_days=(now.date() - finding.first_seen_at.date()).days if finding.first_seen_at is not None else None,
            first_seen_at=_iso(finding.first_seen_at),
            last_seen_at=_iso(finding.last_seen_at),
        )
        for finding in active_qs.select_related('fabric').order_by('first_seen_at', 'pk')[:10]
    )

    expiring_suppressions = tuple(
        _suppression_payload(suppression)
        for suppression in AuditSuppression.objects.filter(
            finding__fabric=fabric,
            active=True,
            expires_at__isnull=False,
        ).select_related('finding').order_by('expires_at', 'pk')[:10]
    )

    churn_windows = tuple(
        _churn_window_payload(fabric=fabric, now=now, days=days)
        for days in (7, 30)
    )

    return AuditWorkflowSummaryPayload(
        fabric=_object_reference_payload(build_object_reference(fabric)),
        total_findings=finding_qs.count(),
        active_findings=active_qs.count(),
        resolved_findings=finding_qs.filter(status='resolved').count(),
        suppressed_findings=active_qs.filter(status='suppressed').count(),
        stale_findings_7d=active_qs.filter(first_seen_at__lt=now - timedelta(days=7)).count(),
        stale_findings_30d=active_qs.filter(first_seen_at__lt=now - timedelta(days=30)).count(),
        status_counts=_metric_payloads(status_counter),
        severity_counts=_metric_payloads(severity_counter),
        type_counts=_metric_payloads(type_counter),
        churn_windows=churn_windows,
        recent_runs=recent_runs,
        recent_events=recent_events,
        oldest_active_findings=oldest_active_findings,
        expiring_suppressions=expiring_suppressions,
    )


def build_durable_audit_finding_search(
    *,
    fabric=None,
    plane=None,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    active: bool | None = None,
    assigned_to=None,
    acknowledged_by=None,
    object_type=None,
    object_id=None,
    suppressed: bool | None = None,
    min_age_days: int | None = None,
    search: str | None = None,
    limit: int = 25,
) -> tuple[AuditWorkflowFindingRecordPayload, ...]:
    now = timezone.now()
    queryset = AuditFinding.objects.select_related(
        'fabric',
        'plane',
        'object_type',
        'assigned_to',
        'acknowledged_by',
    ).prefetch_related('suppressions')
    queryset = _apply_finding_filters(
        queryset,
        fabric=fabric,
        plane=plane,
        status=status,
        severity=severity,
        finding_type=finding_type,
        active=active,
        assigned_to=assigned_to,
        acknowledged_by=acknowledged_by,
        object_type=object_type,
        object_id=object_id,
        suppressed=suppressed,
        min_age_days=min_age_days,
        search=search,
    ).order_by('-active', 'severity', 'finding_type', 'pk')
    return tuple(_finding_record_payload(finding, now=now) for finding in queryset[: max(limit, 1)])


def build_durable_audit_finding_detail(*, finding) -> AuditWorkflowFindingDetailPayload | None:
    if finding is None:
        return None
    finding = AuditFinding.objects.select_related(
        'fabric',
        'plane',
        'object_type',
        'assigned_to',
        'acknowledged_by',
    ).prefetch_related('suppressions').filter(pk=getattr(finding, 'pk', finding)).first()
    if finding is None:
        return None
    return AuditWorkflowFindingDetailPayload(
        finding=_finding_record_payload(finding),
        recent_events=tuple(
            _event_payload(event)
            for event in finding.events.select_related('actor', 'run').all()[:10]
        ),
    )


def build_audit_run_timeline(*, fabric=None, limit: int = 10) -> tuple[AuditWorkflowRunPayload, ...]:
    fabric = _normalize_fabric(fabric=fabric)
    queryset = AuditRun.objects.all()
    if fabric is not None:
        queryset = queryset.filter(fabric=fabric)
    return tuple(_run_payload(run) for run in queryset.order_by('-started_at', '-pk')[: max(limit, 1)])
