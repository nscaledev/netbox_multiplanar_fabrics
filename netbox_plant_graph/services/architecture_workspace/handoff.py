from __future__ import annotations

from typing import Any

from netbox_plant_graph.models import ArchitectureWorkspace

from .workspace import architecture_workspace_summary


def build_architecture_handoff_dossier(workspace: ArchitectureWorkspace) -> dict[str, Any]:
    current_plan = workspace.current_plan
    return {
        'schema': 'v2.architecture_workspace.handoff/1',
        'workspace': {
            'id': workspace.pk,
            'name': workspace.name,
            'slug': workspace.slug,
            'workspace_kind': workspace.workspace_kind,
            'status': workspace.status,
            'target_slug': workspace.target_slug,
            'target_version': workspace.target_version,
            'fabric_class': workspace.fabric_class,
            'base_architecture_id': workspace.base_architecture_id,
            'published_architecture_id': workspace.published_architecture_id,
            'current_plan_id': workspace.current_plan_id,
        },
        'summary': architecture_workspace_summary(workspace),
        'sources': [
            {
                'id': artifact.pk,
                'artifact_type': artifact.artifact_type,
                'name': artifact.name,
                'status': artifact.status,
                'source_uri': artifact.source_uri,
                'content_sha256': artifact.content_sha256,
                'payload_version': artifact.payload_version,
                'source_label': artifact.source_label,
                'parser_key': artifact.parser_key,
            }
            for artifact in workspace.source_artifacts.order_by('pk')
        ],
        'components': [
            {
                'id': component.pk,
                'kind': component.kind,
                'natural_key': component.natural_key,
                'validation_status': component.validation_status,
                'source_artifact_id': component.source_artifact_id,
                'provenance': component.provenance or {},
            }
            for component in workspace.design_components.order_by('kind', 'natural_key', 'pk')
        ],
        'validation_runs': [
            {
                'id': run.pk,
                'status': run.status,
                'validation_kind': run.validation_kind,
                'workspace_revision': run.workspace_revision,
                'summary': run.summary or {},
                'issues': run.issues or [],
            }
            for run in workspace.validation_runs.order_by('-created', '-pk')[:10]
        ],
        'current_plan': {
            'id': current_plan.pk,
            'status': current_plan.status,
            'plan_hash': current_plan.plan_hash,
            'workspace_revision': current_plan.workspace_revision,
            'validation_summary': current_plan.validation_summary or {},
            'result': current_plan.result or {},
        }
        if current_plan
        else None,
    }
