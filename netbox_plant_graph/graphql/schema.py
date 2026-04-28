import strawberry
import strawberry_django
from strawberry.scalars import JSON
from strawberry.types import Info

from netbox_plant_graph.models import Fabric, FabricPlane
from netbox_plant_graph.object_registry import GRAPHQL_OBJECT_SPECS
from netbox_plant_graph.services import (
    build_audit_run_timeline,
    build_audit_workflow_summary,
    build_durable_audit_finding_detail,
    build_durable_audit_finding_search,
    build_lane_allocation_summary,
    build_lane_drilldown,
    build_lane_set,
    build_policy_dashboard,
    build_policy_summary,
    build_typed_lane_drilldown,
    compare_lane_allocations,
    compute_blast_radius,
    compute_fabric_health,
    compute_typed_fabric_health,
    list_contamination_domains,
    resolve_path,
    resolve_typed_lane_path,
    run_plane_audit,
)
from netbox_plant_graph.services.netbox.lookup import resolve_registry_object

from .operational_types import (
    FabricHealthType,
    ContaminationDomainType,
    AuditWorkflowRunType,
    AuditWorkflowSummaryType,
    AuditWorkflowFindingDetailType,
    AuditWorkflowFindingRecordType,
    LaneAllocationSummaryType,
    LaneCompareType,
    LaneDrilldownType,
    LanePathType,
    PolicyDashboardType,
    PolicySummaryType,
    LaneSetType,
    fabric_health_type,
    contamination_domain_type,
    audit_workflow_summary_type,
    audit_workflow_finding_detail_type,
    audit_workflow_finding_record_type,
    audit_workflow_run_type,
    lane_allocation_summary_type,
    lane_compare_type,
    lane_drilldown_type,
    lane_path_type,
    policy_dashboard_type,
    policy_summary_type,
    lane_set_type,
)
from .types import GRAPHQL_TYPE_CLASS_MAP


def _resolve_path_query(
    info: Info,
    source_registry_key: str,
    source_id: strawberry.ID,
    destination_registry_key: str | None = None,
    destination_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    resolution: str = 'attachment_unit',
    max_depth: int = 128,
) -> JSON:
    source = resolve_registry_object(source_registry_key, source_id)
    destination = None
    if destination_registry_key and destination_id is not None:
        destination = resolve_registry_object(destination_registry_key, destination_id)
    plane = FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None
    if source is None:
        return {'error': 'source_not_found'}
    if destination_registry_key and destination is None:
        return {'error': 'destination_not_found'}
    return resolve_path(source=source, destination=destination, plane=plane, resolution=resolution, max_depth=max_depth)


def _plane_audit_query(info: Info, fabric_id: strawberry.ID | None = None, plane_ids: list[strawberry.ID] | None = None) -> JSON:
    fabric = Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None
    plane_set = [FabricPlane.objects.get(pk=int(plane_id)) for plane_id in plane_ids or ()]
    return run_plane_audit(fabric=fabric, plane_set=plane_set)


def _blast_radius_query(
    info: Info,
    target_registry_key: str,
    target_id: strawberry.ID,
    resolution: str = 'attachment_unit',
) -> JSON:
    target = resolve_registry_object(target_registry_key, target_id)
    if target is None:
        return {'error': 'target_not_found'}
    return compute_blast_radius(target=target, resolution=resolution)


def _lane_drilldown_query(
    info: Info,
    target_registry_key: str,
    target_id: strawberry.ID,
    lane_index: int | None = None,
) -> JSON:
    target = resolve_registry_object(target_registry_key, target_id)
    if target is None:
        return {'error': 'target_not_found'}
    return build_lane_drilldown(target=target, lane_index=lane_index)


def _fabric_health_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
) -> JSON:
    fabric = Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None
    return compute_fabric_health(fabric=fabric)


def _typed_lane_drilldown_query(
    info: Info,
    target_registry_key: str,
    target_id: strawberry.ID,
    lane_index: int | None = None,
) -> LaneDrilldownType | None:
    target = resolve_registry_object(target_registry_key, target_id)
    if target is None:
        return None
    return lane_drilldown_type(build_typed_lane_drilldown(target=target, lane_index=lane_index))


def _typed_fabric_health_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
) -> FabricHealthType:
    fabric = Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None
    return fabric_health_type(compute_typed_fabric_health(fabric=fabric))


def _lane_path_query(
    info: Info,
    source_registry_key: str,
    source_id: strawberry.ID,
    source_lane_index: int | None = None,
    destination_registry_key: str | None = None,
    destination_id: strawberry.ID | None = None,
    destination_lane_index: int | None = None,
    plane_id: strawberry.ID | None = None,
    max_depth: int = 128,
) -> LanePathType | None:
    source = resolve_registry_object(source_registry_key, source_id)
    destination = None
    if destination_registry_key and destination_id is not None:
        destination = resolve_registry_object(destination_registry_key, destination_id)
    plane = FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None
    if source is None:
        return None
    if destination_registry_key and destination is None:
        return None
    return lane_path_type(resolve_typed_lane_path(
        source=source,
        source_lane_index=source_lane_index,
        destination=destination,
        destination_lane_index=destination_lane_index,
        plane=plane,
        max_depth=max_depth,
    ))


def _lane_set_query(
    info: Info,
    target_registry_key: str,
    target_id: strawberry.ID,
) -> LaneSetType | None:
    target = resolve_registry_object(target_registry_key, target_id)
    if target is None:
        return None
    return lane_set_type(build_lane_set(target))


def _lane_allocation_summary_query(
    info: Info,
    target_registry_key: str,
    target_id: strawberry.ID,
) -> LaneAllocationSummaryType | None:
    target = resolve_registry_object(target_registry_key, target_id)
    if target is None:
        return None
    return lane_allocation_summary_type(build_lane_allocation_summary(target=target))


def _lane_compare_query(
    info: Info,
    baseline_registry_key: str,
    baseline_id: strawberry.ID,
    candidate_registry_key: str,
    candidate_id: strawberry.ID,
) -> LaneCompareType | None:
    baseline_target = resolve_registry_object(baseline_registry_key, baseline_id)
    candidate_target = resolve_registry_object(candidate_registry_key, candidate_id)
    if baseline_target is None or candidate_target is None:
        return None
    return lane_compare_type(compare_lane_allocations(
        baseline_target=baseline_target,
        candidate_target=candidate_target,
    ))


def _audit_workflow_summary_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
) -> AuditWorkflowSummaryType:
    fabric = Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None
    return audit_workflow_summary_type(build_audit_workflow_summary(fabric=fabric))


def _audit_finding_search_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    active: bool | None = None,
    assigned_to_id: strawberry.ID | None = None,
    acknowledged_by_id: strawberry.ID | None = None,
    object_type_id: strawberry.ID | None = None,
    object_id: strawberry.ID | None = None,
    suppressed: bool | None = None,
    min_age_days: int | None = None,
    search: str | None = None,
    limit: int = 25,
) -> list[AuditWorkflowFindingRecordType]:
    return [
        audit_workflow_finding_record_type(item)
        for item in build_durable_audit_finding_search(
            fabric=Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None,
            plane=FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None,
            status=status,
            severity=severity,
            finding_type=finding_type,
            active=active,
            assigned_to=int(assigned_to_id) if assigned_to_id is not None else None,
            acknowledged_by=int(acknowledged_by_id) if acknowledged_by_id is not None else None,
            object_type=int(object_type_id) if object_type_id is not None else None,
            object_id=int(object_id) if object_id is not None else None,
            suppressed=suppressed,
            min_age_days=min_age_days,
            search=search,
            limit=limit,
        )
    ]


def _audit_finding_detail_query(
    info: Info,
    finding_id: strawberry.ID,
) -> AuditWorkflowFindingDetailType | None:
    payload = build_durable_audit_finding_detail(finding=int(finding_id))
    if payload is None:
        return None
    return audit_workflow_finding_detail_type(payload)


def _audit_run_timeline_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
    limit: int = 10,
) -> list[AuditWorkflowRunType]:
    return [
        audit_workflow_run_type(item)
        for item in build_audit_run_timeline(
            fabric=Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None,
            limit=limit,
        )
    ]


def _policy_summary_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
) -> PolicySummaryType | None:
    payload = build_policy_summary(
        fabric=Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None,
        plane=FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None,
    )
    if payload is None:
        return None
    return policy_summary_type(payload)


def _policy_dashboard_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
) -> PolicyDashboardType | None:
    payload = build_policy_dashboard(
        fabric=Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None,
        plane=FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None,
    )
    if payload is None:
        return None
    return policy_dashboard_type(payload)


def _contamination_domains_query(
    info: Info,
    fabric_id: strawberry.ID | None = None,
    plane_id: strawberry.ID | None = None,
) -> list[ContaminationDomainType]:
    return [
        contamination_domain_type(item)
        for item in list_contamination_domains(
            fabric=Fabric.objects.filter(pk=int(fabric_id)).first() if fabric_id is not None else None,
            plane=FabricPlane.objects.filter(pk=int(plane_id)).first() if plane_id is not None else None,
        )
    ]


# ---------------------------------------------------------------------------
# Planning GraphQL queries — Phase 7
# ---------------------------------------------------------------------------


def _assembly_template_detail_query(
    info: Info,
    id: strawberry.ID,
) -> JSON:
    """Return connector and mapping data for an AssemblyTemplate."""
    from netbox_plant_graph.models import AssemblyTemplate
    try:
        template = AssemblyTemplate.objects.get(pk=int(id))
    except AssemblyTemplate.DoesNotExist:
        return None
    connectors = [
        {
            'id': c.pk,
            'side': c.side,
            'connector_number': c.connector_number,
            'position_count': c.position_count,
            'display': str(c),
        }
        for c in template.connectors.order_by('side', 'connector_number')
    ]
    mappings = [
        {
            'id': m.pk,
            'a_connector_id': m.a_connector_id,
            'a_position': m.a_position,
            'b_connector_id': m.b_connector_id,
            'b_position': m.b_position,
        }
        for m in template.mappings.order_by('a_connector', 'a_position')
    ]
    return {
        'id': template.pk,
        'name': template.name,
        'connectors': connectors,
        'mappings': mappings,
    }


def _spatial_template_detail_query(
    info: Info,
    id: strawberry.ID,
) -> JSON:
    """Return node tree for a SpatialTemplate."""
    from netbox_plant_graph.models import SpatialTemplate
    try:
        template = SpatialTemplate.objects.get(pk=int(id))
    except SpatialTemplate.DoesNotExist:
        return None
    nodes = [
        {
            'id': n.pk,
            'name_pattern': n.name_pattern,
            'node_type': n.node_type,
            'quantity': n.quantity,
            'sort_order': n.sort_order,
            'parent_id': n.parent_id,
        }
        for n in template.nodes.order_by('sort_order', 'pk')
    ]
    return {
        'id': template.pk,
        'name': template.name,
        'root_node_type': template.root_node_type,
        'nodes': nodes,
    }


def _deployment_plan_detail_query(
    info: Info,
    id: strawberry.ID,
) -> JSON:
    """Return DeploymentPlan details including stamp record summary."""
    from netbox_plant_graph.models import DeploymentPlan
    try:
        plan = DeploymentPlan.objects.get(pk=int(id))
    except DeploymentPlan.DoesNotExist:
        return None
    stamp_records = [
        {
            'id': r.pk,
            'status': r.status,
            'template_type': r.template_type.model if r.template_type_id else None,
            'template_id': r.template_id,
            'result_type': r.result_type.model if r.result_type_id else None,
            'result_id': r.result_id,
        }
        for r in plan.stamp_records.all().order_by('pk')
    ]
    return {
        'id': plan.pk,
        'name': plan.name,
        'status': plan.status,
        'stamp_records': stamp_records,
    }


def _spatial_placements_by_scope_query(
    info: Info,
    scope_type: str,
    scope_id: strawberry.ID,
    limit: int = 200,
) -> JSON:
    """Return SpatialPlacements filtered by reference frame (scope_type + scope_id)."""
    from django.contrib.contenttypes.models import ContentType
    from netbox_plant_graph.models import SpatialPlacement
    try:
        ct = ContentType.objects.get(app_label__in=('dcim',), model=scope_type)
    except ContentType.DoesNotExist:
        return {'placements': [], 'error': f'Unknown scope_type: {scope_type}'}
    placements_qs = SpatialPlacement.objects.filter(
        reference_frame_type=ct, reference_frame_id=int(scope_id)
    ).select_related('target_type')[:limit]
    placements = [
        {
            'id': p.pk,
            'target_type': p.target_type.model if p.target_type_id else None,
            'target_id': p.target_id,
            'position_x': float(p.position_x),
            'position_y': float(p.position_y),
            'coordinate_unit': p.coordinate_unit,
        }
        for p in placements_qs
    ]
    return {'placements': placements}


def _stamp_preview_query(
    info: Info,
    template_type: str,
    template_id: strawberry.ID,
    parameters: JSON | None = None,
) -> JSON:
    """Preview what would be created by stamping a template (dry-run)."""
    from netbox_plant_graph.services.stamp_preview import build_stamp_preview
    try:
        result = build_stamp_preview(
            template_type=template_type,
            template_id=int(template_id),
            parameters=parameters or {},
        )
        return result
    except Exception as exc:
        return {'error': str(exc)}


def build_query_type() -> type:
    annotations = {}
    namespace = {
        '__module__': __name__,
        'resolve_path': strawberry.field(resolver=_resolve_path_query),
        'plane_audit': strawberry.field(resolver=_plane_audit_query),
        'blast_radius': strawberry.field(resolver=_blast_radius_query),
        'lane_drilldown': strawberry.field(resolver=_lane_drilldown_query),
        'fabric_health': strawberry.field(resolver=_fabric_health_query),
        'lane_drilldown_typed': strawberry.field(resolver=_typed_lane_drilldown_query),
        'fabric_health_typed': strawberry.field(resolver=_typed_fabric_health_query),
        'lane_path': strawberry.field(resolver=_lane_path_query),
        'lane_set': strawberry.field(resolver=_lane_set_query),
        'lane_allocation_summary': strawberry.field(resolver=_lane_allocation_summary_query),
        'lane_compare': strawberry.field(resolver=_lane_compare_query),
        'audit_workflow_summary': strawberry.field(resolver=_audit_workflow_summary_query),
        'audit_finding_search': strawberry.field(resolver=_audit_finding_search_query),
        'audit_finding_detail': strawberry.field(resolver=_audit_finding_detail_query),
        'audit_run_timeline': strawberry.field(resolver=_audit_run_timeline_query),
        'policy_summary': strawberry.field(resolver=_policy_summary_query),
        'policy_dashboard': strawberry.field(resolver=_policy_dashboard_query),
        'contamination_domains': strawberry.field(resolver=_contamination_domains_query),
        'assembly_template_detail': strawberry.field(resolver=_assembly_template_detail_query),
        'spatial_template_detail': strawberry.field(resolver=_spatial_template_detail_query),
        'deployment_plan_detail': strawberry.field(resolver=_deployment_plan_detail_query),
        'spatial_placements_by_scope': strawberry.field(resolver=_spatial_placements_by_scope_query),
        'stamp_preview': strawberry.field(resolver=_stamp_preview_query),
    }
    annotations['resolve_path'] = JSON
    annotations['plane_audit'] = JSON
    annotations['blast_radius'] = JSON
    annotations['lane_drilldown'] = JSON
    annotations['fabric_health'] = JSON
    annotations['lane_drilldown_typed'] = LaneDrilldownType | None
    annotations['fabric_health_typed'] = FabricHealthType
    annotations['lane_path'] = LanePathType | None
    annotations['lane_set'] = LaneSetType | None
    annotations['lane_allocation_summary'] = LaneAllocationSummaryType | None
    annotations['lane_compare'] = LaneCompareType | None
    annotations['audit_workflow_summary'] = AuditWorkflowSummaryType
    annotations['audit_finding_search'] = list[AuditWorkflowFindingRecordType]
    annotations['audit_finding_detail'] = AuditWorkflowFindingDetailType | None
    annotations['audit_run_timeline'] = list[AuditWorkflowRunType]
    annotations['policy_summary'] = PolicySummaryType | None
    annotations['policy_dashboard'] = PolicyDashboardType | None
    annotations['contamination_domains'] = list[ContaminationDomainType]
    annotations['assembly_template_detail'] = JSON | None
    annotations['spatial_template_detail'] = JSON | None
    annotations['deployment_plan_detail'] = JSON | None
    annotations['spatial_placements_by_scope'] = JSON
    annotations['stamp_preview'] = JSON
    for spec in GRAPHQL_OBJECT_SPECS:
        type_class = GRAPHQL_TYPE_CLASS_MAP[spec.registry_key]
        annotations[spec.graphql.detail_field_name] = type_class | None
        annotations[spec.graphql.list_field_name] = list[type_class]
        namespace[spec.graphql.detail_field_name] = strawberry_django.field()
        namespace[spec.graphql.list_field_name] = strawberry_django.field()
    namespace['__annotations__'] = annotations
    query_class = type('NetBoxPlantGraphQuery', (), namespace)
    return strawberry.type(query_class, name='Query')


NetBoxPlantGraphQuery = build_query_type()
schema = [NetBoxPlantGraphQuery]
