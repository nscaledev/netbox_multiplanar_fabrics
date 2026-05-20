import django.db.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


def netbox_fields():
    return [
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
        ('created', models.DateTimeField(auto_now_add=True, null=True)),
        ('last_updated', models.DateTimeField(auto_now=True, null=True)),
        (
            'custom_field_data',
            models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder),
        ),
        ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
    ]


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('dcim', '0191_module_bay_rebuild'),
        ('extras', '0122_charfield_null_choices'),
        ('tenancy', '0017_natural_ordering'),
    ]

    operations = [
        migrations.CreateModel(
            name='FabricArchitecture',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200)),
                ('version', models.CharField(default='v1', max_length=64)),
                ('status', models.CharField(default='draft', max_length=32)),
                ('plane_count', models.PositiveIntegerField(default=4)),
                ('description', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
            options={'ordering': ('name', 'version')},
        ),
        migrations.CreateModel(
            name='Fabric',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200, unique=True)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('status', models.CharField(default='draft', max_length=32)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='fabrics',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
                (
                    'scope_location',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='dcim.location',
                    ),
                ),
                (
                    'scope_site',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='dcim.site',
                    ),
                ),
                (
                    'tenant',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='tenancy.tenant',
                    ),
                ),
            ],
            options={'ordering': ('name',)},
        ),
        migrations.CreateModel(
            name='ArchitectureRole',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200)),
                ('role_kind', models.CharField(blank=True, max_length=64)),
                ('description', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='roles',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
            ],
            options={'ordering': ('architecture', 'slug')},
        ),
        migrations.CreateModel(
            name='TransferPattern',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200)),
                ('pattern_kind', models.CharField(default='identity', max_length=64)),
                ('rule', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transfer_patterns',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
            ],
            options={'ordering': ('architecture', 'slug')},
        ),
        migrations.CreateModel(
            name='AllocationRuleSet',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200)),
                ('rule', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='allocation_rule_sets',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
            ],
            options={'ordering': ('architecture', 'slug')},
        ),
        migrations.CreateModel(
            name='Plane',
            fields=netbox_fields() + [
                ('plane_number', models.PositiveIntegerField()),
                ('label', models.CharField(blank=True, max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='planes',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'plane_number')},
        ),
        migrations.CreateModel(
            name='FabricNode',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('address', models.CharField(max_length=500)),
                ('node_kind', models.CharField(default='logical_container', max_length=64)),
                ('local_index', models.PositiveIntegerField(blank=True, null=True)),
                ('source_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='nodes',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'parent',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='children',
                        to='netbox_plant_graph.fabricnode',
                    ),
                ),
                (
                    'role',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='nodes',
                        to='netbox_plant_graph.architecturerole',
                    ),
                ),
                (
                    'source_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to='contenttypes.contenttype',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'address')},
        ),
        migrations.CreateModel(
            name='Endpoint',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('address', models.CharField(max_length=700)),
                ('endpoint_kind', models.CharField(default='plugin_port', max_length=64)),
                ('connector_kind', models.CharField(default='other', max_length=64)),
                ('position_count', models.PositiveIntegerField(default=0)),
                ('source_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='endpoints',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'node',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='endpoints',
                        to='netbox_plant_graph.fabricnode',
                    ),
                ),
                (
                    'parent',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='children',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'source_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to='contenttypes.contenttype',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'address')},
        ),
        migrations.CreateModel(
            name='ConnectorPosition',
            fields=netbox_fields() + [
                ('position_number', models.PositiveIntegerField()),
                ('label', models.CharField(blank=True, max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='positions',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
            ],
            options={'ordering': ('endpoint', 'position_number')},
        ),
        migrations.CreateModel(
            name='TransportChannel',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('channel_index', models.PositiveIntegerField()),
                ('speed_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transport_channels',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transport_channels',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'plane',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='transport_channels',
                        to='netbox_plant_graph.plane',
                    ),
                ),
            ],
            options={'ordering': ('endpoint', 'channel_index')},
        ),
        migrations.CreateModel(
            name='FiberSegment',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('segment_kind', models.CharField(default='jumper', max_length=64)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'a_endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='fiber_segments_a',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'b_endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='fiber_segments_b',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='fiber_segments',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'name')},
        ),
        migrations.CreateModel(
            name='FiberStrand',
            fields=netbox_fields() + [
                ('strand_index', models.PositiveIntegerField()),
                ('label', models.CharField(blank=True, max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'segment',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='strands',
                        to='netbox_plant_graph.fibersegment',
                    ),
                ),
            ],
            options={'ordering': ('segment', 'strand_index')},
        ),
        migrations.CreateModel(
            name='StrandTermination',
            fields=netbox_fields() + [
                ('termination_index', models.PositiveIntegerField(blank=True, null=True)),
                ('label', models.CharField(blank=True, max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'mpo_endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='strand_terminations',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'mpo_position',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='strand_terminations',
                        to='netbox_plant_graph.connectorposition',
                    ),
                ),
                (
                    'strand',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='terminations',
                        to='netbox_plant_graph.fiberstrand',
                    ),
                ),
            ],
            options={'ordering': ('strand', 'termination_index', 'pk')},
        ),
        migrations.CreateModel(
            name='OpticalLane',
            fields=netbox_fields() + [
                ('lane_index', models.PositiveIntegerField()),
                ('local_mpo_index', models.PositiveIntegerField(default=1)),
                ('direction', models.CharField(max_length=32)),
                ('wavelength_nm', models.DecimalField(decimal_places=3, max_digits=8)),
                ('pair_key', models.CharField(blank=True, max_length=200)),
                ('nominal_rate_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'channel',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='optical_lanes',
                        to='netbox_plant_graph.transportchannel',
                    ),
                ),
                (
                    'endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='optical_lanes',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='optical_lanes',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'plane',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='optical_lanes',
                        to='netbox_plant_graph.plane',
                    ),
                ),
                (
                    'local_mpo_endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='local_optical_lanes',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'local_mpo_position',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='optical_lanes',
                        to='netbox_plant_graph.connectorposition',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'endpoint', 'lane_index', 'direction', 'pk')},
        ),
        migrations.CreateModel(
            name='TransferMap',
            fields=netbox_fields() + [
                ('map_kind', models.CharField(default='identity', max_length=64)),
                ('bidirectional', models.BooleanField(default=True)),
                ('group_key', models.CharField(blank=True, max_length=200)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'dst_position',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transfer_map_destinations',
                        to='netbox_plant_graph.connectorposition',
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transfer_maps',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'owner_node',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transfer_maps',
                        to='netbox_plant_graph.fabricnode',
                    ),
                ),
                (
                    'owner_segment',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transfer_maps',
                        to='netbox_plant_graph.fibersegment',
                    ),
                ),
                (
                    'pattern',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='transfer_maps',
                        to='netbox_plant_graph.transferpattern',
                    ),
                ),
                (
                    'src_position',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='transfer_map_sources',
                        to='netbox_plant_graph.connectorposition',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'owner_node', 'owner_segment', 'pk')},
        ),
        migrations.CreateModel(
            name='PathIntent',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('selector', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'destination_channel',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='destination_path_intents',
                        to='netbox_plant_graph.transportchannel',
                    ),
                ),
                (
                    'destination_endpoint',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='destination_path_intents',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='path_intents',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'plane',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='path_intents',
                        to='netbox_plant_graph.plane',
                    ),
                ),
                (
                    'source_channel',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='source_path_intents',
                        to='netbox_plant_graph.transportchannel',
                    ),
                ),
                (
                    'source_endpoint',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='source_path_intents',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
            ],
            options={'ordering': ('fabric', 'name')},
        ),
        migrations.CreateModel(
            name='StampTemplate',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('description', models.TextField(blank=True)),
                ('template', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='stamp_templates',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
            ],
            options={'ordering': ('name',)},
        ),
        migrations.CreateModel(
            name='StampRun',
            fields=netbox_fields() + [
                ('status', models.CharField(default='pending', max_length=32)),
                ('parameters', models.JSONField(blank=True, default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('error_detail', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'fabric',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='stamp_runs',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'template',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='stamp_runs',
                        to='netbox_plant_graph.stamptemplate',
                    ),
                ),
            ],
            options={'ordering': ('-created', '-pk')},
        ),
        migrations.AddConstraint(
            model_name='fabricarchitecture',
            constraint=models.UniqueConstraint(
                fields=('slug', 'version'),
                name='netbox_plant_graph_architecture_slug_version_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='architecturerole',
            constraint=models.UniqueConstraint(
                fields=('architecture', 'slug'),
                name='netbox_plant_graph_architecture_role_slug_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transferpattern',
            constraint=models.UniqueConstraint(
                fields=('architecture', 'slug'),
                name='netbox_plant_graph_transfer_pattern_slug_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='allocationruleset',
            constraint=models.UniqueConstraint(
                fields=('architecture', 'slug'),
                name='netbox_plant_graph_allocation_rule_set_slug_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='plane',
            constraint=models.UniqueConstraint(
                fields=('fabric', 'plane_number'),
                name='netbox_plant_graph_plane_number_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='fabricnode',
            constraint=models.UniqueConstraint(
                fields=('fabric', 'address'),
                name='netbox_plant_graph_fabric_node_address_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='endpoint',
            constraint=models.UniqueConstraint(
                fields=('fabric', 'address'),
                name='netbox_plant_graph_endpoint_address_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='connectorposition',
            constraint=models.UniqueConstraint(
                fields=('endpoint', 'position_number'),
                name='netbox_plant_graph_connector_position_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transportchannel',
            constraint=models.UniqueConstraint(
                fields=('endpoint', 'channel_index'),
                name='netbox_plant_graph_transport_channel_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='fiberstrand',
            constraint=models.UniqueConstraint(
                fields=('segment', 'strand_index'),
                name='netbox_plant_graph_fiber_strand_index_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='strandtermination',
            constraint=models.UniqueConstraint(
                fields=('strand', 'mpo_position'),
                name='netbox_plant_graph_strand_position_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='strandtermination',
            constraint=models.UniqueConstraint(
                fields=('mpo_position',),
                name='netbox_plant_graph_strand_position_single_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='opticallane',
            constraint=models.UniqueConstraint(
                fields=('endpoint', 'lane_index', 'direction'),
                name='netbox_plant_graph_optical_lane_endpoint_idx_dir_uniq',
            ),
        ),
        migrations.AddIndex(
            model_name='opticallane',
            index=models.Index(fields=['fabric', 'lane_index'], name='mpf_olane_fabric_lane_idx'),
        ),
        migrations.AddIndex(
            model_name='opticallane',
            index=models.Index(fields=['fabric', 'pair_key'], name='mpf_olane_fabric_pair_idx'),
        ),
        migrations.AddIndex(
            model_name='opticallane',
            index=models.Index(fields=['fabric', 'wavelength_nm'], name='mpf_olane_fabric_wave_idx'),
        ),
    ]
