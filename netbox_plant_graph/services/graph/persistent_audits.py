from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from netbox_plant_graph.models import AuditFinding, AuditRun, Fabric, FabricPlane

from .audits import run_plane_audit
from .finding_fingerprints import build_audit_finding_fingerprint
from .finding_state import record_audit_finding_event
from .suppressions import active_suppression_for_finding
from .unresolved_reporting import list_related_unresolved_summaries


def _normalize_fabric(fabric=None):
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return Fabric.objects.order_by('pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _full_fabric_scope_identity(fabric: Fabric) -> tuple[ContentType, int, str]:
    content_type = ContentType.objects.get_for_model(Fabric)
    return content_type, fabric.pk, str(fabric)


def _plane_for_finding(finding: dict, fabric: Fabric):
    obj = finding.get('object') or {}
    if obj.get('app_label') == 'netbox_plant_graph' and obj.get('model') == 'fabricplane':
        return FabricPlane.objects.filter(fabric=fabric, pk=obj.get('pk')).first()
    return None


def _object_content_type_for_finding(finding: dict) -> ContentType:
    obj = finding.get('object') or {}
    return ContentType.objects.get(app_label=obj['app_label'], model=obj['model'])


def run_persistent_plane_audit(*, fabric=None, trigger_mode='manual') -> dict:
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        return {
            'audit_run': None,
            'fabric': None,
            'findings': [],
            'new_count': 0,
            'reopened_count': 0,
            'resolved_count': 0,
        }

    scope_type, scope_id, scope_label = _full_fabric_scope_identity(fabric)
    started_at = timezone.now()
    audit_run = AuditRun.objects.create(
        fabric=fabric,
        scope_type=scope_type,
        scope_id=scope_id,
        scope_label=scope_label,
        trigger_mode=trigger_mode,
        status='running',
        started_at=started_at,
    )

    try:
        audit_result = run_plane_audit(fabric=fabric)
        findings = audit_result['findings']
        completed_at = timezone.now()
        new_count = 0
        reopened_count = 0
        resolved_count = 0

        with transaction.atomic():
            existing_findings = {
                finding.fingerprint: finding
                for finding in AuditFinding.objects.filter(
                    fabric=fabric,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
                if finding.fingerprint
            }

            seen_fingerprints = set()
            for finding in findings:
                fingerprint = build_audit_finding_fingerprint(finding)
                seen_fingerprints.add(fingerprint)
                object_content_type = _object_content_type_for_finding(finding)
                existing = existing_findings.get(fingerprint)
                related_unresolved_summaries = list_related_unresolved_summaries(
                    fabric=fabric,
                    finding=finding,
                    active_only=True,
                    limit=5,
                )
                related_summary_fingerprints = tuple(
                    summary.fingerprint
                    for summary in related_unresolved_summaries
                )
                finding_metadata = dict(finding.get('metadata') or {})
                if related_summary_fingerprints:
                    finding_metadata['related_unresolved_summary_fingerprints'] = related_summary_fingerprints
                else:
                    finding_metadata.pop('related_unresolved_summary_fingerprints', None)
                if existing is None:
                    created = AuditFinding.objects.create(
                        fabric=fabric,
                        plane=_plane_for_finding(finding, fabric),
                        scope_type=scope_type,
                        scope_id=scope_id,
                        fingerprint=fingerprint,
                        status='open',
                        active=True,
                        finding_type=finding['finding_type'],
                        severity=finding['severity'],
                        object_type=object_content_type,
                        object_id=finding['object']['pk'],
                        message=finding['message'],
                        first_seen_run=audit_run,
                        last_seen_run=audit_run,
                        first_seen_at=completed_at,
                        last_seen_at=completed_at,
                        metadata=finding_metadata,
                    )
                    record_audit_finding_event(
                        finding=created,
                        run=audit_run,
                        event_type='opened',
                        new_status='open',
                        message='Finding opened by persistent audit.',
                    )
                    new_count += 1
                    continue

                was_resolved = existing.status == 'resolved' or not existing.active
                active_suppression = active_suppression_for_finding(existing)
                if was_resolved:
                    reopened_count += 1

                existing.fabric = fabric
                existing.plane = _plane_for_finding(finding, fabric)
                existing.scope_type = scope_type
                existing.scope_id = scope_id
                existing.finding_type = finding['finding_type']
                existing.severity = finding['severity']
                existing.object_type = object_content_type
                existing.object_id = finding['object']['pk']
                existing.message = finding['message']
                existing.last_seen_run = audit_run
                existing.last_seen_at = completed_at
                if was_resolved:
                    existing.status = 'suppressed' if active_suppression is not None else 'open'
                    existing.active = True
                    existing.resolved_at = None
                existing.metadata = finding_metadata
                existing.save()
                if was_resolved:
                    record_audit_finding_event(
                        finding=existing,
                        run=audit_run,
                        event_type='reopened',
                        old_status='resolved',
                        new_status=existing.status,
                        message='Finding reopened by persistent audit.',
                    )

            for fingerprint, existing in existing_findings.items():
                if fingerprint in seen_fingerprints or not existing.active:
                    continue
                old_status = existing.status
                existing.status = 'resolved'
                existing.active = False
                existing.resolved_at = completed_at
                existing.save(update_fields=('status', 'active', 'resolved_at', 'last_updated'))
                record_audit_finding_event(
                    finding=existing,
                    run=audit_run,
                    event_type='auto_resolved',
                    old_status=old_status,
                    new_status='resolved',
                    message='Finding auto-resolved because it was absent from the latest persistent audit.',
                )
                resolved_count += 1

            audit_run.status = 'completed'
            audit_run.completed_at = completed_at
            audit_run.finding_count = len(findings)
            audit_run.new_count = new_count
            audit_run.reopened_count = reopened_count
            audit_run.resolved_count = resolved_count
            audit_run.save()

        return {
            'audit_run': audit_run.pk,
            'fabric': fabric.pk,
            'findings': findings,
            'new_count': new_count,
            'reopened_count': reopened_count,
            'resolved_count': resolved_count,
        }
    except Exception as exc:
        audit_run.status = 'failed'
        audit_run.completed_at = timezone.now()
        audit_run.metadata = {'error': str(exc)}
        audit_run.save(update_fields=('status', 'completed_at', 'metadata', 'last_updated'))
        raise
