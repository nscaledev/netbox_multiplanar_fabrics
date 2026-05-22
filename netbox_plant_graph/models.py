from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel
from django.utils import timezone

from .choices import (
    ArchitectureComponentStatusChoices,
    ArchitecturePublishPlanStatusChoices,
    ArchitectureSourceArtifactStatusChoices,
    ArchitectureSourceArtifactTypeChoices,
    AuditEventTypeChoices,
    ArchitectureStatusChoices,
    ArchitectureValidationStatusChoices,
    ArchitectureWorkspaceKindChoices,
    ArchitectureWorkspaceStatusChoices,
    ConnectorKindChoices,
    EndpointKindChoices,
    FabricClassChoices,
    FabricStatusChoices,
    LaneDirectionChoices,
    NodeKindChoices,
    OnboardingDesignItemStatusChoices,
    OnboardingPlanStatusChoices,
    OnboardingPrerequisiteResolutionModeChoices,
    OnboardingPrerequisiteStatusChoices,
    OnboardingSourceArtifactStatusChoices,
    OnboardingSourceArtifactTypeChoices,
    OnboardingStageStatusChoices,
    OnboardingWorkspaceStatusChoices,
    OperationProfileChoices,
    OperationRunStatusChoices,
    SegmentKindChoices,
    StampRunStatusChoices,
    SuppressionStatusChoices,
    TransferMapKindChoices,
)


class V2Model(NetBoxModel):
    class Meta:
        abstract = True

    def get_absolute_url(self):
        return reverse(f'plugins:netbox_plant_graph:{self._meta.model_name}', kwargs={'pk': self.pk})


class FabricArchitecture(V2Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    version = models.CharField(max_length=64, default='v1')
    status = models.CharField(max_length=32, choices=ArchitectureStatusChoices, default='draft')
    fabric_class = models.CharField(max_length=64, choices=FabricClassChoices, default='roce_backend')
    plane_count = models.PositiveIntegerField(default=4)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name', 'version')
        constraints = (
            models.UniqueConstraint(
                fields=('slug', 'version'),
                name='netbox_plant_graph_architecture_slug_version_uniq',
            ),
        )

    def __str__(self):
        return f'{self.name} {self.version}'


class ArchitectureRole(V2Model):
    architecture = models.ForeignKey(FabricArchitecture, related_name='roles', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    role_kind = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('architecture', 'slug')
        constraints = (
            models.UniqueConstraint(
                fields=('architecture', 'slug'),
                name='netbox_plant_graph_architecture_role_slug_uniq',
            ),
        )

    def __str__(self):
        return f'{self.architecture.slug}:{self.slug}'


class TransferPattern(V2Model):
    architecture = models.ForeignKey(
        FabricArchitecture, null=True, blank=True, related_name='transfer_patterns', on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    pattern_kind = models.CharField(max_length=64, choices=TransferMapKindChoices, default='identity')
    rule = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('architecture', 'slug')
        constraints = (
            models.UniqueConstraint(
                fields=('architecture', 'slug'),
                name='netbox_plant_graph_transfer_pattern_slug_uniq',
            ),
        )

    def __str__(self):
        return self.slug


class AllocationRuleSet(V2Model):
    architecture = models.ForeignKey(FabricArchitecture, related_name='allocation_rule_sets', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    rule = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('architecture', 'slug')
        constraints = (
            models.UniqueConstraint(
                fields=('architecture', 'slug'),
                name='netbox_plant_graph_allocation_rule_set_slug_uniq',
            ),
        )

    def __str__(self):
        return self.slug


class Fabric(V2Model):
    architecture = models.ForeignKey(
        FabricArchitecture, null=True, blank=True, related_name='fabrics', on_delete=models.PROTECT
    )
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True)
    status = models.CharField(max_length=32, choices=FabricStatusChoices, default='draft')
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    scope_site = models.ForeignKey('dcim.Site', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    scope_location = models.ForeignKey(
        'dcim.Location', null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class Plane(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='planes', on_delete=models.CASCADE)
    plane_number = models.PositiveIntegerField()
    label = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'plane_number')
        constraints = (
            models.UniqueConstraint(
                fields=('fabric', 'plane_number'),
                name='netbox_plant_graph_plane_number_uniq',
            ),
        )

    def __str__(self):
        return self.label or f'{self.fabric}:plane-{self.plane_number}'


class FabricNode(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='nodes', on_delete=models.CASCADE)
    role = models.ForeignKey(ArchitectureRole, null=True, blank=True, related_name='nodes', on_delete=models.SET_NULL)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='children', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=500)
    node_kind = models.CharField(max_length=64, choices=NodeKindChoices, default='logical_container')
    local_index = models.PositiveIntegerField(null=True, blank=True)
    source_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_type', 'source_id')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'address')
        constraints = (
            models.UniqueConstraint(
                fields=('fabric', 'address'),
                name='netbox_plant_graph_fabric_node_address_uniq',
            ),
        )

    def __str__(self):
        return self.address


class Endpoint(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='endpoints', on_delete=models.CASCADE)
    node = models.ForeignKey(FabricNode, related_name='endpoints', on_delete=models.CASCADE)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='children', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=700)
    endpoint_kind = models.CharField(max_length=64, choices=EndpointKindChoices, default='plugin_port')
    connector_kind = models.CharField(max_length=64, choices=ConnectorKindChoices, default='other')
    position_count = models.PositiveIntegerField(default=0)
    source_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.PROTECT)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    source = GenericForeignKey('source_type', 'source_id')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'address')
        constraints = (
            models.UniqueConstraint(
                fields=('fabric', 'address'),
                name='netbox_plant_graph_endpoint_address_uniq',
            ),
        )

    def __str__(self):
        return self.address


class ConnectorPosition(V2Model):
    endpoint = models.ForeignKey(Endpoint, related_name='positions', on_delete=models.CASCADE)
    position_number = models.PositiveIntegerField()
    label = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('endpoint', 'position_number')
        constraints = (
            models.UniqueConstraint(
                fields=('endpoint', 'position_number'),
                name='netbox_plant_graph_connector_position_uniq',
            ),
        )

    def __str__(self):
        return f'{self.endpoint}:{self.position_number}'


class TransportChannel(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='transport_channels', on_delete=models.CASCADE)
    endpoint = models.ForeignKey(Endpoint, related_name='transport_channels', on_delete=models.CASCADE)
    plane = models.ForeignKey(Plane, null=True, blank=True, related_name='transport_channels', on_delete=models.SET_NULL)
    source_subinterface = models.ForeignKey(
        'dcim.Interface',
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.SET_NULL,
    )
    name = models.CharField(max_length=200)
    channel_index = models.PositiveIntegerField()
    speed_gbps = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('endpoint', 'channel_index')
        constraints = (
            models.UniqueConstraint(
                fields=('endpoint', 'channel_index'),
                name='netbox_plant_graph_transport_channel_uniq',
            ),
        )

    def __str__(self):
        return f'{self.endpoint}:{self.name}'

    def clean(self):
        super().clean()
        if self.endpoint_id and self.endpoint.fabric_id != self.fabric_id:
            raise ValidationError({'endpoint': 'Transport channel endpoint must belong to the channel fabric.'})
        if self.plane_id and self.plane.fabric_id != self.fabric_id:
            raise ValidationError({'plane': 'Transport channel plane must belong to the channel fabric.'})
        if self.source_subinterface_id and self.endpoint_id:
            endpoint_source = self.endpoint.source
            if endpoint_source is not None and getattr(endpoint_source, 'pk', None):
                if endpoint_source._meta.model_name == 'interface':
                    if self.source_subinterface.parent_id != endpoint_source.pk:
                        raise ValidationError(
                            {'source_subinterface': 'Source sub-interface parent must be the endpoint source interface.'}
                        )


class TransportChannelPositionMap(V2Model):
    channel = models.ForeignKey(
        TransportChannel,
        related_name='position_maps',
        on_delete=models.CASCADE,
    )
    mpo_endpoint = models.ForeignKey(
        Endpoint,
        related_name='channel_position_maps',
        on_delete=models.CASCADE,
    )
    mpo_position = models.ForeignKey(
        ConnectorPosition,
        related_name='channel_position_maps',
        on_delete=models.CASCADE,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('channel', 'mpo_position', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('channel', 'mpo_position'),
                name='netbox_plant_graph_transport_channel_position_map_uniq',
            ),
        )

    def __str__(self):
        return f'{self.channel}:{self.mpo_position}'

    def clean(self):
        super().clean()
        if self.channel_id and self.channel.fabric_id != self.mpo_endpoint.fabric_id:
            raise ValidationError({'mpo_endpoint': 'MPO endpoint must belong to the channel fabric.'})
        if self.mpo_endpoint_id and self.mpo_position_id and self.mpo_position.endpoint_id != self.mpo_endpoint_id:
            raise ValidationError({'mpo_position': 'MPO position must belong to the MPO endpoint.'})
        if self.channel_id and self.channel.endpoint_id and self.mpo_endpoint_id and self.mpo_endpoint.parent_id:
            if self.mpo_endpoint.parent_id != self.channel.endpoint_id:
                raise ValidationError(
                    {'mpo_endpoint': 'MPO endpoint parent must match the channel endpoint when parent is set.'}
                )


class CableAssembly(V2Model):
    site = models.ForeignKey('dcim.Site', related_name='+', on_delete=models.PROTECT)
    cable_id = models.CharField(max_length=200)
    manufacturer = models.CharField(max_length=200, blank=True)
    serial_number = models.CharField(max_length=200, blank=True)
    model_id = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    parent_cable = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        related_name='child_cables',
        on_delete=models.SET_NULL,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('site', 'cable_id')
        constraints = (
            models.UniqueConstraint(
                fields=('site', 'cable_id'),
                name='netbox_plant_graph_cable_assembly_site_cable_id_uniq',
            ),
        )

    def __str__(self):
        return f'{self.site}:{self.cable_id}'

    def clean(self):
        super().clean()
        if self.parent_cable_id and self.parent_cable_id == self.pk:
            raise ValidationError({'parent_cable': 'A cable assembly cannot be its own parent.'})
        if self.parent_cable_id and self.parent_cable.site_id != self.site_id:
            raise ValidationError({'parent_cable': 'Parent cable must belong to the same site.'})


class FiberSegment(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='fiber_segments', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    segment_kind = models.CharField(max_length=64, choices=SegmentKindChoices, default='jumper')
    a_endpoint = models.ForeignKey(Endpoint, related_name='fiber_segments_a', on_delete=models.CASCADE)
    b_endpoint = models.ForeignKey(Endpoint, related_name='fiber_segments_b', on_delete=models.CASCADE)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'name')

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.a_endpoint_id and self.b_endpoint_id and self.a_endpoint_id == self.b_endpoint_id:
            raise ValidationError({'b_endpoint': 'A fiber segment must connect two distinct endpoints.'})
        if self.a_endpoint_id and self.b_endpoint_id and self.a_endpoint.fabric_id != self.b_endpoint.fabric_id:
            raise ValidationError({'b_endpoint': 'Fiber segment endpoints must belong to the same fabric.'})


class FiberStrand(V2Model):
    segment = models.ForeignKey(FiberSegment, related_name='strands', on_delete=models.CASCADE)
    strand_index = models.PositiveIntegerField()
    cable_site = models.ForeignKey(
        'dcim.Site',
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.PROTECT,
    )
    cable_id = models.CharField(max_length=200, blank=True)
    label = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('segment', 'strand_index')
        constraints = (
            models.UniqueConstraint(
                fields=('segment', 'strand_index'),
                name='netbox_plant_graph_fiber_strand_index_uniq',
            ),
        )

    def __str__(self):
        return self.label or f'{self.segment}:strand-{self.strand_index}'

    @property
    def cable_assembly(self):
        if not self.cable_site_id or not self.cable_id:
            return None
        return CableAssembly.objects.filter(
            site_id=self.cable_site_id,
            cable_id=self.cable_id,
        ).first()

    def clean(self):
        super().clean()
        has_site = bool(self.cable_site_id)
        has_cable_id = bool(self.cable_id)
        if has_site != has_cable_id:
            raise ValidationError('Fiber strand cable_site and cable_id must be provided together.')
        if has_site and has_cable_id:
            exists = CableAssembly.objects.filter(
                site_id=self.cable_site_id,
                cable_id=self.cable_id,
            ).exists()
            if not exists:
                raise ValidationError(
                    {'cable_id': 'No cable assembly exists for the provided site and cable ID.'}
                )


class StrandTermination(V2Model):
    strand = models.ForeignKey(FiberStrand, related_name='terminations', on_delete=models.CASCADE)
    mpo_endpoint = models.ForeignKey(Endpoint, related_name='strand_terminations', on_delete=models.CASCADE)
    mpo_position = models.ForeignKey(ConnectorPosition, related_name='strand_terminations', on_delete=models.CASCADE)
    termination_index = models.PositiveIntegerField(null=True, blank=True)
    label = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('strand', 'termination_index', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('strand', 'mpo_position'),
                name='netbox_plant_graph_strand_position_uniq',
            ),
            models.UniqueConstraint(
                fields=('mpo_position',),
                name='netbox_plant_graph_strand_position_single_uniq',
            ),
        )

    def __str__(self):
        return self.label or f'{self.strand}@{self.mpo_position}'

    def clean(self):
        super().clean()
        if self.mpo_endpoint_id and self.mpo_position_id and self.mpo_position.endpoint_id != self.mpo_endpoint_id:
            raise ValidationError({'mpo_position': 'Strand termination position must belong to the MPO endpoint.'})
        if self.mpo_endpoint_id and self.strand_id and self.mpo_endpoint.fabric_id != self.strand.segment.fabric_id:
            raise ValidationError({'mpo_endpoint': 'Strand termination endpoint must belong to the strand fabric.'})


class OpticalLane(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='optical_lanes', on_delete=models.CASCADE)
    endpoint = models.ForeignKey(Endpoint, related_name='optical_lanes', on_delete=models.CASCADE)
    channel = models.ForeignKey(
        TransportChannel, null=True, blank=True, related_name='optical_lanes', on_delete=models.SET_NULL
    )
    plane = models.ForeignKey(Plane, null=True, blank=True, related_name='optical_lanes', on_delete=models.SET_NULL)
    local_mpo_endpoint = models.ForeignKey(
        Endpoint, related_name='local_optical_lanes', on_delete=models.CASCADE
    )
    local_mpo_position = models.ForeignKey(
        ConnectorPosition, related_name='optical_lanes', on_delete=models.CASCADE
    )
    lane_index = models.PositiveIntegerField()
    local_mpo_index = models.PositiveIntegerField(default=1)
    direction = models.CharField(max_length=32, choices=LaneDirectionChoices)
    wavelength_nm = models.DecimalField(max_digits=8, decimal_places=3)
    pair_key = models.CharField(max_length=200, blank=True)
    nominal_rate_gbps = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'endpoint', 'lane_index', 'direction', 'pk')
        indexes = (
            models.Index(fields=('fabric', 'lane_index'), name='mpf_olane_fabric_lane_idx'),
            models.Index(fields=('fabric', 'pair_key'), name='mpf_olane_fabric_pair_idx'),
            models.Index(fields=('fabric', 'wavelength_nm'), name='mpf_olane_fabric_wave_idx'),
        )
        constraints = (
            models.UniqueConstraint(
                fields=('endpoint', 'lane_index', 'direction'),
                name='netbox_plant_graph_optical_lane_endpoint_idx_dir_uniq',
            ),
        )

    def __str__(self):
        return f'{self.endpoint}:lane-{self.lane_index}:{self.direction}:{self.wavelength_nm}nm'

    def clean(self):
        super().clean()
        if self.endpoint_id and self.endpoint.fabric_id != self.fabric_id:
            raise ValidationError({'endpoint': 'Optical lane endpoint must belong to the lane fabric.'})
        if self.local_mpo_endpoint_id and self.local_mpo_endpoint.fabric_id != self.fabric_id:
            raise ValidationError({'local_mpo_endpoint': 'Optical lane MPO endpoint must belong to the lane fabric.'})
        if (
            self.endpoint_id
            and self.local_mpo_endpoint_id
            and self.local_mpo_endpoint.parent_id
            and self.local_mpo_endpoint.parent_id != self.endpoint_id
        ):
            raise ValidationError({'local_mpo_endpoint': 'Optical lane MPO endpoint must be a child of the lane endpoint.'})
        if (
            self.local_mpo_endpoint_id
            and self.local_mpo_position_id
            and self.local_mpo_position.endpoint_id != self.local_mpo_endpoint_id
        ):
            raise ValidationError({'local_mpo_position': 'Optical lane position must belong to the local MPO endpoint.'})
        if self.channel_id and self.channel.endpoint_id != self.endpoint_id:
            raise ValidationError({'channel': 'Optical lane channel must belong to the lane endpoint.'})
        if self.plane_id and self.plane.fabric_id != self.fabric_id:
            raise ValidationError({'plane': 'Optical lane plane must belong to the lane fabric.'})


class TransferMap(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='transfer_maps', on_delete=models.CASCADE)
    owner_node = models.ForeignKey(FabricNode, null=True, blank=True, related_name='transfer_maps', on_delete=models.CASCADE)
    owner_segment = models.ForeignKey(
        FiberSegment, null=True, blank=True, related_name='transfer_maps', on_delete=models.CASCADE
    )
    pattern = models.ForeignKey(TransferPattern, null=True, blank=True, related_name='transfer_maps', on_delete=models.SET_NULL)
    map_kind = models.CharField(max_length=64, choices=TransferMapKindChoices, default='identity')
    src_position = models.ForeignKey(ConnectorPosition, related_name='transfer_map_sources', on_delete=models.CASCADE)
    dst_position = models.ForeignKey(
        ConnectorPosition, related_name='transfer_map_destinations', on_delete=models.CASCADE
    )
    bidirectional = models.BooleanField(default=True)
    group_key = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'owner_node', 'owner_segment', 'pk')

    def __str__(self):
        return f'{self.map_kind}:{self.pk}'

    def clean(self):
        super().clean()
        if bool(self.owner_node_id) == bool(self.owner_segment_id):
            raise ValidationError('A transfer map must have exactly one owner node or owner segment.')
        if not (self.src_position_id and self.dst_position_id):
            raise ValidationError('A transfer map must map two connector positions.')
        if self.src_position_id == self.dst_position_id:
            raise ValidationError({'dst_position': 'A transfer map must connect two distinct positions.'})
        if self.src_position_id and self.src_position.endpoint.fabric_id != self.fabric_id:
            raise ValidationError({'src_position': 'Source position must belong to the transfer map fabric.'})
        if self.dst_position_id and self.dst_position.endpoint.fabric_id != self.fabric_id:
            raise ValidationError({'dst_position': 'Destination position must belong to the transfer map fabric.'})


class PathIntent(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='path_intents', on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    plane = models.ForeignKey(Plane, null=True, blank=True, related_name='path_intents', on_delete=models.SET_NULL)
    source_channel = models.ForeignKey(
        TransportChannel, null=True, blank=True, related_name='source_path_intents', on_delete=models.SET_NULL
    )
    destination_channel = models.ForeignKey(
        TransportChannel, null=True, blank=True, related_name='destination_path_intents', on_delete=models.SET_NULL
    )
    source_endpoint = models.ForeignKey(
        Endpoint, null=True, blank=True, related_name='source_path_intents', on_delete=models.SET_NULL
    )
    destination_endpoint = models.ForeignKey(
        Endpoint, null=True, blank=True, related_name='destination_path_intents', on_delete=models.SET_NULL
    )
    selector = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('fabric', 'name')

    def __str__(self):
        return self.name


class StampTemplate(V2Model):
    architecture = models.ForeignKey(
        FabricArchitecture, null=True, blank=True, related_name='stamp_templates', on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    template = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class StampRun(V2Model):
    template = models.ForeignKey(StampTemplate, null=True, blank=True, related_name='stamp_runs', on_delete=models.SET_NULL)
    fabric = models.ForeignKey(Fabric, null=True, blank=True, related_name='stamp_runs', on_delete=models.SET_NULL)
    status = models.CharField(max_length=32, choices=StampRunStatusChoices, default='pending')
    parameters = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self):
        return f'{self.template or "ad hoc"} stamp #{self.pk}'


class SuppressionRule(V2Model):
    fabric = models.ForeignKey(Fabric, related_name='suppression_rules', on_delete=models.CASCADE)
    plane = models.ForeignKey(Plane, null=True, blank=True, related_name='suppression_rules', on_delete=models.CASCADE)
    optical_lane = models.ForeignKey(
        OpticalLane, null=True, blank=True, related_name='suppression_rules', on_delete=models.CASCADE
    )
    path_hop_object_type = models.CharField(max_length=64, blank=True)
    path_hop_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    policy_key = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=32, choices=SuppressionStatusChoices, default='pending')
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self):
        if self.optical_lane_id:
            return f'lane suppression #{self.pk}'
        if self.plane_id:
            return f'plane suppression #{self.pk}'
        if self.policy_key:
            return f'policy suppression #{self.pk}'
        if self.path_hop_object_type and self.path_hop_object_id:
            return f'path-hop suppression #{self.pk}'
        return f'fabric suppression #{self.pk}'

    @property
    def is_effective(self) -> bool:
        if self.status != 'active':
            return False
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    def clean(self):
        super().clean()
        if self.plane_id and self.plane.fabric_id != self.fabric_id:
            raise ValidationError({'plane': 'Suppression plane must belong to suppression fabric.'})
        if self.optical_lane_id and self.optical_lane.fabric_id != self.fabric_id:
            raise ValidationError({'optical_lane': 'Suppression lane must belong to suppression fabric.'})
        if bool(self.path_hop_object_type) != bool(self.path_hop_object_id):
            raise ValidationError('path_hop_object_type and path_hop_object_id must be provided together.')
        if self.optical_lane_id and self.plane_id and self.optical_lane.plane_id and self.optical_lane.plane_id != self.plane_id:
            raise ValidationError({'plane': 'Suppression plane must match suppression optical lane plane when both are set.'})


class AuditEvent(V2Model):
    fabric = models.ForeignKey(Fabric, null=True, blank=True, related_name='audit_events', on_delete=models.CASCADE)
    event_type = models.CharField(max_length=64, choices=AuditEventTypeChoices, default='stamp')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    subject_type = models.ForeignKey(
        ContentType, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    subject_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey('subject_type', 'subject_id')
    outcome = models.CharField(max_length=32, default='ok')
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self):
        return f'{self.event_type}:{self.outcome} #{self.pk}'


class OperationRun(V2Model):
    profile = models.CharField(max_length=64, choices=OperationProfileChoices, default='generic_roce')
    status = models.CharField(max_length=32, choices=OperationRunStatusChoices, default='pending')
    fabric = models.ForeignKey(Fabric, null=True, blank=True, related_name='operation_runs', on_delete=models.SET_NULL)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL
    )
    dedupe_key = models.CharField(max_length=128, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created', '-pk')

    def __str__(self):
        return f'{self.profile} run #{self.pk}'


class ArchitectureWorkspace(V2Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    workspace_kind = models.CharField(max_length=32, choices=ArchitectureWorkspaceKindChoices, default='new_blueprint')
    status = models.CharField(max_length=32, choices=ArchitectureWorkspaceStatusChoices, default='draft')
    target_slug = models.SlugField(max_length=200, blank=True)
    target_version = models.CharField(max_length=64, blank=True)
    fabric_class = models.CharField(max_length=64, choices=FabricClassChoices, default='roce_backend')
    base_architecture = models.ForeignKey(
        FabricArchitecture,
        null=True,
        blank=True,
        related_name='derived_architecture_workspaces',
        on_delete=models.SET_NULL,
    )
    published_architecture = models.ForeignKey(
        FabricArchitecture,
        null=True,
        blank=True,
        related_name='publishing_architecture_workspaces',
        on_delete=models.SET_NULL,
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    current_plan = models.ForeignKey(
        'ArchitecturePublishPlan',
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.SET_NULL,
    )
    source_summary = models.JSONField(default=dict, blank=True)
    validation_summary = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-last_updated', '-pk')
        indexes = (
            models.Index(fields=('status', 'fabric_class'), name='mpf_archws_status_class_idx'),
            models.Index(fields=('target_slug', 'target_version'), name='mpf_archws_target_idx'),
        )

    def __str__(self):
        return self.name


class ArchitectureSourceArtifact(V2Model):
    workspace = models.ForeignKey(ArchitectureWorkspace, related_name='source_artifacts', on_delete=models.CASCADE)
    artifact_type = models.CharField(
        max_length=64,
        choices=ArchitectureSourceArtifactTypeChoices,
        default='api_payload',
    )
    name = models.CharField(max_length=200)
    source_uri = models.CharField(max_length=1000, blank=True)
    content_sha256 = models.CharField(max_length=128, blank=True)
    payload_version = models.CharField(max_length=64, blank=True)
    source_label = models.CharField(max_length=200, blank=True)
    parser_key = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=32, choices=ArchitectureSourceArtifactStatusChoices, default='received')
    raw_payload = models.JSONField(default=dict, blank=True)
    parse_result = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', '-created', '-pk')

    def __str__(self):
        return f'{self.workspace.slug}:{self.name}'


class ArchitectureDesignComponent(V2Model):
    workspace = models.ForeignKey(ArchitectureWorkspace, related_name='design_components', on_delete=models.CASCADE)
    source_artifact = models.ForeignKey(
        ArchitectureSourceArtifact,
        null=True,
        blank=True,
        related_name='design_components',
        on_delete=models.SET_NULL,
    )
    kind = models.CharField(max_length=100)
    natural_key = models.CharField(max_length=500)
    desired_state = models.JSONField(default=dict, blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    validation_status = models.CharField(max_length=32, choices=ArchitectureComponentStatusChoices, default='pending')
    validation_messages = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', 'kind', 'natural_key', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('workspace', 'kind', 'natural_key'),
                name='netbox_plant_graph_arch_component_key_uniq',
            ),
        )

    def __str__(self):
        return f'{self.workspace.slug}:{self.kind}:{self.natural_key}'


class ArchitectureValidationRun(V2Model):
    workspace = models.ForeignKey(ArchitectureWorkspace, related_name='validation_runs', on_delete=models.CASCADE)
    source_artifact = models.ForeignKey(
        ArchitectureSourceArtifact,
        null=True,
        blank=True,
        related_name='validation_runs',
        on_delete=models.SET_NULL,
    )
    status = models.CharField(max_length=32, choices=ArchitectureValidationStatusChoices, default='pending')
    validation_kind = models.CharField(max_length=64, default='publish_preflight')
    workspace_revision = models.CharField(max_length=128, blank=True)
    executed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    summary = models.JSONField(default=dict, blank=True)
    issues = models.JSONField(default=list, blank=True)
    import_plan = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', '-created', '-pk')

    def __str__(self):
        return f'{self.workspace.slug}:validation:{self.status}'


class ArchitecturePublishPlan(V2Model):
    workspace = models.ForeignKey(ArchitectureWorkspace, related_name='publish_plans', on_delete=models.CASCADE)
    status = models.CharField(max_length=32, choices=ArchitecturePublishPlanStatusChoices, default='generated')
    plan_hash = models.CharField(max_length=128)
    workspace_revision = models.CharField(max_length=128, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    warning_acknowledgements = models.JSONField(default=list, blank=True)
    publish_payload = models.JSONField(default=dict, blank=True)
    validation_summary = models.JSONField(default=dict, blank=True)
    import_plan = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', '-created', '-pk')
        constraints = (
            models.UniqueConstraint(
                fields=('workspace', 'plan_hash'),
                name='netbox_plant_graph_arch_publish_plan_hash_uniq',
            ),
        )

    def __str__(self):
        return f'{self.workspace.slug}:architecture-plan:{self.plan_hash[:12]}'


class OnboardingWorkspace(V2Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    status = models.CharField(max_length=32, choices=OnboardingWorkspaceStatusChoices, default='draft')
    fabric_class = models.CharField(max_length=64, choices=FabricClassChoices, default='roce_backend')
    target_fabric_name = models.CharField(max_length=200, blank=True)
    target_fabric_slug = models.SlugField(max_length=200, blank=True)
    fabric = models.ForeignKey(Fabric, null=True, blank=True, related_name='onboarding_workspaces', on_delete=models.SET_NULL)
    architecture = models.ForeignKey(
        FabricArchitecture, null=True, blank=True, related_name='onboarding_workspaces', on_delete=models.SET_NULL
    )
    site = models.ForeignKey('dcim.Site', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    location = models.ForeignKey('dcim.Location', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    tenant = models.ForeignKey('tenancy.Tenant', null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    current_plan = models.ForeignKey(
        'OnboardingPlan',
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.SET_NULL,
    )
    source_summary = models.JSONField(default=dict, blank=True)
    readiness_summary = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('-last_updated', '-pk')
        indexes = (
            models.Index(fields=('status', 'fabric_class'), name='mpf_obws_status_class_idx'),
            models.Index(fields=('target_fabric_slug',), name='mpf_obws_target_slug_idx'),
        )

    def __str__(self):
        return self.name


class OnboardingSourceArtifact(V2Model):
    workspace = models.ForeignKey(OnboardingWorkspace, related_name='source_artifacts', on_delete=models.CASCADE)
    artifact_type = models.CharField(
        max_length=64,
        choices=OnboardingSourceArtifactTypeChoices,
        default='api_payload',
    )
    name = models.CharField(max_length=200)
    source_uri = models.CharField(max_length=1000, blank=True)
    content_sha256 = models.CharField(max_length=128, blank=True)
    payload_version = models.CharField(max_length=64, blank=True)
    source_label = models.CharField(max_length=200, blank=True)
    parser_key = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=32, choices=OnboardingSourceArtifactStatusChoices, default='received')
    raw_payload = models.JSONField(default=dict, blank=True)
    parse_result = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', '-created', '-pk')

    def __str__(self):
        return f'{self.workspace.slug}:{self.name}'


class OnboardingDesignItem(V2Model):
    workspace = models.ForeignKey(OnboardingWorkspace, related_name='design_items', on_delete=models.CASCADE)
    source_artifact = models.ForeignKey(
        OnboardingSourceArtifact,
        null=True,
        blank=True,
        related_name='design_items',
        on_delete=models.SET_NULL,
    )
    kind = models.CharField(max_length=100)
    natural_key = models.CharField(max_length=500)
    desired_state = models.JSONField(default=dict, blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    validation_status = models.CharField(max_length=32, choices=OnboardingDesignItemStatusChoices, default='pending')
    validation_messages = models.JSONField(default=list, blank=True)
    planned_object_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.SET_NULL,
    )
    planned_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', 'kind', 'natural_key', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('workspace', 'kind', 'natural_key'),
                name='netbox_plant_graph_onboarding_item_natural_key_uniq',
            ),
        )

    def __str__(self):
        return f'{self.workspace.slug}:{self.kind}:{self.natural_key}'


class OnboardingPrerequisite(V2Model):
    workspace = models.ForeignKey(OnboardingWorkspace, related_name='prerequisites', on_delete=models.CASCADE)
    design_item = models.ForeignKey(
        OnboardingDesignItem,
        null=True,
        blank=True,
        related_name='prerequisites',
        on_delete=models.SET_NULL,
    )
    requirement_key = models.CharField(max_length=500)
    object_model = models.CharField(max_length=100)
    role = models.CharField(max_length=100, blank=True)
    desired_identity = models.JSONField(default=dict, blank=True)
    resolution_mode = models.CharField(
        max_length=32,
        choices=OnboardingPrerequisiteResolutionModeChoices,
        default='unresolved',
    )
    resolved_object_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        related_name='+',
        on_delete=models.SET_NULL,
    )
    resolved_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    planned_create = models.JSONField(default=dict, blank=True)
    defer_reason = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=OnboardingPrerequisiteStatusChoices, default='open')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', 'status', 'requirement_key', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('workspace', 'requirement_key'),
                name='netbox_plant_graph_onboarding_prereq_key_uniq',
            ),
        )

    def __str__(self):
        return f'{self.workspace.slug}:{self.requirement_key}'


class OnboardingPlan(V2Model):
    workspace = models.ForeignKey(OnboardingWorkspace, related_name='plans', on_delete=models.CASCADE)
    status = models.CharField(max_length=32, choices=OnboardingPlanStatusChoices, default='draft')
    plan_hash = models.CharField(max_length=128)
    workspace_revision = models.CharField(max_length=128, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    warning_acknowledgements = models.JSONField(default=list, blank=True)
    prerequisite_plan = models.JSONField(default=dict, blank=True)
    stamp_preview = models.JSONField(default=dict, blank=True)
    import_plan = models.JSONField(default=dict, blank=True)
    audit_projection = models.JSONField(default=dict, blank=True)
    impact_projection = models.JSONField(default=dict, blank=True)
    readiness_projection = models.JSONField(default=dict, blank=True)
    rollback_preview = models.JSONField(default=dict, blank=True)
    retry_preview = models.JSONField(default=dict, blank=True)
    plan_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', '-created', '-pk')
        constraints = (
            models.UniqueConstraint(
                fields=('workspace', 'plan_hash'),
                name='netbox_plant_graph_onboarding_plan_hash_uniq',
            ),
        )

    def __str__(self):
        return f'{self.workspace.slug}:plan:{self.plan_hash[:12]}'


class OnboardingExecutionStage(V2Model):
    plan = models.ForeignKey(OnboardingPlan, related_name='stages', on_delete=models.CASCADE)
    stage_key = models.CharField(max_length=100)
    stage_kind = models.CharField(max_length=100)
    status = models.CharField(max_length=32, choices=OnboardingStageStatusChoices, default='pending')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    operation_run = models.ForeignKey(OperationRun, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    stamp_run = models.ForeignKey(StampRun, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    result = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('plan', 'pk')
        constraints = (
            models.UniqueConstraint(
                fields=('plan', 'stage_key'),
                name='netbox_plant_graph_onboarding_stage_key_uniq',
            ),
        )

    def __str__(self):
        return f'{self.plan}:{self.stage_key}'


class OnboardingObjectLink(V2Model):
    workspace = models.ForeignKey(OnboardingWorkspace, related_name='object_links', on_delete=models.CASCADE)
    plan = models.ForeignKey(OnboardingPlan, null=True, blank=True, related_name='object_links', on_delete=models.SET_NULL)
    stage = models.ForeignKey(
        OnboardingExecutionStage,
        null=True,
        blank=True,
        related_name='object_links',
        on_delete=models.SET_NULL,
    )
    design_item = models.ForeignKey(
        OnboardingDesignItem,
        null=True,
        blank=True,
        related_name='object_links',
        on_delete=models.SET_NULL,
    )
    source_artifact = models.ForeignKey(
        OnboardingSourceArtifact,
        null=True,
        blank=True,
        related_name='object_links',
        on_delete=models.SET_NULL,
    )
    link_kind = models.CharField(max_length=64)
    object_type = models.ForeignKey(ContentType, null=True, blank=True, related_name='+', on_delete=models.SET_NULL)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    label = models.CharField(max_length=300, blank=True)
    external_url = models.CharField(max_length=1000, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ('workspace', 'link_kind', 'label', 'pk')

    def __str__(self):
        return self.label or f'{self.workspace.slug}:{self.link_kind}:{self.pk}'
