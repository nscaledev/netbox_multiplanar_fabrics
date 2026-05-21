from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from django.db.models import Count
from django.utils import timezone

from netbox_plant_graph.models import (
    AuditEvent,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FiberStrand,
    OperationRun,
    OpticalLane,
    Plane,
    StampRun,
    StampTemplate,
    SuppressionRule,
    TransferMap,
)
from netbox_plant_graph.services.audit import (
    FINDING_STATUS_ACKNOWLEDGED,
    FINDING_STATUS_IN_PROGRESS,
    FINDING_STATUS_OPEN,
    FINDING_STATUS_RESOLVED,
    FINDING_STATUS_SUPPRESSED,
    get_audit_finding,
    list_audit_findings,
)
from netbox_plant_graph.services.plan_execution import build_deployment_workflow_summary
from netbox_plant_graph.services.resolver import (
    build_lane_drilldown_report,
    compare_optical_lane_paths,
    compute_unavailability_blast_radius,
)
from netbox_plant_graph.services.stamp_preview import build_v2_stamp_template_preview


ACTIVE_FINDING_STATUSES = frozenset(
    {
        FINDING_STATUS_OPEN,
        FINDING_STATUS_ACKNOWLEDGED,
        FINDING_STATUS_IN_PROGRESS,
        FINDING_STATUS_SUPPRESSED,
    }
)

_SEVERITY_RANK = {
    'error': 30,
    'warning': 20,
    'info': 10,
}


def _as_int(value, *, default: int | None = None) -> int | None:
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def _clamp(value: int, *, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


def _reference(obj) -> dict[str, Any] | None:
    if obj is None:
        return None
    meta = getattr(obj, '_meta', None)
    model_name = getattr(meta, 'model_name', obj.__class__.__name__.lower())
    return {
        'object_type': model_name,
        'object_id': getattr(obj, 'pk', None),
        'display': str(obj),
        'url': obj.get_absolute_url() if hasattr(obj, 'get_absolute_url') else None,
    }


def _lane_payload(lane: OpticalLane | None) -> dict[str, Any] | None:
    if lane is None:
        return None
    return {
        'id': lane.pk,
        'fabric_id': lane.fabric_id,
        'endpoint_id': lane.endpoint_id,
        'plane_id': lane.plane_id,
        'lane_index': lane.lane_index,
        'local_mpo_index': lane.local_mpo_index,
        'local_mpo_endpoint_id': lane.local_mpo_endpoint_id,
        'local_mpo_position_id': lane.local_mpo_position_id,
        'direction': lane.direction,
        'wavelength_nm': str(lane.wavelength_nm),
        'pair_key': lane.pair_key,
        'nominal_rate_gbps': lane.nominal_rate_gbps,
        'endpoint_label': str(lane.endpoint),
    }


def _serialize_path(path) -> dict[str, Any]:
    return {
        'path_found': path.path_found,
        'source_lane_id': path.source_lane_id,
        'destination_lane_id': path.destination_lane_id,
        'error': path.error,
        'steps': [
            {
                'step_type': step.step_type,
                'object_type': step.object_type,
                'object_id': step.object_id,
                'label': step.label,
                'metadata': dict(step.metadata or {}),
            }
            for step in path.steps
        ],
    }


def _serialize_suppression(detail) -> dict[str, Any]:
    return {
        'object_type': detail.object_type,
        'object_id': detail.object_id,
        'suppressed': bool(detail.suppressed),
        'reason': detail.reason,
        'suppression_rule_id': detail.suppression_rule_id,
    }


def _severity_name_for_rank(rank: int) -> str:
    if rank >= _SEVERITY_RANK['error']:
        return 'error'
    if rank >= _SEVERITY_RANK['warning']:
        return 'warning'
    return 'info'


_REGISTRY_MODEL_MAP = {
    'opticallane': OpticalLane,
    'optical_lane': OpticalLane,
    'signal_lane': OpticalLane,
    'signallane': OpticalLane,
    'lane': OpticalLane,
    'endpoint': Endpoint,
    'plane': Plane,
    'fabric': Fabric,
    'connector_position': ConnectorPosition,
    'connectorposition': ConnectorPosition,
    'fiber_strand': FiberStrand,
    'fiberstrand': FiberStrand,
    'transfer_map': TransferMap,
    'transfermap': TransferMap,
}


def resolve_lane_analysis_target(*, registry_key: str | None, object_id) -> Any | None:
    normalized_key = (registry_key or '').strip().lower()
    model = _REGISTRY_MODEL_MAP.get(normalized_key)
    object_pk = _as_int(object_id)
    if model is None or object_pk is None:
        return None
    return model.objects.filter(pk=object_pk).first()


def _lanes_for_target(target):
    queryset = OpticalLane.objects.select_related(
        'fabric',
        'endpoint',
        'endpoint__node',
        'plane',
        'local_mpo_endpoint',
        'local_mpo_position',
    ).order_by('endpoint__address', 'lane_index', 'direction', 'pk')
    if isinstance(target, OpticalLane):
        return queryset.filter(pk=target.pk)
    if isinstance(target, Endpoint):
        return queryset.filter(endpoint_id=target.pk)
    if isinstance(target, Plane):
        return queryset.filter(plane_id=target.pk)
    if isinstance(target, Fabric):
        return queryset.filter(fabric_id=target.pk)
    if isinstance(target, ConnectorPosition):
        return queryset.filter(local_mpo_position_id=target.pk)
    fabric_id = getattr(target, 'fabric_id', None)
    if fabric_id is not None:
        return queryset.filter(fabric_id=fabric_id)
    return queryset.none()


def _rule_for_finding(rule: SuppressionRule, *, finding_id: int) -> bool:
    metadata = dict(rule.metadata or {})
    return _as_int(metadata.get('finding_pk')) == finding_id


def _domain_plane_ids_from_finding(finding) -> tuple[int, ...]:
    plane_ids = set()
    if finding.plane_id is not None:
        plane_ids.add(int(finding.plane_id))
    payload = dict(finding.payload or {})
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    lifecycle = dict(finding.lifecycle or {})
    for raw in (
        payload.get('plane_id'),
        metadata.get('plane_id'),
        lifecycle.get('plane_id'),
    ):
        parsed = _as_int(raw)
        if parsed is not None:
            plane_ids.add(parsed)
    for raw_list in (
        payload.get('plane_ids'),
        metadata.get('plane_ids'),
        lifecycle.get('plane_ids'),
    ):
        if not isinstance(raw_list, (list, tuple)):
            continue
        for raw in raw_list:
            parsed = _as_int(raw)
            if parsed is not None:
                plane_ids.add(parsed)
    return tuple(sorted(plane_ids))


def _domain_key_from_finding(finding) -> str:
    payload = dict(finding.payload or {})
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    lifecycle = dict(finding.lifecycle or {})
    for source in (lifecycle, metadata, payload, finding.metadata):
        if not isinstance(source, dict):
            continue
        key = source.get('contamination_domain_key')
        if key not in (None, ''):
            return str(key)
    plane_ids = _domain_plane_ids_from_finding(finding)
    if plane_ids:
        return f"planes:{','.join(str(plane_id) for plane_id in plane_ids)}"
    return f'fabric:{finding.fabric_id or 0}:global'


def _serialize_audit_finding(finding) -> dict[str, Any]:
    return {
        'id': finding.finding_id,
        'fabric_id': finding.fabric_id,
        'plane_id': finding.plane_id,
        'finding_type': finding.finding_type,
        'severity': finding.severity,
        'status': finding.status,
        'suppressed': finding.suppressed,
        'message': finding.message,
        'first_seen_at': finding.first_seen_at,
        'last_seen_at': finding.last_seen_at,
        'created': _iso(getattr(finding.event, 'created', None)),
        'metadata': dict(finding.metadata or {}),
        'payload': dict(finding.payload or {}),
        'lifecycle': dict(finding.lifecycle or {}),
    }


def _serialize_audit_event(event: AuditEvent) -> dict[str, Any]:
    return {
        'id': event.pk,
        'event_type': event.event_type,
        'fabric_id': event.fabric_id,
        'subject_type_id': event.subject_type_id,
        'subject_id': event.subject_id,
        'actor_id': event.actor_id,
        'outcome': event.outcome,
        'message': event.message,
        'created': _iso(event.created),
        'payload': dict(event.payload or {}),
        'metadata': dict(event.metadata or {}),
    }


def _serialize_run_event(*, run_type: str, run) -> dict[str, Any]:
    base = {
        'run_type': run_type,
        'run_id': run.pk,
        'fabric_id': run.fabric_id,
        'status': run.status,
        'created': _iso(run.created),
        'updated': _iso(run.last_updated),
    }
    if run_type == 'operation':
        base.update(
            {
                'profile': run.profile,
                'started_at': _iso(run.started_at),
                'completed_at': _iso(run.completed_at),
                'initiated_by_id': run.initiated_by_id,
                'result': dict(run.result or {}),
            }
        )
    else:
        base.update(
            {
                'template_id': run.template_id,
                'started_at': _iso(run.created),
                'completed_at': _iso(run.created if run.status == 'completed' else None),
                'error_detail': run.error_detail,
                'result': dict(run.result or {}),
            }
        )
    return base


def build_lane_drilldown_payload(
    *,
    source_lane_id=None,
    destination_lane_id=None,
    target_registry_key: str | None = None,
    target_id=None,
    lane_index: int | None = None,
    max_depth: int = 64,
) -> dict[str, Any]:
    source_lane_pk = _as_int(source_lane_id)
    if source_lane_pk is not None:
        source_lane = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'plane',
            'local_mpo_endpoint',
            'local_mpo_position',
        ).filter(pk=source_lane_pk).first()
        if source_lane is None:
            return {'error': f'Source lane {source_lane_pk} was not found.'}
        destination_lane = None
        destination_lane_pk = _as_int(destination_lane_id)
        if destination_lane_pk is not None:
            destination_lane = OpticalLane.objects.filter(pk=destination_lane_pk).first()
            if destination_lane is None:
                return {'error': f'Destination lane {destination_lane_pk} was not found.'}
        report = build_lane_drilldown_report(
            source=source_lane,
            destination=destination_lane,
            max_depth=max_depth,
        )
        return {
            'mode': 'path',
            'source': report.source,
            'requested_destination': report.requested_destination,
            'resolved_destination': report.resolved_destination,
            'resolved_path': _serialize_path(report.resolved_path),
            'lane_suppression': _serialize_suppression(report.lane_suppression),
            'hop_suppressions': tuple(_serialize_suppression(item) for item in report.hop_suppressions),
            'blocking_hop_suppression': (
                _serialize_suppression(report.blocking_hop_suppression)
                if report.blocking_hop_suppression is not None
                else None
            ),
        }

    target = resolve_lane_analysis_target(registry_key=target_registry_key, object_id=target_id)
    if target is None:
        return {'error': 'Select a valid lane-analysis target or source lane.'}

    lanes = list(_lanes_for_target(target))
    available_lane_indexes = sorted({lane.lane_index for lane in lanes})
    selected_lane_index = _as_int(lane_index)
    if selected_lane_index is not None:
        lanes = [lane for lane in lanes if lane.lane_index == selected_lane_index]

    grouped: dict[int, dict[str, Any]] = {}
    for lane in lanes:
        row = grouped.setdefault(
            lane.endpoint_id,
            {
                'attachment_unit': _reference(lane.endpoint),
                'lanes': [],
            },
        )
        row['lanes'].append(_lane_payload(lane))

    attachment_units = sorted(
        (
            {
                'attachment_unit': row['attachment_unit'],
                'lane_count': len(row['lanes']),
                'lanes': tuple(row['lanes']),
            }
            for row in grouped.values()
        ),
        key=lambda item: item['attachment_unit']['display'],
    )
    return {
        'mode': 'inventory',
        'target': _reference(target),
        'lane_index': selected_lane_index,
        'available_lane_indexes': available_lane_indexes,
        'attachment_units': tuple(attachment_units),
        'total_attachment_units': len(attachment_units),
        'total_signal_lanes': sum(unit['lane_count'] for unit in attachment_units),
    }


def build_lane_compare_payload(
    *,
    baseline_source_lane_id,
    candidate_source_lane_id,
    max_depth: int = 64,
) -> dict[str, Any]:
    baseline_id = _as_int(baseline_source_lane_id)
    candidate_id = _as_int(candidate_source_lane_id)
    if baseline_id is None or candidate_id is None:
        return {'error': 'Both baseline_source_lane_id and candidate_source_lane_id are required.'}

    baseline_lane = OpticalLane.objects.filter(pk=baseline_id).first()
    if baseline_lane is None:
        return {'error': f'Baseline source lane {baseline_id} was not found.'}
    candidate_lane = OpticalLane.objects.filter(pk=candidate_id).first()
    if candidate_lane is None:
        return {'error': f'Candidate source lane {candidate_id} was not found.'}

    report = compare_optical_lane_paths(
        baseline=baseline_lane,
        candidate=candidate_lane,
        max_depth=max_depth,
    )
    return {
        'baseline': report.baseline,
        'candidate': report.candidate,
        'baseline_path': _serialize_path(report.baseline_path),
        'candidate_path': _serialize_path(report.candidate_path),
        'checks': dict(report.checks or {}),
        'deltas': dict(report.deltas or {}),
    }


def build_blast_radius_payload(
    *,
    target_registry_key: str | None,
    target_id,
    resolution: str = 'attachment_unit',
    max_depth: int = 64,
) -> dict[str, Any]:
    target = resolve_lane_analysis_target(registry_key=target_registry_key, object_id=target_id)
    if target is None:
        return {'error': 'Select a valid blast-radius target.'}

    if isinstance(target, (OpticalLane, ConnectorPosition, FiberStrand, TransferMap)):
        report = compute_unavailability_blast_radius(unavailable=target, max_depth=max_depth)
        return {
            'mode': 'path_impact',
            'unavailable': dict(report.unavailable or {}),
            'impacted_lane_count': len(report.impacted_lanes),
            'impacted_lanes': tuple(dict(row or {}) for row in report.impacted_lanes),
            'impacted_entities': dict(report.impacted_entities or {}),
            'impacts': tuple(
                {
                    'source_lane': dict(impact.source_lane or {}),
                    'destination_lane': dict(impact.destination_lane or {}) if impact.destination_lane else None,
                    'resolved_path': _serialize_path(impact.resolved_path),
                    'impacted': impact.impacted,
                    'reason': impact.reason,
                }
                for impact in report.impacts
            ),
        }

    lanes = list(_lanes_for_target(target)[:1000])
    impacted_lanes = {_lane_payload(lane)['id']: _lane_payload(lane) for lane in lanes if _lane_payload(lane)}
    if resolution == 'signal_lane':
        pair_keys = {lane.pair_key for lane in lanes if lane.pair_key}
        target_fabric_id = getattr(target, 'fabric_id', None) or (
            target.pk if isinstance(target, Fabric) else None
        )
        if pair_keys:
            peer_lanes = OpticalLane.objects.filter(
                fabric_id=target_fabric_id,
                pair_key__in=pair_keys,
            ).exclude(pk__in=impacted_lanes.keys()).select_related('endpoint', 'plane')
            for lane in peer_lanes[:1000]:
                payload = _lane_payload(lane)
                if payload is not None:
                    impacted_lanes[payload['id']] = payload
    return {
        'mode': 'scope_impact',
        'target': _reference(target),
        'resolution': resolution,
        'impacted_lane_count': len(impacted_lanes),
        'impacted_lanes': tuple(
            impacted_lanes[lane_id]
            for lane_id in sorted(impacted_lanes)
        ),
    }


def build_audit_workflow_summary_payload(*, fabric_id=None) -> dict[str, Any]:
    selected_fabric_id = _as_int(fabric_id)
    findings = list_audit_findings(fabric=selected_fabric_id)
    now = timezone.now()
    active_findings = [row for row in findings if row.status in ACTIVE_FINDING_STATUSES]

    status_counts = Counter(row.status for row in active_findings)
    severity_counts = Counter(row.severity for row in active_findings)
    type_counts = Counter(row.finding_type for row in active_findings)
    stale_7d = sum(
        1
        for row in active_findings
        if row.event.created and row.event.created <= now - timedelta(days=7)
    )
    stale_30d = sum(
        1
        for row in active_findings
        if row.event.created and row.event.created <= now - timedelta(days=30)
    )

    operation_runs = OperationRun.objects.order_by('-created', '-pk')
    if selected_fabric_id is not None:
        operation_runs = operation_runs.filter(fabric_id=selected_fabric_id)
    recent_runs = [
        {
            'id': run.pk,
            'profile': run.profile,
            'status': run.status,
            'fabric_id': run.fabric_id,
            'started_at': _iso(run.started_at),
            'completed_at': _iso(run.completed_at),
            'initiated_by_id': run.initiated_by_id,
            'result': dict(run.result or {}),
        }
        for run in operation_runs[:10]
    ]

    recent_events = AuditEvent.objects.order_by('-created', '-pk')
    if selected_fabric_id is not None:
        recent_events = recent_events.filter(fabric_id=selected_fabric_id)

    expiring_suppressions = SuppressionRule.objects.filter(
        status='active',
        revoked_at__isnull=True,
        expires_at__isnull=False,
    ).order_by('expires_at', 'pk')
    if selected_fabric_id is not None:
        expiring_suppressions = expiring_suppressions.filter(fabric_id=selected_fabric_id)

    oldest_active = sorted(
        active_findings,
        key=lambda row: (row.event.created or timezone.now(), row.finding_id),
    )[:10]

    return {
        'fabric_id': selected_fabric_id,
        'total_findings': len(findings),
        'active_findings': len(active_findings),
        'resolved_findings': sum(1 for row in findings if row.status == FINDING_STATUS_RESOLVED),
        'suppressed_findings': sum(1 for row in findings if row.status == FINDING_STATUS_SUPPRESSED),
        'stale_findings_7d': stale_7d,
        'stale_findings_30d': stale_30d,
        'status_counts': tuple(
            {'name': key, 'value': value}
            for key, value in sorted(status_counts.items())
        ),
        'severity_counts': tuple(
            {'name': key, 'value': value}
            for key, value in sorted(severity_counts.items())
        ),
        'type_counts': tuple(
            {'name': key, 'value': value}
            for key, value in sorted(type_counts.items())
        ),
        'recent_runs': tuple(recent_runs),
        'recent_events': tuple(_serialize_audit_event(event) for event in recent_events[:15]),
        'oldest_active_findings': tuple(_serialize_audit_finding(row) for row in oldest_active),
        'expiring_suppressions': tuple(
            {
                'id': rule.pk,
                'fabric_id': rule.fabric_id,
                'policy_key': rule.policy_key,
                'reason': rule.reason,
                'expires_at': _iso(rule.expires_at),
                'remaining_days': (rule.expires_at.date() - now.date()).days,
            }
            for rule in expiring_suppressions[:10]
        ),
    }


def build_audit_finding_search_payload(
    *,
    fabric_id=None,
    plane_id=None,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    suppressed: bool | str | None = None,
    search: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    findings = list_audit_findings(
        fabric=_as_int(fabric_id),
        plane=_as_int(plane_id),
        status=status,
        severity=severity,
        finding_type=finding_type,
        suppressed=suppressed,
    )
    search_term = (search or '').strip().lower()
    if search_term:
        findings = tuple(
            row
            for row in findings
            if search_term in (row.message or '').lower()
            or search_term in row.finding_type.lower()
        )
    clamped_limit = _clamp(limit, lower=1, upper=2000)
    return [
        _serialize_audit_finding(row)
        for row in findings[:clamped_limit]
    ]


def build_audit_finding_detail_payload(*, finding_id) -> dict[str, Any]:
    selected_finding_id = _as_int(finding_id)
    if selected_finding_id is None:
        return {'error': 'finding_id is required.'}
    try:
        finding = get_audit_finding(finding=selected_finding_id)
    except AuditEvent.DoesNotExist:
        return {'error': f'Finding {selected_finding_id} was not found.'}

    timeline_events = AuditEvent.objects.filter(
        fabric_id=finding.fabric_id,
    ).order_by('-created', '-pk')
    timeline = []
    for event in timeline_events[:500]:
        payload = dict(event.payload or {})
        metadata = dict(event.metadata or {})
        if event.pk == finding.finding_id:
            timeline.append(event)
            continue
        if _as_int(payload.get('finding_id')) == finding.finding_id:
            timeline.append(event)
            continue
        if _as_int(metadata.get('finding_pk')) == finding.finding_id:
            timeline.append(event)
            continue

    active_suppressions = SuppressionRule.objects.filter(
        fabric_id=finding.fabric_id,
        status='active',
        revoked_at__isnull=True,
    ).order_by('-created', '-pk')
    suppression_rows = [
        {
            'id': rule.pk,
            'policy_key': rule.policy_key,
            'reason': rule.reason,
            'plane_id': rule.plane_id,
            'optical_lane_id': rule.optical_lane_id,
            'expires_at': _iso(rule.expires_at),
        }
        for rule in active_suppressions
        if _rule_for_finding(rule, finding_id=finding.finding_id)
    ]

    return {
        'finding': _serialize_audit_finding(finding),
        'timeline': tuple(_serialize_audit_event(event) for event in timeline[:50]),
        'active_suppressions': tuple(suppression_rows),
        'timeline_event_count': len(timeline),
    }


def build_audit_run_timeline_payload(*, fabric_id=None, limit: int = 20) -> list[dict[str, Any]]:
    selected_fabric_id = _as_int(fabric_id)
    clamped_limit = _clamp(limit, lower=1, upper=500)

    operation_qs = OperationRun.objects.order_by('-created', '-pk')
    stamp_qs = StampRun.objects.order_by('-created', '-pk')
    if selected_fabric_id is not None:
        operation_qs = operation_qs.filter(fabric_id=selected_fabric_id)
        stamp_qs = stamp_qs.filter(fabric_id=selected_fabric_id)

    rows = []
    for run in operation_qs[:clamped_limit]:
        rows.append(_serialize_run_event(run_type='operation', run=run))
    for run in stamp_qs[:clamped_limit]:
        rows.append(_serialize_run_event(run_type='stamp', run=run))
    rows.sort(key=lambda row: (row['created'] or '', row['run_type'], row['run_id']), reverse=True)
    return rows[:clamped_limit]


def build_contamination_domain_payloads(
    *,
    fabric_id=None,
    plane_id=None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    findings = list_audit_findings(
        fabric=_as_int(fabric_id),
        plane=_as_int(plane_id),
    )
    domains: dict[str, dict[str, Any]] = {}
    for finding in findings:
        domain_key = _domain_key_from_finding(finding)
        domain = domains.setdefault(
            domain_key,
            {
                'domain_key': domain_key,
                'plane_ids': set(),
                'finding_ids': [],
                'finding_count': 0,
                'active_finding_count': 0,
                'resolved_finding_count': 0,
                'suppressed_finding_count': 0,
                'highest_severity_rank': 0,
            },
        )
        domain['plane_ids'].update(_domain_plane_ids_from_finding(finding))
        domain['finding_ids'].append(finding.finding_id)
        domain['finding_count'] += 1
        if finding.status in ACTIVE_FINDING_STATUSES:
            domain['active_finding_count'] += 1
        if finding.status == FINDING_STATUS_RESOLVED:
            domain['resolved_finding_count'] += 1
        if finding.status == FINDING_STATUS_SUPPRESSED:
            domain['suppressed_finding_count'] += 1
        domain['highest_severity_rank'] = max(
            domain['highest_severity_rank'],
            _SEVERITY_RANK.get(finding.severity.lower(), 0),
        )

    rows = []
    for domain in domains.values():
        rows.append(
            {
                'domain_key': domain['domain_key'],
                'plane_ids': tuple(sorted(domain['plane_ids'])),
                'finding_count': domain['finding_count'],
                'active_finding_count': domain['active_finding_count'],
                'resolved_finding_count': domain['resolved_finding_count'],
                'suppressed_finding_count': domain['suppressed_finding_count'],
                'highest_severity': _severity_name_for_rank(domain['highest_severity_rank']),
                'sample_finding_ids': tuple(domain['finding_ids'][:10]),
            }
        )
    rows.sort(
        key=lambda row: (
            row['active_finding_count'],
            row['finding_count'],
            row['domain_key'],
        ),
        reverse=True,
    )
    return rows[: _clamp(limit, lower=1, upper=500)]


def build_policy_summary_payload(*, fabric_id=None, plane_id=None) -> dict[str, Any]:
    selected_fabric_id = _as_int(fabric_id)
    selected_plane_id = _as_int(plane_id)
    findings = list_audit_findings(
        fabric=selected_fabric_id,
        plane=selected_plane_id,
        finding_type=None,
    )
    rules = SuppressionRule.objects.order_by('-created', '-pk')
    if selected_fabric_id is not None:
        rules = rules.filter(fabric_id=selected_fabric_id)
    if selected_plane_id is not None:
        rules = rules.filter(plane_id=selected_plane_id)

    active_rules = [rule for rule in rules if rule.is_effective]
    domains = build_contamination_domain_payloads(
        fabric_id=selected_fabric_id,
        plane_id=selected_plane_id,
        limit=500,
    )
    status_counts = Counter(row.status for row in findings)
    disjoint_rules = [
        rule
        for rule in rules
        if (rule.policy_key in {'disjointness', 'disjointness_exception'})
        or (dict(rule.metadata or {}).get('kind') == 'disjointness_exception')
    ]
    active_disjoint_rules = [rule for rule in disjoint_rules if rule.is_effective]
    uncovered_domain_count = max(0, len(domains) - len(active_disjoint_rules))

    return {
        'fabric_id': selected_fabric_id,
        'plane_id': selected_plane_id,
        'finding_count': len(findings),
        'active_finding_count': sum(1 for row in findings if row.status in ACTIVE_FINDING_STATUSES),
        'resolved_finding_count': sum(1 for row in findings if row.status == FINDING_STATUS_RESOLVED),
        'suppressed_finding_count': sum(1 for row in findings if row.status == FINDING_STATUS_SUPPRESSED),
        'finding_status_counts': tuple(
            {'name': key, 'value': value}
            for key, value in sorted(status_counts.items())
        ),
        'rule_count': rules.count(),
        'active_rule_count': len(active_rules),
        'disjointness_exception_count': len(disjoint_rules),
        'active_disjointness_exception_count': len(active_disjoint_rules),
        'contamination_domain_count': len(domains),
        'uncovered_domain_count': uncovered_domain_count,
    }


def build_policy_dashboard_payload(
    *,
    fabric_id=None,
    plane_id=None,
    domain_limit: int = 10,
    finding_limit: int = 10,
) -> dict[str, Any]:
    selected_fabric_id = _as_int(fabric_id)
    selected_plane_id = _as_int(plane_id)
    summary = build_policy_summary_payload(
        fabric_id=selected_fabric_id,
        plane_id=selected_plane_id,
    )
    domains = build_contamination_domain_payloads(
        fabric_id=selected_fabric_id,
        plane_id=selected_plane_id,
        limit=domain_limit,
    )
    rules = SuppressionRule.objects.exclude(policy_key='').order_by('policy_key', 'status')
    if selected_fabric_id is not None:
        rules = rules.filter(fabric_id=selected_fabric_id)
    if selected_plane_id is not None:
        rules = rules.filter(plane_id=selected_plane_id)

    findings = list_audit_findings(
        fabric=selected_fabric_id,
        plane=selected_plane_id,
    )
    oldest_active = sorted(
        [row for row in findings if row.status in ACTIVE_FINDING_STATUSES],
        key=lambda row: (row.event.created or timezone.now(), row.finding_id),
    )[: _clamp(finding_limit, lower=1, upper=100)]

    policy_groups = (
        rules.values('policy_key', 'status')
        .annotate(total=Count('id'))
        .order_by('policy_key', 'status')
    )

    return {
        'summary': summary,
        'domains': tuple(domains),
        'policy_groups': tuple(
            {
                'policy_key': row['policy_key'],
                'status': row['status'],
                'count': row['total'],
            }
            for row in policy_groups
        ),
        'oldest_active_findings': tuple(_serialize_audit_finding(row) for row in oldest_active),
    }


def build_deployment_workflow_summary_payload(*, fabric_id=None, template_id=None, limit: int = 20) -> dict[str, Any]:
    summary = build_deployment_workflow_summary(
        fabric=_as_int(fabric_id),
        template=_as_int(template_id),
        limit=_clamp(limit, lower=1, upper=200),
    )
    return {
        'total_runs': summary.total_runs,
        'rollback_eligible_run_ids': summary.rollback_eligible_run_ids,
        'rollback_applied_run_ids': summary.rollback_applied_run_ids,
        'recent_runs': tuple(
            {
                'id': row.stamp_run.pk,
                'fabric_id': row.stamp_run.fabric_id,
                'template_id': row.stamp_run.template_id,
                'status': row.stamp_run.status,
                'created': _iso(row.stamp_run.created),
                'managed_object_total': row.managed_object_total,
                'rollback_applied': row.rollback_applied,
                'rollback_eligible': row.rollback_eligible,
                'rollback_mode_hint': row.rollback_mode_hint,
            }
            for row in summary.recent_runs
        ),
    }


def build_stamp_template_preview_payload(
    *,
    template_id,
    fabric_name: str | None = None,
    fabric_slug: str | None = None,
) -> dict[str, Any]:
    selected_template_id = _as_int(template_id)
    if selected_template_id is None:
        return {'error': 'template_id is required.'}
    template = StampTemplate.objects.filter(pk=selected_template_id).first()
    if template is None:
        return {'error': f'Stamp template {selected_template_id} was not found.'}

    timestamp_suffix = timezone.now().strftime('%Y%m%d%H%M%S')
    preview = build_v2_stamp_template_preview(
        template,
        {
            'fabric_name': (fabric_name or f'{template.name} Preview {timestamp_suffix}')[:200],
            'fabric_slug': (fabric_slug or f'{template.slug}-preview-{timestamp_suffix}')[:200],
        },
    )
    return preview


__all__ = [
    'build_audit_finding_detail_payload',
    'build_audit_finding_search_payload',
    'build_audit_run_timeline_payload',
    'build_audit_workflow_summary_payload',
    'build_blast_radius_payload',
    'build_contamination_domain_payloads',
    'build_deployment_workflow_summary_payload',
    'build_lane_compare_payload',
    'build_lane_drilldown_payload',
    'build_policy_dashboard_payload',
    'build_policy_summary_payload',
    'build_stamp_template_preview_payload',
]
