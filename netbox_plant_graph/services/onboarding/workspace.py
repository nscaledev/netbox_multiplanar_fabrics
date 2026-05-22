from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from django.contrib.auth import get_user_model
from netbox.context import current_request

from netbox_plant_graph.models import (
    AuditEvent,
    OnboardingDesignItem,
    OnboardingExecutionStage,
    OnboardingPlan,
    OnboardingPrerequisite,
    OnboardingSourceArtifact,
    OnboardingWorkspace,
)


def normalize_actor(actor):
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        return None
    return actor


def fallback_changelog_user():
    User = get_user_model()
    user = (
        User.objects.filter(is_active=True, is_superuser=True).order_by('pk').first()
        or User.objects.filter(is_active=True, is_staff=True).order_by('pk').first()
        or User.objects.filter(is_active=True).order_by('pk').first()
    )
    if user is not None:
        return user

    username_field = getattr(User, 'USERNAME_FIELD', 'username')
    system_identifier = 'netbox-plant-graph-system'
    system_user, created = User.objects.get_or_create(**{username_field: system_identifier})
    if created and hasattr(system_user, 'set_unusable_password'):
        system_user.set_unusable_password()
        update_fields = []
        if hasattr(system_user, 'password'):
            update_fields.append('password')
        if hasattr(system_user, 'is_active'):
            system_user.is_active = True
            update_fields.append('is_active')
        if update_fields:
            system_user.save(update_fields=update_fields)
    return system_user


@contextmanager
def changelog_actor_context(actor=None):
    """
    Prevent NetBox's ObjectChange writer from receiving AnonymousUser in local
    noauth development while still preserving actor=None on plugin data.
    """
    request_token = None
    request = current_request.get()
    if request is not None and not getattr(getattr(request, 'user', None), 'is_authenticated', False):
        changelog_user = normalize_actor(actor) or fallback_changelog_user()
        request_token = current_request.set(SimpleNamespace(id=getattr(request, 'id', None), user=changelog_user))
    try:
        yield
    finally:
        if request_token is not None:
            current_request.reset(request_token)


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode('utf-8')).hexdigest()


def workspace_revision(workspace: OnboardingWorkspace) -> str:
    source_rows = list(
        OnboardingSourceArtifact.objects.filter(workspace=workspace)
        .order_by('pk')
        .values('pk', 'artifact_type', 'status', 'content_sha256', 'payload_version', 'source_label', 'parser_key', 'raw_payload')
    )
    design_rows = list(
        OnboardingDesignItem.objects.filter(workspace=workspace)
        .order_by('pk')
        .values('pk', 'kind', 'natural_key', 'desired_state', 'validation_status', 'planned_object_type_id', 'planned_object_id')
    )
    prereq_rows = list(
        OnboardingPrerequisite.objects.filter(workspace=workspace)
        .order_by('pk')
        .values(
            'pk',
            'requirement_key',
            'object_model',
            'role',
            'desired_identity',
            'resolution_mode',
            'resolved_object_type_id',
            'resolved_object_id',
            'planned_create',
            'status',
        )
    )
    workspace_payload = {
        'workspace': {
            'pk': workspace.pk,
            'slug': workspace.slug,
            'target_fabric_slug': workspace.target_fabric_slug,
            'fabric_class': workspace.fabric_class,
            'fabric_id': workspace.fabric_id,
            'architecture_id': workspace.architecture_id,
            'site_id': workspace.site_id,
            'location_id': workspace.location_id,
            'tenant_id': workspace.tenant_id,
            'metadata': workspace.metadata or {},
        },
        'sources': source_rows,
        'design_items': design_rows,
        'prerequisites': prereq_rows,
    }
    return stable_digest(workspace_payload)


def workspace_summary(workspace: OnboardingWorkspace) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    for status in (
        OnboardingSourceArtifact.objects.filter(workspace=workspace)
        .values_list('status', flat=True)
        .order_by('status')
    ):
        source_counts[status] = source_counts.get(status, 0) + 1

    design_counts: dict[str, int] = {}
    for kind in OnboardingDesignItem.objects.filter(workspace=workspace).values_list('kind', flat=True):
        design_counts[kind] = design_counts.get(kind, 0) + 1

    prereq_counts: dict[str, int] = {}
    for status in OnboardingPrerequisite.objects.filter(workspace=workspace).values_list('status', flat=True):
        prereq_counts[status] = prereq_counts.get(status, 0) + 1

    stage_counts: dict[str, int] = {}
    if workspace.current_plan_id:
        for stage_status in OnboardingExecutionStage.objects.filter(plan=workspace.current_plan).values_list('status', flat=True):
            stage_counts[stage_status] = stage_counts.get(stage_status, 0) + 1

    return {
        'workspace_id': workspace.pk,
        'status': workspace.status,
        'revision': workspace_revision(workspace) if workspace.pk else '',
        'source_counts': source_counts,
        'design_counts': design_counts,
        'prerequisite_counts': prereq_counts,
        'plan_count': OnboardingPlan.objects.filter(workspace=workspace).count(),
        'current_plan_id': workspace.current_plan_id,
        'stage_counts': stage_counts,
        'readiness': workspace.readiness_summary or {},
    }


def transition_workspace(workspace: OnboardingWorkspace, status: str, actor=None, message: str = '') -> OnboardingWorkspace:
    old_status = workspace.status
    actor = normalize_actor(actor)
    with changelog_actor_context(actor):
        workspace.status = status
        workspace.save(update_fields=('status', 'last_updated'))
        AuditEvent.objects.create(
            fabric=workspace.fabric,
            event_type='operation_run',
            actor=actor,
            subject=workspace,
            outcome='ok',
            message=message or f'Onboarding workspace moved from {old_status} to {status}.',
            payload={
                'workspace_id': workspace.pk,
                'old_status': old_status,
                'new_status': status,
            },
            metadata={'workflow': 'onboarding_workspace'},
        )
    return workspace
