from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Model
from dcim.models import Site

from netbox_plant_graph.models import (
    AllocationRuleSet,
    ArchitectureRole,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    Plane,
    StampTemplate,
    StrandTermination,
    TransportChannel,
    TransportChannelPositionMap,
    TransferPattern,
)
from netbox_plant_graph.services.architecture import (
    build_roce_4plane_shuffle_architecture_schema,
    shuffle_2x2_transfer_position_pairs,
)
from netbox_plant_graph.services.architecture_schema import (
    ARCHITECTURE_COMPATIBILITY_COMPATIBLE,
    ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE,
    ARCHITECTURE_COMPATIBILITY_WARNING,
    ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
    compare_persisted_architecture_compatibility,
    validate_architecture_schema,
)
from netbox_plant_graph.services.blueprint_registry import (
    BLUEPRINT_LIFECYCLE_ACTIVE,
    BLUEPRINT_LIFECYCLE_RETIRED,
    architecture_definition_from_payload,
    architecture_definition_to_payload,
    check_blueprint_lifecycle,
    get_default_blueprint_registry,
    validate_parameter_schema,
)


OUTCOME_CREATE = 'create'
OUTCOME_UPDATE = 'update'
OUTCOME_SKIP = 'skip'
OUTCOME_CONFLICT = 'conflict'
SUPPORTED_OUTCOMES = (OUTCOME_CREATE, OUTCOME_UPDATE, OUTCOME_SKIP, OUTCOME_CONFLICT)

MISSING = object()

DEPENDENCY_EXISTING = 'existing'
DEPENDENCY_MISSING = 'missing'
DEPENDENCY_PLANNED = 'planned'

IMPORT_ARCHITECTURE_NOT_PROVIDED = 'not_provided'

PROVENANCE_METADATA_NAMESPACE = 'import_reconciliation'
PROVENANCE_FIELDS = (
    'source_system',
    'source_document',
    'source_row',
    'external_id',
    'idempotency_key',
)


@dataclass(frozen=True)
class FieldChange:
    field: str
    current: Any
    desired: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            'field': self.field,
            'current': _serializable_value(self.current),
            'desired': _serializable_value(self.desired),
        }


@dataclass(frozen=True)
class ImportDiff:
    index: int
    kind: str
    identity: str
    outcome: str
    message: str
    changes: tuple[FieldChange, ...] = ()
    object_model: str = ''
    object_id: int | None = None
    error: str = ''
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return self.outcome in {OUTCOME_CREATE, OUTCOME_UPDATE}

    @property
    def is_conflict(self) -> bool:
        return self.outcome == OUTCOME_CONFLICT

    def to_dict(self) -> dict[str, Any]:
        return {
            'index': self.index,
            'kind': self.kind,
            'identity': self.identity,
            'outcome': self.outcome,
            'message': self.message,
            'changes': [change.to_dict() for change in self.changes],
            'object_model': self.object_model,
            'object_id': self.object_id,
            'error': self.error,
            'details': _serializable_value(dict(self.details)),
        }


@dataclass(frozen=True)
class ImportReference:
    kind: str
    model: str
    identity: str
    key: tuple[Any, ...]
    lookup: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'model': self.model,
            'identity': self.identity,
            'lookup': _serializable_value(dict(self.lookup)),
        }


@dataclass(frozen=True)
class ImportDependencyEdge:
    dependent_index: int
    field: str
    reference: ImportReference
    status: str
    prerequisite_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'dependent_index': self.dependent_index,
            'prerequisite_index': self.prerequisite_index,
            'field': self.field,
            'status': self.status,
            'reference': self.reference.to_dict(),
        }


@dataclass(frozen=True)
class ReconciliationSummary:
    total: int
    create: int = 0
    update: int = 0
    skip: int = 0
    conflict: int = 0

    @classmethod
    def from_diffs(cls, diffs: Iterable[ImportDiff]) -> 'ReconciliationSummary':
        counts = Counter(diff.outcome for diff in diffs)
        return cls(
            total=sum(counts.values()),
            create=counts[OUTCOME_CREATE],
            update=counts[OUTCOME_UPDATE],
            skip=counts[OUTCOME_SKIP],
            conflict=counts[OUTCOME_CONFLICT],
        )

    def to_dict(self) -> dict[str, int]:
        return {
            'total': self.total,
            OUTCOME_CREATE: self.create,
            OUTCOME_UPDATE: self.update,
            OUTCOME_SKIP: self.skip,
            OUTCOME_CONFLICT: self.conflict,
        }


@dataclass(frozen=True)
class ImportArchitectureIssue:
    code: str
    path: str
    message: str
    severity: str = 'error'
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
            'severity': self.severity,
            'context': _serializable_value(dict(self.context)),
        }


@dataclass(frozen=True)
class ImportArchitectureGate:
    hint_present: bool = False
    status: str = IMPORT_ARCHITECTURE_NOT_PROVIDED
    target_source: str = 'none'
    target_architecture_id: int | None = None
    target_architecture_slug: str | None = None
    target_architecture_version: str | None = None
    schema_contract_version: str | None = None
    issues: tuple[ImportArchitectureIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == 'error' for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            'hint_present': self.hint_present,
            'status': self.status,
            'target_source': self.target_source,
            'target_architecture_id': self.target_architecture_id,
            'target_architecture_slug': self.target_architecture_slug,
            'target_architecture_version': self.target_architecture_version,
            'schema_contract_version': self.schema_contract_version,
            'issues': [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ImportPlan:
    diffs: tuple[ImportDiff, ...]
    applied: bool = False
    payload_version: str = ''
    source_label: str = ''
    apply_order: tuple[int, ...] = ()
    dependency_edges: tuple[ImportDependencyEdge, ...] = ()
    architecture_gate: ImportArchitectureGate = field(default_factory=ImportArchitectureGate)
    committed: bool = False
    transactional: bool = True
    summary: ReconciliationSummary = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'summary', ReconciliationSummary.from_diffs(self.diffs))

    @property
    def has_conflicts(self) -> bool:
        return self.summary.conflict > 0 or self.architecture_gate.has_errors

    @property
    def has_changes(self) -> bool:
        return self.summary.create > 0 or self.summary.update > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            'applied': self.applied,
            'summary': self.summary.to_dict(),
            'diffs': [diff.to_dict() for diff in self.diffs],
            'payload_version': self.payload_version,
            'source_label': self.source_label,
            'apply_order': list(self.apply_order),
            'dependency_edges': [edge.to_dict() for edge in self.dependency_edges],
            'architecture_gate': self.architecture_gate.to_dict(),
            'committed': self.committed,
            'transactional': self.transactional,
        }


@dataclass(frozen=True)
class ImportBundleItem:
    index: int
    kind: str
    data: Mapping[str, Any]
    produces: tuple[ImportReference, ...] = ()
    requires: tuple[tuple[str, ImportReference], ...] = ()


@dataclass(frozen=True)
class ImportBundle:
    items: tuple[ImportBundleItem, ...]
    item_count: int
    payload_version: str = ''
    source_label: str = ''


@dataclass(frozen=True)
class DependencyPlan:
    ordered_items: tuple[ImportBundleItem, ...]
    apply_order: tuple[int, ...]
    dependency_edges: tuple[ImportDependencyEdge, ...]
    conflicts: tuple[ImportDiff, ...] = ()


@dataclass(frozen=True)
class PlannedCable:
    site_id: int
    cable_id: str


@dataclass
class ReconciliationContext:
    planned_cables: set[PlannedCable] = field(default_factory=set)

    def mark_cable_available(self, site: Site, cable_id: str):
        self.planned_cables.add(PlannedCable(site_id=site.pk, cable_id=cable_id))

    def cable_available(self, site: Site, cable_id: str) -> bool:
        if CableAssembly.objects.filter(site=site, cable_id=cable_id).exists():
            return True
        return PlannedCable(site_id=site.pk, cable_id=cable_id) in self.planned_cables


class ImportRowError(ValueError):
    pass


class BaseHandler:
    kind = ''
    model: type[Model] | None = None

    def reconcile(
        self,
        *,
        index: int,
        data: Mapping[str, Any],
        context: ReconciliationContext,
        apply: bool,
    ) -> ImportDiff:
        raise NotImplementedError

    def conflict(
        self,
        *,
        index: int,
        identity: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> ImportDiff:
        return _conflict(
            index=index,
            kind=self.kind,
            identity=identity,
            reason=reason,
            model=self.model,
            details=details,
        )


class CableAssemblyHandler(BaseHandler):
    kind = 'cable_assembly'
    model = CableAssembly

    def reconcile(self, *, index, data, context, apply):
        site = _resolve_site(_required(data, 'site', 'site_slug'))
        cable_id = _required_string(data, 'cable_id')
        identity = f'site={site.slug} cable_id={cable_id}'
        parent_cable = _resolve_optional_cable_ref(
            _optional(data, 'parent_cable', 'parent_cable_id'),
            default_site=site,
        )
        desired = _desired_fields(
            data,
            {
                'manufacturer': ('manufacturer',),
                'serial_number': ('serial_number',),
                'model_id': ('model_id',),
                'description': ('description',),
                'metadata': ('metadata',),
            },
        )
        if _has_any(data, 'parent_cable', 'parent_cable_id'):
            desired['parent_cable'] = parent_cable
        instance = CableAssembly.objects.filter(site=site, cable_id=cable_id).first()
        desired = _merge_provenance_metadata(data, desired, instance=instance)
        diff = _reconcile_model(
            index=index,
            kind=self.kind,
            identity=identity,
            model=CableAssembly,
            instance=instance,
            create_kwargs={'site': site, 'cable_id': cable_id},
            desired=desired,
            apply=apply,
        )
        if diff.outcome in {OUTCOME_CREATE, OUTCOME_UPDATE, OUTCOME_SKIP}:
            context.mark_cable_available(site, cable_id)
        return diff


class FiberStrandCableHandler(BaseHandler):
    kind = 'fiber_strand_cable'
    model = FiberStrand

    def reconcile(self, *, index, data, context, apply):
        fabric = _resolve_fabric(_required(data, 'fabric', 'fabric_slug'))
        segment = _resolve_segment(fabric, _required(data, 'segment', 'segment_name'))
        strand_index = _required_int(data, 'strand_index')
        identity = f'fabric={fabric.slug} segment={segment.name} strand={strand_index}'
        strand = FiberStrand.objects.filter(segment=segment, strand_index=strand_index).first()
        if strand is None:
            return self.conflict(index=index, identity=identity, reason='missing FiberStrand')

        cable_site_value = _optional(data, 'cable_site', 'cable_site_slug')
        cable_id_value = _optional(data, 'cable_id')
        if cable_site_value is MISSING and cable_id_value is MISSING:
            return self.conflict(index=index, identity=identity, reason='missing cable_site and cable_id')
        cable_site = None if cable_site_value in (MISSING, None, '') else _resolve_site(cable_site_value)
        cable_id = '' if cable_id_value in (MISSING, None) else str(cable_id_value)
        if bool(cable_site) != bool(cable_id):
            return self.conflict(
                index=index,
                identity=identity,
                reason='cable_site and cable_id must be provided together',
            )
        if cable_site is not None and not context.cable_available(cable_site, cable_id):
            reason = f'missing CableAssembly site={cable_site.slug} cable_id={cable_id}'
            return self.conflict(index=index, identity=identity, reason=reason)

        desired = {
            'cable_site': cable_site,
            'cable_id': cable_id,
            **_desired_fields(data, {'label': ('label',), 'metadata': ('metadata',)}),
        }
        desired = _merge_provenance_metadata(data, desired, instance=strand)
        return _reconcile_model(
            index=index,
            kind=self.kind,
            identity=identity,
            model=FiberStrand,
            instance=strand,
            create_kwargs={},
            desired=desired,
            apply=apply,
            create_allowed=False,
        )


class EndpointHandler(BaseHandler):
    kind = 'endpoint'
    model = Endpoint

    def reconcile(self, *, index, data, context, apply):
        fabric = _resolve_fabric(_required(data, 'fabric', 'fabric_slug'))
        address = _required_string(data, 'address')
        node = _resolve_node(fabric, _required(data, 'node', 'node_address'))
        identity = f'fabric={fabric.slug} address={address}'
        parent = _resolve_optional_endpoint(fabric, _optional(data, 'parent', 'parent_address'))
        name = _optional_string(data, 'name', default=address.rsplit('.', 1)[-1])
        desired = {
            'node': node,
            'name': name,
            **_desired_fields(
                data,
                {
                    'endpoint_kind': ('endpoint_kind',),
                    'connector_kind': ('connector_kind',),
                    'position_count': ('position_count',),
                    'metadata': ('metadata',),
                },
            ),
        }
        if _has_any(data, 'parent', 'parent_address'):
            desired['parent'] = parent
        instance = Endpoint.objects.filter(fabric=fabric, address=address).first()
        desired = _merge_provenance_metadata(data, desired, instance=instance)
        return _reconcile_model(
            index=index,
            kind=self.kind,
            identity=identity,
            model=Endpoint,
            instance=instance,
            create_kwargs={'fabric': fabric, 'address': address},
            desired=desired,
            apply=apply,
        )


class TransportChannelHandler(BaseHandler):
    kind = 'transport_channel'
    model = TransportChannel

    def reconcile(self, *, index, data, context, apply):
        fabric = _resolve_fabric(_required(data, 'fabric', 'fabric_slug'))
        endpoint = _resolve_endpoint(fabric, _required(data, 'endpoint', 'endpoint_address'))
        channel_index = _required_int(data, 'channel_index')
        identity = f'fabric={fabric.slug} endpoint={endpoint.address} channel={channel_index}'
        plane = _resolve_optional_plane(fabric, _optional(data, 'plane', 'plane_number', 'plane_label'))
        desired = {
            'name': _optional_string(data, 'name', default=f'channel-{channel_index}'),
            **_desired_fields(data, {'speed_gbps': ('speed_gbps',), 'metadata': ('metadata',)}),
        }
        if _has_any(data, 'plane', 'plane_number', 'plane_label'):
            desired['plane'] = plane
        instance = TransportChannel.objects.filter(
            fabric=fabric,
            endpoint=endpoint,
            channel_index=channel_index,
        ).first()
        desired = _merge_provenance_metadata(data, desired, instance=instance)
        return _reconcile_model(
            index=index,
            kind=self.kind,
            identity=identity,
            model=TransportChannel,
            instance=instance,
            create_kwargs={'fabric': fabric, 'endpoint': endpoint, 'channel_index': channel_index},
            desired=desired,
            apply=apply,
        )


class TransportChannelPositionMapHandler(BaseHandler):
    kind = 'transport_channel_position_map'
    model = TransportChannelPositionMap

    def reconcile(self, *, index, data, context, apply):
        fabric = _resolve_fabric(_required(data, 'fabric', 'fabric_slug'))
        channel_endpoint = _resolve_endpoint(
            fabric,
            _required(data, 'channel_endpoint', 'endpoint', 'endpoint_address'),
        )
        channel_index = _required_int(data, 'channel_index')
        channel = TransportChannel.objects.filter(
            fabric=fabric,
            endpoint=channel_endpoint,
            channel_index=channel_index,
        ).first()
        channel_identity = f'fabric={fabric.slug} endpoint={channel_endpoint.address} channel={channel_index}'
        if channel is None:
            return self.conflict(index=index, identity=channel_identity, reason='missing TransportChannel')

        mpo_endpoint = _resolve_endpoint(fabric, _required(data, 'mpo_endpoint', 'mpo_endpoint_address'))
        position_number = _required_int(data, 'mpo_position', 'position', 'position_number')
        identity = (
            f'fabric={fabric.slug} endpoint={channel_endpoint.address} channel={channel_index} '
            f'mpo={mpo_endpoint.address}:{position_number}'
        )
        mpo_position = _resolve_connector_position(mpo_endpoint, position_number)
        desired = {
            'mpo_endpoint': mpo_endpoint,
            **_desired_fields(data, {'metadata': ('metadata',)}),
        }
        instance = TransportChannelPositionMap.objects.filter(channel=channel, mpo_position=mpo_position).first()
        desired = _merge_provenance_metadata(data, desired, instance=instance)
        return _reconcile_model(
            index=index,
            kind=self.kind,
            identity=identity,
            model=TransportChannelPositionMap,
            instance=instance,
            create_kwargs={'channel': channel, 'mpo_position': mpo_position},
            desired=desired,
            apply=apply,
        )


class StrandTerminationHandler(BaseHandler):
    kind = 'strand_termination'
    model = StrandTermination

    def reconcile(self, *, index, data, context, apply):
        fabric = _resolve_fabric(_required(data, 'fabric', 'fabric_slug'))
        segment = _resolve_segment(fabric, _required(data, 'segment', 'segment_name'))
        strand_index = _required_int(data, 'strand_index')
        strand = FiberStrand.objects.filter(segment=segment, strand_index=strand_index).first()
        strand_identity = f'fabric={fabric.slug} segment={segment.name} strand={strand_index}'
        if strand is None:
            return self.conflict(index=index, identity=strand_identity, reason='missing FiberStrand')

        mpo_endpoint = _resolve_endpoint(fabric, _required(data, 'mpo_endpoint', 'mpo_endpoint_address'))
        position_number = _required_int(data, 'mpo_position', 'position', 'position_number')
        identity = f'{strand_identity} mpo={mpo_endpoint.address}:{position_number}'
        mpo_position = _resolve_connector_position(mpo_endpoint, position_number)
        other_termination = StrandTermination.objects.filter(mpo_position=mpo_position).exclude(strand=strand).first()
        if other_termination is not None:
            other_strand = other_termination.strand
            reason = (
                'mpo_position already terminated by '
                f'segment={other_strand.segment.name} strand={other_strand.strand_index}'
            )
            return self.conflict(index=index, identity=identity, reason=reason)

        desired = {
            'mpo_endpoint': mpo_endpoint,
            **_desired_fields(
                data,
                {
                    'termination_index': ('termination_index',),
                    'label': ('label',),
                    'metadata': ('metadata',),
                },
            ),
        }
        instance = StrandTermination.objects.filter(strand=strand, mpo_position=mpo_position).first()
        desired = _merge_provenance_metadata(data, desired, instance=instance)
        return _reconcile_model(
            index=index,
            kind=self.kind,
            identity=identity,
            model=StrandTermination,
            instance=instance,
            create_kwargs={'strand': strand, 'mpo_position': mpo_position},
            desired=desired,
            apply=apply,
        )


class FabricArchitectureBlueprintHandler(BaseHandler):
    kind = 'fabric_architecture_blueprint'
    model = FabricArchitecture

    def reconcile(self, *, index, data, context, apply):
        definition_payload = _blueprint_definition_payload(data)
        parameter_schema = _mapping_value(
            _optional_default(data, 'parameter_schema', default={}),
            field_name='parameter_schema',
        )
        required_device_types = _mapping_value(
            _optional_default(data, 'required_device_types', default={}),
            field_name='required_device_types',
        )
        lifecycle = str(_optional_default(data, 'lifecycle', default=BLUEPRINT_LIFECYCLE_ACTIVE))
        successor_version = str(_optional_default(data, 'successor_version', default=''))
        schema_contract_version = _blueprint_schema_contract_version(data)

        try:
            definition = architecture_definition_from_payload(definition_payload)
        except ValueError as exc:
            return self.conflict(
                index=index,
                identity=f'item[{index}]',
                reason=str(exc),
                details={'code': 'architecture_schema_payload_invalid'},
            )

        identity = f'slug={definition.slug} version={definition.version}'
        if schema_contract_version != ARCHITECTURE_SCHEMA_CONTRACT_VERSION:
            return self.conflict(
                index=index,
                identity=identity,
                reason=(
                    f'schema_contract_version {schema_contract_version!r} does not match '
                    f'{ARCHITECTURE_SCHEMA_CONTRACT_VERSION!r}'
                ),
                details={
                    'code': 'schema_contract_version_mismatch',
                    'actual': schema_contract_version,
                    'expected': ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
                },
            )
        if lifecycle == BLUEPRINT_LIFECYCLE_RETIRED:
            return self.conflict(
                index=index,
                identity=identity,
                reason='retired blueprints cannot be imported as active architecture definitions',
                details={'code': 'blueprint_lifecycle', 'lifecycle': lifecycle},
            )

        schema_result = validate_architecture_schema(definition)
        if not schema_result.is_valid:
            return self.conflict(
                index=index,
                identity=identity,
                reason='architecture schema validation failed',
                details={
                    'code': 'architecture_schema_invalid',
                    'issues': [
                        {
                            'code': error.code,
                            'path': error.path,
                            'message': error.message,
                            'context': dict(error.context),
                        }
                        for error in schema_result.errors
                    ],
                },
            )

        parameter_schema_issues = validate_parameter_schema(parameter_schema)
        if parameter_schema_issues:
            return self.conflict(
                index=index,
                identity=identity,
                reason='parameter_schema validation failed',
                details={
                    'code': 'parameter_schema_invalid',
                    'issues': [issue.to_dict() for issue in parameter_schema_issues],
                },
            )

        architecture = FabricArchitecture.objects.filter(slug=definition.slug, version=definition.version).first()
        desired = _blueprint_architecture_desired_fields(
            data=data,
            definition=definition,
            parameter_schema=parameter_schema,
            required_device_types=required_device_types,
            lifecycle=lifecycle,
            successor_version=successor_version,
            schema_contract_version=schema_contract_version,
        )
        child_state = _blueprint_child_state(architecture, definition, data)
        outcome = OUTCOME_CREATE if architecture is None else OUTCOME_SKIP
        changes = ()
        if architecture is not None:
            changes = tuple(
                FieldChange(field=name, current=getattr(architecture, name), desired=value)
                for name, value in desired.items()
                if not _values_equal(getattr(architecture, name), value)
            )
            if changes or child_state['has_changes']:
                outcome = OUTCOME_UPDATE

        if apply:
            architecture = _apply_blueprint_architecture(
                architecture=architecture,
                definition=definition,
                desired=desired,
                data=data,
            )

        details = {
            'code': 'fabric_architecture_blueprint',
            'schema_contract_version': schema_contract_version,
            'lifecycle': lifecycle,
            'successor_version': successor_version,
            'child_state': child_state,
            'parameter_schema': parameter_schema,
            'required_device_types': required_device_types,
        }
        diff = _diff(
            index=index,
            kind=self.kind,
            identity=identity,
            outcome=outcome,
            model=FabricArchitecture,
            object_id=getattr(architecture, 'pk', None),
            changes=changes,
        )
        return replace(diff, details=details)


HANDLERS = {
    'cable_assembly': CableAssemblyHandler(),
    'fiber_strand_cable': FiberStrandCableHandler(),
    'endpoint': EndpointHandler(),
    'transport_channel': TransportChannelHandler(),
    'transport_channel_position_map': TransportChannelPositionMapHandler(),
    'strand_termination': StrandTerminationHandler(),
    'fabric_architecture_blueprint': FabricArchitectureBlueprintHandler(),
}

KIND_ALIASES = {
    'cable': 'cable_assembly',
    'cable_assembly': 'cable_assembly',
    'cableassembly': 'cable_assembly',
    'fiber_strand_cable': 'fiber_strand_cable',
    'fiber_strand_cable_link': 'fiber_strand_cable',
    'fiber_strand_cable_linkage': 'fiber_strand_cable',
    'fiberstrand_cable': 'fiber_strand_cable',
    'fiberstrandcable': 'fiber_strand_cable',
    'endpoint': 'endpoint',
    'channel': 'transport_channel',
    'transport_channel': 'transport_channel',
    'transportchannel': 'transport_channel',
    'channel_position_map': 'transport_channel_position_map',
    'transport_channel_position_map': 'transport_channel_position_map',
    'transportchannelpositionmap': 'transport_channel_position_map',
    'strand_termination': 'strand_termination',
    'strandtermination': 'strand_termination',
    'architecture_blueprint': 'fabric_architecture_blueprint',
    'blueprint': 'fabric_architecture_blueprint',
    'fabric_architecture_blueprint': 'fabric_architecture_blueprint',
    'fabricarchitectureblueprint': 'fabric_architecture_blueprint',
}


def build_import_plan(payload: Any) -> ImportPlan:
    return reconcile_import_payload(payload, apply=False)


def reconcile_import_payload(payload: Any, *, apply: bool = False) -> ImportPlan:
    bundle, initial_diffs = _build_import_bundle(payload)
    architecture_gate = _build_import_architecture_gate(payload)
    if architecture_gate.has_errors:
        return ImportPlan(
            diffs=_architecture_gate_conflicts(architecture_gate),
            applied=apply,
            payload_version=bundle.payload_version,
            source_label=bundle.source_label,
            architecture_gate=architecture_gate,
            committed=False,
        )

    dependency_plan = _plan_bundle_dependencies(bundle)
    diff_by_index = {diff.index: diff for diff in initial_diffs}
    for diff in dependency_plan.conflicts:
        diff_by_index.setdefault(diff.index, diff)

    context = ReconciliationContext()
    runnable_items = tuple(item for item in dependency_plan.ordered_items if item.index not in diff_by_index)
    apply_with_rollback = any(edge.status == DEPENDENCY_PLANNED for edge in dependency_plan.dependency_edges)
    should_write = apply or apply_with_rollback

    if should_write:
        with transaction.atomic():
            _reconcile_ordered_items(
                runnable_items,
                context=context,
                diff_by_index=diff_by_index,
                apply=True,
            )
            if not apply or any(diff.is_conflict for diff in diff_by_index.values()):
                transaction.set_rollback(True)
    else:
        _reconcile_ordered_items(
            runnable_items,
            context=context,
            diff_by_index=diff_by_index,
            apply=False,
        )

    diffs = tuple(diff_by_index[index] for index in range(bundle.item_count) if index in diff_by_index)
    has_conflicts = any(diff.is_conflict for diff in diffs)
    return ImportPlan(
        diffs=diffs,
        applied=apply,
        payload_version=bundle.payload_version,
        source_label=bundle.source_label,
        apply_order=dependency_plan.apply_order,
        dependency_edges=dependency_plan.dependency_edges,
        architecture_gate=architecture_gate,
        committed=bool(apply and not has_conflicts),
    )


def _build_import_architecture_gate(payload: Any) -> ImportArchitectureGate:
    if not isinstance(payload, Mapping):
        return ImportArchitectureGate()
    if _payload_contains_blueprint_import(payload):
        return ImportArchitectureGate()

    hint = _architecture_hint(payload)
    if not hint:
        return ImportArchitectureGate()

    issues: list[ImportArchitectureIssue] = []
    registry_entry = _blueprint_entry_for_hint(hint)
    expected = registry_entry.definition if registry_entry is not None else build_roce_4plane_shuffle_architecture_schema()
    architecture, target_source = _architecture_from_hint(hint, issues)

    slug = _hint_string(hint, 'slug', 'architecture_slug')
    version = _hint_string(hint, 'version', 'architecture_version')
    schema_contract_version = _schema_contract_hint(hint)
    if architecture is not None:
        slug = architecture.slug
        version = architecture.version
        compatibility = compare_persisted_architecture_compatibility(
            architecture,
            expected,
            shuffle_pair_provider=expected.shuffle_pair_provider or shuffle_2x2_transfer_position_pairs,
        )
        issues.extend(_import_compatibility_issues(compatibility.issues))
    else:
        _compare_declared_architecture_hint(
            hint,
            issues=issues,
            expected_slug=expected.slug,
            expected_version=expected.version,
        )

    if registry_entry is not None:
        issues.extend(_import_blueprint_lifecycle_issues(registry_entry))
    _compare_explicit_architecture_contract_hint(hint, issues=issues, expected=expected)
    status = ARCHITECTURE_COMPATIBILITY_COMPATIBLE
    if any(issue.severity == 'error' for issue in issues):
        status = ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE
    elif issues:
        status = ARCHITECTURE_COMPATIBILITY_WARNING

    return ImportArchitectureGate(
        hint_present=True,
        status=status,
        target_source=target_source,
        target_architecture_id=getattr(architecture, 'pk', None),
        target_architecture_slug=slug,
        target_architecture_version=version,
        schema_contract_version=schema_contract_version,
        issues=tuple(issues),
    )


def _architecture_hint(payload: Mapping[str, Any]) -> dict[str, Any]:
    hint: dict[str, Any] = {}
    raw_architecture = (
        payload.get('architecture')
        or payload.get('fabric_architecture')
        or payload.get('architecture_hint')
        or payload.get('architecture_contract')
    )
    if isinstance(raw_architecture, Mapping):
        hint.update(raw_architecture)
    elif raw_architecture not in (None, ''):
        hint['slug'] = str(raw_architecture)

    for payload_key, hint_key in (
        ('architecture_id', 'architecture_id'),
        ('fabric_architecture_id', 'architecture_id'),
        ('architecture_slug', 'slug'),
        ('architecture_version', 'version'),
        ('schema_contract_version', 'schema_contract_version'),
        ('architecture_schema_contract_version', 'schema_contract_version'),
        ('channel_map_matrix', 'channel_map_matrix'),
        ('mpo_position_count', 'mpo_position_count'),
        ('dark_positions', 'dark_positions'),
    ):
        value = payload.get(payload_key, MISSING)
        if value is not MISSING:
            hint[hint_key] = value

    channel_subinterfaces = payload.get('channel_subinterfaces')
    if isinstance(channel_subinterfaces, Mapping) and 'channel_map_matrix' in channel_subinterfaces:
        hint.setdefault('channel_map_matrix', channel_subinterfaces['channel_map_matrix'])
    return {key: value for key, value in hint.items() if value not in (None, '')}


def _architecture_from_hint(
    hint: Mapping[str, Any],
    issues: list[ImportArchitectureIssue],
) -> tuple[FabricArchitecture | None, str]:
    architecture_id = _hint_int(hint, 'id', 'pk', 'architecture_id', 'fabric_architecture_id')
    if architecture_id is not None:
        architecture = FabricArchitecture.objects.filter(pk=architecture_id).first()
        if architecture is None:
            issues.append(
                ImportArchitectureIssue(
                    code='architecture_id_missing',
                    path='architecture.id',
                    message=f'FabricArchitecture #{architecture_id} does not exist.',
                    context={'architecture_id': architecture_id},
                )
            )
            return None, 'payload_id_missing'
        return architecture, 'payload_id'

    slug = _hint_string(hint, 'slug', 'architecture_slug')
    version = _hint_string(hint, 'version', 'architecture_version')
    if slug and version:
        architecture = FabricArchitecture.objects.filter(slug=slug, version=version).first()
        if architecture is not None:
            return architecture, 'payload_slug_version'
    return None, 'payload_hint'


def _blueprint_entry_for_hint(hint: Mapping[str, Any]):
    slug = _hint_string(hint, 'slug', 'architecture_slug')
    version = _hint_string(hint, 'version', 'architecture_version')
    if not slug:
        return None
    try:
        return get_default_blueprint_registry().get_blueprint(slug, version)
    except KeyError:
        return None


def _import_blueprint_lifecycle_issues(entry) -> list[ImportArchitectureIssue]:
    return [
        ImportArchitectureIssue(
            code=issue.code,
            path=issue.path,
            message=issue.message,
            severity=issue.severity,
            context=issue.context,
        )
        for issue in check_blueprint_lifecycle(entry)
    ]


def _payload_contains_blueprint_import(payload: Mapping[str, Any]) -> bool:
    if _is_blueprint_bundle_payload(payload):
        return True
    items = payload.get('items')
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_kind = _optional(item, 'kind', 'object_type', 'model')
        if raw_kind is not MISSING and _normalize_kind(str(raw_kind)) == 'fabric_architecture_blueprint':
            return True
    return False


def _compare_declared_architecture_hint(
    hint: Mapping[str, Any],
    *,
    issues: list[ImportArchitectureIssue],
    expected_slug: str,
    expected_version: str,
) -> None:
    slug = _hint_string(hint, 'slug', 'architecture_slug')
    version = _hint_string(hint, 'version', 'architecture_version')
    if slug and slug != expected_slug:
        issues.append(
            ImportArchitectureIssue(
                code='architecture_slug_mismatch',
                path='architecture.slug',
                message=f'Import payload architecture slug {slug!r} does not match supported slug {expected_slug!r}.',
                context={'actual': slug, 'expected': expected_slug},
            )
        )
    if version and version != expected_version:
        issues.append(
            ImportArchitectureIssue(
                code='architecture_version_mismatch',
                path='architecture.version',
                message=(
                    f'Import payload architecture version {version!r} does not match supported version '
                    f'{expected_version!r}.'
                ),
                context={'actual': version, 'expected': expected_version},
            )
        )


def _compare_explicit_architecture_contract_hint(
    hint: Mapping[str, Any],
    *,
    issues: list[ImportArchitectureIssue],
    expected,
) -> None:
    schema_contract_version = _schema_contract_hint(hint)
    if schema_contract_version and schema_contract_version != ARCHITECTURE_SCHEMA_CONTRACT_VERSION:
        issues.append(
            ImportArchitectureIssue(
                code='schema_contract_version_mismatch',
                path='architecture.schema_contract_version',
                message=(
                    f'Import payload schema contract version {schema_contract_version!r} does not match '
                    f'supported version {ARCHITECTURE_SCHEMA_CONTRACT_VERSION!r}.'
                ),
                context={'actual': schema_contract_version, 'expected': ARCHITECTURE_SCHEMA_CONTRACT_VERSION},
            )
        )

    channel_map_matrix = _channel_map_hint(hint)
    if (
        channel_map_matrix is not None
        and _normalize_channel_map_hint(channel_map_matrix) != _normalize_channel_map_hint(expected.channel_map_matrix)
    ):
        issues.append(
            ImportArchitectureIssue(
                code='channel_map_matrix_mismatch',
                path='architecture.channel_map_matrix',
                message='Import payload channel map matrix does not match the supported architecture contract.',
                context={
                    'actual': _normalize_channel_map_hint(channel_map_matrix),
                    'expected': _normalize_channel_map_hint(expected.channel_map_matrix),
                },
            )
        )

    mpo_position_count = _hint_int(hint, 'mpo_position_count', 'position_count')
    if mpo_position_count is not None and mpo_position_count != expected.mpo_position_count:
        issues.append(
            ImportArchitectureIssue(
                code='mpo_position_count_mismatch',
                path='architecture.mpo_position_count',
                message=(
                    f'Import payload MPO position count {mpo_position_count!r} does not match supported count '
                    f'{expected.mpo_position_count!r}.'
                ),
                context={'actual': mpo_position_count, 'expected': expected.mpo_position_count},
            )
        )

    dark_positions = _int_tuple_hint(hint, 'dark_positions')
    if dark_positions is not None and tuple(sorted(dark_positions)) != tuple(sorted(expected.dark_positions)):
        issues.append(
            ImportArchitectureIssue(
                code='dark_positions_mismatch',
                path='architecture.dark_positions',
                message='Import payload dark MPO positions do not match the supported architecture contract.',
                context={'actual': tuple(sorted(dark_positions)), 'expected': tuple(sorted(expected.dark_positions))},
            )
        )


def _architecture_gate_conflicts(gate: ImportArchitectureGate) -> tuple[ImportDiff, ...]:
    return (
        _conflict(
            index=-1,
            kind='architecture_gate',
            identity=gate.target_architecture_slug or 'architecture',
            reason='architecture preflight failed',
            model=None,
            details={
                'code': 'architecture_preflight_failed',
                'architecture_gate': gate.to_dict(),
            },
        ),
    )


def _import_compatibility_issues(issues: Iterable[Any]) -> list[ImportArchitectureIssue]:
    return [
        ImportArchitectureIssue(
            code=f'persisted.{issue.code}',
            path=issue.path,
            message=issue.message,
            severity=issue.severity,
            context=issue.context,
        )
        for issue in issues
    ]


def _hint_string(hint: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = hint.get(name, MISSING)
        if value in (MISSING, None, ''):
            continue
        if isinstance(value, Mapping):
            value = value.get('slug') or value.get('version') or value.get('name') or value.get('id')
        if value in (None, ''):
            continue
        return str(value).strip()
    return None


def _hint_int(hint: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        value = hint.get(name, MISSING)
        if value in (MISSING, None, ''):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _schema_contract_hint(hint: Mapping[str, Any]) -> str | None:
    direct = _hint_string(hint, 'schema_contract_version')
    if direct:
        return direct
    schema_contract = hint.get('schema_contract')
    if isinstance(schema_contract, Mapping):
        return _hint_string(schema_contract, 'version')
    metadata = hint.get('metadata')
    if isinstance(metadata, Mapping):
        return _schema_contract_hint(metadata)
    return None


def _channel_map_hint(hint: Mapping[str, Any]) -> Sequence[Any] | None:
    matrix = hint.get('channel_map_matrix')
    if isinstance(matrix, Sequence) and not isinstance(matrix, (str, bytes, bytearray)):
        return matrix
    channel_subinterfaces = hint.get('channel_subinterfaces')
    if isinstance(channel_subinterfaces, Mapping):
        matrix = channel_subinterfaces.get('channel_map_matrix')
        if isinstance(matrix, Sequence) and not isinstance(matrix, (str, bytes, bytearray)):
            return matrix
    return None


def _normalize_channel_map_hint(matrix: Sequence[Any]) -> tuple[tuple[Any, Any, tuple[Any, ...]], ...]:
    normalized = []
    for entry in matrix:
        if not isinstance(entry, Mapping):
            normalized.append((None, None, ()))
            continue
        positions = entry.get('positions') or ()
        if not isinstance(positions, Sequence) or isinstance(positions, (str, bytes, bytearray)):
            positions = ()
        normalized.append(
            (
                _integer_or_value(entry.get('subinterface_index')),
                _integer_or_value(entry.get('mpo_index')),
                tuple(_integer_or_value(position) for position in positions),
            )
        )
    return tuple(normalized)


def _int_tuple_hint(hint: Mapping[str, Any], name: str) -> tuple[int, ...] | None:
    raw = hint.get(name, MISSING)
    if raw in (MISSING, None, ''):
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    values = []
    for item in raw:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            return ()
    return tuple(values)


def _integer_or_value(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _build_import_bundle(payload: Any) -> tuple[ImportBundle, tuple[ImportDiff, ...]]:
    items = _payload_items(payload)
    payload_version, source_label = _payload_metadata(payload)
    bundle_items: list[ImportBundleItem] = []
    diffs: list[ImportDiff] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            diffs.append(
                _conflict(
                    index=index,
                    kind='unknown',
                    identity=f'item[{index}]',
                    reason='item must be a JSON object',
                    model=None,
                )
            )
            continue
        raw_kind = _optional(item, 'kind', 'object_type', 'model')
        if raw_kind is MISSING:
            diffs.append(
                _with_item_details(
                    _conflict(
                        index=index,
                        kind='unknown',
                        identity=f'item[{index}]',
                        reason='missing kind',
                        model=None,
                    ),
                    item,
                )
            )
            continue
        kind = _normalize_kind(str(raw_kind))
        handler = HANDLERS.get(kind)
        if handler is None:
            diffs.append(
                _with_item_details(
                    _conflict(
                        index=index,
                        kind=kind,
                        identity=f'item[{index}]',
                        reason=f'unsupported kind {raw_kind}',
                        model=None,
                    ),
                    item,
                )
            )
            continue
        bundle_items.append(
            ImportBundleItem(
                index=index,
                kind=kind,
                data=item,
                produces=_produced_references(kind, item),
                requires=_required_references(kind, item),
            )
        )

    return (
        ImportBundle(
            items=tuple(bundle_items),
            item_count=len(items),
            payload_version=payload_version,
            source_label=source_label,
        ),
        tuple(diffs),
    )


def _reconcile_ordered_items(
    items: Iterable[ImportBundleItem],
    *,
    context: ReconciliationContext,
    diff_by_index: dict[int, ImportDiff],
    apply: bool,
) -> None:
    for item in items:
        handler = HANDLERS[item.kind]
        try:
            diff = handler.reconcile(index=item.index, data=item.data, context=context, apply=apply)
        except ImportRowError as exc:
            diff = handler.conflict(index=item.index, identity=f'item[{item.index}]', reason=str(exc))
        except (IntegrityError, ValidationError) as exc:
            diff = handler.conflict(index=item.index, identity=f'item[{item.index}]', reason=_validation_message(exc))
        diff_by_index[item.index] = _with_item_details(diff, item.data)


def _payload_metadata(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, Mapping):
        return '', ''
    version = _optional(payload, 'payload_version', 'version', 'schema_version', 'bundle_version')
    source = _optional(payload, 'source_label', 'source', 'source_name', 'bundle_author')
    return _label_value(version), _label_value(source)


def _item_provenance(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    provenance = {}
    for field_name in PROVENANCE_FIELDS:
        value = _optional(data, field_name)
        if value is MISSING or value is None or value == '':
            continue
        provenance[field_name] = _serializable_value(value)
    return provenance


def _item_diff_details(data: Mapping[str, Any] | None) -> dict[str, Any]:
    provenance = _item_provenance(data)
    if not provenance:
        return {}
    return {'provenance': provenance}


def _with_item_details(diff: ImportDiff, data: Mapping[str, Any] | None) -> ImportDiff:
    details = _item_diff_details(data)
    if not details:
        return diff
    merged_details = {**dict(diff.details), **details}
    message = diff.message
    provenance = details.get('provenance')
    if provenance:
        message = f'{message} provenance={_provenance_message(provenance)}'
    return replace(diff, message=message, details=merged_details)


def _provenance_message(provenance: Mapping[str, Any]) -> str:
    parts = [
        f'{field_name}={provenance[field_name]}'
        for field_name in PROVENANCE_FIELDS
        if field_name in provenance
    ]
    return ';'.join(parts)


def _merge_provenance_metadata(
    data: Mapping[str, Any],
    desired: dict[str, Any],
    *,
    instance: Model | None,
) -> dict[str, Any]:
    provenance = _item_provenance(data)
    if not provenance:
        return desired

    desired = dict(desired)
    if 'metadata' in desired:
        metadata = dict(desired['metadata'])
    elif instance is not None and isinstance(getattr(instance, 'metadata', None), Mapping):
        metadata = dict(instance.metadata or {})
    else:
        metadata = {}

    existing_namespace = metadata.get(PROVENANCE_METADATA_NAMESPACE)
    namespace = dict(existing_namespace) if isinstance(existing_namespace, Mapping) else {}
    namespace.update(provenance)
    metadata[PROVENANCE_METADATA_NAMESPACE] = namespace
    desired['metadata'] = metadata
    return desired


def _label_value(value: Any) -> str:
    if value in (MISSING, None, ''):
        return ''
    if isinstance(value, Mapping):
        value = value.get('label') or value.get('name') or value.get('slug') or value.get('id')
        if value in (None, ''):
            return ''
    return str(value)


def _plan_bundle_dependencies(bundle: ImportBundle) -> DependencyPlan:
    producers: dict[tuple[str, tuple[Any, ...]], ImportBundleItem] = {}
    for item in bundle.items:
        for reference in item.produces:
            producers.setdefault((reference.kind, reference.key), item)

    edges: list[ImportDependencyEdge] = []
    missing_by_index: dict[int, list[ImportDependencyEdge]] = {}
    planned_edges: list[ImportDependencyEdge] = []
    for item in bundle.items:
        for field_name, reference in item.requires:
            producer = producers.get((reference.kind, reference.key))
            if producer is not None:
                edge = ImportDependencyEdge(
                    dependent_index=item.index,
                    prerequisite_index=producer.index,
                    field=field_name,
                    reference=reference,
                    status=DEPENDENCY_PLANNED,
                )
                planned_edges.append(edge)
            elif _reference_exists(reference):
                edge = ImportDependencyEdge(
                    dependent_index=item.index,
                    field=field_name,
                    reference=reference,
                    status=DEPENDENCY_EXISTING,
                )
            else:
                edge = ImportDependencyEdge(
                    dependent_index=item.index,
                    field=field_name,
                    reference=reference,
                    status=DEPENDENCY_MISSING,
                )
                missing_by_index.setdefault(item.index, []).append(edge)
            edges.append(edge)

    conflicts = [
        _missing_prerequisite_conflict(item, missing_by_index[item.index])
        for item in bundle.items
        if item.index in missing_by_index
    ]

    ordered_items, cycle_conflicts = _topological_items(bundle.items, planned_edges)
    conflicts.extend(cycle_conflicts)

    return DependencyPlan(
        ordered_items=ordered_items,
        apply_order=tuple(item.index for item in ordered_items),
        dependency_edges=tuple(edges),
        conflicts=tuple(conflicts),
    )


def _topological_items(
    items: tuple[ImportBundleItem, ...],
    edges: list[ImportDependencyEdge],
) -> tuple[tuple[ImportBundleItem, ...], list[ImportDiff]]:
    items_by_index = {item.index: item for item in items}
    incoming = {item.index: 0 for item in items}
    outgoing = {item.index: [] for item in items}
    for edge in edges:
        if edge.prerequisite_index is None:
            continue
        if edge.prerequisite_index not in incoming or edge.dependent_index not in incoming:
            continue
        incoming[edge.dependent_index] += 1
        outgoing[edge.prerequisite_index].append(edge.dependent_index)

    ready = sorted(index for index, count in incoming.items() if count == 0)
    ordered_indexes: list[int] = []
    while ready:
        index = ready.pop(0)
        ordered_indexes.append(index)
        for dependent_index in sorted(outgoing[index]):
            incoming[dependent_index] -= 1
            if incoming[dependent_index] == 0:
                ready.append(dependent_index)
                ready.sort()

    blocked_indexes = sorted(index for index, count in incoming.items() if count > 0)
    if not blocked_indexes:
        return tuple(items_by_index[index] for index in ordered_indexes), []

    blocked_edges = [
        edge
        for edge in edges
        if edge.prerequisite_index in blocked_indexes or edge.dependent_index in blocked_indexes
    ]
    cycle_details = {
        'code': 'dependency_cycle',
        'item_indexes': blocked_indexes,
        'dependency_edges': [edge.to_dict() for edge in blocked_edges],
    }
    conflicts = [
        _with_item_details(
            HANDLERS[items_by_index[index].kind].conflict(
                index=index,
                identity=f'item[{index}]',
                reason='dependency cycle',
                details=cycle_details,
            ),
            items_by_index[index].data,
        )
        for index in blocked_indexes
    ]
    ordered = tuple(items_by_index[index] for index in ordered_indexes + blocked_indexes)
    return ordered, conflicts


def _missing_prerequisite_conflict(item: ImportBundleItem, edges: list[ImportDependencyEdge]) -> ImportDiff:
    if len(edges) == 1:
        reference = edges[0].reference
        reason = f'missing {reference.model} {reference.identity}'
    else:
        reason = 'missing prerequisites'
    details = {
        'code': 'missing_prerequisite',
        'missing_prerequisites': [edge.to_dict() for edge in edges],
    }
    return _with_item_details(
        HANDLERS[item.kind].conflict(
            index=item.index,
            identity=f'item[{item.index}]',
            reason=reason,
            details=details,
        ),
        item.data,
    )


def _produced_references(kind: str, data: Mapping[str, Any]) -> tuple[ImportReference, ...]:
    if kind == 'cable_assembly':
        reference = _cable_reference(
            _optional(data, 'site', 'site_slug'),
            _optional(data, 'cable_id'),
        )
    elif kind == 'endpoint':
        reference = _endpoint_reference(
            _optional(data, 'fabric', 'fabric_slug'),
            _optional(data, 'address'),
        )
    elif kind == 'transport_channel':
        reference = _transport_channel_reference(
            _optional(data, 'fabric', 'fabric_slug'),
            _optional(data, 'endpoint', 'endpoint_address'),
            _optional(data, 'channel_index'),
        )
    else:
        reference = None
    return () if reference is None else (reference,)


def _required_references(kind: str, data: Mapping[str, Any]) -> tuple[tuple[str, ImportReference], ...]:
    references: list[tuple[str, ImportReference]] = []
    if kind == 'cable_assembly':
        parent_value = _optional(data, 'parent_cable', 'parent_cable_id')
        if parent_value not in (MISSING, None, ''):
            reference = _parent_cable_reference(data, parent_value)
            if reference is not None:
                references.append(('parent_cable', reference))
    elif kind == 'fiber_strand_cable':
        cable_site = _optional(data, 'cable_site', 'cable_site_slug')
        cable_id = _optional(data, 'cable_id')
        if cable_site not in (MISSING, None, '') and cable_id not in (MISSING, None, ''):
            reference = _cable_reference(cable_site, cable_id)
            if reference is not None:
                references.append(('cable', reference))
    elif kind == 'endpoint':
        parent_value = _optional(data, 'parent', 'parent_address')
        if parent_value not in (MISSING, None, ''):
            reference = _endpoint_reference(
                _optional(data, 'fabric', 'fabric_slug'),
                _endpoint_address_value(parent_value),
            )
            if reference is not None:
                references.append(('parent', reference))
    elif kind == 'transport_channel':
        reference = _endpoint_reference(
            _optional(data, 'fabric', 'fabric_slug'),
            _optional(data, 'endpoint', 'endpoint_address'),
        )
        if reference is not None:
            references.append(('endpoint', reference))
    elif kind == 'transport_channel_position_map':
        channel_reference = _transport_channel_reference(
            _optional(data, 'fabric', 'fabric_slug'),
            _optional(data, 'channel_endpoint', 'endpoint', 'endpoint_address'),
            _optional(data, 'channel_index'),
        )
        if channel_reference is not None:
            references.append(('channel', channel_reference))
        endpoint_reference = _endpoint_reference(
            _optional(data, 'fabric', 'fabric_slug'),
            _optional(data, 'mpo_endpoint', 'mpo_endpoint_address'),
        )
        if endpoint_reference is not None:
            references.append(('mpo_endpoint', endpoint_reference))
    elif kind == 'strand_termination':
        reference = _endpoint_reference(
            _optional(data, 'fabric', 'fabric_slug'),
            _optional(data, 'mpo_endpoint', 'mpo_endpoint_address'),
        )
        if reference is not None:
            references.append(('mpo_endpoint', reference))
    return tuple(references)


def _parent_cable_reference(data: Mapping[str, Any], value: Any) -> ImportReference | None:
    if isinstance(value, Mapping):
        site_value = (
            value.get('site')
            or value.get('site_slug')
            or value.get('slug')
            or _optional(data, 'site', 'site_slug')
        )
        cable_id = value.get('cable_id')
    else:
        site_value = _optional(data, 'site', 'site_slug')
        cable_id = value
    return _cable_reference(site_value, cable_id)


def _cable_reference(site_value: Any, cable_id_value: Any) -> ImportReference | None:
    if site_value in (MISSING, None, '') or cable_id_value in (MISSING, None, ''):
        return None
    site_key, site_label = _site_reference_key(site_value)
    cable_id = str(cable_id_value)
    return ImportReference(
        kind='cable_assembly',
        model='CableAssembly',
        identity=f'site={site_label} cable_id={cable_id}',
        key=(site_key, cable_id),
        lookup={'site': site_label, 'cable_id': cable_id},
    )


def _endpoint_reference(fabric_value: Any, address_value: Any) -> ImportReference | None:
    if fabric_value in (MISSING, None, '') or address_value in (MISSING, None, ''):
        return None
    fabric_key, fabric_label = _fabric_reference_key(fabric_value)
    address = str(address_value)
    return ImportReference(
        kind='endpoint',
        model='Endpoint',
        identity=f'fabric={fabric_label} address={address}',
        key=(fabric_key, address),
        lookup={'fabric': fabric_label, 'address': address},
    )


def _transport_channel_reference(
    fabric_value: Any,
    endpoint_value: Any,
    channel_index_value: Any,
) -> ImportReference | None:
    if fabric_value in (MISSING, None, '') or endpoint_value in (MISSING, None, ''):
        return None
    try:
        channel_index = _int_value(channel_index_value, field_name='channel_index')
    except ImportRowError:
        return None
    fabric_key, fabric_label = _fabric_reference_key(fabric_value)
    endpoint_address = _endpoint_address_value(endpoint_value)
    if endpoint_address in (MISSING, None, ''):
        return None
    endpoint_address = str(endpoint_address)
    return ImportReference(
        kind='transport_channel',
        model='TransportChannel',
        identity=f'fabric={fabric_label} endpoint={endpoint_address} channel={channel_index}',
        key=(fabric_key, endpoint_address, channel_index),
        lookup={'fabric': fabric_label, 'endpoint': endpoint_address, 'channel_index': channel_index},
    )


def _site_reference_key(value: Any) -> tuple[tuple[str, Any], str]:
    if isinstance(value, Mapping):
        value = value.get('slug') or value.get('site_slug') or value.get('site') or value.get('name')
    ref = str(value)
    try:
        site = _resolve_site(ref)
    except ImportRowError:
        return ('site_ref', ref), ref
    return ('site_pk', site.pk), site.slug


def _fabric_reference_key(value: Any) -> tuple[tuple[str, Any], str]:
    if isinstance(value, Mapping):
        value = value.get('slug') or value.get('fabric_slug') or value.get('fabric') or value.get('name')
    ref = str(value)
    try:
        fabric = _resolve_fabric(ref)
    except ImportRowError:
        return ('fabric_ref', ref), ref
    return ('fabric_pk', fabric.pk), fabric.slug


def _endpoint_address_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get('address') or value.get('name')
    return value


def _reference_exists(reference: ImportReference) -> bool:
    if reference.kind == 'cable_assembly':
        site_key, cable_id = reference.key
        if site_key[0] != 'site_pk':
            return False
        return CableAssembly.objects.filter(site_id=site_key[1], cable_id=cable_id).exists()
    if reference.kind == 'endpoint':
        fabric_key, address = reference.key
        if fabric_key[0] != 'fabric_pk':
            return False
        return Endpoint.objects.filter(fabric_id=fabric_key[1], address=address).exists()
    if reference.kind == 'transport_channel':
        fabric_key, endpoint_address, channel_index = reference.key
        if fabric_key[0] != 'fabric_pk':
            return False
        endpoint = Endpoint.objects.filter(fabric_id=fabric_key[1], address=endpoint_address).first()
        if endpoint is None:
            return False
        return TransportChannel.objects.filter(
            fabric_id=fabric_key[1],
            endpoint=endpoint,
            channel_index=channel_index,
        ).exists()
    return False


def _is_blueprint_bundle_payload(payload: Mapping[str, Any]) -> bool:
    if not isinstance(payload, Mapping) or isinstance(payload.get('items'), list):
        return False
    if not any(
        key in payload
        for key in (
            'bundle_version',
            'bundle_author',
            'parameter_schema',
            'required_device_types',
            'stamp_templates',
            'templates',
        )
    ):
        return False
    definition = payload.get('definition') or payload.get('architecture') or payload.get('blueprint') or payload
    return (
        isinstance(definition, Mapping)
        and 'roles' in definition
        and 'transfer_patterns' in definition
        and 'allocation_rule_sets' in definition
    )


def _blueprint_bundle_item(payload: Mapping[str, Any]) -> dict[str, Any]:
    definition = payload.get('definition') or payload.get('architecture') or payload.get('blueprint') or payload
    item = {
        'kind': 'fabric_architecture_blueprint',
        'definition': definition,
        'parameter_schema': payload.get('parameter_schema') or {},
        'required_device_types': payload.get('required_device_types') or {},
        'stamp_templates': payload.get('stamp_templates') or payload.get('templates') or {},
        'schema_contract_version': payload.get('schema_contract_version') or ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
        'bundle': {
            'bundle_version': payload.get('bundle_version') or '',
            'bundle_author': payload.get('bundle_author') or '',
            'schema_contract_version': payload.get('schema_contract_version') or ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
        },
    }
    for key in ('name', 'description', 'status', 'lifecycle', 'successor_version', 'metadata'):
        if key in payload:
            item[key] = payload[key]
    return item


def _blueprint_definition_payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ('definition', 'architecture', 'blueprint'):
        value = data.get(key)
        if isinstance(value, Mapping):
            if key == 'blueprint' and isinstance(value.get('definition'), Mapping):
                return value['definition']
            return value
    return data


def _blueprint_schema_contract_version(data: Mapping[str, Any]) -> str:
    value = _optional(data, 'schema_contract_version', 'architecture_schema_contract_version')
    if value not in (MISSING, None, ''):
        return str(value)
    bundle = data.get('bundle')
    if isinstance(bundle, Mapping):
        value = bundle.get('schema_contract_version')
        if value not in (None, ''):
            return str(value)
    metadata = data.get('metadata')
    if isinstance(metadata, Mapping):
        value = _schema_contract_hint(metadata)
        if value:
            return value
    return ARCHITECTURE_SCHEMA_CONTRACT_VERSION


def _blueprint_architecture_desired_fields(
    *,
    data: Mapping[str, Any],
    definition,
    parameter_schema: Mapping[str, Any],
    required_device_types: Mapping[str, Any],
    lifecycle: str,
    successor_version: str,
    schema_contract_version: str,
) -> dict[str, Any]:
    metadata = dict(data.get('metadata') or {}) if isinstance(data.get('metadata'), Mapping) else {}
    bundle = data.get('bundle') if isinstance(data.get('bundle'), Mapping) else {}
    metadata['schema_contract_version'] = schema_contract_version
    metadata['blueprint'] = {
        'lifecycle': lifecycle,
        'successor_version': successor_version,
        'parameter_schema': dict(parameter_schema),
        'required_device_types': {
            str(role_slug): list(slugs) if isinstance(slugs, Sequence) and not isinstance(slugs, str) else [str(slugs)]
            for role_slug, slugs in required_device_types.items()
        },
        'schema_contract_version': schema_contract_version,
        'definition': architecture_definition_to_payload(definition),
        'bundle': dict(bundle),
    }
    return {
        'name': str(_optional_default(data, 'name', default=_humanize_slug(definition.slug)))[:200],
        'status': str(_optional_default(data, 'status', default='active')),
        'plane_count': definition.plane_count,
        'description': str(_optional_default(data, 'description', default='')),
        'metadata': metadata,
    }


def _blueprint_child_state(
    architecture: FabricArchitecture | None,
    definition,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    desired_roles = _definitions_by_slug(definition.roles)
    desired_patterns = _definitions_by_slug(definition.transfer_patterns)
    desired_rules = _definitions_by_slug(definition.allocation_rule_sets)
    desired_templates = _stamp_templates_by_slug(_normalize_stamp_templates(data))
    existing_roles = _existing_role_definitions(architecture)
    existing_patterns = _existing_transfer_pattern_definitions(architecture)
    existing_rules = _existing_allocation_rule_definitions(architecture)
    existing_templates = _existing_stamp_template_definitions(architecture, desired_templates)

    changed_sections = []
    for section, desired, existing in (
        ('roles', desired_roles, existing_roles),
        ('transfer_patterns', desired_patterns, existing_patterns),
        ('allocation_rule_sets', desired_rules, existing_rules),
        ('stamp_templates', desired_templates, existing_templates),
    ):
        if architecture is None and desired:
            changed_sections.append(section)
        elif desired and desired != {slug: existing.get(slug) for slug in desired}:
            changed_sections.append(section)

    return {
        'has_changes': bool(changed_sections),
        'changed_sections': changed_sections,
        'desired_counts': {
            'roles': len(desired_roles),
            'transfer_patterns': len(desired_patterns),
            'allocation_rule_sets': len(desired_rules),
            'stamp_templates': len(desired_templates),
        },
        'existing_counts': {
            'roles': len(existing_roles),
            'transfer_patterns': len(existing_patterns),
            'allocation_rule_sets': len(existing_rules),
            'stamp_templates': len(existing_templates),
        },
    }


def _apply_blueprint_architecture(
    *,
    architecture: FabricArchitecture | None,
    definition,
    desired: Mapping[str, Any],
    data: Mapping[str, Any],
) -> FabricArchitecture:
    if architecture is None:
        architecture = FabricArchitecture(slug=definition.slug, version=definition.version, **desired)
    else:
        for field_name, value in desired.items():
            setattr(architecture, field_name, value)
    architecture.full_clean()
    architecture.save()
    _sync_architecture_roles(architecture, definition.roles)
    _sync_transfer_patterns(architecture, definition.transfer_patterns)
    _sync_allocation_rule_sets(architecture, definition.allocation_rule_sets)
    _sync_stamp_templates(architecture, _normalize_stamp_templates(data))
    return architecture


def _sync_architecture_roles(architecture: FabricArchitecture, roles: Sequence[Mapping[str, Any]]) -> None:
    for role in roles:
        ArchitectureRole.objects.update_or_create(
            architecture=architecture,
            slug=str(role['slug']),
            defaults={
                'name': role.get('name') or role['slug'],
                'role_kind': role.get('role_kind') or '',
                'description': role.get('description') or '',
                'metadata': dict(role.get('metadata') or {}),
            },
        )


def _sync_transfer_patterns(architecture: FabricArchitecture, patterns: Sequence[Mapping[str, Any]]) -> None:
    for pattern in patterns:
        TransferPattern.objects.update_or_create(
            architecture=architecture,
            slug=str(pattern['slug']),
            defaults={
                'name': pattern.get('name') or pattern['slug'],
                'pattern_kind': pattern.get('pattern_kind') or 'custom',
                'rule': dict(pattern.get('rule') or {}),
                'metadata': dict(pattern.get('metadata') or {}),
            },
        )


def _sync_allocation_rule_sets(architecture: FabricArchitecture, rule_sets: Sequence[Mapping[str, Any]]) -> None:
    for rule_set in rule_sets:
        AllocationRuleSet.objects.update_or_create(
            architecture=architecture,
            slug=str(rule_set['slug']),
            defaults={
                'name': rule_set.get('name') or rule_set['slug'],
                'rule': dict(rule_set.get('rule') or {}),
                'metadata': dict(rule_set.get('metadata') or {}),
            },
        )


def _sync_stamp_templates(architecture: FabricArchitecture, templates: Sequence[Mapping[str, Any]]) -> None:
    for template in templates:
        metadata = dict(template.get('metadata') or {})
        metadata.setdefault('blueprint_import', True)
        StampTemplate.objects.update_or_create(
            slug=str(template['slug']),
            defaults={
                'architecture': architecture,
                'name': template.get('name') or template['slug'],
                'description': template.get('description') or '',
                'template': dict(template.get('template') or {}),
                'metadata': metadata,
            },
        )


def _normalize_stamp_templates(data: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_templates = _optional(data, 'stamp_templates', 'templates')
    if raw_templates in (MISSING, None, ''):
        return ()
    normalized: list[dict[str, Any]] = []
    if isinstance(raw_templates, Mapping):
        iterable = raw_templates.items()
    elif isinstance(raw_templates, Sequence) and not isinstance(raw_templates, (str, bytes, bytearray)):
        iterable = ((None, item) for item in raw_templates)
    else:
        raise ImportRowError('stamp_templates must be an object or array')

    for key, raw_template in iterable:
        if not isinstance(raw_template, Mapping):
            raise ImportRowError('stamp_templates entries must be objects')
        slug = str(raw_template.get('slug') or key or '')
        if not slug:
            raise ImportRowError('stamp_templates entries require a slug')
        template_body = raw_template.get('template')
        if template_body is None:
            template_body = {
                item_key: item_value
                for item_key, item_value in raw_template.items()
                if item_key not in {'slug', 'name', 'description', 'metadata'}
            }
        if not isinstance(template_body, Mapping):
            raise ImportRowError(f'stamp_templates.{slug}.template must be an object')
        normalized.append(
            {
                'slug': slug,
                'name': str(raw_template.get('name') or _humanize_slug(slug)),
                'description': str(raw_template.get('description') or ''),
                'template': dict(template_body),
                'metadata': dict(raw_template.get('metadata') or {}),
            }
        )
    return tuple(normalized)


def _definitions_by_slug(definitions: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(definition.get('slug')): _normalize_definition(definition) for definition in definitions}


def _stamp_templates_by_slug(templates: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(template.get('slug')): _normalize_definition(template) for template in templates}


def _existing_role_definitions(architecture: FabricArchitecture | None) -> dict[str, dict[str, Any]]:
    if architecture is None:
        return {}
    return {
        role.slug: _normalize_definition(
            {
                'slug': role.slug,
                'name': role.name,
                'role_kind': role.role_kind,
                'description': role.description,
                'metadata': role.metadata or {},
            }
        )
        for role in architecture.roles.all()
    }


def _existing_transfer_pattern_definitions(architecture: FabricArchitecture | None) -> dict[str, dict[str, Any]]:
    if architecture is None:
        return {}
    return {
        pattern.slug: _normalize_definition(
            {
                'slug': pattern.slug,
                'name': pattern.name,
                'pattern_kind': pattern.pattern_kind,
                'rule': pattern.rule or {},
                'metadata': pattern.metadata or {},
            }
        )
        for pattern in architecture.transfer_patterns.all()
    }


def _existing_allocation_rule_definitions(architecture: FabricArchitecture | None) -> dict[str, dict[str, Any]]:
    if architecture is None:
        return {}
    return {
        rule_set.slug: _normalize_definition(
            {
                'slug': rule_set.slug,
                'name': rule_set.name,
                'rule': rule_set.rule or {},
                'metadata': rule_set.metadata or {},
            }
        )
        for rule_set in architecture.allocation_rule_sets.all()
    }


def _existing_stamp_template_definitions(
    architecture: FabricArchitecture | None,
    desired_templates: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if architecture is None or not desired_templates:
        return {}
    return {
        template.slug: _normalize_definition(
            {
                'slug': template.slug,
                'name': template.name,
                'description': template.description,
                'template': template.template or {},
                'metadata': template.metadata or {},
            }
        )
        for template in StampTemplate.objects.filter(architecture=architecture, slug__in=desired_templates.keys())
    }


def _normalize_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    return _serializable_value(dict(value))


def _humanize_slug(slug: str) -> str:
    return str(slug).replace('-', ' ').replace('_', ' ').title()


def _payload_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        if _is_blueprint_bundle_payload(payload):
            return [_blueprint_bundle_item(payload)]
        items = payload.get('items')
        if isinstance(items, list):
            return items
        raise ValueError('Import payload must contain an items list.')
    raise ValueError('Import payload must be a JSON object with items or a list of items.')


def _normalize_kind(value: str) -> str:
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', value).replace('-', '_').replace('.', '_').lower()
    snake = re.sub(r'__+', '_', snake).strip('_')
    return KIND_ALIASES.get(snake, snake)


def _reconcile_model(
    *,
    index: int,
    kind: str,
    identity: str,
    model: type[Model],
    instance: Model | None,
    create_kwargs: dict[str, Any],
    desired: dict[str, Any],
    apply: bool,
    create_allowed: bool = True,
) -> ImportDiff:
    if instance is None:
        if not create_allowed:
            return _conflict(index=index, kind=kind, identity=identity, reason=f'missing {model.__name__}', model=model)
        candidate = model(**create_kwargs, **desired)
        try:
            candidate.full_clean()
            if apply:
                candidate.save()
        except (IntegrityError, ValidationError) as exc:
            return _conflict(index=index, kind=kind, identity=identity, reason=_validation_message(exc), model=model)
        return _diff(
            index=index,
            kind=kind,
            identity=identity,
            outcome=OUTCOME_CREATE,
            model=model,
            object_id=getattr(candidate, 'pk', None),
        )

    changes = tuple(
        FieldChange(field=name, current=getattr(instance, name), desired=value)
        for name, value in desired.items()
        if not _values_equal(getattr(instance, name), value)
    )
    if not changes:
        return _diff(
            index=index,
            kind=kind,
            identity=identity,
            outcome=OUTCOME_SKIP,
            model=model,
            object_id=instance.pk,
        )

    for change in changes:
        setattr(instance, change.field, change.desired)
    try:
        instance.full_clean()
        if apply:
            instance.save()
    except (IntegrityError, ValidationError) as exc:
        return _conflict(index=index, kind=kind, identity=identity, reason=_validation_message(exc), model=model)
    return _diff(
        index=index,
        kind=kind,
        identity=identity,
        outcome=OUTCOME_UPDATE,
        model=model,
        object_id=instance.pk,
        changes=changes,
    )


def _diff(
    *,
    index: int,
    kind: str,
    identity: str,
    outcome: str,
    model: type[Model] | None,
    object_id: int | None = None,
    changes: tuple[FieldChange, ...] = (),
) -> ImportDiff:
    if outcome == OUTCOME_UPDATE:
        fields = ','.join(change.field for change in changes)
        message = f'{outcome} {kind} {identity} fields={fields}'
    else:
        message = f'{outcome} {kind} {identity}'
    return ImportDiff(
        index=index,
        kind=kind,
        identity=identity,
        outcome=outcome,
        message=message,
        changes=changes,
        object_model=model.__name__ if model is not None else '',
        object_id=object_id,
    )


def _conflict(
    *,
    index: int,
    kind: str,
    identity: str,
    reason: str,
    model: type[Model] | None,
    details: Mapping[str, Any] | None = None,
) -> ImportDiff:
    message = f'{OUTCOME_CONFLICT} {kind} {identity} reason={reason}'
    return ImportDiff(
        index=index,
        kind=kind,
        identity=identity,
        outcome=OUTCOME_CONFLICT,
        message=message,
        object_model=model.__name__ if model is not None else '',
        error=reason,
        details={} if details is None else details,
    )


def _desired_fields(data: Mapping[str, Any], field_names: Mapping[str, tuple[str, ...]]) -> dict[str, Any]:
    desired = {}
    for target, names in field_names.items():
        value = _optional(data, *names)
        if value is MISSING:
            continue
        if target == 'metadata':
            desired[target] = _mapping_value(value, field_name=target)
        elif target in {'position_count', 'speed_gbps', 'termination_index'}:
            desired[target] = _int_value(
                value,
                field_name=target,
                allow_none=target in {'speed_gbps', 'termination_index'},
            )
        else:
            desired[target] = '' if value is None else value
    return desired


def _required(data: Mapping[str, Any], *names: str) -> Any:
    value = _optional(data, *names)
    if value is MISSING or value == '':
        raise ImportRowError(f'missing required field {names[0]}')
    return value


def _required_string(data: Mapping[str, Any], *names: str) -> str:
    return str(_required(data, *names))


def _optional_string(data: Mapping[str, Any], *names: str, default: str) -> str:
    value = _optional(data, *names)
    if value is MISSING or value is None:
        return default
    return str(value)


def _required_int(data: Mapping[str, Any], *names: str) -> int:
    return _int_value(_required(data, *names), field_name=names[0])


def _int_value(value: Any, *, field_name: str, allow_none: bool = False) -> int | None:
    if allow_none and value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ImportRowError(f'{field_name} must be an integer') from exc


def _mapping_value(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ImportRowError(f'{field_name} must be an object')
    return dict(value)


def _optional(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    fields = data.get('fields')
    if isinstance(fields, Mapping):
        for name in names:
            if name in fields:
                return fields[name]
    return MISSING


def _optional_default(data: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    value = _optional(data, *names)
    if value is MISSING or value is None or value == '':
        return default
    return value


def _has_any(data: Mapping[str, Any], *names: str) -> bool:
    return _optional(data, *names) is not MISSING


def _resolve_site(value: Any) -> Site:
    if isinstance(value, Mapping):
        value = value.get('slug') or value.get('name')
    ref = str(value)
    site = Site.objects.filter(slug=ref).first() or Site.objects.filter(name=ref).first()
    if site is None:
        raise ImportRowError(f'missing Site {ref}')
    return site


def _resolve_fabric(value: Any) -> Fabric:
    if isinstance(value, Mapping):
        value = value.get('slug') or value.get('name')
    ref = str(value)
    fabric = Fabric.objects.filter(slug=ref).first() or Fabric.objects.filter(name=ref).first()
    if fabric is None:
        raise ImportRowError(f'missing Fabric {ref}')
    return fabric


def _resolve_node(fabric: Fabric, value: Any) -> FabricNode:
    if isinstance(value, Mapping):
        value = value.get('address') or value.get('name')
    ref = str(value)
    node = FabricNode.objects.filter(fabric=fabric, address=ref).first()
    if node is None:
        raise ImportRowError(f'missing FabricNode fabric={fabric.slug} address={ref}')
    return node


def _resolve_endpoint(fabric: Fabric, value: Any) -> Endpoint:
    if isinstance(value, Mapping):
        value = value.get('address') or value.get('name')
    ref = str(value)
    endpoint = Endpoint.objects.filter(fabric=fabric, address=ref).first()
    if endpoint is None:
        raise ImportRowError(f'missing Endpoint fabric={fabric.slug} address={ref}')
    return endpoint


def _resolve_optional_endpoint(fabric: Fabric, value: Any) -> Endpoint | None:
    if value in (MISSING, None, ''):
        return None
    return _resolve_endpoint(fabric, value)


def _resolve_optional_plane(fabric: Fabric, value: Any) -> Plane | None:
    if value in (MISSING, None, ''):
        return None
    if isinstance(value, Mapping):
        if value.get('plane_number') is not None:
            value = value['plane_number']
        else:
            value = value.get('label')
    plane = None
    if isinstance(value, int) or str(value).isdigit():
        plane = Plane.objects.filter(fabric=fabric, plane_number=int(value)).first()
    if plane is None:
        plane = Plane.objects.filter(fabric=fabric, label=str(value)).first()
    if plane is None:
        raise ImportRowError(f'missing Plane fabric={fabric.slug} ref={value}')
    return plane


def _resolve_connector_position(endpoint: Endpoint, position_number: int) -> ConnectorPosition:
    position = ConnectorPosition.objects.filter(endpoint=endpoint, position_number=position_number).first()
    if position is None:
        raise ImportRowError(f'missing ConnectorPosition endpoint={endpoint.address} position={position_number}')
    return position


def _resolve_segment(fabric: Fabric, value: Any) -> FiberSegment:
    if isinstance(value, Mapping):
        value = value.get('name')
    ref = str(value)
    matches = list(FiberSegment.objects.filter(fabric=fabric, name=ref)[:2])
    if not matches:
        raise ImportRowError(f'missing FiberSegment fabric={fabric.slug} name={ref}')
    if len(matches) > 1:
        raise ImportRowError(f'ambiguous FiberSegment fabric={fabric.slug} name={ref}')
    return matches[0]


def _resolve_optional_cable_ref(value: Any, *, default_site: Site) -> CableAssembly | None:
    if value in (MISSING, None, ''):
        return None
    if isinstance(value, Mapping):
        site = _resolve_site(value.get('site') or value.get('site_slug') or default_site.slug)
        cable_id = value.get('cable_id')
    else:
        site = default_site
        cable_id = value
    if not cable_id:
        raise ImportRowError('parent_cable cable_id is required')
    cable = CableAssembly.objects.filter(site=site, cable_id=str(cable_id)).first()
    if cable is None:
        raise ImportRowError(f'missing parent CableAssembly site={site.slug} cable_id={cable_id}')
    return cable


def _values_equal(current: Any, desired: Any) -> bool:
    if isinstance(current, Model) or isinstance(desired, Model):
        return _model_identity(current) == _model_identity(desired)
    return current == desired


def _model_identity(value: Any) -> tuple[str, int | None] | None:
    if value is None:
        return None
    if not isinstance(value, Model):
        return None
    return (value._meta.label_lower, value.pk)


def _serializable_value(value: Any) -> Any:
    if isinstance(value, Model):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serializable_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable_value(child) for child in value]
    return value


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        if hasattr(exc, 'message_dict'):
            return '; '.join(
                f'{field}: {", ".join(str(message) for message in messages)}'
                for field, messages in exc.message_dict.items()
            )
        return '; '.join(str(message) for message in exc.messages)
    return str(exc)
