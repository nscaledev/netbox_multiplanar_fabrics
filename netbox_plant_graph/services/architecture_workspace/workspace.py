from __future__ import annotations

from typing import Any

from netbox_plant_graph.models import (
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureSourceArtifact,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
)
from netbox_plant_graph.services.onboarding.workspace import stable_digest


def architecture_workspace_revision(workspace: ArchitectureWorkspace) -> str:
    source_rows = list(
        ArchitectureSourceArtifact.objects.filter(workspace=workspace)
        .order_by('pk')
        .values('pk', 'artifact_type', 'status', 'content_sha256', 'payload_version', 'source_label', 'parser_key', 'raw_payload')
    )
    component_rows = list(
        ArchitectureDesignComponent.objects.filter(workspace=workspace)
        .order_by('pk')
        .values('pk', 'kind', 'natural_key', 'desired_state', 'validation_status', 'validation_messages')
    )
    workspace_payload = {
        'workspace': {
            'pk': workspace.pk,
            'slug': workspace.slug,
            'workspace_kind': workspace.workspace_kind,
            'target_slug': workspace.target_slug,
            'target_version': workspace.target_version,
            'fabric_class': workspace.fabric_class,
            'base_architecture_id': workspace.base_architecture_id,
            'metadata': workspace.metadata or {},
        },
        'sources': source_rows,
        'components': component_rows,
    }
    return stable_digest(workspace_payload)


def architecture_workspace_summary(workspace: ArchitectureWorkspace) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    for status in ArchitectureSourceArtifact.objects.filter(workspace=workspace).values_list('status', flat=True):
        source_counts[status] = source_counts.get(status, 0) + 1

    component_counts: dict[str, int] = {}
    for kind in ArchitectureDesignComponent.objects.filter(workspace=workspace).values_list('kind', flat=True):
        component_counts[kind] = component_counts.get(kind, 0) + 1

    validation_status_counts: dict[str, int] = {}
    for status in ArchitectureValidationRun.objects.filter(workspace=workspace).values_list('status', flat=True):
        validation_status_counts[status] = validation_status_counts.get(status, 0) + 1

    plan_status_counts: dict[str, int] = {}
    for status in ArchitecturePublishPlan.objects.filter(workspace=workspace).values_list('status', flat=True):
        plan_status_counts[status] = plan_status_counts.get(status, 0) + 1

    return {
        'workspace_id': workspace.pk,
        'status': workspace.status,
        'revision': architecture_workspace_revision(workspace) if workspace.pk else '',
        'source_counts': source_counts,
        'component_counts': component_counts,
        'validation_status_counts': validation_status_counts,
        'plan_status_counts': plan_status_counts,
        'current_plan_id': workspace.current_plan_id,
        'published_architecture_id': workspace.published_architecture_id,
        'validation': workspace.validation_summary or {},
    }
