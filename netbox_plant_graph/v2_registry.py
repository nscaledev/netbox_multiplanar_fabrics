from __future__ import annotations

from dataclasses import dataclass

from .models import (
    AllocationRuleSet,
    ArchitectureRole,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    PathIntent,
    Plane,
    StampRun,
    StampTemplate,
    StrandTermination,
    TransferMap,
    TransferPattern,
    TransportChannel,
)


@dataclass(frozen=True)
class V2ObjectSpec:
    registry_key: str
    model: type
    class_prefix: str
    route_slug: str
    api_basename: str
    label_singular: str
    label_plural: str
    fields: tuple[str, ...]
    brief_fields: tuple[str, ...]
    detail_fields: tuple[str, ...] | None = None

    @property
    def serializer_name(self) -> str:
        return f'{self.class_prefix}Serializer'

    @property
    def viewset_name(self) -> str:
        return f'{self.class_prefix}ViewSet'

    @property
    def api_fields(self) -> tuple[str, ...]:
        return ('id', 'url', 'display') + self.fields

    @property
    def api_list_url_name(self) -> str:
        return f'plugins-api:netbox_plant_graph-api:{self.registry_key}-list'

    @property
    def api_detail_url_name(self) -> str:
        return f'plugins-api:netbox_plant_graph-api:{self.registry_key}-detail'

    @property
    def resolved_detail_fields(self) -> tuple[str, ...]:
        return self.detail_fields or self.fields


V2_OBJECT_SPECS = (
    V2ObjectSpec(
        registry_key='fabricarchitecture',
        model=FabricArchitecture,
        class_prefix='FabricArchitecture',
        route_slug='fabricarchitecture',
        api_basename='architectures',
        label_singular='Architecture',
        label_plural='Architectures',
        fields=('name', 'slug', 'version', 'status', 'plane_count', 'description', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'version'),
    ),
    V2ObjectSpec(
        registry_key='architecturerole',
        model=ArchitectureRole,
        class_prefix='ArchitectureRole',
        route_slug='architecture-role',
        api_basename='architecture-roles',
        label_singular='Architecture Role',
        label_plural='Architecture Roles',
        fields=('architecture', 'name', 'slug', 'role_kind', 'description', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'role_kind'),
    ),
    V2ObjectSpec(
        registry_key='transferpattern',
        model=TransferPattern,
        class_prefix='TransferPattern',
        route_slug='transfer-pattern',
        api_basename='transfer-patterns',
        label_singular='Transfer Pattern',
        label_plural='Transfer Patterns',
        fields=('architecture', 'name', 'slug', 'pattern_kind', 'rule', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'pattern_kind'),
    ),
    V2ObjectSpec(
        registry_key='allocationruleset',
        model=AllocationRuleSet,
        class_prefix='AllocationRuleSet',
        route_slug='allocation-rule-set',
        api_basename='allocation-rule-sets',
        label_singular='Allocation Rule Set',
        label_plural='Allocation Rule Sets',
        fields=('architecture', 'name', 'slug', 'rule', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug'),
    ),
    V2ObjectSpec(
        registry_key='fabric',
        model=Fabric,
        class_prefix='Fabric',
        route_slug='fabric',
        api_basename='fabrics',
        label_singular='Fabric',
        label_plural='Fabrics',
        fields=('architecture', 'name', 'slug', 'status', 'tenant', 'scope_site', 'scope_location', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'status'),
    ),
    V2ObjectSpec(
        registry_key='plane',
        model=Plane,
        class_prefix='Plane',
        route_slug='plane',
        api_basename='planes',
        label_singular='Plane',
        label_plural='Planes',
        fields=('fabric', 'plane_number', 'label', 'metadata'),
        brief_fields=('id', 'url', 'display', 'fabric', 'plane_number', 'label'),
    ),
    V2ObjectSpec(
        registry_key='fabricnode',
        model=FabricNode,
        class_prefix='FabricNode',
        route_slug='node',
        api_basename='nodes',
        label_singular='Fabric Node',
        label_plural='Fabric Nodes',
        fields=('fabric', 'role', 'parent', 'name', 'address', 'node_kind', 'local_index', 'metadata'),
        brief_fields=('id', 'url', 'display', 'fabric', 'name', 'address', 'node_kind'),
    ),
    V2ObjectSpec(
        registry_key='endpoint',
        model=Endpoint,
        class_prefix='Endpoint',
        route_slug='endpoint',
        api_basename='endpoints',
        label_singular='Endpoint',
        label_plural='Endpoints',
        fields=('fabric', 'node', 'parent', 'name', 'address', 'endpoint_kind', 'connector_kind', 'position_count', 'metadata'),
        brief_fields=('id', 'url', 'display', 'fabric', 'name', 'address', 'connector_kind'),
    ),
    V2ObjectSpec(
        registry_key='connectorposition',
        model=ConnectorPosition,
        class_prefix='ConnectorPosition',
        route_slug='connector-position',
        api_basename='connector-positions',
        label_singular='Connector Position',
        label_plural='Connector Positions',
        fields=('endpoint', 'position_number', 'label', 'metadata'),
        brief_fields=('id', 'url', 'display', 'endpoint', 'position_number'),
    ),
    V2ObjectSpec(
        registry_key='transportchannel',
        model=TransportChannel,
        class_prefix='TransportChannel',
        route_slug='transport-channel',
        api_basename='transport-channels',
        label_singular='Transport Channel',
        label_plural='Transport Channels',
        fields=('fabric', 'endpoint', 'plane', 'name', 'channel_index', 'speed_gbps', 'metadata'),
        brief_fields=('id', 'url', 'display', 'fabric', 'endpoint', 'name', 'channel_index'),
    ),
    V2ObjectSpec(
        registry_key='fibersegment',
        model=FiberSegment,
        class_prefix='FiberSegment',
        route_slug='fiber-segment',
        api_basename='fiber-segments',
        label_singular='Fiber Segment',
        label_plural='Fiber Segments',
        fields=('fabric', 'name', 'segment_kind', 'a_endpoint', 'b_endpoint', 'metadata'),
        brief_fields=('id', 'url', 'display', 'fabric', 'name', 'segment_kind'),
    ),
    V2ObjectSpec(
        registry_key='fiberstrand',
        model=FiberStrand,
        class_prefix='FiberStrand',
        route_slug='fiber-strand',
        api_basename='fiber-strands',
        label_singular='Fiber Strand',
        label_plural='Fiber Strands',
        fields=('segment', 'strand_index', 'label', 'metadata'),
        brief_fields=('id', 'url', 'display', 'segment', 'strand_index', 'label'),
    ),
    V2ObjectSpec(
        registry_key='strandtermination',
        model=StrandTermination,
        class_prefix='StrandTermination',
        route_slug='strand-termination',
        api_basename='strand-terminations',
        label_singular='Strand Termination',
        label_plural='Strand Terminations',
        fields=('strand', 'mpo_endpoint', 'mpo_position', 'termination_index', 'label', 'metadata'),
        brief_fields=('id', 'url', 'display', 'strand', 'mpo_position'),
    ),
    V2ObjectSpec(
        registry_key='opticallane',
        model=OpticalLane,
        class_prefix='OpticalLane',
        route_slug='optical-lane',
        api_basename='optical-lanes',
        label_singular='Optical Lane',
        label_plural='Optical Lanes',
        fields=(
            'fabric',
            'endpoint',
            'channel',
            'plane',
            'local_mpo_endpoint',
            'local_mpo_position',
            'lane_index',
            'local_mpo_index',
            'direction',
            'wavelength_nm',
            'pair_key',
            'nominal_rate_gbps',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'endpoint', 'lane_index', 'direction', 'wavelength_nm'),
    ),
    V2ObjectSpec(
        registry_key='transfermap',
        model=TransferMap,
        class_prefix='TransferMap',
        route_slug='transfer-map',
        api_basename='transfer-maps',
        label_singular='Transfer Map',
        label_plural='Transfer Maps',
        fields=(
            'fabric',
            'owner_node',
            'owner_segment',
            'pattern',
            'map_kind',
            'src_position',
            'dst_position',
            'bidirectional',
            'group_key',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'map_kind', 'src_position', 'dst_position'),
    ),
    V2ObjectSpec(
        registry_key='pathintent',
        model=PathIntent,
        class_prefix='PathIntent',
        route_slug='path-intent',
        api_basename='path-intents',
        label_singular='Path Intent',
        label_plural='Path Intents',
        fields=(
            'fabric',
            'name',
            'plane',
            'source_channel',
            'destination_channel',
            'source_endpoint',
            'destination_endpoint',
            'selector',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'name'),
    ),
    V2ObjectSpec(
        registry_key='stamptemplate',
        model=StampTemplate,
        class_prefix='StampTemplate',
        route_slug='stamp-template',
        api_basename='stamp-templates',
        label_singular='Stamp Template',
        label_plural='Stamp Templates',
        fields=('architecture', 'name', 'slug', 'description', 'template', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug'),
    ),
    V2ObjectSpec(
        registry_key='stamprun',
        model=StampRun,
        class_prefix='StampRun',
        route_slug='stamp-run',
        api_basename='stamp-runs',
        label_singular='Stamp Run',
        label_plural='Stamp Runs',
        fields=('template', 'fabric', 'status', 'parameters', 'result', 'error_detail', 'metadata'),
        brief_fields=('id', 'url', 'display', 'template', 'fabric', 'status'),
    ),
)

V2_OBJECT_SPEC_BY_KEY = {spec.registry_key: spec for spec in V2_OBJECT_SPECS}
V2_OBJECT_SPEC_BY_MODEL = {spec.model: spec for spec in V2_OBJECT_SPECS}


def get_v2_object_spec(registry_key: str) -> V2ObjectSpec:
    return V2_OBJECT_SPEC_BY_KEY[registry_key]


def get_v2_object_spec_for_model(model: type) -> V2ObjectSpec:
    return V2_OBJECT_SPEC_BY_MODEL[model]
