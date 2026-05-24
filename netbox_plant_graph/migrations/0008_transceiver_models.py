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
    dependencies = [
        ('netbox_plant_graph', '0007_architecture_workspace'),
    ]

    operations = [
        migrations.CreateModel(
            name='TransceiverProfile',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('status', models.CharField(default='draft', max_length=32)),
                ('form_factor', models.CharField(default='other', max_length=32)),
                ('media_type', models.CharField(default='other', max_length=32)),
                ('aggregate_rate_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('channel_count', models.PositiveIntegerField(blank=True, null=True)),
                ('channel_rate_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('wavelength_plan', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='transceiver_profiles',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
            ],
            options={'ordering': ('name',)},
        ),
        migrations.CreateModel(
            name='TransceiverProfileModuleType',
            fields=netbox_fields() + [
                ('is_default', models.BooleanField(default=False)),
                ('role_hint', models.CharField(blank=True, max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'module_type',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='dcim.moduletype',
                    ),
                ),
                (
                    'profile',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='module_type_mappings',
                        to='netbox_plant_graph.transceiverprofile',
                    ),
                ),
            ],
            options={'ordering': ('profile', 'module_type', 'role_hint')},
        ),
        migrations.CreateModel(
            name='TransceiverConnectorProfile',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('connector_index', models.PositiveIntegerField()),
                ('connector_family', models.CharField(default='mpo-12', max_length=64)),
                ('position_count', models.PositiveIntegerField(default=0)),
                ('polish', models.CharField(default='not_specified', max_length=32)),
                ('pinning', models.CharField(default='not_specified', max_length=32)),
                ('key_orientation', models.CharField(blank=True, max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'profile',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='connector_profiles',
                        to='netbox_plant_graph.transceiverprofile',
                    ),
                ),
            ],
            options={'ordering': ('profile', 'connector_index', 'name')},
        ),
        migrations.CreateModel(
            name='TransceiverLaneProfile',
            fields=netbox_fields() + [
                ('channel_index', models.PositiveIntegerField()),
                ('lane_index', models.PositiveIntegerField()),
                ('direction', models.CharField(max_length=32)),
                ('mpo_position', models.PositiveIntegerField()),
                ('wavelength_nm', models.DecimalField(blank=True, decimal_places=3, max_digits=8, null=True)),
                ('nominal_rate_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'connector_profile',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='lane_profiles',
                        to='netbox_plant_graph.transceiverconnectorprofile',
                    ),
                ),
            ],
            options={'ordering': ('connector_profile', 'channel_index', 'direction', 'lane_index')},
        ),
        migrations.CreateModel(
            name='TransceiverConnector',
            fields=netbox_fields() + [
                ('connector_family', models.CharField(default='mpo-12', max_length=64)),
                ('position_count', models.PositiveIntegerField(default=0)),
                ('polish', models.CharField(default='not_specified', max_length=32)),
                ('pinning', models.CharField(default='not_specified', max_length=32)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'connector_profile',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='installed_connectors',
                        to='netbox_plant_graph.transceiverconnectorprofile',
                    ),
                ),
                (
                    'endpoint',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='transceiver_connector',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'module',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='dcim.module',
                    ),
                ),
            ],
            options={'ordering': ('module', 'connector_profile')},
        ),
        migrations.AddConstraint(
            model_name='transceiverprofilemoduletype',
            constraint=models.UniqueConstraint(
                fields=('profile', 'module_type', 'role_hint'),
                name='mpf_xcvr_profile_module_role_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transceiverconnectorprofile',
            constraint=models.UniqueConstraint(
                fields=('profile', 'connector_index'),
                name='mpf_xcvr_conn_profile_index_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transceiverconnectorprofile',
            constraint=models.UniqueConstraint(
                fields=('profile', 'name'),
                name='mpf_xcvr_conn_profile_name_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transceiverlaneprofile',
            constraint=models.UniqueConstraint(
                fields=('connector_profile', 'channel_index', 'direction', 'lane_index'),
                name='mpf_xcvr_lane_profile_lane_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transceiverlaneprofile',
            constraint=models.UniqueConstraint(
                fields=('connector_profile', 'direction', 'mpo_position'),
                name='mpf_xcvr_lane_profile_pos_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='transceiverconnector',
            constraint=models.UniqueConstraint(
                fields=('module', 'connector_profile'),
                name='mpf_xcvr_connector_module_profile_uniq',
            ),
        ),
    ]
