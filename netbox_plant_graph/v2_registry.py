from __future__ import annotations

from dataclasses import dataclass
from django.db import models as django_models

from .models import (
    AuditEvent,
    OperationRun,
    AllocationRuleSet,
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureRole,
    ArchitectureSourceArtifact,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    OnboardingDesignItem,
    OnboardingExecutionStage,
    OnboardingObjectLink,
    OnboardingPlan,
    OnboardingPrerequisite,
    OnboardingSourceArtifact,
    OnboardingWorkspace,
    PathIntent,
    Plane,
    StampRun,
    StampTemplate,
    StrandTermination,
    SuppressionRule,
    TransceiverConnector,
    TransceiverConnectorProfile,
    TransceiverLaneProfile,
    TransceiverProfile,
    TransceiverProfileModuleType,
    TransferMap,
    TransferPattern,
    TransportChannel,
    TransportChannelPositionMap,
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
    form_fields: tuple[str, ...] | None = None
    filter_fields: tuple[str, ...] | None = None
    search_fields: tuple[str, ...] | None = None
    table_fields: tuple[str, ...] | None = None
    default_columns: tuple[str, ...] | None = None
    linkify_field: str | None = None

    @property
    def serializer_name(self) -> str:
        return f'{self.class_prefix}Serializer'

    @property
    def viewset_name(self) -> str:
        return f'{self.class_prefix}ViewSet'

    @property
    def table_name(self) -> str:
        return f'{self.class_prefix}Table'

    @property
    def form_name(self) -> str:
        return f'{self.class_prefix}Form'

    @property
    def filterset_name(self) -> str:
        return f'{self.class_prefix}FilterSet'

    @property
    def filter_form_name(self) -> str:
        return f'{self.class_prefix}FilterForm'

    @property
    def list_view_name(self) -> str:
        return f'{self.class_prefix}ListView'

    @property
    def detail_view_name(self) -> str:
        return f'{self.class_prefix}View'

    @property
    def edit_view_name(self) -> str:
        return f'{self.class_prefix}EditView'

    @property
    def delete_view_name(self) -> str:
        return f'{self.class_prefix}DeleteView'

    @property
    def changelog_view_name(self) -> str:
        return f'{self.class_prefix}ChangeLogView'

    @property
    def journal_view_name(self) -> str:
        return f'{self.class_prefix}JournalView'

    @property
    def path_prefix(self) -> str:
        return self.api_basename

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

    @property
    def resolved_form_fields(self) -> tuple[str, ...]:
        return self.form_fields or self.fields

    @property
    def resolved_filter_fields(self) -> tuple[str, ...]:
        if self.filter_fields is not None:
            return self.filter_fields
        return ('id',) + tuple(
            field_name
            for field_name in self.fields
            if _is_filterable_model_field(self.model, field_name)
        )

    @property
    def resolved_search_fields(self) -> tuple[str, ...]:
        if self.search_fields is not None:
            return self.search_fields
        return tuple(
            f'{field_name}__icontains'
            for field_name in self.fields
            if _is_searchable_model_field(self.model, field_name)
        )

    @property
    def resolved_table_fields(self) -> tuple[str, ...]:
        return self.table_fields or ('pk', 'id') + self.fields

    @property
    def resolved_default_columns(self) -> tuple[str, ...]:
        return self.default_columns or self.brief_fields[3:] or self.fields[:4]

    @property
    def resolved_linkify_field(self) -> str:
        if self.linkify_field:
            return self.linkify_field
        for candidate in ('name', 'address', 'label', 'slug', 'plane_number', 'lane_index', 'status'):
            if candidate in self.fields:
                return candidate
        return 'pk'


def _get_model_field(model: type, field_name: str):
    try:
        return model._meta.get_field(field_name)
    except Exception:
        return None


def _is_filterable_model_field(model: type, field_name: str) -> bool:
    field = _get_model_field(model, field_name)
    if field is None:
        return False
    return isinstance(
        field,
        (
            django_models.AutoField,
            django_models.BigAutoField,
            django_models.BooleanField,
            django_models.CharField,
            django_models.DecimalField,
            django_models.ForeignKey,
            django_models.IntegerField,
            django_models.PositiveIntegerField,
            django_models.PositiveBigIntegerField,
            django_models.SlugField,
        ),
    )


def _is_searchable_model_field(model: type, field_name: str) -> bool:
    field = _get_model_field(model, field_name)
    return isinstance(field, (django_models.CharField, django_models.SlugField, django_models.TextField))


V2_OBJECT_SPECS = (
    V2ObjectSpec(
        registry_key='fabricarchitecture',
        model=FabricArchitecture,
        class_prefix='FabricArchitecture',
        route_slug='fabricarchitecture',
        api_basename='architectures',
        label_singular='Architecture',
        label_plural='Architectures',
        fields=('name', 'slug', 'version', 'status', 'fabric_class', 'plane_count', 'description', 'metadata'),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'version', 'fabric_class'),
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
        registry_key='architectureworkspace',
        model=ArchitectureWorkspace,
        class_prefix='ArchitectureWorkspace',
        route_slug='architecture-workspace',
        api_basename='architecture-workspaces',
        label_singular='Architecture Workspace',
        label_plural='Architecture Workspaces',
        fields=(
            'name',
            'slug',
            'workspace_kind',
            'status',
            'target_slug',
            'target_version',
            'fabric_class',
            'base_architecture',
            'published_architecture',
            'created_by',
            'owner',
            'current_plan',
            'source_summary',
            'validation_summary',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'workspace_kind', 'status', 'target_slug', 'target_version'),
        form_fields=(
            'name',
            'slug',
            'workspace_kind',
            'status',
            'target_slug',
            'target_version',
            'fabric_class',
            'base_architecture',
            'owner',
            'metadata',
        ),
        default_columns=('name', 'status', 'target_slug', 'target_version', 'fabric_class'),
    ),
    V2ObjectSpec(
        registry_key='architecturesourceartifact',
        model=ArchitectureSourceArtifact,
        class_prefix='ArchitectureSourceArtifact',
        route_slug='architecture-source-artifact',
        api_basename='architecture-source-artifacts',
        label_singular='Architecture Source Artifact',
        label_plural='Architecture Source Artifacts',
        fields=(
            'workspace',
            'artifact_type',
            'name',
            'source_uri',
            'content_sha256',
            'payload_version',
            'source_label',
            'parser_key',
            'status',
            'raw_payload',
            'parse_result',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'artifact_type', 'name', 'status'),
        form_fields=(
            'workspace',
            'artifact_type',
            'name',
            'source_uri',
            'payload_version',
            'source_label',
            'parser_key',
            'raw_payload',
            'metadata',
        ),
        default_columns=('workspace', 'artifact_type', 'name', 'status'),
    ),
    V2ObjectSpec(
        registry_key='architecturedesigncomponent',
        model=ArchitectureDesignComponent,
        class_prefix='ArchitectureDesignComponent',
        route_slug='architecture-design-component',
        api_basename='architecture-design-components',
        label_singular='Architecture Design Component',
        label_plural='Architecture Design Components',
        fields=(
            'workspace',
            'source_artifact',
            'kind',
            'natural_key',
            'desired_state',
            'provenance',
            'validation_status',
            'validation_messages',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'kind', 'natural_key', 'validation_status'),
        default_columns=('workspace', 'kind', 'natural_key', 'validation_status'),
    ),
    V2ObjectSpec(
        registry_key='architecturevalidationrun',
        model=ArchitectureValidationRun,
        class_prefix='ArchitectureValidationRun',
        route_slug='architecture-validation-run',
        api_basename='architecture-validation-runs',
        label_singular='Architecture Validation Run',
        label_plural='Architecture Validation Runs',
        fields=(
            'workspace',
            'source_artifact',
            'status',
            'validation_kind',
            'workspace_revision',
            'executed_by',
            'summary',
            'issues',
            'import_plan',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'status', 'validation_kind'),
        form_fields=('workspace', 'source_artifact', 'status', 'validation_kind', 'metadata'),
        default_columns=('workspace', 'status', 'validation_kind', 'executed_by'),
    ),
    V2ObjectSpec(
        registry_key='architecturepublishplan',
        model=ArchitecturePublishPlan,
        class_prefix='ArchitecturePublishPlan',
        route_slug='architecture-publish-plan',
        api_basename='architecture-publish-plans',
        label_singular='Architecture Publish Plan',
        label_plural='Architecture Publish Plans',
        fields=(
            'workspace',
            'status',
            'plan_hash',
            'workspace_revision',
            'generated_by',
            'approved_by',
            'approved_at',
            'warning_acknowledgements',
            'publish_payload',
            'validation_summary',
            'import_plan',
            'result',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'status', 'plan_hash', 'approved_by'),
        form_fields=('workspace', 'status', 'warning_acknowledgements', 'metadata'),
        default_columns=('workspace', 'status', 'plan_hash', 'approved_by'),
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
        fields=(
            'fabric',
            'role',
            'parent',
            'name',
            'address',
            'node_kind',
            'local_index',
            'source_type',
            'source_id',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'name', 'address', 'node_kind'),
        detail_fields=(
            'fabric',
            'role',
            'parent',
            'name',
            'address',
            'node_kind',
            'local_index',
            'source',
            'metadata',
        ),
        form_fields=('fabric', 'role', 'parent', 'name', 'address', 'node_kind', 'local_index', 'metadata'),
    ),
    V2ObjectSpec(
        registry_key='endpoint',
        model=Endpoint,
        class_prefix='Endpoint',
        route_slug='endpoint',
        api_basename='endpoints',
        label_singular='Endpoint',
        label_plural='Endpoints',
        fields=(
            'fabric',
            'node',
            'parent',
            'name',
            'address',
            'endpoint_kind',
            'connector_kind',
            'position_count',
            'source_type',
            'source_id',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'name', 'address', 'connector_kind'),
        detail_fields=(
            'fabric',
            'node',
            'parent',
            'name',
            'address',
            'endpoint_kind',
            'connector_kind',
            'position_count',
            'source',
            'metadata',
        ),
        form_fields=(
            'fabric',
            'node',
            'parent',
            'name',
            'address',
            'endpoint_kind',
            'connector_kind',
            'position_count',
            'metadata',
        ),
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
        registry_key='transceiverprofile',
        model=TransceiverProfile,
        class_prefix='TransceiverProfile',
        route_slug='transceiver-profile',
        api_basename='transceiver-profiles',
        label_singular='Transceiver Profile',
        label_plural='Transceiver Profiles',
        fields=(
            'architecture',
            'name',
            'slug',
            'status',
            'form_factor',
            'media_type',
            'aggregate_rate_gbps',
            'channel_count',
            'channel_rate_gbps',
            'wavelength_plan',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'status', 'form_factor', 'media_type'),
        default_columns=('name', 'status', 'form_factor', 'media_type', 'aggregate_rate_gbps'),
    ),
    V2ObjectSpec(
        registry_key='transceiverprofilemoduletype',
        model=TransceiverProfileModuleType,
        class_prefix='TransceiverProfileModuleType',
        route_slug='transceiver-profile-module-type',
        api_basename='transceiver-profile-module-types',
        label_singular='Transceiver Profile Module Type',
        label_plural='Transceiver Profile Module Types',
        fields=('profile', 'module_type', 'is_default', 'role_hint', 'metadata'),
        brief_fields=('id', 'url', 'display', 'profile', 'module_type', 'role_hint', 'is_default'),
        default_columns=('profile', 'module_type', 'role_hint', 'is_default'),
    ),
    V2ObjectSpec(
        registry_key='transceiverconnectorprofile',
        model=TransceiverConnectorProfile,
        class_prefix='TransceiverConnectorProfile',
        route_slug='transceiver-connector-profile',
        api_basename='transceiver-connector-profiles',
        label_singular='Transceiver Connector Profile',
        label_plural='Transceiver Connector Profiles',
        fields=(
            'profile',
            'name',
            'connector_index',
            'connector_family',
            'position_count',
            'polish',
            'pinning',
            'key_orientation',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'profile', 'name', 'connector_family', 'position_count'),
        default_columns=('profile', 'name', 'connector_family', 'position_count', 'polish'),
    ),
    V2ObjectSpec(
        registry_key='transceiverlaneprofile',
        model=TransceiverLaneProfile,
        class_prefix='TransceiverLaneProfile',
        route_slug='transceiver-lane-profile',
        api_basename='transceiver-lane-profiles',
        label_singular='Transceiver Lane Profile',
        label_plural='Transceiver Lane Profiles',
        fields=(
            'connector_profile',
            'channel_index',
            'lane_index',
            'direction',
            'mpo_position',
            'wavelength_nm',
            'nominal_rate_gbps',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'connector_profile', 'channel_index', 'lane_index', 'mpo_position'),
        default_columns=('connector_profile', 'channel_index', 'lane_index', 'direction', 'mpo_position'),
    ),
    V2ObjectSpec(
        registry_key='transceiverconnector',
        model=TransceiverConnector,
        class_prefix='TransceiverConnector',
        route_slug='transceiver-connector',
        api_basename='transceiver-connectors',
        label_singular='Transceiver Connector',
        label_plural='Transceiver Connectors',
        fields=(
            'module',
            'connector_profile',
            'endpoint',
            'connector_family',
            'position_count',
            'polish',
            'pinning',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'module', 'connector_profile', 'endpoint'),
        default_columns=('module', 'connector_profile', 'endpoint', 'polish'),
    ),
    V2ObjectSpec(
        registry_key='transportchannel',
        model=TransportChannel,
        class_prefix='TransportChannel',
        route_slug='transport-channel',
        api_basename='transport-channels',
        label_singular='Transport Channel',
        label_plural='Transport Channels',
        fields=('fabric', 'endpoint', 'plane', 'source_subinterface', 'name', 'channel_index', 'speed_gbps', 'metadata'),
        brief_fields=('id', 'url', 'display', 'fabric', 'endpoint', 'name', 'channel_index'),
    ),
    V2ObjectSpec(
        registry_key='transportchannelpositionmap',
        model=TransportChannelPositionMap,
        class_prefix='TransportChannelPositionMap',
        route_slug='transport-channel-position-map',
        api_basename='transport-channel-position-maps',
        label_singular='Transport Channel Position Map',
        label_plural='Transport Channel Position Maps',
        fields=('channel', 'mpo_endpoint', 'mpo_position', 'metadata'),
        brief_fields=('id', 'url', 'display', 'channel', 'mpo_endpoint', 'mpo_position'),
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
        registry_key='cableassembly',
        model=CableAssembly,
        class_prefix='CableAssembly',
        route_slug='cable-assembly',
        api_basename='cable-assemblies',
        label_singular='Cable Assembly',
        label_plural='Cable Assemblies',
        fields=(
            'site',
            'cable_id',
            'manufacturer',
            'serial_number',
            'model_id',
            'description',
            'parent_cable',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'site', 'cable_id', 'manufacturer', 'model_id'),
    ),
    V2ObjectSpec(
        registry_key='fiberstrand',
        model=FiberStrand,
        class_prefix='FiberStrand',
        route_slug='fiber-strand',
        api_basename='fiber-strands',
        label_singular='Fiber Strand',
        label_plural='Fiber Strands',
        fields=('segment', 'strand_index', 'cable_site', 'cable_id', 'label', 'metadata'),
        brief_fields=('id', 'url', 'display', 'segment', 'strand_index', 'cable_id', 'label'),
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
    V2ObjectSpec(
        registry_key='suppressionrule',
        model=SuppressionRule,
        class_prefix='SuppressionRule',
        route_slug='suppression-rule',
        api_basename='suppression-rules',
        label_singular='Suppression Rule',
        label_plural='Suppression Rules',
        fields=(
            'fabric',
            'plane',
            'optical_lane',
            'path_hop_object_type',
            'path_hop_object_id',
            'policy_key',
            'status',
            'reason',
            'created_by',
            'approved_by',
            'approved_at',
            'expires_at',
            'revoked_at',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'status', 'policy_key', 'optical_lane', 'plane'),
    ),
    V2ObjectSpec(
        registry_key='auditevent',
        model=AuditEvent,
        class_prefix='AuditEvent',
        route_slug='audit-event',
        api_basename='audit-events',
        label_singular='Audit Event',
        label_plural='Audit Events',
        fields=(
            'fabric',
            'event_type',
            'actor',
            'subject_type',
            'subject_id',
            'outcome',
            'message',
            'payload',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'fabric', 'event_type', 'outcome', 'actor'),
        form_fields=('fabric', 'event_type', 'actor', 'subject_type', 'subject_id', 'outcome', 'message', 'payload', 'metadata'),
    ),
    V2ObjectSpec(
        registry_key='operationrun',
        model=OperationRun,
        class_prefix='OperationRun',
        route_slug='operation-run',
        api_basename='operation-runs',
        label_singular='Operation Run',
        label_plural='Operation Runs',
        fields=(
            'profile',
            'status',
            'fabric',
            'initiated_by',
            'dedupe_key',
            'parameters',
            'result',
            'error_detail',
            'metadata',
            'started_at',
            'completed_at',
        ),
        brief_fields=('id', 'url', 'display', 'profile', 'status', 'fabric', 'initiated_by'),
        form_fields=('profile', 'fabric', 'parameters', 'metadata'),
    ),
    V2ObjectSpec(
        registry_key='onboardingworkspace',
        model=OnboardingWorkspace,
        class_prefix='OnboardingWorkspace',
        route_slug='onboarding-workspace',
        api_basename='onboarding-workspaces',
        label_singular='Onboarding Workspace',
        label_plural='Onboarding Workspaces',
        fields=(
            'name',
            'slug',
            'status',
            'fabric_class',
            'target_fabric_name',
            'target_fabric_slug',
            'fabric',
            'architecture',
            'site',
            'location',
            'tenant',
            'created_by',
            'owner',
            'current_plan',
            'source_summary',
            'readiness_summary',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'name', 'slug', 'status', 'site', 'architecture'),
        form_fields=(
            'name',
            'slug',
            'status',
            'fabric_class',
            'target_fabric_name',
            'target_fabric_slug',
            'fabric',
            'architecture',
            'site',
            'location',
            'tenant',
            'owner',
            'metadata',
        ),
        default_columns=('name', 'status', 'target_fabric_slug', 'site', 'architecture'),
    ),
    V2ObjectSpec(
        registry_key='onboardingsourceartifact',
        model=OnboardingSourceArtifact,
        class_prefix='OnboardingSourceArtifact',
        route_slug='onboarding-source-artifact',
        api_basename='onboarding-source-artifacts',
        label_singular='Onboarding Source Artifact',
        label_plural='Onboarding Source Artifacts',
        fields=(
            'workspace',
            'artifact_type',
            'name',
            'source_uri',
            'content_sha256',
            'payload_version',
            'source_label',
            'parser_key',
            'status',
            'raw_payload',
            'parse_result',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'artifact_type', 'name', 'status'),
        form_fields=(
            'workspace',
            'artifact_type',
            'name',
            'source_uri',
            'payload_version',
            'source_label',
            'parser_key',
            'raw_payload',
            'metadata',
        ),
        default_columns=('workspace', 'artifact_type', 'name', 'status'),
    ),
    V2ObjectSpec(
        registry_key='onboardingdesignitem',
        model=OnboardingDesignItem,
        class_prefix='OnboardingDesignItem',
        route_slug='onboarding-design-item',
        api_basename='onboarding-design-items',
        label_singular='Onboarding Design Item',
        label_plural='Onboarding Design Items',
        fields=(
            'workspace',
            'source_artifact',
            'kind',
            'natural_key',
            'desired_state',
            'provenance',
            'validation_status',
            'validation_messages',
            'planned_object_type',
            'planned_object_id',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'kind', 'natural_key', 'validation_status'),
        default_columns=('workspace', 'kind', 'natural_key', 'validation_status'),
    ),
    V2ObjectSpec(
        registry_key='onboardingprerequisite',
        model=OnboardingPrerequisite,
        class_prefix='OnboardingPrerequisite',
        route_slug='onboarding-prerequisite',
        api_basename='onboarding-prerequisites',
        label_singular='Onboarding Prerequisite',
        label_plural='Onboarding Prerequisites',
        fields=(
            'workspace',
            'design_item',
            'requirement_key',
            'object_model',
            'role',
            'desired_identity',
            'resolution_mode',
            'resolved_object_type',
            'resolved_object_id',
            'planned_create',
            'defer_reason',
            'status',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'requirement_key', 'status', 'resolution_mode'),
        default_columns=('workspace', 'requirement_key', 'object_model', 'status', 'resolution_mode'),
    ),
    V2ObjectSpec(
        registry_key='onboardingplan',
        model=OnboardingPlan,
        class_prefix='OnboardingPlan',
        route_slug='onboarding-plan',
        api_basename='onboarding-plans',
        label_singular='Onboarding Plan',
        label_plural='Onboarding Plans',
        fields=(
            'workspace',
            'status',
            'plan_hash',
            'workspace_revision',
            'generated_by',
            'approved_by',
            'approved_at',
            'warning_acknowledgements',
            'prerequisite_plan',
            'stamp_preview',
            'import_plan',
            'audit_projection',
            'impact_projection',
            'readiness_projection',
            'rollback_preview',
            'retry_preview',
            'plan_payload',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'status', 'plan_hash', 'approved_by'),
        form_fields=('workspace', 'status', 'warning_acknowledgements', 'metadata'),
        default_columns=('workspace', 'status', 'plan_hash', 'approved_by'),
    ),
    V2ObjectSpec(
        registry_key='onboardingexecutionstage',
        model=OnboardingExecutionStage,
        class_prefix='OnboardingExecutionStage',
        route_slug='onboarding-execution-stage',
        api_basename='onboarding-execution-stages',
        label_singular='Onboarding Execution Stage',
        label_plural='Onboarding Execution Stages',
        fields=(
            'plan',
            'stage_key',
            'stage_kind',
            'status',
            'started_at',
            'completed_at',
            'operation_run',
            'stamp_run',
            'result',
            'error_detail',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'plan', 'stage_key', 'stage_kind', 'status'),
        form_fields=('plan', 'stage_key', 'stage_kind', 'status', 'metadata'),
        default_columns=('plan', 'stage_key', 'stage_kind', 'status'),
    ),
    V2ObjectSpec(
        registry_key='onboardingobjectlink',
        model=OnboardingObjectLink,
        class_prefix='OnboardingObjectLink',
        route_slug='onboarding-object-link',
        api_basename='onboarding-object-links',
        label_singular='Onboarding Object Link',
        label_plural='Onboarding Object Links',
        fields=(
            'workspace',
            'plan',
            'stage',
            'design_item',
            'source_artifact',
            'link_kind',
            'object_type',
            'object_id',
            'label',
            'external_url',
            'metadata',
        ),
        brief_fields=('id', 'url', 'display', 'workspace', 'link_kind', 'label'),
        default_columns=('workspace', 'link_kind', 'label', 'object_type', 'object_id'),
    ),
)

V2_OBJECT_SPEC_BY_KEY = {spec.registry_key: spec for spec in V2_OBJECT_SPECS}
V2_OBJECT_SPEC_BY_MODEL = {spec.model: spec for spec in V2_OBJECT_SPECS}


def get_v2_object_spec(registry_key: str) -> V2ObjectSpec:
    return V2_OBJECT_SPEC_BY_KEY[registry_key]


def get_v2_object_spec_for_model(model: type) -> V2ObjectSpec:
    return V2_OBJECT_SPEC_BY_MODEL[model]
