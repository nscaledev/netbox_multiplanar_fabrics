"""
Deployment plan execution orchestrator.

Executes all pending StampRecords in a DeploymentPlan in dependency order
(spatial → rack population → assembly), and provides rollback capability.

StampRecord.parameters schema (keyed by stamp_type):

  spatial:
    {"stamp_type": "spatial", "template_id": <int>, "scope_type": "site"|"location", "scope_id": <int>}

  rack_population:
    {"stamp_type": "rack_population", "template_id": <int>, "rack_pk": <int>}

  assembly_passive:
    {"stamp_type": "assembly_passive", "template_id": <int>, "name": <str>,
     "site_pk": <int|null>, "location_pk": <int|null>, "rack_pk": <int|null>,
     "device_role_pk": <int|null>, "position": <float|null>, "face": <str>}

  cable_assembly:
    {"stamp_type": "cable_assembly", "template_id": <int>, "label": <str>,
     "a_ct_id": <int>, "a_pks": [<int>, ...],
     "b_ct_id": <int>, "b_pks": [<int>, ...]}

  breakout:
    {"stamp_type": "breakout", "template_id": <int>, "device_pk": <int>}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from ..models import DeploymentPlan

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Execution outcome plus any metadata that should be persisted on the record."""

    result_obj: Any = None
    record_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TemplatePlanExecutionResult:
    """V2 helper return contract for template execution workflows."""

    template: Any
    fabric: Any
    stamp_run: Any
    stamp_result: Any
    rollback_eligible: bool


@dataclass(frozen=True)
class StampRunRollbackResult:
    """Rollback outcome contract for deployment workflow pages."""

    stamp_run: Any
    already_rolled_back: bool
    rollback_mode: str
    deleted_counts: dict[str, int]
    deleted_total: int


@dataclass(frozen=True)
class DeploymentWorkflowRunSummary:
    """Per-run summary row for deployment workflow pages."""

    stamp_run: Any
    managed_object_total: int
    rollback_applied: bool
    rollback_eligible: bool
    rollback_mode_hint: str


@dataclass(frozen=True)
class DeploymentWorkflowSummary:
    """Summary payload for deployment workflow page context."""

    recent_runs: tuple[DeploymentWorkflowRunSummary, ...]
    rollback_eligible_run_ids: tuple[int, ...]
    rollback_applied_run_ids: tuple[int, ...]
    total_runs: int


_MANAGED_OBJECT_MODEL_KEYS = (
    ('optical_lanes', 'OpticalLane'),
    ('transfer_maps', 'TransferMap'),
    ('strand_terminations', 'StrandTermination'),
    ('fiber_strands', 'FiberStrand'),
    ('cable_assemblies', 'CableAssembly'),
    ('fiber_segments', 'FiberSegment'),
    ('transport_channels', 'TransportChannel'),
    ('connector_positions', 'ConnectorPosition'),
    ('endpoints', 'Endpoint'),
    ('nodes', 'FabricNode'),
    ('planes', 'Plane'),
    ('fabrics', 'Fabric'),
)


def _is_authenticated_actor(actor) -> bool:
    return actor is not None and bool(getattr(actor, 'is_authenticated', False))


def _actor_id(actor) -> int | None:
    return actor.pk if _is_authenticated_actor(actor) else None


def _iso_timestamp(value=None) -> str:
    if value is None:
        value = timezone.now()
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def _rollback_metadata(stamp_run) -> dict[str, Any]:
    metadata = dict(stamp_run.metadata or {})
    rollback = metadata.get('rollback')
    return rollback if isinstance(rollback, dict) else {}


def _managed_objects_from_result(stamp_run) -> dict[str, list[int]]:
    raw = (stamp_run.result or {}).get('managed_objects')
    if not isinstance(raw, dict):
        return {}
    managed_objects: dict[str, list[int]] = {}
    for key, value in raw.items():
        if not isinstance(value, (list, tuple)):
            continue
        ids: list[int] = []
        for raw_id in value:
            try:
                ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        managed_objects[key] = ids
    return managed_objects


def _managed_object_total(managed_objects: dict[str, list[int]]) -> int:
    return sum(len(ids) for ids in managed_objects.values() if isinstance(ids, list))


def _resolve_stamp_run(stamp_run):
    from ..models import StampRun

    if isinstance(stamp_run, StampRun):
        return stamp_run
    return StampRun.objects.get(pk=getattr(stamp_run, 'pk', stamp_run))


def _managed_model_map() -> dict[str, Any]:
    from ..models import (
        CableAssembly,
        ConnectorPosition,
        Endpoint,
        Fabric,
        FabricNode,
        FiberSegment,
        FiberStrand,
        OpticalLane,
        Plane,
        StrandTermination,
        TransferMap,
        TransportChannel,
    )

    return {
        'optical_lanes': OpticalLane,
        'transfer_maps': TransferMap,
        'strand_terminations': StrandTermination,
        'fiber_strands': FiberStrand,
        'cable_assemblies': CableAssembly,
        'fiber_segments': FiberSegment,
        'transport_channels': TransportChannel,
        'connector_positions': ConnectorPosition,
        'endpoints': Endpoint,
        'nodes': FabricNode,
        'planes': Plane,
        'fabrics': Fabric,
    }


def _delete_managed_objects(*, managed_objects: dict[str, list[int]], skip_keys: set[str] | None = None) -> dict[str, int]:
    model_map = _managed_model_map()
    deleted_counts: dict[str, int] = {}
    skip = skip_keys or set()
    for key, _model_name in _MANAGED_OBJECT_MODEL_KEYS:
        if key in skip:
            continue
        model = model_map.get(key)
        object_ids = managed_objects.get(key) or []
        if model is None or not object_ids:
            continue
        queryset = model.objects.filter(pk__in=object_ids)
        existing_count = queryset.count()
        if existing_count:
            queryset.delete()
        deleted_counts[key] = existing_count
    return deleted_counts


def _stamp_run_is_rollback_eligible(stamp_run) -> bool:
    rollback = _rollback_metadata(stamp_run)
    if rollback.get('state') == 'completed':
        return False
    managed_objects = _managed_objects_from_result(stamp_run)
    if _managed_object_total(managed_objects) > 0:
        return True
    return bool(stamp_run.fabric_id)


def _rollback_mode_hint(stamp_run) -> str:
    if stamp_run.fabric_id:
        return 'fabric_delete'
    managed_objects = _managed_objects_from_result(stamp_run)
    if _managed_object_total(managed_objects) > 0:
        return 'managed_objects'
    return 'none'


@transaction.atomic
def execute_template_plan(
    *,
    template,
    fabric_name: str,
    fabric_slug: str,
    source_bindings: dict | None = None,
    creation_options: dict | None = None,
    actor=None,
) -> TemplatePlanExecutionResult:
    """
    Execute a V2 StampTemplate through the stamping service.

    Returns execution context used by deployment workflow pages.
    """
    from .stamping import execute_stamp_template

    stamp_result = execute_stamp_template(
        template=template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=source_bindings,
        creation_options=creation_options,
        actor=actor,
    )
    stamp_run = stamp_result.stamp_run
    stamp_run.metadata = {
        **(stamp_run.metadata or {}),
        'deployment_workflow': {
            'helper': 'execute_template_plan',
            'executed_at': _iso_timestamp(),
            'executed_by_id': _actor_id(actor),
        },
    }
    stamp_run.save(update_fields=['metadata'])
    return TemplatePlanExecutionResult(
        template=template,
        fabric=stamp_result.fabric,
        stamp_run=stamp_run,
        stamp_result=stamp_result,
        rollback_eligible=_stamp_run_is_rollback_eligible(stamp_run),
    )


@transaction.atomic
def rollback_stamp_run(
    *,
    stamp_run,
    actor=None,
    delete_fabric_as_primitive: bool = True,
) -> StampRunRollbackResult:
    """
    Roll back one V2 StampRun.

    Rollback is idempotent: repeated calls return the existing rollback metadata
    without executing additional deletes.
    """
    from ..models import Fabric
    from .audit import record_audit_event

    stamp_run = _resolve_stamp_run(stamp_run)
    rollback = _rollback_metadata(stamp_run)
    if rollback.get('state') == 'completed':
        deleted_counts = rollback.get('deleted_counts') or {}
        deleted_total = int(rollback.get('deleted_total') or 0)
        return StampRunRollbackResult(
            stamp_run=stamp_run,
            already_rolled_back=True,
            rollback_mode=str(rollback.get('mode') or 'none'),
            deleted_counts=dict(deleted_counts),
            deleted_total=deleted_total,
        )

    fabric = stamp_run.fabric
    managed_objects = _managed_objects_from_result(stamp_run)
    deleted_counts: dict[str, int] = {}
    rollback_mode = 'none'

    if delete_fabric_as_primitive and fabric is not None:
        fabric_id = fabric.pk
        existed = Fabric.objects.filter(pk=fabric_id).exists()
        if existed:
            fabric.delete()
            deleted_counts['fabrics'] = 1
            rollback_mode = 'fabric_delete'
        else:
            deleted_counts['fabrics'] = 0
            rollback_mode = 'fabric_delete'
        managed_deletes = _delete_managed_objects(
            managed_objects=managed_objects,
            skip_keys={'fabrics'},
        )
        deleted_counts.update(
            {
                key: max(deleted_counts.get(key, 0), managed_deletes.get(key, 0))
                for key in managed_deletes
            }
        )
    else:
        deleted_counts = _delete_managed_objects(managed_objects=managed_objects)
        rollback_mode = 'managed_objects' if deleted_counts else 'none'

    stamp_run.refresh_from_db()
    deleted_total = sum(deleted_counts.values())
    rollback_metadata = {
        'state': 'completed',
        'mode': rollback_mode,
        'applied_at': _iso_timestamp(),
        'applied_by_id': _actor_id(actor),
        'deleted_counts': deleted_counts,
        'deleted_total': deleted_total,
    }
    stamp_run.metadata = {
        **(stamp_run.metadata or {}),
        'rollback': rollback_metadata,
    }
    stamp_run.result = {
        **(stamp_run.result or {}),
        'rollback': rollback_metadata,
    }
    stamp_run.save(update_fields=['metadata', 'result'])

    record_audit_event(
        event_type='stamp',
        fabric=stamp_run.fabric,
        actor=actor if _is_authenticated_actor(actor) else None,
        subject=stamp_run,
        outcome='ok',
        message=f'Rolled back stamp run #{stamp_run.pk}.',
        payload={
            'action': 'rollback',
            'stamp_run_id': stamp_run.pk,
            'mode': rollback_mode,
            'deleted_total': deleted_total,
            'deleted_counts': deleted_counts,
        },
        metadata={
            'rollback': rollback_metadata,
        },
    )

    return StampRunRollbackResult(
        stamp_run=stamp_run,
        already_rolled_back=False,
        rollback_mode=rollback_mode,
        deleted_counts=deleted_counts,
        deleted_total=deleted_total,
    )


def build_deployment_workflow_summary(
    *,
    fabric=None,
    template=None,
    limit: int = 20,
) -> DeploymentWorkflowSummary:
    """
    Build summary context for deployment workflow UI pages.
    """
    from ..models import Fabric, StampRun, StampTemplate

    queryset = StampRun.objects.select_related('fabric', 'template').order_by('-created', '-pk')
    if fabric is not None:
        fabric_id = getattr(fabric, 'pk', fabric)
        if isinstance(fabric, Fabric) or fabric_id is not None:
            queryset = queryset.filter(fabric_id=fabric_id)
    if template is not None:
        template_id = getattr(template, 'pk', template)
        if isinstance(template, StampTemplate) or template_id is not None:
            queryset = queryset.filter(template_id=template_id)

    rows: list[DeploymentWorkflowRunSummary] = []
    rollback_eligible_run_ids: list[int] = []
    rollback_applied_run_ids: list[int] = []

    for run in queryset[: max(1, limit)]:
        managed_total = _managed_object_total(_managed_objects_from_result(run))
        rollback = _rollback_metadata(run)
        rollback_applied = rollback.get('state') == 'completed'
        rollback_eligible = _stamp_run_is_rollback_eligible(run)
        if rollback_applied:
            rollback_applied_run_ids.append(run.pk)
        if rollback_eligible:
            rollback_eligible_run_ids.append(run.pk)
        rows.append(
            DeploymentWorkflowRunSummary(
                stamp_run=run,
                managed_object_total=managed_total,
                rollback_applied=rollback_applied,
                rollback_eligible=rollback_eligible,
                rollback_mode_hint=_rollback_mode_hint(run),
            )
        )

    return DeploymentWorkflowSummary(
        recent_runs=tuple(rows),
        rollback_eligible_run_ids=tuple(rollback_eligible_run_ids),
        rollback_applied_run_ids=tuple(rollback_applied_run_ids),
        total_runs=len(rows),
    )


def execute_plan(
    plan: DeploymentPlan,
    *,
    user: AbstractUser | None = None,
) -> DeploymentPlan:
    """
    Execute all pending StampRecords in a DeploymentPlan.

    The plan must be in 'approved' status. Stamps are executed in
    dependency order: spatial → rack population → assembly.
    On any failure, the current transaction is rolled back and the
    failing StampRecord is marked 'failed'.

    Parameters
    ----------
    plan : DeploymentPlan
        The plan to execute.
    user : AbstractUser | None
        The user triggering execution.

    Returns
    -------
    DeploymentPlan
        The updated plan object.
    """
    if plan.status != 'approved':
        raise ValueError(f'Plan must be in approved status to execute, got {plan.status}')

    plan.status = 'stamping'
    plan.save(update_fields=['status'])

    pending_records = list(
        plan.stamp_records.filter(status='pending').order_by('pk')
    )

    current_record = None
    try:
        with transaction.atomic():
            for record in pending_records:
                current_record = record
                dispatch_result = _dispatch_record(record, user)
                if isinstance(dispatch_result, DispatchResult):
                    result_obj = dispatch_result.result_obj
                    if dispatch_result.record_metadata:
                        record.metadata = {
                            **(record.metadata or {}),
                            **dispatch_result.record_metadata,
                        }
                else:
                    result_obj = dispatch_result
                if result_obj is not None:
                    result_ct = ContentType.objects.get_for_model(result_obj)
                    record.result_type = result_ct
                    record.result_id = result_obj.pk
                record.stamped_at = timezone.now()
                record.stamped_by = user
                record.status = 'stamped'
                record.save()

            plan.status = 'active'
            plan.save(update_fields=['status'])

    except Exception as exc:
        logger.exception('Plan execution failed for plan pk=%s', plan.pk)
        # The atomic block has been rolled back; persist the failure state.
        if current_record is not None:
            current_record.status = 'failed'
            current_record.error_detail = str(exc)[:2000]
            current_record.save(update_fields=['status', 'error_detail'])
        plan.refresh_from_db(fields=['status'])
        plan.status = 'draft'
        plan.save(update_fields=['status'])
        raise

    logger.info('Executed plan %s — %d stamps completed', plan, len(pending_records))

    # Post-stamp: trigger graph rebuild for affected scope
    try:
        from .sync.rebuilder import rebuild_graph

        rebuild_graph(trigger_mode='stamp')
        logger.info('Post-stamp graph rebuild completed for plan %s', plan)
    except Exception:
        logger.exception('Post-stamp graph rebuild failed for plan %s', plan.pk)

    # Post-rebuild: trigger persistent plane audit
    try:
        from .graph.persistent_audits import run_persistent_plane_audit

        run_persistent_plane_audit(trigger_mode='stamp')
        logger.info('Post-stamp audit completed for plan %s', plan)
    except Exception:
        logger.exception('Post-stamp audit failed for plan %s', plan.pk)

    return plan


def _resolve_scope(scope_type: str, scope_id: int):
    """Resolve scope_type name + pk to the actual Site or Location instance."""
    if scope_type == 'site':
        from dcim.models import Site
        return Site.objects.get(pk=scope_id)
    if scope_type == 'location':
        from dcim.models import Location
        return Location.objects.get(pk=scope_id)
    raise ValueError(f'Unknown scope_type: {scope_type!r}')


def _dispatch_record(record, user):
    """
    Execute a single pending StampRecord by dispatching on parameters['stamp_type'].

    Returns the primary result object (for provenance tracking), or None if the
    stamp type produces no single identifiable result.
    """
    params = record.parameters or {}
    stamp_type = params.get('stamp_type')

    if stamp_type == 'spatial':
        from .spatial_stamp import stamp_spatial_template
        from ..models import SpatialTemplate
        template = SpatialTemplate.objects.get(pk=params['template_id'])
        scope = _resolve_scope(params['scope_type'], params['scope_id'])
        stamp_spatial_template(
            template,
            scope,
            variables=params.get('variables'),
            user=user,
        )
        return DispatchResult(
            result_obj=scope,
        )

    if stamp_type == 'rack_population':
        from .rack_population_stamp import stamp_rack_population
        from ..models import RackPopulationTemplate
        from dcim.models import Rack
        template = RackPopulationTemplate.objects.get(pk=params['template_id'])
        rack = Rack.objects.get(pk=params['rack_pk'])
        stamp_rack_population(template, rack, user=user)
        return rack

    if stamp_type == 'assembly_passive':
        from .assembly_stamp import stamp_passive_device
        from ..models import AssemblyTemplate
        from dcim.models import DeviceRole, Location, Rack, Site
        template = AssemblyTemplate.objects.get(pk=params['template_id'])
        site = Site.objects.get(pk=params['site_pk']) if params.get('site_pk') else None
        location = Location.objects.get(pk=params['location_pk']) if params.get('location_pk') else None
        rack = Rack.objects.get(pk=params['rack_pk']) if params.get('rack_pk') else None
        device_role = DeviceRole.objects.get(pk=params['device_role_pk']) if params.get('device_role_pk') else None
        result = stamp_passive_device(
            template,
            name=params['name'],
            site=site,
            location=location,
            rack=rack,
            position=params.get('position'),
            face=params.get('face', 'front'),
            device_role=device_role,
            user=user,
        )
        return result.device

    if stamp_type == 'cable_assembly':
        from .assembly_stamp import stamp_cable_assembly
        from ..models import AssemblyTemplate
        template = AssemblyTemplate.objects.get(pk=params['template_id'])
        a_ct = ContentType.objects.get(pk=params['a_ct_id'])
        b_ct = ContentType.objects.get(pk=params['b_ct_id'])
        a_targets = list(a_ct.model_class().objects.filter(pk__in=params['a_pks']))
        b_targets = list(b_ct.model_class().objects.filter(pk__in=params['b_pks']))
        result = stamp_cable_assembly(
            template,
            a_targets,
            b_targets,
            label=params.get('label', ''),
            user=user,
        )
        return result.cable

    if stamp_type == 'breakout':
        from .assembly_stamp import create_child_interfaces_from_breakout_spec
        from ..models import DeviceBreakoutTemplate
        from dcim.models import Device
        template = DeviceBreakoutTemplate.objects.get(pk=params['template_id'])
        device = Device.objects.get(pk=params['device_pk'])
        create_child_interfaces_from_breakout_spec(device=device, breakout_template=template)
        return device

    if stamp_type is None:
        # No stamp_type means this record was created without a dispatch payload
        # (e.g. tests exercising plan lifecycle only). Treat as a no-op.
        return None

    raise ValueError(f'Unknown stamp_type: {stamp_type!r} in StampRecord pk={record.pk}')


def rollback_plan(
    plan: DeploymentPlan,
    *,
    user: AbstractUser | None = None,
) -> DeploymentPlan:
    """
    Rollback all stamped records in a DeploymentPlan.

    Walks StampRecords in reverse order and deletes the result objects.
    Each rolled-back record is marked 'rolled_back'.

    Parameters
    ----------
    plan : DeploymentPlan
        The plan to roll back.
    user : AbstractUser | None
        The user triggering rollback.

    Returns
    -------
    DeploymentPlan
        The updated plan object.
    """
    if plan.status not in ('active', 'stamping'):
        raise ValueError(f'Plan must be in active or stamping status to rollback, got {plan.status}')

    stamped_records = list(
        plan.stamp_records.filter(status='stamped').order_by('-pk')
    )

    with transaction.atomic():
        for record in stamped_records:
            if record.result_type and record.result_id:
                try:
                    model_class = record.result_type.model_class()
                    if model_class:
                        model_class.objects.filter(pk=record.result_id).delete()
                except Exception:
                    logger.warning(
                        'Could not delete result for stamp record pk=%s', record.pk, exc_info=True,
                    )
            record.status = 'rolled_back'
            record.save(update_fields=['status'])

        plan.status = 'rolled_back'
        plan.save(update_fields=['status'])

    logger.info('Rolled back plan %s — %d records reversed', plan, len(stamped_records))
    return plan
