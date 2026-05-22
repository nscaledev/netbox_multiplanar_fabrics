from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models.deletion import Collector, ProtectedError
from django.utils import timezone
from dcim.models import Device, DeviceRole, DeviceType, Interface, Site

from netbox_plant_graph.models import (
    AuditEvent,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StampRun,
    StrandTermination,
    TransferMap,
    TransportChannel,
    TransportChannelPositionMap,
)
from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_SLUG,
    ARCHITECTURE_VERSION,
    build_roce_4plane_shuffle_architecture_schema,
    shuffle_2x2_transfer_position_pairs,
    validate_roce_4plane_shuffle_architecture_fixture,
)
from netbox_plant_graph.services.architecture_schema import (
    compare_persisted_architecture_compatibility,
    validate_architecture_schema,
    validate_persisted_architecture_schema,
)
from netbox_plant_graph.services.stamp_template_validation import validate_stamp_template_spec
from netbox_plant_graph.services.stamping import HYBRID_STAMP_EXECUTORS, MiniFabricStampResult, execute_stamp_template


@dataclass(frozen=True)
class StampingValidationIssue:
    code: str
    path: str
    message: str
    severity: str = 'error'
    context: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f'{self.path}: {self.message}'


@dataclass(frozen=True)
class StampPreviewChange:
    action: str
    object_type: str
    identity: str
    summary: str
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StampNamePatternSample:
    object_type: str
    address: str
    name: str
    exists: bool = False
    collision: bool = False
    existing_object_id: int | None = None


@dataclass(frozen=True)
class StampArchitectureGate:
    fixture_valid: bool
    fixture_error_count: int
    template_architecture_slug: str | None = None
    template_architecture_version: str | None = None
    target_source: str = 'none'
    target_architecture_id: int | None = None
    target_architecture_slug: str | None = None
    target_architecture_version: str | None = None
    persisted_schema_valid: bool | None = None
    persisted_schema_error_count: int = 0
    compatibility_status: str = 'not_checked'
    compatibility_issue_count: int = 0


@dataclass(frozen=True)
class StampRollbackManifestItem:
    model_label: str
    primary_key: int
    natural_key: str
    action: str
    ownership_marker: str


@dataclass(frozen=True)
class StampRollbackPlan:
    supported: bool
    strategy: str
    message: str
    stamp_run_id: int | None = None
    manifest: tuple[StampRollbackManifestItem, ...] = ()
    issues: tuple[StampingValidationIssue, ...] = ()
    applied: bool = False
    deleted_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StampRetryPlan:
    supported: bool
    strategy: str
    operation_key: str
    classification: str = 'retryable'
    message: str = ''


@dataclass(frozen=True)
class StampOperationPreview:
    template_id: int | None
    template_slug: str
    fabric_name: str
    fabric_slug: str
    executor: str | None
    operation_key: str
    changes: tuple[StampPreviewChange, ...]
    issues: tuple[StampingValidationIssue, ...]
    rollback: StampRollbackPlan
    retry: StampRetryPlan
    name_pattern_samples: tuple[StampNamePatternSample, ...] = ()
    architecture_gate: StampArchitectureGate | None = None

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == 'error' for issue in self.issues)

    @property
    def action_counts(self) -> dict[str, int]:
        return dict(Counter(change.action for change in self.changes))

    def issues_for_code(self, code: str) -> tuple[StampingValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == code)


@dataclass(frozen=True)
class StampApplyResult:
    preview: StampOperationPreview
    execution: MiniFabricStampResult
    applied: bool = True


@dataclass(frozen=True)
class _StampOperationValidation:
    issues: tuple[StampingValidationIssue, ...]
    architecture_gate: StampArchitectureGate | None = None


class StampValidationError(ValueError):
    def __init__(self, preview: StampOperationPreview):
        self.preview = preview
        messages = '; '.join(str(issue) for issue in preview.issues if issue.severity == 'error')
        super().__init__(messages or 'Stamp operation preview is not valid.')


def preview_stamp_template_v25(
    *,
    template,
    fabric_name: str,
    fabric_slug: str,
    source_bindings: dict | None = None,
    creation_options: dict | None = None,
) -> StampOperationPreview:
    """
    Build a dry-run preview for the safe V2.5 stamping subset.

    This function reads the database to classify creates vs. updates but never
    writes rows. Use apply_stamp_template_v25() to execute the same operation.
    """
    template_slug = getattr(template, 'slug', '')
    template_id = getattr(template, 'pk', None)
    template_spec = deepcopy(getattr(template, 'template', None) or {})
    creation_options = creation_options or {}
    source_bindings = source_bindings or {}
    executor = _executor_name(template_spec)
    operation_key = f'{template_id or "unsaved"}:{template_slug}:{fabric_slug}:{executor or "unknown"}'

    validation = _validate_stamp_operation(
        template=template,
        template_spec=template_spec,
        fabric_slug=fabric_slug,
        executor=executor,
        source_bindings=source_bindings,
        creation_options=creation_options,
    )
    issues = list(validation.issues)
    changes = ()
    name_pattern_samples = _build_name_pattern_samples(
        template_spec=template_spec,
        fabric_slug=fabric_slug,
        creation_options=creation_options,
    )
    if not any(issue.code == 'template_spec_invalid' for issue in issues):
        changes = tuple(
            _build_preview_changes(
                template_spec,
                template,
                fabric_name,
                fabric_slug,
                creation_options=creation_options,
            )
        )

    return StampOperationPreview(
        template_id=template_id,
        template_slug=template_slug,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        executor=executor,
        operation_key=operation_key,
        changes=changes,
        issues=tuple(issues),
        rollback=StampRollbackPlan(
            supported=False,
            strategy='future_managed_object_compensation',
            message=(
                'Rollback is not automated yet; StampRun.result.managed_objects is preserved '
                'for a future rollback.'
            ),
        ),
        retry=StampRetryPlan(
            supported=True,
            strategy='idempotent_reapply',
            operation_key=operation_key,
        ),
        name_pattern_samples=tuple(name_pattern_samples),
        architecture_gate=validation.architecture_gate,
    )


def apply_stamp_template_v25(
    *,
    template,
    fabric_name: str,
    fabric_slug: str,
    source_bindings: dict | None = None,
    creation_options: dict | None = None,
    actor=None,
) -> StampApplyResult:
    preview = preview_stamp_template_v25(
        template=template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
    )
    if not preview.is_valid:
        raise StampValidationError(preview)

    execution = execute_stamp_template(
        template=template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
        actor=actor,
    )
    return StampApplyResult(preview=preview, execution=execution)


def rollback_stamp_run_v25(*, stamp_run: StampRun, apply: bool = False, actor=None) -> StampRollbackPlan:
    plan = _build_rollback_plan(stamp_run=stamp_run)
    if not apply or not plan.supported:
        return plan
    return _apply_rollback_plan(stamp_run=stamp_run, plan=plan, actor=actor)


def classify_stamp_retry_v25(
    *,
    stamp_run: StampRun,
    template=None,
    source_bindings: dict | None = None,
    creation_options: dict | None = None,
) -> StampRetryPlan:
    parameters = stamp_run.parameters or {}
    operation_key = (
        f'{getattr(stamp_run.template, "pk", None) or "unsaved"}:'
        f'{parameters.get("template_slug") or getattr(stamp_run.template, "slug", "")}:'
        f'{parameters.get("fabric_slug") or getattr(stamp_run.fabric, "slug", "")}:'
        f'{parameters.get("executor") or "unknown"}'
    )
    rollback = (stamp_run.result or {}).get('rollback') or (stamp_run.metadata or {}).get('rollback') or {}
    if isinstance(rollback, dict) and rollback.get('state') == 'completed':
        return StampRetryPlan(
            supported=False,
            strategy='manual_recreate_after_rollback',
            operation_key=operation_key,
            classification='blocked',
            message='This run has already been rolled back; create a fresh apply operation instead of retrying it.',
        )
    if stamp_run.status == 'completed':
        return StampRetryPlan(
            supported=False,
            strategy='none',
            operation_key=operation_key,
            classification='already-converged',
            message='The stamp run completed; reapplying the same template would be an idempotent converge, not a retry.',
        )

    retry_template = template or stamp_run.template
    fabric_name = parameters.get('fabric_name') or getattr(stamp_run.fabric, 'name', '')
    fabric_slug = parameters.get('fabric_slug') or getattr(stamp_run.fabric, 'slug', '')
    if retry_template is None or not fabric_name or not fabric_slug:
        return StampRetryPlan(
            supported=False,
            strategy='missing_retry_parameters',
            operation_key=operation_key,
            classification='blocked',
            message='Retry requires a template plus fabric_name and fabric_slug parameters.',
        )
    preview = preview_stamp_template_v25(
        template=retry_template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
    )
    if not preview.is_valid:
        return StampRetryPlan(
            supported=False,
            strategy='preview_blocked',
            operation_key=preview.operation_key,
            classification='blocked',
            message='Retry is blocked by the current V2.5 preview gates.',
        )
    return StampRetryPlan(
        supported=True,
        strategy='idempotent_reapply',
        operation_key=preview.operation_key,
        classification='retryable',
        message='The failed or incomplete run can be retried through the normal V2.5 apply path.',
    )


_ROLLBACK_MODEL_KEYS = (
    ('optical_lanes', OpticalLane),
    ('transfer_maps', TransferMap),
    ('strand_terminations', StrandTermination),
    ('fiber_strands', FiberStrand),
    ('cable_assemblies', CableAssembly),
    ('fiber_segments', FiberSegment),
    ('transport_channel_position_maps', TransportChannelPositionMap),
    ('transport_channels', TransportChannel),
    ('connector_positions', ConnectorPosition),
    ('endpoints', Endpoint),
    ('nodes', FabricNode),
    ('planes', Plane),
    ('fabrics', Fabric),
)


def _build_rollback_plan(*, stamp_run: StampRun) -> StampRollbackPlan:
    existing_rollback = (stamp_run.result or {}).get('rollback') or (stamp_run.metadata or {}).get('rollback') or {}
    if isinstance(existing_rollback, dict) and existing_rollback.get('state') == 'completed':
        return StampRollbackPlan(
            supported=False,
            strategy='managed_object_compensation',
            message='Rollback has already been applied for this stamp run.',
            stamp_run_id=stamp_run.pk,
            applied=True,
            deleted_counts=existing_rollback.get('deleted_counts') or {},
        )

    manifest, issues = _rollback_manifest_from_stamp_run(stamp_run)
    issues = list(issues)
    if not manifest:
        issues.append(
            StampingValidationIssue(
                code='rollback_manifest_empty',
                path='stamp_run.result.managed_objects',
                message='StampRun.result.managed_objects does not contain rollback candidates.',
            )
        )
    if stamp_run.status != 'completed':
        issues.append(
            StampingValidationIssue(
                code='rollback_run_not_completed',
                path='stamp_run.status',
                message='Only completed stamp runs can be rolled back by compensation.',
                context={'status': stamp_run.status},
            )
        )
    if stamp_run.fabric_id and StampRun.objects.filter(fabric_id=stamp_run.fabric_id).exclude(pk=stamp_run.pk).exists():
        issues.append(
            StampingValidationIssue(
                code='rollback_shared_fabric_stamp_runs',
                path='stamp_run.fabric',
                message='Rollback is blocked because this fabric has other stamp runs; ownership is ambiguous.',
                context={'fabric_id': stamp_run.fabric_id},
            )
        )
    issues.extend(_rollback_dependency_issues(manifest))

    supported = not any(issue.severity == 'error' for issue in issues)
    return StampRollbackPlan(
        supported=supported,
        strategy='managed_object_compensation',
        message=(
            f'Rollback can delete {len(manifest)} plugin-owned manifest objects.'
            if supported
            else 'Rollback is blocked until manifest ownership and dependency issues are resolved.'
        ),
        stamp_run_id=stamp_run.pk,
        manifest=tuple(manifest),
        issues=tuple(issues),
    )


def _rollback_manifest_from_stamp_run(
    stamp_run: StampRun,
) -> tuple[list[StampRollbackManifestItem], tuple[StampingValidationIssue, ...]]:
    raw_managed = (stamp_run.result or {}).get('managed_objects')
    if not isinstance(raw_managed, dict):
        return [], ()

    manifest: list[StampRollbackManifestItem] = []
    issues: list[StampingValidationIssue] = []
    for key, model in _ROLLBACK_MODEL_KEYS:
        raw_ids = raw_managed.get(key) or ()
        if not isinstance(raw_ids, (list, tuple)):
            continue
        ids = _integer_ids(raw_ids)
        objects = {obj.pk: obj for obj in model.objects.filter(pk__in=ids)}
        for pk in ids:
            obj = objects.get(pk)
            if obj is None:
                continue
            ownership_marker = _ownership_marker(obj=obj, stamp_run=stamp_run)
            if not ownership_marker:
                issues.append(
                    StampingValidationIssue(
                        code='rollback_ownership_ambiguous',
                        path=f'stamp_run.result.managed_objects.{key}.{pk}',
                        message=f'{obj._meta.label} #{pk} is not clearly owned by this stamp.',
                        context={'model_label': obj._meta.label_lower, 'primary_key': pk},
                    )
                )
                continue
            manifest.append(
                StampRollbackManifestItem(
                    model_label=obj._meta.label_lower,
                    primary_key=int(pk),
                    natural_key=_natural_key(obj),
                    action='delete',
                    ownership_marker=ownership_marker,
                )
            )
    return manifest, tuple(issues)


def _integer_ids(raw_ids: Sequence[Any]) -> list[int]:
    ids: list[int] = []
    for raw_id in raw_ids:
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    return ids


def _ownership_marker(*, obj, stamp_run: StampRun) -> str:
    metadata = getattr(obj, 'metadata', None) or {}
    if isinstance(obj, Fabric):
        expected_template_slug = (stamp_run.parameters or {}).get('template_slug') or getattr(stamp_run.template, 'slug', None)
        expected_executor = (stamp_run.parameters or {}).get('executor')
        if (
            metadata.get('fixture') is True
            and metadata.get('template_slug') == expected_template_slug
            and (not expected_executor or metadata.get('stamp_executor') == expected_executor)
        ):
            return 'fabric.metadata.fixture_template_executor'
        return ''
    if isinstance(obj, CableAssembly):
        if metadata.get('fixture') is True and metadata.get('fabric_id') == stamp_run.fabric_id:
            return 'cable_assembly.metadata.fixture_fabric_id'
        return ''
    if isinstance(obj, ConnectorPosition):
        endpoint_metadata = getattr(obj.endpoint, 'metadata', None) or {}
        if endpoint_metadata.get('fixture') is True:
            return 'connector_position.parent_endpoint.metadata.fixture'
        return ''
    if metadata.get('fixture') is True:
        return f'{obj._meta.model_name}.metadata.fixture'
    return ''


def _natural_key(obj) -> str:
    if isinstance(obj, Fabric):
        return obj.slug
    if isinstance(obj, Plane):
        return f'{obj.fabric.slug}:plane-{obj.plane_number}'
    if isinstance(obj, FabricNode):
        return f'{obj.fabric.slug}:{obj.address}'
    if isinstance(obj, Endpoint):
        return f'{obj.fabric.slug}:{obj.address}'
    if isinstance(obj, ConnectorPosition):
        return f'{obj.endpoint.fabric.slug}:{obj.endpoint.address}:position-{obj.position_number}'
    if isinstance(obj, TransportChannel):
        return f'{obj.fabric.slug}:{obj.endpoint.address}:channel-{obj.channel_index}'
    if isinstance(obj, TransportChannelPositionMap):
        return f'{obj.channel.fabric.slug}:{obj.channel.endpoint.address}:channel-{obj.channel.channel_index}:position-{obj.mpo_position.position_number}'
    if isinstance(obj, CableAssembly):
        return f'{obj.site_id}:{obj.cable_id}'
    if isinstance(obj, FiberSegment):
        return f'{obj.fabric.slug}:{obj.name}'
    if isinstance(obj, FiberStrand):
        return f'{obj.segment.fabric.slug}:{obj.segment.name}:strand-{obj.strand_index}'
    if isinstance(obj, StrandTermination):
        return f'{obj.strand.segment.fabric.slug}:{obj.strand.segment.name}:termination-{obj.termination_index or obj.pk}'
    if isinstance(obj, TransferMap):
        return f'{obj.fabric.slug}:transfer-map-{obj.pk}'
    if isinstance(obj, OpticalLane):
        return f'{obj.fabric.slug}:{obj.endpoint.address}:lane-{obj.lane_index}:{obj.direction}'
    return str(obj.pk)


def _rollback_dependency_issues(
    manifest: Sequence[StampRollbackManifestItem],
) -> tuple[StampingValidationIssue, ...]:
    objects_by_model = _objects_for_manifest_by_model(manifest)
    if not objects_by_model:
        return ()
    selected = {(item.model_label, item.primary_key) for item in manifest}
    collector = Collector(using=DEFAULT_DB_ALIAS)
    for objects in objects_by_model.values():
        try:
            collector.collect(objects)
        except ProtectedError as exc:
            return (
                StampingValidationIssue(
                    code='rollback_protected_dependency',
                    path='stamp_run.result.managed_objects',
                    message='Rollback is blocked by protected downstream dependencies.',
                    context={'protected_count': len(exc.protected_objects)},
                ),
            )

    issues: list[StampingValidationIssue] = []
    for model, collected in collector.data.items():
        model_label = model._meta.label_lower
        for obj in collected:
            identity = (model_label, obj.pk)
            if identity not in selected and not _allowed_rollback_cascade(obj):
                issues.append(
                    StampingValidationIssue(
                        code='rollback_downstream_dependency',
                        path=f'{model_label}.{obj.pk}',
                        message=f'{model._meta.label} #{obj.pk} would be deleted but is not in the stamp manifest.',
                        context={'model_label': model_label, 'primary_key': obj.pk},
                    )
                )
    return tuple(issues)


def _allowed_rollback_cascade(obj) -> bool:
    if isinstance(obj, AuditEvent):
        return True
    return False


def _objects_for_manifest_by_model(manifest: Sequence[StampRollbackManifestItem]) -> dict[Any, list[Any]]:
    model_map = {model._meta.label_lower: model for _, model in _ROLLBACK_MODEL_KEYS}
    ids_by_model: dict[Any, list[int]] = {}
    for item in manifest:
        model = model_map.get(item.model_label)
        if model is None:
            continue
        ids_by_model.setdefault(model, []).append(item.primary_key)
    return {
        model: list(model.objects.filter(pk__in=ids))
        for model, ids in ids_by_model.items()
    }


@transaction.atomic
def _apply_rollback_plan(*, stamp_run: StampRun, plan: StampRollbackPlan, actor=None) -> StampRollbackPlan:
    deleted_counts: dict[str, int] = {}
    for key, model in _ROLLBACK_MODEL_KEYS:
        ids = [item.primary_key for item in plan.manifest if item.model_label == model._meta.label_lower]
        if not ids:
            continue
        queryset = model.objects.filter(pk__in=ids)
        existing_count = queryset.count()
        if existing_count:
            queryset.delete()
        deleted_counts[key] = existing_count

    rollback_record = {
        'state': 'completed',
        'strategy': plan.strategy,
        'applied_at': timezone.now().isoformat(),
        'applied_by_id': getattr(actor, 'pk', None) if getattr(actor, 'is_authenticated', False) else None,
        'deleted_counts': deleted_counts,
        'deleted_total': sum(deleted_counts.values()),
        'manifest': [_manifest_item_payload(item) for item in plan.manifest],
    }
    stamp_run.refresh_from_db()
    stamp_run.metadata = {
        **(stamp_run.metadata or {}),
        'rollback': rollback_record,
    }
    stamp_run.result = {
        **(stamp_run.result or {}),
        'rollback': rollback_record,
    }
    stamp_run.save(update_fields=['metadata', 'result'])

    return StampRollbackPlan(
        supported=True,
        strategy=plan.strategy,
        message=f'Rollback deleted {rollback_record["deleted_total"]} plugin-owned manifest objects.',
        stamp_run_id=stamp_run.pk,
        manifest=plan.manifest,
        applied=True,
        deleted_counts=deleted_counts,
    )


def _manifest_item_payload(item: StampRollbackManifestItem) -> dict[str, Any]:
    return {
        'model_label': item.model_label,
        'primary_key': item.primary_key,
        'natural_key': item.natural_key,
        'action': item.action,
        'ownership_marker': item.ownership_marker,
    }


def _validate_stamp_operation(
    *,
    template,
    template_spec: dict,
    fabric_slug: str,
    executor: str | None,
    source_bindings: dict,
    creation_options: dict,
) -> _StampOperationValidation:
    issues: list[StampingValidationIssue] = []

    try:
        validate_stamp_template_spec(template_spec)
    except ValueError as exc:
        issues.append(
            StampingValidationIssue(
                code='template_spec_invalid',
                path='template.template',
                message=str(exc),
            )
        )
        return _StampOperationValidation(issues=tuple(issues))

    if executor is None:
        issues.append(
            StampingValidationIssue(
                code='executor_missing',
                path='template.template.executor.primitive',
                message='StampTemplate.template.executor.primitive is required.',
            )
        )
    elif executor not in HYBRID_STAMP_EXECUTORS:
        issues.append(
            StampingValidationIssue(
                code='executor_unknown',
                path='template.template.executor.primitive',
                message=f'Unknown hybrid stamp executor: {executor!r}.',
            )
        )

    architecture_gate = _validate_architecture_contract(template=template, template_spec=template_spec, issues=issues)
    _validate_fabric_collision(template=template, template_spec=template_spec, fabric_slug=fabric_slug, issues=issues)
    _validate_netbox_creation_options(
        template_spec=template_spec,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
        issues=issues,
    )
    return _StampOperationValidation(issues=tuple(issues), architecture_gate=architecture_gate)


def _validate_architecture_contract(
    *,
    template,
    template_spec: dict,
    issues: list[StampingValidationIssue],
) -> StampArchitectureGate:
    schema = build_roce_4plane_shuffle_architecture_schema()
    schema_result = validate_roce_4plane_shuffle_architecture_fixture()
    for error in schema_result.errors:
        issues.append(
            StampingValidationIssue(
                code=f'architecture_schema.{error.code}',
                path=error.path,
                message=error.message,
                context=error.context,
            )
        )

    template_architecture_slug = template_spec.get('architecture_slug')
    if not isinstance(template_architecture_slug, str):
        template_architecture_slug = None
    template_architecture_version = template_spec.get('architecture_version')
    if not isinstance(template_architecture_version, str):
        template_architecture_version = None

    if template_spec.get('architecture_slug') != ARCHITECTURE_SLUG:
        issues.append(
            StampingValidationIssue(
                code='unsupported_architecture',
                path='template.template.architecture_slug',
                message=f'Only {ARCHITECTURE_SLUG!r} is supported by the V2.5 stamp runner.',
            )
        )
    if template_spec.get('architecture_version') != ARCHITECTURE_VERSION:
        issues.append(
            StampingValidationIssue(
                code='unsupported_architecture_version',
                path='template.template.architecture_version',
                message=f'Only architecture version {ARCHITECTURE_VERSION!r} is supported by the V2.5 stamp runner.',
            )
        )

    architecture, target_source = _infer_persisted_architecture_target(template=template, template_spec=template_spec)
    persisted_schema_valid = None
    persisted_schema_error_count = 0
    compatibility_status = 'not_checked'
    compatibility_issue_count = 0
    if architecture is not None:
        persisted_result = validate_persisted_architecture_schema(
            architecture,
            shuffle_pair_provider=shuffle_2x2_transfer_position_pairs,
        )
        persisted_schema_valid = persisted_result.is_valid
        persisted_schema_error_count = len(persisted_result.errors)
        for error in persisted_result.errors:
            issues.append(
                StampingValidationIssue(
                    code=f'architecture_schema.{error.code}',
                    path=_persisted_architecture_issue_path(error.path),
                    message=error.message,
                    context=error.context,
                )
            )
        if persisted_result.is_valid:
            compatibility_result = compare_persisted_architecture_compatibility(
                architecture,
                schema,
                shuffle_pair_provider=shuffle_2x2_transfer_position_pairs,
            )
            compatibility_status = compatibility_result.status
            compatibility_issue_count = len(compatibility_result.issues)
            for issue in compatibility_result.issues:
                issues.append(
                    StampingValidationIssue(
                        code=f'architecture_compatibility.{issue.code}',
                        path=issue.path,
                        message=issue.message,
                        severity=issue.severity,
                        context=issue.context,
                    )
                )

        existing_roles = set(architecture.roles.values_list('slug', flat=True))
        for role_slug in sorted(definition['slug'] for definition in schema.roles):
            if role_slug not in existing_roles:
                issues.append(
                    StampingValidationIssue(
                        code='missing_architecture_role',
                        path=f'architecture.roles.{role_slug}',
                        message=f'Architecture is missing required role {role_slug!r}.',
                    )
                )

    template_matrix = _channel_map_matrix(template_spec)
    if _normalize_matrix(template_matrix) != _normalize_matrix(schema.channel_map_matrix):
        issues.append(
            StampingValidationIssue(
                code='channel_map_mismatch',
                path='template.template.channel_subinterfaces.channel_map_matrix',
                message='Template channel map matrix must match the built-in architecture channel map matrix.',
            )
        )

    matrix_schema = replace(
        schema,
        channel_map_matrix=tuple(template_matrix),
        allocation_rule_sets=_allocation_rules_with_channel_map(schema.allocation_rule_sets, template_matrix),
    )
    matrix_result = validate_architecture_schema(matrix_schema)
    for error in matrix_result.errors:
        if error.code.startswith('channel_map.') or error.code.startswith('mpo_positions.'):
            issues.append(
                StampingValidationIssue(
                    code=f'architecture_schema.{error.code}',
                    path=f'template.template.channel_subinterfaces.{error.path}',
                    message=error.message,
                    context=error.context,
                )
            )

    return StampArchitectureGate(
        fixture_valid=schema_result.is_valid,
        fixture_error_count=len(schema_result.errors),
        template_architecture_slug=template_architecture_slug,
        template_architecture_version=template_architecture_version,
        target_source=target_source,
        target_architecture_id=getattr(architecture, 'pk', None),
        target_architecture_slug=getattr(architecture, 'slug', None) if architecture is not None else None,
        target_architecture_version=getattr(architecture, 'version', None) if architecture is not None else None,
        persisted_schema_valid=persisted_schema_valid,
        persisted_schema_error_count=persisted_schema_error_count,
        compatibility_status=compatibility_status,
        compatibility_issue_count=compatibility_issue_count,
    )


def _infer_persisted_architecture_target(*, template, template_spec: dict) -> tuple[FabricArchitecture | None, str]:
    architecture_id = getattr(template, 'architecture_id', None)
    if architecture_id:
        architecture = FabricArchitecture.objects.filter(pk=architecture_id).first()
        if architecture is not None:
            return architecture, 'template_fk'
        return None, 'template_fk_missing'

    try:
        architecture = getattr(template, 'architecture', None)
    except Exception:
        architecture = None
    if architecture is not None:
        if getattr(architecture, 'pk', None):
            return architecture, 'template_object'
        return None, 'template_object_unpersisted'

    architecture_slug = template_spec.get('architecture_slug')
    architecture_version = template_spec.get('architecture_version')
    if isinstance(architecture_slug, str) and architecture_slug.strip() and isinstance(architecture_version, str):
        architecture = FabricArchitecture.objects.filter(
            slug=architecture_slug.strip(),
            version=architecture_version.strip(),
        ).first()
        if architecture is not None:
            return architecture, 'template_spec'
        return None, 'template_spec_not_found'

    return None, 'none'


def _persisted_architecture_issue_path(path: str) -> str:
    if path.startswith('architecture.'):
        return path
    return f'architecture.{path}'


def _validate_fabric_collision(
    *,
    template,
    template_spec: dict,
    fabric_slug: str,
    issues: list[StampingValidationIssue],
) -> None:
    existing = Fabric.objects.filter(slug=fabric_slug).first()
    if existing is None:
        return
    executor = _executor_name(template_spec)
    expected_template_slug = getattr(template, 'slug', None)
    metadata = existing.metadata or {}
    if metadata.get('template_slug') == expected_template_slug and metadata.get('stamp_executor') == executor:
        return
    issues.append(
        StampingValidationIssue(
            code='name_collision_risk',
            path='fabric_slug',
            message=f'Fabric slug {fabric_slug!r} already belongs to a fabric not managed by this stamp operation.',
            context={'fabric_id': existing.pk},
        )
    )


def _validate_netbox_creation_options(
    *,
    template_spec: dict,
    fabric_slug: str,
    source_bindings: dict,
    creation_options: dict,
    issues: list[StampingValidationIssue],
) -> None:
    _validate_source_binding_types(source_bindings, issues)
    if not creation_options.get('enabled'):
        return

    site = creation_options.get('site')
    gpu_device_type = creation_options.get('gpu_device_type')
    gpu_role = creation_options.get('gpu_role')
    leaf_device_type = creation_options.get('leaf_device_type') or gpu_device_type
    leaf_role = creation_options.get('leaf_role') or gpu_role
    name_prefix = creation_options.get('name_prefix') or fabric_slug

    if not isinstance(site, Site):
        issues.append(
            StampingValidationIssue(
                code='missing_site_selection',
                path='creation_options.site',
                message='creation_options.site must be a Site when creation_options.enabled is true.',
            )
        )
    if not isinstance(gpu_device_type, DeviceType):
        issues.append(
            StampingValidationIssue(
                code='missing_device_type_selection',
                path='creation_options.gpu_device_type',
                message='creation_options.gpu_device_type must be a DeviceType when enabled.',
            )
        )
    if not isinstance(gpu_role, DeviceRole):
        issues.append(
            StampingValidationIssue(
                code='missing_device_role_selection',
                path='creation_options.gpu_role',
                message='creation_options.gpu_role must be a DeviceRole when enabled.',
            )
        )
    if (
        not isinstance(leaf_device_type, DeviceType)
        or not isinstance(leaf_role, DeviceRole)
        or not isinstance(site, Site)
    ):
        return

    for spec in _expected_created_device_specs(
        template_spec=template_spec,
        name_prefix=name_prefix,
        site=site,
        gpu_device_type=gpu_device_type,
        gpu_role=gpu_role,
        leaf_device_type=leaf_device_type,
        leaf_role=leaf_role,
    ):
        existing = Device.objects.filter(name=spec['name']).first()
        if existing is None:
            continue
        if (
            existing.site_id == spec['site'].pk
            and existing.device_type_id == spec['device_type'].pk
            and existing.role_id == spec['role'].pk
        ):
            continue
        issues.append(
            StampingValidationIssue(
                code='name_collision_risk',
                path=f'creation_options.name_prefix.{spec["name"]}',
                message=f'Device name {spec["name"]!r} already exists with a different site/type/role.',
                context={'device_id': existing.pk},
            )
        )


def _validate_source_binding_types(source_bindings: dict, issues: list[StampingValidationIssue]) -> None:
    for address, source in (source_bindings.get('nodes') or {}).items():
        if not isinstance(source, Device):
            issues.append(
                StampingValidationIssue(
                    code='invalid_source_binding',
                    path=f'source_bindings.nodes.{address}',
                    message='Node source bindings must be dcim.Device objects.',
                )
            )
    for address, source in (source_bindings.get('endpoints') or {}).items():
        if not isinstance(source, Interface):
            issues.append(
                StampingValidationIssue(
                    code='invalid_source_binding',
                    path=f'source_bindings.endpoints.{address}',
                    message='Endpoint source bindings must be dcim.Interface objects.',
                )
            )


def _build_preview_changes(
    template_spec: dict,
    template,
    fabric_name: str,
    fabric_slug: str,
    *,
    creation_options: dict | None = None,
) -> list[StampPreviewChange]:
    changes: list[StampPreviewChange] = []
    creation_options = creation_options or {}
    architecture_id = getattr(template, 'architecture_id', None)
    fabric = Fabric.objects.filter(slug=fabric_slug).first()
    changes.append(
        StampPreviewChange(
            action=_action(fabric),
            object_type='Fabric',
            identity=fabric_slug,
            summary=f'Fabric {fabric_slug}',
            before=_object_before(fabric, 'name', 'status', 'architecture_id'),
            after={
                'name': fabric_name,
                'status': 'planned',
                'architecture_id': architecture_id,
            },
        )
    )

    changes.extend(
        _expected_netbox_source_changes(
            template_spec=template_spec,
            fabric_slug=fabric_slug,
            creation_options=creation_options,
        )
    )

    for plane_number in template_spec['planes']:
        plane = Plane.objects.filter(fabric=fabric, plane_number=plane_number).first() if fabric else None
        changes.append(
            StampPreviewChange(
                action=_action(plane),
                object_type='Plane',
                identity=f'{fabric_slug}:plane-{plane_number}',
                summary=f'Plane {plane_number}',
                before=_object_before(plane, 'label'),
                after={'label': f'Plane {plane_number}'},
            )
        )

    for node in _expected_node_specs(template_spec):
        existing = FabricNode.objects.filter(fabric=fabric, address=node['address']).first() if fabric else None
        changes.append(
            StampPreviewChange(
                action=_action(existing),
                object_type='FabricNode',
                identity=f'{fabric_slug}:{node["address"]}',
                summary=f'Fabric node {node["address"]}',
                before=_object_before(existing, 'name', 'node_kind', 'local_index'),
                after=node,
            )
        )

    endpoint_specs = _expected_endpoint_specs(template_spec)
    for endpoint in endpoint_specs:
        existing = Endpoint.objects.filter(fabric=fabric, address=endpoint['address']).first() if fabric else None
        changes.append(
            StampPreviewChange(
                action=_action(existing),
                object_type='Endpoint',
                identity=f'{fabric_slug}:{endpoint["address"]}',
                summary=f'Endpoint {endpoint["address"]}',
                before=_object_before(existing, 'name', 'endpoint_kind', 'connector_kind', 'position_count'),
                after=endpoint,
            )
        )
        if endpoint.get('position_count'):
            changes.append(
                StampPreviewChange(
                    action=_action(existing),
                    object_type='ConnectorPositionSet',
                    identity=f'{fabric_slug}:{endpoint["address"]}:positions',
                    summary=f'{endpoint["position_count"]} connector positions for {endpoint["address"]}',
                    before={
                        'count': (
                            ConnectorPosition.objects.filter(endpoint=existing).count() if existing is not None else 0
                        )
                    },
                    after={'count': endpoint['position_count']},
                )
            )

    for channel in _expected_transport_channels(template_spec, fabric_slug=fabric_slug):
        existing = None
        if fabric is not None:
            existing = TransportChannel.objects.filter(
                endpoint__fabric=fabric,
                endpoint__address=channel['endpoint_address'],
                channel_index=channel['channel_index'],
            ).first()
        changes.append(
            StampPreviewChange(
                action=_action(existing),
                object_type='TransportChannel',
                identity=f'{fabric_slug}:{channel["endpoint_address"]}:channel-{channel["channel_index"]}',
                summary=f'Transport channel {channel["channel_index"]} on {channel["endpoint_address"]}',
                before=_object_before(existing, 'name', 'channel_index'),
                after=channel,
            )
        )

    for segment_name in _expected_segment_names(template_spec):
        segment = FiberSegment.objects.filter(fabric=fabric, name=segment_name).first() if fabric else None
        changes.extend(
            [
                StampPreviewChange(
                    action=_action(segment),
                    object_type='FiberSegment',
                    identity=f'{fabric_slug}:{segment_name}',
                    summary=f'Fiber segment {segment_name}',
                ),
                StampPreviewChange(
                    action=_action(_existing_cable_assembly(fabric, segment_name)),
                    object_type='CableAssembly',
                    identity=f'{fabric_slug}:{segment_name}',
                    summary=f'Cable assembly for {segment_name}',
                ),
            ]
        )

    changes.extend(
        _aggregate_existing_child_changes(fabric=fabric, fabric_slug=fabric_slug, template_spec=template_spec)
    )
    changes.extend(
        [
            StampPreviewChange(
                action='create',
                object_type='StampRun',
                identity=f'{fabric_slug}:stamp-run',
                summary='A stamp run audit record will be created for this apply.',
                after={
                    'status': 'completed',
                    'operation_key': f'{getattr(template, "pk", None) or "unsaved"}:{fabric_slug}',
                },
            ),
            StampPreviewChange(
                action='create',
                object_type='AuditEvent',
                identity=f'{fabric_slug}:stamp-audit-event',
                summary='A stamp audit event will be recorded for this apply.',
                after={'event_type': 'stamp', 'outcome': 'ok'},
            ),
        ]
    )
    return changes


def _expected_netbox_source_changes(
    *,
    template_spec: dict,
    fabric_slug: str,
    creation_options: dict,
) -> list[StampPreviewChange]:
    if not _creation_options_are_previewable(creation_options):
        return []

    site = creation_options['site']
    gpu_device_type = creation_options['gpu_device_type']
    gpu_role = creation_options['gpu_role']
    leaf_device_type = creation_options.get('leaf_device_type') or gpu_device_type
    leaf_role = creation_options.get('leaf_role') or gpu_role
    name_prefix = creation_options.get('name_prefix') or fabric_slug

    changes: list[StampPreviewChange] = []
    device_specs = _expected_created_device_specs(
        template_spec=template_spec,
        name_prefix=name_prefix,
        site=site,
        gpu_device_type=gpu_device_type,
        gpu_role=gpu_role,
        leaf_device_type=leaf_device_type,
        leaf_role=leaf_role,
    )
    for spec in device_specs:
        existing = Device.objects.filter(name=spec['name']).first()
        changes.append(
            StampPreviewChange(
                action=_action(existing),
                object_type='Device',
                identity=spec['name'],
                summary=f'NetBox device {spec["name"]}',
                before=_device_before(existing),
                after=_device_after(spec),
            )
        )

    for spec in _expected_created_interface_specs(template_spec=template_spec, device_specs=device_specs):
        existing = Interface.objects.filter(device__name=spec['device_name'], name=spec['name']).first()
        changes.append(
            StampPreviewChange(
                action=_action(existing),
                object_type='Interface',
                identity=f'{spec["device_name"]}:{spec["name"]}',
                summary=f'NetBox interface {spec["device_name"]}:{spec["name"]}',
                before=_interface_before(existing),
                after={
                    'device_name': spec['device_name'],
                    'name': spec['name'],
                    'type': spec['type'],
                },
            )
        )

    for spec in _expected_channel_subinterface_specs(template_spec=template_spec, device_specs=device_specs):
        existing = Interface.objects.filter(device__name=spec['device_name'], name=spec['name']).first()
        changes.append(
            StampPreviewChange(
                action=_action(existing),
                object_type='ChannelSubinterface',
                identity=f'{spec["device_name"]}:{spec["name"]}',
                summary=f'NetBox 200G channel sub-interface {spec["device_name"]}:{spec["name"]}',
                before=_interface_before(existing),
                after={
                    'device_name': spec['device_name'],
                    'parent_name': spec['parent_name'],
                    'name': spec['name'],
                    'type': spec['type'],
                    'speed': spec['speed_gbps'] * 1000000,
                },
            )
        )
    return changes


def _aggregate_existing_child_changes(
    *,
    fabric: Fabric | None,
    fabric_slug: str,
    template_spec: dict,
) -> list[StampPreviewChange]:
    expected_segments = len(_expected_segment_names(template_spec))
    expected_channels = len(_expected_transport_channels(template_spec, fabric_slug=fabric_slug))
    channel_width = max((len(entry.get('positions') or ()) for entry in _channel_map_matrix(template_spec)), default=0)
    expected_paths = len(template_spec.get('proof_paths') or ())
    return [
        StampPreviewChange(
            action='update' if fabric else 'create',
            object_type='TransportChannelPositionMap',
            identity=f'{fabric_slug}:channel-position-maps',
            summary='Transport channel to MPO position mappings are reconciled.',
            before={'count': _count_for_fabric(TransportChannelPositionMap, fabric, 'channel__fabric')},
            after={'count': expected_channels * channel_width},
        ),
        StampPreviewChange(
            action='update' if fabric else 'create',
            object_type='FiberStrand',
            identity=f'{fabric_slug}:fiber-strands',
            summary='One fiber strand per stamped fiber segment is reconciled.',
            before={'count': _count_for_fabric(FiberStrand, fabric, 'segment__fabric')},
            after={'count': expected_segments},
        ),
        StampPreviewChange(
            action='update' if fabric else 'create',
            object_type='StrandTermination',
            identity=f'{fabric_slug}:strand-terminations',
            summary='Two strand terminations per stamped fiber strand are reconciled.',
            before={'count': _count_for_fabric(StrandTermination, fabric, 'strand__segment__fabric')},
            after={'count': expected_segments * 2},
        ),
        StampPreviewChange(
            action='update' if fabric else 'create',
            object_type='TransferMap',
            identity=f'{fabric_slug}:shuffle-transfer-maps',
            summary='2x2 shuffle transfer maps are reconciled.',
            before={'count': _count_for_fabric(TransferMap, fabric, 'fabric')},
            after={'count': expected_paths},
        ),
        StampPreviewChange(
            action='update' if fabric else 'create',
            object_type='OpticalLane',
            identity=f'{fabric_slug}:optical-lanes',
            summary='Source and destination optical lanes are reconciled.',
            before={'count': _count_for_fabric(OpticalLane, fabric, 'fabric')},
            after={'count': expected_paths * 2},
        ),
    ]


def _expected_node_specs(template_spec: dict) -> list[dict]:
    nodes = [
        {'address': 'GB300-TRAY-1', 'name': 'GB300-TRAY-1', 'node_kind': 'active_device', 'local_index': 1},
    ]
    for cassette in range(1, template_spec['shuffle_cassettes']['count'] + 1):
        nodes.append(
            {
                'address': f'SHUFFLE-CASSETTE-{cassette}',
                'name': f'SHUFFLE-CASSETTE-{cassette}',
                'node_kind': 'passive_assembly',
                'local_index': cassette,
            }
        )
    for leaf in range(1, template_spec['leaf_ports']['count'] + 1):
        nodes.append(
            {'address': f'LEAF-{leaf}', 'name': f'LEAF-{leaf}', 'node_kind': 'active_device', 'local_index': leaf}
        )
    return nodes


def _expected_endpoint_specs(template_spec: dict) -> list[dict]:
    endpoints: list[dict] = []
    gpu_spec = template_spec['gpu_tray']
    shuffle_spec = template_spec['shuffle_cassettes']

    for osfp in range(1, gpu_spec['osfp_count'] + 1):
        osfp_address = f'GB300-TRAY-1.OSFP-{osfp}'
        endpoints.append(_endpoint_spec(osfp_address, f'OSFP-{osfp}', 'plugin_port', 'osfp'))
        for mpo in range(1, gpu_spec['mpo_per_osfp'] + 1):
            endpoints.append(
                _endpoint_spec(
                    f'{osfp_address}.MPO-{mpo}',
                    f'OSFP-{osfp}.MPO-{mpo}',
                    'subconnector',
                    'mpo-12',
                    gpu_spec['positions_per_mpo'],
                )
            )

    for cassette in range(1, shuffle_spec['count'] + 1):
        base = f'SHUFFLE-CASSETTE-{cassette}'
        for mpo in range(1, shuffle_spec['front_mpo_count'] + 1):
            endpoints.append(
                _endpoint_spec(
                    f'{base}.FRONT.MPO-{mpo}',
                    f'FRONT.MPO-{mpo}',
                    'connector',
                    'mpo-12',
                    shuffle_spec['positions_per_mpo'],
                )
            )
        for mpo in range(1, shuffle_spec['rear_mpo_count'] + 1):
            endpoints.append(
                _endpoint_spec(
                    f'{base}.REAR.MPO-{mpo}',
                    f'REAR.MPO-{mpo}',
                    'connector',
                    'mpo-12',
                    shuffle_spec['positions_per_mpo'],
                )
            )

    for leaf in range(1, template_spec['leaf_ports']['count'] + 1):
        osfp_address = f'LEAF-{leaf}.OSFP-1'
        endpoints.append(_endpoint_spec(osfp_address, 'OSFP-1', 'plugin_port', 'osfp'))
        for mpo in range(1, gpu_spec['mpo_per_osfp'] + 1):
            endpoints.append(
                _endpoint_spec(
                    f'{osfp_address}.MPO-{mpo}',
                    f'OSFP-1.MPO-{mpo}',
                    'subconnector',
                    'mpo-12',
                    gpu_spec['positions_per_mpo'],
                )
            )
    return endpoints


def _endpoint_spec(
    address: str,
    name: str,
    endpoint_kind: str,
    connector_kind: str,
    position_count: int = 0,
) -> dict:
    return {
        'address': address,
        'name': name,
        'endpoint_kind': endpoint_kind,
        'connector_kind': connector_kind,
        'position_count': position_count,
    }


def _expected_transport_channels(template_spec: dict, *, fabric_slug: str) -> list[dict]:
    channels = []
    seen = set()
    for proof_path in template_spec['proof_paths']:
        gpu_endpoint = f'GB300-TRAY-1.OSFP-{proof_path["gpu_osfp"]}'
        leaf_endpoint = f'LEAF-{proof_path["leaf"]}.OSFP-1'
        for endpoint_address, position, label in (
            (gpu_endpoint, proof_path['front_position'], 'GPU'),
            (leaf_endpoint, proof_path['rear_position'], 'Leaf'),
        ):
            channel_index = _channel_index_for_position(template_spec, 1, position)
            key = (endpoint_address, channel_index)
            if key in seen:
                continue
            seen.add(key)
            channels.append(
                {
                    'endpoint_address': endpoint_address,
                    'channel_index': channel_index,
                    'name': f'{label} plane {proof_path["plane"]}',
                    'pair_key': f'{fabric_slug}:plane-{proof_path["plane"]}',
                }
            )
    return channels


def _expected_segment_names(template_spec: dict) -> list[str]:
    names = []
    for proof_path in template_spec['proof_paths']:
        plane = proof_path['plane']
        names.append(f'P{plane}-GPU-to-SHUFFLE')
        names.append(f'P{plane}-SHUFFLE-to-LEAF')
    return names


def _expected_created_device_specs(
    *,
    template_spec: dict,
    name_prefix: str,
    site: Site,
    gpu_device_type: DeviceType,
    gpu_role: DeviceRole,
    leaf_device_type: DeviceType,
    leaf_role: DeviceRole,
) -> list[dict]:
    specs = [
        {
            'address': 'GB300-TRAY-1',
            'device_kind': 'gpu',
            'name': _netbox_name(name_prefix, 'GB300-TRAY-1'),
            'site': site,
            'device_type': gpu_device_type,
            'role': gpu_role,
        }
    ]
    for leaf in range(1, template_spec['leaf_ports']['count'] + 1):
        address = f'LEAF-{leaf}'
        specs.append(
            {
                'address': address,
                'device_kind': 'leaf',
                'name': _netbox_name(name_prefix, address),
                'site': site,
                'device_type': leaf_device_type,
                'role': leaf_role,
            }
        )
    return specs


def _expected_created_interface_specs(*, template_spec: dict, device_specs: Sequence[Mapping[str, Any]]) -> list[dict]:
    specs: list[dict] = []
    gpu_osfp_count = int((template_spec.get('gpu_tray') or {}).get('osfp_count') or 0)
    for device in device_specs:
        device_kind = device.get('device_kind')
        device_name = device.get('name')
        address = device.get('address')
        if not device_name or not address:
            continue
        if device_kind == 'gpu':
            for osfp_index in range(1, gpu_osfp_count + 1):
                specs.append(
                    {
                        'device_name': device_name,
                        'device_address': address,
                        'endpoint_address': f'{address}.OSFP-{osfp_index}',
                        'name': f'OSFP-{osfp_index}',
                        'type': '800gbase-x-osfp',
                    }
                )
        elif device_kind == 'leaf':
            specs.append(
                {
                    'device_name': device_name,
                    'device_address': address,
                    'endpoint_address': f'{address}.OSFP-1',
                    'name': 'OSFP-1',
                    'type': '800gbase-x-osfp',
                }
            )
    return specs


def _expected_channel_subinterface_specs(
    *,
    template_spec: dict,
    device_specs: Sequence[Mapping[str, Any]],
) -> list[dict]:
    channel_spec = template_spec.get('channel_subinterfaces') or {}
    if not channel_spec.get('enabled'):
        return []

    indexes = sorted({
        int(entry['subinterface_index'])
        for entry in _channel_map_matrix(template_spec)
        if entry.get('subinterface_index') is not None
    })
    if not indexes:
        return []

    name_pattern = channel_spec.get('name_pattern') or '{parent_name}/{channel_index}'
    interface_type = channel_spec.get('type') or 'virtual'
    speed_gbps = int(channel_spec.get('speed_gbps') or 200)
    specs: list[dict] = []
    for interface_spec in _expected_created_interface_specs(template_spec=template_spec, device_specs=device_specs):
        for channel_index in indexes:
            specs.append(
                {
                    'device_name': interface_spec['device_name'],
                    'device_address': interface_spec['device_address'],
                    'parent_endpoint_address': interface_spec['endpoint_address'],
                    'parent_name': interface_spec['name'],
                    'channel_index': channel_index,
                    'name': name_pattern.format(
                        parent_name=interface_spec['name'],
                        channel_index=channel_index,
                    ),
                    'type': interface_type,
                    'speed_gbps': speed_gbps,
                }
            )
    return specs


def _build_name_pattern_samples(
    *,
    template_spec: dict,
    fabric_slug: str,
    creation_options: dict,
) -> list[StampNamePatternSample]:
    if not _creation_options_are_previewable(creation_options):
        return []
    required_sections = ('gpu_tray', 'leaf_ports', 'channel_subinterfaces')
    if any(not isinstance(template_spec.get(section), Mapping) for section in required_sections):
        return []

    site = creation_options['site']
    gpu_device_type = creation_options['gpu_device_type']
    gpu_role = creation_options['gpu_role']
    leaf_device_type = creation_options.get('leaf_device_type') or gpu_device_type
    leaf_role = creation_options.get('leaf_role') or gpu_role
    name_prefix = creation_options.get('name_prefix') or fabric_slug
    device_specs = _expected_created_device_specs(
        template_spec=template_spec,
        name_prefix=name_prefix,
        site=site,
        gpu_device_type=gpu_device_type,
        gpu_role=gpu_role,
        leaf_device_type=leaf_device_type,
        leaf_role=leaf_role,
    )

    samples: list[StampNamePatternSample] = []
    for spec in device_specs:
        existing = Device.objects.filter(name=spec['name']).first()
        collision = bool(
            existing
            and (
                existing.site_id != spec['site'].pk
                or existing.device_type_id != spec['device_type'].pk
                or existing.role_id != spec['role'].pk
            )
        )
        samples.append(
            StampNamePatternSample(
                object_type='Device',
                address=spec['address'],
                name=spec['name'],
                exists=existing is not None,
                collision=collision,
                existing_object_id=existing.pk if existing else None,
            )
        )

    for spec in _expected_created_interface_specs(template_spec=template_spec, device_specs=device_specs):
        existing = Interface.objects.filter(device__name=spec['device_name'], name=spec['name']).first()
        samples.append(
            StampNamePatternSample(
                object_type='Interface',
                address=spec['endpoint_address'],
                name=f'{spec["device_name"]}:{spec["name"]}',
                exists=existing is not None,
                collision=bool(existing and existing.type != spec['type']),
                existing_object_id=existing.pk if existing else None,
            )
        )

    for spec in _expected_channel_subinterface_specs(template_spec=template_spec, device_specs=device_specs):
        existing = Interface.objects.filter(device__name=spec['device_name'], name=spec['name']).first()
        samples.append(
            StampNamePatternSample(
                object_type='ChannelSubinterface',
                address=f'{spec["parent_endpoint_address"]}.channel-{spec["channel_index"]}',
                name=f'{spec["device_name"]}:{spec["name"]}',
                exists=existing is not None,
                collision=bool(existing and existing.type != spec['type']),
                existing_object_id=existing.pk if existing else None,
            )
        )
    return samples


def _creation_options_are_previewable(creation_options: Mapping[str, Any]) -> bool:
    if not creation_options.get('enabled'):
        return False
    if not isinstance(creation_options.get('site'), Site):
        return False
    if not isinstance(creation_options.get('gpu_device_type'), DeviceType):
        return False
    if not isinstance(creation_options.get('gpu_role'), DeviceRole):
        return False
    leaf_device_type = creation_options.get('leaf_device_type') or creation_options.get('gpu_device_type')
    leaf_role = creation_options.get('leaf_role') or creation_options.get('gpu_role')
    return isinstance(leaf_device_type, DeviceType) and isinstance(leaf_role, DeviceRole)


def _device_before(device: Device | None) -> dict:
    if device is None:
        return {}
    return {
        'name': device.name,
        'site_id': device.site_id,
        'device_type_id': device.device_type_id,
        'role_id': device.role_id,
    }


def _device_after(spec: Mapping[str, Any]) -> dict:
    return {
        'name': spec['name'],
        'site_id': spec['site'].pk,
        'device_type_id': spec['device_type'].pk,
        'role_id': spec['role'].pk,
    }


def _interface_before(interface: Interface | None) -> dict:
    if interface is None:
        return {}
    return {
        'device_id': interface.device_id,
        'name': interface.name,
        'type': interface.type,
        'parent_id': interface.parent_id,
        'speed': interface.speed,
    }


def _allocation_rules_with_channel_map(
    allocation_rule_sets: Sequence[Mapping[str, Any]],
    channel_map_matrix: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    normalized = []
    for rule_set in allocation_rule_sets:
        rule_set_copy = deepcopy(dict(rule_set))
        if rule_set_copy.get('slug') == 'channel_subinterface_mapping':
            rule = dict(rule_set_copy.get('rule') or {})
            rule['channel_map_matrix'] = [dict(entry) for entry in channel_map_matrix]
            rule_set_copy['rule'] = rule
        normalized.append(rule_set_copy)
    return tuple(normalized)


def _channel_map_matrix(template_spec: dict) -> list[dict]:
    channel_subinterfaces = template_spec.get('channel_subinterfaces') or {}
    matrix = channel_subinterfaces.get('channel_map_matrix') or []
    return [dict(entry) for entry in matrix if isinstance(entry, Mapping)]


def _channel_index_for_position(template_spec: dict, mpo_index: int, position_number: int) -> int:
    for entry in _channel_map_matrix(template_spec):
        if int(entry.get('mpo_index', 0)) != mpo_index:
            continue
        if position_number in {int(position) for position in entry.get('positions') or []}:
            return int(entry['subinterface_index'])
    return 0


def _executor_name(template_spec: dict) -> str | None:
    executor = template_spec.get('executor') or {}
    if not isinstance(executor, Mapping):
        return None
    primitive = executor.get('primitive')
    if not isinstance(primitive, str) or not primitive.strip():
        return None
    return primitive


def _normalize_matrix(matrix: Sequence[Mapping[str, Any]]) -> tuple[tuple[Any, Any, tuple[Any, ...]], ...]:
    return tuple(
        (
            entry.get('subinterface_index'),
            entry.get('mpo_index'),
            tuple(entry.get('positions') or ()),
        )
        for entry in matrix
    )


def _netbox_name(prefix: str, address: str) -> str:
    from django.utils.text import slugify

    return f'{prefix}-{slugify(address)}'


def _action(existing) -> str:
    return 'update' if existing is not None else 'create'


def _object_before(instance, *fields: str) -> dict:
    if instance is None:
        return {}
    return {field: getattr(instance, field) for field in fields}


def _existing_cable_assembly(fabric: Fabric | None, segment_name: str):
    if fabric is None:
        return None
    return CableAssembly.objects.filter(metadata__fabric_id=fabric.pk, cable_id=f'{fabric.slug}:{segment_name}').first()


def _count_for_fabric(model, fabric: Fabric | None, lookup: str) -> int:
    if fabric is None:
        return 0
    return model.objects.filter(**{lookup: fabric}).count()


__all__ = (
    'StampApplyResult',
    'StampArchitectureGate',
    'StampNamePatternSample',
    'StampOperationPreview',
    'StampPreviewChange',
    'StampRetryPlan',
    'StampRollbackPlan',
    'StampValidationError',
    'StampingValidationIssue',
    'apply_stamp_template_v25',
    'preview_stamp_template_v25',
    'rollback_stamp_run_v25',
)
