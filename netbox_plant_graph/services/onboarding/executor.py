from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from netbox_plant_graph.models import (
    OnboardingExecutionStage,
    OnboardingObjectLink,
    OnboardingPlan,
    OperationRun,
    StampTemplate,
)
from netbox_plant_graph.services.imports import reconcile_import_payload
from netbox_plant_graph.services.stamping_v25 import StampValidationError, apply_stamp_template_v25

from .handoff import build_handoff_dossier
from .readiness import evaluate_onboarding_readiness
from .workspace import changelog_actor_context, normalize_actor, workspace_revision


@dataclass(frozen=True)
class ExecutionResult:
    plan_id: int
    status: str
    stages: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            'plan_id': self.plan_id,
            'status': self.status,
            'stages': list(self.stages),
        }


STAGE_ORDER = (
    ('prerequisites', 'prerequisites'),
    ('stamp', 'stamp'),
    ('import', 'import'),
    ('readiness', 'readiness_audit'),
    ('handoff', 'handoff'),
)


def apply_onboarding_plan(
    plan: OnboardingPlan,
    *,
    actor=None,
    stages: list[str] | tuple[str, ...] | None = None,
) -> ExecutionResult:
    actor = normalize_actor(actor)
    if plan.status not in {'approved', 'applying', 'failed'}:
        raise ValueError('Only approved, applying, or failed onboarding plans can be applied.')
    current_revision = workspace_revision(plan.workspace)
    if current_revision != plan.workspace_revision:
        raise ValueError('Plan is stale; regenerate it before applying.')
    selected = set(stages or ())
    selected_all = not selected
    with changelog_actor_context(actor):
        plan.status = 'applying'
        plan.workspace.status = 'applying'
        plan.save(update_fields=('status', 'last_updated'))
        plan.workspace.save(update_fields=('status', 'last_updated'))
        _ensure_stages(plan)

    for stage_key, _stage_kind in STAGE_ORDER:
        if not selected_all and stage_key not in selected:
            continue
        stage = plan.stages.get(stage_key=stage_key)
        if stage.status == 'completed':
            continue
        try:
            _apply_stage(stage, actor=actor)
        except Exception as exc:
            with changelog_actor_context(actor):
                stage.status = 'failed'
                stage.error_detail = str(exc)
                stage.completed_at = timezone.now()
                stage.save(update_fields=('status', 'error_detail', 'completed_at', 'last_updated'))
                plan.status = 'failed'
                plan.workspace.status = 'blocked'
                plan.save(update_fields=('status', 'last_updated'))
                plan.workspace.save(update_fields=('status', 'last_updated'))
            return _execution_result(plan)

    with changelog_actor_context(actor):
        has_failed = plan.stages.filter(status='failed').exists()
        has_incomplete = plan.stages.exclude(status='completed').exists()
        if has_failed:
            plan.status = 'failed'
            plan.workspace.status = 'blocked'
        elif has_incomplete:
            plan.status = 'applying'
            plan.workspace.status = 'applying'
        else:
            plan.status = 'applied'
            plan.workspace.status = 'applied'
        plan.save(update_fields=('status', 'last_updated'))
        plan.workspace.save(update_fields=('status', 'last_updated'))
    return _execution_result(plan)


def resume_onboarding_plan(plan: OnboardingPlan, *, actor=None) -> ExecutionResult:
    return apply_onboarding_plan(plan, actor=actor)


def _ensure_stages(plan: OnboardingPlan) -> None:
    payload = plan.plan_payload or {}
    for stage_key, stage_kind in STAGE_ORDER:
        if stage_key == 'stamp' and not (payload.get('stamp') or {}).get('available'):
            continue
        if stage_key == 'import' and not ((payload.get('imports') or {}).get('payload') or {}).get('items'):
            continue
        OnboardingExecutionStage.objects.get_or_create(
            plan=plan,
            stage_key=stage_key,
            defaults={'stage_kind': stage_kind, 'status': 'pending'},
        )


def _apply_stage(stage: OnboardingExecutionStage, *, actor=None) -> None:
    with changelog_actor_context(actor):
        stage.status = 'running'
        stage.started_at = stage.started_at or timezone.now()
        stage.error_detail = ''
        stage.save(update_fields=('status', 'started_at', 'error_detail', 'last_updated'))
    if stage.stage_key == 'prerequisites':
        result = _apply_prerequisite_stage(stage)
    elif stage.stage_key == 'stamp':
        result = _apply_stamp_stage(stage, actor=actor)
    elif stage.stage_key == 'import':
        result = _apply_import_stage(stage, actor=actor)
    elif stage.stage_key == 'readiness':
        result = _apply_readiness_stage(stage, actor=actor)
    elif stage.stage_key == 'handoff':
        result = _apply_handoff_stage(stage)
    else:
        result = {'status': 'skipped', 'message': f'Unknown stage {stage.stage_key!r}.'}
    with changelog_actor_context(actor):
        stage.status = 'completed'
        stage.result = result
        stage.completed_at = timezone.now()
        stage.save(update_fields=('status', 'result', 'completed_at', 'last_updated'))


def _apply_prerequisite_stage(stage: OnboardingExecutionStage) -> dict[str, Any]:
    plan = stage.plan
    blocking = (plan.prerequisite_plan or {}).get('summary', {}).get('blocking', 0)
    if blocking:
        raise ValueError('Prerequisites are still blocking this onboarding plan.')
    return {'status': 'completed', 'blocking': 0}


def _apply_stamp_stage(stage: OnboardingExecutionStage, *, actor=None) -> dict[str, Any]:
    payload = stage.plan.plan_payload or {}
    stamp = payload.get('stamp') or {}
    template_id = stamp.get('template_id')
    if not template_id:
        return {'status': 'skipped', 'reason': 'No stamp template selected.'}
    template = StampTemplate.objects.get(pk=template_id)
    parameters = stamp.get('parameters') or {}
    try:
        apply_result = apply_stamp_template_v25(
            template=template,
            fabric_name=parameters.get('fabric_name') or payload.get('target', {}).get('fabric_name'),
            fabric_slug=parameters.get('fabric_slug') or payload.get('target', {}).get('fabric_slug'),
            source_bindings=parameters.get('source_bindings') or {},
            creation_options=parameters.get('creation_options') or {},
            actor=actor,
            phase=parameters.get('phase') or None,
        )
    except StampValidationError as exc:
        raise ValueError(f'Stamp validation failed: {exc}') from exc
    execution = apply_result.execution
    with changelog_actor_context(actor), transaction.atomic():
        stage.stamp_run = execution.stamp_run
        stage.save(update_fields=('stamp_run', 'last_updated'))
        stage.plan.workspace.fabric = execution.fabric
        stage.plan.workspace.save(update_fields=('fabric', 'last_updated'))
        OnboardingObjectLink.objects.create(
            workspace=stage.plan.workspace,
            plan=stage.plan,
            stage=stage,
            link_kind='report',
            object_type=ContentType.objects.get_for_model(execution.stamp_run, for_concrete_model=False),
            object_id=execution.stamp_run.pk,
            label=f'StampRun #{execution.stamp_run.pk}',
        )
        OnboardingObjectLink.objects.create(
            workspace=stage.plan.workspace,
            plan=stage.plan,
            stage=stage,
            link_kind='applied_object',
            object_type=ContentType.objects.get_for_model(execution.fabric, for_concrete_model=False),
            object_id=execution.fabric.pk,
            label=f'Fabric {execution.fabric}',
        )
    return {
        'status': 'completed',
        'fabric_id': execution.fabric.pk,
        'stamp_run_id': execution.stamp_run.pk,
        'resolved_path_count': len(getattr(execution, 'resolved_paths', ()) or ()),
    }


def _apply_import_stage(stage: OnboardingExecutionStage, *, actor=None) -> dict[str, Any]:
    payload = stage.plan.plan_payload or {}
    import_payload = (payload.get('imports') or {}).get('payload') or {}
    if not import_payload.get('items'):
        return {'status': 'skipped', 'reason': 'No import items in plan.'}
    import_plan = reconcile_import_payload(import_payload, apply=True)
    report_payload = import_plan.to_dict()
    with changelog_actor_context(actor), transaction.atomic():
        operation_run = OperationRun.objects.create(
            profile='import_reconciliation',
            status='completed' if not import_plan.has_conflicts else 'failed',
            fabric=stage.plan.workspace.fabric,
            initiated_by=actor,
            parameters={'workspace_id': stage.plan.workspace_id, 'plan_id': stage.plan_id, 'payload': import_payload},
            result=report_payload,
            metadata={
                'operation_kind': 'import_reconciliation',
                'schema': 'v2.import_reconciliation.report.v1',
                'onboarding_workspace_id': stage.plan.workspace_id,
                'onboarding_plan_id': stage.plan_id,
            },
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        stage.operation_run = operation_run
        stage.save(update_fields=('operation_run', 'last_updated'))
        OnboardingObjectLink.objects.create(
            workspace=stage.plan.workspace,
            plan=stage.plan,
            stage=stage,
            link_kind='report',
            object_type=ContentType.objects.get_for_model(operation_run, for_concrete_model=False),
            object_id=operation_run.pk,
            label=f'Import report #{operation_run.pk}',
        )
    if import_plan.has_conflicts:
        raise ValueError('Import apply produced conflicts; inspect the linked import report.')
    return {
        'status': 'completed',
        'operation_run_id': operation_run.pk,
        'summary': report_payload.get('summary') or {},
        'committed': report_payload.get('committed'),
    }


def _apply_readiness_stage(stage: OnboardingExecutionStage, *, actor=None) -> dict[str, Any]:
    readiness = evaluate_onboarding_readiness(stage.plan.workspace, actor=actor)
    if readiness.get('status') == 'blocked':
        raise ValueError('Readiness gate is blocked.')
    return readiness


def _apply_handoff_stage(stage: OnboardingExecutionStage) -> dict[str, Any]:
    return build_handoff_dossier(stage.plan.workspace)


def _execution_result(plan: OnboardingPlan) -> ExecutionResult:
    return ExecutionResult(
        plan_id=plan.pk,
        status=plan.status,
        stages=tuple(
            {
                'id': stage.pk,
                'stage_key': stage.stage_key,
                'stage_kind': stage.stage_kind,
                'status': stage.status,
                'error_detail': stage.error_detail,
            }
            for stage in plan.stages.order_by('pk')
        ),
    )
