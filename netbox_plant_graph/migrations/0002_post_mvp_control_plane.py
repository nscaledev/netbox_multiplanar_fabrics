import django.db.models.deletion
import taggit.managers
import utilities.json
from django.conf import settings
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
        ('contenttypes', '0002_remove_content_type_name'),
        ('extras', '0122_charfield_null_choices'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_plant_graph', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditEvent',
            fields=netbox_fields() + [
                ('event_type', models.CharField(default='stamp', max_length=64)),
                ('subject_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('outcome', models.CharField(default='ok', max_length=32)),
                ('message', models.TextField(blank=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'actor',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='audit_events',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'subject_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='contenttypes.contenttype',
                    ),
                ),
            ],
            options={'ordering': ('-created', '-pk')},
        ),
        migrations.CreateModel(
            name='OperationRun',
            fields=netbox_fields() + [
                ('profile', models.CharField(default='generic_roce', max_length=64)),
                ('status', models.CharField(default='pending', max_length=32)),
                ('dedupe_key', models.CharField(blank=True, max_length=128)),
                ('parameters', models.JSONField(blank=True, default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('error_detail', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                (
                    'fabric',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='operation_runs',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'initiated_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={'ordering': ('-created', '-pk')},
        ),
        migrations.CreateModel(
            name='SuppressionRule',
            fields=netbox_fields() + [
                ('path_hop_object_type', models.CharField(blank=True, max_length=64)),
                ('path_hop_object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('policy_key', models.CharField(blank=True, max_length=200)),
                ('status', models.CharField(default='pending', max_length=32)),
                ('reason', models.TextField(blank=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'approved_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'fabric',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='suppression_rules',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'optical_lane',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='suppression_rules',
                        to='netbox_plant_graph.opticallane',
                    ),
                ),
                (
                    'plane',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='suppression_rules',
                        to='netbox_plant_graph.plane',
                    ),
                ),
            ],
            options={'ordering': ('-created', '-pk')},
        ),
    ]
