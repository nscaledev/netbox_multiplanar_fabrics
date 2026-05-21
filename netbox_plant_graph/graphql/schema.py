from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from netbox_plant_graph.graphql.operational_types import (
    build_audit_finding_detail_payload,
    build_audit_finding_search_payload,
    build_audit_run_timeline_payload,
    build_audit_workflow_summary_payload,
    build_blast_radius_payload,
    build_contamination_domain_payloads,
    build_deployment_workflow_summary_payload,
    build_lane_compare_payload,
    build_lane_drilldown_payload,
    build_policy_dashboard_payload,
    build_policy_summary_payload,
    build_stamp_template_preview_payload,
)
from netbox_plant_graph.models import AuditEvent, Fabric, FabricArchitecture, OperationRun, OpticalLane, StampRun, SuppressionRule
from netbox_plant_graph.services.resolver import resolve_optical_lane_path


GRAPHQL_CONTRACT_VERSION = '2.0.0'


def _v2_status() -> JSON:
    return {
        'status': 'v2_kernel',
        'architecture_count': FabricArchitecture.objects.count(),
        'fabric_count': Fabric.objects.count(),
    }


def _optical_lane_path(source_id: strawberry.ID, destination_id: strawberry.ID | None = None) -> JSON:
    source = OpticalLane.objects.get(pk=source_id)
    destination = OpticalLane.objects.get(pk=destination_id) if destination_id is not None else None
    path = resolve_optical_lane_path(source=source, destination=destination)
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
                'metadata': step.metadata,
            }
            for step in path.steps
        ],
    }


def _fabrics() -> list[JSON]:
    return [
        {
            'id': fabric.pk,
            'name': fabric.name,
            'slug': fabric.slug,
            'status': fabric.status,
        }
        for fabric in Fabric.objects.order_by('name', 'pk')
    ]


def _optical_lanes(
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    direction: str | None = None,
) -> list[JSON]:
    queryset = OpticalLane.objects.select_related('fabric', 'endpoint', 'plane').order_by('pk')
    if fabric_id is not None:
        queryset = queryset.filter(fabric_id=fabric_id)
    if plane_id is not None:
        queryset = queryset.filter(plane_id=plane_id)
    if direction in {'send', 'receive'}:
        queryset = queryset.filter(direction=direction)
    return [
        {
            'id': lane.pk,
            'fabric_id': lane.fabric_id,
            'endpoint_id': lane.endpoint_id,
            'plane_id': lane.plane_id,
            'lane_index': lane.lane_index,
            'direction': lane.direction,
            'wavelength_nm': str(lane.wavelength_nm),
            'pair_key': lane.pair_key,
        }
        for lane in queryset[:2000]
    ]


def _stamp_runs(fabric_id: strawberry.ID | None = None) -> list[JSON]:
    queryset = StampRun.objects.select_related('fabric', 'template').order_by('-created', '-pk')
    if fabric_id is not None:
        queryset = queryset.filter(fabric_id=fabric_id)
    return [
        {
            'id': run.pk,
            'fabric_id': run.fabric_id,
            'template_id': run.template_id,
            'status': run.status,
            'created': run.created.isoformat() if run.created else None,
        }
        for run in queryset[:500]
    ]


def _suppression_rules(fabric_id: strawberry.ID | None = None, active_only: bool = False) -> list[JSON]:
    queryset = SuppressionRule.objects.select_related('fabric', 'plane', 'optical_lane').order_by('-created', '-pk')
    if fabric_id is not None:
        queryset = queryset.filter(fabric_id=fabric_id)
    if active_only:
        queryset = queryset.filter(status='active', revoked_at__isnull=True)
    return [
        {
            'id': rule.pk,
            'fabric_id': rule.fabric_id,
            'plane_id': rule.plane_id,
            'optical_lane_id': rule.optical_lane_id,
            'policy_key': rule.policy_key,
            'status': rule.status,
            'expires_at': rule.expires_at.isoformat() if rule.expires_at else None,
            'revoked_at': rule.revoked_at.isoformat() if rule.revoked_at else None,
        }
        for rule in queryset[:2000]
    ]


def _audit_events(fabric_id: strawberry.ID | None = None, limit: int = 250) -> list[JSON]:
    queryset = AuditEvent.objects.select_related('fabric', 'actor').order_by('-created', '-pk')
    if fabric_id is not None:
        queryset = queryset.filter(fabric_id=fabric_id)
    clamped_limit = max(1, min(limit, 2000))
    return [
        {
            'id': event.pk,
            'fabric_id': event.fabric_id,
            'event_type': event.event_type,
            'outcome': event.outcome,
            'actor_id': event.actor_id,
            'subject_type_id': event.subject_type_id,
            'subject_id': event.subject_id,
            'created': event.created.isoformat() if event.created else None,
            'message': event.message,
        }
        for event in queryset[:clamped_limit]
    ]


def _operation_runs(fabric_id: strawberry.ID | None = None) -> list[JSON]:
    queryset = OperationRun.objects.select_related('fabric', 'initiated_by').order_by('-created', '-pk')
    if fabric_id is not None:
        queryset = queryset.filter(fabric_id=fabric_id)
    return [
        {
            'id': run.pk,
            'fabric_id': run.fabric_id,
            'profile': run.profile,
            'status': run.status,
            'initiated_by_id': run.initiated_by_id,
            'started_at': run.started_at.isoformat() if run.started_at else None,
            'completed_at': run.completed_at.isoformat() if run.completed_at else None,
        }
        for run in queryset[:500]
    ]


def _lane_drilldown(
    source_lane_id: strawberry.ID | None = None,
    destination_lane_id: strawberry.ID | None = None,
    target_registry_key: str | None = None,
    target_id: strawberry.ID | None = None,
    lane_index: int | None = None,
    max_depth: int = 64,
) -> JSON:
    return build_lane_drilldown_payload(
        source_lane_id=source_lane_id,
        destination_lane_id=destination_lane_id,
        target_registry_key=target_registry_key,
        target_id=target_id,
        lane_index=lane_index,
        max_depth=max_depth,
    )


def _lane_compare(
    baseline_source_lane_id: strawberry.ID,
    candidate_source_lane_id: strawberry.ID,
    max_depth: int = 64,
) -> JSON:
    return build_lane_compare_payload(
        baseline_source_lane_id=baseline_source_lane_id,
        candidate_source_lane_id=candidate_source_lane_id,
        max_depth=max_depth,
    )


def _blast_radius(
    target_registry_key: str,
    target_id: strawberry.ID,
    resolution: str = 'attachment_unit',
    max_depth: int = 64,
) -> JSON:
    return build_blast_radius_payload(
        target_registry_key=target_registry_key,
        target_id=target_id,
        resolution=resolution,
        max_depth=max_depth,
    )


def _audit_workflow_summary(fabric_id: strawberry.ID | None = None) -> JSON:
    return build_audit_workflow_summary_payload(fabric_id=fabric_id)


def _audit_finding_search(
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    suppressed: bool | None = None,
    search: str | None = None,
    limit: int = 25,
) -> list[JSON]:
    return build_audit_finding_search_payload(
        fabric_id=fabric_id,
        plane_id=plane_id,
        status=status,
        severity=severity,
        finding_type=finding_type,
        suppressed=suppressed,
        search=search,
        limit=limit,
    )


def _audit_finding_detail(finding_id: strawberry.ID) -> JSON:
    return build_audit_finding_detail_payload(finding_id=finding_id)


def _audit_run_timeline(fabric_id: strawberry.ID | None = None, limit: int = 20) -> list[JSON]:
    return build_audit_run_timeline_payload(fabric_id=fabric_id, limit=limit)


def _policy_summary(fabric_id: strawberry.ID | None = None, plane_id: strawberry.ID | None = None) -> JSON:
    return build_policy_summary_payload(fabric_id=fabric_id, plane_id=plane_id)


def _policy_dashboard(
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    domain_limit: int = 10,
    finding_limit: int = 10,
) -> JSON:
    return build_policy_dashboard_payload(
        fabric_id=fabric_id,
        plane_id=plane_id,
        domain_limit=domain_limit,
        finding_limit=finding_limit,
    )


def _contamination_domains(
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    limit: int = 25,
) -> list[JSON]:
    return build_contamination_domain_payloads(
        fabric_id=fabric_id,
        plane_id=plane_id,
        limit=limit,
    )


def _deployment_workflow_summary(
    fabric_id: strawberry.ID | None = None,
    template_id: strawberry.ID | None = None,
    limit: int = 20,
) -> JSON:
    return build_deployment_workflow_summary_payload(
        fabric_id=fabric_id,
        template_id=template_id,
        limit=limit,
    )


def _stamp_template_preview(
    template_id: strawberry.ID,
    fabric_name: str | None = None,
    fabric_slug: str | None = None,
) -> JSON:
    return build_stamp_template_preview_payload(
        template_id=template_id,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
    )


def _graphql_contract_version() -> str:
    return GRAPHQL_CONTRACT_VERSION


@strawberry.type(name='Query')
class NetBoxPlantGraphQuery:
    graphql_contract_version: str = strawberry.field(resolver=_graphql_contract_version)
    v2_status: JSON = strawberry.field(resolver=_v2_status)
    optical_lane_path: JSON = strawberry.field(resolver=_optical_lane_path)
    fabrics: list[JSON] = strawberry.field(resolver=_fabrics)
    optical_lanes: list[JSON] = strawberry.field(resolver=_optical_lanes)
    stamp_runs: list[JSON] = strawberry.field(resolver=_stamp_runs)
    suppression_rules: list[JSON] = strawberry.field(resolver=_suppression_rules)
    audit_events: list[JSON] = strawberry.field(resolver=_audit_events)
    operation_runs: list[JSON] = strawberry.field(resolver=_operation_runs)
    lane_drilldown: JSON = strawberry.field(resolver=_lane_drilldown)
    lane_compare: JSON = strawberry.field(resolver=_lane_compare)
    blast_radius: JSON = strawberry.field(resolver=_blast_radius)
    audit_workflow_summary: JSON = strawberry.field(resolver=_audit_workflow_summary)
    audit_finding_search: list[JSON] = strawberry.field(resolver=_audit_finding_search)
    audit_finding_detail: JSON = strawberry.field(resolver=_audit_finding_detail)
    audit_run_timeline: list[JSON] = strawberry.field(resolver=_audit_run_timeline)
    policy_summary: JSON = strawberry.field(resolver=_policy_summary)
    policy_dashboard: JSON = strawberry.field(resolver=_policy_dashboard)
    contamination_domains: list[JSON] = strawberry.field(resolver=_contamination_domains)
    deployment_workflow_summary: JSON = strawberry.field(resolver=_deployment_workflow_summary)
    stamp_template_preview: JSON = strawberry.field(resolver=_stamp_template_preview)


schema = [NetBoxPlantGraphQuery]
