from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from django.utils import timezone

from netbox_plant_graph.models import (
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
    FabricArchitecture,
)
from netbox_plant_graph.services.architecture_schema import ARCHITECTURE_SCHEMA_CONTRACT_VERSION, validate_architecture_schema
from netbox_plant_graph.services.blueprint_registry import (
    BlueprintRegistryEntry,
    architecture_definition_from_payload,
    check_blueprint_device_type_compatibility,
    validate_parameter_schema,
)
from netbox_plant_graph.services.imports import reconcile_import_payload
from netbox_plant_graph.services.onboarding.workspace import changelog_actor_context, normalize_actor, stable_digest

from .workspace import architecture_workspace_revision, architecture_workspace_summary


PLAN_SCHEMA = 'v2.architecture_workspace.publish_plan/1'


@dataclass(frozen=True)
class ArchitectureValidationResult:
    status: str
    summary: dict[str, Any]
    issues: tuple[dict[str, Any], ...]
    import_plan: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            'status': self.status,
            'summary': self.summary,
            'issues': list(self.issues),
            'import_plan': self.import_plan,
        }


def validate_architecture_workspace(workspace: ArchitectureWorkspace, *, actor=None) -> ArchitectureValidationRun:
    actor = normalize_actor(actor)
    result = _validate_workspace_payload(workspace)
    revision = architecture_workspace_revision(workspace)
    with changelog_actor_context(actor):
        run = ArchitectureValidationRun.objects.create(
            workspace=workspace,
            status=result.status,
            workspace_revision=revision,
            executed_by=actor,
            summary=result.summary,
            issues=list(result.issues),
            import_plan=result.import_plan,
            metadata={'schema': 'v2.architecture_workspace.validation/1'},
        )
        workspace.validation_summary = result.to_dict()
        workspace.status = 'blocked' if result.status == 'failed' else 'ready'
        workspace.save(update_fields=('validation_summary', 'status', 'last_updated'))
    return run


def generate_architecture_publish_plan(
    workspace: ArchitectureWorkspace,
    *,
    actor=None,
) -> ArchitecturePublishPlan:
    actor = normalize_actor(actor)
    validation = _validate_workspace_payload(workspace)
    payload = _canonical_plan_payload(workspace, validation=validation)
    plan_hash = stable_digest(payload)
    plan_status = 'blocked' if validation.status == 'failed' else 'generated'
    with changelog_actor_context(actor):
        plan, created = ArchitecturePublishPlan.objects.get_or_create(
            workspace=workspace,
            plan_hash=plan_hash,
            defaults={
                'status': plan_status,
                'workspace_revision': payload['workspace']['revision'],
                'generated_by': actor,
                'publish_payload': payload['publish_payload'],
                'validation_summary': validation.to_dict(),
                'import_plan': validation.import_plan,
                'metadata': {'schema': PLAN_SCHEMA, 'plan_payload': payload},
            },
        )
        if not created:
            plan.status = plan_status
            plan.workspace_revision = payload['workspace']['revision']
            plan.generated_by = actor
            plan.publish_payload = payload['publish_payload']
            plan.validation_summary = validation.to_dict()
            plan.import_plan = validation.import_plan
            plan.metadata = {'schema': PLAN_SCHEMA, 'plan_payload': payload}
            plan.save()
        workspace.current_plan = plan
        workspace.validation_summary = validation.to_dict()
        workspace.status = 'blocked' if plan.status == 'blocked' else 'awaiting_approval'
        workspace.save(update_fields=('current_plan', 'validation_summary', 'status', 'last_updated'))
    return plan


def approve_architecture_publish_plan(
    plan: ArchitecturePublishPlan,
    *,
    actor=None,
    acknowledgements: list[dict[str, Any]] | None = None,
) -> ArchitecturePublishPlan:
    actor = normalize_actor(actor)
    current_revision = architecture_workspace_revision(plan.workspace)
    if current_revision != plan.workspace_revision:
        raise ValueError('Architecture publish plan is stale; regenerate it before approval.')
    if plan.status == 'blocked':
        raise ValueError('Blocked architecture publish plans cannot be approved.')
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


def publish_architecture_plan(plan: ArchitecturePublishPlan, *, actor=None) -> ArchitecturePublishPlan:
    actor = normalize_actor(actor)
    if plan.status not in {'approved', 'publishing', 'failed'}:
        raise ValueError('Only approved, publishing, or failed architecture publish plans can be published.')
    current_revision = architecture_workspace_revision(plan.workspace)
    if current_revision != plan.workspace_revision:
        raise ValueError('Architecture publish plan is stale; regenerate it before publishing.')
    with changelog_actor_context(actor):
        plan.status = 'publishing'
        plan.workspace.status = 'publishing'
        plan.save(update_fields=('status', 'last_updated'))
        plan.workspace.save(update_fields=('status', 'last_updated'))

    try:
        import_plan = reconcile_import_payload(plan.publish_payload, apply=True)
        result = import_plan.to_dict()
        if import_plan.has_conflicts:
            raise ValueError('Architecture publish produced import conflicts.')
        architecture = _architecture_from_publish_result(import_plan)
    except Exception as exc:
        with changelog_actor_context(actor):
            plan.status = 'failed'
            plan.result = {'error': str(exc)}
            plan.workspace.status = 'blocked'
            plan.save(update_fields=('status', 'result', 'last_updated'))
            plan.workspace.save(update_fields=('status', 'last_updated'))
        raise

    with changelog_actor_context(actor):
        plan.status = 'published'
        plan.result = result
        plan.save(update_fields=('status', 'result', 'last_updated'))
        if architecture is not None:
            plan.workspace.published_architecture = architecture
        plan.workspace.status = 'published'
        plan.workspace.save(update_fields=('published_architecture', 'status', 'last_updated'))
    return plan


def _validate_workspace_payload(workspace: ArchitectureWorkspace) -> ArchitectureValidationResult:
    issues: list[dict[str, Any]] = []
    publish_payload = _publish_payload(workspace)
    import_plan = reconcile_import_payload(publish_payload, apply=False)
    import_payload = import_plan.to_dict()
    if import_plan.has_conflicts:
        issues.append(
            {
                'severity': 'error',
                'code': 'architecture_import_conflicts',
                'message': 'Architecture import dry-run contains conflicts.',
            }
        )

    blueprint_item = _blueprint_item(workspace)
    if blueprint_item is None:
        issues.append(
            {
                'severity': 'error',
                'code': 'blueprint_missing',
                'message': 'Architecture workspace does not contain a fabric_architecture_blueprint component.',
            }
        )
        summary = _summary(workspace, publish_payload=publish_payload, import_plan=import_payload, definition=None)
        return ArchitectureValidationResult(status='failed', summary=summary, issues=tuple(issues), import_plan=import_payload)

    definition_payload = _definition_payload(blueprint_item)
    definition = None
    try:
        definition = architecture_definition_from_payload(definition_payload)
    except ValueError as exc:
        issues.append({'severity': 'error', 'code': 'architecture_payload_invalid', 'message': str(exc)})
    if definition is not None:
        schema_result = validate_architecture_schema(definition)
        for error in schema_result.errors:
            issues.append(
                {
                    'severity': 'error',
                    'code': f'architecture_schema.{error.code}',
                    'path': error.path,
                    'message': error.message,
                    'context': dict(error.context),
                }
            )
        parameter_schema = blueprint_item.get('parameter_schema') or definition_payload.get('parameter_schema') or {}
        for issue in validate_parameter_schema(parameter_schema):
            payload = issue.to_dict()
            payload['severity'] = payload.get('severity') or 'error'
            issues.append(payload)
        required_device_types = blueprint_item.get('required_device_types') or definition_payload.get('required_device_types') or {}
        if required_device_types:
            entry = BlueprintRegistryEntry(
                definition=definition,
                parameter_schema=parameter_schema,
                required_device_types=required_device_types,
                lifecycle=str(blueprint_item.get('lifecycle') or 'active'),
                successor_version=str(blueprint_item.get('successor_version') or ''),
            )
            for issue in check_blueprint_device_type_compatibility(entry).issues:
                payload = issue.to_dict()
                payload['severity'] = 'warning'
                issues.append(payload)

    has_errors = any(issue.get('severity') == 'error' for issue in issues)
    has_warnings = any(issue.get('severity') == 'warning' for issue in issues)
    status = 'failed' if has_errors else 'warning' if has_warnings else 'passed'
    summary = _summary(workspace, publish_payload=publish_payload, import_plan=import_payload, definition=definition)
    return ArchitectureValidationResult(status=status, summary=summary, issues=tuple(issues), import_plan=import_payload)


def _canonical_plan_payload(
    workspace: ArchitectureWorkspace,
    *,
    validation: ArchitectureValidationResult,
) -> dict[str, Any]:
    return {
        'schema': PLAN_SCHEMA,
        'workspace': {
            'id': workspace.pk,
            'slug': workspace.slug,
            'revision': architecture_workspace_revision(workspace),
        },
        'target': {
            'target_slug': workspace.target_slug,
            'target_version': workspace.target_version,
            'fabric_class': workspace.fabric_class,
            'base_architecture_id': workspace.base_architecture_id,
        },
        'publish_payload': _publish_payload(workspace),
        'validation': validation.to_dict(),
        'issues': list(validation.issues),
        'next_actions': _next_actions(validation),
    }


def _publish_payload(workspace: ArchitectureWorkspace) -> dict[str, Any]:
    item = _blueprint_item(workspace)
    return {
        'metadata': {
            'source': 'architecture_workspace',
            'workspace_id': workspace.pk,
            'workspace_slug': workspace.slug,
        },
        'items': [item] if item else [],
    }


def _blueprint_item(workspace: ArchitectureWorkspace) -> dict[str, Any] | None:
    component = (
        ArchitectureDesignComponent.objects.filter(workspace=workspace, kind='fabric_architecture_blueprint')
        .order_by('-last_updated', '-pk')
        .first()
    )
    if component is not None:
        return dict(component.desired_state or {})
    return _blueprint_item_from_base_architecture(workspace)


def _blueprint_item_from_base_architecture(workspace: ArchitectureWorkspace) -> dict[str, Any] | None:
    if not workspace.base_architecture_id:
        return None
    metadata = workspace.base_architecture.metadata or {}
    blueprint = metadata.get('blueprint') if isinstance(metadata, Mapping) else {}
    definition = blueprint.get('definition') if isinstance(blueprint, Mapping) else None
    if not isinstance(definition, Mapping):
        return None
    return {
        'kind': 'fabric_architecture_blueprint',
        'definition': dict(definition),
        'parameter_schema': dict(blueprint.get('parameter_schema') or {}),
        'required_device_types': dict(blueprint.get('required_device_types') or {}),
        'schema_contract_version': blueprint.get('schema_contract_version') or ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
        'lifecycle': blueprint.get('lifecycle') or 'active',
        'successor_version': blueprint.get('successor_version') or '',
        'stamp_templates': {},
    }


def _definition_payload(blueprint_item: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ('definition', 'architecture', 'blueprint'):
        value = blueprint_item.get(key)
        if isinstance(value, Mapping):
            if key == 'blueprint' and isinstance(value.get('definition'), Mapping):
                return value['definition']
            return value
    return blueprint_item


def _summary(
    workspace: ArchitectureWorkspace,
    *,
    publish_payload: dict[str, Any],
    import_plan: dict[str, Any],
    definition,
) -> dict[str, Any]:
    component_counts: dict[str, int] = {}
    for kind in ArchitectureDesignComponent.objects.filter(workspace=workspace).values_list('kind', flat=True):
        component_counts[kind] = component_counts.get(kind, 0) + 1
    return {
        'workspace': architecture_workspace_summary(workspace),
        'component_counts': component_counts,
        'publish_item_count': len(publish_payload.get('items') or []),
        'import_summary': import_plan.get('summary') or {},
        'definition': {
            'slug': getattr(definition, 'slug', None),
            'version': getattr(definition, 'version', None),
            'plane_count': getattr(definition, 'plane_count', None),
            'fabric_class': getattr(definition, 'fabric_class', None),
            'role_count': len(getattr(definition, 'roles', ()) or ()),
            'transfer_pattern_count': len(getattr(definition, 'transfer_patterns', ()) or ()),
            'allocation_rule_set_count': len(getattr(definition, 'allocation_rule_sets', ()) or ()),
        },
    }


def _next_actions(validation: ArchitectureValidationResult) -> list[dict[str, str]]:
    if validation.status == 'failed':
        return [{'action': 'fix_components', 'label': 'Fix architecture components and regenerate plan'}]
    if validation.status == 'warning':
        return [{'action': 'approve_with_acknowledgement', 'label': 'Acknowledge warnings and approve publish plan'}]
    return [{'action': 'approve', 'label': 'Approve publish plan'}]


def _architecture_from_publish_result(import_plan) -> FabricArchitecture | None:
    for diff in import_plan.diffs:
        if diff.kind == 'fabric_architecture_blueprint' and diff.object_id:
            return FabricArchitecture.objects.filter(pk=diff.object_id).first()
    return None
