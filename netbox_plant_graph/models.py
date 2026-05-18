from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from .choices import (
    AssemblyConnectorSideChoices,
    AssemblyConnectorTypeChoices,
    AssemblyMappingTypeChoices,
    AssemblyTypeChoices,
    AttachmentUnitTypeChoices,
    AuditFindingEventTypeChoices,
    AuditFindingStatusChoices,
    AuditRunStatusChoices,
    AuditRunTriggerModeChoices,
    BreakoutProfileMappingModeChoices,
    CoarseEdgeTypeChoices,
    CoordinateUnitChoices,
    DeploymentPlanStatusChoices,
    DisjointnessExceptionScopeChoices,
    DisjointnessExceptionStatusChoices,
    DisjointnessExceptionTypeChoices,
    DisjointnessChoices,
    FineEdgeTypeChoices,
    GraphResolutionChoices,
    GraphBuildRunStatusChoices,
    GraphBuildRunTriggerModeChoices,
    LaneMapTypeChoices,
    PlaneMembershipRoleChoices,
    PlantNodeTypeChoices,
    RackFaceChoices,
    SignalEncodingChoices,
    SignalLaneKindChoices,
    SpatialNodeTypeChoices,
    StampRecordStatusChoices,
    TerminationPointTypeChoices,
    TransferMapTypeChoices,
    UnresolvedStateCauseChoices,
    UnresolvedStateSummaryKindChoices,
)

TENANT_RESOLUTION_PATHS = {
    'fabricplane': ('fabric',),
    'plantnode': ('fabric',),     # also has direct tenant FK; fallback remains for fabric-level resolution
    'terminationpoint': ('plant_node',),
    'attachmentunit': ('termination_point',),
    'signallane': ('attachment_unit',),
    'coarseedge': ('a_tp', 'b_tp'),
    'fineedge': ('a_lane', 'b_lane', 'a_au', 'b_au', 'parent_coarse_edge'),
    'transfermap': ('owner_node', 'src_attachment_unit', 'dst_attachment_unit'),
    'lanemap': ('owner_node', 'owner_edge', 'src_lane', 'dst_lane'),
    'planemembership': ('plane', 'member'),
    'disjointnessexception': ('fabric',),
    'auditrun': ('fabric', 'scope'),
    'graphbuildrun': ('fabric', 'scope'),
    'unresolvedstatesummary': ('fabric', 'owner_object', 'representative_object'),
    'unresolvedstateobservation': ('summary', 'build', 'related_audit_run'),
    'auditfinding': ('fabric', 'plane', 'object'),
    'auditfindingevent': ('finding', 'run'),
    'auditsuppression': ('finding',),
    'assemblyconnectortemplate': ('template',),
    'assemblymappingtemplate': ('template', 'a_connector', 'b_connector'),
    'spatialplacement': ('target', 'reference_frame'),
    'spatialtemplatenode': ('template', 'rack_population_template'),
    'deploymentplan': ('fabric',),
    'stamprecord': ('plan', 'template', 'result'),
    'rackpopulationslot': ('template', 'assembly_template'),
    'devicechildinterfacespec': ('breakout_template',),
}


def _resolve_tenant_from_object(obj):
    if obj is None:
        return None

    tenant = getattr(obj, 'tenant', None)
    if tenant is not None:
        return tenant

    resolved_tenant = getattr(obj, 'resolved_tenant', None)
    if resolved_tenant is not None:
        return resolved_tenant

    return None


class RegistryModelMixin(NetBoxModel):
    registry_key: str = ''

    class Meta:
        abstract = True

    @classmethod
    def _get_action_url(cls, action=None, rest_api=False, kwargs=None):
        from .object_registry import get_object_spec

        spec = get_object_spec(cls.registry_key)
        if rest_api:
            view_name = spec.api.detail_view_name
            if action == 'list':
                view_name = f'plugins-api:netbox_plant_graph-api:{spec.api.basename}-list'
            return reverse(view_name, kwargs=kwargs)

        route_slug = spec.routes.slug
        if action:
            if action == 'list':
                view_name = f'plugins:netbox_plant_graph:{route_slug}_list'
            else:
                view_name = f'plugins:netbox_plant_graph:{route_slug}_{action}'
        else:
            view_name = f'plugins:netbox_plant_graph:{route_slug}'
        return reverse(view_name, kwargs=kwargs)

    def get_absolute_url(self):
        return self._get_action_url(kwargs={'pk': self.pk})

    @property
    def resolved_tenant(self):
        tenant = getattr(self, 'tenant', None)
        if tenant is not None:
            return tenant

        for path in TENANT_RESOLUTION_PATHS.get(self.registry_key, ()):
            related_obj = self
            for attr in path.split('.'):
                related_obj = getattr(related_obj, attr, None)
                if related_obj is None:
                    break
            tenant = _resolve_tenant_from_object(related_obj)
            if tenant is not None:
                return tenant

        return None


class Fabric(RegistryModelMixin):
    registry_key = 'fabric'

    name = models.CharField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    expected_plane_count = models.PositiveIntegerField(default=4)
    tier_depth = models.PositiveIntegerField(default=3)
    disjointness_policy = models.CharField(max_length=50, choices=DisjointnessChoices, default='full')
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    scope_site = models.ForeignKey('dcim.Site', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    scope_location = models.ForeignKey('dcim.Location', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    tier_role_map = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Map of NetBox device role slug → fabric tier level (integer, 0-indexed). '
            'Example: {"roce-leaf-switch": 0, "roce-spine-switch": 1}. '
            'Used by the transformer to classify PlantNode tier depth.'
        ),
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name


class FabricPlane(RegistryModelMixin):
    registry_key = 'fabricplane'

    fabric = models.ForeignKey('Fabric', related_name='planes', on_delete=models.CASCADE)
    plane_number = models.PositiveIntegerField()
    description = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'plane_number')
        unique_together = ('fabric', 'plane_number')

    def __str__(self) -> str:
        return f'{self.fabric}:{self.plane_number}'


class PlantNode(RegistryModelMixin):
    registry_key = 'plantnode'

    fabric = models.ForeignKey('Fabric', related_name='plant_nodes', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    node_type = models.CharField(max_length=50, choices=PlantNodeTypeChoices, default='device')
    role = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50, blank=True)
    tenant = models.ForeignKey(
        'tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    location_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    location_id = models.PositiveBigIntegerField(null=True, blank=True)
    location = GenericForeignKey('location_type', 'location_id')
    source_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_type', 'source_id')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'name')
        unique_together = ('fabric', 'name')

    def __str__(self) -> str:
        return self.name


class TerminationPoint(RegistryModelMixin):
    registry_key = 'terminationpoint'

    plant_node = models.ForeignKey('PlantNode', related_name='termination_points', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    tp_type = models.CharField(max_length=50, choices=TerminationPointTypeChoices, default='interface')
    connector_type = models.CharField(max_length=100, blank=True)
    channel_capacity = models.PositiveIntegerField(default=0)
    speed_gbps = models.PositiveIntegerField(null=True, blank=True)
    source_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_type', 'source_id')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('plant_node', 'name')
        unique_together = ('plant_node', 'name')

    def __str__(self) -> str:
        return self.name


class AttachmentUnit(RegistryModelMixin):
    registry_key = 'attachmentunit'

    termination_point = models.ForeignKey('TerminationPoint', related_name='attachment_units', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    ordinal = models.PositiveIntegerField(default=0)
    unit_type = models.CharField(max_length=50, choices=AttachmentUnitTypeChoices, default='child_interface')
    speed_gbps = models.PositiveIntegerField(null=True, blank=True)
    topology_role = models.CharField(max_length=50, blank=True)
    active = models.BooleanField(default=True)
    source_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_type', 'source_id')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('termination_point', 'ordinal', 'name')
        unique_together = ('termination_point', 'ordinal')

    def __str__(self) -> str:
        return self.name


class SignalLane(RegistryModelMixin):
    registry_key = 'signallane'

    attachment_unit = models.ForeignKey('AttachmentUnit', related_name='signal_lanes', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    lane_index = models.PositiveIntegerField(default=0)
    lane_kind = models.CharField(max_length=50, choices=SignalLaneKindChoices, default='electrical_tx')
    signaling = models.CharField(max_length=32, choices=SignalEncodingChoices, default='pam4')
    nominal_rate_gbps = models.PositiveIntegerField(null=True, blank=True)
    direction_role = models.CharField(max_length=50, blank=True)
    wavelength_group = models.CharField(max_length=64, blank=True)
    source_anchor = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('attachment_unit', 'lane_index')
        unique_together = ('attachment_unit', 'lane_index')

    def __str__(self) -> str:
        return self.name


class CoarseEdge(RegistryModelMixin):
    registry_key = 'coarseedge'

    edge_type = models.CharField(max_length=50, choices=CoarseEdgeTypeChoices, default='cable')
    a_tp = models.ForeignKey('TerminationPoint', related_name='coarse_edges_a', on_delete=models.CASCADE)
    b_tp = models.ForeignKey('TerminationPoint', related_name='coarse_edges_b', on_delete=models.CASCADE)
    source_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_type', 'source_id')
    cable_profile_name = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('pk',)

    def __str__(self) -> str:
        return f'{self.a_tp} -> {self.b_tp}'

    def clean(self):
        super().clean()
        if self.a_tp_id and self.b_tp_id and self.a_tp_id == self.b_tp_id:
            raise ValidationError({'b_tp': 'A coarse edge must connect two distinct termination points.'})


class FineEdge(RegistryModelMixin):
    registry_key = 'fineedge'

    granularity = models.CharField(max_length=32, choices=GraphResolutionChoices)
    edge_type = models.CharField(max_length=50, choices=FineEdgeTypeChoices, default='derived_cable_segment')
    a_au = models.ForeignKey('AttachmentUnit', null=True, blank=True, related_name='fine_edges_a_au', on_delete=models.CASCADE)
    b_au = models.ForeignKey('AttachmentUnit', null=True, blank=True, related_name='fine_edges_b_au', on_delete=models.CASCADE)
    a_lane = models.ForeignKey('SignalLane', null=True, blank=True, related_name='fine_edges_a_lane', on_delete=models.CASCADE)
    b_lane = models.ForeignKey('SignalLane', null=True, blank=True, related_name='fine_edges_b_lane', on_delete=models.CASCADE)
    parent_coarse_edge = models.ForeignKey('CoarseEdge', null=True, blank=True, related_name='fine_edges', on_delete=models.CASCADE)
    derived_from_profile = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('pk',)

    def __str__(self) -> str:
        if self.granularity == 'signal_lane':
            return f'{self.a_lane} -> {self.b_lane}'
        return f'{self.a_au} -> {self.b_au}'

    def clean(self):
        super().clean()
        if self.granularity == 'attachment_unit' and (self.a_lane_id or self.b_lane_id):
            raise ValidationError('Attachment-unit fine edges cannot populate lane endpoints.')
        if self.granularity == 'signal_lane' and not (self.a_lane_id and self.b_lane_id):
            raise ValidationError('Signal-lane fine edges must populate both lane endpoints.')
        if self.granularity == 'attachment_unit' and not (self.a_au_id and self.b_au_id):
            raise ValidationError('Attachment-unit fine edges must populate both attachment-unit endpoints.')


class TransferMap(RegistryModelMixin):
    registry_key = 'transfermap'

    owner_node = models.ForeignKey('PlantNode', related_name='transfer_maps', on_delete=models.CASCADE)
    src_attachment_unit = models.ForeignKey('AttachmentUnit', related_name='transfer_map_sources', on_delete=models.CASCADE)
    dst_attachment_unit = models.ForeignKey('AttachmentUnit', related_name='transfer_map_destinations', on_delete=models.CASCADE)
    mapping_type = models.CharField(max_length=50, choices=TransferMapTypeChoices, default='identity')
    source_port_mapping = models.PositiveBigIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('owner_node', 'pk')

    def __str__(self) -> str:
        return f'{self.src_attachment_unit} -> {self.dst_attachment_unit}'


class LaneMap(RegistryModelMixin):
    registry_key = 'lanemap'

    owner_node = models.ForeignKey('PlantNode', null=True, blank=True, related_name='lane_maps', on_delete=models.CASCADE)
    owner_edge = models.ForeignKey('CoarseEdge', null=True, blank=True, related_name='lane_maps', on_delete=models.CASCADE)
    src_lane = models.ForeignKey('SignalLane', related_name='lane_map_sources', on_delete=models.CASCADE)
    dst_lane = models.ForeignKey('SignalLane', related_name='lane_map_destinations', on_delete=models.CASCADE)
    mapping_type = models.CharField(max_length=50, choices=LaneMapTypeChoices, default='identity')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('pk',)

    def __str__(self) -> str:
        return f'{self.src_lane} -> {self.dst_lane}'


class PlaneMembership(RegistryModelMixin):
    registry_key = 'planemembership'

    plane = models.ForeignKey('FabricPlane', related_name='memberships', on_delete=models.CASCADE)
    member_type = models.ForeignKey(ContentType, related_name='+', on_delete=models.CASCADE)
    member_id = models.PositiveBigIntegerField()
    member = GenericForeignKey('member_type', 'member_id')
    membership_role = models.CharField(max_length=50, choices=PlaneMembershipRoleChoices, default='native')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('plane', 'member_type', 'member_id')
        unique_together = ('plane', 'member_type', 'member_id', 'membership_role')

    def __str__(self) -> str:
        return f'{self.plane}:{self.member}'


class DisjointnessException(RegistryModelMixin):
    registry_key = 'disjointnessexception'

    fabric = models.ForeignKey('Fabric', related_name='disjointness_exceptions', on_delete=models.CASCADE)
    policy_mode = models.CharField(max_length=50, choices=DisjointnessChoices, blank=True)
    exception_type = models.CharField(
        max_length=64,
        choices=DisjointnessExceptionTypeChoices,
        default=DisjointnessExceptionTypeChoices.CHOICES[0][0],
    )
    target_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    target = GenericForeignKey('target_type', 'target_id')
    plane_a = models.ForeignKey('FabricPlane', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    plane_b = models.ForeignKey('FabricPlane', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    scope_kind = models.CharField(
        max_length=32,
        choices=DisjointnessExceptionScopeChoices,
        default=DisjointnessExceptionScopeChoices.CHOICES[0][0],
    )
    lane_selector = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=DisjointnessExceptionStatusChoices,
        default=DisjointnessExceptionStatusChoices.CHOICES[0][0],
        db_index=True,
    )
    active = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', '-created', '-pk')

    def __str__(self) -> str:
        target = self.target or f'{self.target_type}:{self.target_id}'
        return f'{self.exception_type} exception for {target}'

    def clean(self):
        super().clean()

        if bool(self.target_type_id) != bool(self.target_id):
            raise ValidationError({'target_id': 'Target type and target ID must both be set together.'})

        if self.plane_a_id is None or self.plane_b_id is None:
            raise ValidationError({'plane_b': 'Plane A and Plane B are both required.'})

        if self.plane_a_id == self.plane_b_id:
            raise ValidationError({'plane_b': 'Plane A and Plane B must be distinct.'})

        for field_name in ('plane_a', 'plane_b'):
            plane = getattr(self, field_name)
            if plane is not None and plane.fabric_id != self.fabric_id:
                raise ValidationError({field_name: 'Selected plane must belong to the same fabric as the exception.'})

        if self.status == 'approved' and not self.active:
            raise ValidationError({'active': 'Approved exceptions must be active.'})
        if self.status in {'draft', 'expired'} and self.active:
            raise ValidationError({'active': 'Only approved exceptions may be active.'})

    @property
    def effective_status(self) -> str:
        if self.status == 'approved' and self.expires_at is not None:
            from django.utils import timezone

            if self.expires_at <= timezone.now():
                return 'expired'
        return self.status

    @property
    def plane_pair_ids(self) -> tuple[int, int] | tuple[()]:
        if self.plane_a_id and self.plane_b_id:
            return tuple(sorted((self.plane_a_id, self.plane_b_id)))
        return ()


class AuditRun(RegistryModelMixin):
    registry_key = 'auditrun'

    fabric = models.ForeignKey('Fabric', related_name='audit_runs', on_delete=models.CASCADE)
    scope_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    scope_id = models.PositiveBigIntegerField(null=True, blank=True)
    scope = GenericForeignKey('scope_type', 'scope_id')
    scope_label = models.CharField(max_length=200, blank=True)
    trigger_mode = models.CharField(
        max_length=32,
        choices=AuditRunTriggerModeChoices,
        default=AuditRunTriggerModeChoices.CHOICES[0][0],
    )
    status = models.CharField(
        max_length=32,
        choices=AuditRunStatusChoices,
        default=AuditRunStatusChoices.CHOICES[0][0],
    )
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finding_count = models.PositiveIntegerField(default=0)
    new_count = models.PositiveIntegerField(default=0)
    reopened_count = models.PositiveIntegerField(default=0)
    resolved_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-started_at', '-pk')

    def __str__(self) -> str:
        if self.started_at is not None:
            return f'{self.fabric} audit @ {self.started_at.isoformat()}'
        return f'{self.fabric} audit #{self.pk}'


class GraphBuildRun(RegistryModelMixin):
    registry_key = 'graphbuildrun'

    fabric = models.ForeignKey('Fabric', null=True, blank=True, related_name='graph_build_runs', on_delete=models.CASCADE)
    scope_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    scope_id = models.PositiveBigIntegerField(null=True, blank=True)
    scope = GenericForeignKey('scope_type', 'scope_id')
    scope_label = models.CharField(max_length=200, blank=True)
    trigger_mode = models.CharField(
        max_length=32,
        choices=GraphBuildRunTriggerModeChoices,
        default=GraphBuildRunTriggerModeChoices.CHOICES[0][0],
    )
    status = models.CharField(
        max_length=32,
        choices=GraphBuildRunStatusChoices,
        default=GraphBuildRunStatusChoices.CHOICES[0][0],
    )
    started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    stats = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-started_at', '-pk')
        indexes = (
            models.Index(fields=('fabric', 'status', 'started_at')),
            models.Index(fields=('fabric', 'completed_at')),
            models.Index(fields=('trigger_mode', 'started_at')),
        )

    def __str__(self) -> str:
        fabric_label = str(self.fabric) if self.fabric is not None else 'Unscoped'
        if self.started_at is not None:
            return f'{fabric_label} build @ {self.started_at.isoformat()}'
        return f'{fabric_label} build #{self.pk}'


class UnresolvedStateSummary(RegistryModelMixin):
    registry_key = 'unresolvedstatesummary'

    fabric = models.ForeignKey('Fabric', related_name='unresolved_state_summaries', on_delete=models.CASCADE)
    plane = models.ForeignKey('FabricPlane', null=True, blank=True, related_name='unresolved_state_summaries', on_delete=models.SET_NULL)
    scope_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    scope_id = models.PositiveBigIntegerField(null=True, blank=True)
    scope = GenericForeignKey('scope_type', 'scope_id')
    scope_label = models.CharField(max_length=200, blank=True)
    summary_kind = models.CharField(
        max_length=32,
        choices=UnresolvedStateSummaryKindChoices.CHOICES,
        default=UnresolvedStateSummaryKindChoices.CHOICES[0][0],
    )
    cause_code = models.CharField(
        max_length=64,
        choices=UnresolvedStateCauseChoices.CHOICES,
        default=UnresolvedStateCauseChoices.CHOICES[0][0],
    )
    fingerprint = models.CharField(max_length=128, db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    first_seen_build = models.ForeignKey('GraphBuildRun', null=True, blank=True, related_name='first_seen_unresolved_summaries', on_delete=models.SET_NULL)
    last_seen_build = models.ForeignKey('GraphBuildRun', null=True, blank=True, related_name='last_seen_unresolved_summaries', on_delete=models.SET_NULL)
    first_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    owner_object_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    owner_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    owner_object = GenericForeignKey('owner_object_type', 'owner_object_id')
    representative_object_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    representative_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    representative_object = GenericForeignKey('representative_object_type', 'representative_object_id')
    plane_ids = models.JSONField(default=list, blank=True)
    selector = models.JSONField(default=dict, blank=True)
    anchors = models.JSONField(default=dict, blank=True)
    impact = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('cause_code', '-last_seen_at', '-pk')
        constraints = (
            models.UniqueConstraint(
                fields=('fabric', 'scope_type', 'scope_id', 'fingerprint'),
                name='netbox_plant_graph_unresolved_summary_scope_fingerprint_uniq',
            ),
        )
        indexes = (
            models.Index(fields=('fabric', 'active', 'last_seen_at')),
            models.Index(fields=('fabric', 'cause_code', 'active')),
            models.Index(fields=('owner_object_type', 'owner_object_id', 'active')),
            models.Index(fields=('representative_object_type', 'representative_object_id')),
        )

    def __str__(self) -> str:
        owner_label = self.scope_label or str(self.owner_object or self.fabric)
        return f'{self.cause_code}: {owner_label}'


class UnresolvedStateObservation(RegistryModelMixin):
    registry_key = 'unresolvedstateobservation'

    summary = models.ForeignKey('UnresolvedStateSummary', related_name='observations', on_delete=models.CASCADE)
    build = models.ForeignKey('GraphBuildRun', null=True, blank=True, related_name='unresolved_state_observations', on_delete=models.SET_NULL)
    observed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    impact = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    related_audit_run = models.ForeignKey('AuditRun', null=True, blank=True, related_name='unresolved_state_observations', on_delete=models.SET_NULL)

    class Meta:
        ordering = ('-observed_at', '-pk')
        constraints = (
            models.UniqueConstraint(
                fields=('summary', 'build'),
                name='netbox_plant_graph_unresolved_observation_summary_build_uniq',
            ),
        )
        indexes = (
            models.Index(fields=('build', 'observed_at')),
            models.Index(fields=('summary', 'observed_at')),
        )

    def __str__(self) -> str:
        if self.observed_at is not None:
            return f'{self.summary} @ {self.observed_at.isoformat()}'
        return f'{self.summary} observation #{self.pk}'


class AuditFinding(RegistryModelMixin):
    registry_key = 'auditfinding'

    fabric = models.ForeignKey('Fabric', null=True, blank=True, related_name='audit_findings', on_delete=models.CASCADE)
    plane = models.ForeignKey('FabricPlane', null=True, blank=True, related_name='audit_findings', on_delete=models.SET_NULL)
    scope_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    scope_id = models.PositiveBigIntegerField(null=True, blank=True)
    scope = GenericForeignKey('scope_type', 'scope_id')
    fingerprint = models.CharField(max_length=128, blank=True, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=AuditFindingStatusChoices,
        default=AuditFindingStatusChoices.CHOICES[0][0],
    )
    active = models.BooleanField(default=True, db_index=True)
    finding_type = models.CharField(max_length=100)
    severity = models.CharField(max_length=32)
    object_type = models.ForeignKey(ContentType, related_name='+', on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    object = GenericForeignKey('object_type', 'object_id')
    message = models.TextField()
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    acknowledged_at = models.DateTimeField(null=True, blank=True, db_index=True)
    first_seen_run = models.ForeignKey('AuditRun', null=True, blank=True, related_name='first_seen_findings', on_delete=models.SET_NULL)
    last_seen_run = models.ForeignKey('AuditRun', null=True, blank=True, related_name='last_seen_findings', on_delete=models.SET_NULL)
    first_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolution_summary = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('severity', 'finding_type', 'pk')

    def __str__(self) -> str:
        return f'{self.finding_type}: {self.message[:80]}'


class AuditFindingEvent(RegistryModelMixin):
    registry_key = 'auditfindingevent'

    finding = models.ForeignKey('AuditFinding', related_name='events', on_delete=models.CASCADE)
    run = models.ForeignKey('AuditRun', null=True, blank=True, related_name='events', on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=50, choices=AuditFindingEventTypeChoices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    old_status = models.CharField(max_length=32, blank=True)
    new_status = models.CharField(max_length=32, blank=True)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self) -> str:
        return f'{self.finding} [{self.event_type}]'


class AuditSuppression(RegistryModelMixin):
    registry_key = 'auditsuppression'

    finding = models.ForeignKey('AuditFinding', related_name='suppressions', on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    reason = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self) -> str:
        return f'{self.finding} suppression'


# --- Breakout Profiles ---


class BreakoutProfile(RegistryModelMixin):
    """
    A DB-backed cable breakout profile. Cable-level breakout assignment is
    carried via the plugin-managed Cable object custom field.
    """
    registry_key = 'breakoutprofile'

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    parent_speed_gbps = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Nominal parent port speed in Gbps (informational).',
    )
    child_count = models.PositiveIntegerField(default=4)
    child_speed_gbps = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Nominal child channel speed in Gbps (informational).',
    )
    mapping_mode = models.CharField(
        max_length=20,
        choices=BreakoutProfileMappingModeChoices,
        default='sequential',
    )
    position_map = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'For "explicit" mode only.  '
            'Map of 1-indexed parent position (string key) to 0-indexed child ordinal. '
            'Example: {"1": 0, "2": 1, "3": 2, "4": 3}.'
        ),
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name

    def get_child_ordinal(self, parent_position: int) -> int | None:
        """
        Map a 1-indexed parent cable position to a 0-indexed child ordinal.
        Returns None if the position has no mapping.
        """
        if self.mapping_mode == 'sequential':
            if 1 <= parent_position <= self.child_count:
                return parent_position - 1
            return None
        return self.position_map.get(str(parent_position))


# --- Device Breakout Templates ---


class DeviceBreakoutTemplate(RegistryModelMixin):
    """
    Reusable template that drives child interface auto-creation during
    ``stamp_rack_population``.  Each template owns one or more
    ``DeviceChildInterfaceSpec`` rows.
    """
    registry_key = 'devicebreakouttemplate'

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    device_type = models.ForeignKey(
        'dcim.DeviceType', null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
        help_text='Intended device type (informational, not enforced at stamp time).',
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name


class DeviceChildInterfaceSpec(RegistryModelMixin):
    """
    One breakout specification within a ``DeviceBreakoutTemplate``.
    Describes how to create N child interfaces under a named parent interface.
    """
    registry_key = 'devicechildinterfacespec'

    breakout_template = models.ForeignKey(
        'DeviceBreakoutTemplate', related_name='child_specs', on_delete=models.CASCADE
    )
    parent_interface_name = models.CharField(
        max_length=200,
        help_text='Exact name of the parent interface as it appears on the Device.',
    )
    child_name_pattern = models.CharField(
        max_length=200,
        help_text=(
            'Python str.format pattern.  Available vars: '
            '{parent} (parent interface name), {n} (1-based child number), '
            '{plane} (fabric plane number).  '
            'Example: \"{parent}.plane{plane}\" \u2192 \"NIC0.plane1\".'
        ),
    )
    child_count = models.PositiveIntegerField(default=4)
    child_interface_type = models.CharField(
        max_length=50, default='virtual',
        help_text='NetBox interface type slug (e.g. "virtual", "200gbase-cr4").',
    )
    child_speed_kbps = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Speed in kbps as stored by NetBox Interface.speed (e.g. 200000000 for 200G).',
    )
    fabric_plane_start = models.PositiveIntegerField(
        default=1,
        help_text='Child 0 gets fabric_plane = fabric_plane_start, child 1 gets +1, etc.',
    )
    breakout_profile = models.ForeignKey(
        'BreakoutProfile', null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
        help_text=(
            'BreakoutProfile governing cable-segment FineEdge derivation for cables '
            'connected to this parent interface.'
        ),
    )
    sort_order = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('breakout_template', 'sort_order', 'parent_interface_name')
        unique_together = ('breakout_template', 'parent_interface_name')

    def __str__(self) -> str:
        return f'{self.breakout_template}: {self.parent_interface_name} \u2192 {self.child_count}\xd7 children'

    def generate_child_name(self, child_index: int) -> str:
        """child_index is 0-based."""
        plane = self.fabric_plane_start + child_index
        n = child_index + 1
        return self.child_name_pattern.format(
            parent=self.parent_interface_name,
            n=n,
            plane=plane,
        )


# --- Planning / Assembly Templates ---


class AssemblyTemplate(RegistryModelMixin):
    registry_key = 'assemblytemplate'

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    assembly_type = models.CharField(max_length=50, choices=AssemblyTypeChoices, default='shuffle_trunk')
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    manufacturer = models.ForeignKey(
        'dcim.Manufacturer', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    part_number = models.CharField(max_length=100, blank=True)
    device_type = models.ForeignKey(
        'dcim.DeviceType', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    cable_profile_hint = models.CharField(max_length=50, blank=True)
    breakout_profile = models.ForeignKey(
        'BreakoutProfile', null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
        help_text='DB-backed breakout profile used when stamping cables from this assembly template.',
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name

    @property
    def a_connectors(self):
        return self.connectors.filter(side='A').order_by('connector_number')

    @property
    def b_connectors(self):
        return self.connectors.filter(side='B').order_by('connector_number')

    @property
    def total_a_positions(self) -> int:
        return sum(c.position_count for c in self.a_connectors)

    @property
    def total_b_positions(self) -> int:
        return sum(c.position_count for c in self.b_connectors)


class AssemblyConnectorTemplate(RegistryModelMixin):
    registry_key = 'assemblyconnectortemplate'

    template = models.ForeignKey('AssemblyTemplate', related_name='connectors', on_delete=models.CASCADE)
    side = models.CharField(max_length=1, choices=AssemblyConnectorSideChoices)
    connector_number = models.PositiveIntegerField()
    connector_type = models.CharField(max_length=50, choices=AssemblyConnectorTypeChoices, default='mpo-12')
    position_count = models.PositiveIntegerField(default=8)
    label = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('template', 'side', 'connector_number')
        constraints = (
            models.UniqueConstraint(
                fields=('template', 'side', 'connector_number'),
                name='netbox_plant_graph_assembly_connector_side_number_uniq',
            ),
        )

    def __str__(self) -> str:
        return self.label or f'{self.get_side_display()}{self.connector_number}'

    def clean(self):
        super().clean()
        if self.position_count < 1:
            raise ValidationError({'position_count': 'Position count must be at least 1.'})


class AssemblyMappingTemplate(RegistryModelMixin):
    registry_key = 'assemblymappingtemplate'

    template = models.ForeignKey('AssemblyTemplate', related_name='mappings', on_delete=models.CASCADE)
    a_connector = models.ForeignKey(
        'AssemblyConnectorTemplate', related_name='a_mappings', on_delete=models.CASCADE
    )
    a_position = models.PositiveSmallIntegerField()
    b_connector = models.ForeignKey(
        'AssemblyConnectorTemplate', related_name='b_mappings', on_delete=models.CASCADE
    )
    b_position = models.PositiveSmallIntegerField()
    mapping_type = models.CharField(max_length=50, choices=AssemblyMappingTypeChoices, default='identity')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('template', 'a_connector', 'a_position')
        constraints = (
            models.UniqueConstraint(
                fields=('template', 'a_connector', 'a_position'),
                name='netbox_plant_graph_assembly_mapping_a_position_uniq',
            ),
            models.UniqueConstraint(
                fields=('template', 'b_connector', 'b_position'),
                name='netbox_plant_graph_assembly_mapping_b_position_uniq',
            ),
        )

    def __str__(self) -> str:
        return f'{self.a_connector}:{self.a_position} -> {self.b_connector}:{self.b_position}'

    def clean(self):
        super().clean()
        if self.a_connector_id and self.a_connector.side != 'A':
            raise ValidationError({'a_connector': 'A-side connector must have side=A.'})
        if self.b_connector_id and self.b_connector.side != 'B':
            raise ValidationError({'b_connector': 'B-side connector must have side=B.'})
        if self.a_connector_id and self.a_position > self.a_connector.position_count:
            raise ValidationError({
                'a_position': f'Position {self.a_position} exceeds connector capacity ({self.a_connector.position_count}).'
            })
        if self.b_connector_id and self.b_position > self.b_connector.position_count:
            raise ValidationError({
                'b_position': f'Position {self.b_position} exceeds connector capacity ({self.b_connector.position_count}).'
            })


# --- Planning / Spatial Placement ---


class SpatialPlacement(RegistryModelMixin):
    registry_key = 'spatialplacement'

    target_type = models.ForeignKey(ContentType, related_name='+', on_delete=models.CASCADE)
    target_id = models.PositiveBigIntegerField()
    target = GenericForeignKey('target_type', 'target_id')
    reference_frame_type = models.ForeignKey(
        ContentType, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    reference_frame_id = models.PositiveBigIntegerField(null=True, blank=True)
    reference_frame = GenericForeignKey('reference_frame_type', 'reference_frame_id')
    position_x = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    position_y = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    position_z = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    orientation = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    coordinate_unit = models.CharField(max_length=20, choices=CoordinateUnitChoices, default='meters')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('target_type', 'target_id')
        constraints = (
            models.UniqueConstraint(
                fields=('target_type', 'target_id'),
                name='netbox_plant_graph_spatial_placement_target_uniq',
            ),
        )

    def __str__(self) -> str:
        return f'{self.target} @ ({self.position_x}, {self.position_y}, {self.position_z})'


# --- Planning / Spatial Templates ---


class SpatialTemplate(RegistryModelMixin):
    registry_key = 'spatialtemplate'

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    root_node_type = models.CharField(max_length=50, choices=SpatialNodeTypeChoices, default='building')
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Declared variables for this template. '
            'Schema: {"<var>": {"type": "int"|"str", "default": <value>, "label": "<label>"}}. '
            'Values are substituted into name_pattern and quantity_expr at stamp time.'
        ),
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name


class SpatialTemplateNode(RegistryModelMixin):
    registry_key = 'spatialtemplatenode'

    template = models.ForeignKey('SpatialTemplate', related_name='nodes', on_delete=models.CASCADE)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='children', on_delete=models.CASCADE)
    name_pattern = models.CharField(max_length=200)
    node_type = models.CharField(max_length=50, choices=SpatialNodeTypeChoices)
    quantity = models.PositiveIntegerField(default=1)
    quantity_expr = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            'When set, overrides quantity at stamp time. '
            'Must be a bare variable reference like "{hall_count}" declared in the parent SpatialTemplate.parameters. '
            'The resolved integer value replaces the static quantity field.'
        ),
    )
    sort_order = models.PositiveIntegerField(default=0)
    position_x = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    position_y = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    position_z = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    position_x_stride = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    position_y_stride = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    rack_type = models.ForeignKey(
        'dcim.RackType', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    rack_population_template = models.ForeignKey(
        'RackPopulationTemplate', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('template', 'sort_order', 'pk')

    def __str__(self) -> str:
        return self.name_pattern


# --- Planning / Deployment Plan & Stamp Provenance ---


class DeploymentPlan(RegistryModelMixin):
    registry_key = 'deploymentplan'

    fabric = models.ForeignKey(
        'Fabric', null=True, blank=True, related_name='deployment_plans', on_delete=models.SET_NULL
    )
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    name = models.CharField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=32, choices=DeploymentPlanStatusChoices, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self) -> str:
        return self.name


class StampRecord(RegistryModelMixin):
    registry_key = 'stamprecord'

    plan = models.ForeignKey(
        'DeploymentPlan', null=True, blank=True, related_name='stamp_records', on_delete=models.CASCADE
    )
    template_type = models.ForeignKey(ContentType, related_name='+', on_delete=models.CASCADE)
    template_id = models.PositiveBigIntegerField()
    template = GenericForeignKey('template_type', 'template_id')
    result_type = models.ForeignKey(
        ContentType, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    result_id = models.PositiveBigIntegerField(null=True, blank=True)
    result = GenericForeignKey('result_type', 'result_id')
    parameters = models.JSONField(default=dict, blank=True)
    stamped_at = models.DateTimeField(null=True, blank=True)
    stamped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    status = models.CharField(max_length=32, choices=StampRecordStatusChoices, default='pending')
    error_detail = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('plan', 'pk')

    def __str__(self) -> str:
        return f'{self.get_status_display()} stamp → {self.result or "(pending)"}'


# --- Planning / Rack Population Templates ---


class RackPopulationTemplate(RegistryModelMixin):
    registry_key = 'rackpopulationtemplate'

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    rack_type = models.ForeignKey(
        'dcim.RackType', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    plane_multiplier = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            'When set, stamp this template once per plane 1..N. '
            '{plane} is available in slot name_pattern values during each iteration.'
        ),
    )
    fabric = models.ForeignKey(
        'Fabric',
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.SET_NULL,
        help_text=(
            'If set and plane_multiplier is None, defaults plane_multiplier to the '
            'fabric\'s expected plane count at stamp time.'
        ),
    )
    parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text='Declared variables for this template (same schema as SpatialTemplate.parameters).',
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self) -> str:
        return self.name


class RackPopulationSlot(RegistryModelMixin):
    registry_key = 'rackpopulationslot'

    template = models.ForeignKey('RackPopulationTemplate', related_name='slots', on_delete=models.CASCADE)
    u_position = models.DecimalField(max_digits=4, decimal_places=1)
    face = models.CharField(max_length=10, choices=RackFaceChoices, default='front')
    device_type = models.ForeignKey('dcim.DeviceType', related_name='+', on_delete=models.CASCADE)
    device_role = models.ForeignKey('dcim.DeviceRole', related_name='+', on_delete=models.CASCADE)
    name_pattern = models.CharField(max_length=200)
    assembly_template = models.ForeignKey(
        'AssemblyTemplate', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    breakout_template = models.ForeignKey(
        'DeviceBreakoutTemplate', null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
        help_text='If set, child interfaces are auto-created on the stamped device using this template.',
    )
    sort_order = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('template', 'u_position', 'face')
        constraints = (
            models.UniqueConstraint(
                fields=('template', 'u_position', 'face'),
                name='netbox_plant_graph_rack_pop_slot_position_face_uniq',
            ),
        )

    def __str__(self) -> str:
        return self.name_pattern


# --- Planning / Connection Templates ---


CONNECTION_ENUMERATION_CHOICES = [
    ('one_to_one', 'One-to-one (source[i] \u2192 dest[i])'),
    ('fan_out', 'Fan-out (each source \u2192 all dests)'),
    ('fan_in', 'Fan-in (all sources \u2192 one dest)'),
]


class ConnectionTemplate(RegistryModelMixin):
    """
    Encodes a cabling topology between two SpatialTemplateNode populations.

    Belongs to a SpatialTemplate and is stamped after the spatial hierarchy
    and rack-population passes are complete.  For each source/dest pair
    (enumerated per enumeration_mode) the stamp service calls
    stamp_cable_assembly() with the assembly_template's connector shape.
    """
    registry_key = 'connectiontemplate'

    spatial_template = models.ForeignKey(
        'SpatialTemplate',
        related_name='connections',
        on_delete=models.CASCADE,
        help_text='Parent SpatialTemplate that owns this cabling topology.',
    )
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=200, blank=True)
    assembly_template = models.ForeignKey(
        'AssemblyTemplate',
        on_delete=models.PROTECT,
        help_text='Defines connector shape and breakout profile for each cable stamped.',
    )
    source_node = models.ForeignKey(
        'SpatialTemplateNode',
        related_name='source_connections',
        on_delete=models.CASCADE,
        help_text='SpatialTemplateNode whose stamped devices provide the A-side termination.',
    )
    source_slot_index = models.PositiveIntegerField(
        help_text='1-based index into source_node.rack_population_template.slots for the A-side port.',
    )
    source_connector_number = models.PositiveIntegerField(
        default=1,
        help_text='Connector number on the A-side assembly template.',
    )
    dest_node = models.ForeignKey(
        'SpatialTemplateNode',
        related_name='dest_connections',
        on_delete=models.CASCADE,
        help_text='SpatialTemplateNode whose stamped devices provide the B-side termination.',
    )
    dest_slot_index = models.PositiveIntegerField(
        help_text='1-based index into dest_node.rack_population_template.slots for the B-side port.',
    )
    dest_connector_number = models.PositiveIntegerField(
        default=1,
        help_text='Connector number on the B-side assembly template.',
    )
    enumeration_mode = models.CharField(
        max_length=20,
        choices=CONNECTION_ENUMERATION_CHOICES,
        default='one_to_one',
        help_text='How to pair source and dest instances when nodes have quantity > 1.',
    )
    label_pattern = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            'Python str.format pattern for cable labels. '
            'Available vars: {a_name}, {b_name}, {index}.'
        ),
    )
    sort_order = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('spatial_template', 'sort_order', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('spatial_template', 'name'),
                name='netbox_plant_graph_connection_template_name_uniq',
            ),
        )

    def __str__(self) -> str:
        return f'{self.spatial_template}: {self.name}'

    def get_absolute_url(self) -> str:
        return self._get_action_url('detail', kwargs={'pk': self.pk})

    def clean(self):
        super().clean()
        if self.source_node_id and self.dest_node_id:
            if self.source_node.template_id != self.spatial_template_id:
                raise ValidationError({'source_node': 'Source node must belong to the same SpatialTemplate.'})
            if self.dest_node.template_id != self.spatial_template_id:
                raise ValidationError({'dest_node': 'Dest node must belong to the same SpatialTemplate.'})
