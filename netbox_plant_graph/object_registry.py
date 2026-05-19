from .constants import MENU_GROUP_ORDER
from .models import (
    AssemblyConnectorTemplate,
    AssemblyMappingTemplate,
    AssemblyTemplate,
    AttachmentUnit,
    AuditFinding,
    AuditFindingEvent,
    AuditRun,
    AuditSuppression,
    BreakoutProfile,
    CoarseEdge,
    ConnectionTemplate,
    DeploymentPlan,
    DeviceBreakoutTemplate,
    DeviceChildInterfaceSpec,
    DisjointnessException,
    Fabric,
    FabricPlane,
    FineEdge,
    GraphBuildRun,
    LaneMap,
    PlaneMembership,
    PlantNode,
    RackPopulationSlot,
    RackPopulationTemplate,
    SignalLane,
    SpatialPlacement,
    SpatialTemplate,
    SpatialTemplateNode,
    StampRecord,
    TerminationPoint,
    TransferMap,
    UnresolvedStateObservation,
    UnresolvedStateSummary,
)
from .object_specs import (
    ApiSpec,
    FilterFormSpec,
    FilterSetSpec,
    FormSpec,
    GraphQLFilterFieldSpec,
    GraphQLFilterSpec,
    GraphQLSpec,
    GraphQLTypeSpec,
    LabelSpec,
    NavigationSpec,
    ObjectSpec,
    RouteSpec,
    TableSpec,
    ViewSpec,
)


def build_standard_object_spec(
    *,
    registry_key: str,
    model: type,
    class_prefix: str,
    route_slug: str,
    api_basename: str,
    label_singular: str,
    label_plural: str,
    navigation_group: str,
    navigation_label: str,
    navigation_order: int,
    api_fields: tuple[str, ...],
    brief_fields: tuple[str, ...],
    filter_fields: tuple[str, ...],
    search_fields: tuple[str, ...],
    graphql_fields: tuple[tuple[str, str], ...] = (),
    form_fields: tuple[str, ...] | None = None,
    table_fields: tuple[str, ...] | None = None,
    default_columns: tuple[str, ...] | None = None,
    linkify_field: str = 'name',
    ui_read_only: bool = False,
    api_read_only: bool = False,
    simple_detail: bool = True,
) -> ObjectSpec:
    form_spec = None
    view_edit_class_name = None
    view_delete_class_name = None

    if not ui_read_only:
        form_spec = FormSpec(class_name=f'{class_prefix}Form', fields=form_fields or api_fields)
        view_edit_class_name = f'{class_prefix}EditView'
        view_delete_class_name = f'{class_prefix}DeleteView'

    return ObjectSpec(
        registry_key=registry_key,
        model=model,
        labels=LabelSpec(singular=label_singular, plural=label_plural),
        routes=RouteSpec(slug=route_slug),
        api=ApiSpec(
            serializer_name=f'{class_prefix}Serializer',
            viewset_name=f'{class_prefix}ViewSet',
            basename=api_basename,
            fields=('id', 'url') + api_fields,
            brief_fields=brief_fields,
            read_only=api_read_only,
        ),
        filterset=FilterSetSpec(
            class_name=f'{class_prefix}FilterSet',
            fields=filter_fields,
            search_fields=search_fields,
        ),
        graphql=GraphQLSpec(
            filter=GraphQLFilterSpec(
                class_name=f'{class_prefix}Filter',
                fields=tuple(
                    GraphQLFilterFieldSpec(field_name=field_name, filter_kind=filter_kind)
                    for field_name, filter_kind in graphql_fields
                ),
            ),
            type=GraphQLTypeSpec(class_name=f'{class_prefix}Type'),
            detail_field_name=f'netbox_plant_graph_{route_slug.replace('-', '_')}',
            list_field_name=f'netbox_plant_graph_{route_slug.replace('-', '_')}_list',
        ),
        navigation=NavigationSpec(
            group=navigation_group,
            label=navigation_label,
            order=navigation_order,
            show_add_button=not ui_read_only,
        ),
        form=form_spec,
        filter_form=FilterFormSpec(class_name=f'{class_prefix}FilterForm', fields=filter_fields),
        table=TableSpec(
            class_name=f'{class_prefix}Table',
            fields=table_fields or ('pk',) + api_fields,
            default_columns=default_columns or brief_fields,
            linkify_field=linkify_field,
        ),
        view=ViewSpec(
            list_class_name=f'{class_prefix}ListView',
            detail_class_name=f'{class_prefix}View',
            edit_class_name=view_edit_class_name,
            delete_class_name=view_delete_class_name,
            simple_detail=simple_detail,
        ),
    )


OBJECT_SPECS = (
    build_standard_object_spec(registry_key='fabric', model=Fabric, class_prefix='Fabric', route_slug='fabric', api_basename='fabrics', label_singular='Fabric', label_plural='Fabrics', navigation_group='Fabric Model', navigation_label='Fabrics', navigation_order=100, api_fields=('name', 'description', 'expected_plane_count', 'tier_depth', 'disjointness_policy', 'tenant', 'tier_role_map'), brief_fields=('name', 'expected_plane_count', 'tier_depth', 'tenant'), filter_fields=('name', 'disjointness_policy', 'tenant'), search_fields=('name__icontains', 'description__icontains'), graphql_fields=(('name', 'str'), ('disjointness_policy', 'str'))),
    build_standard_object_spec(registry_key='fabricplane', model=FabricPlane, class_prefix='FabricPlane', route_slug='fabric-plane', api_basename='fabric-planes', label_singular='Fabric Plane', label_plural='Fabric Planes', navigation_group='Fabric Model', navigation_label='Fabric Planes', navigation_order=110, api_fields=('fabric', 'plane_number', 'description'), brief_fields=('fabric', 'plane_number'), filter_fields=('fabric', 'plane_number'), search_fields=('description__icontains',), graphql_fields=(('fabric_id', 'id'), ('plane_number', 'id'))),
    build_standard_object_spec(registry_key='plantnode', model=PlantNode, class_prefix='PlantNode', route_slug='plant-node', api_basename='plant-nodes', label_singular='Plant Node', label_plural='Plant Nodes', navigation_group='Physical Topology', navigation_label='Plant Nodes', navigation_order=200, api_fields=('fabric', 'name', 'node_type', 'role', 'status', 'tenant'), brief_fields=('name', 'node_type', 'status', 'tenant'), filter_fields=('fabric', 'node_type', 'status', 'tenant'), search_fields=('name__icontains', 'role__icontains', 'status__icontains'), graphql_fields=(('fabric_id', 'id'), ('name', 'str'), ('node_type', 'str'), ('status', 'str'))),
    build_standard_object_spec(registry_key='terminationpoint', model=TerminationPoint, class_prefix='TerminationPoint', route_slug='termination-point', api_basename='termination-points', label_singular='Termination Point', label_plural='Termination Points', navigation_group='Physical Topology', navigation_label='Termination Points', navigation_order=210, api_fields=('plant_node', 'name', 'tp_type', 'connector_type', 'channel_capacity', 'speed_gbps'), brief_fields=('plant_node', 'name', 'tp_type'), filter_fields=('plant_node', 'tp_type'), search_fields=('name__icontains', 'connector_type__icontains'), graphql_fields=(('plant_node_id', 'id'), ('name', 'str'), ('tp_type', 'str'))),
    build_standard_object_spec(registry_key='attachmentunit', model=AttachmentUnit, class_prefix='AttachmentUnit', route_slug='attachment-unit', api_basename='attachment-units', label_singular='Attachment Unit', label_plural='Attachment Units', navigation_group='Physical Topology', navigation_label='Attachment Units', navigation_order=220, api_fields=('termination_point', 'name', 'ordinal', 'unit_type', 'speed_gbps', 'active'), brief_fields=('termination_point', 'name', 'ordinal', 'unit_type'), filter_fields=('termination_point', 'unit_type', 'active'), search_fields=('name__icontains', 'topology_role__icontains'), graphql_fields=(('termination_point_id', 'id'), ('name', 'str'), ('unit_type', 'str'), ('active', 'bool'))),
    build_standard_object_spec(registry_key='signallane', model=SignalLane, class_prefix='SignalLane', route_slug='signal-lane', api_basename='signal-lanes', label_singular='Optical Lane', label_plural='Optical Lanes', navigation_group='Physical Topology', navigation_label='Optical Lanes', navigation_order=230, api_fields=('attachment_unit', 'name', 'lane_index', 'lane_kind', 'signaling', 'nominal_rate_gbps', 'wavelength_nm'), brief_fields=('attachment_unit', 'name', 'lane_index', 'lane_kind', 'wavelength_nm'), filter_fields=('attachment_unit', 'lane_kind', 'signaling', 'wavelength_nm'), search_fields=('name__icontains', 'direction_role__icontains'), graphql_fields=(('attachment_unit_id', 'id'), ('name', 'str'), ('lane_kind', 'str'), ('signaling', 'str')), ui_read_only=True, api_read_only=True),
    build_standard_object_spec(registry_key='coarseedge', model=CoarseEdge, class_prefix='CoarseEdge', route_slug='coarse-edge', api_basename='coarse-edges', label_singular='Coarse Edge', label_plural='Coarse Edges', navigation_group='Physical Topology', navigation_label='Coarse Edges', navigation_order=240, api_fields=('edge_type', 'a_tp', 'b_tp', 'cable_profile_name'), brief_fields=('edge_type', 'a_tp', 'b_tp'), filter_fields=('edge_type', 'a_tp', 'b_tp'), search_fields=('cable_profile_name__icontains',), graphql_fields=(('edge_type', 'str'), ('a_tp_id', 'id'), ('b_tp_id', 'id')), linkify_field='a_tp'),
    build_standard_object_spec(registry_key='fineedge', model=FineEdge, class_prefix='FineEdge', route_slug='fine-edge', api_basename='fine-edges', label_singular='Fine Edge', label_plural='Fine Edges', navigation_group='Physical Topology', navigation_label='Fine Edges', navigation_order=250, api_fields=('granularity', 'edge_type', 'a_au', 'b_au', 'a_lane', 'b_lane', 'derived_from_profile'), brief_fields=('granularity', 'edge_type', 'derived_from_profile'), filter_fields=('granularity', 'edge_type', 'derived_from_profile'), search_fields=(), graphql_fields=(('granularity', 'str'), ('edge_type', 'str'), ('derived_from_profile', 'bool')), ui_read_only=True, api_read_only=True, linkify_field='edge_type'),
    build_standard_object_spec(registry_key='transfermap', model=TransferMap, class_prefix='TransferMap', route_slug='transfer-map', api_basename='transfer-maps', label_singular='Transfer Map', label_plural='Transfer Maps', navigation_group='Connectivity Mapping', navigation_label='Transfer Maps', navigation_order=300, api_fields=('owner_node', 'src_attachment_unit', 'dst_attachment_unit', 'mapping_type'), brief_fields=('owner_node', 'mapping_type', 'src_attachment_unit', 'dst_attachment_unit'), filter_fields=('owner_node', 'mapping_type'), search_fields=(), graphql_fields=(('owner_node_id', 'id'), ('mapping_type', 'str')), linkify_field='mapping_type'),
    build_standard_object_spec(registry_key='lanemap', model=LaneMap, class_prefix='LaneMap', route_slug='lane-map', api_basename='lane-maps', label_singular='Lane Map', label_plural='Lane Maps', navigation_group='Connectivity Mapping', navigation_label='Lane Maps', navigation_order=310, api_fields=('owner_node', 'owner_edge', 'src_lane', 'dst_lane', 'mapping_type'), brief_fields=('mapping_type', 'src_lane', 'dst_lane'), filter_fields=('owner_node', 'owner_edge', 'mapping_type'), search_fields=(), graphql_fields=(('owner_node_id', 'id'), ('owner_edge_id', 'id'), ('mapping_type', 'str')), ui_read_only=True, api_read_only=True, linkify_field='mapping_type'),
    build_standard_object_spec(registry_key='planemembership', model=PlaneMembership, class_prefix='PlaneMembership', route_slug='plane-membership', api_basename='plane-memberships', label_singular='Plane Membership', label_plural='Plane Memberships', navigation_group='Policy Control', navigation_label='Plane Memberships', navigation_order=400, api_fields=('plane', 'member_type', 'member_id', 'membership_role'), brief_fields=('plane', 'membership_role'), filter_fields=('plane', 'membership_role'), search_fields=(), graphql_fields=(('plane_id', 'id'), ('membership_role', 'str')), linkify_field='membership_role'),
    build_standard_object_spec(registry_key='disjointnessexception', model=DisjointnessException, class_prefix='DisjointnessException', route_slug='disjointness-exception', api_basename='disjointness-exceptions', label_singular='Disjointness Exception', label_plural='Disjointness Exceptions', navigation_group='Policy Control', navigation_label='Disjointness Exceptions', navigation_order=410, api_fields=('fabric', 'policy_mode', 'exception_type', 'target_type', 'target_id', 'plane_a', 'plane_b', 'scope_kind', 'reason', 'approved_by', 'approved_at', 'expires_at', 'status', 'active'), brief_fields=('fabric', 'exception_type', 'status'), filter_fields=('fabric', 'policy_mode', 'exception_type', 'target_type', 'target_id', 'plane_a', 'plane_b', 'scope_kind', 'status', 'active'), search_fields=('reason__icontains',), graphql_fields=(('fabric_id', 'id'), ('policy_mode', 'str'), ('exception_type', 'str'), ('scope_kind', 'str'), ('status', 'str'), ('active', 'bool')), form_fields=('fabric', 'policy_mode', 'exception_type', 'target_type', 'target_id', 'plane_a', 'plane_b', 'scope_kind', 'lane_selector', 'reason', 'expires_at', 'metadata'), default_columns=('fabric', 'exception_type', 'status', 'plane_a', 'plane_b'), linkify_field='exception_type', api_read_only=True),
    build_standard_object_spec(registry_key='graphbuildrun', model=GraphBuildRun, class_prefix='GraphBuildRun', route_slug='graph-build-run', api_basename='graph-build-runs', label_singular='Graph Build Run', label_plural='Graph Build Runs', navigation_group='Audit Records', navigation_label='Graph Build Runs', navigation_order=480, api_fields=('fabric', 'scope_label', 'trigger_mode', 'status', 'started_at', 'completed_at', 'stats', 'metadata'), brief_fields=('fabric', 'status', 'started_at'), filter_fields=('fabric', 'trigger_mode', 'status'), search_fields=('scope_label__icontains',), graphql_fields=(('fabric_id', 'id'), ('status', 'str'), ('trigger_mode', 'str')), ui_read_only=True, api_read_only=True, linkify_field='status'),
    build_standard_object_spec(registry_key='unresolvedstatesummary', model=UnresolvedStateSummary, class_prefix='UnresolvedStateSummary', route_slug='unresolved-state-summary', api_basename='unresolved-state-summaries', label_singular='Unresolved State Summary', label_plural='Unresolved State Summaries', navigation_group='Audit Records', navigation_label='Unresolved State Summaries', navigation_order=485, api_fields=('fabric', 'plane', 'scope_label', 'summary_kind', 'cause_code', 'fingerprint', 'active', 'first_seen_build', 'last_seen_build', 'first_seen_at', 'last_seen_at', 'resolved_at', 'owner_object_type', 'owner_object_id', 'representative_object_type', 'representative_object_id', 'plane_ids', 'selector', 'anchors', 'impact', 'metadata'), brief_fields=('cause_code', 'summary_kind', 'active', 'last_seen_at'), filter_fields=('fabric', 'plane', 'summary_kind', 'cause_code', 'active', 'owner_object_type', 'owner_object_id', 'representative_object_type', 'representative_object_id'), search_fields=('scope_label__icontains', 'fingerprint__icontains'), graphql_fields=(('fabric_id', 'id'), ('plane_id', 'id'), ('summary_kind', 'str'), ('cause_code', 'str'), ('active', 'bool'), ('owner_object_type_id', 'id'), ('owner_object_id', 'id'), ('representative_object_type_id', 'id'), ('representative_object_id', 'id')), ui_read_only=True, api_read_only=True, default_columns=('cause_code', 'summary_kind', 'active', 'last_seen_at', 'fabric'), linkify_field='cause_code'),
    build_standard_object_spec(registry_key='unresolvedstateobservation', model=UnresolvedStateObservation, class_prefix='UnresolvedStateObservation', route_slug='unresolved-state-observation', api_basename='unresolved-state-observations', label_singular='Unresolved State Observation', label_plural='Unresolved State Observations', navigation_group='Audit Records', navigation_label='Unresolved State Observations', navigation_order=486, api_fields=('summary', 'build', 'observed_at', 'impact', 'metadata', 'related_audit_run'), brief_fields=('summary', 'build', 'observed_at'), filter_fields=('summary', 'build', 'related_audit_run'), search_fields=(), graphql_fields=(('summary_id', 'id'), ('build_id', 'id'), ('related_audit_run_id', 'id')), ui_read_only=True, api_read_only=True, default_columns=('summary', 'build', 'observed_at'), linkify_field='summary'),
    build_standard_object_spec(registry_key='auditrun', model=AuditRun, class_prefix='AuditRun', route_slug='audit-run', api_basename='audit-runs', label_singular='Audit Run', label_plural='Audit Runs', navigation_group='Audit Records', navigation_label='Audit Runs', navigation_order=490, api_fields=('fabric', 'scope_label', 'trigger_mode', 'status', 'started_at', 'completed_at', 'finding_count', 'new_count', 'reopened_count', 'resolved_count'), brief_fields=('fabric', 'status', 'started_at'), filter_fields=('fabric', 'trigger_mode', 'status'), search_fields=('scope_label__icontains',), graphql_fields=(('fabric_id', 'id'), ('status', 'str'), ('trigger_mode', 'str')), ui_read_only=True, api_read_only=True, linkify_field='status'),
    build_standard_object_spec(registry_key='auditfinding', model=AuditFinding, class_prefix='AuditFinding', route_slug='audit-finding', api_basename='audit-findings', label_singular='Audit Finding', label_plural='Audit Findings', navigation_group='Audit Records', navigation_label='Audit Findings', navigation_order=500, api_fields=('fabric', 'plane', 'finding_type', 'severity', 'status', 'active', 'message', 'object_type', 'object_id', 'assigned_to', 'acknowledged_by', 'last_seen_at', 'resolved_at'), brief_fields=('finding_type', 'severity', 'status'), filter_fields=('fabric', 'plane', 'finding_type', 'severity', 'status', 'active', 'assigned_to', 'acknowledged_by', 'object_type', 'object_id'), search_fields=('message__icontains',), graphql_fields=(('fabric_id', 'id'), ('plane_id', 'id'), ('finding_type', 'str'), ('severity', 'str'), ('status', 'str'), ('active', 'bool')), ui_read_only=True, api_read_only=True, linkify_field='finding_type'),
    build_standard_object_spec(registry_key='auditfindingevent', model=AuditFindingEvent, class_prefix='AuditFindingEvent', route_slug='audit-finding-event', api_basename='audit-finding-events', label_singular='Audit Finding Event', label_plural='Audit Finding Events', navigation_group='Audit Records', navigation_label='Audit Finding Events', navigation_order=510, api_fields=('finding', 'run', 'event_type', 'actor', 'old_status', 'new_status', 'message', 'created'), brief_fields=('finding', 'event_type', 'created'), filter_fields=('finding', 'run', 'event_type', 'actor'), search_fields=('message__icontains',), graphql_fields=(('finding_id', 'id'), ('run_id', 'id'), ('event_type', 'str')), ui_read_only=True, api_read_only=True, linkify_field='event_type'),
    build_standard_object_spec(registry_key='auditsuppression', model=AuditSuppression, class_prefix='AuditSuppression', route_slug='audit-suppression', api_basename='audit-suppressions', label_singular='Audit Suppression', label_plural='Audit Suppressions', navigation_group='Audit Records', navigation_label='Audit Suppressions', navigation_order=520, api_fields=('finding', 'created_by', 'reason', 'expires_at', 'active', 'created'), brief_fields=('finding', 'active', 'expires_at'), filter_fields=('finding', 'created_by', 'active'), search_fields=('reason__icontains',), graphql_fields=(('finding_id', 'id'), ('active', 'bool')), ui_read_only=True, api_read_only=True, linkify_field='finding'),
    # --- Planning / Assembly Templates ---
    build_standard_object_spec(registry_key='assemblytemplate', model=AssemblyTemplate, class_prefix='AssemblyTemplate', route_slug='assembly-template', api_basename='assembly-templates', label_singular='Assembly Template', label_plural='Assembly Templates', navigation_group='Assembly Templates', navigation_label='Assembly Templates', navigation_order=600, api_fields=('name', 'slug', 'description', 'tenant', 'assembly_type', 'manufacturer', 'part_number', 'device_type', 'cable_profile_hint', 'breakout_profile', 'metadata'), brief_fields=('name', 'tenant', 'assembly_type', 'manufacturer'), filter_fields=('name', 'tenant', 'assembly_type', 'manufacturer', 'device_type', 'breakout_profile'), search_fields=('name__icontains', 'description__icontains', 'part_number__icontains'), graphql_fields=(('name', 'str'), ('assembly_type', 'str'), ('manufacturer_id', 'id')), form_fields=('name', 'slug', 'description', 'tenant', 'assembly_type', 'manufacturer', 'part_number', 'device_type', 'cable_profile_hint', 'breakout_profile', 'metadata')),
    build_standard_object_spec(registry_key='assemblyconnectortemplate', model=AssemblyConnectorTemplate, class_prefix='AssemblyConnectorTemplate', route_slug='assembly-connector-template', api_basename='assembly-connector-templates', label_singular='Assembly Connector Template', label_plural='Assembly Connector Templates', navigation_group='Assembly Templates', navigation_label='Assembly Connectors', navigation_order=610, api_fields=('template', 'side', 'connector_number', 'connector_type', 'position_count', 'label', 'metadata'), brief_fields=('template', 'side', 'connector_number', 'connector_type'), filter_fields=('template', 'side', 'connector_type'), search_fields=('label__icontains',), graphql_fields=(('template_id', 'id'), ('side', 'str'), ('connector_type', 'str')), form_fields=('template', 'side', 'connector_number', 'connector_type', 'position_count', 'label', 'metadata'), linkify_field='label'),
    build_standard_object_spec(registry_key='assemblymappingtemplate', model=AssemblyMappingTemplate, class_prefix='AssemblyMappingTemplate', route_slug='assembly-mapping-template', api_basename='assembly-mapping-templates', label_singular='Assembly Mapping Template', label_plural='Assembly Mapping Templates', navigation_group='Assembly Templates', navigation_label='Assembly Mappings', navigation_order=620, api_fields=('template', 'a_connector', 'a_position', 'b_connector', 'b_position', 'mapping_type', 'metadata'), brief_fields=('template', 'a_connector', 'a_position', 'b_connector', 'b_position'), filter_fields=('template', 'a_connector', 'b_connector', 'mapping_type'), search_fields=(), graphql_fields=(('template_id', 'id'), ('mapping_type', 'str')), form_fields=('template', 'a_connector', 'a_position', 'b_connector', 'b_position', 'mapping_type', 'metadata'), linkify_field='mapping_type'),
    # --- Planning / Spatial Placement ---
    build_standard_object_spec(registry_key='spatialplacement', model=SpatialPlacement, class_prefix='SpatialPlacement', route_slug='spatial-placement', api_basename='spatial-placements', label_singular='Spatial Placement', label_plural='Spatial Placements', navigation_group='Spatial Planning', navigation_label='Spatial Placements', navigation_order=630, api_fields=('target_type', 'target_id', 'reference_frame_type', 'reference_frame_id', 'position_x', 'position_y', 'position_z', 'orientation', 'coordinate_unit', 'metadata'), brief_fields=('target_type', 'target_id', 'position_x', 'position_y', 'position_z'), filter_fields=('target_type', 'coordinate_unit'), search_fields=(), graphql_fields=(('coordinate_unit', 'str'),), form_fields=('target_type', 'target_id', 'reference_frame_type', 'reference_frame_id', 'position_x', 'position_y', 'position_z', 'orientation', 'coordinate_unit', 'metadata'), ui_read_only=True, api_read_only=True),
    # --- Planning / Spatial Templates ---
    build_standard_object_spec(registry_key='spatialtemplate', model=SpatialTemplate, class_prefix='SpatialTemplate', route_slug='spatial-template', api_basename='spatial-templates', label_singular='Spatial Template', label_plural='Spatial Templates', navigation_group='Spatial Planning', navigation_label='Spatial Templates', navigation_order=640, api_fields=('name', 'slug', 'description', 'tenant', 'root_node_type', 'metadata'), brief_fields=('name', 'tenant', 'root_node_type'), filter_fields=('name', 'tenant', 'root_node_type'), search_fields=('name__icontains', 'description__icontains'), graphql_fields=(('name', 'str'), ('root_node_type', 'str')), form_fields=('name', 'slug', 'description', 'tenant', 'root_node_type', 'metadata')),
    build_standard_object_spec(registry_key='spatialtemplatenode', model=SpatialTemplateNode, class_prefix='SpatialTemplateNode', route_slug='spatial-template-node', api_basename='spatial-template-nodes', label_singular='Spatial Template Node', label_plural='Spatial Template Nodes', navigation_group='Spatial Planning', navigation_label='Spatial Template Nodes', navigation_order=650, api_fields=('template', 'parent', 'name_pattern', 'node_type', 'quantity', 'sort_order', 'position_x', 'position_y', 'position_z', 'position_x_stride', 'position_y_stride', 'rack_type', 'rack_population_template', 'metadata'), brief_fields=('template', 'name_pattern', 'node_type', 'quantity'), filter_fields=('template', 'node_type', 'rack_type', 'rack_population_template'), search_fields=('name_pattern__icontains',), graphql_fields=(('template_id', 'id'), ('node_type', 'str')), form_fields=('template', 'parent', 'name_pattern', 'node_type', 'quantity', 'sort_order', 'position_x', 'position_y', 'position_z', 'position_x_stride', 'position_y_stride', 'rack_type', 'rack_population_template', 'metadata')),
    # --- Planning / Deployment Plan & Stamp Provenance ---
    build_standard_object_spec(registry_key='deploymentplan', model=DeploymentPlan, class_prefix='DeploymentPlan', route_slug='deployment-plan', api_basename='deployment-plans', label_singular='Deployment Plan', label_plural='Deployment Plans', navigation_group='Deployment Planning', navigation_label='Deployment Plans', navigation_order=660, api_fields=('fabric', 'tenant', 'name', 'description', 'status', 'created_by', 'approved_by', 'approved_at', 'metadata'), brief_fields=('name', 'status', 'fabric', 'tenant'), filter_fields=('fabric', 'tenant', 'status', 'created_by', 'approved_by'), search_fields=('name__icontains', 'description__icontains'), graphql_fields=(('name', 'str'), ('status', 'str'), ('fabric_id', 'id')), form_fields=('fabric', 'tenant', 'name', 'description', 'status', 'metadata')),
    build_standard_object_spec(registry_key='stamprecord', model=StampRecord, class_prefix='StampRecord', route_slug='stamp-record', api_basename='stamp-records', label_singular='Stamp Record', label_plural='Stamp Records', navigation_group='Deployment Planning', navigation_label='Stamp Records', navigation_order=670, api_fields=('plan', 'template_type', 'template_id', 'result_type', 'result_id', 'parameters', 'stamped_at', 'stamped_by', 'status', 'error_detail', 'metadata'), brief_fields=('plan', 'status', 'stamped_at'), filter_fields=('plan', 'status', 'template_type', 'result_type', 'stamped_by'), search_fields=('error_detail__icontains',), graphql_fields=(('plan_id', 'id'), ('status', 'str')), form_fields=('plan', 'template_type', 'template_id', 'result_type', 'result_id', 'parameters', 'status', 'error_detail', 'metadata')),
    # --- Planning / Rack Population Templates ---
    build_standard_object_spec(registry_key='rackpopulationtemplate', model=RackPopulationTemplate, class_prefix='RackPopulationTemplate', route_slug='rack-population-template', api_basename='rack-population-templates', label_singular='Rack Population Template', label_plural='Rack Population Templates', navigation_group='Rack Population', navigation_label='Rack Population Templates', navigation_order=680, api_fields=('name', 'slug', 'description', 'tenant', 'rack_type', 'plane_multiplier', 'fabric', 'parameters', 'metadata'), brief_fields=('name', 'tenant', 'rack_type'), filter_fields=('name', 'tenant', 'rack_type', 'fabric'), search_fields=('name__icontains', 'description__icontains'), graphql_fields=(('name', 'str'), ('rack_type_id', 'id')), form_fields=('name', 'slug', 'description', 'tenant', 'rack_type', 'plane_multiplier', 'fabric', 'parameters', 'metadata')),
    build_standard_object_spec(registry_key='rackpopulationslot', model=RackPopulationSlot, class_prefix='RackPopulationSlot', route_slug='rack-population-slot', api_basename='rack-population-slots', label_singular='Rack Population Slot', label_plural='Rack Population Slots', navigation_group='Rack Population', navigation_label='Rack Population Slots', navigation_order=690, api_fields=('template', 'u_position', 'face', 'device_type', 'device_role', 'name_pattern', 'assembly_template', 'breakout_template', 'sort_order', 'metadata'), brief_fields=('template', 'u_position', 'face', 'device_type'), filter_fields=('template', 'face', 'device_type', 'device_role', 'assembly_template', 'breakout_template'), search_fields=('name_pattern__icontains',), graphql_fields=(('template_id', 'id'), ('face', 'str'), ('device_type_id', 'id')), form_fields=('template', 'u_position', 'face', 'device_type', 'device_role', 'name_pattern', 'assembly_template', 'breakout_template', 'sort_order', 'metadata')),
    # --- Planning / Connection Templates ---
    build_standard_object_spec(registry_key='connectiontemplate', model=ConnectionTemplate, class_prefix='ConnectionTemplate', route_slug='connection-template', api_basename='connection-templates', label_singular='Connection Template', label_plural='Connection Templates', navigation_group='Spatial Planning', navigation_label='Connection Templates', navigation_order=655, api_fields=('spatial_template', 'name', 'description', 'assembly_template', 'source_node', 'source_slot_index', 'source_connector_number', 'dest_node', 'dest_slot_index', 'dest_connector_number', 'enumeration_mode', 'label_pattern', 'sort_order', 'metadata'), brief_fields=('spatial_template', 'name', 'enumeration_mode'), filter_fields=('spatial_template', 'assembly_template', 'source_node', 'dest_node', 'enumeration_mode'), search_fields=('name__icontains', 'description__icontains', 'label_pattern__icontains'), graphql_fields=(('spatial_template_id', 'id'), ('name', 'str'), ('enumeration_mode', 'str')), form_fields=('spatial_template', 'name', 'description', 'assembly_template', 'source_node', 'source_slot_index', 'source_connector_number', 'dest_node', 'dest_slot_index', 'dest_connector_number', 'enumeration_mode', 'label_pattern', 'sort_order', 'metadata')),
    # --- Breakout Profiles ---
    build_standard_object_spec(registry_key='breakoutprofile', model=BreakoutProfile, class_prefix='BreakoutProfile', route_slug='breakout-profile', api_basename='breakout-profiles', label_singular='Breakout Profile', label_plural='Breakout Profiles', navigation_group='Connectivity Mapping', navigation_label='Breakout Profiles', navigation_order=320, api_fields=('name', 'slug', 'description', 'parent_speed_gbps', 'child_count', 'child_speed_gbps', 'mapping_mode', 'position_map', 'metadata'), brief_fields=('name', 'child_count', 'mapping_mode'), filter_fields=('name', 'mapping_mode', 'child_count'), search_fields=('name__icontains', 'description__icontains', 'slug__icontains'), graphql_fields=(('name', 'str'), ('slug', 'str'), ('mapping_mode', 'str')), form_fields=('name', 'slug', 'description', 'parent_speed_gbps', 'child_count', 'child_speed_gbps', 'mapping_mode', 'position_map', 'metadata')),
    # --- Device Breakout Templates ---
    build_standard_object_spec(registry_key='devicebreakouttemplate', model=DeviceBreakoutTemplate, class_prefix='DeviceBreakoutTemplate', route_slug='device-breakout-template', api_basename='device-breakout-templates', label_singular='Device Breakout Template', label_plural='Device Breakout Templates', navigation_group='Assembly Templates', navigation_label='Device Breakout Templates', navigation_order=595, api_fields=('name', 'slug', 'description', 'device_type', 'metadata'), brief_fields=('name', 'device_type'), filter_fields=('name', 'device_type'), search_fields=('name__icontains', 'description__icontains'), graphql_fields=(('name', 'str'), ('device_type_id', 'id')), form_fields=('name', 'slug', 'description', 'device_type', 'metadata')),
    build_standard_object_spec(registry_key='devicechildinterfacespec', model=DeviceChildInterfaceSpec, class_prefix='DeviceChildInterfaceSpec', route_slug='device-child-interface-spec', api_basename='device-child-interface-specs', label_singular='Device Child Interface Spec', label_plural='Device Child Interface Specs', navigation_group='Assembly Templates', navigation_label='Device Child Interface Specs', navigation_order=596, api_fields=('breakout_template', 'parent_interface_name', 'child_name_pattern', 'child_count', 'child_interface_type', 'child_speed_kbps', 'fabric_plane_start', 'breakout_profile', 'sort_order', 'metadata'), brief_fields=('breakout_template', 'parent_interface_name', 'child_count'), filter_fields=('breakout_template', 'breakout_profile', 'child_interface_type'), search_fields=('parent_interface_name__icontains', 'child_name_pattern__icontains'), graphql_fields=(('breakout_template_id', 'id'), ('parent_interface_name', 'str')), form_fields=('breakout_template', 'parent_interface_name', 'child_name_pattern', 'child_count', 'child_interface_type', 'child_speed_kbps', 'fabric_plane_start', 'breakout_profile', 'sort_order', 'metadata'), linkify_field='parent_interface_name'),
)

API_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.api is not None)
GRAPHQL_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.graphql is not None)
FILTERSET_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.filterset is not None)
FORM_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.form is not None)
FILTER_FORM_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.filter_form is not None)
TABLE_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.table is not None)
VIEW_OBJECT_SPECS = tuple(spec for spec in OBJECT_SPECS if spec.view is not None)
SIMPLE_DETAIL_VIEW_OBJECT_SPECS = tuple(spec for spec in VIEW_OBJECT_SPECS if spec.view.simple_detail)
OBJECT_SPEC_BY_REGISTRY_KEY = {spec.registry_key: spec for spec in OBJECT_SPECS}


def get_object_spec(registry_key: str) -> ObjectSpec:
    return OBJECT_SPEC_BY_REGISTRY_KEY[registry_key]


def get_navigation_groups() -> list[tuple[str, list[ObjectSpec]]]:
    grouped: dict[str, list[ObjectSpec]] = {group: [] for group in MENU_GROUP_ORDER}
    for spec in OBJECT_SPECS:
        if spec.navigation is None:
            continue
        grouped.setdefault(spec.navigation.group, []).append(spec)
    return [
        (group, sorted(grouped.get(group, []), key=lambda spec: spec.navigation.order))
        for group in MENU_GROUP_ORDER
        if grouped.get(group)
    ]
