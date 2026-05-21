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
        ('dcim', '0191_module_bay_rebuild'),
        ('extras', '0122_charfield_null_choices'),
        ('netbox_plant_graph', '0003_cable_assembly'),
    ]

    operations = [
        migrations.AddField(
            model_name='transportchannel',
            name='source_subinterface',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='dcim.interface',
            ),
        ),
        migrations.CreateModel(
            name='TransportChannelPositionMap',
            fields=netbox_fields() + [
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'channel',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='position_maps',
                        to='netbox_plant_graph.transportchannel',
                    ),
                ),
                (
                    'mpo_endpoint',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='channel_position_maps',
                        to='netbox_plant_graph.endpoint',
                    ),
                ),
                (
                    'mpo_position',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='channel_position_maps',
                        to='netbox_plant_graph.connectorposition',
                    ),
                ),
            ],
            options={'ordering': ('channel', 'mpo_position', 'pk')},
        ),
        migrations.AddConstraint(
            model_name='transportchannelpositionmap',
            constraint=models.UniqueConstraint(
                fields=('channel', 'mpo_position'),
                name='netbox_plant_graph_transport_channel_position_map_uniq',
            ),
        ),
    ]
