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
        ('netbox_plant_graph', '0002_post_mvp_control_plane'),
    ]

    operations = [
        migrations.CreateModel(
            name='CableAssembly',
            fields=netbox_fields() + [
                ('cable_id', models.CharField(max_length=200)),
                ('manufacturer', models.CharField(blank=True, max_length=200)),
                ('serial_number', models.CharField(blank=True, max_length=200)),
                ('model_id', models.CharField(blank=True, max_length=200)),
                ('description', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'parent_cable',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='child_cables',
                        to='netbox_plant_graph.cableassembly',
                    ),
                ),
                (
                    'site',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='+',
                        to='dcim.site',
                    ),
                ),
            ],
            options={'ordering': ('site', 'cable_id')},
        ),
        migrations.AddField(
            model_name='fiberstrand',
            name='cable_id',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='fiberstrand',
            name='cable_site',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='dcim.site',
            ),
        ),
        migrations.AddConstraint(
            model_name='cableassembly',
            constraint=models.UniqueConstraint(
                fields=('site', 'cable_id'),
                name='netbox_plant_graph_cable_assembly_site_cable_id_uniq',
            ),
        ),
    ]
