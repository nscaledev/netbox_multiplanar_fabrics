from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from netbox_plant_graph.models import Fabric, OperationRun, OpticalLane
from netbox_plant_graph.services.audit import record_audit_event
from netbox_plant_graph.services.resolver import resolve_optical_lane_path


@dataclass(frozen=True)
class OperationExecutionResult:
    run: OperationRun
    reused_existing: bool = False


def _dedupe_key(*, profile: str, fabric_id: int | None, parameters: dict[str, Any]) -> str:
    payload = {
        'profile': profile,
        'fabric_id': fabric_id,
        'parameters': parameters,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def execute_operation_profile(
    *,
    profile: str,
    fabric: Fabric | None = None,
    parameters: dict[str, Any] | None = None,
    actor=None,
) -> OperationExecutionResult:
    parameters = parameters or {}
    key = _dedupe_key(profile=profile, fabric_id=getattr(fabric, 'pk', None), parameters=parameters)
    existing = OperationRun.objects.filter(profile=profile, dedupe_key=key, status='completed').first()
    if existing is not None:
        return OperationExecutionResult(run=existing, reused_existing=True)

    run = OperationRun.objects.create(
        profile=profile,
        status='running',
        fabric=fabric,
        initiated_by=actor,
        dedupe_key=key,
        parameters=parameters,
        started_at=timezone.now(),
    )
    try:
        if profile not in {'generic_roce', 'madison_default'}:
            raise ValueError(f'Unsupported operation profile: {profile!r}')

        target_fabric = fabric
        if target_fabric is None:
            target_fabric = Fabric.objects.order_by('name', 'pk').first()
        if target_fabric is None:
            raise ValueError('No fabric is available for operation execution.')

        send_lanes = list(
            OpticalLane.objects.filter(fabric=target_fabric, direction='send')
            .select_related('endpoint', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
            .order_by('pk')
        )
        paths_resolved = 0
        paths_failed = 0
        sample_errors: list[str] = []
        for lane in send_lanes:
            resolved = resolve_optical_lane_path(source=lane)
            if resolved.path_found:
                paths_resolved += 1
            else:
                paths_failed += 1
                if resolved.error:
                    sample_errors.append(resolved.error)

        result = {
            'fabric_id': target_fabric.pk,
            'lane_count': len(send_lanes),
            'paths_resolved': paths_resolved,
            'paths_failed': paths_failed,
            'profile': profile,
            'sample_errors': sample_errors[:10],
            'mode': 'madison' if profile == 'madison_default' else 'generic',
        }
        run.fabric = target_fabric
        run.status = 'completed'
        run.result = result
        run.completed_at = timezone.now()
        run.save(update_fields=['fabric', 'status', 'result', 'completed_at', 'last_updated'])
        record_audit_event(
            event_type='operation_run',
            fabric=target_fabric,
            actor=actor,
            subject=run,
            outcome='ok',
            message=f'Operation profile {profile} completed.',
            payload=result,
        )
    except Exception as exc:
        run.status = 'failed'
        run.error_detail = str(exc)
        run.completed_at = timezone.now()
        run.save(update_fields=['status', 'error_detail', 'completed_at', 'last_updated'])
        record_audit_event(
            event_type='operation_run',
            fabric=fabric,
            actor=actor,
            subject=run,
            outcome='failed',
            message=f'Operation profile {profile} failed: {exc}',
            payload={'error': str(exc)},
        )
        raise

    return OperationExecutionResult(run=run, reused_existing=False)
