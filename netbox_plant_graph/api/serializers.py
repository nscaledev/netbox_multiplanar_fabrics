from rest_framework import serializers
from rest_framework.relations import HyperlinkedIdentityField
from netbox.api.serializers import NetBoxModelSerializer

from netbox_plant_graph.object_registry import API_OBJECT_SPECS


def build_serializer_class(spec):
    meta_class = type(
        'Meta',
        (),
        {
            'model': spec.model,
            'fields': spec.api.fields,
            'brief_fields': spec.api.brief_fields,
        },
    )
    return type(
        spec.api.serializer_name,
        (NetBoxModelSerializer,),
        {
            '__module__': __name__,
            'url': HyperlinkedIdentityField(view_name=spec.api.detail_view_name),
            'Meta': meta_class,
        },
    )


SERIALIZER_CLASS_MAP = {}
for object_spec in API_OBJECT_SPECS:
    serializer_class = build_serializer_class(object_spec)
    SERIALIZER_CLASS_MAP[object_spec.registry_key] = serializer_class
    globals()[object_spec.api.serializer_name] = serializer_class


class AuditFindingActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)


class AuditFindingSuppressActionSerializer(AuditFindingActionSerializer):
    days = serializers.IntegerField(required=False, min_value=0)


class DisjointnessExceptionActionSerializer(serializers.Serializer):
    pass


class ObjectReferencePayloadSerializer(serializers.Serializer):
    app_label = serializers.CharField()
    model = serializers.CharField()
    pk = serializers.IntegerField()
    display = serializers.CharField()
    registry_key = serializers.CharField(allow_null=True, required=False)
    url = serializers.CharField(allow_null=True, required=False)
    path_resolver_url = serializers.CharField(allow_null=True, required=False)
    blast_radius_url = serializers.CharField(allow_null=True, required=False)
    lane_drilldown_url = serializers.CharField(allow_null=True, required=False)
    lane_workspace_url = serializers.CharField(allow_null=True, required=False)
    signal_path_resolver_url = serializers.CharField(allow_null=True, required=False)
    signal_blast_radius_url = serializers.CharField(allow_null=True, required=False)
    health_url = serializers.CharField(allow_null=True, required=False)


class CountMetricPayloadSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.IntegerField()


class AuditWorkflowRunPayloadSerializer(serializers.Serializer):
    run = ObjectReferencePayloadSerializer()
    scope_label = serializers.CharField()
    status = serializers.CharField()
    trigger_mode = serializers.CharField()
    started_at = serializers.CharField(allow_null=True)
    completed_at = serializers.CharField(allow_null=True)
    finding_count = serializers.IntegerField()
    new_count = serializers.IntegerField()
    reopened_count = serializers.IntegerField()
    resolved_count = serializers.IntegerField()


class AuditWorkflowEventPayloadSerializer(serializers.Serializer):
    finding = ObjectReferencePayloadSerializer()
    event_type = serializers.CharField()
    actor_display = serializers.CharField(allow_null=True)
    created_at = serializers.CharField(allow_null=True)
    message = serializers.CharField(allow_blank=True)
    run = ObjectReferencePayloadSerializer(allow_null=True)
    old_status = serializers.CharField(allow_null=True)
    new_status = serializers.CharField(allow_null=True)


class AuditWorkflowFindingPayloadSerializer(serializers.Serializer):
    finding = ObjectReferencePayloadSerializer()
    affected_object = ObjectReferencePayloadSerializer(allow_null=True)
    status = serializers.CharField()
    severity = serializers.CharField()
    age_days = serializers.IntegerField(allow_null=True)
    first_seen_at = serializers.CharField(allow_null=True)
    last_seen_at = serializers.CharField(allow_null=True)


class AuditWorkflowSuppressionPayloadSerializer(serializers.Serializer):
    suppression = ObjectReferencePayloadSerializer()
    finding = ObjectReferencePayloadSerializer()
    expires_at = serializers.CharField(allow_null=True)
    remaining_days = serializers.IntegerField(allow_null=True)
    reason = serializers.CharField(allow_blank=True)


class AuditWorkflowChurnWindowPayloadSerializer(serializers.Serializer):
    label = serializers.CharField()
    days = serializers.IntegerField()
    opened_count = serializers.IntegerField()
    reopened_count = serializers.IntegerField()
    resolved_count = serializers.IntegerField()
    auto_resolved_count = serializers.IntegerField()
    suppressed_count = serializers.IntegerField()


class AuditWorkflowSummaryPayloadSerializer(serializers.Serializer):
    fabric = ObjectReferencePayloadSerializer(allow_null=True)
    total_findings = serializers.IntegerField()
    active_findings = serializers.IntegerField()
    resolved_findings = serializers.IntegerField()
    suppressed_findings = serializers.IntegerField()
    stale_findings_7d = serializers.IntegerField()
    stale_findings_30d = serializers.IntegerField()
    status_counts = CountMetricPayloadSerializer(many=True)
    severity_counts = CountMetricPayloadSerializer(many=True)
    type_counts = CountMetricPayloadSerializer(many=True)
    churn_windows = AuditWorkflowChurnWindowPayloadSerializer(many=True)
    recent_runs = AuditWorkflowRunPayloadSerializer(many=True)
    recent_events = AuditWorkflowEventPayloadSerializer(many=True)
    oldest_active_findings = AuditWorkflowFindingPayloadSerializer(many=True)
    expiring_suppressions = AuditWorkflowSuppressionPayloadSerializer(many=True)


class AuditWorkflowFindingRecordPayloadSerializer(serializers.Serializer):
    finding = ObjectReferencePayloadSerializer()
    affected_object = ObjectReferencePayloadSerializer(allow_null=True)
    fabric = ObjectReferencePayloadSerializer(allow_null=True)
    plane = ObjectReferencePayloadSerializer(allow_null=True)
    status = serializers.CharField()
    severity = serializers.CharField()
    active = serializers.BooleanField()
    finding_type = serializers.CharField()
    message = serializers.CharField()
    age_days = serializers.IntegerField(allow_null=True)
    first_seen_at = serializers.CharField(allow_null=True)
    last_seen_at = serializers.CharField(allow_null=True)
    resolved_at = serializers.CharField(allow_null=True)
    assigned_to_display = serializers.CharField(allow_null=True)
    acknowledged_by_display = serializers.CharField(allow_null=True)
    active_suppression = AuditWorkflowSuppressionPayloadSerializer(allow_null=True)


class AuditWorkflowFindingDetailPayloadSerializer(serializers.Serializer):
    finding = AuditWorkflowFindingRecordPayloadSerializer()
    recent_events = AuditWorkflowEventPayloadSerializer(many=True)


class AuditWorkflowSummaryQuerySerializer(serializers.Serializer):
    fabric = serializers.IntegerField(required=False)


class AuditWorkflowFindingSearchQuerySerializer(serializers.Serializer):
    fabric = serializers.IntegerField(required=False)
    plane = serializers.IntegerField(required=False)
    status = serializers.CharField(required=False)
    severity = serializers.CharField(required=False)
    finding_type = serializers.CharField(required=False)
    active = serializers.BooleanField(required=False, allow_null=True, default=None)
    assigned_to = serializers.IntegerField(required=False)
    acknowledged_by = serializers.IntegerField(required=False)
    object_type = serializers.IntegerField(required=False)
    object_id = serializers.IntegerField(required=False)
    suppressed = serializers.BooleanField(required=False, allow_null=True, default=None)
    min_age_days = serializers.IntegerField(required=False, min_value=0)
    search = serializers.CharField(required=False, allow_blank=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=200, default=25)


class AuditWorkflowRunTimelineQuerySerializer(serializers.Serializer):
    fabric = serializers.IntegerField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=200, default=10)


# --- Planning / Stamp Action Serializers ---


class DeploymentPlanActionSerializer(serializers.Serializer):
    """Empty serializer for plan execute/rollback actions."""
    pass


class AssemblyTemplateStampSerializer(serializers.Serializer):
    """Request serializer for stamping a passive device from an assembly template."""
    site = serializers.IntegerField(help_text='Site ID for the stamped device.')
    location = serializers.IntegerField(required=False, help_text='Location ID for the stamped device.')
    rack = serializers.IntegerField(required=False, help_text='Rack ID for the stamped device.')
    position = serializers.DecimalField(
        required=False, max_digits=4, decimal_places=1,
        help_text='U position in rack.',
    )
    face = serializers.CharField(required=False, default='front', help_text='Rack face (front/rear).')
    device_role = serializers.IntegerField(help_text='DeviceRole ID for the stamped device.')
    name = serializers.CharField(help_text='Name for the stamped device.')
    plan = serializers.IntegerField(required=False, help_text='DeploymentPlan ID for provenance tracking.')
    dry_run = serializers.BooleanField(required=False, default=False)


class SpatialTemplateStampSerializer(serializers.Serializer):
    """Request serializer for stamping a spatial template."""
    site = serializers.IntegerField(help_text='Site ID to stamp under.')
    parent_location = serializers.IntegerField(required=False, help_text='Parent Location ID to stamp under.')
    plan = serializers.IntegerField(required=False, help_text='DeploymentPlan ID for provenance tracking.')
    dry_run = serializers.BooleanField(required=False, default=False)
    sync_floorplan = serializers.BooleanField(required=False, default=True)
    force_floorplan_sync = serializers.BooleanField(required=False, default=False)


class SpatialPlacementReconcileSerializer(serializers.Serializer):
    scope_type = serializers.ChoiceField(choices=(('site', 'Site'), ('location', 'Location')))
    scope_id = serializers.IntegerField(min_value=1)
    create_missing = serializers.BooleanField(required=False, default=False)


class SpatialPlacementReconcilePayloadSerializer(serializers.Serializer):
    scope_type = serializers.CharField()
    scope_id = serializers.IntegerField()
    floorplan_id = serializers.IntegerField(allow_null=True)
    updated_placements = serializers.IntegerField()
    created_placements = serializers.IntegerField()
    skipped_unmanaged = serializers.IntegerField()
    skipped_missing_rack = serializers.IntegerField()
    skipped_missing_placement = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.CharField(), allow_empty=True)


class RackPopulationTemplateStampSerializer(serializers.Serializer):
    """Request serializer for stamping a rack population template."""
    rack = serializers.IntegerField(help_text='Rack ID to populate.')
    plan = serializers.IntegerField(required=False, help_text='DeploymentPlan ID for provenance tracking.')
    dry_run = serializers.BooleanField(required=False, default=False)


class StampPreviewSerializer(serializers.Serializer):
    """Request serializer for the unified stamp preview endpoint."""
    TEMPLATE_TYPE_CHOICES = [
        ('assembly', 'Assembly Template'),
        ('spatial', 'Spatial Template'),
        ('rack_population', 'Rack Population Template'),
    ]
    template_type = serializers.ChoiceField(
        choices=TEMPLATE_TYPE_CHOICES,
        help_text='Type of template to preview.',
    )
    template_id = serializers.IntegerField(help_text='PK of the template to preview.')
    parameters = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        default=dict,
        help_text='Stamp parameters (site, rack, name, etc.) as key-value pairs.',
    )
