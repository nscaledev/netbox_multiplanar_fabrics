from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.urls import NoReverseMatch
from django.utils import timezone
from dcim.models import Device, Interface

from netbox_plant_graph.models import (
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FiberStrand,
    OperationRun,
    OpticalLane,
    StrandTermination,
    TransceiverConnector,
    TransportChannel,
)
from netbox_plant_graph.services.resolver import _neighbors, build_path_resolver_matrix


SCENARIO_CABLE_ASSEMBLY_CUT = 'cable_assembly_cut'
SCENARIO_MPO_CONNECTOR_UNPLUG = 'mpo_connector_unplug'
SCENARIO_OSFP_TRANSCEIVER_UNSEAT = 'osfp_transceiver_unseat'

IMPACT_FAILED = 'failed'
IMPACT_DEGRADED = 'degraded'
IMPACT_AT_RISK = 'at_risk'
IMPACT_LOW = 'low'

IMPACT_RANK = {
    IMPACT_FAILED: 4,
    IMPACT_DEGRADED: 3,
    IMPACT_AT_RISK: 2,
    IMPACT_LOW: 1,
}

IMPACT_LABELS = {
    IMPACT_FAILED: 'FAILED',
    IMPACT_DEGRADED: 'DEGRADED',
    IMPACT_AT_RISK: 'AT_RISK',
    IMPACT_LOW: 'LOW',
}

OPERATIONAL_IMPACT_REPORT_SCHEMA = 'v2.operational_impact.report/1'
OPERATIONAL_IMPACT_COMPARISON_SCHEMA = 'v2.operational_impact.comparison/1'
OPERATIONAL_IMPACT_OPERATION_PROFILE = 'operational_impact'
OPERATIONAL_IMPACT_OPERATION_KIND = 'operational_impact_report'

_IMPACT_COMPARISON_COLLECTIONS = (
    'simulated_components',
    'impacted_paths',
    'impacted_lanes',
    'impacted_channels',
    'impacted_endpoints',
    'impacted_devices',
)

_SUMMARY_COUNT_FIELDS = (
    'simulated_component_count',
    'impacted_path_count',
    'impacted_lane_count',
    'impacted_channel_count',
    'impacted_endpoint_count',
    'impacted_device_count',
)

_SUMMARY_SEVERITY_FIELDS = ('paths_by_severity', 'objects_by_severity')


@dataclass(frozen=True)
class ImpactObjectRef:
    model: str
    object_id: int | None
    label: str
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'model': self.model,
            'id': self.object_id,
            'label': self.label,
            'url': self.url,
        }


@dataclass(frozen=True)
class ImpactPathStep:
    index: int
    step_type: str
    object: ImpactObjectRef
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            'index': self.index,
            'step_type': self.step_type,
            'object': self.object.as_dict(),
            'metadata': self.metadata,
        }


@dataclass(frozen=True)
class ImpactPath:
    path_id: str
    severity: str
    reason_code: str
    source_lane: ImpactObjectRef
    destination_lane: ImpactObjectRef | None
    source_lane_id: int
    destination_lane_id: int | None
    source_channel_id: int | None
    destination_channel_id: int | None
    path_found: bool
    error: str
    matched_components: tuple[ImpactObjectRef, ...]
    steps: tuple[ImpactPathStep, ...]
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        return IMPACT_LABELS[self.severity]

    def as_dict(self) -> dict[str, Any]:
        return {
            'path_id': self.path_id,
            'severity': self.severity,
            'severity_label': self.severity_label,
            'reason_code': self.reason_code,
            'source_lane': self.source_lane.as_dict(),
            'destination_lane': self.destination_lane.as_dict() if self.destination_lane else None,
            'source_lane_id': self.source_lane_id,
            'destination_lane_id': self.destination_lane_id,
            'source_channel_id': self.source_channel_id,
            'destination_channel_id': self.destination_channel_id,
            'path_found': self.path_found,
            'error': self.error,
            'matched_components': [component.as_dict() for component in self.matched_components],
            'steps': [step.as_dict() for step in self.steps],
            'details': self.details,
        }


@dataclass(frozen=True)
class ImpactEntry:
    kind: str
    object: ImpactObjectRef
    severity: str
    reason_code: str
    message: str
    remediation: str = ''
    fabric_id: int | None = None
    fabric_slug: str = ''
    lane_ids: tuple[int, ...] = ()
    channel_ids: tuple[int, ...] = ()
    endpoint_ids: tuple[int, ...] = ()
    path_ids: tuple[str, ...] = ()
    related_objects: tuple[ImpactObjectRef, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        return IMPACT_LABELS[self.severity]

    def as_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'object': self.object.as_dict(),
            'severity': self.severity,
            'severity_label': self.severity_label,
            'reason_code': self.reason_code,
            'message': self.message,
            'remediation': self.remediation,
            'fabric': {
                'id': self.fabric_id,
                'slug': self.fabric_slug,
            },
            'lane_ids': list(self.lane_ids),
            'channel_ids': list(self.channel_ids),
            'endpoint_ids': list(self.endpoint_ids),
            'path_ids': list(self.path_ids),
            'related_objects': [related.as_dict() for related in self.related_objects],
            'details': self.details,
        }


@dataclass(frozen=True)
class ImpactHierarchyNode:
    kind: str
    object: ImpactObjectRef
    severity: str
    lane_ids: tuple[int, ...] = ()
    channel_ids: tuple[int, ...] = ()
    endpoint_ids: tuple[int, ...] = ()
    path_ids: tuple[str, ...] = ()
    children: tuple['ImpactHierarchyNode', ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        return IMPACT_LABELS[self.severity]

    def as_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'object': self.object.as_dict(),
            'severity': self.severity,
            'severity_label': self.severity_label,
            'lane_ids': list(self.lane_ids),
            'channel_ids': list(self.channel_ids),
            'endpoint_ids': list(self.endpoint_ids),
            'path_ids': list(self.path_ids),
            'details': self.details,
            'children': [child.as_dict() for child in self.children],
        }


@dataclass(frozen=True)
class ImpactScenario:
    scenario_type: str
    label: str
    target_objects: tuple[ImpactObjectRef, ...]
    selected_fabric: ImpactObjectRef | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            'scenario_type': self.scenario_type,
            'label': self.label,
            'target_objects': [target.as_dict() for target in self.target_objects],
            'selected_fabric': self.selected_fabric.as_dict() if self.selected_fabric else None,
            'details': self.details,
        }


@dataclass(frozen=True)
class OperationalImpactReport:
    scenario: ImpactScenario
    scope: dict[str, Any]
    simulated_components: tuple[ImpactEntry, ...]
    impacted_paths: tuple[ImpactPath, ...]
    impacted_lanes: tuple[ImpactEntry, ...]
    impacted_channels: tuple[ImpactEntry, ...]
    impacted_endpoints: tuple[ImpactEntry, ...]
    impacted_devices: tuple[ImpactEntry, ...]
    hierarchy: tuple[ImpactHierarchyNode, ...]

    @property
    def summary(self) -> dict[str, Any]:
        path_counts = Counter(path.severity for path in self.impacted_paths)
        object_counts = Counter()
        for collection in (
            self.simulated_components,
            self.impacted_lanes,
            self.impacted_channels,
            self.impacted_endpoints,
            self.impacted_devices,
        ):
            object_counts.update(entry.severity for entry in collection)
        return {
            'simulated_component_count': len(self.simulated_components),
            'impacted_path_count': len(self.impacted_paths),
            'impacted_lane_count': len(self.impacted_lanes),
            'impacted_channel_count': len(self.impacted_channels),
            'impacted_endpoint_count': len(self.impacted_endpoints),
            'impacted_device_count': len(self.impacted_devices),
            'paths_by_severity': _severity_counts(path_counts),
            'objects_by_severity': _severity_counts(object_counts),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            'scenario': self.scenario.as_dict(),
            'scope': self.scope,
            'summary': self.summary,
            'simulated_components': [component.as_dict() for component in self.simulated_components],
            'impacted_paths': [path.as_dict() for path in self.impacted_paths],
            'impacted_lanes': [lane.as_dict() for lane in self.impacted_lanes],
            'impacted_channels': [channel.as_dict() for channel in self.impacted_channels],
            'impacted_endpoints': [endpoint.as_dict() for endpoint in self.impacted_endpoints],
            'impacted_devices': [device.as_dict() for device in self.impacted_devices],
            'hierarchy': [node.as_dict() for node in self.hierarchy],
        }


@dataclass(frozen=True)
class OperationalImpactComparison:
    report_summaries: tuple[dict[str, Any], ...]
    common_impacts: tuple[dict[str, Any], ...]
    scenario_specific_impacts: tuple[dict[str, Any], ...]
    severity_deltas: tuple[dict[str, Any], ...]
    device_deltas: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            'comparison_schema': OPERATIONAL_IMPACT_COMPARISON_SCHEMA,
            'report_count': len(self.report_summaries),
            'reports': list(self.report_summaries),
            'common_impacts': list(self.common_impacts),
            'scenario_specific_impacts': list(self.scenario_specific_impacts),
            'severity_deltas': list(self.severity_deltas),
            'device_deltas': list(self.device_deltas),
        }


@dataclass(frozen=True)
class _ScenarioComponents:
    scenario: ImpactScenario
    unavailable_positions: frozenset[int]
    unavailable_strands: frozenset[int]
    direct_endpoint_ids: frozenset[int]
    direct_interface_ids: frozenset[int]
    direct_channel_ids: frozenset[int]
    simulated_components: tuple[ImpactEntry, ...]
    fabric_ids: frozenset[int]


def persist_operational_impact_report(
    report: OperationalImpactReport,
    *,
    report_name: str = '',
    operation_run: OperationRun | int | None = None,
    actor=None,
    parameters: dict[str, Any] | None = None,
) -> OperationRun:
    report_payload = report.as_dict()
    summary = report.summary
    scenario = report_payload['scenario']
    report_hash = _stable_report_hash(report_payload)
    now = timezone.now()
    fabric = _fabric_from_impact_report_scope(report_payload)
    target_objects = scenario.get('target_objects') or ()
    run_parameters = {
        'operation_kind': OPERATIONAL_IMPACT_OPERATION_KIND,
        'report_name': report_name,
        'scenario_type': scenario.get('scenario_type', ''),
        'target_objects': target_objects,
        'scope': report_payload.get('scope', {}),
        **(parameters or {}),
    }
    result = {
        'operation_kind': OPERATIONAL_IMPACT_OPERATION_KIND,
        'report_schema': OPERATIONAL_IMPACT_REPORT_SCHEMA,
        'report_name': report_name,
        'scenario_type': scenario.get('scenario_type', ''),
        'target_objects': target_objects,
        'summary': summary,
        'scope': report_payload.get('scope', {}),
        'report_hash': report_hash,
        'persisted_at': now.isoformat(),
        'report': report_payload,
    }
    metadata = {
        'operation_kind': OPERATIONAL_IMPACT_OPERATION_KIND,
        'report_schema': OPERATIONAL_IMPACT_REPORT_SCHEMA,
        'report_hash': report_hash,
        'report_name': report_name,
        'scenario_type': scenario.get('scenario_type', ''),
        'target_object_count': len(target_objects),
        'impacted_device_count': summary['impacted_device_count'],
        'impacted_path_count': summary['impacted_path_count'],
    }

    run = _normalize_operation_run(operation_run)
    if run is None:
        return OperationRun.objects.create(
            profile=OPERATIONAL_IMPACT_OPERATION_PROFILE,
            status='completed',
            fabric=fabric,
            initiated_by=actor,
            parameters=run_parameters,
            result=result,
            metadata=metadata,
            started_at=now,
            completed_at=now,
        )

    run.profile = OPERATIONAL_IMPACT_OPERATION_PROFILE
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


def compare_operational_impact_reports(*reports) -> OperationalImpactComparison:
    if len(reports) == 1 and isinstance(reports[0], (list, tuple)):
        reports = tuple(reports[0])
    if len(reports) < 2:
        raise ValueError('At least two operational impact reports are required for comparison.')

    payloads = tuple(_impact_report_payload(report) for report in reports)
    report_summaries = tuple(
        _comparison_report_summary(index, payload)
        for index, payload in enumerate(payloads)
    )
    impact_maps = tuple(_comparison_impact_map(payload) for payload in payloads)
    report_indexes_by_key = defaultdict(set)
    for report_index, impact_map in enumerate(impact_maps):
        for key in impact_map:
            report_indexes_by_key[key].add(report_index)

    report_count = len(payloads)
    common_keys = {
        key
        for key, indexes in report_indexes_by_key.items()
        if len(indexes) == report_count
    }
    scenario_specific_keys = {
        key
        for key, indexes in report_indexes_by_key.items()
        if len(indexes) == 1
    }

    common_impacts = tuple(
        _comparison_group_payload(key, report_indexes_by_key[key], impact_maps, report_summaries)
        for key in sorted(common_keys, key=lambda item: _comparison_sort_key(item, impact_maps))
    )
    scenario_specific_impacts = tuple(
        {
            'report_index': index,
            'report_label': report_summaries[index]['label'],
            'impact_count': len(specific_impacts),
            'impacts': specific_impacts,
        }
        for index, specific_impacts in (
            (
                index,
                [
                    _comparison_group_payload(key, {index}, impact_maps, report_summaries)
                    for key in sorted(
                        (
                            key
                            for key in scenario_specific_keys
                            if key in impact_maps[index]
                        ),
                        key=lambda item: _comparison_sort_key(item, impact_maps),
                    )
                ],
            )
            for index in range(report_count)
        )
    )
    severity_deltas = tuple(
        _severity_delta_payload(
            base_payload=payloads[0],
            compare_payload=payloads[index],
            base_impacts=impact_maps[0],
            compare_impacts=impact_maps[index],
            base_summary=report_summaries[0],
            compare_summary=report_summaries[index],
        )
        for index in range(1, report_count)
    )
    device_deltas = tuple(
        _device_delta_payload(
            base_payload=payloads[0],
            compare_payload=payloads[index],
            base_summary=report_summaries[0],
            compare_summary=report_summaries[index],
        )
        for index in range(1, report_count)
    )

    return OperationalImpactComparison(
        report_summaries=report_summaries,
        common_impacts=common_impacts,
        scenario_specific_impacts=scenario_specific_impacts,
        severity_deltas=severity_deltas,
        device_deltas=device_deltas,
    )


def model_operational_impact(
    *,
    scenario_type: str,
    cable_assembly: CableAssembly | None = None,
    cable_assemblies=(),
    connector_endpoint: Endpoint | None = None,
    connector_endpoints=(),
    interface: Interface | None = None,
    interfaces=(),
    selected_fabric: Fabric | int | None = None,
    max_depth: int = 64,
    actor=None,
) -> OperationalImpactReport:
    if scenario_type == SCENARIO_CABLE_ASSEMBLY_CUT:
        return model_cable_assembly_cut_impact(
            cable_assembly=cable_assembly,
            cable_assemblies=cable_assemblies,
            selected_fabric=selected_fabric,
            max_depth=max_depth,
            actor=actor,
        )
    if scenario_type == SCENARIO_MPO_CONNECTOR_UNPLUG:
        return model_mpo_connector_unplug_impact(
            connector_endpoint=connector_endpoint,
            connector_endpoints=connector_endpoints,
            selected_fabric=selected_fabric,
            max_depth=max_depth,
            actor=actor,
        )
    if scenario_type == SCENARIO_OSFP_TRANSCEIVER_UNSEAT:
        return model_osfp_transceiver_unseat_impact(
            interface=interface,
            interfaces=interfaces,
            selected_fabric=selected_fabric,
            max_depth=max_depth,
            actor=actor,
        )
    raise ValueError(f'Unsupported operational impact scenario type: {scenario_type}')


def model_cable_assembly_cut_impact(
    *,
    cable_assembly: CableAssembly | None = None,
    cable_assemblies=(),
    selected_fabric: Fabric | int | None = None,
    max_depth: int = 64,
    actor=None,
) -> OperationalImpactReport:
    fabric = _normalize_fabric(selected_fabric)
    selected_cables = _normalize_many(cable_assemblies, cable_assembly)
    if not selected_cables:
        raise ValueError('At least one cable assembly is required for cable assembly cut impact modeling.')
    components = _components_for_cable_assembly_cut(selected_cables, selected_fabric=fabric)
    return _build_impact_report(components=components, max_depth=max_depth, actor=actor)


def model_mpo_connector_unplug_impact(
    *,
    connector_endpoint: Endpoint | None = None,
    connector_endpoints=(),
    selected_fabric: Fabric | int | None = None,
    max_depth: int = 64,
    actor=None,
) -> OperationalImpactReport:
    fabric = _normalize_fabric(selected_fabric)
    endpoints = _normalize_many(connector_endpoints, connector_endpoint)
    if fabric is not None:
        endpoints = tuple(endpoint for endpoint in endpoints if endpoint.fabric_id == fabric.pk)
    if not endpoints:
        raise ValueError('At least one MPO connector endpoint is required for connector unplug impact modeling.')
    components = _components_for_mpo_connector_unplug(endpoints, selected_fabric=fabric)
    return _build_impact_report(components=components, max_depth=max_depth, actor=actor)


def model_osfp_transceiver_unseat_impact(
    *,
    interface: Interface | None = None,
    interfaces=(),
    selected_fabric: Fabric | int | None = None,
    max_depth: int = 64,
    actor=None,
) -> OperationalImpactReport:
    fabric = _normalize_fabric(selected_fabric)
    selected_interfaces = _normalize_many(interfaces, interface)
    if not selected_interfaces:
        raise ValueError('At least one Interface is required for OSFP transceiver unseat impact modeling.')
    components = _components_for_osfp_transceiver_unseat(selected_interfaces, selected_fabric=fabric)
    return _build_impact_report(components=components, max_depth=max_depth, actor=actor)


def _components_for_cable_assembly_cut(
    cable_assemblies: tuple[CableAssembly, ...],
    *,
    selected_fabric: Fabric | None,
) -> _ScenarioComponents:
    strands = []
    for cable in cable_assemblies:
        queryset = FiberStrand.objects.filter(
            cable_site_id=cable.site_id,
            cable_id=cable.cable_id,
        ).select_related('segment', 'segment__fabric')
        if selected_fabric is not None:
            queryset = queryset.filter(segment__fabric=selected_fabric)
        strands.extend(queryset.order_by('segment__fabric_id', 'segment__name', 'strand_index', 'pk'))

    strand_ids = {strand.pk for strand in strands}
    position_ids, endpoint_ids = _termination_position_and_endpoint_ids(strand_ids)
    direct_channel_ids = _channel_ids_for_positions(position_ids)
    fabric_ids = {strand.segment.fabric_id for strand in strands}
    if selected_fabric is not None:
        fabric_ids.add(selected_fabric.pk)

    components = [
        _component_entry(
            obj=cable,
            kind='cable_assembly',
            severity=IMPACT_FAILED,
            reason_code='scenario_target',
            message='Cable assembly is selected for simulated cut/disconnect.',
            remediation='Restore, replace, or re-seat the cable assembly before clearing the impact.',
        )
        for cable in cable_assemblies
    ]
    components.extend(
        _component_entry(
            obj=strand,
            kind='fiber_strand',
            severity=IMPACT_FAILED,
            reason_code='cable_assembly_member_strand',
            message='Fiber strand belongs to a simulated failed cable assembly.',
            remediation='Repair or replace the failed cable assembly member strand.',
        )
        for strand in strands
    )

    selected_fabric_ref = _ref(selected_fabric) if selected_fabric is not None else None
    return _ScenarioComponents(
        scenario=ImpactScenario(
            scenario_type=SCENARIO_CABLE_ASSEMBLY_CUT,
            label='Cable Assembly Cut/Disconnect',
            target_objects=tuple(_ref(cable) for cable in cable_assemblies),
            selected_fabric=selected_fabric_ref,
            details={'cable_assembly_ids': [cable.pk for cable in cable_assemblies]},
        ),
        unavailable_positions=frozenset(position_ids),
        unavailable_strands=frozenset(strand_ids),
        direct_endpoint_ids=frozenset(endpoint_ids),
        direct_interface_ids=frozenset(),
        direct_channel_ids=frozenset(direct_channel_ids),
        simulated_components=tuple(components),
        fabric_ids=frozenset(fabric_ids),
    )


def _components_for_mpo_connector_unplug(
    endpoints: tuple[Endpoint, ...],
    *,
    selected_fabric: Fabric | None,
) -> _ScenarioComponents:
    endpoint_ids = {endpoint.pk for endpoint in endpoints}
    positions = tuple(
        ConnectorPosition.objects.filter(endpoint_id__in=endpoint_ids)
        .select_related('endpoint', 'endpoint__fabric')
        .order_by('endpoint__address', 'position_number', 'pk')
    )
    position_ids = {position.pk for position in positions}
    direct_channel_ids = _channel_ids_for_positions(position_ids)
    fabric_ids = {endpoint.fabric_id for endpoint in endpoints}
    if selected_fabric is not None:
        fabric_ids.add(selected_fabric.pk)

    components = [
        _component_entry(
            obj=endpoint,
            kind='endpoint',
            severity=IMPACT_FAILED,
            reason_code='scenario_target',
            message='MPO connector endpoint is selected for simulated unplug.',
            remediation='Re-seat the MPO connector and validate all mapped transport channels.',
            endpoint_ids=(endpoint.pk,),
        )
        for endpoint in endpoints
    ]
    components.extend(
        _component_entry(
            obj=position,
            kind='connector_position',
            severity=IMPACT_FAILED,
            reason_code='connector_unplug_position',
            message='Connector position is unavailable because its MPO connector is unplugged.',
            remediation='Re-seat the MPO connector and inspect this position if link light does not recover.',
            endpoint_ids=(position.endpoint_id,),
        )
        for position in positions
    )

    selected_fabric_ref = _ref(selected_fabric) if selected_fabric is not None else None
    return _ScenarioComponents(
        scenario=ImpactScenario(
            scenario_type=SCENARIO_MPO_CONNECTOR_UNPLUG,
            label='MPO Connector Unplug',
            target_objects=tuple(_ref(endpoint) for endpoint in endpoints),
            selected_fabric=selected_fabric_ref,
            details={'endpoint_ids': sorted(endpoint_ids)},
        ),
        unavailable_positions=frozenset(position_ids),
        unavailable_strands=frozenset(),
        direct_endpoint_ids=frozenset(endpoint_ids),
        direct_interface_ids=frozenset(),
        direct_channel_ids=frozenset(direct_channel_ids),
        simulated_components=tuple(components),
        fabric_ids=frozenset(fabric_ids),
    )


def _components_for_osfp_transceiver_unseat(
    interfaces: tuple[Interface, ...],
    *,
    selected_fabric: Fabric | None,
) -> _ScenarioComponents:
    endpoints = _endpoints_for_interfaces(interfaces)
    if selected_fabric is not None:
        endpoints = tuple(endpoint for endpoint in endpoints if endpoint.fabric_id == selected_fabric.pk)
    endpoint_ids = {endpoint.pk for endpoint in endpoints}
    positions = tuple(
        ConnectorPosition.objects.filter(endpoint_id__in=endpoint_ids)
        .select_related('endpoint', 'endpoint__fabric')
        .order_by('endpoint__address', 'position_number', 'pk')
    )
    position_ids = {position.pk for position in positions}
    direct_channel_ids = set(
        TransportChannel.objects.filter(endpoint_id__in=endpoint_ids).values_list('pk', flat=True)
    )
    direct_channel_ids.update(_channel_ids_for_positions(position_ids))
    fabric_ids = {endpoint.fabric_id for endpoint in endpoints}
    if selected_fabric is not None:
        fabric_ids.add(selected_fabric.pk)
    transceiver_connectors = tuple(
        TransceiverConnector.objects.filter(endpoint_id__in=endpoint_ids)
        .select_related('module', 'connector_profile', 'endpoint')
        .order_by('module', 'connector_profile__connector_index', 'pk')
    )

    components = [
        _component_entry(
            obj=interface,
            kind='interface',
            severity=IMPACT_FAILED,
            reason_code='scenario_target',
            message='OSFP/transceiver interface is selected for simulated unseat.',
            remediation='Re-seat or replace the transceiver and validate child MPO/channel recovery.',
        )
        for interface in interfaces
    ]
    components.extend(
        _component_entry(
            obj=endpoint,
            kind='endpoint',
            severity=IMPACT_FAILED,
            reason_code='transceiver_endpoint_unavailable',
            message='Plugin endpoint is unavailable because its OSFP/transceiver is unseated.',
            remediation='Re-seat the transceiver and validate endpoint/channel state.',
            endpoint_ids=(endpoint.pk,),
        )
        for endpoint in endpoints
    )
    components.extend(
        _component_entry(
            obj=connector,
            kind='transceiver_connector',
            severity=IMPACT_FAILED,
            reason_code='transceiver_connector_unavailable',
            message='Installed transceiver connector face is unavailable because the transceiver is unseated.',
            remediation='Re-seat or replace the transceiver, then validate this connector face and attached fiber positions.',
            endpoint_ids=(connector.endpoint_id,),
        )
        for connector in transceiver_connectors
    )

    selected_fabric_ref = _ref(selected_fabric) if selected_fabric is not None else None
    return _ScenarioComponents(
        scenario=ImpactScenario(
            scenario_type=SCENARIO_OSFP_TRANSCEIVER_UNSEAT,
            label='OSFP/Transceiver Unseat',
            target_objects=tuple(_ref(interface) for interface in interfaces),
            selected_fabric=selected_fabric_ref,
            details={
                'interface_ids': [interface.pk for interface in interfaces],
                'endpoint_ids': sorted(endpoint_ids),
                'transceiver_connector_ids': [connector.pk for connector in transceiver_connectors],
            },
        ),
        unavailable_positions=frozenset(position_ids),
        unavailable_strands=frozenset(),
        direct_endpoint_ids=frozenset(endpoint_ids),
        direct_interface_ids=frozenset(interface.pk for interface in interfaces),
        direct_channel_ids=frozenset(direct_channel_ids),
        simulated_components=tuple(components),
        fabric_ids=frozenset(fabric_ids),
    )


def _build_impact_report(
    *,
    components: _ScenarioComponents,
    max_depth: int,
    actor,
) -> OperationalImpactReport:
    impacted_paths = _impacted_paths_for_components(
        components=components,
        max_depth=max_depth,
        actor=actor,
    )
    lanes = _lanes_for_paths(impacted_paths)
    lane_entries = _lane_impacts(components=components, paths=impacted_paths, lanes=lanes)
    channel_entries = _channel_impacts(components=components, paths=impacted_paths, lane_entries=lane_entries)
    endpoint_entries = _endpoint_impacts(
        components=components,
        paths=impacted_paths,
        lane_entries=lane_entries,
        channel_entries=channel_entries,
    )
    device_entries = _device_impacts(components=components, endpoint_entries=endpoint_entries)
    hierarchy = _impact_hierarchy(
        lane_entries=lane_entries,
        channel_entries=channel_entries,
        endpoint_entries=endpoint_entries,
        device_entries=device_entries,
    )
    return OperationalImpactReport(
        scenario=components.scenario,
        scope=_scope_payload(components.fabric_ids),
        simulated_components=components.simulated_components,
        impacted_paths=impacted_paths,
        impacted_lanes=lane_entries,
        impacted_channels=channel_entries,
        impacted_endpoints=endpoint_entries,
        impacted_devices=device_entries,
        hierarchy=hierarchy,
    )


def _impacted_paths_for_components(
    *,
    components: _ScenarioComponents,
    max_depth: int,
    actor,
) -> tuple[ImpactPath, ...]:
    if not components.fabric_ids:
        return ()

    connector_position_ids = set(components.unavailable_positions)
    fiber_strand_ids = set(components.unavailable_strands)
    source_lanes = []
    for fabric_id in sorted(components.fabric_ids):
        source_lanes.extend(
            _candidate_source_lanes_for_failure(
                fabric_id=fabric_id,
                connector_position_ids=connector_position_ids,
                fiber_strand_ids=fiber_strand_ids,
            )
        )
    if not source_lanes:
        return ()

    rows = build_path_resolver_matrix(
        source_lanes=source_lanes,
        max_depth=max_depth,
        actor=actor,
    )
    source_lane_by_id = {lane.pk: lane for lane in source_lanes}
    destination_lane_ids = {
        row.destination_lane['lane_id']
        for row in rows
        if row.destination_lane and row.destination_lane.get('lane_id')
    }
    destination_lane_by_id = {
        lane.pk: lane
        for lane in OpticalLane.objects.filter(pk__in=destination_lane_ids)
        .select_related('endpoint', 'channel', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
    }
    matched_refs = _matched_component_refs(
        connector_position_ids=connector_position_ids,
        fiber_strand_ids=fiber_strand_ids,
    )

    paths = []
    seen_path_ids = set()
    for row in rows:
        matched_keys = _matched_component_keys(
            path=row.resolved_path,
            connector_position_ids=connector_position_ids,
            fiber_strand_ids=fiber_strand_ids,
        )
        if not matched_keys:
            continue

        source_lane_id = row.source_lane['lane_id']
        destination_lane_id = row.destination_lane.get('lane_id') if row.destination_lane else None
        source_lane = source_lane_by_id.get(source_lane_id)
        destination_lane = destination_lane_by_id.get(destination_lane_id)
        if source_lane is None:
            continue
        path_id = f'path:{source_lane_id}:{destination_lane_id or "unresolved"}'
        if path_id in seen_path_ids:
            continue
        seen_path_ids.add(path_id)

        source_direct = _lane_directly_failed(source_lane, components)
        destination_direct = destination_lane is not None and _lane_directly_failed(destination_lane, components)
        severity = IMPACT_FAILED if source_direct else IMPACT_DEGRADED
        reason_code = 'direct_physical_failure' if source_direct else 'path_traverses_failed_component'
        paths.append(
            ImpactPath(
                path_id=path_id,
                severity=severity,
                reason_code=reason_code,
                source_lane=_ref(source_lane),
                destination_lane=_ref(destination_lane) if destination_lane is not None else None,
                source_lane_id=source_lane_id,
                destination_lane_id=destination_lane_id,
                source_channel_id=source_lane.channel_id,
                destination_channel_id=getattr(destination_lane, 'channel_id', None),
                path_found=row.path_found,
                error=row.error,
                matched_components=tuple(
                    matched_refs[key]
                    for key in sorted(matched_keys)
                    if key in matched_refs
                ),
                steps=_path_steps(row.resolved_path.steps),
                details={
                    'source_lane_directly_failed': source_direct,
                    'destination_lane_directly_failed': destination_direct,
                    'matched_component_keys': [list(key) for key in sorted(matched_keys)],
                },
            )
        )

    paths.sort(key=lambda path: (-IMPACT_RANK[path.severity], path.path_id))
    return tuple(paths)


def _candidate_source_lanes_for_failure(
    *,
    fabric_id: int,
    connector_position_ids: set[int],
    fiber_strand_ids: set[int],
):
    seed_position_ids = set(connector_position_ids)
    if fiber_strand_ids:
        seed_position_ids.update(
            StrandTermination.objects.filter(strand_id__in=fiber_strand_ids)
            .values_list('mpo_position_id', flat=True)
        )
    if not seed_position_ids:
        return ()

    positions_by_id = {
        position.pk: position
        for position in ConnectorPosition.objects.filter(
            endpoint__fabric_id=fabric_id,
        ).select_related('endpoint')
    }
    visited_position_ids = set()
    frontier = [
        positions_by_id[position_id]
        for position_id in seed_position_ids
        if position_id in positions_by_id
    ]
    while frontier:
        position = frontier.pop()
        if position.pk in visited_position_ids:
            continue
        visited_position_ids.add(position.pk)
        for neighbor, _step in _neighbors(position, fabric_id=fabric_id):
            if neighbor.pk in visited_position_ids:
                continue
            frontier.append(positions_by_id.get(neighbor.pk) or neighbor)

    return tuple(
        OpticalLane.objects.filter(
            fabric_id=fabric_id,
            direction='send',
            local_mpo_position_id__in=visited_position_ids,
        )
        .select_related('endpoint', 'channel', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
        .order_by('pk')
    )


def _matched_component_keys(*, path, connector_position_ids: set[int], fiber_strand_ids: set[int]):
    keys = set()
    for step in path.steps:
        if step.object_type == 'connector_position' and step.object_id in connector_position_ids:
            keys.add(('connector_position', step.object_id))
        elif step.object_type == 'fiber_strand' and step.object_id in fiber_strand_ids:
            keys.add(('fiber_strand', step.object_id))
    return keys


def _matched_component_refs(*, connector_position_ids: set[int], fiber_strand_ids: set[int]):
    refs = {}
    for position in ConnectorPosition.objects.filter(pk__in=connector_position_ids).select_related('endpoint'):
        refs[('connector_position', position.pk)] = _ref(position)
    for strand in FiberStrand.objects.filter(pk__in=fiber_strand_ids).select_related('segment'):
        refs[('fiber_strand', strand.pk)] = _ref(strand)
    return refs


def _path_steps(steps) -> tuple[ImpactPathStep, ...]:
    return tuple(
        ImpactPathStep(
            index=index,
            step_type=step.step_type,
            object=ImpactObjectRef(
                model=step.object_type,
                object_id=step.object_id,
                label=step.label,
            ),
            metadata=dict(step.metadata or {}),
        )
        for index, step in enumerate(steps, start=1)
    )


def _lanes_for_paths(paths: tuple[ImpactPath, ...]) -> dict[int, OpticalLane]:
    lane_ids = set()
    for path in paths:
        lane_ids.add(path.source_lane_id)
        if path.destination_lane_id:
            lane_ids.add(path.destination_lane_id)
    return {
        lane.pk: lane
        for lane in OpticalLane.objects.filter(pk__in=lane_ids)
        .select_related('fabric', 'endpoint', 'channel', 'local_mpo_endpoint', 'local_mpo_position', 'plane')
    }


def _lane_impacts(
    *,
    components: _ScenarioComponents,
    paths: tuple[ImpactPath, ...],
    lanes: dict[int, OpticalLane],
) -> tuple[ImpactEntry, ...]:
    builder = _ImpactEntryBuilder()
    for path in paths:
        for lane_id, role in (
            (path.source_lane_id, 'source_lane'),
            (path.destination_lane_id, 'destination_lane'),
        ):
            if lane_id is None:
                continue
            lane = lanes.get(lane_id)
            if lane is None:
                continue
            direct = _lane_directly_failed(lane, components)
            severity = IMPACT_FAILED if direct else IMPACT_DEGRADED
            reason_code = 'direct_physical_failure' if direct else 'peer_path_impacted'
            builder.add(
                kind='lane',
                obj=lane,
                severity=severity,
                reason_code=reason_code,
                message=(
                    'Optical lane is directly failed by the simulated component.'
                    if direct
                    else 'Optical lane is not directly failed, but its resolved path crosses the simulated failure.'
                ),
                remediation=(
                    'Validate both local optics and the end-to-end path after remediating the failed component.'
                ),
                lane_ids=(lane.pk,),
                channel_ids=(lane.channel_id,) if lane.channel_id else (),
                endpoint_ids=(lane.endpoint_id, lane.local_mpo_endpoint_id),
                path_ids=(path.path_id,),
                related_objects=path.matched_components,
                details={'path_role': role},
            )
    return builder.entries()


def _channel_impacts(
    *,
    components: _ScenarioComponents,
    paths: tuple[ImpactPath, ...],
    lane_entries: tuple[ImpactEntry, ...],
) -> tuple[ImpactEntry, ...]:
    channel_ids = {
        channel_id
        for entry in lane_entries
        for channel_id in entry.channel_ids
        if channel_id is not None
    }
    channels = {
        channel.pk: channel
        for channel in TransportChannel.objects.filter(pk__in=channel_ids)
        .select_related('fabric', 'endpoint', 'plane')
    }
    path_ids_by_channel = defaultdict(set)
    lane_ids_by_channel = defaultdict(set)
    related_by_channel = defaultdict(list)
    for entry in lane_entries:
        for channel_id in entry.channel_ids:
            path_ids_by_channel[channel_id].update(entry.path_ids)
            lane_ids_by_channel[channel_id].update(entry.lane_ids)
            related_by_channel[channel_id].extend(entry.related_objects)

    builder = _ImpactEntryBuilder()
    for channel_id, channel in channels.items():
        direct = channel_id in components.direct_channel_ids
        severity = IMPACT_FAILED if direct else IMPACT_DEGRADED
        builder.add(
            kind='channel',
            obj=channel,
            severity=severity,
            reason_code='direct_channel_component_failed' if direct else 'channel_has_impacted_lanes',
            message=(
                'Transport channel maps to a directly failed component.'
                if direct
                else 'Transport channel has one or more lanes whose resolved path crosses the simulated failure.'
            ),
            remediation='Validate channel membership and optical signal after the failed component is restored.',
            lane_ids=tuple(lane_ids_by_channel[channel_id]),
            channel_ids=(channel_id,),
            endpoint_ids=(channel.endpoint_id,),
            path_ids=tuple(path_ids_by_channel[channel_id]),
            related_objects=tuple(related_by_channel[channel_id]),
        )

    for path in paths:
        for channel_id in (path.source_channel_id, path.destination_channel_id):
            if channel_id in channels:
                continue
    return builder.entries()


def _endpoint_impacts(
    *,
    components: _ScenarioComponents,
    paths: tuple[ImpactPath, ...],
    lane_entries: tuple[ImpactEntry, ...],
    channel_entries: tuple[ImpactEntry, ...],
) -> tuple[ImpactEntry, ...]:
    endpoint_ids = set(components.direct_endpoint_ids)
    for entry in lane_entries + channel_entries:
        endpoint_ids.update(entry.endpoint_ids)
    endpoints = {
        endpoint.pk: endpoint
        for endpoint in Endpoint.objects.filter(pk__in=endpoint_ids)
        .select_related('fabric', 'node', 'parent', 'source_type')
    }
    path_ids_by_endpoint = defaultdict(set)
    lane_ids_by_endpoint = defaultdict(set)
    channel_ids_by_endpoint = defaultdict(set)
    related_by_endpoint = defaultdict(list)
    severity_by_endpoint = defaultdict(lambda: IMPACT_LOW)

    for entry in lane_entries + channel_entries:
        for endpoint_id in entry.endpoint_ids:
            if endpoint_id is None:
                continue
            path_ids_by_endpoint[endpoint_id].update(entry.path_ids)
            lane_ids_by_endpoint[endpoint_id].update(entry.lane_ids)
            channel_ids_by_endpoint[endpoint_id].update(entry.channel_ids)
            related_by_endpoint[endpoint_id].append(entry.object)
            severity_by_endpoint[endpoint_id] = _max_severity(severity_by_endpoint[endpoint_id], entry.severity)

    builder = _ImpactEntryBuilder()
    for endpoint_id, endpoint in endpoints.items():
        direct = endpoint_id in components.direct_endpoint_ids
        severity = IMPACT_FAILED if direct else _max_severity(IMPACT_DEGRADED, severity_by_endpoint[endpoint_id])
        if endpoint_id not in path_ids_by_endpoint and not direct:
            severity = IMPACT_AT_RISK
        builder.add(
            kind='endpoint',
            obj=endpoint,
            severity=severity,
            reason_code='direct_endpoint_unavailable' if direct else 'endpoint_has_impacted_lanes',
            message=(
                'Endpoint is directly unavailable in the simulated scenario.'
                if direct
                else 'Endpoint has lanes or channels impacted by the simulated scenario.'
            ),
            remediation='Inspect endpoint seating and mapped lanes/channels after physical remediation.',
            lane_ids=tuple(lane_ids_by_endpoint[endpoint_id]),
            channel_ids=tuple(channel_ids_by_endpoint[endpoint_id]),
            endpoint_ids=(endpoint_id,),
            path_ids=tuple(path_ids_by_endpoint[endpoint_id]),
            related_objects=tuple(related_by_endpoint[endpoint_id]),
        )
    return builder.entries()


def _device_impacts(
    *,
    components: _ScenarioComponents,
    endpoint_entries: tuple[ImpactEntry, ...],
) -> tuple[ImpactEntry, ...]:
    endpoints = {
        endpoint.pk: endpoint
        for endpoint in Endpoint.objects.filter(
            pk__in={
                endpoint_id
                for entry in endpoint_entries
                for endpoint_id in entry.endpoint_ids
            }
        ).select_related('node', 'parent', 'source_type')
    }
    builder = _ImpactEntryBuilder()
    for entry in endpoint_entries:
        endpoint = endpoints.get(entry.endpoint_ids[0]) if entry.endpoint_ids else None
        if endpoint is None:
            continue
        device_or_node = _endpoint_source_device(endpoint) or endpoint.node
        source_interface = _endpoint_source_interface(endpoint)
        direct = source_interface is not None and source_interface.pk in components.direct_interface_ids
        severity = IMPACT_FAILED if direct else IMPACT_AT_RISK
        if entry.severity == IMPACT_FAILED and endpoint.pk in components.direct_endpoint_ids:
            severity = IMPACT_FAILED
        elif entry.severity == IMPACT_DEGRADED:
            severity = _max_severity(severity, IMPACT_AT_RISK)
        builder.add(
            kind='device',
            obj=device_or_node,
            severity=severity,
            reason_code='direct_device_interface_failed' if direct else 'device_contains_impacted_endpoint',
            message=(
                'Device has a directly failed interface/transceiver in the simulated scenario.'
                if direct
                else 'Device or plugin node contains impacted fabric endpoints.'
            ),
            remediation='Check device optics and peer fabric paths after restoring the failed component.',
            lane_ids=entry.lane_ids,
            channel_ids=entry.channel_ids,
            endpoint_ids=entry.endpoint_ids,
            path_ids=entry.path_ids,
            related_objects=(entry.object,),
        )
    return builder.entries()


def _impact_hierarchy(
    *,
    lane_entries: tuple[ImpactEntry, ...],
    channel_entries: tuple[ImpactEntry, ...],
    endpoint_entries: tuple[ImpactEntry, ...],
    device_entries: tuple[ImpactEntry, ...],
) -> tuple[ImpactHierarchyNode, ...]:
    endpoint_by_id = {
        endpoint.pk: endpoint
        for endpoint in Endpoint.objects.filter(
            pk__in={endpoint_id for entry in endpoint_entries for endpoint_id in entry.endpoint_ids}
        ).select_related('fabric', 'node', 'parent', 'source_type')
    }
    channel_by_id = {
        channel.pk: channel
        for channel in TransportChannel.objects.filter(
            pk__in={channel_id for entry in channel_entries for channel_id in entry.channel_ids}
        ).select_related('endpoint')
    }
    lane_by_id = {
        lane.pk: lane
        for lane in OpticalLane.objects.filter(
            pk__in={lane_id for entry in lane_entries for lane_id in entry.lane_ids}
        ).select_related('endpoint', 'channel')
    }
    lane_entry_by_id = {entry.lane_ids[0]: entry for entry in lane_entries if entry.lane_ids}
    channel_entry_by_id = {entry.channel_ids[0]: entry for entry in channel_entries if entry.channel_ids}
    endpoint_entry_by_id = {entry.endpoint_ids[0]: entry for entry in endpoint_entries if entry.endpoint_ids}
    device_entry_by_key = {
        (entry.object.model, entry.object.object_id): entry
        for entry in device_entries
    }

    site_tree = {}
    for endpoint_id, endpoint_entry in endpoint_entry_by_id.items():
        endpoint = endpoint_by_id.get(endpoint_id)
        if endpoint is None:
            continue
        device_or_node = _endpoint_source_device(endpoint) or endpoint.node
        device_key = (_model_label(device_or_node), getattr(device_or_node, 'pk', None))
        device_entry = device_entry_by_key.get(device_key)
        device_severity = device_entry.severity if device_entry is not None else IMPACT_AT_RISK

        site_obj = getattr(device_or_node, 'site', None)
        rack_obj = getattr(device_or_node, 'rack', None)
        site_key = getattr(site_obj, 'pk', None) or f'fabric:{endpoint.fabric_id}:site'
        rack_key = getattr(rack_obj, 'pk', None) or f'{site_key}:rack'

        site_group = site_tree.setdefault(
            site_key,
            {
                'object': _ref(site_obj) if site_obj is not None else _synthetic_ref('site', 'Unknown site'),
                'children': {},
                'lane_ids': set(),
                'channel_ids': set(),
                'endpoint_ids': set(),
                'path_ids': set(),
            },
        )
        rack_group = site_group['children'].setdefault(
            rack_key,
            {
                'object': _ref(rack_obj) if rack_obj is not None else _synthetic_ref('rack', 'Unknown rack'),
                'children': {},
                'lane_ids': set(),
                'channel_ids': set(),
                'endpoint_ids': set(),
                'path_ids': set(),
            },
        )
        device_group = rack_group['children'].setdefault(
            device_key,
            {
                'object': _ref(device_or_node),
                'severity': device_severity,
                'children': {},
                'lane_ids': set(),
                'channel_ids': set(),
                'endpoint_ids': set(),
                'path_ids': set(),
            },
        )
        endpoint_group = device_group['children'].setdefault(
            endpoint_id,
            {
                'entry': endpoint_entry,
                'children': {},
            },
        )

        for collection in (site_group, rack_group, device_group):
            collection['lane_ids'].update(endpoint_entry.lane_ids)
            collection['channel_ids'].update(endpoint_entry.channel_ids)
            collection['endpoint_ids'].update(endpoint_entry.endpoint_ids)
            collection['path_ids'].update(endpoint_entry.path_ids)

        for channel_id in endpoint_entry.channel_ids:
            channel = channel_by_id.get(channel_id)
            channel_entry = channel_entry_by_id.get(channel_id)
            if channel is None or channel_entry is None:
                continue
            channel_group = endpoint_group['children'].setdefault(
                channel_id,
                {
                    'entry': channel_entry,
                    'children': {},
                },
            )
            for lane_id in channel_entry.lane_ids:
                lane = lane_by_id.get(lane_id)
                lane_entry = lane_entry_by_id.get(lane_id)
                if lane is None or lane_entry is None:
                    continue
                channel_group['children'][lane_id] = {'entry': lane_entry}

    site_nodes = []
    for site_group in site_tree.values():
        rack_nodes = []
        for rack_group in site_group['children'].values():
            device_nodes = []
            for device_group in rack_group['children'].values():
                endpoint_nodes = []
                for endpoint_group in device_group['children'].values():
                    endpoint_entry = endpoint_group['entry']
                    channel_nodes = []
                    for channel_group in endpoint_group['children'].values():
                        channel_entry = channel_group['entry']
                        lane_nodes = [
                            _entry_hierarchy_node('lane', lane_group['entry'])
                            for lane_group in channel_group['children'].values()
                        ]
                        channel_nodes.append(
                            _entry_hierarchy_node('channel', channel_entry, children=tuple(lane_nodes))
                        )
                    endpoint_nodes.append(
                        _entry_hierarchy_node('endpoint', endpoint_entry, children=tuple(channel_nodes))
                    )
                device_nodes.append(
                    ImpactHierarchyNode(
                        kind='device',
                        object=device_group['object'],
                        severity=device_group['severity'],
                        lane_ids=tuple(sorted(device_group['lane_ids'])),
                        channel_ids=tuple(sorted(device_group['channel_ids'])),
                        endpoint_ids=tuple(sorted(device_group['endpoint_ids'])),
                        path_ids=tuple(sorted(device_group['path_ids'])),
                        children=tuple(sorted(endpoint_nodes, key=lambda node: node.object.label)),
                    )
                )
            rack_nodes.append(
                ImpactHierarchyNode(
                    kind='rack',
                    object=rack_group['object'],
                    severity=IMPACT_LOW,
                    lane_ids=tuple(sorted(rack_group['lane_ids'])),
                    channel_ids=tuple(sorted(rack_group['channel_ids'])),
                    endpoint_ids=tuple(sorted(rack_group['endpoint_ids'])),
                    path_ids=tuple(sorted(rack_group['path_ids'])),
                    children=tuple(sorted(device_nodes, key=lambda node: node.object.label)),
                )
            )
        site_nodes.append(
            ImpactHierarchyNode(
                kind='site',
                object=site_group['object'],
                severity=IMPACT_LOW,
                lane_ids=tuple(sorted(site_group['lane_ids'])),
                channel_ids=tuple(sorted(site_group['channel_ids'])),
                endpoint_ids=tuple(sorted(site_group['endpoint_ids'])),
                path_ids=tuple(sorted(site_group['path_ids'])),
                children=tuple(sorted(rack_nodes, key=lambda node: node.object.label)),
            )
        )
    return tuple(sorted(site_nodes, key=lambda node: node.object.label))


def _entry_hierarchy_node(kind: str, entry: ImpactEntry, *, children=()) -> ImpactHierarchyNode:
    return ImpactHierarchyNode(
        kind=kind,
        object=entry.object,
        severity=entry.severity,
        lane_ids=entry.lane_ids,
        channel_ids=entry.channel_ids,
        endpoint_ids=entry.endpoint_ids,
        path_ids=entry.path_ids,
        children=children,
        details={'reason_code': entry.reason_code},
    )


class _ImpactEntryBuilder:
    def __init__(self):
        self._entries = {}

    def add(
        self,
        *,
        kind: str,
        obj,
        severity: str,
        reason_code: str,
        message: str,
        remediation: str = '',
        lane_ids=(),
        channel_ids=(),
        endpoint_ids=(),
        path_ids=(),
        related_objects=(),
        details: dict[str, Any] | None = None,
    ) -> None:
        ref = _ref(obj)
        key = (kind, ref.model, ref.object_id)
        if key not in self._entries:
            fabric = _object_fabric(obj)
            self._entries[key] = {
                'kind': kind,
                'object': ref,
                'severity': severity,
                'reason_code': reason_code,
                'message': message,
                'remediation': remediation,
                'fabric_id': getattr(fabric, 'pk', None),
                'fabric_slug': getattr(fabric, 'slug', ''),
                'lane_ids': set(),
                'channel_ids': set(),
                'endpoint_ids': set(),
                'path_ids': set(),
                'related_objects': {},
                'details': {},
            }
        entry = self._entries[key]
        if IMPACT_RANK[severity] > IMPACT_RANK[entry['severity']]:
            entry['severity'] = severity
            entry['reason_code'] = reason_code
            entry['message'] = message
        entry['lane_ids'].update(value for value in lane_ids if value is not None)
        entry['channel_ids'].update(value for value in channel_ids if value is not None)
        entry['endpoint_ids'].update(value for value in endpoint_ids if value is not None)
        entry['path_ids'].update(value for value in path_ids if value)
        for related in related_objects:
            entry['related_objects'][(related.model, related.object_id)] = related
        entry['details'].update(details or {})

    def entries(self) -> tuple[ImpactEntry, ...]:
        entries = []
        for entry in self._entries.values():
            entries.append(
                ImpactEntry(
                    kind=entry['kind'],
                    object=entry['object'],
                    severity=entry['severity'],
                    reason_code=entry['reason_code'],
                    message=entry['message'],
                    remediation=entry['remediation'],
                    fabric_id=entry['fabric_id'],
                    fabric_slug=entry['fabric_slug'],
                    lane_ids=tuple(sorted(entry['lane_ids'])),
                    channel_ids=tuple(sorted(entry['channel_ids'])),
                    endpoint_ids=tuple(sorted(entry['endpoint_ids'])),
                    path_ids=tuple(sorted(entry['path_ids'])),
                    related_objects=tuple(
                        sorted(entry['related_objects'].values(), key=lambda ref: (ref.model, ref.object_id or 0))
                    ),
                    details=entry['details'],
                )
            )
        return tuple(sorted(entries, key=lambda item: (-IMPACT_RANK[item.severity], item.kind, item.object.label)))


def _component_entry(
    *,
    obj,
    kind: str,
    severity: str,
    reason_code: str,
    message: str,
    remediation: str,
    endpoint_ids=(),
) -> ImpactEntry:
    fabric = _object_fabric(obj)
    return ImpactEntry(
        kind=kind,
        object=_ref(obj),
        severity=severity,
        reason_code=reason_code,
        message=message,
        remediation=remediation,
        fabric_id=getattr(fabric, 'pk', None),
        fabric_slug=getattr(fabric, 'slug', ''),
        endpoint_ids=tuple(sorted(endpoint_ids)),
    )


def _lane_directly_failed(lane: OpticalLane, components: _ScenarioComponents) -> bool:
    if lane.local_mpo_position_id in components.unavailable_positions:
        return True
    if lane.local_mpo_endpoint_id in components.direct_endpoint_ids:
        return True
    if lane.endpoint_id in components.direct_endpoint_ids:
        return True
    if lane.channel_id and lane.channel_id in components.direct_channel_ids:
        return True
    return False


def _termination_position_and_endpoint_ids(strand_ids: set[int]) -> tuple[set[int], set[int]]:
    position_ids = set()
    endpoint_ids = set()
    if not strand_ids:
        return position_ids, endpoint_ids
    for position_id, endpoint_id in StrandTermination.objects.filter(strand_id__in=strand_ids).values_list(
        'mpo_position_id',
        'mpo_endpoint_id',
    ):
        position_ids.add(position_id)
        endpoint_ids.add(endpoint_id)
    return position_ids, endpoint_ids


def _channel_ids_for_positions(position_ids: set[int]) -> set[int]:
    if not position_ids:
        return set()
    return set(
        TransportChannel.objects.filter(position_maps__mpo_position_id__in=position_ids)
        .values_list('pk', flat=True)
        .distinct()
    )


def _endpoints_for_interfaces(interfaces: tuple[Interface, ...]) -> tuple[Endpoint, ...]:
    interface_ids = {interface.pk for interface in interfaces}
    if not interface_ids:
        return ()
    content_type = ContentType.objects.get_for_model(Interface, for_concrete_model=False)
    root_ids = set(
        Endpoint.objects.filter(
            source_type=content_type,
            source_id__in=interface_ids,
        ).values_list('pk', flat=True)
    )
    endpoint_ids = set(root_ids)
    frontier = set(root_ids)
    while frontier:
        child_ids = set(Endpoint.objects.filter(parent_id__in=frontier).values_list('pk', flat=True))
        child_ids -= endpoint_ids
        endpoint_ids.update(child_ids)
        frontier = child_ids
    return tuple(
        Endpoint.objects.filter(pk__in=endpoint_ids)
        .select_related('fabric', 'node', 'parent', 'source_type')
        .order_by('address', 'pk')
    )


def _endpoint_source_interface(endpoint: Endpoint) -> Interface | None:
    current = endpoint
    visited = set()
    while current is not None and current.pk not in visited:
        visited.add(current.pk)
        try:
            source = current.source
        except ObjectDoesNotExist:
            source = None
        if isinstance(source, Interface):
            return source
        current = current.parent

    try:
        node_source = endpoint.node.source
    except ObjectDoesNotExist:
        node_source = None
    if isinstance(node_source, Interface):
        return node_source
    return None


def _endpoint_source_device(endpoint: Endpoint) -> Device | None:
    interface = _endpoint_source_interface(endpoint)
    if interface is not None:
        return interface.device
    try:
        node_source = endpoint.node.source
    except ObjectDoesNotExist:
        node_source = None
    if isinstance(node_source, Device):
        return node_source
    return None


def _object_fabric(obj) -> Fabric | None:
    if obj is None:
        return None
    if isinstance(obj, Fabric):
        return obj
    fabric = getattr(obj, 'fabric', None)
    if isinstance(fabric, Fabric):
        return fabric
    endpoint = getattr(obj, 'endpoint', None)
    if endpoint is not None and isinstance(getattr(endpoint, 'fabric', None), Fabric):
        return endpoint.fabric
    segment = getattr(obj, 'segment', None)
    if segment is not None and isinstance(getattr(segment, 'fabric', None), Fabric):
        return segment.fabric
    return None


def _ref(obj) -> ImpactObjectRef:
    if obj is None:
        return _synthetic_ref('object', 'Unknown')
    url = None
    if hasattr(obj, 'get_absolute_url'):
        try:
            url = obj.get_absolute_url()
        except (NoReverseMatch, AttributeError):
            url = None
    return ImpactObjectRef(
        model=_model_label(obj),
        object_id=getattr(obj, 'pk', None),
        label=str(obj),
        url=url,
    )


def _synthetic_ref(model: str, label: str) -> ImpactObjectRef:
    return ImpactObjectRef(model=model, object_id=None, label=label, url=None)


def _model_label(obj) -> str:
    meta = getattr(obj, '_meta', None)
    if meta is None:
        return obj.__class__.__name__.lower()
    return meta.label_lower


def _normalize_many(items, single) -> tuple:
    normalized = []
    if single is not None:
        normalized.append(single)
    for item in items or ():
        if item is not None and item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def _normalize_fabric(fabric: Fabric | int | None) -> Fabric | None:
    if fabric is None:
        return None
    if isinstance(fabric, Fabric):
        return fabric
    return Fabric.objects.get(pk=fabric)


def _scope_payload(fabric_ids: frozenset[int]) -> dict[str, Any]:
    fabrics = tuple(Fabric.objects.filter(pk__in=fabric_ids).order_by('slug', 'pk'))
    return {
        'fabric_ids': [fabric.pk for fabric in fabrics],
        'fabric_slugs': [fabric.slug for fabric in fabrics],
    }


def _stable_report_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _normalize_operation_run(operation_run: OperationRun | int | None) -> OperationRun | None:
    if operation_run is None:
        return None
    if isinstance(operation_run, OperationRun):
        return operation_run
    return OperationRun.objects.get(pk=operation_run)


def _fabric_from_impact_report_scope(report_payload: dict[str, Any]) -> Fabric | None:
    scope = report_payload.get('scope') or {}
    fabric_id = scope.get('fabric_id')
    if fabric_id is None:
        fabric_ids = [item for item in scope.get('fabric_ids') or () if item is not None]
        if len(fabric_ids) == 1:
            fabric_id = fabric_ids[0]
    if fabric_id is None:
        return None
    return Fabric.objects.filter(pk=fabric_id).first()


def _impact_report_payload(report) -> dict[str, Any]:
    if isinstance(report, OperationalImpactReport):
        return report.as_dict()
    if isinstance(report, dict):
        if 'scenario' in report and 'summary' in report:
            return report
        nested_report = report.get('report')
        if isinstance(nested_report, dict) and 'scenario' in nested_report and 'summary' in nested_report:
            return nested_report
    if hasattr(report, 'as_dict'):
        payload = report.as_dict()
        if isinstance(payload, dict):
            return payload
    raise TypeError('Operational impact comparison accepts OperationalImpactReport objects or report dictionaries.')


def _comparison_report_summary(index: int, payload: dict[str, Any]) -> dict[str, Any]:
    scenario = payload.get('scenario') or {}
    return {
        'report_index': index,
        'label': _comparison_report_label(index, payload),
        'scenario_type': scenario.get('scenario_type', ''),
        'scenario_label': scenario.get('label', ''),
        'target_objects': scenario.get('target_objects') or [],
        'selected_fabric': scenario.get('selected_fabric'),
        'scope': payload.get('scope') or {},
        'summary': payload.get('summary') or {},
    }


def _comparison_report_label(index: int, payload: dict[str, Any]) -> str:
    scenario = payload.get('scenario') or {}
    label = scenario.get('label') or scenario.get('scenario_type') or f'Report {index + 1}'
    target_labels = [
        target.get('label')
        for target in scenario.get('target_objects') or ()
        if isinstance(target, dict) and target.get('label')
    ]
    if target_labels:
        return f'{label}: {", ".join(target_labels[:2])}'
    return str(label)


def _comparison_impact_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    impacts = {}
    for collection in _IMPACT_COMPARISON_COLLECTIONS:
        for item in payload.get(collection) or ():
            if not isinstance(item, dict):
                continue
            impact = _comparison_impact_payload(collection, item)
            if impact is not None:
                impacts.setdefault(impact['key'], impact)
    return impacts


def _comparison_impact_payload(collection: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if collection == 'impacted_paths':
        path_id = item.get('path_id')
        if not path_id:
            return None
        source_lane = item.get('source_lane') or {}
        destination_lane = item.get('destination_lane') or {}
        destination_label = destination_lane.get('label') or 'unresolved'
        object_ref = {
            'model': 'operational_impact.path',
            'id': path_id,
            'label': f'{source_lane.get("label", path_id)} -> {destination_label}',
            'url': None,
        }
        key = f'{collection}:{path_id}'
        kind = 'path'
        detail = {
            'path_id': path_id,
            'source_lane_id': item.get('source_lane_id'),
            'destination_lane_id': item.get('destination_lane_id'),
            'source_channel_id': item.get('source_channel_id'),
            'destination_channel_id': item.get('destination_channel_id'),
        }
    else:
        object_ref = item.get('object') or {}
        if not isinstance(object_ref, dict):
            return None
        kind = item.get('kind') or collection
        key = f'{collection}:{_object_ref_key(object_ref, fallback=kind)}'
        detail = {
            'lane_ids': item.get('lane_ids') or [],
            'channel_ids': item.get('channel_ids') or [],
            'endpoint_ids': item.get('endpoint_ids') or [],
            'path_ids': item.get('path_ids') or [],
        }

    return {
        'key': key,
        'collection': collection,
        'kind': kind,
        'object': object_ref,
        'severity': item.get('severity') or IMPACT_LOW,
        'severity_label': item.get('severity_label') or IMPACT_LABELS.get(item.get('severity'), ''),
        'reason_code': item.get('reason_code', ''),
        'message': item.get('message', ''),
        'details': detail,
    }


def _object_ref_key(object_ref: dict[str, Any], *, fallback: str) -> str:
    model = object_ref.get('model') or fallback
    object_id = object_ref.get('id')
    if object_id is None:
        object_id = object_ref.get('label') or ''
    return f'{model}:{object_id}'


def _comparison_group_payload(
    key: str,
    report_indexes: set[int],
    impact_maps: tuple[dict[str, dict[str, Any]], ...],
    report_summaries: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    ordered_indexes = tuple(sorted(report_indexes))
    first_impact = next(impact_maps[index][key] for index in ordered_indexes if key in impact_maps[index])
    impacts = tuple(impact_maps[index][key] for index in ordered_indexes if key in impact_maps[index])
    return {
        'key': key,
        'collection': first_impact['collection'],
        'kind': first_impact['kind'],
        'object': first_impact['object'],
        'report_indexes': list(ordered_indexes),
        'reports': [
            {
                'report_index': index,
                'report_label': report_summaries[index]['label'],
            }
            for index in ordered_indexes
        ],
        'severities': [
            {
                'report_index': index,
                'severity': impact_maps[index][key]['severity'],
                'severity_label': impact_maps[index][key]['severity_label'],
                'rank': IMPACT_RANK.get(impact_maps[index][key]['severity'], 0),
            }
            for index in ordered_indexes
        ],
        'max_severity': max(
            (impact['severity'] for impact in impacts),
            key=lambda severity: IMPACT_RANK.get(severity, 0),
        ),
        'reason_codes': sorted(
            {
                impact.get('reason_code', '')
                for impact in impacts
                if impact.get('reason_code')
            }
        ),
        'impact_by_report': [
            {
                'report_index': index,
                'severity': impact_maps[index][key]['severity'],
                'reason_code': impact_maps[index][key]['reason_code'],
                'message': impact_maps[index][key]['message'],
                'details': impact_maps[index][key]['details'],
            }
            for index in ordered_indexes
        ],
    }


def _comparison_sort_key(key: str, impact_maps: tuple[dict[str, dict[str, Any]], ...]):
    impacts = [
        impact_map[key]
        for impact_map in impact_maps
        if key in impact_map
    ]
    max_rank = max((IMPACT_RANK.get(impact['severity'], 0) for impact in impacts), default=0)
    collection = impacts[0]['collection'] if impacts else ''
    collection_index = (
        _IMPACT_COMPARISON_COLLECTIONS.index(collection)
        if collection in _IMPACT_COMPARISON_COLLECTIONS
        else len(_IMPACT_COMPARISON_COLLECTIONS)
    )
    kind = impacts[0]['kind'] if impacts else ''
    return (-max_rank, collection_index, kind, key)


def _severity_delta_payload(
    *,
    base_payload: dict[str, Any],
    compare_payload: dict[str, Any],
    base_impacts: dict[str, dict[str, Any]],
    compare_impacts: dict[str, dict[str, Any]],
    base_summary: dict[str, Any],
    compare_summary: dict[str, Any],
) -> dict[str, Any]:
    base_report_summary = base_payload.get('summary') or {}
    compare_report_summary = compare_payload.get('summary') or {}
    severity_changes = []
    for key in sorted(set(base_impacts) & set(compare_impacts), key=lambda item: _comparison_sort_key(item, (base_impacts, compare_impacts))):
        base_severity = base_impacts[key]['severity']
        compare_severity = compare_impacts[key]['severity']
        rank_delta = IMPACT_RANK.get(compare_severity, 0) - IMPACT_RANK.get(base_severity, 0)
        if rank_delta == 0:
            continue
        severity_changes.append(
            {
                'key': key,
                'collection': compare_impacts[key]['collection'],
                'kind': compare_impacts[key]['kind'],
                'object': compare_impacts[key]['object'],
                'base_severity': base_severity,
                'compare_severity': compare_severity,
                'rank_delta': rank_delta,
            }
        )

    return {
        'base_report_index': base_summary['report_index'],
        'base_report_label': base_summary['label'],
        'compare_report_index': compare_summary['report_index'],
        'compare_report_label': compare_summary['label'],
        'summary_delta': {
            field: (compare_report_summary.get(field, 0) or 0) - (base_report_summary.get(field, 0) or 0)
            for field in _SUMMARY_COUNT_FIELDS
        },
        'severity_count_delta': {
            field: {
                severity: (
                    (compare_report_summary.get(field) or {}).get(severity, 0)
                    - (base_report_summary.get(field) or {}).get(severity, 0)
                )
                for severity in (IMPACT_FAILED, IMPACT_DEGRADED, IMPACT_AT_RISK, IMPACT_LOW)
            }
            for field in _SUMMARY_SEVERITY_FIELDS
        },
        'severity_change_count': len(severity_changes),
        'severity_changes': severity_changes,
    }


def _device_delta_payload(
    *,
    base_payload: dict[str, Any],
    compare_payload: dict[str, Any],
    base_summary: dict[str, Any],
    compare_summary: dict[str, Any],
) -> dict[str, Any]:
    base_devices = _device_impact_map(base_payload)
    compare_devices = _device_impact_map(compare_payload)
    base_keys = set(base_devices)
    compare_keys = set(compare_devices)
    common_keys = base_keys & compare_keys
    added_keys = compare_keys - base_keys
    removed_keys = base_keys - compare_keys
    severity_changes = []
    for key in sorted(common_keys):
        base_severity = base_devices[key]['severity']
        compare_severity = compare_devices[key]['severity']
        rank_delta = IMPACT_RANK.get(compare_severity, 0) - IMPACT_RANK.get(base_severity, 0)
        if rank_delta == 0:
            continue
        severity_changes.append(
            {
                'key': key,
                'object': compare_devices[key]['object'],
                'base_severity': base_severity,
                'compare_severity': compare_severity,
                'rank_delta': rank_delta,
            }
        )

    return {
        'base_report_index': base_summary['report_index'],
        'base_report_label': base_summary['label'],
        'compare_report_index': compare_summary['report_index'],
        'compare_report_label': compare_summary['label'],
        'common_device_count': len(common_keys),
        'added_device_count': len(added_keys),
        'removed_device_count': len(removed_keys),
        'added_devices': [
            compare_devices[key]
            for key in sorted(added_keys)
        ],
        'removed_devices': [
            base_devices[key]
            for key in sorted(removed_keys)
        ],
        'severity_change_count': len(severity_changes),
        'severity_changes': severity_changes,
    }


def _device_impact_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    devices = {}
    for item in payload.get('impacted_devices') or ():
        if not isinstance(item, dict):
            continue
        object_ref = item.get('object') or {}
        if not isinstance(object_ref, dict):
            continue
        key = _object_ref_key(object_ref, fallback='device')
        devices[key] = {
            'key': key,
            'object': object_ref,
            'severity': item.get('severity') or IMPACT_LOW,
            'severity_label': item.get('severity_label') or IMPACT_LABELS.get(item.get('severity'), ''),
            'lane_ids': item.get('lane_ids') or [],
            'channel_ids': item.get('channel_ids') or [],
            'endpoint_ids': item.get('endpoint_ids') or [],
            'path_ids': item.get('path_ids') or [],
        }
    return devices


def _max_severity(left: str, right: str) -> str:
    return left if IMPACT_RANK[left] >= IMPACT_RANK[right] else right


def _severity_counts(counter: Counter) -> dict[str, int]:
    return {
        severity: counter.get(severity, 0)
        for severity in (IMPACT_FAILED, IMPACT_DEGRADED, IMPACT_AT_RISK, IMPACT_LOW)
    }
