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
        from .spatial_stamp import build_floorplan_sync_summary, stamp_spatial_template
        from ..models import SpatialTemplate
        template = SpatialTemplate.objects.get(pk=params['template_id'])
        scope = _resolve_scope(params['scope_type'], params['scope_id'])
        sync_floorplan = params.get('sync_floorplan', True)
        force_floorplan_sync = params.get('force_floorplan_sync', False)
        stamp_result = stamp_spatial_template(
            template,
            scope,
            variables=params.get('variables'),
            user=user,
            sync_floorplan=sync_floorplan,
            force_floorplan_sync=force_floorplan_sync,
        )
        return DispatchResult(
            result_obj=scope,
            record_metadata={
                'floorplan_sync': build_floorplan_sync_summary(
                    stamp_result,
                    sync_requested=sync_floorplan,
                ),
            },
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
