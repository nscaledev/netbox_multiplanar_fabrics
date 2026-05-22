from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from django.db.models import Prefetch, Q
from django.utils import timezone

from netbox_plant_graph.models import (
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FiberSegment,
    FiberStrand,
    OperationRun,
    OpticalLane,
    StampRun,
    StrandTermination,
    TransferMap,
    TransportChannel,
    TransportChannelPositionMap,
)
from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_SLUG,
    ARCHITECTURE_VERSION,
    CHANNEL_MAP_MATRIX,
    MPO_POSITION_COUNT,
)


SEVERITIES = ('critical', 'error', 'warning', 'info')
SEVERITY_RANK = {
    'critical': 4,
    'error': 3,
    'warning': 2,
    'info': 1,
}

MPO_CONNECTOR_KINDS = frozenset({'mpo-8', 'mpo-12', 'mpo-16', 'mpo-24'})

TOPOLOGY_INTEGRITY_CATALOG_VERSION = 'v2.topology_integrity.catalog/1'
TOPOLOGY_INTEGRITY_REPORT_SCHEMA = 'v2.topology_integrity.report/1'
TOPOLOGY_INTEGRITY_GATE_SCHEMA = 'v2.topology_integrity.gate/1'
TOPOLOGY_INTEGRITY_OPERATION_PROFILE = 'topology_integrity'
TOPOLOGY_INTEGRITY_OPERATION_KIND = 'topology_integrity_audit'

OBJECT_FAMILY_LABELS = {
    'architecture': 'Architecture',
    'cable_plant': 'Cable Plant',
    'endpoints': 'Endpoints',
    'channels': 'Channels',
    'lanes': 'Optical Lanes',
    'transfer_maps': 'Transfer Maps',
    'unknown': 'Unknown',
}
OBJECT_FAMILY_ORDER = tuple(OBJECT_FAMILY_LABELS)
WORKFLOW_FLAG_KEYS = (
    'path_tracing',
    'blast_radius',
    'channel_coverage',
    'import_reconciliation',
    'stamp_preflight',
    'operator_display',
)


@dataclass(frozen=True)
class FindingCatalogEntry:
    code: str
    severity: str
    object_family: str
    likely_cause: str
    remediation: str
    affected_workflows: tuple[str, ...] = ()
    path_blocking: bool = False

    @property
    def workflow_flags(self) -> dict[str, bool]:
        affected = set(self.affected_workflows)
        return {key: key in affected for key in WORKFLOW_FLAG_KEYS}

    def as_dict(self) -> dict[str, Any]:
        return {
            'code': self.code,
            'severity': self.severity,
            'object_family': self.object_family,
            'object_family_label': OBJECT_FAMILY_LABELS.get(self.object_family, self.object_family.title()),
            'likely_cause': self.likely_cause,
            'remediation': self.remediation,
            'affected_workflows': list(self.affected_workflows),
            'workflow_flags': self.workflow_flags,
            'path_blocking': self.path_blocking,
        }


def _catalog_entry(
    code: str,
    *,
    severity: str,
    object_family: str,
    likely_cause: str,
    remediation: str,
    affected_workflows: tuple[str, ...],
    path_blocking: bool,
) -> FindingCatalogEntry:
    return FindingCatalogEntry(
        code=code,
        severity=severity,
        object_family=object_family,
        likely_cause=likely_cause,
        remediation=remediation,
        affected_workflows=affected_workflows,
        path_blocking=path_blocking,
    )


CABLE_PATH_WORKFLOWS = ('path_tracing', 'blast_radius', 'import_reconciliation', 'operator_display')
CHANNEL_WORKFLOWS = ('path_tracing', 'blast_radius', 'channel_coverage', 'import_reconciliation')
LANE_WORKFLOWS = ('path_tracing', 'blast_radius', 'channel_coverage', 'operator_display')
TRANSFER_WORKFLOWS = ('path_tracing', 'blast_radius', 'import_reconciliation', 'operator_display')

TOPOLOGY_INTEGRITY_FINDING_CATALOG = {
    'dark_mpo_position_usage': _catalog_entry(
        'dark_mpo_position_usage',
        severity='error',
        object_family='architecture',
        likely_cause='Modeled topology references an MPO position marked dark by the architecture position policy.',
        remediation=(
            'Move the modeled lane, strand termination, channel map, or transfer map to an active MPO position, '
            'or update the architecture channel map if the position is intentionally active.'
        ),
        affected_workflows=(
            'path_tracing',
            'blast_radius',
            'channel_coverage',
            'import_reconciliation',
            'stamp_preflight',
        ),
        path_blocking=True,
    ),
    'fiber_segment_endpoint_invalid': _catalog_entry(
        'fiber_segment_endpoint_invalid',
        severity='error',
        object_family='cable_plant',
        likely_cause='A fiber segment connects one endpoint back to itself.',
        remediation='Point a_endpoint and b_endpoint at two distinct MPO endpoints before using the segment.',
        affected_workflows=CABLE_PATH_WORKFLOWS,
        path_blocking=True,
    ),
    'fiber_segment_endpoint_fabric_mismatch': _catalog_entry(
        'fiber_segment_endpoint_fabric_mismatch',
        severity='error',
        object_family='cable_plant',
        likely_cause='A fiber segment references endpoints outside its fabric.',
        remediation='Move the segment to the endpoint fabric or replace the mismatched endpoint reference.',
        affected_workflows=CABLE_PATH_WORKFLOWS,
        path_blocking=True,
    ),
    'fiber_strand_termination_count': _catalog_entry(
        'fiber_strand_termination_count',
        severity='error',
        object_family='cable_plant',
        likely_cause='A fiber strand does not have exactly one termination at each end.',
        remediation='Add or remove StrandTermination rows so the strand has one termination at each segment end.',
        affected_workflows=CABLE_PATH_WORKFLOWS,
        path_blocking=True,
    ),
    'strand_termination_position_mismatch': _catalog_entry(
        'strand_termination_position_mismatch',
        severity='error',
        object_family='cable_plant',
        likely_cause='A strand termination position is owned by a different MPO endpoint than the termination says.',
        remediation=(
            'Set mpo_endpoint to the connector owning mpo_position, or select a position on the declared endpoint.'
        ),
        affected_workflows=CABLE_PATH_WORKFLOWS,
        path_blocking=True,
    ),
    'strand_termination_fabric_mismatch': _catalog_entry(
        'strand_termination_fabric_mismatch',
        severity='error',
        object_family='cable_plant',
        likely_cause='A strand termination points at an endpoint outside the strand fabric.',
        remediation='Move the termination to an endpoint in the same fabric as the strand segment.',
        affected_workflows=CABLE_PATH_WORKFLOWS,
        path_blocking=True,
    ),
    'fiber_strand_segment_endpoint_mismatch': _catalog_entry(
        'fiber_strand_segment_endpoint_mismatch',
        severity='error',
        object_family='cable_plant',
        likely_cause='The two strand terminations do not match the parent segment endpoint pair.',
        remediation=(
            'Terminate the strand on the segment a_endpoint and b_endpoint, or correct the FiberSegment endpoints.'
        ),
        affected_workflows=CABLE_PATH_WORKFLOWS,
        path_blocking=True,
    ),
    'strand_termination_index_incoherent': _catalog_entry(
        'strand_termination_index_incoherent',
        severity='warning',
        object_family='cable_plant',
        likely_cause='Termination indexes are duplicated or outside the expected 1/2 pair.',
        remediation='Normalize termination_index values to one 1 and one 2 for deterministic operator displays.',
        affected_workflows=('operator_display',),
        path_blocking=False,
    ),
    'cable_reference_incomplete': _catalog_entry(
        'cable_reference_incomplete',
        severity='error',
        object_family='cable_plant',
        likely_cause='A fiber strand has only one half of its cable assembly natural-key reference.',
        remediation='Set both cable_site and cable_id, or clear both until the strand is assigned to a cable assembly.',
        affected_workflows=('blast_radius', 'import_reconciliation', 'operator_display'),
        path_blocking=False,
    ),
    'cable_reference_missing': _catalog_entry(
        'cable_reference_missing',
        severity='error',
        object_family='cable_plant',
        likely_cause='A fiber strand references a cable assembly that has not been modeled.',
        remediation='Create the CableAssembly row, or correct the strand cable_site/cable_id values.',
        affected_workflows=('blast_radius', 'import_reconciliation', 'operator_display'),
        path_blocking=False,
    ),
    'cable_parent_self_reference': _catalog_entry(
        'cable_parent_self_reference',
        severity='error',
        object_family='cable_plant',
        likely_cause='A cable assembly is set as its own parent cable.',
        remediation='Clear parent_cable or point it at a distinct parent cable assembly.',
        affected_workflows=('blast_radius', 'operator_display'),
        path_blocking=False,
    ),
    'cable_parent_site_mismatch': _catalog_entry(
        'cable_parent_site_mismatch',
        severity='error',
        object_family='cable_plant',
        likely_cause='A cable assembly parent belongs to a different site than the child assembly.',
        remediation='Use a parent cable assembly from the same site, or split the child assembly reference.',
        affected_workflows=('blast_radius', 'operator_display'),
        path_blocking=False,
    ),
    'channel_position_map_fabric_mismatch': _catalog_entry(
        'channel_position_map_fabric_mismatch',
        severity='error',
        object_family='channels',
        likely_cause='A channel position map points at an MPO endpoint outside the channel fabric.',
        remediation='Move the map to an MPO endpoint in the channel fabric.',
        affected_workflows=CHANNEL_WORKFLOWS,
        path_blocking=True,
    ),
    'channel_position_map_position_mismatch': _catalog_entry(
        'channel_position_map_position_mismatch',
        severity='error',
        object_family='channels',
        likely_cause='A channel position map position is not owned by its declared MPO endpoint.',
        remediation=(
            'Set mpo_endpoint to the connector owning mpo_position, or select a position on the declared endpoint.'
        ),
        affected_workflows=CHANNEL_WORKFLOWS,
        path_blocking=True,
    ),
    'channel_position_map_parent_mismatch': _catalog_entry(
        'channel_position_map_parent_mismatch',
        severity='error',
        object_family='channels',
        likely_cause='A mapped MPO endpoint is not a child of the channel endpoint.',
        remediation='Map the channel to an MPO child of the channel endpoint.',
        affected_workflows=CHANNEL_WORKFLOWS,
        path_blocking=True,
    ),
    'channel_position_map_not_disjoint': _catalog_entry(
        'channel_position_map_not_disjoint',
        severity='error',
        object_family='channels',
        likely_cause='One MPO position is assigned to more than one channel on the same endpoint.',
        remediation='Keep each local MPO position in exactly one TransportChannelPositionMap for an OSFP endpoint.',
        affected_workflows=CHANNEL_WORKFLOWS,
        path_blocking=True,
    ),
    'channel_position_map_missing_mpo_endpoint': _catalog_entry(
        'channel_position_map_missing_mpo_endpoint',
        severity='error',
        object_family='endpoints',
        likely_cause='The architecture matrix expects an MPO child endpoint that is not modeled.',
        remediation='Create the expected MPO child endpoint or correct the channel map matrix MPO index.',
        affected_workflows=CHANNEL_WORKFLOWS + ('stamp_preflight',),
        path_blocking=True,
    ),
    'channel_position_map_incomplete': _catalog_entry(
        'channel_position_map_incomplete',
        severity='error',
        object_family='channels',
        likely_cause='A channel is missing expected active MPO position maps from the architecture matrix.',
        remediation='Create TransportChannelPositionMap rows for every expected active position in the channel.',
        affected_workflows=CHANNEL_WORKFLOWS,
        path_blocking=True,
    ),
    'channel_position_map_extra': _catalog_entry(
        'channel_position_map_extra',
        severity='error',
        object_family='channels',
        likely_cause='A channel has position maps outside the architecture matrix.',
        remediation=(
            'Remove extra TransportChannelPositionMap rows or update the architecture matrix if they are intentional.'
        ),
        affected_workflows=CHANNEL_WORKFLOWS,
        path_blocking=True,
    ),
    'transfer_map_owner_invalid': _catalog_entry(
        'transfer_map_owner_invalid',
        severity='error',
        object_family='transfer_maps',
        likely_cause='A transfer map has zero owners or both node and segment owners.',
        remediation='Set either owner_node or owner_segment, but not both.',
        affected_workflows=TRANSFER_WORKFLOWS,
        path_blocking=True,
    ),
    'transfer_map_owner_fabric_mismatch': _catalog_entry(
        'transfer_map_owner_fabric_mismatch',
        severity='error',
        object_family='transfer_maps',
        likely_cause='A transfer map owner belongs to a different fabric than the transfer map.',
        remediation='Move the transfer map to the owner fabric or select an owner in this fabric.',
        affected_workflows=TRANSFER_WORKFLOWS,
        path_blocking=True,
    ),
    'transfer_map_same_position': _catalog_entry(
        'transfer_map_same_position',
        severity='error',
        object_family='transfer_maps',
        likely_cause='A transfer map source and destination are the same connector position.',
        remediation='Select two distinct connector positions.',
        affected_workflows=TRANSFER_WORKFLOWS,
        path_blocking=True,
    ),
    'transfer_map_position_fabric_mismatch': _catalog_entry(
        'transfer_map_position_fabric_mismatch',
        severity='error',
        object_family='transfer_maps',
        likely_cause='A transfer map source or destination position belongs to another fabric.',
        remediation='Replace the connector position with one in the transfer map fabric.',
        affected_workflows=TRANSFER_WORKFLOWS,
        path_blocking=True,
    ),
    'transfer_map_position_owner_mismatch': _catalog_entry(
        'transfer_map_position_owner_mismatch',
        severity='error',
        object_family='transfer_maps',
        likely_cause='A transfer map references positions outside its node or segment owner.',
        remediation='Use connector positions on endpoints owned by the transfer map owner.',
        affected_workflows=TRANSFER_WORKFLOWS,
        path_blocking=True,
    ),
    'transfer_map_pattern_architecture_mismatch': _catalog_entry(
        'transfer_map_pattern_architecture_mismatch',
        severity='warning',
        object_family='transfer_maps',
        likely_cause='A transfer map references a pattern from another architecture.',
        remediation='Select a TransferPattern from the fabric architecture or clear the pattern for custom maps.',
        affected_workflows=('stamp_preflight', 'operator_display'),
        path_blocking=False,
    ),
    'transfer_map_pattern_kind_mismatch': _catalog_entry(
        'transfer_map_pattern_kind_mismatch',
        severity='warning',
        object_family='transfer_maps',
        likely_cause='A transfer map kind differs from its referenced transfer pattern kind.',
        remediation='Align map_kind with the TransferPattern, or select a matching pattern.',
        affected_workflows=('stamp_preflight', 'operator_display'),
        path_blocking=False,
    ),
    'optical_lane_endpoint_fabric_mismatch': _catalog_entry(
        'optical_lane_endpoint_fabric_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane endpoint belongs to another fabric.',
        remediation='Move the lane to the endpoint fabric or select an endpoint in the lane fabric.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_anchor_fabric_mismatch': _catalog_entry(
        'optical_lane_anchor_fabric_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane local MPO endpoint belongs to another fabric.',
        remediation='Select a local MPO endpoint in the lane fabric.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_anchor_position_mismatch': _catalog_entry(
        'optical_lane_anchor_position_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane local MPO position is not owned by the declared local MPO endpoint.',
        remediation=(
            'Set local_mpo_endpoint to the connector owning local_mpo_position, or select a position on the '
            'declared endpoint.'
        ),
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_anchor_parent_mismatch': _catalog_entry(
        'optical_lane_anchor_parent_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane local MPO endpoint is not a child of the lane endpoint.',
        remediation='Anchor the lane on an MPO child of the lane endpoint.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_plane_fabric_mismatch': _catalog_entry(
        'optical_lane_plane_fabric_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane references a plane from another fabric.',
        remediation='Select a plane in the lane fabric, or clear plane if the lane is intentionally unscoped.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_local_mpo_index_mismatch': _catalog_entry(
        'optical_lane_local_mpo_index_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane local_mpo_index disagrees with the local MPO endpoint metadata/name.',
        remediation='Correct local_mpo_index or the local MPO endpoint metadata.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_missing_channel': _catalog_entry(
        'optical_lane_missing_channel',
        severity='warning',
        object_family='lanes',
        likely_cause='An optical lane is not linked to a transport channel.',
        remediation='Assign a TransportChannel when the lane is intended to participate in channel-scoped path audits.',
        affected_workflows=('channel_coverage', 'operator_display'),
        path_blocking=False,
    ),
    'optical_lane_channel_fabric_mismatch': _catalog_entry(
        'optical_lane_channel_fabric_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane references a channel from another fabric.',
        remediation='Select a TransportChannel in the lane fabric.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_channel_endpoint_mismatch': _catalog_entry(
        'optical_lane_channel_endpoint_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane references a channel on a different endpoint.',
        remediation='Select a TransportChannel whose endpoint matches the lane endpoint.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_channel_plane_mismatch': _catalog_entry(
        'optical_lane_channel_plane_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane plane differs from its transport channel plane.',
        remediation='Align the lane plane with its channel plane, or clear one side if intentionally unscoped.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_channel_position_missing': _catalog_entry(
        'optical_lane_channel_position_missing',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane local MPO position is not included in its channel position map.',
        remediation=(
            'Add the missing TransportChannelPositionMap row or move the lane to a position mapped by its channel.'
        ),
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
    'optical_lane_channel_index_mismatch': _catalog_entry(
        'optical_lane_channel_index_mismatch',
        severity='error',
        object_family='lanes',
        likely_cause='An optical lane position belongs to another channel according to the architecture matrix.',
        remediation='Move the lane to the expected transport channel for its MPO index and position.',
        affected_workflows=LANE_WORKFLOWS,
        path_blocking=True,
    ),
}


@dataclass(frozen=True)
class IntegrityObjectRef:
    model: str
    object_id: int | None
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            'model': self.model,
            'id': self.object_id,
            'label': self.label,
        }


@dataclass(frozen=True)
class IntegrityFinding:
    severity: str
    code: str
    message: str
    object: IntegrityObjectRef
    fabric_id: int | None = None
    fabric_slug: str = ''
    remediation: str = ''
    related_objects: tuple[IntegrityObjectRef, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def catalog_entry(self) -> FindingCatalogEntry:
        return _catalog_entry_for_code(self.code)

    @property
    def object_family(self) -> str:
        return self.catalog_entry.object_family

    @property
    def affected_workflows(self) -> tuple[str, ...]:
        return self.catalog_entry.affected_workflows

    @property
    def workflow_flags(self) -> dict[str, bool]:
        return self.catalog_entry.workflow_flags

    @property
    def path_blocking(self) -> bool:
        return self.catalog_entry.path_blocking

    def as_dict(self) -> dict[str, Any]:
        catalog_entry = self.catalog_entry
        return {
            'severity': self.severity,
            'code': self.code,
            'message': self.message,
            'object': self.object.as_dict(),
            'fabric': {
                'id': self.fabric_id,
                'slug': self.fabric_slug,
            },
            'remediation': self.remediation,
            'related_objects': [related.as_dict() for related in self.related_objects],
            'details': self.details,
            'object_family': catalog_entry.object_family,
            'object_family_label': OBJECT_FAMILY_LABELS.get(
                catalog_entry.object_family,
                catalog_entry.object_family.title(),
            ),
            'affected_workflows': list(catalog_entry.affected_workflows),
            'workflow_flags': catalog_entry.workflow_flags,
            'path_blocking': catalog_entry.path_blocking,
            'catalog': catalog_entry.as_dict(),
        }


@dataclass(frozen=True)
class ChannelMapEntry:
    channel_index: int
    mpo_index: int
    positions: tuple[int, ...]


@dataclass(frozen=True)
class MPOPositionPolicy:
    position_count: int
    active_positions: frozenset[int]
    dark_positions: frozenset[int]
    source: str
    dark_positions_by_plane: Mapping[int, frozenset[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class TopologyIntegrityReport:
    scope: dict[str, Any]
    checked: dict[str, int]
    findings: tuple[IntegrityFinding, ...]

    @property
    def ok(self) -> bool:
        return not any(finding.severity in {'critical', 'error'} for finding in self.findings)

    @property
    def summary(self) -> dict[str, int]:
        return _severity_summary(self.findings)

    @property
    def grouped_summary(self) -> dict[str, Any]:
        return _grouped_findings_summary(self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            'scope': self.scope,
            'ok': self.ok,
            'summary': self.summary,
            'grouped_summary': self.grouped_summary,
            'checked': self.checked,
            'catalog_version': TOPOLOGY_INTEGRITY_CATALOG_VERSION,
            'finding_catalog': topology_integrity_finding_catalog(),
            'findings': [finding.as_dict() for finding in self.findings],
        }

    def has_findings_at_or_above(self, severity: str) -> bool:
        threshold = SEVERITY_RANK.get(severity, 0)
        if threshold <= 0:
            return False
        return any(SEVERITY_RANK.get(finding.severity, 0) >= threshold for finding in self.findings)


@dataclass(frozen=True)
class TopologyIntegrityGateResult:
    status: str
    ok: bool
    scope: dict[str, Any]
    summary: dict[str, int]
    grouped_summary: dict[str, Any]
    blocking_findings: tuple[dict[str, Any], ...] = ()
    blocking_count: int = 0
    fail_threshold: str = 'none'
    fail_threshold_count: int = 0
    non_blocking_warning_count: int = 0
    source: str = 'fresh_audit'
    operation_run_id: int | None = None

    @property
    def path_blocking_count(self) -> int:
        return self.blocking_count

    @property
    def blocking_summary(self) -> dict[str, int]:
        return _severity_summary_from_payloads(self.blocking_findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            'schema': TOPOLOGY_INTEGRITY_GATE_SCHEMA,
            'status': self.status,
            'ok': self.ok,
            'scope': self.scope,
            'summary': self.summary,
            'grouped_summary': self.grouped_summary,
            'blocking_count': self.blocking_count,
            'path_blocking_count': self.path_blocking_count,
            'blocking_summary': self.blocking_summary,
            'blocking_findings': list(self.blocking_findings),
            'fail_threshold': self.fail_threshold,
            'fail_threshold_count': self.fail_threshold_count,
            'non_blocking_warning_count': self.non_blocking_warning_count,
            'source': self.source,
            'operation_run_id': self.operation_run_id,
        }


def integrity_gate_for_fabric(
    fabric: Fabric | int | None = None,
    *,
    report: TopologyIntegrityReport | OperationRun | Mapping[str, Any] | None = None,
    operation_run: OperationRun | int | None = None,
    latest: bool = False,
    run_audit: bool = True,
    fail_on: str | None = 'none',
) -> TopologyIntegrityGateResult:
    """Return a pass/warn/fail gate result for topology-sensitive workflows."""
    target_fabric = _normalize_fabric(fabric)
    fail_threshold = _normalize_gate_fail_threshold(fail_on)
    report_payload: dict[str, Any] | None = None
    source = 'fresh_audit'
    operation_run_id = None
    missing_latest = False

    if report is not None:
        report_payload, operation_run_id = _report_payload_from_report_like(report)
        source = 'supplied_operation_run' if operation_run_id is not None else 'supplied_report'
    elif operation_run is not None:
        run = _normalize_operation_run(operation_run)
        report_payload, operation_run_id = _report_payload_from_report_like(run)
        source = 'operation_run'
    elif latest:
        run = _latest_topology_integrity_operation_run(target_fabric)
        if run is not None:
            report_payload, operation_run_id = _report_payload_from_report_like(run)
            source = 'latest_operation_run'
        else:
            missing_latest = True

    if report_payload is None:
        if run_audit:
            report_payload = audit_topology_integrity(fabric=target_fabric).as_dict()
            source = 'fresh_audit'
        else:
            report_payload = _empty_report_payload(target_fabric)
            source = 'no_report'

    return _build_gate_result(
        report_payload,
        fail_threshold=fail_threshold,
        source=source,
        operation_run_id=operation_run_id,
        missing_report=missing_latest and not run_audit,
    )


def _normalize_gate_fail_threshold(fail_on: str | None) -> str:
    if fail_on in (None, '', 'none'):
        return 'none'
    fail_threshold = str(fail_on).lower()
    if fail_threshold not in SEVERITY_RANK:
        raise ValueError(f'Unsupported topology integrity gate fail threshold: {fail_on!r}')
    return fail_threshold


def _report_payload_from_report_like(
    report_like: TopologyIntegrityReport | OperationRun | Mapping[str, Any],
) -> tuple[dict[str, Any], int | None]:
    operation_run_id = None
    raw_payload: Any = report_like

    if isinstance(report_like, TopologyIntegrityReport):
        return _normalized_report_payload(report_like.as_dict()), None

    if isinstance(report_like, OperationRun):
        operation_run_id = report_like.pk
        raw_payload = report_like.result or {}

    if not isinstance(raw_payload, Mapping):
        raise TypeError('Topology integrity gate report payload must be a report, OperationRun, or mapping.')

    if isinstance(raw_payload.get('result'), Mapping):
        raw_payload = raw_payload['result']

    payload = raw_payload
    if isinstance(raw_payload.get('report'), Mapping):
        payload = {
            'summary': raw_payload.get('summary'),
            'grouped_summary': raw_payload.get('grouped_summary'),
            'checked': raw_payload.get('checked'),
            'scope': raw_payload.get('scope'),
            **raw_payload['report'],
        }

    return _normalized_report_payload(payload), operation_run_id


def _normalized_report_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    findings = _finding_payloads(payload.get('findings') or ())
    summary = _summary_from_report_payload(payload, findings)
    grouped_summary = _grouped_summary_from_report_payload(payload, findings)
    scope = payload.get('scope') if isinstance(payload.get('scope'), Mapping) else {}

    return {
        **dict(payload),
        'scope': dict(scope),
        'summary': summary,
        'grouped_summary': grouped_summary,
        'findings': list(findings),
        'catalog_version': payload.get('catalog_version') or TOPOLOGY_INTEGRITY_CATALOG_VERSION,
    }


def _finding_payloads(findings) -> tuple[dict[str, Any], ...]:
    payloads = []
    for finding in findings:
        if isinstance(finding, IntegrityFinding):
            payloads.append(finding.as_dict())
        elif isinstance(finding, Mapping):
            payloads.append(dict(finding))
        elif hasattr(finding, 'as_dict'):
            payloads.append(finding.as_dict())
    return tuple(payloads)


def _severity_from_finding_payload(finding: Mapping[str, Any]) -> str:
    severity = finding.get('severity')
    if not severity and isinstance(finding.get('catalog'), Mapping):
        severity = finding['catalog'].get('severity')
    return str(severity or 'info').lower()


def _finding_catalog_payload(finding: Mapping[str, Any]) -> Mapping[str, Any]:
    catalog = finding.get('catalog')
    return catalog if isinstance(catalog, Mapping) else {}


def _finding_object_family(finding: Mapping[str, Any]) -> str:
    catalog = _finding_catalog_payload(finding)
    return str(finding.get('object_family') or catalog.get('object_family') or 'unknown')


def _finding_path_blocking(finding: Mapping[str, Any]) -> bool:
    if 'path_blocking' in finding:
        return _coerce_bool(finding.get('path_blocking'))
    catalog = _finding_catalog_payload(finding)
    return _coerce_bool(catalog.get('path_blocking'))


def _finding_affected_workflows(finding: Mapping[str, Any]) -> tuple[str, ...]:
    workflows = finding.get('affected_workflows')
    if workflows is None:
        workflows = _finding_catalog_payload(finding).get('affected_workflows')
    if not isinstance(workflows, (list, tuple, set)):
        return ()
    return tuple(str(workflow) for workflow in workflows if str(workflow) in WORKFLOW_FLAG_KEYS)


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {'1', 'true', 'yes', 'y'}
    return bool(value)


def _severity_summary_from_payloads(findings) -> dict[str, int]:
    payloads = _finding_payloads(findings)
    counts = Counter(_severity_from_finding_payload(finding) for finding in payloads)
    return {
        'total': len(payloads),
        'critical': counts.get('critical', 0),
        'error': counts.get('error', 0),
        'warning': counts.get('warning', 0),
        'info': counts.get('info', 0),
    }


def _summary_from_report_payload(payload: Mapping[str, Any], findings) -> dict[str, int]:
    fallback = _severity_summary_from_payloads(findings)
    raw_summary = payload.get('summary')
    if not isinstance(raw_summary, Mapping):
        return fallback
    summary = {}
    for key in ('total', 'critical', 'error', 'warning', 'info'):
        summary[key] = _safe_int(raw_summary.get(key)) if raw_summary.get(key) is not None else fallback[key]
        if summary[key] is None:
            summary[key] = fallback[key]
    return summary


def _workflow_counts_from_payloads(findings) -> dict[str, int]:
    counts = Counter()
    for finding in _finding_payloads(findings):
        counts.update(_finding_affected_workflows(finding))
    return {key: counts.get(key, 0) for key in WORKFLOW_FLAG_KEYS}


def _grouped_summary_from_report_payload(payload: Mapping[str, Any], findings) -> dict[str, Any]:
    raw_grouped_summary = payload.get('grouped_summary')
    if isinstance(raw_grouped_summary, Mapping):
        grouped_summary = dict(raw_grouped_summary)
        grouped_summary.setdefault('catalog_version', TOPOLOGY_INTEGRITY_CATALOG_VERSION)
        grouped_summary.setdefault('path_blocking_count', _path_blocking_count_from_payloads(findings))
        grouped_summary.setdefault('workflow_counts', _workflow_counts_from_payloads(findings))
        grouped_summary.setdefault(
            'workflow_flags',
            _workflow_flags_from_counts(grouped_summary['workflow_counts']),
        )
        grouped_summary.setdefault(
            'affected_workflows',
            [
                workflow
                for workflow in WORKFLOW_FLAG_KEYS
                if grouped_summary['workflow_counts'].get(workflow, 0)
            ],
        )
        grouped_summary.setdefault('groups', [])
        return grouped_summary

    groups: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(_finding_payloads(findings)):
        family = _finding_object_family(finding)
        group = groups.setdefault(
            family,
            {
                'object_family': family,
                'object_family_label': OBJECT_FAMILY_LABELS.get(family, family.title()),
                'finding_indexes': [],
                'findings': [],
                'code_counts': Counter(),
                'path_blocking_count': 0,
            },
        )
        group['finding_indexes'].append(index)
        group['findings'].append(finding)
        group['code_counts'][finding.get('code') or 'unknown'] += 1
        if _finding_path_blocking(finding):
            group['path_blocking_count'] += 1

    group_payloads = []
    for family, group in sorted(groups.items(), key=lambda item: _family_sort_key(item[0])):
        group_findings = tuple(group['findings'])
        workflow_counts = _workflow_counts_from_payloads(group_findings)
        group_payloads.append(
            {
                'object_family': family,
                'object_family_label': group['object_family_label'],
                'summary': _severity_summary_from_payloads(group_findings),
                'path_blocking_count': group['path_blocking_count'],
                'workflow_counts': workflow_counts,
                'workflow_flags': _workflow_flags_from_counts(workflow_counts),
                'affected_workflows': [
                    workflow
                    for workflow in WORKFLOW_FLAG_KEYS
                    if workflow_counts.get(workflow, 0)
                ],
                'finding_indexes': group['finding_indexes'],
                'codes': [
                    _code_summary_payload(code, count)
                    for code, count in sorted(
                        group['code_counts'].items(),
                        key=lambda item: (-SEVERITY_RANK.get(_catalog_entry_for_code(item[0]).severity, 0), item[0]),
                    )
                ],
            }
        )

    workflow_counts = _workflow_counts_from_payloads(findings)
    return {
        'catalog_version': TOPOLOGY_INTEGRITY_CATALOG_VERSION,
        'path_blocking_count': _path_blocking_count_from_payloads(findings),
        'workflow_counts': workflow_counts,
        'workflow_flags': _workflow_flags_from_counts(workflow_counts),
        'affected_workflows': [
            workflow
            for workflow in WORKFLOW_FLAG_KEYS
            if workflow_counts.get(workflow, 0)
        ],
        'groups': group_payloads,
    }


def _path_blocking_count_from_payloads(findings) -> int:
    return sum(1 for finding in _finding_payloads(findings) if _finding_path_blocking(finding))


def _empty_report_payload(fabric: Fabric | None) -> dict[str, Any]:
    return {
        'scope': _scope_payload(fabric),
        'ok': True,
        'summary': _severity_summary_from_payloads(()),
        'grouped_summary': _grouped_summary_from_report_payload({}, ()),
        'checked': {},
        'catalog_version': TOPOLOGY_INTEGRITY_CATALOG_VERSION,
        'findings': [],
    }


def _latest_topology_integrity_operation_run(fabric: Fabric | None) -> OperationRun | None:
    queryset = OperationRun.objects.filter(
        profile=TOPOLOGY_INTEGRITY_OPERATION_PROFILE,
        status='completed',
    )
    if fabric is None:
        queryset = queryset.filter(fabric__isnull=True)
    else:
        queryset = queryset.filter(fabric=fabric)

    for run in queryset.order_by('-completed_at', '-created', '-pk')[:50]:
        if _operation_run_has_topology_integrity_report(run):
            return run
    return None


def _operation_run_has_topology_integrity_report(run: OperationRun) -> bool:
    for payload in (run.result or {}, run.parameters or {}, run.metadata or {}):
        if not isinstance(payload, Mapping):
            continue
        if payload.get('operation_kind') == TOPOLOGY_INTEGRITY_OPERATION_KIND:
            return True
        if payload.get('report_schema') == TOPOLOGY_INTEGRITY_REPORT_SCHEMA:
            return True
    return False


def _build_gate_result(
    report_payload: Mapping[str, Any],
    *,
    fail_threshold: str,
    source: str,
    operation_run_id: int | None,
    missing_report: bool = False,
) -> TopologyIntegrityGateResult:
    payload = _normalized_report_payload(report_payload)
    findings = _finding_payloads(payload.get('findings') or ())
    grouped_summary = payload['grouped_summary']
    blocking_findings = tuple(finding for finding in findings if _finding_path_blocking(finding))
    reported_blocking_count = _safe_int(grouped_summary.get('path_blocking_count')) or 0
    blocking_count = max(len(blocking_findings), reported_blocking_count)
    threshold_count = _threshold_count_from_payloads(findings, fail_threshold)
    non_blocking_warning_count = sum(
        1
        for finding in findings
        if not _finding_path_blocking(finding)
        and SEVERITY_RANK.get(_severity_from_finding_payload(finding), 0) >= SEVERITY_RANK['warning']
    )

    if blocking_count or threshold_count:
        status = 'fail'
    elif missing_report or non_blocking_warning_count:
        status = 'warn'
    else:
        status = 'pass'

    return TopologyIntegrityGateResult(
        status=status,
        ok=status != 'fail',
        scope=payload['scope'],
        summary=payload['summary'],
        grouped_summary=grouped_summary,
        blocking_findings=blocking_findings,
        blocking_count=blocking_count,
        fail_threshold=fail_threshold,
        fail_threshold_count=threshold_count,
        non_blocking_warning_count=non_blocking_warning_count,
        source=source,
        operation_run_id=operation_run_id,
    )


def _threshold_count_from_payloads(findings, fail_threshold: str) -> int:
    threshold = SEVERITY_RANK.get(fail_threshold, 0)
    if threshold <= 0:
        return 0
    return sum(
        1
        for finding in _finding_payloads(findings)
        if SEVERITY_RANK.get(_severity_from_finding_payload(finding), 0) >= threshold
    )


def topology_integrity_finding_catalog() -> dict[str, dict[str, Any]]:
    return {
        code: entry.as_dict()
        for code, entry in sorted(TOPOLOGY_INTEGRITY_FINDING_CATALOG.items())
    }


def _catalog_entry_for_code(code: str) -> FindingCatalogEntry:
    entry = TOPOLOGY_INTEGRITY_FINDING_CATALOG.get(code)
    if entry is not None:
        return entry
    return FindingCatalogEntry(
        code=code,
        severity='warning',
        object_family='unknown',
        likely_cause='This finding code has not been added to the topology integrity catalog.',
        remediation='Add catalog metadata for this code before exposing it to operator workflows.',
        affected_workflows=(),
        path_blocking=False,
    )


def _severity_summary(findings) -> dict[str, int]:
    counts = Counter(finding.severity for finding in findings)
    return {
        'total': len(findings),
        'critical': counts.get('critical', 0),
        'error': counts.get('error', 0),
        'warning': counts.get('warning', 0),
        'info': counts.get('info', 0),
    }


def _workflow_counts(findings) -> dict[str, int]:
    counts = Counter()
    for finding in findings:
        counts.update(finding.affected_workflows)
    return {key: counts.get(key, 0) for key in WORKFLOW_FLAG_KEYS}


def _workflow_flags_from_counts(counts: dict[str, int]) -> dict[str, bool]:
    return {key: counts.get(key, 0) > 0 for key in WORKFLOW_FLAG_KEYS}


def _family_sort_key(family: str) -> tuple[int, str]:
    try:
        return (OBJECT_FAMILY_ORDER.index(family), family)
    except ValueError:
        return (len(OBJECT_FAMILY_ORDER), family)


def _code_summary_payload(code: str, count: int) -> dict[str, Any]:
    payload = _catalog_entry_for_code(code).as_dict()
    payload['count'] = count
    return payload


def _grouped_findings_summary(findings: tuple[IntegrityFinding, ...]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    path_blocking_count = 0
    for index, finding in enumerate(findings):
        entry = finding.catalog_entry
        family = entry.object_family
        group = groups.setdefault(
            family,
            {
                'object_family': family,
                'object_family_label': OBJECT_FAMILY_LABELS.get(family, family.title()),
                'finding_indexes': [],
                'findings': [],
                'code_counts': Counter(),
                'path_blocking_count': 0,
            },
        )
        group['finding_indexes'].append(index)
        group['findings'].append(finding)
        group['code_counts'][finding.code] += 1
        if finding.path_blocking:
            group['path_blocking_count'] += 1
            path_blocking_count += 1

    group_payloads = []
    for family, group in sorted(groups.items(), key=lambda item: _family_sort_key(item[0])):
        group_findings = tuple(group['findings'])
        workflow_counts = _workflow_counts(group_findings)
        codes = [
            _code_summary_payload(code, count)
            for code, count in sorted(
                group['code_counts'].items(),
                key=lambda item: (-SEVERITY_RANK.get(_catalog_entry_for_code(item[0]).severity, 0), item[0]),
            )
        ]
        group_payloads.append(
            {
                'object_family': family,
                'object_family_label': group['object_family_label'],
                'summary': _severity_summary(group_findings),
                'path_blocking_count': group['path_blocking_count'],
                'workflow_counts': workflow_counts,
                'workflow_flags': _workflow_flags_from_counts(workflow_counts),
                'affected_workflows': [
                    workflow
                    for workflow in WORKFLOW_FLAG_KEYS
                    if workflow_counts.get(workflow, 0)
                ],
                'finding_indexes': group['finding_indexes'],
                'codes': codes,
            }
        )

    workflow_counts = _workflow_counts(findings)
    return {
        'catalog_version': TOPOLOGY_INTEGRITY_CATALOG_VERSION,
        'path_blocking_count': path_blocking_count,
        'workflow_counts': workflow_counts,
        'workflow_flags': _workflow_flags_from_counts(workflow_counts),
        'affected_workflows': [
            workflow
            for workflow in WORKFLOW_FLAG_KEYS
            if workflow_counts.get(workflow, 0)
        ],
        'groups': group_payloads,
    }


def _stable_report_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _fabric_from_report_scope(report: TopologyIntegrityReport) -> Fabric | None:
    fabric_id = report.scope.get('fabric_id')
    if fabric_id is None:
        return None
    return Fabric.objects.filter(pk=fabric_id).first()


def _normalize_operation_run(operation_run: OperationRun | int | None) -> OperationRun | None:
    if operation_run is None:
        return None
    if isinstance(operation_run, OperationRun):
        return operation_run
    return OperationRun.objects.get(pk=operation_run)


def persist_topology_integrity_report(
    report: TopologyIntegrityReport,
    *,
    operation_run: OperationRun | int | None = None,
    actor=None,
    parameters: dict[str, Any] | None = None,
) -> OperationRun:
    report_payload = report.as_dict()
    summary = report.summary
    grouped_summary = report.grouped_summary
    report_hash = _stable_report_hash(report_payload)
    now = timezone.now()
    fabric = _fabric_from_report_scope(report)
    run_parameters = {
        'operation_kind': TOPOLOGY_INTEGRITY_OPERATION_KIND,
        'scope': report.scope,
        **(parameters or {}),
    }
    result = {
        'operation_kind': TOPOLOGY_INTEGRITY_OPERATION_KIND,
        'report_schema': TOPOLOGY_INTEGRITY_REPORT_SCHEMA,
        'catalog_version': TOPOLOGY_INTEGRITY_CATALOG_VERSION,
        'ok': report.ok,
        'finding_count': summary['total'],
        'summary': summary,
        'grouped_summary': grouped_summary,
        'path_blocking_count': grouped_summary['path_blocking_count'],
        'checked': report.checked,
        'scope': report.scope,
        'report_hash': report_hash,
        'report': report_payload,
    }
    metadata = {
        'operation_kind': TOPOLOGY_INTEGRITY_OPERATION_KIND,
        'report_schema': TOPOLOGY_INTEGRITY_REPORT_SCHEMA,
        'catalog_version': TOPOLOGY_INTEGRITY_CATALOG_VERSION,
        'report_hash': report_hash,
        'path_blocking_count': grouped_summary['path_blocking_count'],
        'workflow_flags': grouped_summary['workflow_flags'],
    }

    run = _normalize_operation_run(operation_run)
    if run is None:
        return OperationRun.objects.create(
            profile=TOPOLOGY_INTEGRITY_OPERATION_PROFILE,
            status='completed',
            fabric=fabric,
            initiated_by=actor,
            parameters=run_parameters,
            result=result,
            metadata=metadata,
            started_at=now,
            completed_at=now,
        )

    run.profile = TOPOLOGY_INTEGRITY_OPERATION_PROFILE
    run.status = 'completed'
    run.fabric = fabric
    if actor is not None:
        run.initiated_by = actor
    run.parameters = run_parameters
    run.result = result
    run.error_detail = ''
    run.metadata = {
        **(run.metadata or {}),
        **metadata,
    }
    run.started_at = run.started_at or now
    run.completed_at = now
    run.save(
        update_fields=[
            'profile',
            'status',
            'fabric',
            'initiated_by',
            'parameters',
            'result',
            'error_detail',
            'metadata',
            'started_at',
            'completed_at',
            'last_updated',
        ]
    )
    return run


def audit_topology_integrity(*, fabric: Fabric | int | None = None) -> TopologyIntegrityReport:
    target_fabric = _normalize_fabric(fabric)
    fabrics = tuple(_fabric_queryset(target_fabric).select_related('architecture').order_by('slug', 'pk'))
    fabric_ids = {item.pk for item in fabrics}
    findings: list[IntegrityFinding] = []

    _check_dark_mpo_position_usage(fabrics=fabrics, findings=findings)
    _check_fiber_strand_termination_integrity(fabric_ids=fabric_ids, findings=findings)
    _check_cable_assembly_references(fabric_ids=fabric_ids, findings=findings)
    _check_transport_channel_position_maps(fabrics=fabrics, fabric_ids=fabric_ids, findings=findings)
    _check_transfer_maps(fabric_ids=fabric_ids, findings=findings)
    _check_optical_lanes(fabrics=fabrics, fabric_ids=fabric_ids, findings=findings)

    findings.sort(
        key=lambda item: (
            -SEVERITY_RANK.get(item.severity, 0),
            item.code,
            item.object.model,
            item.object.object_id or 0,
        )
    )
    return TopologyIntegrityReport(
        scope=_scope_payload(target_fabric),
        checked=_checked_counts(fabric_ids),
        findings=tuple(findings),
    )


def format_topology_integrity_report_text(report: TopologyIntegrityReport) -> str:
    scope = report.scope
    if scope.get('fabric_id') is None:
        scope_label = 'global'
    else:
        scope_label = f"fabric {scope.get('fabric_slug') or scope.get('fabric_id')}"

    summary = report.summary
    checked = ', '.join(f'{key}={value}' for key, value in sorted(report.checked.items()) if value)
    lines = [
        f'Topology integrity audit ({scope_label})',
        (
            f"Findings: total={summary['total']} critical={summary['critical']} "
            f"error={summary['error']} warning={summary['warning']} info={summary['info']}"
        ),
        f'Checked: {checked or "no V2 topology objects"}',
    ]
    if not report.findings:
        lines.append('OK: no topology integrity findings.')
        return '\n'.join(lines)

    grouped_summary = report.grouped_summary
    lines.append(
        f"Path-blocking findings: {grouped_summary['path_blocking_count']}"
    )
    if grouped_summary['groups']:
        lines.append('Groups:')
        for group in grouped_summary['groups']:
            group_summary = group['summary']
            workflows = ','.join(group['affected_workflows']) or 'none'
            lines.append(
                f"  {group['object_family_label']}: total={group_summary['total']} "
                f"critical={group_summary['critical']} error={group_summary['error']} "
                f"warning={group_summary['warning']} info={group_summary['info']} "
                f"path_blocking={group['path_blocking_count']} workflows={workflows}"
            )

    for finding in report.findings:
        object_id = finding.object.object_id if finding.object.object_id is not None else 'n/a'
        lines.append(
            f'[{finding.severity.upper()}] {finding.code} '
            f'{finding.object.model}#{object_id}: {finding.message}'
        )
        if finding.remediation:
            lines.append(f'  remediation: {finding.remediation}')
    return '\n'.join(lines)


def _normalize_fabric(fabric: Fabric | int | None) -> Fabric | None:
    if fabric is None:
        return None
    if isinstance(fabric, Fabric):
        return fabric
    return Fabric.objects.get(pk=fabric)


def _fabric_queryset(fabric: Fabric | None):
    queryset = Fabric.objects.all()
    if fabric is not None:
        queryset = queryset.filter(pk=fabric.pk)
    return queryset


def _scope_payload(fabric: Fabric | None) -> dict[str, Any]:
    if fabric is None:
        return {
            'fabric_id': None,
            'fabric_slug': '',
        }
    return {
        'fabric_id': fabric.pk,
        'fabric_slug': fabric.slug,
    }


def _checked_counts(fabric_ids: set[int]) -> dict[str, int]:
    if not fabric_ids:
        return {
            'fabrics': 0,
            'endpoints': 0,
            'connector_positions': 0,
            'transport_channels': 0,
            'transport_channel_position_maps': 0,
            'fiber_segments': 0,
            'fiber_strands': 0,
            'strand_terminations': 0,
            'cable_assemblies': 0,
            'transfer_maps': 0,
            'optical_lanes': 0,
        }

    cable_refs = _strand_cable_refs(fabric_ids)
    cable_assembly_filter = Q(metadata__fabric_id__in=list(fabric_ids))
    if cable_refs:
        cable_assembly_filter |= Q(pk__in=list(cable_refs.values()))
    return {
        'fabrics': Fabric.objects.filter(pk__in=fabric_ids).count(),
        'endpoints': Endpoint.objects.filter(fabric_id__in=fabric_ids).count(),
        'connector_positions': ConnectorPosition.objects.filter(endpoint__fabric_id__in=fabric_ids).count(),
        'transport_channels': TransportChannel.objects.filter(fabric_id__in=fabric_ids).count(),
        'transport_channel_position_maps': TransportChannelPositionMap.objects.filter(
            channel__fabric_id__in=fabric_ids
        ).count(),
        'fiber_segments': FiberSegment.objects.filter(fabric_id__in=fabric_ids).count(),
        'fiber_strands': FiberStrand.objects.filter(segment__fabric_id__in=fabric_ids).count(),
        'strand_terminations': StrandTermination.objects.filter(strand__segment__fabric_id__in=fabric_ids).count(),
        'cable_assemblies': CableAssembly.objects.filter(cable_assembly_filter).distinct().count(),
        'transfer_maps': TransferMap.objects.filter(fabric_id__in=fabric_ids).count(),
        'optical_lanes': OpticalLane.objects.filter(fabric_id__in=fabric_ids).count(),
    }


def _ref(obj, *, label: str | None = None) -> IntegrityObjectRef:
    return IntegrityObjectRef(
        model=obj._meta.label_lower,
        object_id=getattr(obj, 'pk', None),
        label=label if label is not None else str(obj),
    )


def _fabric_finding_kwargs(
    fabric: Fabric | None = None,
    *,
    fabric_id: int | None = None,
    fabric_slug: str = '',
) -> dict:
    if fabric is not None:
        return {
            'fabric_id': fabric.pk,
            'fabric_slug': fabric.slug,
        }
    return {
        'fabric_id': fabric_id,
        'fabric_slug': fabric_slug,
    }


def _add_finding(
    findings: list[IntegrityFinding],
    *,
    severity: str,
    code: str,
    message: str,
    obj,
    fabric: Fabric | None = None,
    fabric_id: int | None = None,
    fabric_slug: str = '',
    remediation: str = '',
    related_objects: tuple[IntegrityObjectRef, ...] = (),
    details: dict[str, Any] | None = None,
) -> None:
    catalog_entry = _catalog_entry_for_code(code)
    findings.append(
        IntegrityFinding(
            severity=severity,
            code=code,
            message=message,
            object=_ref(obj),
            remediation=remediation or catalog_entry.remediation,
            related_objects=related_objects,
            details=details or {},
            **_fabric_finding_kwargs(fabric, fabric_id=fabric_id, fabric_slug=fabric_slug),
        )
    )


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_channel_map_entries(fabric: Fabric) -> tuple[ChannelMapEntry, ...]:
    architecture = fabric.architecture
    raw_entries: list[dict[str, Any]] = []
    source = ''

    if architecture is not None:
        for rule_set in architecture.allocation_rule_sets.all():
            rule = rule_set.rule if isinstance(rule_set.rule, dict) else {}
            matrix = rule.get('channel_map_matrix') or []
            if isinstance(matrix, list):
                raw_entries.extend(entry for entry in matrix if isinstance(entry, dict))
                if matrix and not source:
                    source = f'allocation_rule_set:{rule_set.slug}'

    if not raw_entries and architecture is not None:
        if architecture.slug == ARCHITECTURE_SLUG and architecture.version == ARCHITECTURE_VERSION:
            raw_entries = [dict(entry) for entry in CHANNEL_MAP_MATRIX]
            source = 'built_in_architecture_constants'

    entries = []
    for raw_entry in raw_entries:
        channel_index = _safe_int(raw_entry.get('subinterface_index'))
        mpo_index = _safe_int(raw_entry.get('mpo_index'))
        positions = tuple(
            position
            for position in (_safe_int(raw_position) for raw_position in raw_entry.get('positions') or ())
            if position is not None
        )
        if channel_index is None or mpo_index is None or not positions:
            continue
        entries.append(
            ChannelMapEntry(
                channel_index=channel_index,
                mpo_index=mpo_index,
                positions=positions,
            )
        )
    return tuple(entries)


def _mpo_position_policy(fabric: Fabric) -> MPOPositionPolicy | None:
    architecture = fabric.architecture
    if architecture is None:
        return None

    active_positions: set[int] = set()
    position_count = 0
    source = ''

    for rule_set in architecture.allocation_rule_sets.all():
        rule = rule_set.rule if isinstance(rule_set.rule, dict) else {}
        for key in ('positions_per_mpo', 'mpo_position_count', 'position_count'):
            rule_position_count = _safe_int(rule.get(key))
            if rule_position_count is not None:
                position_count = max(position_count, rule_position_count)

    for entry in _normalized_channel_map_entries(fabric):
        active_positions.update(entry.positions)
        position_count = max(position_count, *(entry.positions or (0,)))
        if not source:
            source = 'channel_map_matrix'

    for pattern in architecture.transfer_patterns.all():
        rule = pattern.rule if isinstance(pattern.rule, dict) else {}
        for group in rule.get('groups') or ():
            if not isinstance(group, dict):
                continue
            active_groups = group.get('active_position_groups') or {}
            if not isinstance(active_groups, dict):
                continue
            for raw_positions in active_groups.values():
                for raw_position in raw_positions or ():
                    position = _safe_int(raw_position)
                    if position is not None:
                        active_positions.add(position)
                        position_count = max(position_count, position)
                        if not source:
                            source = f'transfer_pattern:{pattern.slug}'

    if architecture.slug == ARCHITECTURE_SLUG and architecture.version == ARCHITECTURE_VERSION:
        position_count = max(position_count, MPO_POSITION_COUNT)
        if not active_positions:
            for entry in CHANNEL_MAP_MATRIX:
                active_positions.update(int(position) for position in entry['positions'])
            source = 'built_in_architecture_constants'

    if not active_positions or not position_count:
        return None

    dark_positions = frozenset(
        position
        for position in range(1, position_count + 1)
        if position not in active_positions
    )
    if not dark_positions:
        return None

    dark_positions_by_plane = _dark_position_overrides_from_stamp_manifest(
        fabric=fabric,
        active_positions=frozenset(active_positions),
        position_count=position_count,
    )
    if dark_positions_by_plane:
        dark_positions = dark_positions.union(*dark_positions_by_plane.values())
        source = 'stamp_run.dark_position_overrides'

    return MPOPositionPolicy(
        position_count=position_count,
        active_positions=frozenset(active_positions),
        dark_positions=dark_positions,
        source=source or 'architecture',
        dark_positions_by_plane=dark_positions_by_plane,
    )


def _dark_position_overrides_from_stamp_manifest(
    *,
    fabric: Fabric,
    active_positions: frozenset[int],
    position_count: int,
) -> dict[int, frozenset[int]]:
    run = StampRun.objects.filter(fabric=fabric, status='completed').order_by('-created', '-pk').first()
    if run is None:
        return {}
    result = run.result if isinstance(run.result, Mapping) else {}
    overrides = result.get('dark_position_overrides') or (result.get('stamp_manifest') or {}).get('dark_position_overrides')
    if not isinstance(overrides, Mapping):
        return {}
    all_positions = set(range(1, position_count + 1))
    normalized = {}
    for raw_plane, raw_positions in overrides.items():
        plane = _safe_int(raw_plane)
        if plane is None or not isinstance(raw_positions, (list, tuple, set)):
            continue
        dark_positions = frozenset(
            position
            for position in (_safe_int(raw_position) for raw_position in raw_positions)
            if position is not None
        )
        if not dark_positions:
            continue
        if active_positions & dark_positions:
            continue
        if set(active_positions) | set(dark_positions) != all_positions:
            continue
        normalized[plane] = dark_positions
    return normalized


def _dark_positions_for_plane(policy: MPOPositionPolicy, plane_number: int | None) -> frozenset[int]:
    if plane_number is not None and policy.dark_positions_by_plane:
        return policy.dark_positions_by_plane.get(plane_number, policy.dark_positions)
    return policy.dark_positions


def _plane_number_from_segment_name(name: str | None) -> int | None:
    if not name or not name.startswith('P'):
        return None
    raw_value = name[1:].split('-', 1)[0]
    return _safe_int(raw_value)


def _is_mpo_endpoint(endpoint: Endpoint | None) -> bool:
    return endpoint is not None and endpoint.connector_kind in MPO_CONNECTOR_KINDS


def _check_dark_mpo_position_usage(*, fabrics: tuple[Fabric, ...], findings: list[IntegrityFinding]) -> None:
    for fabric in fabrics:
        policy = _mpo_position_policy(fabric)
        if policy is None:
            continue
        dark_positions = set(policy.dark_positions)
        remediation = (
            'Move modeled lanes, strand terminations, channel maps, and transfer maps to active MPO positions, '
            'or update the architecture channel map if this position should be active.'
        )

        lane_queryset = (
            OpticalLane.objects.filter(
                fabric=fabric,
                local_mpo_position__position_number__in=dark_positions,
            )
            .select_related('local_mpo_endpoint', 'local_mpo_position', 'plane')
            .order_by('pk')
        )
        for lane in lane_queryset:
            if not _is_mpo_endpoint(lane.local_mpo_endpoint):
                continue
            lane_dark_positions = _dark_positions_for_plane(policy, getattr(lane.plane, 'plane_number', None))
            if lane.local_mpo_position.position_number not in lane_dark_positions:
                continue
            _add_finding(
                findings,
                severity='error',
                code='dark_mpo_position_usage',
                message=(
                    f'Optical lane is anchored to dark MPO position '
                    f'{lane.local_mpo_position.position_number} on {lane.local_mpo_endpoint}.'
                ),
                obj=lane,
                fabric=fabric,
                remediation=remediation,
                related_objects=(_ref(lane.local_mpo_position),),
                details={
                    'position_number': lane.local_mpo_position.position_number,
                    'dark_positions': sorted(lane_dark_positions),
                    'policy_source': policy.source,
                    'plane_number': getattr(lane.plane, 'plane_number', None),
                },
            )

        termination_queryset = (
            StrandTermination.objects.filter(
                strand__segment__fabric=fabric,
                mpo_position__position_number__in=dark_positions,
            )
            .select_related('strand', 'strand__segment', 'mpo_endpoint', 'mpo_position')
            .order_by('pk')
        )
        for termination in termination_queryset:
            if not _is_mpo_endpoint(termination.mpo_endpoint):
                continue
            plane_number = _plane_number_from_segment_name(getattr(termination.strand.segment, 'name', ''))
            termination_dark_positions = _dark_positions_for_plane(policy, plane_number)
            if termination.mpo_position.position_number not in termination_dark_positions:
                continue
            _add_finding(
                findings,
                severity='error',
                code='dark_mpo_position_usage',
                message=(
                    f'Strand termination uses dark MPO position '
                    f'{termination.mpo_position.position_number} on {termination.mpo_endpoint}.'
                ),
                obj=termination,
                fabric=fabric,
                remediation=remediation,
                related_objects=(_ref(termination.mpo_position), _ref(termination.strand)),
                details={
                    'position_number': termination.mpo_position.position_number,
                    'dark_positions': sorted(termination_dark_positions),
                    'policy_source': policy.source,
                    'plane_number': plane_number,
                },
            )

        channel_map_queryset = (
            TransportChannelPositionMap.objects.filter(
                channel__fabric=fabric,
                mpo_position__position_number__in=dark_positions,
            )
            .select_related('channel', 'channel__plane', 'mpo_endpoint', 'mpo_position')
            .order_by('pk')
        )
        for position_map in channel_map_queryset:
            if not _is_mpo_endpoint(position_map.mpo_endpoint):
                continue
            map_dark_positions = _dark_positions_for_plane(
                policy,
                getattr(position_map.channel.plane, 'plane_number', None),
            )
            if position_map.mpo_position.position_number not in map_dark_positions:
                continue
            _add_finding(
                findings,
                severity='error',
                code='dark_mpo_position_usage',
                message=(
                    f'Transport channel position map uses dark MPO position '
                    f'{position_map.mpo_position.position_number} on {position_map.mpo_endpoint}.'
                ),
                obj=position_map,
                fabric=fabric,
                remediation=remediation,
                related_objects=(_ref(position_map.mpo_position), _ref(position_map.channel)),
                details={
                    'position_number': position_map.mpo_position.position_number,
                    'dark_positions': sorted(map_dark_positions),
                    'policy_source': policy.source,
                    'plane_number': getattr(position_map.channel.plane, 'plane_number', None),
                },
            )

        transfer_map_queryset = (
            TransferMap.objects.filter(fabric=fabric)
            .filter(
                Q(src_position__position_number__in=dark_positions)
                | Q(dst_position__position_number__in=dark_positions)
            )
            .select_related('src_position', 'src_position__endpoint', 'dst_position', 'dst_position__endpoint')
            .order_by('pk')
        )
        for transfer_map in transfer_map_queryset:
            transfer_plane_number = _safe_int((transfer_map.metadata or {}).get('plane_number'))
            transfer_dark_positions = _dark_positions_for_plane(policy, transfer_plane_number)
            dark_related = []
            position_numbers = []
            for position in (transfer_map.src_position, transfer_map.dst_position):
                if position.position_number in transfer_dark_positions and _is_mpo_endpoint(position.endpoint):
                    dark_related.append(_ref(position))
                    position_numbers.append(position.position_number)
            if not dark_related:
                continue
            _add_finding(
                findings,
                severity='error',
                code='dark_mpo_position_usage',
                message=f'Transfer map references dark MPO position(s) {", ".join(map(str, position_numbers))}.',
                obj=transfer_map,
                fabric=fabric,
                remediation=remediation,
                related_objects=tuple(dark_related),
                details={
                    'position_numbers': position_numbers,
                    'dark_positions': sorted(transfer_dark_positions),
                    'policy_source': policy.source,
                    'plane_number': transfer_plane_number,
                },
            )


def _strand_queryset(fabric_ids: set[int]):
    return (
        FiberStrand.objects.filter(segment__fabric_id__in=fabric_ids)
        .select_related(
            'segment',
            'segment__fabric',
            'segment__a_endpoint',
            'segment__b_endpoint',
            'cable_site',
        )
        .prefetch_related(
            Prefetch(
                'terminations',
                queryset=StrandTermination.objects.select_related(
                    'mpo_endpoint',
                    'mpo_position',
                    'mpo_position__endpoint',
                ).order_by('termination_index', 'pk'),
            )
        )
        .order_by('segment__name', 'strand_index', 'pk')
    )


def _check_fiber_strand_termination_integrity(*, fabric_ids: set[int], findings: list[IntegrityFinding]) -> None:
    if not fabric_ids:
        return

    for segment in (
        FiberSegment.objects.filter(fabric_id__in=fabric_ids)
        .select_related('fabric', 'a_endpoint', 'b_endpoint')
        .order_by('pk')
    ):
        if segment.a_endpoint_id == segment.b_endpoint_id:
            _add_finding(
                findings,
                severity='error',
                code='fiber_segment_endpoint_invalid',
                message='Fiber segment connects an endpoint to itself.',
                obj=segment,
                fabric=segment.fabric,
                remediation='Point a_endpoint and b_endpoint at two distinct MPO endpoints before using the segment.',
                related_objects=(_ref(segment.a_endpoint),),
            )
        endpoint_fabric_ids = {segment.a_endpoint.fabric_id, segment.b_endpoint.fabric_id}
        if endpoint_fabric_ids != {segment.fabric_id}:
            _add_finding(
                findings,
                severity='error',
                code='fiber_segment_endpoint_fabric_mismatch',
                message='Fiber segment endpoints do not both belong to the segment fabric.',
                obj=segment,
                fabric=segment.fabric,
                remediation='Move the segment to the endpoint fabric or replace the mismatched endpoint reference.',
                related_objects=(_ref(segment.a_endpoint), _ref(segment.b_endpoint)),
                details={'endpoint_fabric_ids': sorted(endpoint_fabric_ids)},
            )

    for strand in _strand_queryset(fabric_ids):
        fabric = strand.segment.fabric
        terminations = list(strand.terminations.all())
        if len(terminations) != 2:
            _add_finding(
                findings,
                severity='error',
                code='fiber_strand_termination_count',
                message=f'Fiber strand has {len(terminations)} termination(s); exactly two are required.',
                obj=strand,
                fabric=fabric,
                remediation=(
                    'Add or remove StrandTermination rows so the strand has one termination at each segment end.'
                ),
                related_objects=tuple(_ref(termination) for termination in terminations),
                details={'termination_count': len(terminations)},
            )

        endpoint_ids = set()
        termination_indexes = []
        for termination in terminations:
            endpoint_ids.add(termination.mpo_endpoint_id)
            if termination.termination_index is not None:
                termination_indexes.append(termination.termination_index)

            if termination.mpo_position.endpoint_id != termination.mpo_endpoint_id:
                _add_finding(
                    findings,
                    severity='error',
                    code='strand_termination_position_mismatch',
                    message='Strand termination position does not belong to the declared MPO endpoint.',
                    obj=termination,
                    fabric=fabric,
                    remediation=(
                        'Set mpo_endpoint to the connector owning mpo_position, or select a position on the '
                        'declared endpoint.'
                    ),
                    related_objects=(_ref(termination.mpo_endpoint), _ref(termination.mpo_position)),
                    details={
                        'mpo_endpoint_id': termination.mpo_endpoint_id,
                        'position_endpoint_id': termination.mpo_position.endpoint_id,
                    },
                )
            if termination.mpo_endpoint.fabric_id != strand.segment.fabric_id:
                _add_finding(
                    findings,
                    severity='error',
                    code='strand_termination_fabric_mismatch',
                    message='Strand termination endpoint does not belong to the strand fabric.',
                    obj=termination,
                    fabric=fabric,
                    remediation='Move the termination to an endpoint in the same fabric as the strand segment.',
                    related_objects=(_ref(termination.mpo_endpoint), _ref(strand)),
                    details={
                        'termination_endpoint_fabric_id': termination.mpo_endpoint.fabric_id,
                        'strand_fabric_id': strand.segment.fabric_id,
                    },
                )

        expected_endpoint_ids = {strand.segment.a_endpoint_id, strand.segment.b_endpoint_id}
        if len(terminations) == 2 and endpoint_ids != expected_endpoint_ids:
            _add_finding(
                findings,
                severity='error',
                code='fiber_strand_segment_endpoint_mismatch',
                message='Fiber strand terminations do not match the FiberSegment endpoint pair.',
                obj=strand,
                fabric=fabric,
                remediation=(
                    'Terminate the strand on the segment a_endpoint and b_endpoint, or correct the FiberSegment '
                    'endpoints.'
                ),
                related_objects=tuple(_ref(termination) for termination in terminations),
                details={
                    'termination_endpoint_ids': sorted(endpoint_ids),
                    'segment_endpoint_ids': sorted(expected_endpoint_ids),
                },
            )

        if termination_indexes:
            duplicate_indexes = sorted(
                index for index, count in Counter(termination_indexes).items() if count > 1
            )
            invalid_indexes = sorted(index for index in termination_indexes if index not in {1, 2})
            if duplicate_indexes or invalid_indexes:
                _add_finding(
                    findings,
                    severity='warning',
                    code='strand_termination_index_incoherent',
                    message='Strand termination indexes should identify the two strand ends as 1 and 2.',
                    obj=strand,
                    fabric=fabric,
                    remediation=(
                        'Normalize termination_index values to one 1 and one 2 for deterministic operator displays.'
                    ),
                    related_objects=tuple(_ref(termination) for termination in terminations),
                    details={
                        'termination_indexes': termination_indexes,
                        'duplicate_indexes': duplicate_indexes,
                        'invalid_indexes': invalid_indexes,
                    },
                )


def _strand_cable_refs(fabric_ids: set[int]) -> dict[tuple[int, str], int]:
    if not fabric_ids:
        return {}

    refs = {
        (site_id, cable_id)
        for site_id, cable_id in (
            FiberStrand.objects.filter(
                segment__fabric_id__in=fabric_ids,
                cable_site_id__isnull=False,
            )
            .exclude(cable_id='')
            .values_list('cable_site_id', 'cable_id')
            .distinct()
        )
    }
    if not refs:
        return {}

    site_ids = {site_id for site_id, _cable_id in refs}
    cable_ids = {cable_id for _site_id, cable_id in refs}
    existing = {
        (site_id, cable_id): pk
        for pk, site_id, cable_id in CableAssembly.objects.filter(
            site_id__in=site_ids,
            cable_id__in=cable_ids,
        ).values_list('pk', 'site_id', 'cable_id')
    }
    return existing


def _check_cable_assembly_references(*, fabric_ids: set[int], findings: list[IntegrityFinding]) -> None:
    if not fabric_ids:
        return

    referenced_assembly_ids: set[int] = set()
    strands = list(_strand_queryset(fabric_ids))
    ref_keys = {
        (strand.cable_site_id, strand.cable_id)
        for strand in strands
        if strand.cable_site_id and strand.cable_id
    }
    assemblies_by_ref = {}
    if ref_keys:
        assemblies_by_ref = {
            (site_id, cable_id): assembly
            for assembly, site_id, cable_id in (
                (assembly, assembly.site_id, assembly.cable_id)
                for assembly in CableAssembly.objects.filter(
                    site_id__in={site_id for site_id, _cable_id in ref_keys},
                    cable_id__in={cable_id for _site_id, cable_id in ref_keys},
                ).select_related('site', 'parent_cable', 'parent_cable__site')
            )
        }

    for strand in strands:
        fabric = strand.segment.fabric
        has_site = bool(strand.cable_site_id)
        has_cable_id = bool(strand.cable_id)
        if has_site != has_cable_id:
            _add_finding(
                findings,
                severity='error',
                code='cable_reference_incomplete',
                message='Fiber strand cable reference has only one of cable_site or cable_id set.',
                obj=strand,
                fabric=fabric,
                remediation=(
                    'Set both cable_site and cable_id, or clear both until the strand is assigned to a cable assembly.'
                ),
                details={
                    'cable_site_id': strand.cable_site_id,
                    'cable_id': strand.cable_id,
                },
            )
            continue

        if not has_site and not has_cable_id:
            continue

        assembly = assemblies_by_ref.get((strand.cable_site_id, strand.cable_id))
        if assembly is None:
            _add_finding(
                findings,
                severity='error',
                code='cable_reference_missing',
                message='Fiber strand references a cable assembly that does not exist for its site and cable ID.',
                obj=strand,
                fabric=fabric,
                remediation='Create the CableAssembly row, or correct the strand cable_site/cable_id values.',
                details={
                    'cable_site_id': strand.cable_site_id,
                    'cable_id': strand.cable_id,
                },
            )
            continue
        referenced_assembly_ids.add(assembly.pk)

    assembly_queryset = CableAssembly.objects.filter(
        Q(pk__in=referenced_assembly_ids) | Q(metadata__fabric_id__in=list(fabric_ids))
    ).select_related('site', 'parent_cable', 'parent_cable__site')
    for assembly in assembly_queryset:
        if assembly.parent_cable_id == assembly.pk:
            _add_finding(
                findings,
                severity='error',
                code='cable_parent_self_reference',
                message='Cable assembly is its own parent.',
                obj=assembly,
                remediation='Clear parent_cable or point it at a distinct parent cable assembly.',
                details={'site_id': assembly.site_id, 'cable_id': assembly.cable_id},
            )
        if assembly.parent_cable_id and assembly.parent_cable.site_id != assembly.site_id:
            _add_finding(
                findings,
                severity='error',
                code='cable_parent_site_mismatch',
                message='Cable assembly parent belongs to a different site.',
                obj=assembly,
                remediation='Use a parent cable assembly from the same site, or split the child assembly reference.',
                related_objects=(_ref(assembly.parent_cable),),
                details={
                    'site_id': assembly.site_id,
                    'parent_site_id': assembly.parent_cable.site_id,
                },
            )


def _endpoint_declared_mpo_index(endpoint: Endpoint, *, fallback: int | None = None) -> int | None:
    metadata = endpoint.metadata if isinstance(endpoint.metadata, dict) else {}
    metadata_index = _safe_int(metadata.get('mpo_index'))
    if metadata_index is not None:
        return metadata_index

    for candidate in (endpoint.name, endpoint.address):
        match = re.search(r'(?:^|[.\-_/])MPO[-_. ]?(\d+)$', candidate or '', flags=re.IGNORECASE)
        if match:
            return _safe_int(match.group(1))
    return fallback


def _mpo_children_by_index(parent_endpoint: Endpoint) -> dict[int, Endpoint]:
    children = list(
        Endpoint.objects.filter(
            fabric_id=parent_endpoint.fabric_id,
            parent=parent_endpoint,
            connector_kind__in=MPO_CONNECTOR_KINDS,
        ).order_by('address', 'pk')
    )
    by_index: dict[int, Endpoint] = {}
    next_fallback = 1
    for child in children:
        declared_index = _endpoint_declared_mpo_index(child)
        if declared_index is None:
            while next_fallback in by_index:
                next_fallback += 1
            declared_index = next_fallback
        by_index.setdefault(declared_index, child)
    return by_index


def _expected_channel_position_pairs(channel: TransportChannel, entries: tuple[ChannelMapEntry, ...]):
    matching_entries = tuple(entry for entry in entries if entry.channel_index == channel.channel_index)
    if not matching_entries:
        return None

    mpo_children = _mpo_children_by_index(channel.endpoint)
    missing_mpo_indexes = []
    expected_pairs: set[tuple[int, int]] = set()
    expected_position_ids: set[int] = set()
    position_lookup = {
        (position.endpoint_id, position.position_number): position
        for position in ConnectorPosition.objects.filter(
            endpoint_id__in=[
                endpoint.pk
                for entry in matching_entries
                for endpoint in (mpo_children.get(entry.mpo_index),)
                if endpoint is not None
            ]
        )
    }
    for entry in matching_entries:
        mpo_endpoint = mpo_children.get(entry.mpo_index)
        if mpo_endpoint is None:
            missing_mpo_indexes.append(entry.mpo_index)
            continue
        for position_number in entry.positions:
            expected_pairs.add((mpo_endpoint.pk, position_number))
            position = position_lookup.get((mpo_endpoint.pk, position_number))
            if position is not None:
                expected_position_ids.add(position.pk)

    return {
        'matching_entries': matching_entries,
        'expected_pairs': expected_pairs,
        'expected_position_ids': expected_position_ids,
        'missing_mpo_indexes': tuple(sorted(set(missing_mpo_indexes))),
    }


def _check_transport_channel_position_maps(
    *,
    fabrics: tuple[Fabric, ...],
    fabric_ids: set[int],
    findings: list[IntegrityFinding],
) -> None:
    if not fabric_ids:
        return

    fabrics_by_id = {fabric.pk: fabric for fabric in fabrics}
    channel_entries_by_fabric = {
        fabric.pk: _normalized_channel_map_entries(fabric)
        for fabric in fabrics
    }

    maps = list(
        TransportChannelPositionMap.objects.filter(channel__fabric_id__in=fabric_ids)
        .select_related(
            'channel',
            'channel__fabric',
            'channel__endpoint',
            'mpo_endpoint',
            'mpo_position',
            'mpo_position__endpoint',
        )
        .order_by('pk')
    )
    maps_by_channel: dict[int, list[TransportChannelPositionMap]] = defaultdict(list)
    maps_by_mpo_endpoint_position: dict[tuple[int, int], list[TransportChannelPositionMap]] = defaultdict(list)
    for position_map in maps:
        fabric = fabrics_by_id.get(position_map.channel.fabric_id)
        maps_by_channel[position_map.channel_id].append(position_map)
        maps_by_mpo_endpoint_position[(position_map.mpo_endpoint_id, position_map.mpo_position_id)].append(position_map)

        if position_map.channel.fabric_id != position_map.mpo_endpoint.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='channel_position_map_fabric_mismatch',
                message='Transport channel position map MPO endpoint belongs to a different fabric than the channel.',
                obj=position_map,
                fabric=fabric,
                remediation='Move the map to an MPO endpoint in the channel fabric.',
                related_objects=(_ref(position_map.channel), _ref(position_map.mpo_endpoint)),
            )
        if position_map.mpo_position.endpoint_id != position_map.mpo_endpoint_id:
            _add_finding(
                findings,
                severity='error',
                code='channel_position_map_position_mismatch',
                message='Transport channel position map position does not belong to the declared MPO endpoint.',
                obj=position_map,
                fabric=fabric,
                remediation=(
                    'Set mpo_endpoint to the connector owning mpo_position, or select a position on the '
                    'declared endpoint.'
                ),
                related_objects=(_ref(position_map.mpo_endpoint), _ref(position_map.mpo_position)),
            )
        if (
            position_map.mpo_endpoint.parent_id
            and position_map.mpo_endpoint.parent_id != position_map.channel.endpoint_id
        ):
            _add_finding(
                findings,
                severity='error',
                code='channel_position_map_parent_mismatch',
                message='Transport channel position map MPO endpoint parent does not match the channel endpoint.',
                obj=position_map,
                fabric=fabric,
                remediation='Map the channel to an MPO child of the channel endpoint.',
                related_objects=(_ref(position_map.channel.endpoint), _ref(position_map.mpo_endpoint)),
            )

    for duplicate_maps in maps_by_mpo_endpoint_position.values():
        if len(duplicate_maps) < 2:
            continue
        first = duplicate_maps[0]
        fabric = fabrics_by_id.get(first.channel.fabric_id)
        _add_finding(
            findings,
            severity='error',
            code='channel_position_map_not_disjoint',
            message='One MPO position is assigned to multiple transport channels on the same endpoint.',
            obj=first,
            fabric=fabric,
            remediation='Keep each local MPO position in exactly one TransportChannelPositionMap for an OSFP endpoint.',
            related_objects=tuple(_ref(position_map) for position_map in duplicate_maps[1:]),
            details={
                'channel_ids': sorted({position_map.channel_id for position_map in duplicate_maps}),
                'position_map_ids': [position_map.pk for position_map in duplicate_maps],
                'mpo_position_id': first.mpo_position_id,
            },
        )

    channels = (
        TransportChannel.objects.filter(fabric_id__in=fabric_ids)
        .select_related('fabric', 'endpoint')
        .order_by('endpoint__address', 'channel_index', 'pk')
    )
    for channel in channels:
        fabric = fabrics_by_id.get(channel.fabric_id)
        entries = channel_entries_by_fabric.get(channel.fabric_id, ())
        expected = _expected_channel_position_pairs(channel, entries)
        if expected is None:
            continue

        channel_maps = maps_by_channel.get(channel.pk, [])
        actual_pairs = {
            (position_map.mpo_endpoint_id, position_map.mpo_position.position_number)
            for position_map in channel_maps
        }
        missing_pairs = expected['expected_pairs'] - actual_pairs
        extra_pairs = actual_pairs - expected['expected_pairs']

        if expected['missing_mpo_indexes']:
            _add_finding(
                findings,
                severity='error',
                code='channel_position_map_missing_mpo_endpoint',
                message=(
                    'Transport channel cannot be checked completely because an expected MPO child endpoint is missing.'
                ),
                obj=channel,
                fabric=fabric,
                remediation='Create the expected MPO child endpoint or correct the channel map matrix MPO index.',
                details={'missing_mpo_indexes': list(expected['missing_mpo_indexes'])},
            )

        if missing_pairs:
            _add_finding(
                findings,
                severity='error',
                code='channel_position_map_incomplete',
                message='Transport channel is missing expected MPO position map rows.',
                obj=channel,
                fabric=fabric,
                remediation=(
                    'Create TransportChannelPositionMap rows for every expected active position in the channel.'
                ),
                related_objects=tuple(_ref(position_map) for position_map in channel_maps),
                details={'missing_endpoint_position_pairs': sorted(missing_pairs)},
            )

        if extra_pairs:
            _add_finding(
                findings,
                severity='error',
                code='channel_position_map_extra',
                message='Transport channel has MPO position map rows outside the architecture channel matrix.',
                obj=channel,
                fabric=fabric,
                remediation=(
                    'Remove extra TransportChannelPositionMap rows or update the architecture matrix if they are '
                    'intentional.'
                ),
                related_objects=tuple(_ref(position_map) for position_map in channel_maps),
                details={'extra_endpoint_position_pairs': sorted(extra_pairs)},
            )


def _check_transfer_maps(*, fabric_ids: set[int], findings: list[IntegrityFinding]) -> None:
    if not fabric_ids:
        return

    transfer_maps = (
        TransferMap.objects.filter(fabric_id__in=fabric_ids)
        .select_related(
            'fabric',
            'fabric__architecture',
            'owner_node',
            'owner_segment',
            'owner_segment__a_endpoint',
            'owner_segment__b_endpoint',
            'pattern',
            'pattern__architecture',
            'src_position',
            'src_position__endpoint',
            'dst_position',
            'dst_position__endpoint',
        )
        .order_by('pk')
    )
    for transfer_map in transfer_maps:
        fabric = transfer_map.fabric
        has_owner_node = transfer_map.owner_node_id is not None
        has_owner_segment = transfer_map.owner_segment_id is not None
        if has_owner_node == has_owner_segment:
            _add_finding(
                findings,
                severity='error',
                code='transfer_map_owner_invalid',
                message='Transfer map must have exactly one owner node or owner segment.',
                obj=transfer_map,
                fabric=fabric,
                remediation='Set either owner_node or owner_segment, but not both.',
            )
        if transfer_map.owner_node_id and transfer_map.owner_node.fabric_id != transfer_map.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='transfer_map_owner_fabric_mismatch',
                message='Transfer map owner node belongs to a different fabric.',
                obj=transfer_map,
                fabric=fabric,
                remediation='Move the transfer map to the owner node fabric or select an owner node in this fabric.',
                related_objects=(_ref(transfer_map.owner_node),),
            )
        if transfer_map.owner_segment_id and transfer_map.owner_segment.fabric_id != transfer_map.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='transfer_map_owner_fabric_mismatch',
                message='Transfer map owner segment belongs to a different fabric.',
                obj=transfer_map,
                fabric=fabric,
                remediation=(
                    'Move the transfer map to the owner segment fabric or select an owner segment in this fabric.'
                ),
                related_objects=(_ref(transfer_map.owner_segment),),
            )

        if transfer_map.src_position_id == transfer_map.dst_position_id:
            _add_finding(
                findings,
                severity='error',
                code='transfer_map_same_position',
                message='Transfer map source and destination positions are the same connector position.',
                obj=transfer_map,
                fabric=fabric,
                remediation='Select two distinct connector positions.',
                related_objects=(_ref(transfer_map.src_position),),
            )

        for field_name, position in (
            ('src_position', transfer_map.src_position),
            ('dst_position', transfer_map.dst_position),
        ):
            if position.endpoint.fabric_id != transfer_map.fabric_id:
                _add_finding(
                    findings,
                    severity='error',
                    code='transfer_map_position_fabric_mismatch',
                    message=f'Transfer map {field_name} belongs to a different fabric.',
                    obj=transfer_map,
                    fabric=fabric,
                    remediation='Replace the connector position with one in the transfer map fabric.',
                    related_objects=(_ref(position), _ref(position.endpoint)),
                    details={
                        'field': field_name,
                        'position_fabric_id': position.endpoint.fabric_id,
                        'transfer_map_fabric_id': transfer_map.fabric_id,
                    },
                )

        if transfer_map.owner_node_id:
            mismatched_positions = tuple(
                position
                for position in (transfer_map.src_position, transfer_map.dst_position)
                if position.endpoint.node_id != transfer_map.owner_node_id
            )
            if mismatched_positions:
                _add_finding(
                    findings,
                    severity='error',
                    code='transfer_map_position_owner_mismatch',
                    message='Transfer map owned by a node references positions outside that owner node.',
                    obj=transfer_map,
                    fabric=fabric,
                    remediation='Use connector positions on endpoints owned by the transfer map owner node.',
                    related_objects=tuple(_ref(position) for position in mismatched_positions),
                )
        if transfer_map.owner_segment_id:
            segment_endpoint_ids = {
                transfer_map.owner_segment.a_endpoint_id,
                transfer_map.owner_segment.b_endpoint_id,
            }
            mismatched_positions = tuple(
                position
                for position in (transfer_map.src_position, transfer_map.dst_position)
                if position.endpoint_id not in segment_endpoint_ids
            )
            if mismatched_positions:
                _add_finding(
                    findings,
                    severity='error',
                    code='transfer_map_position_owner_mismatch',
                    message='Transfer map owned by a segment references positions outside that segment endpoint pair.',
                    obj=transfer_map,
                    fabric=fabric,
                    remediation='Use connector positions on the owner segment a_endpoint and b_endpoint.',
                    related_objects=tuple(_ref(position) for position in mismatched_positions),
                )

        if (
            transfer_map.pattern_id
            and transfer_map.pattern.architecture_id
            and transfer_map.fabric.architecture_id
            and transfer_map.pattern.architecture_id != transfer_map.fabric.architecture_id
        ):
            _add_finding(
                findings,
                severity='warning',
                code='transfer_map_pattern_architecture_mismatch',
                message='Transfer map pattern belongs to a different architecture than the fabric.',
                obj=transfer_map,
                fabric=fabric,
                remediation=(
                    'Select a TransferPattern from the fabric architecture or clear the pattern for custom maps.'
                ),
                related_objects=(_ref(transfer_map.pattern),),
            )
        if transfer_map.pattern_id and transfer_map.pattern.pattern_kind != transfer_map.map_kind:
            _add_finding(
                findings,
                severity='warning',
                code='transfer_map_pattern_kind_mismatch',
                message='Transfer map kind differs from its referenced TransferPattern kind.',
                obj=transfer_map,
                fabric=fabric,
                remediation='Align map_kind with the TransferPattern, or select a matching pattern.',
                related_objects=(_ref(transfer_map.pattern),),
                details={
                    'map_kind': transfer_map.map_kind,
                    'pattern_kind': transfer_map.pattern.pattern_kind,
                },
            )


def _channel_index_for_mpo_position(
    *,
    entries: tuple[ChannelMapEntry, ...],
    mpo_index: int,
    position_number: int,
) -> int | None:
    for entry in entries:
        if entry.mpo_index == mpo_index and position_number in entry.positions:
            return entry.channel_index
    return None


def _check_optical_lanes(
    *,
    fabrics: tuple[Fabric, ...],
    fabric_ids: set[int],
    findings: list[IntegrityFinding],
) -> None:
    if not fabric_ids:
        return

    fabrics_by_id = {fabric.pk: fabric for fabric in fabrics}
    channel_entries_by_fabric = {
        fabric.pk: _normalized_channel_map_entries(fabric)
        for fabric in fabrics
    }
    channel_position_ids = set(
        TransportChannelPositionMap.objects.filter(channel__fabric_id__in=fabric_ids)
        .values_list('channel_id', 'mpo_position_id')
    )

    lanes = (
        OpticalLane.objects.filter(fabric_id__in=fabric_ids)
        .select_related(
            'fabric',
            'endpoint',
            'channel',
            'channel__plane',
            'channel__endpoint',
            'plane',
            'local_mpo_endpoint',
            'local_mpo_position',
            'local_mpo_position__endpoint',
        )
        .order_by('pk')
    )
    for lane in lanes:
        fabric = fabrics_by_id.get(lane.fabric_id, lane.fabric)

        if lane.endpoint.fabric_id != lane.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_endpoint_fabric_mismatch',
                message='Optical lane endpoint belongs to a different fabric than the lane.',
                obj=lane,
                fabric=fabric,
                remediation='Move the lane to the endpoint fabric or select an endpoint in the lane fabric.',
                related_objects=(_ref(lane.endpoint),),
            )
        if lane.local_mpo_endpoint.fabric_id != lane.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_anchor_fabric_mismatch',
                message='Optical lane local MPO endpoint belongs to a different fabric than the lane.',
                obj=lane,
                fabric=fabric,
                remediation='Select a local MPO endpoint in the lane fabric.',
                related_objects=(_ref(lane.local_mpo_endpoint),),
            )
        if lane.local_mpo_position.endpoint_id != lane.local_mpo_endpoint_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_anchor_position_mismatch',
                message='Optical lane local MPO position does not belong to the declared local MPO endpoint.',
                obj=lane,
                fabric=fabric,
                remediation=(
                    'Set local_mpo_endpoint to the connector owning local_mpo_position, or select a position on the '
                    'declared endpoint.'
                ),
                related_objects=(_ref(lane.local_mpo_endpoint), _ref(lane.local_mpo_position)),
            )
        if lane.local_mpo_endpoint.parent_id and lane.local_mpo_endpoint.parent_id != lane.endpoint_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_anchor_parent_mismatch',
                message='Optical lane local MPO endpoint parent does not match the lane endpoint.',
                obj=lane,
                fabric=fabric,
                remediation='Anchor the lane on an MPO child of the lane endpoint.',
                related_objects=(_ref(lane.endpoint), _ref(lane.local_mpo_endpoint)),
            )
        if lane.plane_id and lane.plane.fabric_id != lane.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_plane_fabric_mismatch',
                message='Optical lane plane belongs to a different fabric than the lane.',
                obj=lane,
                fabric=fabric,
                remediation='Select a plane in the lane fabric, or clear plane if the lane is intentionally unscoped.',
                related_objects=(_ref(lane.plane),),
            )

        declared_mpo_index = _endpoint_declared_mpo_index(lane.local_mpo_endpoint)
        if declared_mpo_index is not None and declared_mpo_index != lane.local_mpo_index:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_local_mpo_index_mismatch',
                message='Optical lane local_mpo_index does not match the local MPO endpoint index.',
                obj=lane,
                fabric=fabric,
                remediation='Correct local_mpo_index or the local MPO endpoint metadata.',
                related_objects=(_ref(lane.local_mpo_endpoint),),
                details={
                    'local_mpo_index': lane.local_mpo_index,
                    'endpoint_mpo_index': declared_mpo_index,
                },
            )

        if lane.channel_id is None:
            _add_finding(
                findings,
                severity='warning',
                code='optical_lane_missing_channel',
                message='Optical lane has no transport channel reference.',
                obj=lane,
                fabric=fabric,
                remediation=(
                    'Assign a TransportChannel when the lane is intended to participate in channel-scoped path audits.'
                ),
            )
            continue

        if lane.channel.fabric_id != lane.fabric_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_channel_fabric_mismatch',
                message='Optical lane channel belongs to a different fabric than the lane.',
                obj=lane,
                fabric=fabric,
                remediation='Select a TransportChannel in the lane fabric.',
                related_objects=(_ref(lane.channel),),
            )
        if lane.channel.endpoint_id != lane.endpoint_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_channel_endpoint_mismatch',
                message='Optical lane channel belongs to a different endpoint than the lane.',
                obj=lane,
                fabric=fabric,
                remediation='Select a TransportChannel whose endpoint matches the lane endpoint.',
                related_objects=(_ref(lane.channel), _ref(lane.endpoint)),
            )
        if lane.plane_id and lane.channel.plane_id and lane.channel.plane_id != lane.plane_id:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_channel_plane_mismatch',
                message='Optical lane plane differs from the transport channel plane.',
                obj=lane,
                fabric=fabric,
                remediation='Align the lane plane with its channel plane, or clear one side if intentionally unscoped.',
                related_objects=(_ref(lane.channel), _ref(lane.plane)),
                details={
                    'lane_plane_id': lane.plane_id,
                    'channel_plane_id': lane.channel.plane_id,
                },
            )
        if (lane.channel_id, lane.local_mpo_position_id) not in channel_position_ids:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_channel_position_missing',
                message='Optical lane local MPO position is not mapped by its transport channel.',
                obj=lane,
                fabric=fabric,
                remediation=(
                    'Add the missing TransportChannelPositionMap row or move the lane to a position mapped by its '
                    'channel.'
                ),
                related_objects=(_ref(lane.channel), _ref(lane.local_mpo_position)),
            )

        entries = channel_entries_by_fabric.get(lane.fabric_id, ())
        expected_channel_index = _channel_index_for_mpo_position(
            entries=entries,
            mpo_index=lane.local_mpo_index,
            position_number=lane.local_mpo_position.position_number,
        )
        if expected_channel_index is not None and expected_channel_index != lane.channel.channel_index:
            _add_finding(
                findings,
                severity='error',
                code='optical_lane_channel_index_mismatch',
                message='Optical lane local MPO position belongs to a different channel in the architecture matrix.',
                obj=lane,
                fabric=fabric,
                remediation='Move the lane to the expected transport channel for its MPO index and position.',
                related_objects=(_ref(lane.channel), _ref(lane.local_mpo_position)),
                details={
                    'expected_channel_index': expected_channel_index,
                    'actual_channel_index': lane.channel.channel_index,
                    'local_mpo_index': lane.local_mpo_index,
                    'position_number': lane.local_mpo_position.position_number,
                },
            )
