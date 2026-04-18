from .constants import MENU_GROUP_ORDER
from .models import (
    AttachmentUnit,
    AuditFinding,
    CoarseEdge,
    Fabric,
    FabricPlane,
    FineEdge,
    LaneMap,
    PlaneMembership,
    PlantNode,
    SignalLane,
    TerminationPoint,
    TransferMap,
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
    build_standard_object_spec(registry_key='fabric', model=Fabric, class_prefix='Fabric', route_slug='fabric', api_basename='fabrics', label_singular='Fabric', label_plural='Fabrics', navigation_group='Fabrics', navigation_label='Fabrics', navigation_order=100, api_fields=('name', 'description', 'expected_plane_count', 'tier_depth', 'disjointness_policy'), brief_fields=('name', 'expected_plane_count', 'tier_depth'), filter_fields=('name', 'disjointness_policy'), search_fields=('name__icontains', 'description__icontains'), graphql_fields=(('name', 'str'), ('disjointness_policy', 'str'))),
    build_standard_object_spec(registry_key='fabricplane', model=FabricPlane, class_prefix='FabricPlane', route_slug='fabric-plane', api_basename='fabric-planes', label_singular='Fabric Plane', label_plural='Fabric Planes', navigation_group='Fabrics', navigation_label='Fabric Planes', navigation_order=110, api_fields=('fabric', 'plane_number', 'description'), brief_fields=('fabric', 'plane_number'), filter_fields=('fabric', 'plane_number'), search_fields=('description__icontains',), graphql_fields=(('fabric_id', 'id'), ('plane_number', 'id'))),
    build_standard_object_spec(registry_key='plantnode', model=PlantNode, class_prefix='PlantNode', route_slug='plant-node', api_basename='plant-nodes', label_singular='Plant Node', label_plural='Plant Nodes', navigation_group='Topology', navigation_label='Plant Nodes', navigation_order=200, api_fields=('fabric', 'name', 'node_type', 'role', 'status'), brief_fields=('name', 'node_type', 'status'), filter_fields=('fabric', 'node_type', 'status'), search_fields=('name__icontains', 'role__icontains', 'status__icontains'), graphql_fields=(('fabric_id', 'id'), ('name', 'str'), ('node_type', 'str'), ('status', 'str'))),
    build_standard_object_spec(registry_key='terminationpoint', model=TerminationPoint, class_prefix='TerminationPoint', route_slug='termination-point', api_basename='termination-points', label_singular='Termination Point', label_plural='Termination Points', navigation_group='Topology', navigation_label='Termination Points', navigation_order=210, api_fields=('plant_node', 'name', 'tp_type', 'connector_type', 'channel_capacity', 'speed_gbps'), brief_fields=('plant_node', 'name', 'tp_type'), filter_fields=('plant_node', 'tp_type'), search_fields=('name__icontains', 'connector_type__icontains'), graphql_fields=(('plant_node_id', 'id'), ('name', 'str'), ('tp_type', 'str'))),
    build_standard_object_spec(registry_key='attachmentunit', model=AttachmentUnit, class_prefix='AttachmentUnit', route_slug='attachment-unit', api_basename='attachment-units', label_singular='Attachment Unit', label_plural='Attachment Units', navigation_group='Topology', navigation_label='Attachment Units', navigation_order=220, api_fields=('termination_point', 'name', 'ordinal', 'unit_type', 'speed_gbps', 'active'), brief_fields=('termination_point', 'name', 'ordinal', 'unit_type'), filter_fields=('termination_point', 'unit_type', 'active'), search_fields=('name__icontains', 'topology_role__icontains'), graphql_fields=(('termination_point_id', 'id'), ('name', 'str'), ('unit_type', 'str'), ('active', 'bool'))),
    build_standard_object_spec(registry_key='signallane', model=SignalLane, class_prefix='SignalLane', route_slug='signal-lane', api_basename='signal-lanes', label_singular='Signal Lane', label_plural='Signal Lanes', navigation_group='Topology', navigation_label='Signal Lanes', navigation_order=230, api_fields=('attachment_unit', 'name', 'lane_index', 'lane_kind', 'signaling', 'nominal_rate_gbps'), brief_fields=('attachment_unit', 'name', 'lane_index', 'lane_kind'), filter_fields=('attachment_unit', 'lane_kind', 'signaling'), search_fields=('name__icontains', 'direction_role__icontains'), graphql_fields=(('attachment_unit_id', 'id'), ('name', 'str'), ('lane_kind', 'str'), ('signaling', 'str')), ui_read_only=True, api_read_only=True),
    build_standard_object_spec(registry_key='coarseedge', model=CoarseEdge, class_prefix='CoarseEdge', route_slug='coarse-edge', api_basename='coarse-edges', label_singular='Coarse Edge', label_plural='Coarse Edges', navigation_group='Topology', navigation_label='Coarse Edges', navigation_order=240, api_fields=('edge_type', 'a_tp', 'b_tp', 'cable_profile_name'), brief_fields=('edge_type', 'a_tp', 'b_tp'), filter_fields=('edge_type', 'a_tp', 'b_tp'), search_fields=('cable_profile_name__icontains',), graphql_fields=(('edge_type', 'str'), ('a_tp_id', 'id'), ('b_tp_id', 'id')), linkify_field='a_tp'),
    build_standard_object_spec(registry_key='fineedge', model=FineEdge, class_prefix='FineEdge', route_slug='fine-edge', api_basename='fine-edges', label_singular='Fine Edge', label_plural='Fine Edges', navigation_group='Topology', navigation_label='Fine Edges', navigation_order=250, api_fields=('granularity', 'edge_type', 'a_au', 'b_au', 'a_lane', 'b_lane', 'derived_from_profile'), brief_fields=('granularity', 'edge_type', 'derived_from_profile'), filter_fields=('granularity', 'edge_type', 'derived_from_profile'), search_fields=(), graphql_fields=(('granularity', 'str'), ('edge_type', 'str'), ('derived_from_profile', 'bool')), ui_read_only=True, api_read_only=True, linkify_field='edge_type'),
    build_standard_object_spec(registry_key='transfermap', model=TransferMap, class_prefix='TransferMap', route_slug='transfer-map', api_basename='transfer-maps', label_singular='Transfer Map', label_plural='Transfer Maps', navigation_group='Mappings', navigation_label='Transfer Maps', navigation_order=300, api_fields=('owner_node', 'src_attachment_unit', 'dst_attachment_unit', 'mapping_type'), brief_fields=('owner_node', 'mapping_type', 'src_attachment_unit', 'dst_attachment_unit'), filter_fields=('owner_node', 'mapping_type'), search_fields=(), graphql_fields=(('owner_node_id', 'id'), ('mapping_type', 'str')), linkify_field='mapping_type'),
    build_standard_object_spec(registry_key='lanemap', model=LaneMap, class_prefix='LaneMap', route_slug='lane-map', api_basename='lane-maps', label_singular='Lane Map', label_plural='Lane Maps', navigation_group='Mappings', navigation_label='Lane Maps', navigation_order=310, api_fields=('owner_node', 'owner_edge', 'src_lane', 'dst_lane', 'mapping_type'), brief_fields=('mapping_type', 'src_lane', 'dst_lane'), filter_fields=('owner_node', 'owner_edge', 'mapping_type'), search_fields=(), graphql_fields=(('owner_node_id', 'id'), ('owner_edge_id', 'id'), ('mapping_type', 'str')), ui_read_only=True, api_read_only=True, linkify_field='mapping_type'),
    build_standard_object_spec(registry_key='planemembership', model=PlaneMembership, class_prefix='PlaneMembership', route_slug='plane-membership', api_basename='plane-memberships', label_singular='Plane Membership', label_plural='Plane Memberships', navigation_group='Policy', navigation_label='Plane Memberships', navigation_order=400, api_fields=('plane', 'member_type', 'member_id', 'membership_role'), brief_fields=('plane', 'membership_role'), filter_fields=('plane', 'membership_role'), search_fields=(), graphql_fields=(('plane_id', 'id'), ('membership_role', 'str')), linkify_field='membership_role'),
    build_standard_object_spec(registry_key='auditfinding', model=AuditFinding, class_prefix='AuditFinding', route_slug='audit-finding', api_basename='audit-findings', label_singular='Audit Finding', label_plural='Audit Findings', navigation_group='Audit', navigation_label='Audit Findings', navigation_order=500, api_fields=('finding_type', 'severity', 'message', 'object_type', 'object_id'), brief_fields=('finding_type', 'severity'), filter_fields=('finding_type', 'severity'), search_fields=('message__icontains',), graphql_fields=(('finding_type', 'str'), ('severity', 'str')), ui_read_only=True, api_read_only=True, linkify_field='finding_type'),
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
