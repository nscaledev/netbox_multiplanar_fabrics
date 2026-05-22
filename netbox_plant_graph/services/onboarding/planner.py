from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from django.utils import timezone

from netbox_plant_graph.models import OnboardingPlan, OnboardingWorkspace, StampTemplate
from netbox_plant_graph.services.imports import reconcile_import_payload
from netbox_plant_graph.services.stamping_v25 import preview_stamp_template_v25

from .prerequisites import discover_prerequisites, prerequisite_plan
from .workspace import changelog_actor_context, normalize_actor, stable_digest, workspace_revision


PLAN_SCHEMA = 'v2.onboarding.plan/1'


def generate_onboarding_plan(workspace: OnboardingWorkspace, *, actor=None) -> OnboardingPlan:
    actor = normalize_actor(actor)
    with changelog_actor_context(actor):
        prereq_result = discover_prerequisites(workspace, actor=actor)
        payload = canonical_plan_payload(workspace, prereq_result=prereq_result.to_dict())
        plan_hash = stable_digest(payload)
        plan, created = OnboardingPlan.objects.get_or_create(
            workspace=workspace,
            plan_hash=plan_hash,
            defaults={
                'status': 'blocked' if _plan_has_blocking_issues(payload) else 'generated',
                'workspace_revision': payload['workspace']['revision'],
                'generated_by': actor,
                'prerequisite_plan': payload['prerequisites'],
                'stamp_preview': payload['stamp']['preview'],
                'import_plan': payload['imports']['plan'],
                'audit_projection': payload['audit_projection'],
                'impact_projection': payload['impact_projection'],
                'readiness_projection': payload['readiness_projection'],
                'rollback_preview': payload['rollback'],
                'retry_preview': payload['retry'],
                'plan_payload': payload,
                'metadata': {'schema': PLAN_SCHEMA},
            },
        )
        if not created:
            plan.status = 'blocked' if _plan_has_blocking_issues(payload) else 'generated'
            plan.workspace_revision = payload['workspace']['revision']
            plan.generated_by = actor
            plan.prerequisite_plan = payload['prerequisites']
            plan.stamp_preview = payload['stamp']['preview']
            plan.import_plan = payload['imports']['plan']
            plan.audit_projection = payload['audit_projection']
            plan.impact_projection = payload['impact_projection']
            plan.readiness_projection = payload['readiness_projection']
            plan.rollback_preview = payload['rollback']
            plan.retry_preview = payload['retry']
            plan.plan_payload = payload
            plan.save()
        workspace.current_plan = plan
        workspace.status = 'blocked' if plan.status == 'blocked' else 'awaiting_approval'
        workspace.save(update_fields=('current_plan', 'status', 'last_updated'))
    return plan


def canonical_plan_payload(workspace: OnboardingWorkspace, *, prereq_result: dict[str, Any] | None = None) -> dict[str, Any]:
    revision = workspace_revision(workspace)
    import_payload = _workspace_import_payload(workspace)
    stamp_payload = _workspace_stamp_payload(workspace)
    prereqs = prerequisite_plan(workspace)
    if prereq_result:
        prereqs['latest_discovery'] = prereq_result

    stamp_preview = _build_stamp_preview(workspace, stamp_payload)
    import_plan = _build_import_plan(import_payload)
    issues = []
    issues.extend(_stamp_issues(stamp_preview))
    issues.extend(_import_issues(import_plan))
    if prereqs.get('summary', {}).get('blocking', 0):
        issues.append(
            {
                'severity': 'error',
                'code': 'open_prerequisites',
                'message': 'Workspace has unresolved or blocked prerequisites.',
            }
        )
    readiness_projection = {
        'status': 'not_available',
        'reason': 'Planned graph simulation is not implemented; readiness runs after apply.',
    }
    return _jsonable(
        {
            'schema': PLAN_SCHEMA,
            'workspace': {
                'id': workspace.pk,
                'slug': workspace.slug,
                'revision': revision,
            },
            'target': {
                'fabric_name': workspace.target_fabric_name or workspace.name,
                'fabric_slug': workspace.target_fabric_slug or workspace.slug,
                'fabric_class': workspace.fabric_class,
                'fabric_id': workspace.fabric_id,
                'site_id': workspace.site_id,
                'architecture_id': workspace.architecture_id,
                'architecture': _architecture_payload(workspace),
            },
            'sources': _source_manifest(workspace),
            'prerequisites': prereqs,
            'stamp': stamp_preview,
            'imports': {
                'payload': import_payload,
                'plan': import_plan,
            },
            'audit_projection': {
                'status': 'deferred_until_apply',
                'reason': 'Topology integrity audit requires applied topology rows.',
            },
            'impact_projection': {
                'status': 'deferred_until_apply',
                'reason': 'Operational impact models require applied topology rows.',
            },
            'readiness_projection': readiness_projection,
            'rollback': stamp_preview.get('preview', {}).get('rollback') or {},
            'retry': stamp_preview.get('preview', {}).get('retry') or {},
            'issues': issues,
            'next_actions': _next_actions(issues),
        }
    )


def approve_onboarding_plan(plan: OnboardingPlan, *, actor=None, acknowledgements: list[dict[str, Any]] | None = None) -> OnboardingPlan:
    actor = normalize_actor(actor)
    current_revision = workspace_revision(plan.workspace)
    if current_revision != plan.workspace_revision:
        raise ValueError('Plan is stale; regenerate it before approval.')
    if _plan_has_blocking_issues(plan.plan_payload or {}):
        raise ValueError('Plan has blocking issues and cannot be approved.')
    with changelog_actor_context(actor):
        plan.status = 'approved'
        plan.approved_by = actor
        plan.approved_at = timezone.now()
        plan.warning_acknowledgements = acknowledgements or []
        plan.save(update_fields=('status', 'approved_by', 'approved_at', 'warning_acknowledgements', 'last_updated'))
        plan.workspace.current_plan = plan
        plan.workspace.status = 'approved'
        plan.workspace.save(update_fields=('current_plan', 'status', 'last_updated'))
    return plan


def _build_stamp_preview(workspace: OnboardingWorkspace, stamp_payload: dict[str, Any]) -> dict[str, Any]:
    template = _workspace_stamp_template(workspace, stamp_payload)
    if template is None:
        return {
            'available': False,
            'template_id': None,
            'template_slug': '',
            'preview': {},
            'issues': [
                {
                    'severity': 'warning',
                    'code': 'stamp_template_missing',
                    'message': 'No stamp template is selected for this workspace.',
                }
            ],
        }
    preview = preview_stamp_template_v25(
        template=template,
        fabric_name=stamp_payload.get('fabric_name') or workspace.target_fabric_name or workspace.name,
        fabric_slug=stamp_payload.get('fabric_slug') or workspace.target_fabric_slug or workspace.slug,
        source_bindings=stamp_payload.get('source_bindings') or {},
        creation_options=stamp_payload.get('creation_options') or {},
        phase=stamp_payload.get('phase') or None,
    )
    return {
        'available': True,
        'template_id': template.pk,
        'template_slug': template.slug,
        'parameters': stamp_payload,
        'preview': _jsonable(asdict(preview)),
        'is_valid': preview.is_valid,
        'action_counts': preview.action_counts,
    }


def _build_import_plan(import_payload: dict[str, Any]) -> dict[str, Any]:
    if not import_payload.get('items'):
        return {
            'available': False,
            'summary': {'total': 0, 'create': 0, 'update': 0, 'skip': 0, 'conflict': 0},
            'diffs': [],
            'has_conflicts': False,
        }
    plan = reconcile_import_payload(import_payload, apply=False)
    payload = plan.to_dict()
    payload['available'] = True
    payload['has_conflicts'] = plan.has_conflicts
    return _jsonable(payload)


def _workspace_import_payload(workspace: OnboardingWorkspace) -> dict[str, Any]:
    items = []
    for item in workspace.design_items.order_by('pk'):
        if item.kind in {'fabric_architecture_blueprint', 'stamp_parameters', 'manual_note', 'invalid_import_item'}:
            continue
        items.append(item.desired_state or {})
    return {
        'metadata': {
            'source': 'onboarding_workspace',
            'workspace_id': workspace.pk,
            'workspace_slug': workspace.slug,
        },
        'architecture': _architecture_payload(workspace),
        'items': items,
    }


def _workspace_stamp_payload(workspace: OnboardingWorkspace) -> dict[str, Any]:
    payload = {
        'fabric_name': workspace.target_fabric_name or workspace.name,
        'fabric_slug': workspace.target_fabric_slug or workspace.slug,
        'source_bindings': {},
        'creation_options': {},
    }
    for item in workspace.design_items.filter(kind='stamp_parameters').order_by('-last_updated', '-pk')[:1]:
        desired = item.desired_state or {}
        payload.update({key: value for key, value in desired.items() if key not in (None, '')})
    metadata = workspace.metadata or {}
    if metadata.get('stamp_template_id'):
        payload.setdefault('template_id', metadata.get('stamp_template_id'))
    return payload


def _workspace_stamp_template(workspace: OnboardingWorkspace, stamp_payload: dict[str, Any]) -> StampTemplate | None:
    template_id = stamp_payload.get('template_id') or (workspace.metadata or {}).get('stamp_template_id')
    if template_id:
        return StampTemplate.objects.filter(pk=template_id).first()
    if workspace.architecture_id:
        return StampTemplate.objects.filter(architecture=workspace.architecture).order_by('pk').first()
    return StampTemplate.objects.order_by('pk').first()


def _architecture_payload(workspace: OnboardingWorkspace) -> dict[str, Any]:
    if workspace.architecture_id:
        return {
            'id': workspace.architecture_id,
            'slug': workspace.architecture.slug,
            'version': workspace.architecture.version,
            'fabric_class': workspace.architecture.fabric_class,
        }
    return {'fabric_class': workspace.fabric_class}


def _source_manifest(workspace: OnboardingWorkspace) -> list[dict[str, Any]]:
    return [
        {
            'id': artifact.pk,
            'artifact_type': artifact.artifact_type,
            'name': artifact.name,
            'status': artifact.status,
            'content_sha256': artifact.content_sha256,
            'parser_key': artifact.parser_key,
        }
        for artifact in workspace.source_artifacts.order_by('pk')
    ]


def _stamp_issues(stamp_preview: dict[str, Any]) -> list[dict[str, Any]]:
    issues = list(stamp_preview.get('issues') or [])
    preview = stamp_preview.get('preview') or {}
    issues.extend(preview.get('issues') or [])
    return issues


def _import_issues(import_plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not import_plan.get('has_conflicts'):
        return []
    return [
        {
            'severity': 'error',
            'code': 'import_conflicts',
            'message': 'Import dry-run contains conflicts.',
        }
    ]


def _plan_has_blocking_issues(payload: dict[str, Any]) -> bool:
    return any(issue.get('severity') == 'error' for issue in payload.get('issues') or ())


def _next_actions(issues: list[dict[str, Any]]) -> list[dict[str, str]]:
    if any(issue.get('severity') == 'error' for issue in issues):
        return [{'action': 'resolve_blockers', 'label': 'Resolve blockers and regenerate plan'}]
    if issues:
        return [{'action': 'approve_with_acknowledgement', 'label': 'Acknowledge warnings and approve plan'}]
    return [{'action': 'approve', 'label': 'Approve plan'}]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    return json.loads(json.dumps(value, default=str))
