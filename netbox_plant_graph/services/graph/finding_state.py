from django.utils import timezone

from netbox_plant_graph.models import AuditFinding, AuditFindingEvent


def record_audit_finding_event(
    *,
    finding: AuditFinding,
    event_type: str,
    actor=None,
    run=None,
    old_status: str = '',
    new_status: str = '',
    message: str = '',
    metadata: dict | None = None,
):
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        actor = None
    return AuditFindingEvent.objects.create(
        finding=finding,
        run=run,
        event_type=event_type,
        actor=actor,
        old_status=old_status,
        new_status=new_status,
        message=message,
        metadata=metadata or {},
    )


def acknowledge_audit_finding(*, finding: AuditFinding, actor=None, note: str = '') -> AuditFinding:
    old_status = finding.status
    if old_status == 'resolved':
        return finding
    finding.status = 'acknowledged'
    if actor is not None and getattr(actor, 'is_authenticated', False):
        finding.acknowledged_by = actor
    if finding.acknowledged_at is None:
        finding.acknowledged_at = timezone.now()
    finding.save()
    record_audit_finding_event(
        finding=finding,
        event_type='status_changed',
        actor=actor,
        old_status=old_status,
        new_status=finding.status,
        message=note or 'Finding acknowledged.',
    )
    return finding


def start_audit_finding_remediation(*, finding: AuditFinding, actor=None, note: str = '') -> AuditFinding:
    old_status = finding.status
    if old_status == 'resolved':
        return finding
    finding.status = 'in_progress'
    if actor is not None and getattr(actor, 'is_authenticated', False):
        finding.assigned_to = actor
    finding.save()
    record_audit_finding_event(
        finding=finding,
        event_type='status_changed',
        actor=actor,
        old_status=old_status,
        new_status=finding.status,
        message=note or 'Remediation started.',
    )
    return finding


def resolve_audit_finding(*, finding: AuditFinding, actor=None, note: str = '') -> AuditFinding:
    old_status = finding.status
    if old_status == 'resolved' and not finding.active:
        return finding
    finding.status = 'resolved'
    finding.active = False
    finding.resolved_at = timezone.now()
    if note:
        finding.resolution_summary = note
    finding.save()
    record_audit_finding_event(
        finding=finding,
        event_type='resolved',
        actor=actor,
        old_status=old_status,
        new_status=finding.status,
        message=note or 'Finding resolved.',
    )
    return finding


def reopen_audit_finding(*, finding: AuditFinding, actor=None, note: str = '') -> AuditFinding:
    old_status = finding.status
    finding.status = 'open'
    finding.active = True
    finding.resolved_at = None
    finding.save()
    record_audit_finding_event(
        finding=finding,
        event_type='reopened',
        actor=actor,
        old_status=old_status,
        new_status=finding.status,
        message=note or 'Finding reopened.',
    )
    return finding
