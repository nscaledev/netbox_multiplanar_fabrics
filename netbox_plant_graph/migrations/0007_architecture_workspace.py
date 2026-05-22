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
        ('netbox_plant_graph', '0006_onboarding_workspace'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ArchitectureWorkspace',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('workspace_kind', models.CharField(default='new_blueprint', max_length=32)),
                ('status', models.CharField(default='draft', max_length=32)),
                ('target_slug', models.SlugField(blank=True, max_length=200)),
                ('target_version', models.CharField(blank=True, max_length=64)),
                ('fabric_class', models.CharField(default='roce_backend', max_length=64)),
                ('source_summary', models.JSONField(blank=True, default=dict)),
                ('validation_summary', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'base_architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='derived_architecture_workspaces',
                        to='netbox_plant_graph.fabricarchitecture',
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
                    'owner',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'published_architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='publishing_architecture_workspaces',
                        to='netbox_plant_graph.fabricarchitecture',
                    ),
                ),
            ],
            options={'ordering': ('-last_updated', '-pk')},
        ),
        migrations.CreateModel(
            name='ArchitectureSourceArtifact',
            fields=netbox_fields() + [
                ('artifact_type', models.CharField(default='api_payload', max_length=64)),
                ('name', models.CharField(max_length=200)),
                ('source_uri', models.CharField(blank=True, max_length=1000)),
                ('content_sha256', models.CharField(blank=True, max_length=128)),
                ('payload_version', models.CharField(blank=True, max_length=64)),
                ('source_label', models.CharField(blank=True, max_length=200)),
                ('parser_key', models.CharField(blank=True, max_length=100)),
                ('status', models.CharField(default='received', max_length=32)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('parse_result', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='source_artifacts',
                        to='netbox_plant_graph.architectureworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', '-created', '-pk')},
        ),
        migrations.CreateModel(
            name='ArchitectureDesignComponent',
            fields=netbox_fields() + [
                ('kind', models.CharField(max_length=100)),
                ('natural_key', models.CharField(max_length=500)),
                ('desired_state', models.JSONField(blank=True, default=dict)),
                ('provenance', models.JSONField(blank=True, default=dict)),
                ('validation_status', models.CharField(default='pending', max_length=32)),
                ('validation_messages', models.JSONField(blank=True, default=list)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'source_artifact',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='design_components',
                        to='netbox_plant_graph.architecturesourceartifact',
                    ),
                ),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='design_components',
                        to='netbox_plant_graph.architectureworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', 'kind', 'natural_key', 'pk')},
        ),
        migrations.CreateModel(
            name='ArchitectureValidationRun',
            fields=netbox_fields() + [
                ('status', models.CharField(default='pending', max_length=32)),
                ('validation_kind', models.CharField(default='publish_preflight', max_length=64)),
                ('workspace_revision', models.CharField(blank=True, max_length=128)),
                ('summary', models.JSONField(blank=True, default=dict)),
                ('issues', models.JSONField(blank=True, default=list)),
                ('import_plan', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'executed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'source_artifact',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='validation_runs',
                        to='netbox_plant_graph.architecturesourceartifact',
                    ),
                ),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='validation_runs',
                        to='netbox_plant_graph.architectureworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', '-created', '-pk')},
        ),
        migrations.CreateModel(
            name='ArchitecturePublishPlan',
            fields=netbox_fields() + [
                ('status', models.CharField(default='generated', max_length=32)),
                ('plan_hash', models.CharField(max_length=128)),
                ('workspace_revision', models.CharField(blank=True, max_length=128)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('warning_acknowledgements', models.JSONField(blank=True, default=list)),
                ('publish_payload', models.JSONField(blank=True, default=dict)),
                ('validation_summary', models.JSONField(blank=True, default=dict)),
                ('import_plan', models.JSONField(blank=True, default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
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
                    'generated_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='publish_plans',
                        to='netbox_plant_graph.architectureworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', '-created', '-pk')},
        ),
        migrations.AddField(
            model_name='architectureworkspace',
            name='current_plan',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='netbox_plant_graph.architecturepublishplan',
            ),
        ),
        migrations.AddIndex(
            model_name='architectureworkspace',
            index=models.Index(fields=['status', 'fabric_class'], name='mpf_archws_status_class_idx'),
        ),
        migrations.AddIndex(
            model_name='architectureworkspace',
            index=models.Index(fields=['target_slug', 'target_version'], name='mpf_archws_target_idx'),
        ),
        migrations.AddConstraint(
            model_name='architecturedesigncomponent',
            constraint=models.UniqueConstraint(
                fields=('workspace', 'kind', 'natural_key'),
                name='netbox_plant_graph_arch_component_key_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='architecturepublishplan',
            constraint=models.UniqueConstraint(
                fields=('workspace', 'plan_hash'),
                name='netbox_plant_graph_arch_publish_plan_hash_uniq',
            ),
        ),
    ]
