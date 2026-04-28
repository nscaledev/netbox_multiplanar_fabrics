from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from netbox_plant_graph.models import AuditFinding, AuditSuppression

from .finding_state import record_audit_finding_event


def _plugin_config():
    return getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})


def default_suppression_days() -> int:
    return int(_plugin_config().get('audit_suppression_default_days', 7))


def active_suppression_for_finding(finding: AuditFinding):
    return finding.suppressions.filter(active=True).order_by('-created', '-pk').first()


def suppress_audit_finding(*, finding: AuditFinding, actor=None, reason: str = '', days: int | None = None):
    now = timezone.now()
    active = active_suppression_for_finding(finding)
    if active is None:
        duration_days = default_suppression_days() if days is None else days
        expires_at = now + timedelta(days=max(duration_days, 0)) if duration_days is not None else None
        active = AuditSuppression.objects.create(
            finding=finding,
            created_by=actor if actor is not None and getattr(actor, 'is_authenticated', False) else None,
            reason=reason,
            expires_at=expires_at,
            active=True,
        )
    old_status = finding.status
    finding.status = 'suppressed'
    finding.active = True
    finding.save(update_fields=('status', 'active', 'last_updated'))
    record_audit_finding_event(
        finding=finding,
        actor=actor,
        event_type='suppressed',
        old_status=old_status,
        new_status='suppressed',
        message=reason or 'Finding suppressed.',
        metadata={'suppression_id': active.pk, 'expires_at': active.expires_at.isoformat() if active.expires_at else ''},
    )
    return active


def unsuppress_audit_finding(*, finding: AuditFinding, actor=None, reason: str = '') -> AuditFinding:
    active = active_suppression_for_finding(finding)
    if active is not None:
        active.active = False
        active.save(update_fields=('active', 'last_updated'))
    old_status = finding.status
    if finding.active:
        finding.status = 'open'
        finding.save(update_fields=('status', 'last_updated'))
    record_audit_finding_event(
        finding=finding,
        actor=actor,
        event_type='unsuppressed',
        old_status=old_status,
        new_status=finding.status,
        message=reason or 'Finding unsuppressed.',
        metadata={'suppression_id': active.pk if active is not None else None},
    )
    return finding


def expire_audit_suppressions(*, now=None) -> int:
    now = now or timezone.now()
    expired = list(
        AuditSuppression.objects.filter(active=True, expires_at__isnull=False, expires_at__lte=now).select_related('finding')
    )
    for suppression in expired:
        suppression.active = False
        suppression.save(update_fields=('active', 'last_updated'))
        finding = suppression.finding
        old_status = finding.status
        if finding.active and finding.status == 'suppressed':
            finding.status = 'open'
            finding.save(update_fields=('status', 'last_updated'))
        record_audit_finding_event(
            finding=finding,
            event_type='expired',
            old_status=old_status,
            new_status=finding.status,
            message='Suppression expired.',
            metadata={'suppression_id': suppression.pk},
        )
    return len(expired)
