from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from netbox_plant_graph.models import AuditFinding, AuditFindingEvent, AuditRun, GraphBuildRun

from .suppressions import expire_audit_suppressions


def _plugin_config():
    return getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})


def apply_audit_retention(*, now=None) -> dict:
    now = now or timezone.now()
    plugin_config = _plugin_config()
    run_retention_days = int(plugin_config.get('audit_run_retention_days', 90))
    event_retention_days = int(plugin_config.get('audit_event_retention_days', 180))
    build_run_retention_days = int(plugin_config.get('graph_build_run_retention_days', 90))

    expired_suppressions = expire_audit_suppressions(now=now)

    active_run_ids = set(
        AuditFinding.objects.filter(active=True).exclude(first_seen_run_id__isnull=True).values_list('first_seen_run_id', flat=True)
    )
    active_run_ids.update(
        AuditFinding.objects.filter(active=True).exclude(last_seen_run_id__isnull=True).values_list('last_seen_run_id', flat=True)
    )

    event_cutoff = now - timedelta(days=event_retention_days)
    deleted_events, _ = AuditFindingEvent.objects.filter(
        created__lt=event_cutoff,
    ).filter(
        Q(finding__active=False) | Q(finding__status='resolved')
    ).delete()

    run_cutoff = now - timedelta(days=run_retention_days)
    old_runs = AuditRun.objects.filter(
        completed_at__lt=run_cutoff,
        status__in=('completed', 'failed'),
    )
    if active_run_ids:
        old_runs = old_runs.exclude(pk__in=active_run_ids)
    deleted_runs, _ = old_runs.delete()

    build_run_cutoff = now - timedelta(days=build_run_retention_days)
    deleted_build_runs, _ = GraphBuildRun.objects.filter(
        completed_at__lt=build_run_cutoff,
        status__in=('completed', 'failed'),
    ).delete()

    return {
        'expired_suppressions': expired_suppressions,
        'deleted_events': deleted_events,
        'deleted_runs': deleted_runs,
        'deleted_build_runs': deleted_build_runs,
    }
