from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from netbox_plant_graph.models import OnboardingPrerequisite, OnboardingWorkspace

from .workspace import changelog_actor_context


@dataclass(frozen=True)
class PrerequisiteResult:
    created: int
    updated: int
    open_count: int
    blocked_count: int
    requirements: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'created': self.created,
            'updated': self.updated,
            'open_count': self.open_count,
            'blocked_count': self.blocked_count,
            'requirements': list(self.requirements),
        }


def discover_prerequisites(workspace: OnboardingWorkspace, *, actor=None) -> PrerequisiteResult:
    discovered = list(_workspace_prerequisites(workspace))
    created = 0
    updated = 0
    with changelog_actor_context(actor), transaction.atomic():
        seen_keys = set()
        for requirement in discovered:
            seen_keys.add(requirement['requirement_key'])
            prereq, was_created = OnboardingPrerequisite.objects.update_or_create(
                workspace=workspace,
                requirement_key=requirement['requirement_key'],
                defaults={
                    'design_item': requirement.get('design_item'),
                    'object_model': requirement['object_model'],
                    'role': requirement.get('role', ''),
                    'desired_identity': requirement.get('desired_identity') or {},
                    'planned_create': requirement.get('planned_create') or {},
                    'status': requirement.get('status', 'open'),
                    'metadata': requirement.get('metadata') or {},
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
            _auto_bind_prerequisite(prereq)
        stale = OnboardingPrerequisite.objects.filter(workspace=workspace, status='open').exclude(requirement_key__in=seen_keys)
        stale.update(status='deferred', defer_reason='No longer discovered in latest workspace prerequisite pass.')

    queryset = OnboardingPrerequisite.objects.filter(workspace=workspace).order_by('status', 'requirement_key', 'pk')
    rows = tuple(_prerequisite_row(prereq) for prereq in queryset)
    return PrerequisiteResult(
        created=created,
        updated=updated,
        open_count=sum(1 for row in rows if row['status'] == 'open'),
        blocked_count=sum(1 for row in rows if row['status'] == 'blocked'),
        requirements=rows,
    )


def prerequisite_plan(workspace: OnboardingWorkspace) -> dict[str, Any]:
    queryset = OnboardingPrerequisite.objects.filter(workspace=workspace).order_by('status', 'requirement_key', 'pk')
    rows = tuple(_prerequisite_row(prereq) for prereq in queryset)
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row['status']] = status_counts.get(row['status'], 0) + 1
    return {
        'summary': {
            'total': len(rows),
            'by_status': status_counts,
            'blocking': status_counts.get('open', 0) + status_counts.get('blocked', 0),
        },
        'requirements': list(rows),
    }


def resolve_prerequisite(
    prerequisite: OnboardingPrerequisite,
    *,
    mode: str,
    object_id: int | None = None,
    object_model: str = '',
    planned_create: Mapping[str, Any] | None = None,
    defer_reason: str = '',
    actor=None,
) -> OnboardingPrerequisite:
    with changelog_actor_context(actor):
        prerequisite.resolution_mode = mode
        prerequisite.defer_reason = defer_reason or ''
        if planned_create is not None:
            prerequisite.planned_create = dict(planned_create)
        if mode == 'bind':
            model_label = object_model or prerequisite.object_model
            model = _model_for_label(model_label)
            if model is None or object_id is None:
                prerequisite.status = 'blocked'
            else:
                prerequisite.resolved_object_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
                prerequisite.resolved_object_id = object_id
                prerequisite.status = 'resolved'
        elif mode == 'create':
            prerequisite.status = 'resolved' if prerequisite.planned_create else 'blocked'
        elif mode == 'defer':
            prerequisite.status = 'deferred'
        elif mode == 'not_required':
            prerequisite.status = 'resolved'
        else:
            prerequisite.status = 'open'
        prerequisite.save()
    return prerequisite


def _workspace_prerequisites(workspace: OnboardingWorkspace):
    if workspace.site_id is None:
        yield {
            'requirement_key': 'site:target',
            'object_model': 'dcim.site',
            'role': 'site',
            'desired_identity': {'slug': (workspace.target_fabric_slug or workspace.slug).split('-')[0]},
            'status': 'open',
        }
    if workspace.architecture_id is None:
        yield {
            'requirement_key': 'architecture:target',
            'object_model': 'netbox_plant_graph.fabricarchitecture',
            'role': 'architecture',
            'desired_identity': {'fabric_class': workspace.fabric_class},
            'status': 'open',
        }
    for item in workspace.design_items.select_related('source_artifact').order_by('pk'):
        desired = item.desired_state or {}
        for index, requirement in enumerate(desired.get('prerequisites') or ()):
            if not isinstance(requirement, Mapping):
                continue
            object_model = str(requirement.get('object_model') or requirement.get('model') or '')
            role = str(requirement.get('role') or '')
            identity = dict(requirement.get('desired_identity') or requirement.get('identity') or {})
            key = str(requirement.get('requirement_key') or f'{object_model}:{role}:{identity or index}')
            yield {
                'requirement_key': key,
                'object_model': object_model,
                'role': role,
                'desired_identity': identity,
                'planned_create': requirement.get('planned_create') or {},
                'design_item': item,
                'status': 'open',
                'metadata': {'source_design_item_id': item.pk},
            }


def _auto_bind_prerequisite(prereq: OnboardingPrerequisite) -> None:
    if prereq.status != 'open':
        return
    model = _model_for_label(prereq.object_model)
    if model is None:
        prereq.status = 'blocked'
        prereq.save(update_fields=('status', 'last_updated'))
        return
    lookup = _lookup_from_identity(prereq.desired_identity or {})
    if not lookup:
        return
    obj = model.objects.filter(**lookup).first()
    if obj is None:
        return
    prereq.resolution_mode = 'bind'
    prereq.resolved_object_type = ContentType.objects.get_for_model(model, for_concrete_model=False)
    prereq.resolved_object_id = obj.pk
    prereq.status = 'resolved'
    prereq.save(update_fields=('resolution_mode', 'resolved_object_type', 'resolved_object_id', 'status', 'last_updated'))


def _lookup_from_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    for field in ('pk', 'id', 'slug', 'name', 'model'):
        value = identity.get(field)
        if value not in (None, ''):
            return {'pk' if field == 'id' else field: value}
    return {}


def _model_for_label(model_label: str):
    if not model_label or '.' not in model_label:
        return None
    app_label, model_name = model_label.split('.', 1)
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _prerequisite_row(prereq: OnboardingPrerequisite) -> dict[str, Any]:
    return {
        'id': prereq.pk,
        'requirement_key': prereq.requirement_key,
        'object_model': prereq.object_model,
        'role': prereq.role,
        'desired_identity': prereq.desired_identity or {},
        'resolution_mode': prereq.resolution_mode,
        'resolved_object_type_id': prereq.resolved_object_type_id,
        'resolved_object_id': prereq.resolved_object_id,
        'status': prereq.status,
        'defer_reason': prereq.defer_reason,
    }
