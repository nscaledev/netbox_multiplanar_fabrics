from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


def _build_serializer(spec):
    meta = type(
        'Meta',
        (),
        {
            'model': spec.model,
            'fields': spec.api_fields,
            'brief_fields': spec.brief_fields,
        },
    )
    return type(spec.serializer_name, (NetBoxModelSerializer,), {'Meta': meta})


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.serializer_name] = _build_serializer(_spec)


class FabricQuerySerializer(serializers.Serializer):
    fabric = serializers.IntegerField(required=False, min_value=1)


class FabricPlaneQuerySerializer(FabricQuerySerializer):
    plane = serializers.IntegerField(required=False, min_value=1)


class PathQueryRequestSerializer(serializers.Serializer):
    source_lane = serializers.IntegerField(required=True, min_value=1)
    destination_lane = serializers.IntegerField(required=False, min_value=1)


class PathQueryStepSerializer(serializers.Serializer):
    step_type = serializers.CharField()
    object_type = serializers.CharField()
    object_id = serializers.IntegerField()
    label = serializers.CharField()
    metadata = serializers.JSONField()


class PathQueryResponseSerializer(serializers.Serializer):
    path_found = serializers.BooleanField()
    source_lane_id = serializers.IntegerField()
    destination_lane_id = serializers.IntegerField(required=False, allow_null=True)
    error = serializers.CharField(allow_blank=True)
    steps = PathQueryStepSerializer(many=True)


def _normalize_impact_target_ids(attrs, *, single_field, list_field, label):
    requested_ids = []
    single_id = attrs.get(single_field)
    if single_id is not None:
        requested_ids.append(single_id)
    requested_ids.extend(attrs.get(list_field) or ())

    target_ids = []
    seen_ids = set()
    for target_id in requested_ids:
        if target_id in seen_ids:
            continue
        target_ids.append(target_id)
        seen_ids.add(target_id)

    if not target_ids:
        raise serializers.ValidationError(
            {
                list_field: (
                    f'At least one {label} target is required. '
                    f'Provide {single_field} or {list_field}.'
                )
            }
        )

    attrs['target_ids'] = target_ids
    return attrs


class OperationalImpactRequestSerializer(serializers.Serializer):
    fabric = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    max_depth = serializers.IntegerField(required=False, min_value=1, max_value=256, default=64)


class CableAssemblyImpactRequestSerializer(OperationalImpactRequestSerializer):
    cable_assembly_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    cable_assembly_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )

    def validate(self, attrs):
        return _normalize_impact_target_ids(
            attrs,
            single_field='cable_assembly_id',
            list_field='cable_assembly_ids',
            label='cable assembly',
        )


class MPOConnectorUnplugImpactRequestSerializer(OperationalImpactRequestSerializer):
    connector_endpoint_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    connector_endpoint_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )

    def validate(self, attrs):
        return _normalize_impact_target_ids(
            attrs,
            single_field='connector_endpoint_id',
            list_field='connector_endpoint_ids',
            label='MPO connector endpoint',
        )


class OSFPTransceiverUnseatImpactRequestSerializer(OperationalImpactRequestSerializer):
    interface_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    interface_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )

    def validate(self, attrs):
        return _normalize_impact_target_ids(
            attrs,
            single_field='interface_id',
            list_field='interface_ids',
            label='Interface',
        )


class OperationalImpactReportResponseSerializer(serializers.Serializer):
    scenario = serializers.JSONField()
    scope = serializers.JSONField()
    summary = serializers.JSONField()
    simulated_components = serializers.ListField(child=serializers.JSONField())
    impacted_paths = serializers.ListField(child=serializers.JSONField())
    impacted_lanes = serializers.ListField(child=serializers.JSONField())
    impacted_channels = serializers.ListField(child=serializers.JSONField())
    impacted_endpoints = serializers.ListField(child=serializers.JSONField())
    impacted_devices = serializers.ListField(child=serializers.JSONField())
    hierarchy = serializers.ListField(child=serializers.JSONField())


class SuppressionSummaryItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    fabric_id = serializers.IntegerField()
    plane_id = serializers.IntegerField(required=False, allow_null=True)
    optical_lane_id = serializers.IntegerField(required=False, allow_null=True)
    policy_key = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    revoked_at = serializers.DateTimeField(required=False, allow_null=True)


class AuditTimelineItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    fabric_id = serializers.IntegerField(required=False, allow_null=True)
    event_type = serializers.CharField()
    outcome = serializers.CharField()
    subject_type_id = serializers.IntegerField(required=False, allow_null=True)
    subject_id = serializers.IntegerField(required=False, allow_null=True)
    actor_id = serializers.IntegerField(required=False, allow_null=True)
    created = serializers.DateTimeField()
    message = serializers.CharField(allow_blank=True)


class OperationRunSummaryItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    profile = serializers.CharField()
    status = serializers.CharField()
    fabric_id = serializers.IntegerField(required=False, allow_null=True)
    initiated_by_id = serializers.IntegerField(required=False, allow_null=True)
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    completed_at = serializers.DateTimeField(required=False, allow_null=True)


class WorkflowSummaryQuerySerializer(FabricPlaneQuerySerializer):
    pass


class CountMetricSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.IntegerField()


class WorkflowRecentEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    finding_id = serializers.IntegerField(required=False, allow_null=True)
    event_type = serializers.CharField()
    outcome = serializers.CharField()
    actor_id = serializers.IntegerField(required=False, allow_null=True)
    created = serializers.DateTimeField()
    message = serializers.CharField(allow_blank=True)
    old_status = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    new_status = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class WorkflowFindingSnapshotSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    fabric_id = serializers.IntegerField(required=False, allow_null=True)
    plane_id = serializers.IntegerField(required=False, allow_null=True)
    finding_type = serializers.CharField()
    severity = serializers.CharField()
    status = serializers.CharField()
    suppressed = serializers.BooleanField()
    message = serializers.CharField(allow_blank=True)
    age_days = serializers.IntegerField(required=False, allow_null=True)
    first_seen_at = serializers.CharField(allow_blank=True)
    last_seen_at = serializers.CharField(allow_blank=True)


class WorkflowExpiringSuppressionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    fabric_id = serializers.IntegerField()
    plane_id = serializers.IntegerField(required=False, allow_null=True)
    optical_lane_id = serializers.IntegerField(required=False, allow_null=True)
    finding_id = serializers.IntegerField(required=False, allow_null=True)
    reason = serializers.CharField(allow_blank=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    remaining_days = serializers.IntegerField(required=False, allow_null=True)


class WorkflowSummaryResponseSerializer(serializers.Serializer):
    fabric_id = serializers.IntegerField(required=False, allow_null=True)
    plane_id = serializers.IntegerField(required=False, allow_null=True)
    total_findings = serializers.IntegerField()
    active_findings = serializers.IntegerField()
    resolved_findings = serializers.IntegerField()
    suppressed_findings = serializers.IntegerField()
    stale_findings_7d = serializers.IntegerField()
    stale_findings_30d = serializers.IntegerField()
    status_counts = CountMetricSerializer(many=True)
    severity_counts = CountMetricSerializer(many=True)
    type_counts = CountMetricSerializer(many=True)
    recent_runs = OperationRunSummaryItemSerializer(many=True)
    recent_events = WorkflowRecentEventSerializer(many=True)
    oldest_active_findings = WorkflowFindingSnapshotSerializer(many=True)
    expiring_suppressions = WorkflowExpiringSuppressionSerializer(many=True)


class WorkflowFindingsQuerySerializer(FabricPlaneQuerySerializer):
    status = serializers.CharField(required=False, allow_blank=True)
    severity = serializers.CharField(required=False, allow_blank=True)
    finding_type = serializers.CharField(required=False, allow_blank=True)
    suppressed = serializers.BooleanField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=2000, default=200)


class WorkflowFindingRecordSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    fabric_id = serializers.IntegerField(required=False, allow_null=True)
    plane_id = serializers.IntegerField(required=False, allow_null=True)
    finding_type = serializers.CharField()
    severity = serializers.CharField()
    status = serializers.CharField()
    suppressed = serializers.BooleanField()
    message = serializers.CharField(allow_blank=True)
    first_seen_at = serializers.CharField(allow_blank=True)
    last_seen_at = serializers.CharField(allow_blank=True)
    suppression_rule_id = serializers.IntegerField(required=False, allow_null=True)


class WorkflowFindingTransitionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    finding_id = serializers.IntegerField(required=False, allow_null=True)
    event_type = serializers.CharField()
    outcome = serializers.CharField()
    actor_id = serializers.IntegerField(required=False, allow_null=True)
    created = serializers.DateTimeField()
    message = serializers.CharField(allow_blank=True)
    old_status = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    new_status = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class WorkflowFindingDetailResponseSerializer(serializers.Serializer):
    finding = WorkflowFindingRecordSerializer()
    transitions = WorkflowFindingTransitionSerializer(many=True)


class WorkflowRunsQuerySerializer(FabricQuerySerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=2000, default=500)


class WorkflowFindingActionRequestSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    reason = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    resolution_summary = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    days = serializers.IntegerField(required=False, min_value=0)


class WorkflowFindingActionResponseSerializer(serializers.Serializer):
    finding = WorkflowFindingRecordSerializer()


class DisjointnessExceptionRequestSerializer(serializers.Serializer):
    fabric = serializers.IntegerField(min_value=1)
    plane = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    optical_lane = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    path_hop_object_type = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    path_hop_object_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    policy_key = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True, default='disjointness')
    reason = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('metadata must be an object.')
        return value


class DisjointnessExceptionActionRequestSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class DisjointnessExceptionResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    fabric_id = serializers.IntegerField()
    plane_id = serializers.IntegerField(required=False, allow_null=True)
    optical_lane_id = serializers.IntegerField(required=False, allow_null=True)
    path_hop_object_type = serializers.CharField(allow_blank=True)
    path_hop_object_id = serializers.IntegerField(required=False, allow_null=True)
    policy_key = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    revoked_at = serializers.DateTimeField(required=False, allow_null=True)
    approved_at = serializers.DateTimeField(required=False, allow_null=True)
    approved_by_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.JSONField()


class StampTemplateExecuteRequestSerializer(serializers.Serializer):
    fabric_name = serializers.CharField(min_length=1, max_length=200)
    fabric_slug = serializers.SlugField(max_length=200)
    source_bindings = serializers.JSONField(required=False, default=dict)
    creation_options = serializers.JSONField(required=False, default=dict)

    def validate_source_bindings(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('source_bindings must be an object.')
        return value

    def validate_creation_options(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('creation_options must be an object.')
        return value


class StampTemplateExecuteResponseSerializer(serializers.Serializer):
    template_id = serializers.IntegerField()
    fabric_id = serializers.IntegerField()
    stamp_run_id = serializers.IntegerField()
    rollback_eligible = serializers.BooleanField()
    status = serializers.CharField()


class StampRunRollbackRequestSerializer(serializers.Serializer):
    delete_fabric_as_primitive = serializers.BooleanField(required=False, default=True)


class StampRunRollbackResponseSerializer(serializers.Serializer):
    stamp_run_id = serializers.IntegerField()
    already_rolled_back = serializers.BooleanField()
    rollback_mode = serializers.CharField()
    deleted_counts = serializers.DictField(child=serializers.IntegerField(), required=False, default=dict)
    deleted_total = serializers.IntegerField()


class StampPreviewRequestSerializer(serializers.Serializer):
    template_type = serializers.CharField()
    template_id = serializers.IntegerField(min_value=1)
    parameters = serializers.JSONField(required=False, default=dict)

    def validate_template_type(self, value):
        if value != 'stamp_template':
            raise serializers.ValidationError(
                f"Unsupported template_type {value!r}. Supported values: ['stamp_template']."
            )
        return value

    def validate_parameters(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('parameters must be an object.')
        return value


class StampPreviewObjectCountSerializer(serializers.Serializer):
    type = serializers.CharField()
    count = serializers.IntegerField()


class StampPreviewResponseSerializer(serializers.Serializer):
    template_type = serializers.CharField()
    template_id = serializers.IntegerField()
    template_name = serializers.CharField()
    executor = serializers.CharField()
    fabric_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    fabric_slug = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    proof_path_count = serializers.IntegerField()
    description = serializers.CharField()
    objects_to_create = StampPreviewObjectCountSerializer(many=True)


__all__ = (
    tuple(spec.serializer_name for spec in V2_OBJECT_SPECS)
    + (
        'FabricQuerySerializer',
        'FabricPlaneQuerySerializer',
        'PathQueryRequestSerializer',
        'PathQueryStepSerializer',
        'PathQueryResponseSerializer',
        'OperationalImpactRequestSerializer',
        'CableAssemblyImpactRequestSerializer',
        'MPOConnectorUnplugImpactRequestSerializer',
        'OSFPTransceiverUnseatImpactRequestSerializer',
        'OperationalImpactReportResponseSerializer',
        'SuppressionSummaryItemSerializer',
        'AuditTimelineItemSerializer',
        'OperationRunSummaryItemSerializer',
        'WorkflowSummaryQuerySerializer',
        'CountMetricSerializer',
        'WorkflowRecentEventSerializer',
        'WorkflowFindingSnapshotSerializer',
        'WorkflowExpiringSuppressionSerializer',
        'WorkflowSummaryResponseSerializer',
        'WorkflowFindingsQuerySerializer',
        'WorkflowFindingRecordSerializer',
        'WorkflowFindingTransitionSerializer',
        'WorkflowFindingDetailResponseSerializer',
        'WorkflowFindingActionRequestSerializer',
        'WorkflowFindingActionResponseSerializer',
        'WorkflowRunsQuerySerializer',
        'DisjointnessExceptionRequestSerializer',
        'DisjointnessExceptionActionRequestSerializer',
        'DisjointnessExceptionResponseSerializer',
        'StampTemplateExecuteRequestSerializer',
        'StampTemplateExecuteResponseSerializer',
        'StampRunRollbackRequestSerializer',
        'StampRunRollbackResponseSerializer',
        'StampPreviewRequestSerializer',
        'StampPreviewObjectCountSerializer',
        'StampPreviewResponseSerializer',
    )
)
