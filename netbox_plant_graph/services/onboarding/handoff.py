from __future__ import annotations

from typing import Any

from netbox_plant_graph.models import OnboardingWorkspace

from .workspace import workspace_summary


def build_handoff_dossier(workspace: OnboardingWorkspace) -> dict[str, Any]:
    current_plan = workspace.current_plan
    return {
        'schema': 'v2.onboarding.handoff/1',
        'workspace': {
            'id': workspace.pk,
            'name': workspace.name,
            'slug': workspace.slug,
            'status': workspace.status,
            'target_fabric_name': workspace.target_fabric_name,
            'target_fabric_slug': workspace.target_fabric_slug,
            'fabric_id': workspace.fabric_id,
            'architecture_id': workspace.architecture_id,
            'site_id': workspace.site_id,
        },
        'summary': workspace_summary(workspace),
        'current_plan': _plan_payload(current_plan),
        'sources': [
            {
                'id': artifact.pk,
                'artifact_type': artifact.artifact_type,
                'name': artifact.name,
                'status': artifact.status,
                'content_sha256': artifact.content_sha256,
                'source_uri': artifact.source_uri,
                'parse_result': artifact.parse_result,
            }
            for artifact in workspace.source_artifacts.order_by('pk')
        ],
        'object_links': [
            {
                'id': link.pk,
                'link_kind': link.link_kind,
                'label': link.label,
                'object_type_id': link.object_type_id,
                'object_id': link.object_id,
                'external_url': link.external_url,
            }
            for link in workspace.object_links.order_by('link_kind', 'label', 'pk')
        ],
        'readiness': workspace.readiness_summary or {},
    }


def _plan_payload(plan) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        'id': plan.pk,
        'status': plan.status,
        'plan_hash': plan.plan_hash,
        'workspace_revision': plan.workspace_revision,
        'approved_by_id': plan.approved_by_id,
        'approved_at': plan.approved_at.isoformat() if plan.approved_at else None,
        'stages': [
            {
                'id': stage.pk,
                'stage_key': stage.stage_key,
                'stage_kind': stage.stage_kind,
                'status': stage.status,
                'operation_run_id': stage.operation_run_id,
                'stamp_run_id': stage.stamp_run_id,
                'error_detail': stage.error_detail,
            }
            for stage in plan.stages.order_by('pk')
        ],
    }
