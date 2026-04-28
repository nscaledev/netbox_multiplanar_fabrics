from dataclasses import dataclass


@dataclass(frozen=True)
class ObjectReferencePayload:
    app_label: str
    model: str
    pk: int
    display: str
    registry_key: str | None = None
    url: str | None = None
    path_resolver_url: str | None = None
    blast_radius_url: str | None = None
    lane_drilldown_url: str | None = None
    lane_workspace_url: str | None = None
    signal_path_resolver_url: str | None = None
    signal_blast_radius_url: str | None = None
    health_url: str | None = None


@dataclass(frozen=True)
class CountMetricPayload:
    name: str
    value: int


@dataclass(frozen=True)
class LaneAttachmentGroupPayload:
    attachment_unit: ObjectReferencePayload
    lane_count: int
    lanes: tuple[ObjectReferencePayload, ...]


@dataclass(frozen=True)
class LaneDrilldownPayload:
    target: ObjectReferencePayload
    lane_index: int | None
    attachment_units: tuple[LaneAttachmentGroupPayload, ...]
    total_attachment_units: int
    total_signal_lanes: int
    available_lane_indexes: tuple[int, ...]


@dataclass(frozen=True)
class FabricHealthSummaryPayload:
    expected_plane_count: int
    planes_total: int
    healthy_planes: int
    attachment_units: int
    signal_lanes: int
    fine_edges: int


@dataclass(frozen=True)
class FabricHealthPlanePayload:
    plane: ObjectReferencePayload
    status: str
    attachment_membership_count: int
    finding_count: int
    error_count: int
    warning_count: int


@dataclass(frozen=True)
class FabricHealthPayload:
    fabric: ObjectReferencePayload | None
    status: str
    summary: FabricHealthSummaryPayload | None
    planes: tuple[FabricHealthPlanePayload, ...]
    findings: tuple[CountMetricPayload, ...]
    finding_total: int


@dataclass(frozen=True)
class LanePathSummaryPayload:
    coarse_edges_crossed: int
    transfer_maps_crossed: int
    shuffle_modules_crossed: int
    planes_touched: tuple[int, ...]


@dataclass(frozen=True)
class LanePathStepPayload:
    step_kind: str
    display: str
    edge_type: str | None = None
    object: ObjectReferencePayload | None = None
    signal_lane: ObjectReferencePayload | None = None
    attachment_unit: ObjectReferencePayload | None = None
    termination_point: ObjectReferencePayload | None = None
    plant_node: ObjectReferencePayload | None = None
    parent_coarse_edge: ObjectReferencePayload | None = None
    owner_node: ObjectReferencePayload | None = None
    owner_edge: ObjectReferencePayload | None = None


@dataclass(frozen=True)
class LanePathPayload:
    source: ObjectReferencePayload
    destination: ObjectReferencePayload | None
    source_lane_index: int | None
    destination_lane_index: int | None
    path_found: bool
    plane_id: int | None
    max_depth: int
    steps: tuple[LanePathStepPayload, ...]
    summary: LanePathSummaryPayload


@dataclass(frozen=True)
class LaneSetAttachmentPayload:
    attachment_unit: ObjectReferencePayload
    termination_point: ObjectReferencePayload
    plant_node: ObjectReferencePayload
    expected_lane_count: int
    present_lane_count: int
    mapped_lane_count: int
    lane_indexes: tuple[int, ...]
    missing_lane_indexes: tuple[int, ...]
    plane_ids: tuple[int, ...]
    topology_role: str
    position: int | None
    status: str


@dataclass(frozen=True)
class LaneSetPayload:
    scope_kind: str
    target: ObjectReferencePayload
    fabric: ObjectReferencePayload | None
    plane: ObjectReferencePayload | None
    attachment_units: tuple[LaneSetAttachmentPayload, ...]
    total_attachment_units: int
    expected_lane_total: int
    present_lane_total: int
    mapped_lane_total: int
    missing_lane_total: int
    unmatched_peer_positions: tuple[int, ...]
    plane_ids: tuple[int, ...]
    lane_map_consistency: str
    plane_consistency: str


@dataclass(frozen=True)
class LaneAllocationSummaryPayload:
    scope_kind: str
    target: ObjectReferencePayload
    expected_lane_total: int
    present_lane_total: int
    mapped_lane_total: int
    missing_lane_total: int
    unmatched_peer_positions: tuple[int, ...]
    incomplete_attachment_units: int
    lane_map_consistency: str
    plane_consistency: str


@dataclass(frozen=True)
class LaneWorkspaceGroupPayload:
    key: str
    label: str
    reference: ObjectReferencePayload | None
    attachment_unit_count: int
    expected_lane_total: int
    present_lane_total: int
    mapped_lane_total: int
    selected: bool = False
    workspace_url: str | None = None


@dataclass(frozen=True)
class LaneWorkspaceQueryPayload:
    target_registry_key: str
    target_id: str
    group_by: str
    group_by_label: str
    mode: str
    mode_label: str
    focus: str
    focus_label: str
    group_key: str | None = None
    lane_index: int | None = None
    path_lane_index: int | None = None
    plane_id: int | None = None
    compare_registry_key: str | None = None
    compare_id: str = ''
    source_finding_id: int | None = None
    export_format: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LaneWorkspaceFilterChipPayload:
    label: str
    clear_url: str | None = None


@dataclass(frozen=True)
class LaneWorkspaceFindingPayload:
    finding_kind: str
    finding_type: str
    severity: str
    status: str | None
    directness: str
    object: ObjectReferencePayload | None
    summary: str
    message: str
    action_links: tuple['ActionLinkPayload', ...]


@dataclass(frozen=True)
class LaneWorkspaceComparePayload:
    compare_target: ObjectReferencePayload
    compare_summary: str
    metrics: tuple['ComparisonMetricPayload', ...]
    regressions: tuple[str, ...]
    review_count: int
    full_compare_action: 'ActionLinkPayload | None'


@dataclass(frozen=True)
class LaneWorkspacePathGroupPayload:
    key: str
    label: str
    signature: str
    selected: bool
    attachment_unit_count: int
    lane_count: int
    attachment_unit_ids: tuple[int, ...]
    lane_indexes: tuple[int, ...]
    representative_lane_index: int | None
    representative_attachment: ObjectReferencePayload | None
    path_summary: str
    plane_ids: tuple[int, ...]
    representative_path: LanePathPayload | None
    workspace_url: str | None = None


@dataclass(frozen=True)
class LaneWorkspacePayload:
    query: LaneWorkspaceQueryPayload
    target: ObjectReferencePayload
    lane_set: LaneSetPayload
    allocation_summary: LaneAllocationSummaryPayload
    group_by: str
    group_by_label: str
    mode: str
    mode_label: str
    focus: str
    focus_label: str
    attachment_groups: tuple[LaneWorkspaceGroupPayload, ...]
    attachment_rows: tuple[LaneSetAttachmentPayload, ...]
    node_groups: tuple[LaneWorkspaceGroupPayload, ...]
    plane_groups: tuple[LaneWorkspaceGroupPayload, ...]
    path_groups: tuple[LaneWorkspacePathGroupPayload, ...]
    selected_group: LaneWorkspaceGroupPayload | None
    selected_group_rows: tuple[LaneSetAttachmentPayload, ...]
    selected_path_group: LaneWorkspacePathGroupPayload | None
    selected_path_view: LanePathPayload | None
    lane_view: LaneDrilldownPayload | None
    active_lane_index: int | None
    active_path_lane_index: int | None
    available_lane_indexes: tuple[int, ...]
    filter_chips: tuple[LaneWorkspaceFilterChipPayload, ...]
    related_findings: tuple[LaneWorkspaceFindingPayload, ...]
    next_actions: tuple['ActionLinkPayload', ...]
    compare_context: LaneWorkspaceComparePayload | None
    export_links: tuple['ActionLinkPayload', ...]
    source_finding_backlink: 'ActionLinkPayload | None'


@dataclass(frozen=True)
class UnresolvedStateRecordPayload:
    summary: ObjectReferencePayload
    fingerprint: str
    fabric: ObjectReferencePayload | None
    plane: ObjectReferencePayload | None
    owner_object: ObjectReferencePayload | None
    representative_object: ObjectReferencePayload | None
    scope_label: str
    summary_kind: str
    cause_code: str
    active: bool
    plane_ids: tuple[int, ...]
    affected_attachment_units: int | None
    affected_signal_lanes: int | None
    expected_lane_total: int | None
    present_lane_total: int | None
    mapped_lane_total: int | None
    missing_positions: tuple[int, ...]
    unmatched_peer_positions: tuple[int, ...]
    first_seen_at: str | None
    last_seen_at: str | None
    resolved_at: str | None


@dataclass(frozen=True)
class UnresolvedStateOverviewPayload:
    fabric: ObjectReferencePayload | None
    plane: ObjectReferencePayload | None
    target: ObjectReferencePayload | None
    active_total: int
    resolved_total: int
    cause_counts: tuple[CountMetricPayload, ...]
    summaries: tuple[UnresolvedStateRecordPayload, ...]


@dataclass(frozen=True)
class UnresolvedStateAgingPayload:
    summary: ObjectReferencePayload
    owner_object: ObjectReferencePayload | None
    representative_object: ObjectReferencePayload | None
    cause_code: str
    summary_kind: str
    age_days: int | None
    build_count: int
    reopen_count: int
    first_seen_at: str | None
    last_seen_at: str | None


@dataclass(frozen=True)
class UnresolvedStateDashboardPayload:
    fabric: ObjectReferencePayload | None
    active_total: int
    stale_active_7d: int
    recurring_total: int
    reopened_total: int
    cause_counts: tuple[CountMetricPayload, ...]
    oldest_active_summaries: tuple[UnresolvedStateAgingPayload, ...]


@dataclass(frozen=True)
class ActionLinkPayload:
    label: str
    url: str


@dataclass(frozen=True)
class AuditFindingImpactPayload:
    plane_ids: tuple[int, ...]
    affected_attachment_units: int | None
    expected_lane_total: int | None
    present_lane_total: int | None
    mapped_lane_total: int | None
    unmatched_peer_positions: tuple[int, ...]
    lane_map_consistency: str | None
    plane_consistency: str | None
    related_targets: tuple[ObjectReferencePayload, ...]


@dataclass(frozen=True)
class AuditFindingDetailPayload:
    finding_type: str
    severity: str
    object: ObjectReferencePayload
    message: str
    metadata: dict
    summary: str
    remediation_hints: tuple[str, ...]
    action_links: tuple[ActionLinkPayload, ...]
    impact: AuditFindingImpactPayload


@dataclass(frozen=True)
class ComparisonMetricPayload:
    name: str
    baseline_value: int
    candidate_value: int
    delta: int
    status: str


@dataclass(frozen=True)
class LaneComparisonAttachmentPayload:
    compare_key: str
    baseline_attachment: ObjectReferencePayload | None
    candidate_attachment: ObjectReferencePayload | None
    baseline_status: str | None
    candidate_status: str | None
    baseline_plane_ids: tuple[int, ...]
    candidate_plane_ids: tuple[int, ...]
    baseline_present_lane_count: int
    candidate_present_lane_count: int
    baseline_mapped_lane_count: int
    candidate_mapped_lane_count: int
    changed_fields: tuple[str, ...]


@dataclass(frozen=True)
class LaneComparisonNodePayload:
    node_label: str
    baseline_reference: ObjectReferencePayload | None
    candidate_reference: ObjectReferencePayload | None
    baseline_attachment_units: int
    candidate_attachment_units: int
    baseline_present_lane_total: int
    candidate_present_lane_total: int
    baseline_mapped_lane_total: int
    candidate_mapped_lane_total: int
    changed: bool


@dataclass(frozen=True)
class LaneComparisonReviewPayload:
    compare_key: str
    lane_index: int | None
    baseline_attachment: ObjectReferencePayload | None
    candidate_attachment: ObjectReferencePayload | None
    baseline_action: ActionLinkPayload | None
    candidate_action: ActionLinkPayload | None
    reason: str


@dataclass(frozen=True)
class PolicyComparisonDomainDeltaPayload:
    compare_key: str
    status: str
    plane_ids: tuple[int, ...]
    artifact_count: int
    attachment_unit_count: int
    signal_lane_count: int
    artifacts: tuple[ObjectReferencePayload, ...]
    representative_targets: tuple[ObjectReferencePayload, ...]


@dataclass(frozen=True)
class LaneComparePayload:
    baseline_target: ObjectReferencePayload
    candidate_target: ObjectReferencePayload
    baseline_lane_set: LaneSetPayload
    candidate_lane_set: LaneSetPayload
    baseline_summary: LaneAllocationSummaryPayload
    candidate_summary: LaneAllocationSummaryPayload
    metrics: tuple[ComparisonMetricPayload, ...]
    attachment_diffs: tuple[LaneComparisonAttachmentPayload, ...]
    node_diffs: tuple[LaneComparisonNodePayload, ...]
    representative_reviews: tuple[LaneComparisonReviewPayload, ...]
    plane_ids_added: tuple[int, ...]
    plane_ids_removed: tuple[int, ...]
    regressions: tuple[str, ...]
    policy_domain_deltas: tuple[PolicyComparisonDomainDeltaPayload, ...]
    policy_regressions: tuple[str, ...]


@dataclass(frozen=True)
class PolicyEvidenceArtifactSharePayload:
    rule_id: str
    artifact: ObjectReferencePayload
    artifact_kind: str
    plane_ids: tuple[int, ...]
    plane_pair_ids: tuple[tuple[int, int], ...]
    attachment_unit_count: int
    signal_lane_count: int
    attachment_units: tuple[ObjectReferencePayload, ...]
    representative_targets: tuple[ObjectReferencePayload, ...]


@dataclass(frozen=True)
class PolicyEvidenceEdgeBridgePayload:
    rule_id: str
    edge: ObjectReferencePayload
    edge_kind: str
    left_plane_ids: tuple[int, ...]
    right_plane_ids: tuple[int, ...]
    plane_pair_ids: tuple[tuple[int, int], ...]
    attachment_units: tuple[ObjectReferencePayload, ...]
    parent_artifacts: tuple[ObjectReferencePayload, ...]
    representative_targets: tuple[ObjectReferencePayload, ...]


@dataclass(frozen=True)
class ContaminationDomainPayload:
    domain_key: str
    fabric: ObjectReferencePayload
    plane_ids: tuple[int, ...]
    plane_pair_ids: tuple[tuple[int, int], ...]
    artifacts: tuple[ObjectReferencePayload, ...]
    attachment_units: tuple[ObjectReferencePayload, ...]
    signal_lane_count: int
    artifact_shares: tuple[PolicyEvidenceArtifactSharePayload, ...]
    edge_bridges: tuple[PolicyEvidenceEdgeBridgePayload, ...]
    representative_targets: tuple[ObjectReferencePayload, ...]
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True)
class PolicyEvaluationPayload:
    fabric: ObjectReferencePayload
    policy_mode: str
    artifact_shares: tuple[PolicyEvidenceArtifactSharePayload, ...]
    edge_bridges: tuple[PolicyEvidenceEdgeBridgePayload, ...]
    contamination_domains: tuple[ContaminationDomainPayload, ...]
    findings: tuple[dict, ...]


@dataclass(frozen=True)
class PolicySummaryPayload:
    fabric: ObjectReferencePayload
    policy_mode: str
    plane: ObjectReferencePayload | None
    artifact_share_count: int
    edge_bridge_count: int
    contamination_domain_count: int
    plane_pair_count: int
    largest_domain_attachment_units: int
    largest_domain_signal_lanes: int
    rule_counts: tuple[CountMetricPayload, ...]


@dataclass(frozen=True)
class PolicyPlanePairReportPayload:
    plane_pair_ids: tuple[int, int]
    domain_count: int
    uncovered_domain_count: int
    edge_bridge_count: int
    uncovered_edge_bridge_count: int
    artifact_share_count: int
    uncovered_artifact_share_count: int
    active_exception_count: int
    covered_exception_count: int


@dataclass(frozen=True)
class PolicyDomainReportPayload:
    domain_key: str
    plane_ids: tuple[int, ...]
    plane_pair_ids: tuple[tuple[int, int], ...]
    artifact_count: int
    attachment_unit_count: int
    signal_lane_count: int
    matched_exception_count: int
    coverage_status: str
    highest_risk_severity: str
    artifacts: tuple[ObjectReferencePayload, ...]
    representative_targets: tuple[ObjectReferencePayload, ...]


@dataclass(frozen=True)
class PolicyActiveFindingPayload:
    finding: ObjectReferencePayload
    affected_object: ObjectReferencePayload | None
    plane: ObjectReferencePayload | None
    finding_type: str
    severity: str
    status: str
    age_days: int | None
    first_seen_at: str | None


@dataclass(frozen=True)
class PolicyDashboardPayload:
    fabric: ObjectReferencePayload
    policy_mode: str
    active_exception_count: int
    covered_exception_count: int
    drifted_exception_count: int
    contamination_domain_count: int
    uncovered_domain_count: int
    uncovered_edge_bridge_count: int
    uncovered_artifact_share_count: int
    plane_pairs: tuple[PolicyPlanePairReportPayload, ...]
    top_domains: tuple[PolicyDomainReportPayload, ...]
    oldest_active_findings: tuple[PolicyActiveFindingPayload, ...]


@dataclass(frozen=True)
class AuditWorkflowRunPayload:
    run: ObjectReferencePayload
    scope_label: str
    status: str
    trigger_mode: str
    started_at: str | None
    completed_at: str | None
    finding_count: int
    new_count: int
    reopened_count: int
    resolved_count: int


@dataclass(frozen=True)
class AuditWorkflowEventPayload:
    finding: ObjectReferencePayload
    event_type: str
    actor_display: str | None
    created_at: str | None
    message: str
    run: ObjectReferencePayload | None = None
    old_status: str | None = None
    new_status: str | None = None


@dataclass(frozen=True)
class AuditWorkflowFindingPayload:
    finding: ObjectReferencePayload
    affected_object: ObjectReferencePayload | None
    status: str
    severity: str
    age_days: int | None
    first_seen_at: str | None
    last_seen_at: str | None


@dataclass(frozen=True)
class AuditWorkflowSuppressionPayload:
    suppression: ObjectReferencePayload
    finding: ObjectReferencePayload
    expires_at: str | None
    remaining_days: int | None
    reason: str


@dataclass(frozen=True)
class AuditWorkflowChurnWindowPayload:
    label: str
    days: int
    opened_count: int
    reopened_count: int
    resolved_count: int
    auto_resolved_count: int
    suppressed_count: int


@dataclass(frozen=True)
class AuditWorkflowSummaryPayload:
    fabric: ObjectReferencePayload | None
    total_findings: int
    active_findings: int
    resolved_findings: int
    suppressed_findings: int
    stale_findings_7d: int
    stale_findings_30d: int
    status_counts: tuple[CountMetricPayload, ...]
    severity_counts: tuple[CountMetricPayload, ...]
    type_counts: tuple[CountMetricPayload, ...]
    churn_windows: tuple[AuditWorkflowChurnWindowPayload, ...]
    recent_runs: tuple[AuditWorkflowRunPayload, ...]
    recent_events: tuple[AuditWorkflowEventPayload, ...]
    oldest_active_findings: tuple[AuditWorkflowFindingPayload, ...]
    expiring_suppressions: tuple[AuditWorkflowSuppressionPayload, ...]


@dataclass(frozen=True)
class AuditWorkflowFindingRecordPayload:
    finding: ObjectReferencePayload
    affected_object: ObjectReferencePayload | None
    fabric: ObjectReferencePayload | None
    plane: ObjectReferencePayload | None
    status: str
    severity: str
    active: bool
    finding_type: str
    message: str
    age_days: int | None
    first_seen_at: str | None
    last_seen_at: str | None
    resolved_at: str | None
    assigned_to_display: str | None
    acknowledged_by_display: str | None
    active_suppression: AuditWorkflowSuppressionPayload | None


@dataclass(frozen=True)
class AuditWorkflowFindingDetailPayload:
    finding: AuditWorkflowFindingRecordPayload
    recent_events: tuple[AuditWorkflowEventPayload, ...]


@dataclass(frozen=True)
class UnresolvedCandidatePayload:
    fabric_id: int
    summary_kind: str
    cause_code: str
    scope_object: object | None
    scope_label: str
    owner_object: object | None
    representative_object: object | None
    plane_ids: tuple[int, ...]
    selector: dict
    anchors: dict
    impact: dict
    metadata: dict


@dataclass(frozen=True)
class UnresolvedSyncResultPayload:
    candidate_count: int
    created_count: int
    updated_count: int
    reopened_count: int
    resolved_count: int
    observation_count: int
    comparable_scope: bool


@dataclass(frozen=True)
class UnresolvedSummaryRecordPayload:
    summary: ObjectReferencePayload
    owner: ObjectReferencePayload | None
    representative: ObjectReferencePayload | None
    summary_kind: str
    cause_code: str
    active: bool
    age_days: int | None
    first_seen_at: str | None
    last_seen_at: str | None
    resolved_at: str | None
    plane_ids: tuple[int, ...]
    impact: dict


@dataclass(frozen=True)
class TargetUnresolvedSummaryPayload:
    target: ObjectReferencePayload
    active_summary_count: int
    oldest_active_age_days: int | None
    cause_counts: tuple[CountMetricPayload, ...]
    summaries: tuple[UnresolvedSummaryRecordPayload, ...]


@dataclass(frozen=True)
class FabricUnresolvedSummaryPayload:
    fabric: ObjectReferencePayload
    active_summary_count: int
    resolved_summary_count: int
    oldest_active_age_days: int | None
    cause_counts: tuple[CountMetricPayload, ...]
    summaries: tuple[UnresolvedSummaryRecordPayload, ...]
