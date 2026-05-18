import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('dcim', '0200_populate_mac_addresses'),
        ('tenancy', '0017_natural_ordering'),
        ('netbox_plant_graph', '0010_add_tenant_fields'),
    ]

    operations = [
        # ------------------------------------------------------------------ #
        # New tables                                                          #
        # ------------------------------------------------------------------ #

        migrations.CreateModel(
            name='BreakoutProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=None)),
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('parent_speed_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('child_count', models.PositiveIntegerField(default=4)),
                ('child_speed_gbps', models.PositiveIntegerField(blank=True, null=True)),
                ('mapping_mode', models.CharField(default='sequential', max_length=20)),
                ('position_map', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ('name',),
            },
        ),

        migrations.CreateModel(
            name='DeviceBreakoutTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=None)),
                ('name', models.CharField(max_length=200, unique=True)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('device_type', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to='dcim.devicetype',
                )),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ('name',),
            },
        ),

        migrations.CreateModel(
            name='DeviceChildInterfaceSpec',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=None)),
                ('breakout_template', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='child_specs',
                    to='netbox_plant_graph.devicebreakouttemplate',
                )),
                ('parent_interface_name', models.CharField(max_length=200)),
                ('child_name_pattern', models.CharField(max_length=200)),
                ('child_count', models.PositiveIntegerField(default=4)),
                ('child_interface_type', models.CharField(default='virtual', max_length=50)),
                ('child_speed_kbps', models.PositiveIntegerField(blank=True, null=True)),
                ('fabric_plane_start', models.PositiveIntegerField(default=1)),
                ('breakout_profile', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to='netbox_plant_graph.breakoutprofile',
                )),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ('breakout_template', 'sort_order', 'parent_interface_name'),
            },
        ),

        migrations.AddConstraint(
            model_name='devicechildinterfacespec',
            constraint=models.UniqueConstraint(
                fields=('breakout_template', 'parent_interface_name'),
                name='netbox_plant_graph_devicechildinterfacespec_bt_parent_uniq',
            ),
        ),

        # ------------------------------------------------------------------ #
        # New fields on existing tables                                       #
        # ------------------------------------------------------------------ #

        migrations.AddField(
            model_name='fabric',
            name='tier_role_map',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Optional mapping of tier index (string key) to a human-readable role label.',
            ),
        ),

        migrations.AddField(
            model_name='plantnode',
            name='tenant',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='tenancy.tenant',
            ),
        ),

        migrations.AddField(
            model_name='assemblytemplate',
            name='breakout_profile',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='netbox_plant_graph.breakoutprofile',
            ),
        ),

        migrations.AddField(
            model_name='rackpopulationslot',
            name='breakout_template',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='netbox_plant_graph.devicebreakouttemplate',
            ),
        ),
    ]
