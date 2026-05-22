from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


PositionPairProvider = Callable[..., Sequence[tuple[int, int]]]

ROLE_KIND_VALUES = frozenset(
    {
        'active_device',
        'active_device_group',
        'active_port',
        'active_subconnector',
        'passive_assembly',
        'passive_connector',
    }
)
TRANSFER_PATTERN_KIND_VALUES = frozenset(
    {
        'identity',
        'polarity_swap',
        'shuffle_2x2',
        'stagger',
        'breakout',
        'custom',
    }
)

ARCHITECTURE_SCHEMA_CONTRACT_VERSION = 'v2'

ARCHITECTURE_COMPATIBILITY_COMPATIBLE = 'compatible'
ARCHITECTURE_COMPATIBILITY_WARNING = 'warning'
ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE = 'incompatible'


@dataclass(frozen=True)
class ArchitectureSchemaError:
    code: str
    path: str
    message: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f'{self.path}: {self.message}'


@dataclass(frozen=True)
class ArchitectureSchemaValidationResult:
    errors: tuple[ArchitectureSchemaError, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(str(error) for error in self.errors)

    def errors_for_code(self, code: str) -> tuple[ArchitectureSchemaError, ...]:
        return tuple(error for error in self.errors if error.code == code)


@dataclass(frozen=True)
class ArchitectureSchemaDefinition:
    slug: str
    version: str
    plane_count: int
    roles: Sequence[Mapping[str, Any]]
    transfer_patterns: Sequence[Mapping[str, Any]]
    allocation_rule_sets: Sequence[Mapping[str, Any]]
    channel_map_matrix: Sequence[Mapping[str, Any]]
    active_position_groups: Mapping[str, Sequence[int]]
    dark_positions: Sequence[int]
    mpo_position_count: int
    shuffle_mpo_groups: Sequence[Sequence[int]]
    shuffle_pair_provider: PositionPairProvider | None = None
    channels_per_subinterface: int = 4
    mpo_count_per_osfp: int = 2


@dataclass(frozen=True)
class ArchitectureCompatibilityIssue:
    code: str
    path: str
    message: str
    severity: str = 'error'
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchitectureCompatibilityResult:
    issues: tuple[ArchitectureCompatibilityIssue, ...] = ()

    @property
    def status(self) -> str:
        if any(issue.severity == 'error' for issue in self.issues):
            return ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE
        if self.issues:
            return ARCHITECTURE_COMPATIBILITY_WARNING
        return ARCHITECTURE_COMPATIBILITY_COMPATIBLE

    @property
    def is_compatible(self) -> bool:
        return self.status != ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE

    @property
    def warnings(self) -> tuple[ArchitectureCompatibilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'warning')

    @property
    def errors(self) -> tuple[ArchitectureCompatibilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'error')

    def issues_for_code(self, code: str) -> tuple[ArchitectureCompatibilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == code)


@dataclass(frozen=True)
class _PersistedArchitectureSchemaBuildResult:
    definition: ArchitectureSchemaDefinition | None
    errors: tuple[ArchitectureSchemaError, ...] = ()


class _Errors:
    def __init__(self) -> None:
        self.items: list[ArchitectureSchemaError] = []

    def add(self, code: str, path: str, message: str, **context: Any) -> None:
        self.items.append(ArchitectureSchemaError(code=code, path=path, message=message, context=context))

    def result(self) -> ArchitectureSchemaValidationResult:
        return ArchitectureSchemaValidationResult(errors=tuple(self.items))


def validate_architecture_schema(definition: ArchitectureSchemaDefinition) -> ArchitectureSchemaValidationResult:
    """
    Validate architecture fixture semantics without database writes.

    The validator is intentionally narrow: it checks the roles, transfer patterns,
    allocation rules, channel map, MPO position state, and 2x2 shuffle invariants
    that the V2 stamping/resolution services assume are true.
    """
    errors = _Errors()

    _require_string(definition.slug, 'slug', errors)
    _require_string(definition.version, 'version', errors)
    _require_positive_int(definition.plane_count, 'plane_count', errors)
    position_count = _require_positive_int(definition.mpo_position_count, 'mpo_position_count', errors)
    channel_width = _require_positive_int(
        definition.channels_per_subinterface,
        'channels_per_subinterface',
        errors,
    )
    mpo_count = _require_positive_int(definition.mpo_count_per_osfp, 'mpo_count_per_osfp', errors)
    if position_count is None or channel_width is None or mpo_count is None:
        return errors.result()

    active_groups, active_positions, dark_positions = _validate_position_groups(
        definition.active_position_groups,
        definition.dark_positions,
        position_count=position_count,
        channel_width=channel_width,
        errors=errors,
    )
    shuffle_groups = _validate_shuffle_mpo_groups(definition.shuffle_mpo_groups, errors)

    _validate_roles(definition.roles, position_count=position_count, errors=errors)
    _validate_channel_map(
        definition.channel_map_matrix,
        position_count=position_count,
        channel_width=channel_width,
        mpo_count=mpo_count,
        active_positions=active_positions,
        dark_positions=dark_positions,
        errors=errors,
    )
    _validate_transfer_patterns(
        definition.transfer_patterns,
        active_groups=active_groups,
        position_count=position_count,
        shuffle_groups=shuffle_groups,
        pair_provider=definition.shuffle_pair_provider,
        errors=errors,
    )
    _validate_allocation_rules(
        definition.allocation_rule_sets,
        channel_map_matrix=definition.channel_map_matrix,
        errors=errors,
    )
    return errors.result()


def validate_persisted_architecture_schema(
    architecture: Any,
    *,
    shuffle_pair_provider: PositionPairProvider | None = None,
) -> ArchitectureSchemaValidationResult:
    """
    Validate a persisted FabricArchitecture row and its related contract rows.

    Conversion failures are returned as ArchitectureSchemaError objects with
    persisted_architecture.* codes so callers can render malformed rows without
    throwing a server error.
    """
    build = _build_schema_definition_from_persisted_architecture(
        architecture,
        shuffle_pair_provider=shuffle_pair_provider,
    )
    if build.errors:
        return ArchitectureSchemaValidationResult(errors=build.errors)
    return validate_architecture_schema(build.definition)


def compare_persisted_architecture_compatibility(
    architecture: Any,
    expected: ArchitectureSchemaDefinition,
    *,
    expected_schema_contract_version: str | None = ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
    shuffle_pair_provider: PositionPairProvider | None = None,
) -> ArchitectureCompatibilityResult:
    """
    Compare a persisted architecture row against an expected schema contract.

    The helper is intentionally narrow: it compares slug, architecture version,
    optional schema contract version, channel map matrix, MPO position count, and
    dark positions. Missing optional metadata is warning-only; unreadable schema
    data or contract mismatches are incompatible.
    """
    issues: list[ArchitectureCompatibilityIssue] = []
    build = _build_schema_definition_from_persisted_architecture(
        architecture,
        shuffle_pair_provider=shuffle_pair_provider,
    )
    for error in build.errors:
        issues.append(
            ArchitectureCompatibilityIssue(
                code=f'persisted_schema.{error.code}',
                path=error.path,
                message=error.message,
                severity='error',
                context=error.context,
            )
        )
    if build.definition is None:
        return ArchitectureCompatibilityResult(issues=tuple(issues))

    actual = build.definition
    validation_result = validate_architecture_schema(actual)
    for error in validation_result.errors:
        issues.append(
            ArchitectureCompatibilityIssue(
                code=f'schema.{error.code}',
                path=error.path,
                message=error.message,
                severity='error',
                context=error.context,
            )
        )

    if actual.slug != expected.slug:
        issues.append(
            ArchitectureCompatibilityIssue(
                code='architecture_slug_mismatch',
                path='architecture.slug',
                message=f'Architecture slug {actual.slug!r} does not match expected slug {expected.slug!r}.',
                context={'actual': actual.slug, 'expected': expected.slug},
            )
        )
    if actual.version != expected.version:
        issues.append(
            ArchitectureCompatibilityIssue(
                code='architecture_version_mismatch',
                path='architecture.version',
                message=(
                    f'Architecture version {actual.version!r} does not match expected version '
                    f'{expected.version!r}.'
                ),
                context={'actual': actual.version, 'expected': expected.version},
            )
        )

    if expected_schema_contract_version is not None:
        actual_contract_version = _schema_contract_version_from_metadata(getattr(architecture, 'metadata', {}))
        if actual_contract_version is None:
            issues.append(
                ArchitectureCompatibilityIssue(
                    code='schema_contract_version_missing',
                    path='architecture.metadata.schema_contract_version',
                    message='Architecture metadata does not declare a schema contract version.',
                    severity='warning',
                    context={'expected': expected_schema_contract_version},
                )
            )
        elif actual_contract_version != expected_schema_contract_version:
            issues.append(
                ArchitectureCompatibilityIssue(
                    code='schema_contract_version_mismatch',
                    path='architecture.metadata.schema_contract_version',
                    message=(
                        f'Schema contract version {actual_contract_version!r} does not match expected version '
                        f'{expected_schema_contract_version!r}.'
                    ),
                    context={'actual': actual_contract_version, 'expected': expected_schema_contract_version},
                )
            )

    if _normalize_channel_map(actual.channel_map_matrix) != _normalize_channel_map(expected.channel_map_matrix):
        issues.append(
            ArchitectureCompatibilityIssue(
                code='channel_map_matrix_mismatch',
                path='architecture.channel_map_matrix',
                message='Persisted architecture channel map matrix does not match the expected contract.',
                context={
                    'actual': _normalize_channel_map(actual.channel_map_matrix),
                    'expected': _normalize_channel_map(expected.channel_map_matrix),
                },
            )
        )
    if actual.mpo_position_count != expected.mpo_position_count:
        issues.append(
            ArchitectureCompatibilityIssue(
                code='mpo_position_count_mismatch',
                path='architecture.mpo_position_count',
                message=(
                    f'MPO position count {actual.mpo_position_count!r} does not match expected count '
                    f'{expected.mpo_position_count!r}.'
                ),
                context={'actual': actual.mpo_position_count, 'expected': expected.mpo_position_count},
            )
        )
    actual_dark_positions = tuple(sorted(actual.dark_positions))
    expected_dark_positions = tuple(sorted(expected.dark_positions))
    if actual_dark_positions != expected_dark_positions:
        issues.append(
            ArchitectureCompatibilityIssue(
                code='dark_positions_mismatch',
                path='architecture.dark_positions',
                message='Persisted architecture dark MPO positions do not match the expected contract.',
                context={'actual': actual_dark_positions, 'expected': expected_dark_positions},
            )
        )

    return ArchitectureCompatibilityResult(issues=tuple(issues))


def _build_schema_definition_from_persisted_architecture(
    architecture: Any,
    *,
    shuffle_pair_provider: PositionPairProvider | None = None,
) -> _PersistedArchitectureSchemaBuildResult:
    errors = _Errors()
    roles = tuple(_role_definition_from_model(role) for role in _related_items(architecture, 'roles', errors))
    transfer_patterns = tuple(
        _transfer_pattern_definition_from_model(pattern)
        for pattern in _related_items(architecture, 'transfer_patterns', errors)
    )
    allocation_rule_sets = tuple(
        _allocation_rule_definition_from_model(rule_set)
        for rule_set in _related_items(architecture, 'allocation_rule_sets', errors)
    )

    channel_map_matrix = _channel_map_matrix_from_persisted_rules(allocation_rule_sets, errors)
    active_position_groups = _active_position_groups_from_persisted_transfer_patterns(transfer_patterns, errors)
    mpo_position_count = _mpo_position_count_from_persisted_contract(
        roles,
        transfer_patterns,
        allocation_rule_sets,
        errors,
    )
    channels_per_subinterface = _channels_per_subinterface_from_persisted_contract(
        active_position_groups,
        channel_map_matrix,
        errors,
    )
    mpo_count_per_osfp = _mpo_count_per_osfp_from_persisted_contract(
        roles,
        allocation_rule_sets,
        channel_map_matrix,
        errors,
    )
    shuffle_mpo_groups = _shuffle_mpo_groups_from_persisted_transfer_patterns(transfer_patterns)

    if errors.items:
        return _PersistedArchitectureSchemaBuildResult(definition=None, errors=tuple(errors.items))

    dark_positions = _dark_positions_from_active_groups(
        active_position_groups,
        position_count=mpo_position_count,
    )
    definition = ArchitectureSchemaDefinition(
        slug=getattr(architecture, 'slug', None),
        version=getattr(architecture, 'version', None),
        plane_count=getattr(architecture, 'plane_count', None),
        roles=roles,
        transfer_patterns=transfer_patterns,
        allocation_rule_sets=allocation_rule_sets,
        channel_map_matrix=channel_map_matrix,
        active_position_groups=active_position_groups,
        dark_positions=dark_positions,
        mpo_position_count=mpo_position_count,
        shuffle_mpo_groups=shuffle_mpo_groups,
        shuffle_pair_provider=shuffle_pair_provider,
        channels_per_subinterface=channels_per_subinterface,
        mpo_count_per_osfp=mpo_count_per_osfp,
    )
    return _PersistedArchitectureSchemaBuildResult(definition=definition)


def _related_items(architecture: Any, related_name: str, errors: _Errors) -> tuple[Any, ...]:
    relation = getattr(architecture, related_name, None)
    if relation is None:
        errors.add(
            'persisted_architecture.missing_relation',
            f'architecture.{related_name}',
            f'Persisted architecture does not expose related {related_name}.',
        )
        return ()
    if hasattr(relation, 'all'):
        items = relation.all()
        if hasattr(items, 'order_by'):
            try:
                items = items.order_by('slug', 'pk')
            except Exception:
                items = items.order_by('slug')
        return tuple(items)
    if _is_sequence(relation):
        return tuple(relation)
    errors.add(
        'persisted_architecture.relation_type',
        f'architecture.{related_name}',
        f'Persisted architecture related {related_name} must be a related manager or sequence.',
    )
    return ()


def _role_definition_from_model(role: Any) -> dict[str, Any]:
    return {
        'slug': getattr(role, 'slug', None),
        'name': getattr(role, 'name', None),
        'role_kind': getattr(role, 'role_kind', None),
        'description': getattr(role, 'description', ''),
        'metadata': getattr(role, 'metadata', {}),
    }


def _transfer_pattern_definition_from_model(pattern: Any) -> dict[str, Any]:
    return {
        'slug': getattr(pattern, 'slug', None),
        'name': getattr(pattern, 'name', None),
        'pattern_kind': getattr(pattern, 'pattern_kind', None),
        'rule': getattr(pattern, 'rule', {}),
        'metadata': getattr(pattern, 'metadata', {}),
    }


def _allocation_rule_definition_from_model(rule_set: Any) -> dict[str, Any]:
    return {
        'slug': getattr(rule_set, 'slug', None),
        'name': getattr(rule_set, 'name', None),
        'rule': getattr(rule_set, 'rule', {}),
        'metadata': getattr(rule_set, 'metadata', {}),
    }


def _channel_map_matrix_from_persisted_rules(
    allocation_rule_sets: Sequence[Mapping[str, Any]],
    errors: _Errors,
) -> tuple[Any, ...]:
    for rule_set in allocation_rule_sets:
        if rule_set.get('slug') != 'channel_subinterface_mapping':
            continue
        rule = rule_set.get('rule')
        if not isinstance(rule, Mapping):
            errors.add(
                'persisted_architecture.channel_map_rule_type',
                'allocation_rule_sets[channel_subinterface_mapping].rule',
                'channel_subinterface_mapping rule must be an object before it can be converted.',
            )
            return ()
        matrix = rule.get('channel_map_matrix')
        if not _is_sequence(matrix):
            errors.add(
                'persisted_architecture.channel_map_missing',
                'allocation_rule_sets[channel_subinterface_mapping].rule.channel_map_matrix',
                'Persisted architecture is missing channel_subinterface_mapping.rule.channel_map_matrix.',
            )
            return ()
        return tuple(matrix)

    errors.add(
        'persisted_architecture.channel_map_rule_missing',
        'allocation_rule_sets.channel_subinterface_mapping',
        'Persisted architecture is missing the channel_subinterface_mapping allocation rule set.',
    )
    return ()


def _active_position_groups_from_persisted_transfer_patterns(
    transfer_patterns: Sequence[Mapping[str, Any]],
    errors: _Errors,
) -> dict[str, Any]:
    active_group_specs: list[tuple[str, Mapping[str, Any]]] = []
    for pattern in transfer_patterns:
        if pattern.get('slug') != 'shuffle_2x2' and pattern.get('pattern_kind') != 'shuffle_2x2':
            continue
        pattern_path = f'transfer_patterns[{pattern.get("slug") or "shuffle_2x2"}].rule'
        rule = pattern.get('rule')
        if not isinstance(rule, Mapping):
            errors.add(
                'persisted_architecture.shuffle_rule_type',
                pattern_path,
                'shuffle_2x2 transfer pattern rule must be an object before it can be converted.',
            )
            continue
        groups = rule.get('groups')
        if not _is_sequence(groups):
            errors.add(
                'persisted_architecture.shuffle_groups_missing',
                f'{pattern_path}.groups',
                'shuffle_2x2 transfer pattern must include groups before it can be converted.',
            )
            continue
        for group_index, group in enumerate(groups, start=1):
            group_path = f'{pattern_path}.groups[{group_index}]'
            if not isinstance(group, Mapping):
                errors.add(
                    'persisted_architecture.shuffle_group_type',
                    group_path,
                    'shuffle_2x2 transfer pattern groups must be objects before they can be converted.',
                )
                continue
            group_positions = group.get('active_position_groups')
            if not isinstance(group_positions, Mapping):
                errors.add(
                    'persisted_architecture.active_position_groups_missing',
                    f'{group_path}.active_position_groups',
                    'shuffle_2x2 group is missing active_position_groups.',
                )
                continue
            active_group_specs.append((f'{group_path}.active_position_groups', group_positions))

    if not active_group_specs:
        errors.add(
            'persisted_architecture.active_position_groups_missing',
            'transfer_patterns[shuffle_2x2].rule.groups.active_position_groups',
            'Persisted architecture does not expose active MPO position groups.',
        )
        return {}

    canonical_path, canonical_groups = active_group_specs[0]
    normalized = _normalize_active_position_groups(canonical_groups)
    for path, group_spec in active_group_specs[1:]:
        if _normalize_active_position_groups(group_spec) != normalized:
            errors.add(
                'persisted_architecture.active_position_groups_conflict',
                path,
                f'Active position groups do not match {canonical_path}.',
            )
    return dict(normalized)


def _mpo_position_count_from_persisted_contract(
    roles: Sequence[Mapping[str, Any]],
    transfer_patterns: Sequence[Mapping[str, Any]],
    allocation_rule_sets: Sequence[Mapping[str, Any]],
    errors: _Errors,
) -> int | None:
    candidates: list[tuple[str, int]] = []
    for role in roles:
        metadata = role.get('metadata')
        if not isinstance(metadata, Mapping) or metadata.get('connector_kind') != 'mpo-12':
            continue
        _append_positive_int_candidate(
            candidates,
            metadata.get('position_count'),
            f'roles[{role.get("slug")}].metadata.position_count',
            'persisted_architecture.mpo_position_count_type',
            errors,
        )

    for pattern in transfer_patterns:
        if pattern.get('slug') != 'shuffle_2x2' and pattern.get('pattern_kind') != 'shuffle_2x2':
            continue
        rule = pattern.get('rule')
        if not isinstance(rule, Mapping) or not _is_sequence(rule.get('groups')):
            continue
        for group_index, group in enumerate(rule['groups'], start=1):
            if not isinstance(group, Mapping):
                continue
            transform = group.get('rear_position_transform')
            if not isinstance(transform, Mapping):
                continue
            _append_positive_int_candidate(
                candidates,
                transform.get('position_count'),
                (
                    f'transfer_patterns[{pattern.get("slug")}].rule.groups[{group_index}].'
                    'rear_position_transform.position_count'
                ),
                'persisted_architecture.mpo_position_count_type',
                errors,
            )

    for rule_set in allocation_rule_sets:
        if rule_set.get('slug') != 'gb300_osfp_mpo_order':
            continue
        rule = rule_set.get('rule')
        if not isinstance(rule, Mapping):
            continue
        _append_positive_int_candidate(
            candidates,
            rule.get('positions_per_mpo'),
            'allocation_rule_sets[gb300_osfp_mpo_order].rule.positions_per_mpo',
            'persisted_architecture.mpo_position_count_type',
            errors,
        )

    return _unique_positive_int_candidate(
        candidates,
        missing_code='persisted_architecture.mpo_position_count_missing',
        conflict_code='persisted_architecture.mpo_position_count_conflict',
        path='architecture.mpo_position_count',
        label='MPO position count',
        errors=errors,
    )


def _channels_per_subinterface_from_persisted_contract(
    active_position_groups: Mapping[str, Any],
    channel_map_matrix: Sequence[Any],
    errors: _Errors,
) -> int | None:
    candidates: list[tuple[str, int]] = []
    for label, positions in active_position_groups.items():
        if _is_sequence(positions):
            candidates.append((f'active_position_groups.{label}', len(tuple(positions))))
    for index, entry in enumerate(channel_map_matrix, start=1):
        if isinstance(entry, Mapping) and _is_sequence(entry.get('positions')):
            candidates.append((f'channel_map_matrix[{index}].positions', len(tuple(entry['positions']))))
    if candidates:
        return candidates[0][1]
    errors.add(
        'persisted_architecture.channel_width_missing',
        'architecture.channels_per_subinterface',
        'Persisted architecture does not expose enough data to infer channels_per_subinterface.',
    )
    return None


def _mpo_count_per_osfp_from_persisted_contract(
    roles: Sequence[Mapping[str, Any]],
    allocation_rule_sets: Sequence[Mapping[str, Any]],
    channel_map_matrix: Sequence[Any],
    errors: _Errors,
) -> int | None:
    candidates: list[tuple[str, int]] = []
    for role in roles:
        metadata = role.get('metadata')
        if not isinstance(metadata, Mapping) or metadata.get('connector_kind') != 'osfp':
            continue
        _append_positive_int_candidate(
            candidates,
            metadata.get('mpo_children'),
            f'roles[{role.get("slug")}].metadata.mpo_children',
            'persisted_architecture.mpo_count_per_osfp_type',
            errors,
        )

    for rule_set in allocation_rule_sets:
        if rule_set.get('slug') != 'gb300_osfp_mpo_order':
            continue
        rule = rule_set.get('rule')
        if not isinstance(rule, Mapping):
            continue
        _append_positive_int_candidate(
            candidates,
            rule.get('mpo_per_osfp'),
            'allocation_rule_sets[gb300_osfp_mpo_order].rule.mpo_per_osfp',
            'persisted_architecture.mpo_count_per_osfp_type',
            errors,
        )

    max_mpo_index = 0
    for entry in channel_map_matrix:
        if not isinstance(entry, Mapping):
            continue
        mpo_index = entry.get('mpo_index')
        if isinstance(mpo_index, int) and not isinstance(mpo_index, bool) and mpo_index > max_mpo_index:
            max_mpo_index = mpo_index
    if max_mpo_index:
        candidates.append(('channel_map_matrix[].mpo_index', max_mpo_index))

    return _unique_positive_int_candidate(
        candidates,
        missing_code='persisted_architecture.mpo_count_per_osfp_missing',
        conflict_code='persisted_architecture.mpo_count_per_osfp_conflict',
        path='architecture.mpo_count_per_osfp',
        label='MPO count per OSFP',
        errors=errors,
    )


def _shuffle_mpo_groups_from_persisted_transfer_patterns(
    transfer_patterns: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Any, ...], ...]:
    groups: list[tuple[Any, ...]] = []
    for pattern in transfer_patterns:
        if pattern.get('slug') != 'shuffle_2x2' and pattern.get('pattern_kind') != 'shuffle_2x2':
            continue
        rule = pattern.get('rule')
        if not isinstance(rule, Mapping) or not _is_sequence(rule.get('groups')):
            continue
        for group in rule['groups']:
            if not isinstance(group, Mapping):
                continue
            front_mpos = group.get('front_mpos')
            if _is_sequence(front_mpos):
                groups.append(tuple(front_mpos))
    return tuple(groups)


def _dark_positions_from_active_groups(
    active_position_groups: Mapping[str, Any],
    *,
    position_count: int,
) -> tuple[int, ...]:
    active_positions: set[int] = set()
    for positions in active_position_groups.values():
        if not _is_sequence(positions):
            continue
        for position in positions:
            if isinstance(position, int) and not isinstance(position, bool) and position > 0:
                active_positions.add(position)
    return tuple(position for position in range(1, position_count + 1) if position not in active_positions)


def _normalize_active_position_groups(groups: Mapping[str, Any]) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    normalized = []
    for label, positions in groups.items():
        if _is_sequence(positions):
            position_tuple = tuple(positions)
        else:
            position_tuple = (positions,)
        normalized.append((str(label), position_tuple))
    return tuple(sorted(normalized, key=lambda item: item[0]))


def _append_positive_int_candidate(
    candidates: list[tuple[str, int]],
    value: Any,
    path: str,
    code: str,
    errors: _Errors,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.add(code, path, f'{path} must be a positive integer when present.')
        return
    candidates.append((path, value))


def _unique_positive_int_candidate(
    candidates: Sequence[tuple[str, int]],
    *,
    missing_code: str,
    conflict_code: str,
    path: str,
    label: str,
    errors: _Errors,
) -> int | None:
    if not candidates:
        errors.add(missing_code, path, f'Persisted architecture does not expose {label}.')
        return None
    values = {value for _, value in candidates}
    if len(values) > 1:
        errors.add(
            conflict_code,
            path,
            f'Persisted architecture exposes conflicting values for {label}.',
            candidates=tuple({'path': candidate_path, 'value': value} for candidate_path, value in candidates),
        )
        return None
    return candidates[0][1]


def _schema_contract_version_from_metadata(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    version = metadata.get('schema_contract_version')
    if isinstance(version, str) and version.strip():
        return version
    schema = metadata.get('schema_contract')
    if isinstance(schema, Mapping):
        version = schema.get('version')
        if isinstance(version, str) and version.strip():
            return version
    return None


def _validate_roles(roles: Any, *, position_count: int, errors: _Errors) -> None:
    items = _sequence(roles, 'roles', errors)
    if items is None:
        return
    if not items:
        errors.add('roles.empty', 'roles', 'Architecture roles must not be empty.')
        return

    seen: set[str] = set()
    for index, role in enumerate(items, start=1):
        path = f'roles[{index}]'
        if not isinstance(role, Mapping):
            errors.add('roles.entry_type', path, 'Role definitions must be objects.')
            continue

        slug = _require_string(role.get('slug'), f'{path}.slug', errors)
        _require_string(role.get('name'), f'{path}.name', errors)
        role_kind = _require_string(role.get('role_kind'), f'{path}.role_kind', errors)
        if slug in seen:
            errors.add('roles.duplicate_slug', f'{path}.slug', f'Role slug {slug!r} is defined more than once.')
        if slug:
            seen.add(slug)
        if role_kind and role_kind not in ROLE_KIND_VALUES:
            errors.add(
                'roles.unknown_kind',
                f'{path}.role_kind',
                f'Role kind {role_kind!r} is not part of the architecture schema contract.',
            )

        metadata = role.get('metadata', {})
        if not isinstance(metadata, Mapping):
            errors.add('roles.metadata_type', f'{path}.metadata', 'Role metadata must be an object.')
            continue
        if metadata.get('connector_kind') == 'mpo-12' and metadata.get('position_count') != position_count:
            errors.add(
                'roles.mpo_position_count_mismatch',
                f'{path}.metadata.position_count',
                f'MPO-12 roles must declare position_count={position_count}.',
            )


def _validate_position_groups(
    active_group_spec: Any,
    dark_position_spec: Any,
    *,
    position_count: int,
    channel_width: int,
    errors: _Errors,
) -> tuple[dict[str, tuple[int, ...]], frozenset[int], frozenset[int]]:
    active_groups: dict[str, tuple[int, ...]] = {}
    active_owner: dict[int, str] = {}

    if not isinstance(active_group_spec, Mapping):
        errors.add(
            'mpo_positions.active_groups_type',
            'active_position_groups',
            'Active position groups must be an object keyed by group label.',
        )
        active_group_spec = {}
    if not active_group_spec:
        errors.add(
            'mpo_positions.active_groups_empty',
            'active_position_groups',
            'Active position groups are required.',
        )

    for raw_label, raw_positions in active_group_spec.items():
        label = str(raw_label)
        path = f'active_position_groups.{label}'
        positions = _position_tuple(raw_positions, path, position_count=position_count, errors=errors)
        if positions is None:
            continue
        if len(positions) != channel_width:
            errors.add(
                'mpo_positions.active_group_width',
                path,
                f'Active position group {label!r} must contain exactly {channel_width} positions.',
            )
        for position_index, position in enumerate(positions, start=1):
            if position in active_owner:
                errors.add(
                    'mpo_positions.active_overlap',
                    f'{path}[{position_index}]',
                    (
                        f'Position {position} is declared active in both group '
                        f'{active_owner[position]} and group {label}.'
                    ),
                )
            active_owner.setdefault(position, label)
        active_groups[label] = positions

    dark_positions = _position_tuple(dark_position_spec, 'dark_positions', position_count=position_count, errors=errors)
    dark_positions = dark_positions or ()
    dark_seen: set[int] = set()
    for index, position in enumerate(dark_positions, start=1):
        path = f'dark_positions[{index}]'
        if position in dark_seen:
            errors.add('mpo_positions.duplicate_dark', path, f'Dark position {position} is listed more than once.')
        dark_seen.add(position)
        if position in active_owner:
            errors.add(
                'mpo_positions.active_dark_overlap',
                path,
                f'Position {position} is declared active in group {active_owner[position]} and dark.',
            )

    active_set = frozenset(active_owner)
    dark_set = frozenset(dark_seen)
    expected_positions = frozenset(range(1, position_count + 1))
    declared_positions = active_set | dark_set
    if declared_positions != expected_positions:
        errors.add(
            'mpo_positions.coverage',
            'active_position_groups',
            'Every MPO position must be declared either active or dark.',
            missing_positions=sorted(expected_positions - declared_positions),
            extra_positions=sorted(declared_positions - expected_positions),
        )

    return active_groups, active_set, dark_set


def _validate_channel_map(
    matrix: Any,
    *,
    position_count: int,
    channel_width: int,
    mpo_count: int,
    active_positions: frozenset[int],
    dark_positions: frozenset[int],
    errors: _Errors,
) -> None:
    entries = _sequence(matrix, 'channel_map_matrix', errors)
    if entries is None:
        return
    if not entries:
        errors.add('channel_map.empty', 'channel_map_matrix', 'Channel map matrix must not be empty.')
        return

    subinterfaces: set[int] = set()
    lanes: dict[tuple[int, int], int] = {}
    positions_by_mpo: dict[int, set[int]] = {}
    for entry_index, entry in enumerate(entries, start=1):
        path = f'channel_map_matrix[{entry_index}]'
        if not isinstance(entry, Mapping):
            errors.add('channel_map.entry_type', path, 'Channel map entries must be objects.')
            continue
        subinterface = _require_positive_int(entry.get('subinterface_index'), f'{path}.subinterface_index', errors)
        mpo = _require_positive_int(entry.get('mpo_index'), f'{path}.mpo_index', errors)
        positions = _position_tuple(
            entry.get('positions'),
            f'{path}.positions',
            position_count=position_count,
            errors=errors,
        )

        if subinterface in subinterfaces:
            errors.add(
                'channel_map.duplicate_subinterface',
                f'{path}.subinterface_index',
                f'Subinterface index {subinterface} is defined more than once.',
            )
        if subinterface is not None:
            subinterfaces.add(subinterface)
        if mpo is not None and mpo > mpo_count:
            errors.add(
                'channel_map.mpo_index_out_of_range',
                f'{path}.mpo_index',
                f'MPO index {mpo} exceeds mpo_count_per_osfp={mpo_count}.',
            )
        if positions is None or mpo is None:
            continue
        if len(positions) != channel_width:
            errors.add(
                'channel_map.channel_width',
                f'{path}.positions',
                f'Each channel map entry must contain exactly {channel_width} positions.',
            )

        for position_index, position in enumerate(positions, start=1):
            position_path = f'{path}.positions[{position_index}]'
            if position in dark_positions:
                errors.add(
                    'channel_map.dark_position',
                    position_path,
                    f'Position {position} is dark and cannot be assigned to a transport channel.',
                )
            if active_positions and position not in active_positions:
                errors.add(
                    'channel_map.inactive_position',
                    position_path,
                    f'Position {position} is not declared in an active MPO position group.',
                )
            lane = (mpo, position)
            if lane in lanes:
                errors.add(
                    'channel_map.duplicate_lane',
                    position_path,
                    f'Entry {entry_index} duplicates MPO {mpo} position {position} from entry {lanes[lane]}.',
                )
            else:
                lanes[lane] = entry_index
            positions_by_mpo.setdefault(mpo, set()).add(position)

    expected_subinterfaces = set(range(1, len(entries) + 1))
    if subinterfaces != expected_subinterfaces:
        errors.add(
            'channel_map.subinterface_sequence',
            'channel_map_matrix',
            f'Subinterface indexes must be contiguous from 1 to {len(entries)}.',
        )
    for mpo in range(1, mpo_count + 1):
        missing = sorted(active_positions - positions_by_mpo.get(mpo, set()))
        if missing:
            errors.add(
                'channel_map.missing_active_positions',
                f'channel_map_matrix.mpo[{mpo}]',
                f'MPO {mpo} does not map all active positions.',
                missing_positions=missing,
            )


def _validate_transfer_patterns(
    transfer_patterns: Any,
    *,
    active_groups: Mapping[str, tuple[int, ...]],
    position_count: int,
    shuffle_groups: tuple[tuple[int, int], ...],
    pair_provider: PositionPairProvider | None,
    errors: _Errors,
) -> None:
    patterns = _sequence(transfer_patterns, 'transfer_patterns', errors)
    if patterns is None:
        return
    if not patterns:
        errors.add('transfer_patterns.empty', 'transfer_patterns', 'Transfer patterns must not be empty.')
        return

    seen: set[str] = set()
    for index, pattern in enumerate(patterns, start=1):
        path = f'transfer_patterns[{index}]'
        if not isinstance(pattern, Mapping):
            errors.add('transfer_patterns.entry_type', path, 'Transfer pattern definitions must be objects.')
            continue
        slug = _require_string(pattern.get('slug'), f'{path}.slug', errors)
        if slug:
            path = f'transfer_patterns[{slug}]'
            if slug in seen:
                errors.add(
                    'transfer_patterns.duplicate_slug',
                    f'{path}.slug',
                    f'Transfer pattern slug {slug!r} is defined more than once.',
                )
            seen.add(slug)
        _require_string(pattern.get('name'), f'{path}.name', errors)
        pattern_kind = _require_string(pattern.get('pattern_kind'), f'{path}.pattern_kind', errors)
        rule = pattern.get('rule')
        if pattern_kind and pattern_kind not in TRANSFER_PATTERN_KIND_VALUES:
            errors.add(
                'transfer_patterns.unknown_kind',
                f'{path}.pattern_kind',
                f'Transfer pattern kind {pattern_kind!r} is not supported by the schema contract.',
            )
        if not isinstance(rule, Mapping):
            errors.add('transfer_patterns.rule_type', f'{path}.rule', 'Transfer pattern rule must be an object.')
            continue

        if pattern_kind == 'identity':
            _validate_identity_rule(rule, f'{path}.rule', errors)
        elif pattern_kind == 'stagger':
            _validate_stagger_rule(rule, f'{path}.rule', errors)
        elif pattern_kind == 'shuffle_2x2':
            _validate_shuffle_rule(
                rule,
                path,
                active_groups=active_groups,
                position_count=position_count,
                shuffle_groups=shuffle_groups,
                pair_provider=pair_provider,
                errors=errors,
            )


def _validate_identity_rule(rule: Mapping[str, Any], path: str, errors: _Errors) -> None:
    if rule.get('type') != 'position_map':
        errors.add('identity.rule_type', f'{path}.type', 'Identity transfer rule.type must be "position_map".')
    if rule.get('mode') != 'identity':
        errors.add('identity.mode', f'{path}.mode', 'Identity transfer rule.mode must be "identity".')


def _validate_stagger_rule(rule: Mapping[str, Any], path: str, errors: _Errors) -> None:
    if rule.get('type') != 'allocation_transform':
        errors.add('stagger.rule_type', f'{path}.type', 'Stagger transfer rule.type must be "allocation_transform".')
    _require_string(rule.get('scope'), f'{path}.scope', errors)
    members = _positive_int_tuple(rule.get('staggered_members'), f'{path}.staggered_members', errors)
    if members and len(set(members)) != len(members):
        errors.add('stagger.duplicate_members', f'{path}.staggered_members', 'Staggered members must be unique.')


def _validate_shuffle_rule(
    rule: Mapping[str, Any],
    path: str,
    *,
    active_groups: Mapping[str, tuple[int, ...]],
    position_count: int,
    shuffle_groups: tuple[tuple[int, int], ...],
    pair_provider: PositionPairProvider | None,
    errors: _Errors,
) -> None:
    rule_path = f'{path}.rule'
    if rule.get('type') != 'position_map':
        errors.add('shuffle_2x2.rule_type', f'{rule_path}.type', '2x2 shuffle rule.type must be "position_map".')
    if not isinstance(rule.get('bidirectional'), bool):
        errors.add(
            'shuffle_2x2.bidirectional_type',
            f'{rule_path}.bidirectional',
            '2x2 shuffle rule.bidirectional must be a boolean.',
        )
    groups = _sequence(rule.get('groups'), f'{rule_path}.groups', errors)
    if groups is None:
        return

    for group_index, group in enumerate(groups, start=1):
        group_path = f'{rule_path}.groups[{group_index}]'
        if not isinstance(group, Mapping):
            errors.add('shuffle_2x2.group_type', group_path, '2x2 shuffle groups must be objects.')
            continue
        front_mpos = _positive_int_tuple(group.get('front_mpos'), f'{group_path}.front_mpos', errors)
        rear_mpos = _positive_int_tuple(group.get('rear_mpos'), f'{group_path}.rear_mpos', errors)
        if front_mpos and len(front_mpos) != 2:
            errors.add('shuffle_2x2.front_mpo_count', f'{group_path}.front_mpos', '2x2 shuffle needs two front MPOs.')
        if rear_mpos and len(rear_mpos) != 2:
            errors.add('shuffle_2x2.rear_mpo_count', f'{group_path}.rear_mpos', '2x2 shuffle needs two rear MPOs.')

        transform = group.get('rear_position_transform')
        if not isinstance(transform, Mapping):
            errors.add(
                'shuffle_2x2.transform_type',
                f'{group_path}.rear_position_transform',
                '2x2 shuffle rear_position_transform must be an object.',
            )
        else:
            if transform.get('type') != 'key_down_roll':
                errors.add(
                    'shuffle_2x2.transform_kind',
                    f'{group_path}.rear_position_transform.type',
                    '2x2 shuffle rear_position_transform.type must be "key_down_roll".',
                )
            transform_count = _require_positive_int(
                transform.get('position_count'),
                f'{group_path}.rear_position_transform.position_count',
                errors,
            )
            if transform_count is not None and transform_count != position_count:
                errors.add(
                    'shuffle_2x2.transform_position_count',
                    f'{group_path}.rear_position_transform.position_count',
                    f'2x2 shuffle transform position_count must be {position_count}.',
                )

        group_positions = group.get('active_position_groups')
        if not isinstance(group_positions, Mapping):
            errors.add(
                'shuffle_2x2.active_groups_type',
                f'{group_path}.active_position_groups',
                '2x2 shuffle active_position_groups must be an object.',
            )
        else:
            _validate_shuffle_position_groups(
                group_positions,
                f'{group_path}.active_position_groups',
                active_groups=active_groups,
                errors=errors,
            )

        matrix = _sequence(group.get('matrix'), f'{group_path}.matrix', errors)
        if matrix is None or front_mpos is None or rear_mpos is None:
            continue
        _validate_shuffle_matrix(
            matrix,
            f'{group_path}.matrix',
            front_mpos=front_mpos,
            rear_mpos=rear_mpos,
            active_groups=active_groups,
            errors=errors,
        )
        if pair_provider is not None:
            _validate_shuffle_pair_provider(
                f'{group_path}.matrix',
                active_groups=active_groups,
                position_count=position_count,
                shuffle_groups=shuffle_groups,
                pair_provider=pair_provider,
                errors=errors,
            )


def _validate_shuffle_position_groups(
    groups: Mapping[str, Any],
    path: str,
    *,
    active_groups: Mapping[str, tuple[int, ...]],
    errors: _Errors,
) -> None:
    if set(groups) != set(active_groups):
        errors.add(
            'shuffle_2x2.active_group_labels',
            path,
            '2x2 shuffle active_position_groups must use the canonical active group labels.',
        )
        return
    for label, canonical_positions in active_groups.items():
        if tuple(groups.get(label, ())) != canonical_positions:
            errors.add(
                'shuffle_2x2.active_group_mismatch',
                f'{path}.{label}',
                f'2x2 shuffle group {label!r} does not match the canonical active position group.',
            )


def _validate_shuffle_matrix(
    matrix: Sequence[Any],
    path: str,
    *,
    front_mpos: tuple[int, ...],
    rear_mpos: tuple[int, ...],
    active_groups: Mapping[str, tuple[int, ...]],
    errors: _Errors,
) -> None:
    if len(front_mpos) != 2 or len(rear_mpos) != 2 or len(active_groups) != 2:
        return

    first_label, second_label = tuple(active_groups)
    expected = {
        (front_mpos[0], rear_mpos[0]): (first_label, first_label),
        (front_mpos[0], rear_mpos[1]): (second_label, first_label),
        (front_mpos[1], rear_mpos[0]): (first_label, second_label),
        (front_mpos[1], rear_mpos[1]): (second_label, second_label),
    }
    seen: set[tuple[int, int]] = set()
    for row_index, row in enumerate(matrix, start=1):
        row_path = f'{path}[{row_index}]'
        if not isinstance(row, Mapping):
            errors.add('shuffle_2x2.matrix_entry_type', row_path, '2x2 shuffle matrix rows must be objects.')
            continue
        front_mpo = _require_positive_int(row.get('front_mpo'), f'{row_path}.front_mpo', errors)
        rear_mpo = _require_positive_int(row.get('rear_mpo'), f'{row_path}.rear_mpo', errors)
        src_group = _require_string(row.get('src_group'), f'{row_path}.src_group', errors)
        dst_group = _require_string(row.get('dst_group'), f'{row_path}.dst_group', errors)
        if src_group and src_group not in active_groups:
            errors.add(
                'shuffle_2x2.matrix_unknown_group',
                f'{row_path}.src_group',
                f'Source group {src_group!r} is not declared in active_position_groups.',
            )
        if dst_group and dst_group not in active_groups:
            errors.add(
                'shuffle_2x2.matrix_unknown_group',
                f'{row_path}.dst_group',
                f'Destination group {dst_group!r} is not declared in active_position_groups.',
            )
        if None in (front_mpo, rear_mpo) or src_group is None or dst_group is None:
            continue
        crossing = (front_mpo, rear_mpo)
        if crossing in seen:
            errors.add(
                'shuffle_2x2.matrix_duplicate_crossing',
                row_path,
                f'Front MPO {front_mpo} to rear MPO {rear_mpo} is defined more than once.',
            )
        seen.add(crossing)

        expected_groups = expected.get(crossing)
        if expected_groups is not None and (src_group, dst_group) != expected_groups:
            expected_src, expected_dst = expected_groups
            errors.add(
                'shuffle_2x2.matrix_transform',
                row_path,
                (
                    f'Front MPO {front_mpo} to rear MPO {rear_mpo} must map source group '
                    f'{expected_src} to destination group {expected_dst}.'
                ),
            )

    missing = sorted(expected.keys() - seen)
    if missing:
        errors.add(
            'shuffle_2x2.matrix_missing_crossing',
            path,
            '2x2 shuffle matrix must define every front/rear crossing exactly once.',
            missing_crossings=missing,
        )


def _validate_shuffle_pair_provider(
    path: str,
    *,
    active_groups: Mapping[str, tuple[int, ...]],
    position_count: int,
    shuffle_groups: tuple[tuple[int, int], ...],
    pair_provider: PositionPairProvider,
    errors: _Errors,
) -> None:
    if len(active_groups) != 2:
        return
    first_label, second_label = tuple(active_groups)
    expected_by_offset = {
        (0, 0): (first_label, first_label),
        (0, 1): (second_label, first_label),
        (1, 0): (first_label, second_label),
        (1, 1): (second_label, second_label),
    }
    for group_index, group in enumerate(shuffle_groups, start=1):
        for front_offset, front_index in enumerate(group):
            for rear_offset, rear_index in enumerate(group):
                src_group, dst_group = expected_by_offset[(front_offset, rear_offset)]
                expected_pairs = _rolled_pairs(
                    active_groups[src_group],
                    active_groups[dst_group],
                    position_count=position_count,
                )
                helper_path = f'{path}.shuffle_mpo_groups[{group_index}].front_{front_index}.rear_{rear_index}'
                actual_pairs = _call_pair_provider(pair_provider, front_index, rear_index, helper_path, errors)
                if actual_pairs is not None and actual_pairs != expected_pairs:
                    errors.add(
                        'shuffle_2x2.helper_mismatch',
                        helper_path,
                        '2x2 shuffle helper does not match expected key-down-roll pairs.',
                        actual=actual_pairs,
                        expected=expected_pairs,
                    )

    for front_group_index, front_group in enumerate(shuffle_groups, start=1):
        for rear_group_index, rear_group in enumerate(shuffle_groups, start=1):
            if front_group_index == rear_group_index:
                continue
            helper_path = f'{path}.shuffle_mpo_groups[{front_group_index}].to_group[{rear_group_index}]'
            actual_pairs = _call_pair_provider(pair_provider, front_group[0], rear_group[0], helper_path, errors)
            if actual_pairs:
                errors.add(
                    'shuffle_2x2.helper_cross_group',
                    helper_path,
                    '2x2 shuffle helper must not return pairs across independent MPO groups.',
                )


def _validate_shuffle_mpo_groups(raw_groups: Any, errors: _Errors) -> tuple[tuple[int, int], ...]:
    groups = _sequence(raw_groups, 'shuffle_mpo_groups', errors)
    if groups is None:
        return ()
    normalized: list[tuple[int, int]] = []
    seen: set[int] = set()
    for index, group_spec in enumerate(groups, start=1):
        path = f'shuffle_mpo_groups[{index}]'
        group = _positive_int_tuple(group_spec, path, errors)
        if group is None:
            continue
        if len(group) != 2:
            errors.add('shuffle_mpo_groups.group_size', path, 'Each 2x2 shuffle MPO group must contain two MPOs.')
            continue
        for mpo in group:
            if mpo in seen:
                errors.add('shuffle_mpo_groups.overlap', path, f'MPO {mpo} belongs to more than one shuffle group.')
            seen.add(mpo)
        normalized.append(group)
    return tuple(normalized)


def _validate_allocation_rules(
    allocation_rule_sets: Any,
    *,
    channel_map_matrix: Sequence[Mapping[str, Any]],
    errors: _Errors,
) -> None:
    rule_sets = _sequence(allocation_rule_sets, 'allocation_rule_sets', errors)
    if rule_sets is None:
        return
    if not rule_sets:
        errors.add('allocation_rules.empty', 'allocation_rule_sets', 'Allocation rule sets must not be empty.')
        return

    seen: set[str] = set()
    found_channel_map = False
    for index, rule_set in enumerate(rule_sets, start=1):
        path = f'allocation_rule_sets[{index}]'
        if not isinstance(rule_set, Mapping):
            errors.add('allocation_rules.entry_type', path, 'Allocation rule sets must be objects.')
            continue
        slug = _require_string(rule_set.get('slug'), f'{path}.slug', errors)
        if slug:
            path = f'allocation_rule_sets[{slug}]'
            if slug in seen:
                errors.add(
                    'allocation_rules.duplicate_slug',
                    f'{path}.slug',
                    f'Allocation rule set slug {slug!r} is defined more than once.',
                )
            seen.add(slug)
        _require_string(rule_set.get('name'), f'{path}.name', errors)
        rule = rule_set.get('rule')
        if not isinstance(rule, Mapping):
            errors.add('allocation_rules.rule_type', f'{path}.rule', 'Allocation rule set rule must be an object.')
            continue
        if slug == 'channel_subinterface_mapping':
            found_channel_map = True
            _validate_channel_map_rule(rule, f'{path}.rule', channel_map_matrix=channel_map_matrix, errors=errors)

    if not found_channel_map:
        errors.add(
            'allocation_rules.missing_channel_map',
            'allocation_rule_sets',
            'Architecture must include a channel_subinterface_mapping allocation rule set.',
        )


def _validate_channel_map_rule(
    rule: Mapping[str, Any],
    path: str,
    *,
    channel_map_matrix: Sequence[Mapping[str, Any]],
    errors: _Errors,
) -> None:
    _require_positive_int(rule.get('speed_gbps'), f'{path}.speed_gbps', errors)
    _require_string(rule.get('parent_interface_scope'), f'{path}.parent_interface_scope', errors)
    child_name_pattern = _require_string(rule.get('child_name_pattern'), f'{path}.child_name_pattern', errors)
    if child_name_pattern and '{channel_index}' not in child_name_pattern:
        errors.add(
            'allocation_rules.child_name_pattern',
            f'{path}.child_name_pattern',
            'Channel subinterface child_name_pattern must include {channel_index}.',
        )
    rule_matrix = rule.get('channel_map_matrix')
    if not _is_sequence(rule_matrix):
        errors.add(
            'allocation_rules.channel_map_type',
            f'{path}.channel_map_matrix',
            'Channel subinterface allocation rule must include a channel_map_matrix sequence.',
        )
        return
    if _normalize_channel_map(rule_matrix) != _normalize_channel_map(channel_map_matrix):
        errors.add(
            'allocation_rules.channel_map_mismatch',
            f'{path}.channel_map_matrix',
            'Channel subinterface allocation rule must match the architecture channel map matrix.',
        )


def _require_string(value: Any, path: str, errors: _Errors) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.add('schema.string_required', path, f'{path} must be a non-empty string.')
        return None
    return value


def _require_positive_int(value: Any, path: str, errors: _Errors) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.add('schema.positive_int_required', path, f'{path} must be a positive integer.')
        return None
    return value


def _sequence(value: Any, path: str, errors: _Errors) -> tuple[Any, ...] | None:
    if not _is_sequence(value):
        errors.add('schema.sequence_required', path, f'{path} must be a sequence.')
        return None
    return tuple(value)


def _positive_int_tuple(value: Any, path: str, errors: _Errors) -> tuple[int, ...] | None:
    values = _sequence(value, path, errors)
    if values is None:
        return None
    normalized: list[int] = []
    for index, item in enumerate(values, start=1):
        integer = _require_positive_int(item, f'{path}[{index}]', errors)
        if integer is not None:
            normalized.append(integer)
    return tuple(normalized)


def _position_tuple(value: Any, path: str, *, position_count: int, errors: _Errors) -> tuple[int, ...] | None:
    positions = _positive_int_tuple(value, path, errors)
    if positions is None:
        return None
    seen: set[int] = set()
    for index, position in enumerate(positions, start=1):
        position_path = f'{path}[{index}]'
        if position > position_count:
            errors.add(
                'mpo_positions.position_out_of_range',
                position_path,
                f'Position {position} exceeds MPO position_count={position_count}.',
            )
        if position in seen:
            errors.add(
                'mpo_positions.duplicate_position',
                position_path,
                f'Position {position} is listed more than once.',
            )
        seen.add(position)
    return positions


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _rolled_pairs(
    src_positions: Sequence[int],
    base_dst_positions: Sequence[int],
    *,
    position_count: int,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (src_position, position_count + 1 - base_dst_position)
        for src_position, base_dst_position in zip(src_positions, base_dst_positions)
    )


def _call_pair_provider(
    pair_provider: PositionPairProvider,
    front_index: int,
    rear_index: int,
    path: str,
    errors: _Errors,
) -> tuple[tuple[int, int], ...] | None:
    try:
        return tuple(
            (int(src_position), int(dst_position))
            for src_position, dst_position in pair_provider(front_index=front_index, rear_index=rear_index)
        )
    except Exception as exc:
        errors.add('shuffle_2x2.helper_error', path, f'2x2 shuffle helper raised {exc.__class__.__name__}: {exc}')
        return None


def _normalize_channel_map(matrix: Sequence[Any]) -> tuple[tuple[Any, Any, tuple[Any, ...]], ...]:
    normalized = []
    for entry in matrix:
        if not isinstance(entry, Mapping):
            normalized.append((None, None, ()))
            continue
        positions = entry.get('positions', ())
        if not _is_sequence(positions):
            positions = ()
        normalized.append((entry.get('subinterface_index'), entry.get('mpo_index'), tuple(positions)))
    return tuple(normalized)


__all__ = (
    'ARCHITECTURE_COMPATIBILITY_COMPATIBLE',
    'ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE',
    'ARCHITECTURE_COMPATIBILITY_WARNING',
    'ARCHITECTURE_SCHEMA_CONTRACT_VERSION',
    'ArchitectureCompatibilityIssue',
    'ArchitectureCompatibilityResult',
    'ArchitectureSchemaDefinition',
    'ArchitectureSchemaError',
    'ArchitectureSchemaValidationResult',
    'compare_persisted_architecture_compatibility',
    'validate_architecture_schema',
    'validate_persisted_architecture_schema',
)
