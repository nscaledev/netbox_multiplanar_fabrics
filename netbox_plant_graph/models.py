from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from .choices import (
    ArchitectureStatusChoices,
    ConnectorKindChoices,
    EndpointKindChoices,
    FabricStatusChoices,
    LaneDirectionChoices,
    NodeKindChoices,
    SegmentKindChoices,
    StampRunStatusChoices,
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
