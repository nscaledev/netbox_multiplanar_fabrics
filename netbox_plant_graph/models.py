from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from .choices import (
    AttachmentUnitTypeChoices,
    CoarseEdgeTypeChoices,
    DisjointnessChoices,
    FineEdgeTypeChoices,
    GraphResolutionChoices,
    LaneMapTypeChoices,
    PlaneMembershipRoleChoices,
    PlantNodeTypeChoices,
    SignalEncodingChoices,
    SignalLaneKindChoices,
    TerminationPointTypeChoices,
    TransferMapTypeChoices,
)


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


class Fabric(RegistryModelMixin):
    registry_key = 'fabric'

    name = models.CharField(max_length=200, unique=True)
    description = models.CharField(max_length=200, blank=True)
    expected_plane_count = models.PositiveIntegerField(default=4)
    tier_depth = models.PositiveIntegerField(default=3)
    disjointness_policy = models.CharField(max_length=50, choices=DisjointnessChoices, default='full')
    scope_site = models.ForeignKey('dcim.Site', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    scope_location = models.ForeignKey('dcim.Location', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
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
    source_port_mapping = models.ForeignKey('dcim.PortMapping', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
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


class AuditFinding(RegistryModelMixin):
    registry_key = 'auditfinding'

    finding_type = models.CharField(max_length=100)
    severity = models.CharField(max_length=32)
    object_type = models.ForeignKey(ContentType, related_name='+', on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    object = GenericForeignKey('object_type', 'object_id')
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('severity', 'finding_type', 'pk')

    def __str__(self) -> str:
        return f'{self.finding_type}: {self.message[:80]}'
