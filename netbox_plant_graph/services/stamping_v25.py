from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models.deletion import Collector, ProtectedError
from django.utils import timezone
from dcim.models import Device, DeviceRole, DeviceType, Interface, Location, Site
from tenancy.models import Tenant

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
from netbox_plant_graph.services.blueprint_registry import (
    BLUEPRINT_LIFECYCLE_ACTIVE,
    BlueprintRegistryEntry,
    architecture_definition_from_payload,
    check_blueprint_device_type_compatibility,
    get_default_blueprint_registry,
    validate_blueprint_parameters,
)
from netbox_plant_graph.services.architecture import shuffle_2x2_transfer_position_pairs
from netbox_plant_graph.services.architecture_schema import (
    compare_persisted_architecture_compatibility,
    validate_architecture_schema,
    validate_persisted_architecture_schema,
)
from netbox_plant_graph.services.stamp_template_validation import resolve_stamp_template_spec
from netbox_plant_graph.services.stamping import (
    HYBRID_STAMP_EXECUTORS,
    MiniFabricStampResult,
    execute_stamp_template,
    resolve_fabric_ownership,
)


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
    blueprint_source: str = 'none'
    blueprint_slug: str | None = None
    blueprint_version: str | None = None
    blueprint_lifecycle: str | None = None
    blueprint_issue_count: int = 0
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

    def issues_for_code(self, code: str) -> tuple[StampingValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == code)


@dataclass(frozen=True)
class StampRetryPlan:
    supported: bool
    strategy: str
    operation_key: str
    classification: str = 'retryable'
    message: str = ''


@dataclass(frozen=True)
class _RollbackPhaseScope:
    name: str
    active_planes: frozenset[int]


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
    parameter_metadata: Mapping[str, Any] = field(default_factory=dict)

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
    template_spec: Mapping[str, Any] = field(default_factory=dict)
    parameter_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _SelectedBlueprint:
    entry: BlueprintRegistryEntry | None = None
    source: str = 'none'


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
    phase: str | None = None,
) -> StampOperationPreview:
    """
    Build a dry-run preview for the safe V2.5 stamping subset.

    This function reads the database to classify creates vs. updates but never
    writes rows. Use apply_stamp_template_v25() to execute the same operation.
    """
    template_slug = getattr(template, 'slug', '')
    template_id = getattr(template, 'pk', None)
    raw_template_spec = deepcopy(getattr(template, 'template', None) or {})
    creation_options = creation_options or {}
    source_bindings = source_bindings or {}
    executor = _executor_name(raw_template_spec)
    operation_key = (
        f'{template_id or "unsaved"}:{template_slug}:{fabric_slug}:{executor or "unknown"}'
        f'{f":phase-{phase}" if phase else ""}'
    )

    validation = _validate_stamp_operation(
        template=template,
        template_spec=raw_template_spec,
        fabric_slug=fabric_slug,
        executor=executor,
        source_bindings=source_bindings,
        creation_options=creation_options,
        phase=phase,
    )
    issues = list(validation.issues)
    template_spec = dict(validation.template_spec or raw_template_spec)
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
        parameter_metadata=validation.parameter_metadata,
    )


def apply_stamp_template_v25(
    *,
    template,
    fabric_name: str,
    fabric_slug: str,
    source_bindings: dict | None = None,
    creation_options: dict | None = None,
    actor=None,
    phase: str | None = None,
) -> StampApplyResult:
    preview = preview_stamp_template_v25(
        template=template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
        phase=phase,
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
        phase=phase,
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
    phase_suffix = f':phase-{parameters.get("phase")}' if parameters.get('phase') else ''
    operation_key = (
        f'{getattr(stamp_run.template, "pk", None) or "unsaved"}:'
        f'{parameters.get("template_slug") or getattr(stamp_run.template, "slug", "")}:'
        f'{parameters.get("fabric_slug") or getattr(stamp_run.fabric, "slug", "")}:'
        f'{parameters.get("executor") or "unknown"}'
        f'{phase_suffix}'
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
        phase=parameters.get('phase'),
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
    shared_fabric_context = _rollback_shared_fabric_block_context(stamp_run=stamp_run, manifest=manifest)
    if shared_fabric_context is not None:
        issues.append(
            StampingValidationIssue(
                code='rollback_shared_fabric_stamp_runs',
                path='stamp_run.fabric',
                message=(
                    'Rollback is blocked because this fabric has other stamp runs and this run does not have an '
                    'isolated phase-scoped manifest.'
                ),
                context=shared_fabric_context,
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


def _rollback_shared_fabric_block_context(
    *,
    stamp_run: StampRun,
    manifest: Sequence[StampRollbackManifestItem],
) -> dict[str, Any] | None:
    if not stamp_run.fabric_id:
        return None
    other_runs = tuple(
        StampRun.objects.filter(fabric_id=stamp_run.fabric_id)
        .exclude(pk=stamp_run.pk)
        .order_by('created', 'pk')
    )
    if not other_runs:
        return None

    context: dict[str, Any] = {
        'fabric_id': stamp_run.fabric_id,
        'other_stamp_run_count': len(other_runs),
    }
    phase_scope = _rollback_phase_scope(stamp_run)
    if phase_scope is None:
        return {**context, 'reason': 'missing_phase_scope'}

    shared_item = _first_non_phase_local_manifest_item(manifest, phase_scope)
    if shared_item is not None:
        return {
            **context,
            'reason': 'manifest_not_phase_scoped',
            'phase': phase_scope.name,
            'active_planes': tuple(sorted(phase_scope.active_planes)),
            'model_label': shared_item.model_label,
            'primary_key': shared_item.primary_key,
            'natural_key': shared_item.natural_key,
        }

    for other_run in other_runs:
        if _stamp_run_rollback_completed(other_run):
            continue
        other_scope = _rollback_phase_scope(other_run)
        if other_scope is None:
            return {
                **context,
                'reason': 'other_run_missing_phase_scope',
                'phase': phase_scope.name,
                'other_stamp_run_id': other_run.pk,
            }
        if other_scope.name == phase_scope.name:
            return {
                **context,
                'reason': 'same_phase_stamp_run',
                'phase': phase_scope.name,
                'other_stamp_run_id': other_run.pk,
            }

    overlap = _rollback_manifest_overlap(stamp_run=stamp_run, manifest=manifest, other_runs=other_runs)
    if overlap is not None:
        return {
            **context,
            'reason': 'manifest_overlaps_other_stamp_run',
            'phase': phase_scope.name,
            **overlap,
        }
    return None


def _stamp_run_rollback_completed(stamp_run: StampRun) -> bool:
    rollback = (stamp_run.result or {}).get('rollback') or (stamp_run.metadata or {}).get('rollback') or {}
    return isinstance(rollback, dict) and rollback.get('state') == 'completed'


def _rollback_phase_scope(stamp_run: StampRun) -> _RollbackPhaseScope | None:
    parameters = stamp_run.parameters if isinstance(stamp_run.parameters, Mapping) else {}
    result = stamp_run.result if isinstance(stamp_run.result, Mapping) else {}
    stamp_manifest = result.get('stamp_manifest') if isinstance(result.get('stamp_manifest'), Mapping) else {}
    phase_payload = stamp_manifest.get('phase') if isinstance(stamp_manifest.get('phase'), Mapping) else {}
    phase_name = parameters.get('phase') or phase_payload.get('name')
    active_planes = (
        parameters.get('active_planes')
        or phase_payload.get('planes')
        or stamp_manifest.get('active_planes')
        or ()
    )
    plane_numbers = frozenset(_integer_ids(active_planes if isinstance(active_planes, (list, tuple)) else ()))
    if not phase_name or not plane_numbers:
        return None
    return _RollbackPhaseScope(name=str(phase_name), active_planes=plane_numbers)


def _first_non_phase_local_manifest_item(
    manifest: Sequence[StampRollbackManifestItem],
    phase_scope: _RollbackPhaseScope,
) -> StampRollbackManifestItem | None:
    objects_by_model = _objects_for_manifest_by_model(manifest)
    objects_by_identity = {
        (model._meta.label_lower, obj.pk): obj
        for model, objects in objects_by_model.items()
        for obj in objects
    }
    for item in manifest:
        obj = objects_by_identity.get((item.model_label, item.primary_key))
        if obj is None:
            continue
        object_planes = _rollback_object_phase_numbers(obj)
        if not object_planes or not object_planes.issubset(phase_scope.active_planes):
            return item
    return None


def _rollback_manifest_overlap(
    *,
    stamp_run: StampRun,
    manifest: Sequence[StampRollbackManifestItem],
    other_runs: Sequence[StampRun],
) -> dict[str, Any] | None:
    selected = {(item.model_label, item.primary_key): item for item in manifest}
    if not selected:
        return None
    model_labels_by_key = {key: model._meta.label_lower for key, model in _ROLLBACK_MODEL_KEYS}
    for other_run in other_runs:
        if other_run.pk == stamp_run.pk or _stamp_run_rollback_completed(other_run):
            continue
        other_managed = (other_run.result or {}).get('managed_objects')
        if not isinstance(other_managed, Mapping):
            continue
        for key, model_label in model_labels_by_key.items():
            for primary_key in _integer_ids(other_managed.get(key) or ()):
                item = selected.get((model_label, primary_key))
                if item is not None:
                    return {
                        'other_stamp_run_id': other_run.pk,
                        'model_label': item.model_label,
                        'primary_key': item.primary_key,
                        'natural_key': item.natural_key,
                    }
    return None


_PHASE_TOKEN_PATTERN = re.compile(r'(?:^|[:\-])(?:P|plane-)(\d+)(?=$|[:\-])', re.IGNORECASE)


def _rollback_object_phase_numbers(obj) -> frozenset[int]:
    if isinstance(obj, Fabric):
        return frozenset()
    if isinstance(obj, Plane):
        return frozenset({obj.plane_number})
    if isinstance(obj, FabricNode):
        return _phase_numbers_from_metadata(getattr(obj, 'metadata', None))
    if isinstance(obj, Endpoint):
        return _rollback_object_phase_numbers(obj.node)
    if isinstance(obj, ConnectorPosition):
        return _rollback_object_phase_numbers(obj.endpoint)
    if isinstance(obj, TransportChannel):
        if obj.plane_id and obj.plane:
            return frozenset({obj.plane.plane_number})
        return frozenset()
    if isinstance(obj, TransportChannelPositionMap):
        return _rollback_object_phase_numbers(obj.channel)
    if isinstance(obj, FiberSegment):
        return _phase_numbers_from_text(obj.name) or _phase_numbers_from_metadata(getattr(obj, 'metadata', None))
    if isinstance(obj, CableAssembly):
        return _phase_numbers_from_text(obj.cable_id) or _phase_numbers_from_metadata(getattr(obj, 'metadata', None))
    if isinstance(obj, FiberStrand):
        return _rollback_object_phase_numbers(obj.segment)
    if isinstance(obj, StrandTermination):
        return _rollback_object_phase_numbers(obj.strand)
    if isinstance(obj, TransferMap):
        metadata_planes = _phase_numbers_from_metadata(getattr(obj, 'metadata', None))
        return metadata_planes or _phase_numbers_from_text(obj.group_key)
    if isinstance(obj, OpticalLane):
        if obj.plane_id and obj.plane:
            return frozenset({obj.plane.plane_number})
        return _phase_numbers_from_metadata(getattr(obj, 'metadata', None))
    return frozenset()


def _phase_numbers_from_metadata(metadata: Any) -> frozenset[int]:
    if not isinstance(metadata, Mapping):
        return frozenset()
    raw_plane = metadata.get('plane_number') or metadata.get('plane')
    try:
        return frozenset({int(raw_plane)}) if raw_plane is not None else frozenset()
    except (TypeError, ValueError):
        return frozenset()


def _phase_numbers_from_text(value: Any) -> frozenset[int]:
    if not isinstance(value, str):
        return frozenset()
    return frozenset(int(match.group(1)) for match in _PHASE_TOKEN_PATTERN.finditer(value))


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
    phase: str | None = None,
) -> _StampOperationValidation:
    issues: list[StampingValidationIssue] = []

    try:
        resolved_template_spec = resolve_stamp_template_spec(template_spec, phase=phase)
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

    architecture_gate = _validate_architecture_contract(
        template=template,
        template_spec=resolved_template_spec,
        issues=issues,
    )
    _validate_fabric_collision(
        template=template,
        template_spec=resolved_template_spec,
        fabric_slug=fabric_slug,
        issues=issues,
    )
    _validate_fabric_ownership_preview(template_spec=resolved_template_spec, issues=issues)
    _validate_netbox_creation_options(
        template_spec=resolved_template_spec,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
        issues=issues,
    )
    return _StampOperationValidation(
        issues=tuple(issues),
        architecture_gate=architecture_gate,
        template_spec=resolved_template_spec,
        parameter_metadata=_parameter_metadata(resolved_template_spec),
    )


def _validate_architecture_contract(
    *,
    template,
    template_spec: dict,
    issues: list[StampingValidationIssue],
) -> StampArchitectureGate:
    template_architecture_slug = template_spec.get('architecture_slug')
    if not isinstance(template_architecture_slug, str):
        template_architecture_slug = None
    elif template_architecture_slug.strip():
        template_architecture_slug = template_architecture_slug.strip()
    else:
        template_architecture_slug = None
    template_architecture_version = template_spec.get('architecture_version')
    if not isinstance(template_architecture_version, str):
        template_architecture_version = None
    elif template_architecture_version.strip():
        template_architecture_version = template_architecture_version.strip()
    else:
        template_architecture_version = None

    architecture, target_source = _infer_persisted_architecture_target(template=template, template_spec=template_spec)
    selected_blueprint = _select_blueprint_for_template(
        architecture=architecture,
        template_architecture_slug=template_architecture_slug,
        template_architecture_version=template_architecture_version,
        issues=issues,
    )
    blueprint_entry = selected_blueprint.entry
    schema = blueprint_entry.definition if blueprint_entry is not None else None
    schema_errors = ()
    if schema is not None:
        schema_result = validate_architecture_schema(schema)
        schema_errors = schema_result.errors
        for error in schema_errors:
            issues.append(
                StampingValidationIssue(
                    code=f'architecture_schema.{error.code}',
                    path=error.path,
                    message=error.message,
                    context=error.context,
                )
            )

    blueprint_issue_count = 0
    if blueprint_entry is not None:
        blueprint_issue_count += _append_blueprint_parameter_issues(
            blueprint_entry=blueprint_entry,
            template_spec=template_spec,
            issues=issues,
        )
        blueprint_issue_count += _append_blueprint_compatibility_issues(
            blueprint_entry=blueprint_entry,
            issues=issues,
        )

    persisted_schema_valid = None
    persisted_schema_error_count = 0
    compatibility_status = 'not_checked'
    compatibility_issue_count = 0
    if architecture is not None and schema is not None:
        persisted_shuffle_pair_provider = _persisted_architecture_shuffle_pair_provider(
            architecture=architecture,
            fallback=schema.shuffle_pair_provider,
        )
        persisted_result = validate_persisted_architecture_schema(
            architecture,
            shuffle_pair_provider=persisted_shuffle_pair_provider,
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
                shuffle_pair_provider=persisted_shuffle_pair_provider,
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

    if schema is not None:
        template_matrix = _channel_map_matrix(template_spec)
        allocation_override = _allocation_rule_override_metadata(
            template_spec=template_spec,
            architecture=architecture,
            default_matrix=schema.channel_map_matrix,
        )
        if allocation_override.get('exists') and allocation_override.get('channel_map_matrix'):
            template_matrix = allocation_override['channel_map_matrix']
            channel_subinterfaces = template_spec.setdefault('channel_subinterfaces', {})
            channel_subinterfaces['channel_map_matrix'] = [dict(entry) for entry in template_matrix]
        elif allocation_override.get('slug') and not allocation_override.get('exists'):
            issues.append(
                StampingValidationIssue(
                    code='allocation_rule_override_missing',
                    path='template.template.allocation_rule_override',
                    message=(
                        f'Allocation rule override {allocation_override["slug"]!r} was not found on '
                        'the target architecture.'
                    ),
                    context={'slug': allocation_override['slug']},
                )
            )

        if allocation_override.get('exists') and allocation_override.get('delta_from_default'):
            issues.append(
                StampingValidationIssue(
                    code='allocation_rule_override_delta',
                    path='template.template.allocation_rule_override',
                    message='Allocation rule override changes the architecture default channel map.',
                    severity='warning',
                    context=allocation_override,
                )
            )

        if (
            not allocation_override.get('exists')
            and _normalize_matrix(template_matrix) != _normalize_matrix(schema.channel_map_matrix)
        ):
            issues.append(
                StampingValidationIssue(
                    code='channel_map_mismatch',
                    path='template.template.channel_subinterfaces.channel_map_matrix',
                    message='Template channel map matrix must match the selected blueprint channel map matrix.',
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
        fixture_valid=schema is not None and not schema_errors,
        fixture_error_count=len(schema_errors),
        template_architecture_slug=template_architecture_slug,
        template_architecture_version=template_architecture_version,
        blueprint_source=selected_blueprint.source,
        blueprint_slug=blueprint_entry.slug if blueprint_entry is not None else None,
        blueprint_version=blueprint_entry.version if blueprint_entry is not None else None,
        blueprint_lifecycle=blueprint_entry.lifecycle if blueprint_entry is not None else None,
        blueprint_issue_count=blueprint_issue_count,
        target_source=target_source,
        target_architecture_id=getattr(architecture, 'pk', None),
        target_architecture_slug=getattr(architecture, 'slug', None) if architecture is not None else None,
        target_architecture_version=getattr(architecture, 'version', None) if architecture is not None else None,
        persisted_schema_valid=persisted_schema_valid,
        persisted_schema_error_count=persisted_schema_error_count,
        compatibility_status=compatibility_status,
        compatibility_issue_count=compatibility_issue_count,
    )


_BLUEPRINT_TOPOLOGY_PARAMETER_KEYS = frozenset(
    {
        'plane_count',
        'gpu_tray_count',
        'leaf_count_per_plane',
        'racks_per_pod',
        'pods_per_fabric',
    }
)


def _select_blueprint_for_template(
    *,
    architecture: FabricArchitecture | None,
    template_architecture_slug: str | None,
    template_architecture_version: str | None,
    issues: list[StampingValidationIssue],
) -> _SelectedBlueprint:
    if not template_architecture_slug or not template_architecture_version:
        issues.append(
            StampingValidationIssue(
                code='architecture_gate.blueprint_identity_missing',
                path='template.template.architecture_slug',
                message='Template architecture_slug and architecture_version are required to select a blueprint.',
            )
        )
        return _SelectedBlueprint()

    registry = get_default_blueprint_registry()
    try:
        return _SelectedBlueprint(
            entry=registry.get_blueprint(template_architecture_slug, template_architecture_version),
            source='registry',
        )
    except KeyError as exc:
        lookup_error = exc

    if (
        architecture is not None
        and getattr(architecture, 'slug', None) == template_architecture_slug
        and getattr(architecture, 'version', None) == template_architecture_version
    ):
        persisted_entry = _blueprint_entry_from_persisted_architecture(architecture=architecture, issues=issues)
        if persisted_entry is not None:
            return _SelectedBlueprint(entry=persisted_entry, source='persisted_architecture')

    lookup_path = _blueprint_lookup_issue_path(
        registry=registry,
        slug=template_architecture_slug,
        version=template_architecture_version,
    )
    issues.append(
        StampingValidationIssue(
            code='architecture_gate.blueprint_not_registered',
            path=lookup_path,
            message=str(lookup_error).strip("'"),
            context={
                'architecture_slug': template_architecture_slug,
                'architecture_version': template_architecture_version,
            },
        )
    )

    return _SelectedBlueprint()


def _blueprint_lookup_issue_path(*, registry, slug: str, version: str) -> str:
    known_slugs = {entry.slug for entry in registry.list_blueprints(include_retired=True)}
    if slug in known_slugs and version:
        return 'template.template.architecture_version'
    return 'template.template.architecture_slug'


def _blueprint_entry_from_persisted_architecture(
    *,
    architecture: FabricArchitecture,
    issues: list[StampingValidationIssue],
) -> BlueprintRegistryEntry | None:
    metadata = getattr(architecture, 'metadata', None)
    if not isinstance(metadata, Mapping):
        metadata = {}
    blueprint_metadata = metadata.get('blueprint')
    if not isinstance(blueprint_metadata, Mapping):
        blueprint_metadata = {}
    definition_payload = blueprint_metadata.get('definition')
    if not isinstance(definition_payload, Mapping):
        issues.append(
            StampingValidationIssue(
                code='architecture_gate.blueprint_definition_missing',
                path='architecture.metadata.blueprint.definition',
                message=(
                    'Persisted architecture matches the requested template blueprint, but it does not '
                    'store an importable blueprint definition.'
                ),
                context={'architecture_id': getattr(architecture, 'pk', None)},
            )
        )
        return None

    try:
        definition = architecture_definition_from_payload(
            definition_payload,
            shuffle_pair_provider=_shuffle_pair_provider_for_definition_payload(definition_payload),
        )
    except ValueError as exc:
        issues.append(
            StampingValidationIssue(
                code='architecture_gate.blueprint_definition_invalid',
                path='architecture.metadata.blueprint.definition',
                message=str(exc),
                context={'architecture_id': getattr(architecture, 'pk', None)},
            )
        )
        return None

    parameter_schema = blueprint_metadata.get('parameter_schema')
    if not isinstance(parameter_schema, Mapping):
        parameter_schema = metadata.get('parameter_schema') if isinstance(metadata.get('parameter_schema'), Mapping) else {}
    required_device_types = blueprint_metadata.get('required_device_types')
    if not isinstance(required_device_types, Mapping):
        required_device_types = (
            metadata.get('required_device_types') if isinstance(metadata.get('required_device_types'), Mapping) else {}
        )
    lifecycle = blueprint_metadata.get('lifecycle')
    if not isinstance(lifecycle, str) or not lifecycle.strip():
        lifecycle = BLUEPRINT_LIFECYCLE_ACTIVE
    successor_version = blueprint_metadata.get('successor_version')
    if not isinstance(successor_version, str):
        successor_version = ''

    return BlueprintRegistryEntry(
        definition=definition,
        parameter_schema=parameter_schema,
        required_device_types=required_device_types,
        lifecycle=lifecycle,
        successor_version=successor_version,
        metadata={
            'source': 'persisted_architecture',
            'architecture_id': getattr(architecture, 'pk', None),
        },
    )


def _append_blueprint_parameter_issues(
    *,
    blueprint_entry: BlueprintRegistryEntry,
    template_spec: dict,
    issues: list[StampingValidationIssue],
) -> int:
    count = 0
    parameter_payload = _blueprint_parameter_payload(template_spec)
    for issue in validate_blueprint_parameters(
        blueprint_entry.parameter_schema,
        parameter_payload,
        path='template.template.blueprint_parameters',
    ):
        issues.append(
            StampingValidationIssue(
                code=issue.code,
                path=issue.path,
                message=issue.message,
                severity=issue.severity,
                context=issue.context,
            )
        )
        count += 1
    return count


def _append_blueprint_compatibility_issues(
    *,
    blueprint_entry: BlueprintRegistryEntry,
    issues: list[StampingValidationIssue],
) -> int:
    result = check_blueprint_device_type_compatibility(blueprint_entry)
    for issue in result.issues:
        severity = issue.severity
        context = dict(issue.context)
        if issue.code == 'architecture_gate.missing_device_type':
            context['registry_severity'] = severity
            severity = 'warning'
        issues.append(
            StampingValidationIssue(
                code=issue.code,
                path=issue.path,
                message=issue.message,
                severity=severity,
                context=context,
            )
        )
    return len(result.issues)


def _blueprint_parameter_payload(template_spec: dict) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    resolved = template_spec.get('_resolved') if isinstance(template_spec.get('_resolved'), Mapping) else {}

    raw_topology_parameters = template_spec.get('topology_parameters')
    if isinstance(raw_topology_parameters, Mapping) and raw_topology_parameters:
        resolved_topology = resolved.get('topology_parameters') if isinstance(resolved.get('topology_parameters'), Mapping) else {}
        payload['topology_parameters'] = {
            key: resolved_topology[key]
            for key in sorted(_BLUEPRINT_TOPOLOGY_PARAMETER_KEYS.intersection(raw_topology_parameters))
            if key in resolved_topology
        }

    if isinstance(template_spec.get('wavelength_plan'), Mapping):
        payload['wavelength_plan'] = dict(template_spec['wavelength_plan'])

    raw_name_patterns = template_spec.get('name_patterns')
    if isinstance(raw_name_patterns, Mapping) and raw_name_patterns:
        payload['name_patterns'] = _blueprint_name_pattern_parameters(raw_name_patterns)

    if isinstance(template_spec.get('dark_position_overrides'), Mapping) and template_spec['dark_position_overrides']:
        resolved_overrides = resolved.get('dark_position_overrides')
        payload['dark_position_overrides'] = (
            dict(resolved_overrides)
            if isinstance(resolved_overrides, Mapping)
            else dict(template_spec['dark_position_overrides'])
        )

    if template_spec.get('allocation_rule_override') is not None:
        payload['allocation_rule_override'] = (
            resolved.get('allocation_rule_override') or _allocation_rule_override_slug(template_spec)
        )

    return payload


def _blueprint_name_pattern_parameters(raw_name_patterns: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw_name_patterns.items():
        if isinstance(value, Mapping) and value.get('pattern') is not None:
            normalized[str(key)] = str(value['pattern'])
        elif value is not None:
            normalized[str(key)] = str(value)
    return normalized


def _persisted_architecture_shuffle_pair_provider(*, architecture: FabricArchitecture, fallback):
    try:
        entry = get_default_blueprint_registry().get_blueprint(
            getattr(architecture, 'slug', ''),
            getattr(architecture, 'version', ''),
        )
    except KeyError:
        return fallback or _shuffle_pair_provider_for_persisted_architecture(architecture)
    return entry.definition.shuffle_pair_provider or fallback


def _shuffle_pair_provider_for_persisted_architecture(architecture: FabricArchitecture):
    for pattern in architecture.transfer_patterns.all():
        if pattern.slug == 'shuffle_2x2' or pattern.pattern_kind == 'shuffle_2x2':
            return shuffle_2x2_transfer_position_pairs
    return None


def _shuffle_pair_provider_for_definition_payload(definition_payload: Mapping[str, Any]):
    transfer_patterns = definition_payload.get('transfer_patterns')
    if not isinstance(transfer_patterns, Sequence) or isinstance(transfer_patterns, (str, bytes, bytearray)):
        return None
    for pattern in transfer_patterns:
        if not isinstance(pattern, Mapping):
            continue
        if pattern.get('slug') == 'shuffle_2x2' or pattern.get('pattern_kind') == 'shuffle_2x2':
            return shuffle_2x2_transfer_position_pairs
    return None


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


def _allocation_rule_override_slug(template_spec: dict) -> str | None:
    resolved = template_spec.get('_resolved') if isinstance(template_spec.get('_resolved'), Mapping) else {}
    override = resolved.get('allocation_rule_override')
    if override:
        return str(override)
    raw_override = template_spec.get('allocation_rule_override')
    if isinstance(raw_override, Mapping):
        raw_override = raw_override.get('slug')
    if isinstance(raw_override, str) and raw_override.strip():
        return raw_override.strip()
    return None


def _allocation_rule_override_metadata(
    *,
    template_spec: dict,
    architecture: FabricArchitecture | None,
    default_matrix: Sequence[Mapping[str, Any]],
) -> dict:
    slug = _allocation_rule_override_slug(template_spec)
    if not slug:
        return {}
    if architecture is None:
        return {
            'slug': slug,
            'exists': False,
            'target_architecture_id': None,
        }
    rule_set = architecture.allocation_rule_sets.filter(slug=slug).first()
    if rule_set is None:
        return {
            'slug': slug,
            'exists': False,
            'target_architecture_id': architecture.pk,
        }
    rule = rule_set.rule if isinstance(rule_set.rule, Mapping) else {}
    matrix = [dict(entry) for entry in rule.get('channel_map_matrix') or [] if isinstance(entry, Mapping)]
    return {
        'slug': slug,
        'exists': True,
        'rule_set_id': rule_set.pk,
        'rule_set_name': rule_set.name,
        'target_architecture_id': architecture.pk,
        'channel_map_matrix': matrix,
        'delta_from_default': bool(matrix and _normalize_matrix(matrix) != _normalize_matrix(default_matrix)),
    }


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

    ownership = resolve_fabric_ownership(template_spec)
    site = ownership.get('site') or creation_options.get('site')
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
        fabric_slug=fabric_slug,
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


def _validate_fabric_ownership_preview(*, template_spec: dict, issues: list[StampingValidationIssue]) -> None:
    ownership = resolve_fabric_ownership(template_spec)
    for key, slug in sorted((ownership.get('missing') or {}).items()):
        issues.append(
            StampingValidationIssue(
                code='fabric_ownership_missing',
                path=f'template.template.fabric_ownership.{key}',
                message=f'Fabric ownership reference {key}={slug!r} does not exist in NetBox yet.',
                severity='warning',
                context={'field': key, 'slug': slug},
            )
        )


def _parameter_metadata(template_spec: dict) -> dict:
    resolved = template_spec.get('_resolved') if isinstance(template_spec.get('_resolved'), Mapping) else {}
    ownership = resolve_fabric_ownership(template_spec)
    return {
        'topology_parameters': resolved.get('topology_parameters') or {},
        'topology_parameter_metadata': resolved.get('topology_parameter_metadata') or {},
        'phase': resolved.get('phase'),
        'active_planes': resolved.get('active_planes') or template_spec.get('planes') or [],
        'all_planes': resolved.get('all_planes') or template_spec.get('planes') or [],
        'stamp_phases': resolved.get('stamp_phases') or [],
        'wavelength_plan': resolved.get('wavelength_plan'),
        'allocation_rule_override': resolved.get('allocation_rule_override'),
        'dark_position_overrides': resolved.get('dark_position_overrides') or {},
        'fabric_ownership': {
            'requested': ownership.get('requested') or {},
            'resolved': {
                key: value
                for key, value in (ownership.get('resolved') or {}).items()
                if value not in (None, '')
            },
            'missing': ownership.get('missing') or {},
        },
    }


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
    ownership = resolve_fabric_ownership(template_spec)
    ownership_after = {
        key: value
        for key, value in {
            'tenant_id': getattr(ownership.get('tenant'), 'pk', None),
            'scope_site_id': getattr(ownership.get('site'), 'pk', None),
            'scope_location_id': getattr(ownership.get('location'), 'pk', None),
        }.items()
        if value is not None
    }
    changes.append(
        StampPreviewChange(
            action=_action(fabric),
            object_type='Fabric',
            identity=fabric_slug,
            summary=f'Fabric {fabric_slug}',
            before=_object_before(fabric, 'name', 'status', 'architecture_id', 'tenant_id', 'scope_site_id', 'scope_location_id'),
            after={
                'name': fabric_name,
                'status': 'planned',
                'architecture_id': architecture_id,
                **ownership_after,
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
                    'phase': (template_spec.get('_resolved') or {}).get('phase'),
                    'active_planes': (template_spec.get('_resolved') or {}).get('active_planes') or template_spec.get('planes'),
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

    ownership = resolve_fabric_ownership(template_spec)
    site = ownership.get('site') or creation_options['site']
    gpu_device_type = creation_options['gpu_device_type']
    gpu_role = creation_options['gpu_role']
    leaf_device_type = creation_options.get('leaf_device_type') or gpu_device_type
    leaf_role = creation_options.get('leaf_role') or gpu_role
    name_prefix = creation_options.get('name_prefix') or fabric_slug

    changes: list[StampPreviewChange] = []
    device_specs = _expected_created_device_specs(
        template_spec=template_spec,
        fabric_slug=fabric_slug,
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

    for spec in _expected_channel_subinterface_specs(
        template_spec=template_spec,
        device_specs=device_specs,
        fabric_slug=fabric_slug,
    ):
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
    transfer_label = 'direct-attach' if _connection_geometry(template_spec) == 'direct_attach' else 'shuffle'
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
            identity=f'{fabric_slug}:{transfer_label}-transfer-maps',
            summary=f'{transfer_label} transfer maps are reconciled.',
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


def _resolved_spec(template_spec: dict) -> Mapping[str, Any]:
    resolved = template_spec.get('_resolved')
    return resolved if isinstance(resolved, Mapping) else {}


def _active_planes(template_spec: dict) -> tuple[int, ...]:
    return tuple(int(plane) for plane in (_resolved_spec(template_spec).get('active_planes') or template_spec.get('planes') or ()))


def _leaf_plane_assignment(template_spec: dict) -> dict[int, int]:
    assignment = (template_spec.get('leaf_ports') or {}).get('plane_assignment') or {}
    normalized = {}
    for raw_leaf, raw_plane in assignment.items():
        try:
            normalized[int(raw_leaf)] = int(raw_plane)
        except (TypeError, ValueError):
            continue
    return normalized


def _leaf_indexes_for_template(template_spec: dict) -> tuple[int, ...]:
    leaf_count = int((template_spec.get('leaf_ports') or {}).get('count') or 0)
    assignment = _leaf_plane_assignment(template_spec)
    active_planes = set(_active_planes(template_spec))
    if not active_planes:
        return tuple(range(1, leaf_count + 1))
    return tuple(
        leaf_index
        for leaf_index in range(1, leaf_count + 1)
        if assignment.get(leaf_index, leaf_index) in active_planes
    )


def _connection_geometry(template_spec: dict) -> str:
    resolved_geometry = _resolved_spec(template_spec).get('connection_geometry')
    if isinstance(resolved_geometry, str) and resolved_geometry:
        return resolved_geometry
    raw_geometry = template_spec.get('connection_geometry') or template_spec.get('transfer_geometry')
    if isinstance(raw_geometry, str) and raw_geometry:
        return raw_geometry
    return 'shuffle_2x2'


def _node_address_prefix(template_spec: dict, group_name: str, fallback: str) -> str:
    group_spec = template_spec.get(group_name) or {}
    if isinstance(group_spec, Mapping):
        raw_prefix = group_spec.get('address_prefix')
        if isinstance(raw_prefix, str) and raw_prefix.strip():
            return raw_prefix.strip()
    return fallback


def _gpu_node_address(template_spec: dict, index: int) -> str:
    return f'{_node_address_prefix(template_spec, "gpu_tray", "GB300-TRAY")}-{index}'


def _leaf_node_address(template_spec: dict, index: int) -> str:
    return f'{_node_address_prefix(template_spec, "leaf_ports", "LEAF")}-{index}'


def _mpo_connector_kind(template_spec: dict) -> str:
    position_count = int((template_spec.get('gpu_tray') or {}).get('positions_per_mpo') or 12)
    if position_count in {8, 12, 16, 24}:
        return f'mpo-{position_count}'
    return 'other'


def _name_pattern(template_spec: dict, role_kind: str, fallback: str) -> str:
    patterns = _resolved_spec(template_spec).get('name_patterns') or {}
    raw_pattern = patterns.get(role_kind) or patterns.get('device' if role_kind in {'gpu_tray', 'leaf_switch'} else '')
    if isinstance(raw_pattern, Mapping) and raw_pattern.get('pattern'):
        return str(raw_pattern['pattern'])
    return fallback


def _name_pattern_context(
    *,
    fabric_slug: str,
    node_address: str,
    tray_index: int = 1,
    plane_index: int = 1,
    port_index: int = 1,
    channel_index: int = 1,
    rack_id: int = 1,
    parent_name: str = '',
) -> dict:
    return {
        'rack_id': rack_id,
        'tray_index': tray_index,
        'plane_index': plane_index,
        'port_index': port_index,
        'channel_index': channel_index,
        'node_address': node_address,
        'fabric_slug': fabric_slug,
        'parent_name': parent_name,
    }


def _render_name_pattern(pattern: str, context: Mapping[str, Any]) -> str:
    return pattern.format(**context)


def _local_index_from_address(address: str) -> int:
    try:
        return int(str(address).rsplit('-', 1)[1])
    except (IndexError, TypeError, ValueError):
        return 1


def _osfp_index_from_endpoint(address: str) -> int:
    marker = '.OSFP-'
    if marker not in str(address):
        return 1
    raw_value = str(address).split(marker, 1)[1].split('.', 1)[0]
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 1


def _expected_node_specs(template_spec: dict) -> list[dict]:
    nodes = []
    gpu_count = int((template_spec.get('gpu_tray') or {}).get('count') or 0)
    for tray in range(1, gpu_count + 1):
        address = _gpu_node_address(template_spec, tray)
        nodes.append({'address': address, 'name': address, 'node_kind': 'active_device', 'local_index': tray})
    if _connection_geometry(template_spec) != 'direct_attach':
        for cassette in range(1, template_spec['shuffle_cassettes']['count'] + 1):
            nodes.append(
                {
                    'address': f'SHUFFLE-CASSETTE-{cassette}',
                    'name': f'SHUFFLE-CASSETTE-{cassette}',
                    'node_kind': 'passive_assembly',
                    'local_index': cassette,
                }
            )
    for leaf in _leaf_indexes_for_template(template_spec):
        address = _leaf_node_address(template_spec, leaf)
        nodes.append({'address': address, 'name': address, 'node_kind': 'active_device', 'local_index': leaf})
    return nodes


def _expected_endpoint_specs(template_spec: dict) -> list[dict]:
    endpoints: list[dict] = []
    gpu_spec = template_spec['gpu_tray']
    shuffle_spec = template_spec.get('shuffle_cassettes') or {}
    connector_kind = _mpo_connector_kind(template_spec)

    for tray in range(1, gpu_spec['count'] + 1):
        tray_address = _gpu_node_address(template_spec, tray)
        for osfp in range(1, gpu_spec['osfp_count'] + 1):
            osfp_address = f'{tray_address}.OSFP-{osfp}'
            endpoints.append(_endpoint_spec(osfp_address, f'OSFP-{osfp}', 'plugin_port', 'osfp'))
            for mpo in range(1, gpu_spec['mpo_per_osfp'] + 1):
                endpoints.append(
                    _endpoint_spec(
                        f'{osfp_address}.MPO-{mpo}',
                        f'OSFP-{osfp}.MPO-{mpo}',
                        'subconnector',
                        connector_kind,
                        gpu_spec['positions_per_mpo'],
                    )
                )

    if _connection_geometry(template_spec) != 'direct_attach':
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

    for leaf in _leaf_indexes_for_template(template_spec):
        osfp_address = f'{_leaf_node_address(template_spec, leaf)}.OSFP-1'
        endpoints.append(_endpoint_spec(osfp_address, 'OSFP-1', 'plugin_port', 'osfp'))
        for mpo in range(1, gpu_spec['mpo_per_osfp'] + 1):
            endpoints.append(
                _endpoint_spec(
                    f'{osfp_address}.MPO-{mpo}',
                    f'OSFP-1.MPO-{mpo}',
                    'subconnector',
                    connector_kind,
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
        gpu_tray = int(proof_path.get('gpu_tray') or 1)
        gpu_endpoint = f'{_gpu_node_address(template_spec, gpu_tray)}.OSFP-{proof_path["gpu_osfp"]}'
        leaf_endpoint = f'{_leaf_node_address(template_spec, proof_path["leaf"])}.OSFP-1'
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
        if _connection_geometry(template_spec) == 'direct_attach':
            names.append(f'P{plane}-GPU-to-LEAF')
        else:
            names.append(f'P{plane}-GPU-to-SHUFFLE')
            names.append(f'P{plane}-SHUFFLE-to-LEAF')
    return names


def _expected_created_device_specs(
    *,
    template_spec: dict,
    fabric_slug: str,
    name_prefix: str,
    site: Site,
    gpu_device_type: DeviceType,
    gpu_role: DeviceRole,
    leaf_device_type: DeviceType,
    leaf_role: DeviceRole,
) -> list[dict]:
    specs = []
    gpu_count = int((template_spec.get('gpu_tray') or {}).get('count') or 0)
    for tray in range(1, gpu_count + 1):
        address = _gpu_node_address(template_spec, tray)
        specs.append(
            {
                'address': address,
                'device_kind': 'gpu',
                'name': _render_name_pattern(
                    _name_pattern(template_spec, 'gpu_tray', _netbox_name(name_prefix, address)),
                    _name_pattern_context(
                        fabric_slug=fabric_slug,
                        node_address=address,
                        tray_index=tray,
                        rack_id=tray,
                    ),
                ),
                'site': site,
                'device_type': gpu_device_type,
                'role': gpu_role,
            }
        )
    assignment = _leaf_plane_assignment(template_spec)
    for leaf in _leaf_indexes_for_template(template_spec):
        address = _leaf_node_address(template_spec, leaf)
        specs.append(
            {
                'address': address,
                'device_kind': 'leaf',
                'name': _render_name_pattern(
                    _name_pattern(template_spec, 'leaf_switch', _netbox_name(name_prefix, address)),
                    _name_pattern_context(
                        fabric_slug=fabric_slug,
                        node_address=address,
                        tray_index=1,
                        plane_index=assignment.get(leaf, leaf),
                        port_index=leaf,
                        rack_id=leaf,
                    ),
                ),
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
    fabric_slug: str,
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

    name_pattern = _name_pattern(template_spec, 'channel_subinterface', channel_spec.get('name_pattern') or '{parent_name}/{channel_index}')
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
                    'name': _render_name_pattern(
                        name_pattern,
                        _name_pattern_context(
                            fabric_slug=fabric_slug,
                            node_address=interface_spec['device_address'],
                            tray_index=_local_index_from_address(interface_spec['device_address']),
                            plane_index=channel_index,
                            port_index=_osfp_index_from_endpoint(interface_spec['endpoint_address']),
                            channel_index=channel_index,
                            parent_name=interface_spec['name'],
                        ),
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
    samples = _generic_name_pattern_samples(template_spec=template_spec, fabric_slug=fabric_slug)
    if not _creation_options_are_previewable(creation_options):
        return samples
    required_sections = ('gpu_tray', 'leaf_ports', 'channel_subinterfaces')
    if any(not isinstance(template_spec.get(section), Mapping) for section in required_sections):
        return samples

    ownership = resolve_fabric_ownership(template_spec)
    site = ownership.get('site') or creation_options['site']
    gpu_device_type = creation_options['gpu_device_type']
    gpu_role = creation_options['gpu_role']
    leaf_device_type = creation_options.get('leaf_device_type') or gpu_device_type
    leaf_role = creation_options.get('leaf_role') or gpu_role
    name_prefix = creation_options.get('name_prefix') or fabric_slug
    device_specs = _expected_created_device_specs(
        template_spec=template_spec,
        fabric_slug=fabric_slug,
        name_prefix=name_prefix,
        site=site,
        gpu_device_type=gpu_device_type,
        gpu_role=gpu_role,
        leaf_device_type=leaf_device_type,
        leaf_role=leaf_role,
    )

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

    for spec in _expected_channel_subinterface_specs(
        template_spec=template_spec,
        device_specs=device_specs,
        fabric_slug=fabric_slug,
    ):
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


def _generic_name_pattern_samples(*, template_spec: dict, fabric_slug: str) -> list[StampNamePatternSample]:
    patterns = _resolved_spec(template_spec).get('name_patterns') or {}
    samples: list[StampNamePatternSample] = []
    for role_kind, pattern_spec in patterns.items():
        if not isinstance(pattern_spec, Mapping):
            continue
        pattern = pattern_spec.get('pattern')
        if not pattern:
            continue
        sample_count = int(pattern_spec.get('sample_count') or 3)
        for index in range(1, sample_count + 1):
            node_address = (
                _gpu_node_address(template_spec, 1)
                if role_kind in {'gpu_tray', 'device'}
                else _leaf_node_address(template_spec, index)
            )
            context = _name_pattern_context(
                fabric_slug=fabric_slug,
                node_address=node_address,
                rack_id=index,
                tray_index=index,
                plane_index=index,
                port_index=index,
                channel_index=index,
            )
            samples.append(
                StampNamePatternSample(
                    object_type=f'NamePattern:{role_kind}',
                    address=f'{role_kind}:sample-{index}',
                    name=_render_name_pattern(str(pattern), context),
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
