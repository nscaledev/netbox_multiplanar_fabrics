import strawberry

from netbox_plant_graph.services.graph.payloads import (
    AuditWorkflowChurnWindowPayload,
    AuditWorkflowFindingDetailPayload,
    AuditWorkflowEventPayload,
    AuditWorkflowFindingRecordPayload,
    AuditWorkflowFindingPayload,
    AuditWorkflowRunPayload,
    AuditWorkflowSummaryPayload,
    AuditWorkflowSuppressionPayload,
    ComparisonMetricPayload,
    CountMetricPayload,
    FabricHealthPayload,
    FabricHealthPlanePayload,
    FabricHealthSummaryPayload,
    LaneAllocationSummaryPayload,
    LaneComparePayload,
    LaneComparisonAttachmentPayload,
    LaneComparisonNodePayload,
    LaneComparisonReviewPayload,
    LaneAttachmentGroupPayload,
    LaneDrilldownPayload,
    LanePathPayload,
    LanePathStepPayload,
    LanePathSummaryPayload,
    LaneSetAttachmentPayload,
    LaneSetPayload,
    ObjectReferencePayload,
    PolicyActiveFindingPayload,
    PolicyDashboardPayload,
    PolicyDomainReportPayload,
    PolicyEvidenceArtifactSharePayload,
    PolicyEvidenceEdgeBridgePayload,
    PolicyPlanePairReportPayload,
    PolicySummaryPayload,
    ContaminationDomainPayload,
    PolicyComparisonDomainDeltaPayload,
)


@strawberry.type
class ObjectReferenceType:
    app_label: str
    model: str
    pk: int
    display: str
    registry_key: str | None
    url: str | None
    path_resolver_url: str | None
    blast_radius_url: str | None
    lane_drilldown_url: str | None
    lane_workspace_url: str | None
    signal_path_resolver_url: str | None
    signal_blast_radius_url: str | None
    health_url: str | None
    endpoint_label: str | None
    endpoint_context: str | None
    endpoint_device: str | None
    endpoint_device_type: str | None
    endpoint_role: str | None
    endpoint_rack: str | None
    endpoint_source: str | None
    endpoint_module: str | None
    wavelength_nm: int | None


@strawberry.type
class CountMetricType:
    name: str
    value: int


@strawberry.type
class LaneAttachmentGroupType:
    attachment_unit: ObjectReferenceType
    lane_count: int
    lanes: list[ObjectReferenceType]


@strawberry.type
class LaneDrilldownType:
    target: ObjectReferenceType
    lane_index: int | None
    attachment_units: list[LaneAttachmentGroupType]
    total_attachment_units: int
    total_signal_lanes: int
    available_lane_indexes: list[int]


@strawberry.type
class FabricHealthSummaryType:
    expected_plane_count: int
    planes_total: int
    healthy_planes: int
    attachment_units: int
    signal_lanes: int
    fine_edges: int


@strawberry.type
class FabricHealthPlaneType:
    plane: ObjectReferenceType
    status: str
    attachment_membership_count: int
    finding_count: int
    error_count: int
    warning_count: int


@strawberry.type
class FabricHealthType:
    fabric: ObjectReferenceType | None
    status: str
    summary: FabricHealthSummaryType | None
    planes: list[FabricHealthPlaneType]
    findings: list[CountMetricType]
    finding_total: int


@strawberry.type
class LanePathSummaryType:
    coarse_edges_crossed: int
    transfer_maps_crossed: int
    lane_maps_crossed: int
    shuffle_modules_crossed: int
    planes_touched: list[int]


@strawberry.type
class LanePathStepType:
    step_kind: str
    display: str
    edge_type: str | None
    object: ObjectReferenceType | None
    signal_lane: ObjectReferenceType | None
    attachment_unit: ObjectReferenceType | None
    termination_point: ObjectReferenceType | None
    plant_node: ObjectReferenceType | None
    parent_coarse_edge: ObjectReferenceType | None
    owner_node: ObjectReferenceType | None
    owner_edge: ObjectReferenceType | None


@strawberry.type
class LanePathType:
    source: ObjectReferenceType
    destination: ObjectReferenceType | None
    source_lane_index: int | None
    destination_lane_index: int | None
    path_found: bool
    plane_id: int | None
    max_depth: int
    steps: list[LanePathStepType]
    summary: LanePathSummaryType


@strawberry.type
class LaneSetAttachmentType:
    attachment_unit: ObjectReferenceType
    termination_point: ObjectReferenceType
    plant_node: ObjectReferenceType
    expected_lane_count: int
    present_lane_count: int
    mapped_lane_count: int
    lane_indexes: list[int]
    missing_lane_indexes: list[int]
    plane_ids: list[int]
    topology_role: str
    position: int | None
    status: str


@strawberry.type
class LaneSetType:
    scope_kind: str
    target: ObjectReferenceType
    fabric: ObjectReferenceType | None
    plane: ObjectReferenceType | None
    attachment_units: list[LaneSetAttachmentType]
    total_attachment_units: int
    expected_lane_total: int
    present_lane_total: int
    mapped_lane_total: int
    missing_lane_total: int
    unmatched_peer_positions: list[int]
    plane_ids: list[int]
    lane_map_consistency: str
    plane_consistency: str


@strawberry.type
class LaneAllocationSummaryType:
    scope_kind: str
    target: ObjectReferenceType
    expected_lane_total: int
    present_lane_total: int
    mapped_lane_total: int
    missing_lane_total: int
    unmatched_peer_positions: list[int]
    incomplete_attachment_units: int
    lane_map_consistency: str
    plane_consistency: str


@strawberry.type
class ComparisonMetricType:
    name: str
    baseline_value: int
    candidate_value: int
    delta: int
    status: str


@strawberry.type
class LaneComparisonAttachmentType:
    compare_key: str
    baseline_attachment: ObjectReferenceType | None
    candidate_attachment: ObjectReferenceType | None
    baseline_status: str | None
    candidate_status: str | None
    baseline_plane_ids: list[int]
    candidate_plane_ids: list[int]
    baseline_present_lane_count: int
    candidate_present_lane_count: int
    baseline_mapped_lane_count: int
    candidate_mapped_lane_count: int
    changed_fields: list[str]


@strawberry.type
class LaneComparisonNodeType:
    node_label: str
    baseline_reference: ObjectReferenceType | None
    candidate_reference: ObjectReferenceType | None
    baseline_attachment_units: int
    candidate_attachment_units: int
    baseline_present_lane_total: int
    candidate_present_lane_total: int
    baseline_mapped_lane_total: int
    candidate_mapped_lane_total: int
    changed: bool


@strawberry.type
class ActionLinkType:
    label: str
    url: str


@strawberry.type
class LaneComparisonReviewType:
    compare_key: str
    lane_index: int | None
    baseline_attachment: ObjectReferenceType | None
    candidate_attachment: ObjectReferenceType | None
    baseline_action: ActionLinkType | None
    candidate_action: ActionLinkType | None
    reason: str


@strawberry.type
class LaneCompareType:
    baseline_target: ObjectReferenceType
    candidate_target: ObjectReferenceType
    baseline_summary: LaneAllocationSummaryType
    candidate_summary: LaneAllocationSummaryType
    metrics: list[ComparisonMetricType]
    attachment_diffs: list[LaneComparisonAttachmentType]
    node_diffs: list[LaneComparisonNodeType]
    representative_reviews: list[LaneComparisonReviewType]
    plane_ids_added: list[int]
    plane_ids_removed: list[int]
    regressions: list[str]
    policy_domain_deltas: list["PolicyComparisonDomainDeltaType"]
    policy_regressions: list[str]


@strawberry.type
class PolicyComparisonDomainDeltaType:
    compare_key: str
    status: str
    plane_ids: list[int]
    artifact_count: int
    attachment_unit_count: int
    signal_lane_count: int
    artifacts: list[ObjectReferenceType]
    representative_targets: list[ObjectReferenceType]


@strawberry.type
class PolicyEvidenceArtifactShareType:
    rule_id: str
    artifact: ObjectReferenceType
    artifact_kind: str
    plane_ids: list[int]
    plane_pair_ids: list[list[int]]
    attachment_unit_count: int
    signal_lane_count: int
    attachment_units: list[ObjectReferenceType]
    representative_targets: list[ObjectReferenceType]


@strawberry.type
class PolicyEvidenceEdgeBridgeType:
    rule_id: str
    edge: ObjectReferenceType
    edge_kind: str
    left_plane_ids: list[int]
    right_plane_ids: list[int]
    plane_pair_ids: list[list[int]]
    attachment_units: list[ObjectReferenceType]
    parent_artifacts: list[ObjectReferenceType]
    representative_targets: list[ObjectReferenceType]


@strawberry.type
class ContaminationDomainType:
    domain_key: str
    fabric: ObjectReferenceType
    plane_ids: list[int]
    plane_pair_ids: list[list[int]]
    artifacts: list[ObjectReferenceType]
    attachment_units: list[ObjectReferenceType]
    signal_lane_count: int
    artifact_shares: list[PolicyEvidenceArtifactShareType]
    edge_bridges: list[PolicyEvidenceEdgeBridgeType]
    representative_targets: list[ObjectReferenceType]
    evidence_keys: list[str]


@strawberry.type
class PolicySummaryType:
    fabric: ObjectReferenceType
    policy_mode: str
    plane: ObjectReferenceType | None
    artifact_share_count: int
    edge_bridge_count: int
    contamination_domain_count: int
    plane_pair_count: int
    largest_domain_attachment_units: int
    largest_domain_signal_lanes: int
    rule_counts: list[CountMetricType]


@strawberry.type
class PolicyPlanePairReportType:
    plane_pair_ids: list[int]
    domain_count: int
    uncovered_domain_count: int
    edge_bridge_count: int
    uncovered_edge_bridge_count: int
    artifact_share_count: int
    uncovered_artifact_share_count: int
    active_exception_count: int
    covered_exception_count: int


@strawberry.type
class PolicyDomainReportType:
    domain_key: str
    plane_ids: list[int]
    plane_pair_ids: list[list[int]]
    artifact_count: int
    attachment_unit_count: int
    signal_lane_count: int
    matched_exception_count: int
    coverage_status: str
    highest_risk_severity: str
    artifacts: list[ObjectReferenceType]
    representative_targets: list[ObjectReferenceType]


@strawberry.type
class PolicyActiveFindingType:
    finding: ObjectReferenceType
    affected_object: ObjectReferenceType | None
    plane: ObjectReferenceType | None
    finding_type: str
    severity: str
    status: str
    age_days: int | None
    first_seen_at: str | None


@strawberry.type
class PolicyDashboardType:
    fabric: ObjectReferenceType
    policy_mode: str
    active_exception_count: int
    covered_exception_count: int
    drifted_exception_count: int
    contamination_domain_count: int
    uncovered_domain_count: int
    uncovered_edge_bridge_count: int
    uncovered_artifact_share_count: int
    plane_pairs: list[PolicyPlanePairReportType]
    top_domains: list[PolicyDomainReportType]
    oldest_active_findings: list[PolicyActiveFindingType]


@strawberry.type
class AuditWorkflowRunType:
    run: ObjectReferenceType
    scope_label: str
    status: str
    trigger_mode: str
    started_at: str | None
    completed_at: str | None
    finding_count: int
    new_count: int
    reopened_count: int
    resolved_count: int


@strawberry.type
class AuditWorkflowEventType:
    finding: ObjectReferenceType
    event_type: str
    actor_display: str | None
    created_at: str | None
    message: str
    run: ObjectReferenceType | None
    old_status: str | None
    new_status: str | None


@strawberry.type
class AuditWorkflowFindingType:
    finding: ObjectReferenceType
    affected_object: ObjectReferenceType | None
    status: str
    severity: str
    age_days: int | None
    first_seen_at: str | None
    last_seen_at: str | None


@strawberry.type
class AuditWorkflowSuppressionType:
    suppression: ObjectReferenceType
    finding: ObjectReferenceType
    expires_at: str | None
    remaining_days: int | None
    reason: str


@strawberry.type
class AuditWorkflowChurnWindowType:
    label: str
    days: int
    opened_count: int
    reopened_count: int
    resolved_count: int
    auto_resolved_count: int
    suppressed_count: int


@strawberry.type
class AuditWorkflowSummaryType:
    fabric: ObjectReferenceType | None
    total_findings: int
    active_findings: int
    resolved_findings: int
    suppressed_findings: int
    stale_findings_7d: int
    stale_findings_30d: int
    status_counts: list[CountMetricType]
    severity_counts: list[CountMetricType]
    type_counts: list[CountMetricType]
    churn_windows: list[AuditWorkflowChurnWindowType]
    recent_runs: list[AuditWorkflowRunType]
    recent_events: list[AuditWorkflowEventType]
    oldest_active_findings: list[AuditWorkflowFindingType]
    expiring_suppressions: list[AuditWorkflowSuppressionType]


@strawberry.type
class AuditWorkflowFindingRecordType:
    finding: ObjectReferenceType
    affected_object: ObjectReferenceType | None
    fabric: ObjectReferenceType | None
    plane: ObjectReferenceType | None
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
    active_suppression: AuditWorkflowSuppressionType | None


@strawberry.type
class AuditWorkflowFindingDetailType:
    finding: AuditWorkflowFindingRecordType
    recent_events: list[AuditWorkflowEventType]


def _object_reference_type(payload: ObjectReferencePayload) -> ObjectReferenceType:
    return ObjectReferenceType(**payload.__dict__)


def _count_metric_type(payload: CountMetricPayload) -> CountMetricType:
    return CountMetricType(name=payload.name, value=payload.value)


def _action_link_type(payload) -> ActionLinkType:
    return ActionLinkType(label=payload.label, url=payload.url)


def lane_drilldown_type(payload: LaneDrilldownPayload) -> LaneDrilldownType:
    return LaneDrilldownType(
        target=_object_reference_type(payload.target),
        lane_index=payload.lane_index,
        attachment_units=[
            LaneAttachmentGroupType(
                attachment_unit=_object_reference_type(group.attachment_unit),
                lane_count=group.lane_count,
                lanes=[_object_reference_type(lane) for lane in group.lanes],
            )
            for group in payload.attachment_units
        ],
        total_attachment_units=payload.total_attachment_units,
        total_signal_lanes=payload.total_signal_lanes,
        available_lane_indexes=list(payload.available_lane_indexes),
    )


def fabric_health_type(payload: FabricHealthPayload) -> FabricHealthType:
    return FabricHealthType(
        fabric=_object_reference_type(payload.fabric) if payload.fabric is not None else None,
        status=payload.status,
        summary=(
            FabricHealthSummaryType(**payload.summary.__dict__)
            if payload.summary is not None
            else None
        ),
        planes=[
            FabricHealthPlaneType(
                plane=_object_reference_type(plane.plane),
                status=plane.status,
                attachment_membership_count=plane.attachment_membership_count,
                finding_count=plane.finding_count,
                error_count=plane.error_count,
                warning_count=plane.warning_count,
            )
            for plane in payload.planes
        ],
        findings=[_count_metric_type(metric) for metric in payload.findings],
        finding_total=payload.finding_total,
    )


def lane_path_type(payload: LanePathPayload) -> LanePathType:
    return LanePathType(
        source=_object_reference_type(payload.source),
        destination=_object_reference_type(payload.destination) if payload.destination is not None else None,
        source_lane_index=payload.source_lane_index,
        destination_lane_index=payload.destination_lane_index,
        path_found=payload.path_found,
        plane_id=payload.plane_id,
        max_depth=payload.max_depth,
        steps=[
            LanePathStepType(
                step_kind=step.step_kind,
                display=step.display,
                edge_type=step.edge_type,
                object=_object_reference_type(step.object) if step.object is not None else None,
                signal_lane=_object_reference_type(step.signal_lane) if step.signal_lane is not None else None,
                attachment_unit=_object_reference_type(step.attachment_unit) if step.attachment_unit is not None else None,
                termination_point=_object_reference_type(step.termination_point) if step.termination_point is not None else None,
                plant_node=_object_reference_type(step.plant_node) if step.plant_node is not None else None,
                parent_coarse_edge=_object_reference_type(step.parent_coarse_edge) if step.parent_coarse_edge is not None else None,
                owner_node=_object_reference_type(step.owner_node) if step.owner_node is not None else None,
                owner_edge=_object_reference_type(step.owner_edge) if step.owner_edge is not None else None,
            )
            for step in payload.steps
        ],
        summary=LanePathSummaryType(
            coarse_edges_crossed=payload.summary.coarse_edges_crossed,
            transfer_maps_crossed=payload.summary.transfer_maps_crossed,
            lane_maps_crossed=payload.summary.lane_maps_crossed,
            shuffle_modules_crossed=payload.summary.shuffle_modules_crossed,
            planes_touched=list(payload.summary.planes_touched),
        ),
    )


def _lane_set_attachment_type(payload: LaneSetAttachmentPayload) -> LaneSetAttachmentType:
    return LaneSetAttachmentType(
        attachment_unit=_object_reference_type(payload.attachment_unit),
        termination_point=_object_reference_type(payload.termination_point),
        plant_node=_object_reference_type(payload.plant_node),
        expected_lane_count=payload.expected_lane_count,
        present_lane_count=payload.present_lane_count,
        mapped_lane_count=payload.mapped_lane_count,
        lane_indexes=list(payload.lane_indexes),
        missing_lane_indexes=list(payload.missing_lane_indexes),
        plane_ids=list(payload.plane_ids),
        topology_role=payload.topology_role,
        position=payload.position,
        status=payload.status,
    )


def lane_set_type(payload: LaneSetPayload) -> LaneSetType:
    return LaneSetType(
        scope_kind=payload.scope_kind,
        target=_object_reference_type(payload.target),
        fabric=_object_reference_type(payload.fabric) if payload.fabric is not None else None,
        plane=_object_reference_type(payload.plane) if payload.plane is not None else None,
        attachment_units=[_lane_set_attachment_type(member) for member in payload.attachment_units],
        total_attachment_units=payload.total_attachment_units,
        expected_lane_total=payload.expected_lane_total,
        present_lane_total=payload.present_lane_total,
        mapped_lane_total=payload.mapped_lane_total,
        missing_lane_total=payload.missing_lane_total,
        unmatched_peer_positions=list(payload.unmatched_peer_positions),
        plane_ids=list(payload.plane_ids),
        lane_map_consistency=payload.lane_map_consistency,
        plane_consistency=payload.plane_consistency,
    )


def lane_allocation_summary_type(payload: LaneAllocationSummaryPayload) -> LaneAllocationSummaryType:
    return LaneAllocationSummaryType(
        scope_kind=payload.scope_kind,
        target=_object_reference_type(payload.target),
        expected_lane_total=payload.expected_lane_total,
        present_lane_total=payload.present_lane_total,
        mapped_lane_total=payload.mapped_lane_total,
        missing_lane_total=payload.missing_lane_total,
        unmatched_peer_positions=list(payload.unmatched_peer_positions),
        incomplete_attachment_units=payload.incomplete_attachment_units,
        lane_map_consistency=payload.lane_map_consistency,
        plane_consistency=payload.plane_consistency,
    )


def _comparison_metric_type(payload: ComparisonMetricPayload) -> ComparisonMetricType:
    return ComparisonMetricType(
        name=payload.name,
        baseline_value=payload.baseline_value,
        candidate_value=payload.candidate_value,
        delta=payload.delta,
        status=payload.status,
    )


def _policy_evidence_artifact_share_type(payload: PolicyEvidenceArtifactSharePayload) -> PolicyEvidenceArtifactShareType:
    return PolicyEvidenceArtifactShareType(
        rule_id=payload.rule_id,
        artifact=_object_reference_type(payload.artifact),
        artifact_kind=payload.artifact_kind,
        plane_ids=list(payload.plane_ids),
        plane_pair_ids=[list(item) for item in payload.plane_pair_ids],
        attachment_unit_count=payload.attachment_unit_count,
        signal_lane_count=payload.signal_lane_count,
        attachment_units=[_object_reference_type(item) for item in payload.attachment_units],
        representative_targets=[_object_reference_type(item) for item in payload.representative_targets],
    )


def _policy_evidence_edge_bridge_type(payload: PolicyEvidenceEdgeBridgePayload) -> PolicyEvidenceEdgeBridgeType:
    return PolicyEvidenceEdgeBridgeType(
        rule_id=payload.rule_id,
        edge=_object_reference_type(payload.edge),
        edge_kind=payload.edge_kind,
        left_plane_ids=list(payload.left_plane_ids),
        right_plane_ids=list(payload.right_plane_ids),
        plane_pair_ids=[list(item) for item in payload.plane_pair_ids],
        attachment_units=[_object_reference_type(item) for item in payload.attachment_units],
        parent_artifacts=[_object_reference_type(item) for item in payload.parent_artifacts],
        representative_targets=[_object_reference_type(item) for item in payload.representative_targets],
    )


def contamination_domain_type(payload: ContaminationDomainPayload) -> ContaminationDomainType:
    return ContaminationDomainType(
        domain_key=payload.domain_key,
        fabric=_object_reference_type(payload.fabric),
        plane_ids=list(payload.plane_ids),
        plane_pair_ids=[list(item) for item in payload.plane_pair_ids],
        artifacts=[_object_reference_type(item) for item in payload.artifacts],
        attachment_units=[_object_reference_type(item) for item in payload.attachment_units],
        signal_lane_count=payload.signal_lane_count,
        artifact_shares=[_policy_evidence_artifact_share_type(item) for item in payload.artifact_shares],
        edge_bridges=[_policy_evidence_edge_bridge_type(item) for item in payload.edge_bridges],
        representative_targets=[_object_reference_type(item) for item in payload.representative_targets],
        evidence_keys=list(payload.evidence_keys),
    )


def policy_summary_type(payload: PolicySummaryPayload) -> PolicySummaryType:
    return PolicySummaryType(
        fabric=_object_reference_type(payload.fabric),
        policy_mode=payload.policy_mode,
        plane=_object_reference_type(payload.plane) if payload.plane is not None else None,
        artifact_share_count=payload.artifact_share_count,
        edge_bridge_count=payload.edge_bridge_count,
        contamination_domain_count=payload.contamination_domain_count,
        plane_pair_count=payload.plane_pair_count,
        largest_domain_attachment_units=payload.largest_domain_attachment_units,
        largest_domain_signal_lanes=payload.largest_domain_signal_lanes,
        rule_counts=[_count_metric_type(item) for item in payload.rule_counts],
    )


def policy_dashboard_type(payload: PolicyDashboardPayload) -> PolicyDashboardType:
    return PolicyDashboardType(
        fabric=_object_reference_type(payload.fabric),
        policy_mode=payload.policy_mode,
        active_exception_count=payload.active_exception_count,
        covered_exception_count=payload.covered_exception_count,
        drifted_exception_count=payload.drifted_exception_count,
        contamination_domain_count=payload.contamination_domain_count,
        uncovered_domain_count=payload.uncovered_domain_count,
        uncovered_edge_bridge_count=payload.uncovered_edge_bridge_count,
        uncovered_artifact_share_count=payload.uncovered_artifact_share_count,
        plane_pairs=[
            PolicyPlanePairReportType(
                plane_pair_ids=list(item.plane_pair_ids),
                domain_count=item.domain_count,
                uncovered_domain_count=item.uncovered_domain_count,
                edge_bridge_count=item.edge_bridge_count,
                uncovered_edge_bridge_count=item.uncovered_edge_bridge_count,
                artifact_share_count=item.artifact_share_count,
                uncovered_artifact_share_count=item.uncovered_artifact_share_count,
                active_exception_count=item.active_exception_count,
                covered_exception_count=item.covered_exception_count,
            )
            for item in payload.plane_pairs
        ],
        top_domains=[
            PolicyDomainReportType(
                domain_key=item.domain_key,
                plane_ids=list(item.plane_ids),
                plane_pair_ids=[list(pair) for pair in item.plane_pair_ids],
                artifact_count=item.artifact_count,
                attachment_unit_count=item.attachment_unit_count,
                signal_lane_count=item.signal_lane_count,
                matched_exception_count=item.matched_exception_count,
                coverage_status=item.coverage_status,
                highest_risk_severity=item.highest_risk_severity,
                artifacts=[_object_reference_type(reference) for reference in item.artifacts],
                representative_targets=[_object_reference_type(reference) for reference in item.representative_targets],
            )
            for item in payload.top_domains
        ],
        oldest_active_findings=[
            PolicyActiveFindingType(
                finding=_object_reference_type(item.finding),
                affected_object=_object_reference_type(item.affected_object) if item.affected_object is not None else None,
                plane=_object_reference_type(item.plane) if item.plane is not None else None,
                finding_type=item.finding_type,
                severity=item.severity,
                status=item.status,
                age_days=item.age_days,
                first_seen_at=item.first_seen_at,
            )
            for item in payload.oldest_active_findings
        ],
    )


def lane_compare_type(payload: LaneComparePayload) -> LaneCompareType:
    return LaneCompareType(
        baseline_target=_object_reference_type(payload.baseline_target),
        candidate_target=_object_reference_type(payload.candidate_target),
        baseline_summary=lane_allocation_summary_type(payload.baseline_summary),
        candidate_summary=lane_allocation_summary_type(payload.candidate_summary),
        metrics=[_comparison_metric_type(metric) for metric in payload.metrics],
        attachment_diffs=[
            LaneComparisonAttachmentType(
                compare_key=item.compare_key,
                baseline_attachment=_object_reference_type(item.baseline_attachment) if item.baseline_attachment is not None else None,
                candidate_attachment=_object_reference_type(item.candidate_attachment) if item.candidate_attachment is not None else None,
                baseline_status=item.baseline_status,
                candidate_status=item.candidate_status,
                baseline_plane_ids=list(item.baseline_plane_ids),
                candidate_plane_ids=list(item.candidate_plane_ids),
                baseline_present_lane_count=item.baseline_present_lane_count,
                candidate_present_lane_count=item.candidate_present_lane_count,
                baseline_mapped_lane_count=item.baseline_mapped_lane_count,
                candidate_mapped_lane_count=item.candidate_mapped_lane_count,
                changed_fields=list(item.changed_fields),
            )
            for item in payload.attachment_diffs
        ],
        node_diffs=[
            LaneComparisonNodeType(
                node_label=item.node_label,
                baseline_reference=_object_reference_type(item.baseline_reference) if item.baseline_reference is not None else None,
                candidate_reference=_object_reference_type(item.candidate_reference) if item.candidate_reference is not None else None,
                baseline_attachment_units=item.baseline_attachment_units,
                candidate_attachment_units=item.candidate_attachment_units,
                baseline_present_lane_total=item.baseline_present_lane_total,
                candidate_present_lane_total=item.candidate_present_lane_total,
                baseline_mapped_lane_total=item.baseline_mapped_lane_total,
                candidate_mapped_lane_total=item.candidate_mapped_lane_total,
                changed=item.changed,
            )
            for item in payload.node_diffs
        ],
        representative_reviews=[
            LaneComparisonReviewType(
                compare_key=item.compare_key,
                lane_index=item.lane_index,
                baseline_attachment=_object_reference_type(item.baseline_attachment) if item.baseline_attachment is not None else None,
                candidate_attachment=_object_reference_type(item.candidate_attachment) if item.candidate_attachment is not None else None,
                baseline_action=_action_link_type(item.baseline_action) if item.baseline_action is not None else None,
                candidate_action=_action_link_type(item.candidate_action) if item.candidate_action is not None else None,
                reason=item.reason,
            )
            for item in payload.representative_reviews
        ],
        plane_ids_added=list(payload.plane_ids_added),
        plane_ids_removed=list(payload.plane_ids_removed),
        regressions=list(payload.regressions),
        policy_domain_deltas=[
            PolicyComparisonDomainDeltaType(
                compare_key=item.compare_key,
                status=item.status,
                plane_ids=list(item.plane_ids),
                artifact_count=item.artifact_count,
                attachment_unit_count=item.attachment_unit_count,
                signal_lane_count=item.signal_lane_count,
                artifacts=[_object_reference_type(reference) for reference in item.artifacts],
                representative_targets=[_object_reference_type(reference) for reference in item.representative_targets],
            )
            for item in payload.policy_domain_deltas
        ],
        policy_regressions=list(payload.policy_regressions),
    )


def audit_workflow_summary_type(payload: AuditWorkflowSummaryPayload) -> AuditWorkflowSummaryType:
    return AuditWorkflowSummaryType(
        fabric=_object_reference_type(payload.fabric) if payload.fabric is not None else None,
        total_findings=payload.total_findings,
        active_findings=payload.active_findings,
        resolved_findings=payload.resolved_findings,
        suppressed_findings=payload.suppressed_findings,
        stale_findings_7d=payload.stale_findings_7d,
        stale_findings_30d=payload.stale_findings_30d,
        status_counts=[_count_metric_type(item) for item in payload.status_counts],
        severity_counts=[_count_metric_type(item) for item in payload.severity_counts],
        type_counts=[_count_metric_type(item) for item in payload.type_counts],
        churn_windows=[
            AuditWorkflowChurnWindowType(
                label=item.label,
                days=item.days,
                opened_count=item.opened_count,
                reopened_count=item.reopened_count,
                resolved_count=item.resolved_count,
                auto_resolved_count=item.auto_resolved_count,
                suppressed_count=item.suppressed_count,
            )
            for item in payload.churn_windows
        ],
        recent_runs=[
            AuditWorkflowRunType(
                run=_object_reference_type(item.run),
                scope_label=item.scope_label,
                status=item.status,
                trigger_mode=item.trigger_mode,
                started_at=item.started_at,
                completed_at=item.completed_at,
                finding_count=item.finding_count,
                new_count=item.new_count,
                reopened_count=item.reopened_count,
                resolved_count=item.resolved_count,
            )
            for item in payload.recent_runs
        ],
        recent_events=[
            AuditWorkflowEventType(
                finding=_object_reference_type(item.finding),
                event_type=item.event_type,
                actor_display=item.actor_display,
                created_at=item.created_at,
                message=item.message,
                run=_object_reference_type(item.run) if item.run is not None else None,
                old_status=item.old_status,
                new_status=item.new_status,
            )
            for item in payload.recent_events
        ],
        oldest_active_findings=[
            AuditWorkflowFindingType(
                finding=_object_reference_type(item.finding),
                affected_object=_object_reference_type(item.affected_object) if item.affected_object is not None else None,
                status=item.status,
                severity=item.severity,
                age_days=item.age_days,
                first_seen_at=item.first_seen_at,
                last_seen_at=item.last_seen_at,
            )
            for item in payload.oldest_active_findings
        ],
        expiring_suppressions=[
            AuditWorkflowSuppressionType(
                suppression=_object_reference_type(item.suppression),
                finding=_object_reference_type(item.finding),
                expires_at=item.expires_at,
                remaining_days=item.remaining_days,
                reason=item.reason,
            )
            for item in payload.expiring_suppressions
        ],
    )


def audit_workflow_finding_record_type(payload: AuditWorkflowFindingRecordPayload) -> AuditWorkflowFindingRecordType:
    return AuditWorkflowFindingRecordType(
        finding=_object_reference_type(payload.finding),
        affected_object=_object_reference_type(payload.affected_object) if payload.affected_object is not None else None,
        fabric=_object_reference_type(payload.fabric) if payload.fabric is not None else None,
        plane=_object_reference_type(payload.plane) if payload.plane is not None else None,
        status=payload.status,
        severity=payload.severity,
        active=payload.active,
        finding_type=payload.finding_type,
        message=payload.message,
        age_days=payload.age_days,
        first_seen_at=payload.first_seen_at,
        last_seen_at=payload.last_seen_at,
        resolved_at=payload.resolved_at,
        assigned_to_display=payload.assigned_to_display,
        acknowledged_by_display=payload.acknowledged_by_display,
        active_suppression=_audit_workflow_suppression_type(payload.active_suppression) if payload.active_suppression is not None else None,
    )


def audit_workflow_finding_detail_type(payload: AuditWorkflowFindingDetailPayload) -> AuditWorkflowFindingDetailType:
    return AuditWorkflowFindingDetailType(
        finding=audit_workflow_finding_record_type(payload.finding),
        recent_events=[
            AuditWorkflowEventType(
                finding=_object_reference_type(item.finding),
                event_type=item.event_type,
                actor_display=item.actor_display,
                created_at=item.created_at,
                message=item.message,
                run=_object_reference_type(item.run) if item.run is not None else None,
                old_status=item.old_status,
                new_status=item.new_status,
            )
            for item in payload.recent_events
        ],
    )


def _audit_workflow_suppression_type(payload: AuditWorkflowSuppressionPayload) -> AuditWorkflowSuppressionType:
    return AuditWorkflowSuppressionType(
        suppression=_object_reference_type(payload.suppression),
        finding=_object_reference_type(payload.finding),
        expires_at=payload.expires_at,
        remaining_days=payload.remaining_days,
        reason=payload.reason,
    )


def audit_workflow_run_type(payload: AuditWorkflowRunPayload) -> AuditWorkflowRunType:
    return AuditWorkflowRunType(
        run=_object_reference_type(payload.run),
        scope_label=payload.scope_label,
        status=payload.status,
        trigger_mode=payload.trigger_mode,
        started_at=payload.started_at,
        completed_at=payload.completed_at,
        finding_count=payload.finding_count,
        new_count=payload.new_count,
        reopened_count=payload.reopened_count,
        resolved_count=payload.resolved_count,
    )
