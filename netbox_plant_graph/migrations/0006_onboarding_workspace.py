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
        ('dcim', '0191_module_bay_rebuild'),
        ('extras', '0122_charfield_null_choices'),
        ('tenancy', '0017_natural_ordering'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('netbox_plant_graph', '0005_fabricarchitecture_fabric_class'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnboardingWorkspace',
            fields=netbox_fields() + [
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=200, unique=True)),
                ('status', models.CharField(default='draft', max_length=32)),
                ('fabric_class', models.CharField(default='roce_backend', max_length=64)),
                ('target_fabric_name', models.CharField(blank=True, max_length=200)),
                ('target_fabric_slug', models.SlugField(blank=True, max_length=200)),
                ('source_summary', models.JSONField(blank=True, default=dict)),
                ('readiness_summary', models.JSONField(blank=True, default=dict)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'architecture',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='onboarding_workspaces',
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
                    'fabric',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='onboarding_workspaces',
                        to='netbox_plant_graph.fabric',
                    ),
                ),
                (
                    'location',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='dcim.location',
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
                    'site',
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
            options={'ordering': ('-last_updated', '-pk')},
        ),
        migrations.CreateModel(
            name='OnboardingSourceArtifact',
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
                        to='netbox_plant_graph.onboardingworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', '-created', '-pk')},
        ),
        migrations.CreateModel(
            name='OnboardingDesignItem',
            fields=netbox_fields() + [
                ('kind', models.CharField(max_length=100)),
                ('natural_key', models.CharField(max_length=500)),
                ('desired_state', models.JSONField(blank=True, default=dict)),
                ('provenance', models.JSONField(blank=True, default=dict)),
                ('validation_status', models.CharField(default='pending', max_length=32)),
                ('validation_messages', models.JSONField(blank=True, default=list)),
                ('planned_object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'planned_object_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='contenttypes.contenttype',
                    ),
                ),
                (
                    'source_artifact',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='design_items',
                        to='netbox_plant_graph.onboardingsourceartifact',
                    ),
                ),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='design_items',
                        to='netbox_plant_graph.onboardingworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', 'kind', 'natural_key', 'pk')},
        ),
        migrations.CreateModel(
            name='OnboardingPrerequisite',
            fields=netbox_fields() + [
                ('requirement_key', models.CharField(max_length=500)),
                ('object_model', models.CharField(max_length=100)),
                ('role', models.CharField(blank=True, max_length=100)),
                ('desired_identity', models.JSONField(blank=True, default=dict)),
                ('resolution_mode', models.CharField(default='unresolved', max_length=32)),
                ('resolved_object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('planned_create', models.JSONField(blank=True, default=dict)),
                ('defer_reason', models.TextField(blank=True)),
                ('status', models.CharField(default='open', max_length=32)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'design_item',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='prerequisites',
                        to='netbox_plant_graph.onboardingdesignitem',
                    ),
                ),
                (
                    'resolved_object_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='contenttypes.contenttype',
                    ),
                ),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='prerequisites',
                        to='netbox_plant_graph.onboardingworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', 'status', 'requirement_key', 'pk')},
        ),
        migrations.CreateModel(
            name='OnboardingPlan',
            fields=netbox_fields() + [
                ('status', models.CharField(default='draft', max_length=32)),
                ('plan_hash', models.CharField(max_length=128)),
                ('workspace_revision', models.CharField(blank=True, max_length=128)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('warning_acknowledgements', models.JSONField(blank=True, default=list)),
                ('prerequisite_plan', models.JSONField(blank=True, default=dict)),
                ('stamp_preview', models.JSONField(blank=True, default=dict)),
                ('import_plan', models.JSONField(blank=True, default=dict)),
                ('audit_projection', models.JSONField(blank=True, default=dict)),
                ('impact_projection', models.JSONField(blank=True, default=dict)),
                ('readiness_projection', models.JSONField(blank=True, default=dict)),
                ('rollback_preview', models.JSONField(blank=True, default=dict)),
                ('retry_preview', models.JSONField(blank=True, default=dict)),
                ('plan_payload', models.JSONField(blank=True, default=dict)),
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
                        related_name='plans',
                        to='netbox_plant_graph.onboardingworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', '-created', '-pk')},
        ),
        migrations.CreateModel(
            name='OnboardingExecutionStage',
            fields=netbox_fields() + [
                ('stage_key', models.CharField(max_length=100)),
                ('stage_kind', models.CharField(max_length=100)),
                ('status', models.CharField(default='pending', max_length=32)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('error_detail', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'operation_run',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='netbox_plant_graph.operationrun',
                    ),
                ),
                (
                    'plan',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='stages',
                        to='netbox_plant_graph.onboardingplan',
                    ),
                ),
                (
                    'stamp_run',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='netbox_plant_graph.stamprun',
                    ),
                ),
            ],
            options={'ordering': ('plan', 'pk')},
        ),
        migrations.CreateModel(
            name='OnboardingObjectLink',
            fields=netbox_fields() + [
                ('link_kind', models.CharField(max_length=64)),
                ('object_id', models.PositiveBigIntegerField(blank=True, null=True)),
                ('label', models.CharField(blank=True, max_length=300)),
                ('external_url', models.CharField(blank=True, max_length=1000)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                (
                    'design_item',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='object_links',
                        to='netbox_plant_graph.onboardingdesignitem',
                    ),
                ),
                (
                    'object_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='+',
                        to='contenttypes.contenttype',
                    ),
                ),
                (
                    'plan',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='object_links',
                        to='netbox_plant_graph.onboardingplan',
                    ),
                ),
                (
                    'source_artifact',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='object_links',
                        to='netbox_plant_graph.onboardingsourceartifact',
                    ),
                ),
                (
                    'stage',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='object_links',
                        to='netbox_plant_graph.onboardingexecutionstage',
                    ),
                ),
                (
                    'workspace',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='object_links',
                        to='netbox_plant_graph.onboardingworkspace',
                    ),
                ),
            ],
            options={'ordering': ('workspace', 'link_kind', 'label', 'pk')},
        ),
        migrations.AddField(
            model_name='onboardingworkspace',
            name='current_plan',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='netbox_plant_graph.onboardingplan',
            ),
        ),
        migrations.AddIndex(
            model_name='onboardingworkspace',
            index=models.Index(fields=['status', 'fabric_class'], name='mpf_obws_status_class_idx'),
        ),
        migrations.AddIndex(
            model_name='onboardingworkspace',
            index=models.Index(fields=['target_fabric_slug'], name='mpf_obws_target_slug_idx'),
        ),
        migrations.AddConstraint(
            model_name='onboardingdesignitem',
            constraint=models.UniqueConstraint(
                fields=('workspace', 'kind', 'natural_key'),
                name='netbox_plant_graph_onboarding_item_natural_key_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='onboardingprerequisite',
            constraint=models.UniqueConstraint(
                fields=('workspace', 'requirement_key'),
                name='netbox_plant_graph_onboarding_prereq_key_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='onboardingplan',
            constraint=models.UniqueConstraint(
                fields=('workspace', 'plan_hash'),
                name='netbox_plant_graph_onboarding_plan_hash_uniq',
            ),
        ),
        migrations.AddConstraint(
            model_name='onboardingexecutionstage',
            constraint=models.UniqueConstraint(
                fields=('plan', 'stage_key'),
                name='netbox_plant_graph_onboarding_stage_key_uniq',
            ),
        ),
    ]
