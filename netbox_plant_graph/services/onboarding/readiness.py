from __future__ import annotations

from typing import Any

from django.utils import timezone

from netbox_plant_graph.models import OnboardingWorkspace
from netbox_plant_graph.services.topology_integrity import audit_topology_integrity, persist_topology_integrity_report

from .workspace import changelog_actor_context, normalize_actor


def evaluate_onboarding_readiness(workspace: OnboardingWorkspace, *, actor=None) -> dict[str, Any]:
    actor = normalize_actor(actor)
    if workspace.fabric_id is None:
        summary = {
            'status': 'blocked',
            'reason': 'Workspace has not produced or bound a Fabric yet.',
            'evaluated_at': timezone.now().isoformat(),
        }
        _save_readiness(workspace, summary, actor=actor)
        return summary

    report = audit_topology_integrity(fabric=workspace.fabric)
    with changelog_actor_context(actor):
        operation_run = persist_topology_integrity_report(report, actor=actor)
    grouped_summary = report.grouped_summary
    blocking_count = grouped_summary.get('path_blocking_count', 0)
    summary = {
        'status': 'blocked' if blocking_count else 'ready',
        'reason': 'Topology integrity audit has blocking findings.' if blocking_count else 'Topology integrity audit passed.',
        'evaluated_at': timezone.now().isoformat(),
        'fabric_id': workspace.fabric_id,
        'operation_run_id': operation_run.pk,
        'blocking_count': blocking_count,
        'finding_count': len(report.findings),
        'summary': report.summary,
        'grouped_summary': grouped_summary,
    }
    _save_readiness(workspace, summary, actor=actor)
    return summary


def publish_workspace(workspace: OnboardingWorkspace, *, actor=None) -> dict[str, Any]:
    actor = normalize_actor(actor)
    readiness = workspace.readiness_summary or evaluate_onboarding_readiness(workspace, actor=actor)
    if readiness.get('status') != 'ready':
        raise ValueError('Workspace is not ready to publish.')
    with changelog_actor_context(actor):
        workspace.status = 'published'
        if workspace.fabric_id and workspace.fabric.status != 'active':
            workspace.fabric.status = 'active'
            workspace.fabric.save(update_fields=('status', 'last_updated'))
        workspace.save(update_fields=('status', 'last_updated'))
    return {
        'status': 'published',
        'workspace_id': workspace.pk,
        'fabric_id': workspace.fabric_id,
        'readiness': readiness,
    }


def _save_readiness(workspace: OnboardingWorkspace, summary: dict[str, Any], *, actor=None) -> None:
    with changelog_actor_context(actor):
        workspace.readiness_summary = summary
        workspace.status = 'readiness_failed' if summary.get('status') == 'blocked' else workspace.status
        workspace.save(update_fields=('readiness_summary', 'status', 'last_updated'))
