from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from dcim.models import DeviceType

from netbox_plant_graph.services.architecture import (
    GB300_8PLANE_STAMP_TEMPLATE,
    GB300_8PLANE_STAMP_TEMPLATE_SLUG,
    H100_DIRECT_ATTACH_STAMP_TEMPLATE,
    H100_DIRECT_ATTACH_STAMP_TEMPLATE_SLUG,
    STAMP_TEMPLATE,
    STAMP_TEMPLATE_SLUG,
    build_roce_4plane_h100_direct_attach_architecture_schema,
    build_roce_4plane_shuffle_architecture_schema,
    build_roce_8plane_gb300_shuffle_architecture_schema,
)
from netbox_plant_graph.services.architecture_schema import (
    ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
    ArchitectureSchemaDefinition,
    validate_architecture_schema,
)
from netbox_plant_graph.services.stamp_template_validation import validate_stamp_template_spec


BLUEPRINT_LIFECYCLE_ACTIVE = 'active'
BLUEPRINT_LIFECYCLE_DEPRECATED = 'deprecated'
BLUEPRINT_LIFECYCLE_RETIRED = 'retired'
BLUEPRINT_LIFECYCLE_VALUES = frozenset(
    {
        BLUEPRINT_LIFECYCLE_ACTIVE,
        BLUEPRINT_LIFECYCLE_DEPRECATED,
        BLUEPRINT_LIFECYCLE_RETIRED,
    }
)

JSON_SCHEMA_DRAFT = 'https://json-schema.org/draft/2020-12/schema'


@dataclass(frozen=True)
class BlueprintParameterIssue:
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
            'context': dict(self.context),
        }


@dataclass(frozen=True)
class BlueprintCompatibilityIssue:
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
            'context': dict(self.context),
        }


@dataclass(frozen=True)
class BlueprintCompatibilityResult:
    slug: str
    version: str
    issues: tuple[BlueprintCompatibilityIssue, ...] = ()

    @property
    def is_compatible(self) -> bool:
        return not any(issue.severity == 'error' for issue in self.issues)

    @property
    def errors(self) -> tuple[BlueprintCompatibilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'error')

    @property
    def warnings(self) -> tuple[BlueprintCompatibilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == 'warning')

    def to_dict(self) -> dict[str, Any]:
        return {
            'slug': self.slug,
            'version': self.version,
            'is_compatible': self.is_compatible,
            'issues': [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class BlueprintRegistryEntry:
    definition: ArchitectureSchemaDefinition
    parameter_schema: Mapping[str, Any]
    required_device_types: Mapping[str, Sequence[str]] = field(default_factory=dict)
    lifecycle: str = BLUEPRINT_LIFECYCLE_ACTIVE
    successor_version: str = ''
    metadata: Mapping[str, Any] = field(default_factory=dict)
    stamp_templates: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.definition.slug

    @property
    def version(self) -> str:
        return self.definition.version

    def to_summary(self) -> dict[str, Any]:
        return {
            'slug': self.slug,
            'version': self.version,
            'plane_count': self.definition.plane_count,
            'lifecycle': self.lifecycle,
            'successor_version': self.successor_version,
            'required_device_types': {
                role_slug: list(slugs)
                for role_slug, slugs in sorted(self.required_device_types.items())
            },
            'metadata': dict(self.metadata),
        }


class BlueprintRegistry:
    def __init__(self, entries: Iterable[BlueprintRegistryEntry] = ()):
        self._entries: dict[tuple[str, str], BlueprintRegistryEntry] = {}
        for entry in entries:
            self.register(entry)

    def register(self, entry: BlueprintRegistryEntry) -> BlueprintRegistryEntry:
        _validate_lifecycle(entry.lifecycle)
        schema_result = validate_architecture_schema(entry.definition)
        if not schema_result.is_valid:
            messages = '; '.join(schema_result.messages)
            raise ValueError(f'Blueprint {entry.slug} {entry.version} does not validate: {messages}')
        parameter_schema_issues = validate_parameter_schema(entry.parameter_schema)
        if parameter_schema_issues:
            messages = '; '.join(issue.message for issue in parameter_schema_issues)
            raise ValueError(f'Blueprint {entry.slug} {entry.version} parameter_schema is invalid: {messages}')
        for template_slug, template_payload in entry.stamp_templates.items():
            try:
                validate_stamp_template_spec(_stamp_template_body(template_payload))
            except ValueError as exc:
                raise ValueError(
                    f'Blueprint {entry.slug} {entry.version} stamp_template {template_slug!r} is invalid: {exc}'
                ) from exc

        key = (entry.slug, entry.version)
        if key in self._entries:
            raise ValueError(f'Blueprint {entry.slug} {entry.version} is already registered.')
        self._entries[key] = entry
        return entry

    def list_blueprints(self, *, include_retired: bool = True) -> tuple[BlueprintRegistryEntry, ...]:
        entries = tuple(
            entry
            for entry in self._entries.values()
            if include_retired or entry.lifecycle != BLUEPRINT_LIFECYCLE_RETIRED
        )
        return tuple(sorted(entries, key=lambda entry: (entry.slug, _version_sort_key(entry.version))))

    def get_blueprint(self, slug: str, version: str | None = None) -> BlueprintRegistryEntry:
        normalized_slug = str(slug)
        normalized_version = str(version) if version is not None else self.latest_version(normalized_slug)
        try:
            return self._entries[(normalized_slug, normalized_version)]
        except KeyError as exc:
            raise KeyError(f'Blueprint {normalized_slug} {normalized_version} is not registered.') from exc

    def latest_version(self, slug: str) -> str:
        versions = [
            entry.version
            for entry in self._entries.values()
            if entry.slug == slug and entry.lifecycle != BLUEPRINT_LIFECYCLE_RETIRED
        ]
        if not versions:
            raise KeyError(f'Blueprint {slug} has no active or deprecated versions.')
        return sorted(versions, key=_version_sort_key)[-1]

    def deprecate(self, slug: str, version: str, successor_version: str) -> BlueprintRegistryEntry:
        if not successor_version:
            raise ValueError('successor_version is required when deprecating a blueprint.')
        entry = self.get_blueprint(slug, version)
        updated = replace(
            entry,
            lifecycle=BLUEPRINT_LIFECYCLE_DEPRECATED,
            successor_version=str(successor_version),
        )
        self._entries[(entry.slug, entry.version)] = updated
        return updated

    def retire(self, slug: str, version: str) -> BlueprintRegistryEntry:
        entry = self.get_blueprint(slug, version)
        updated = replace(entry, lifecycle=BLUEPRINT_LIFECYCLE_RETIRED)
        self._entries[(entry.slug, entry.version)] = updated
        return updated


def validate_parameter_schema(schema: Mapping[str, Any]) -> tuple[BlueprintParameterIssue, ...]:
    if not isinstance(schema, Mapping):
        return (
            BlueprintParameterIssue(
                code='blueprint_parameter.schema_type',
                path='parameter_schema',
                message='Blueprint parameter_schema must be a JSON Schema object.',
            ),
        )

    try:
        from jsonschema import Draft202012Validator, exceptions
    except Exception:
        return _fallback_validate_parameter_schema(schema)

    try:
        Draft202012Validator.check_schema(dict(schema))
    except exceptions.SchemaError as exc:
        return (
            BlueprintParameterIssue(
                code='blueprint_parameter.schema_invalid',
                path='parameter_schema',
                message=str(exc).splitlines()[0],
            ),
        )
    return ()


def _stamp_template_body(template_payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(template_payload, Mapping):
        raise ValueError('stamp template payload must be an object')
    template_body = template_payload.get('template')
    if template_body is None:
        template_body = template_payload
    if not isinstance(template_body, Mapping):
        raise ValueError('stamp template body must be an object')
    return deepcopy(dict(template_body))


def validate_blueprint_parameters(
    parameter_schema: Mapping[str, Any],
    parameters: Mapping[str, Any],
    *,
    path: str = 'blueprint_parameters',
) -> tuple[BlueprintParameterIssue, ...]:
    schema_issues = validate_parameter_schema(parameter_schema)
    if schema_issues:
        return schema_issues
    if not isinstance(parameters, Mapping):
        return (
            BlueprintParameterIssue(
                code='blueprint_parameter.type',
                path=path,
                message='Blueprint parameters must be an object.',
                context={'expected_type': 'object'},
            ),
        )
    return tuple(_validate_json_value(dict(parameters), parameter_schema, path))


def check_blueprint_lifecycle(entry: BlueprintRegistryEntry) -> tuple[BlueprintCompatibilityIssue, ...]:
    if entry.lifecycle == BLUEPRINT_LIFECYCLE_ACTIVE:
        return ()
    if entry.lifecycle == BLUEPRINT_LIFECYCLE_DEPRECATED:
        return (
            BlueprintCompatibilityIssue(
                code='blueprint_lifecycle',
                path=f'blueprints.{entry.slug}.{entry.version}.lifecycle',
                message=(
                    f'Blueprint {entry.slug} {entry.version} is deprecated; '
                    f'use successor version {entry.successor_version!r}.'
                    if entry.successor_version
                    else f'Blueprint {entry.slug} {entry.version} is deprecated.'
                ),
                severity='warning',
                context={'lifecycle': entry.lifecycle, 'successor_version': entry.successor_version},
            ),
        )
    if entry.lifecycle == BLUEPRINT_LIFECYCLE_RETIRED:
        return (
            BlueprintCompatibilityIssue(
                code='blueprint_lifecycle',
                path=f'blueprints.{entry.slug}.{entry.version}.lifecycle',
                message=(
                    f'Blueprint {entry.slug} {entry.version} is retired'
                    + (
                        f'; use successor version {entry.successor_version!r}.'
                        if entry.successor_version
                        else '.'
                    )
                ),
                severity='error',
                context={'lifecycle': entry.lifecycle, 'successor_version': entry.successor_version},
            ),
        )
    return ()


def check_blueprint_device_type_compatibility(
    entry: BlueprintRegistryEntry,
    *,
    device_type_model=DeviceType,
) -> BlueprintCompatibilityResult:
    issues: list[BlueprintCompatibilityIssue] = list(check_blueprint_lifecycle(entry))
    required = _normalize_required_device_types(entry.required_device_types)
    all_slugs = sorted({slug for slugs in required.values() for slug in slugs})
    existing_slugs = set(device_type_model.objects.filter(slug__in=all_slugs).values_list('slug', flat=True))

    for role_slug, compatible_slugs in sorted(required.items()):
        if not compatible_slugs:
            continue
        if existing_slugs.intersection(compatible_slugs):
            continue
        issues.append(
            BlueprintCompatibilityIssue(
                code='architecture_gate.missing_device_type',
                path=f'required_device_types.{role_slug}',
                message=(
                    f'Blueprint {entry.slug} {entry.version} requires one of these DeviceType slugs '
                    f'for role {role_slug!r}: {", ".join(compatible_slugs)}.'
                ),
                context={'role_slug': role_slug, 'compatible_slugs': compatible_slugs},
            )
        )
    return BlueprintCompatibilityResult(slug=entry.slug, version=entry.version, issues=tuple(issues))


def check_all_blueprint_device_type_compatibility(
    registry: BlueprintRegistry | None = None,
) -> tuple[BlueprintCompatibilityResult, ...]:
    registry = registry or get_default_blueprint_registry()
    return tuple(
        check_blueprint_device_type_compatibility(entry)
        for entry in registry.list_blueprints(include_retired=True)
    )


def architecture_definition_to_payload(definition: ArchitectureSchemaDefinition) -> dict[str, Any]:
    return {
        'slug': definition.slug,
        'version': definition.version,
        'plane_count': definition.plane_count,
        'roles': _jsonable_sequence(definition.roles),
        'transfer_patterns': _jsonable_sequence(definition.transfer_patterns),
        'allocation_rule_sets': _jsonable_sequence(definition.allocation_rule_sets),
        'channel_map_matrix': _jsonable_sequence(definition.channel_map_matrix),
        'active_position_groups': {
            str(label): list(positions)
            for label, positions in definition.active_position_groups.items()
        },
        'dark_positions': list(definition.dark_positions),
        'mpo_position_count': definition.mpo_position_count,
        'shuffle_mpo_groups': [list(group) for group in definition.shuffle_mpo_groups],
        'channels_per_subinterface': definition.channels_per_subinterface,
        'mpo_count_per_osfp': definition.mpo_count_per_osfp,
        'min_planes': definition.min_planes,
        'max_planes': definition.max_planes,
        'default_planes': definition.default_planes,
        'fabric_class': definition.fabric_class,
        'parameter_schema': deepcopy(dict(definition.parameter_schema)),
        'required_device_types': {
            role_slug: list(slugs)
            for role_slug, slugs in definition.required_device_types.items()
        },
        'status': definition.status,
        'lifecycle': deepcopy(dict(definition.lifecycle)),
        'custom_validator_entrypoints': list(definition.custom_validator_entrypoints),
    }


def architecture_definition_from_payload(
    payload: Mapping[str, Any],
    *,
    shuffle_pair_provider=None,
) -> ArchitectureSchemaDefinition:
    if not isinstance(payload, Mapping):
        raise ValueError('architecture definition payload must be an object')
    plane_count = _required_int(payload, 'plane_count')
    return ArchitectureSchemaDefinition(
        slug=_required_string(payload, 'slug'),
        version=_required_string(payload, 'version'),
        plane_count=plane_count,
        roles=_required_sequence(payload, 'roles'),
        transfer_patterns=_required_sequence(payload, 'transfer_patterns'),
        allocation_rule_sets=_required_sequence(payload, 'allocation_rule_sets'),
        channel_map_matrix=_required_sequence(payload, 'channel_map_matrix'),
        active_position_groups=_required_mapping(payload, 'active_position_groups'),
        dark_positions=_required_sequence(payload, 'dark_positions'),
        mpo_position_count=_required_int(payload, 'mpo_position_count'),
        shuffle_mpo_groups=_required_sequence(payload, 'shuffle_mpo_groups'),
        shuffle_pair_provider=shuffle_pair_provider,
        channels_per_subinterface=_optional_int(payload, 'channels_per_subinterface', default=4),
        mpo_count_per_osfp=_optional_int(payload, 'mpo_count_per_osfp', default=2),
        min_planes=_optional_int_or_default(payload, 'min_planes', default=plane_count),
        max_planes=_optional_int_or_default(payload, 'max_planes', default=plane_count),
        default_planes=_optional_int_or_default(payload, 'default_planes', default=plane_count),
        fabric_class=str(payload.get('fabric_class') or 'roce_backend'),
        parameter_schema=dict(payload.get('parameter_schema') or {}),
        required_device_types=_required_device_types_from_payload(payload.get('required_device_types') or {}),
        status=str(payload.get('status') or 'active'),
        lifecycle=dict(payload.get('lifecycle') or {}),
        custom_validator_entrypoints=tuple(payload.get('custom_validator_entrypoints') or ()),
    )


def build_builtin_blueprint_entries() -> tuple[BlueprintRegistryEntry, ...]:
    gb300_4plane = build_roce_4plane_shuffle_architecture_schema()
    gb300_8plane = build_roce_8plane_gb300_shuffle_architecture_schema()
    h100_direct_attach = build_roce_4plane_h100_direct_attach_architecture_schema()

    return (
        BlueprintRegistryEntry(
            definition=gb300_4plane,
            parameter_schema=_base_parameter_schema(default_planes=4, max_planes=4),
            required_device_types={
                'gpu_tray': ('gb300-tray', 'gb300-gpu-tray', 'gb300-nvl72-tray'),
                'leaf_switch': ('roce-leaf-switch', 'leaf-switch', 'sn5600-roce-leaf'),
                'shuffle_cassette': ('gpu-leaf-shuffle-1x4', 'shuffle-cassette', 'gb300-shuffle-cassette'),
            },
            metadata={
                'family': 'gb300',
                'transfer_geometry': 'shuffle_2x2',
                'stamp_template_slugs': [STAMP_TEMPLATE_SLUG],
            },
            stamp_templates={
                STAMP_TEMPLATE_SLUG: {
                    'slug': STAMP_TEMPLATE_SLUG,
                    'name': 'RoCE 4-plane mini proof',
                    'template': deepcopy(STAMP_TEMPLATE),
                    'metadata': {'fixture': True},
                },
            },
        ),
        BlueprintRegistryEntry(
            definition=h100_direct_attach,
            parameter_schema=_base_parameter_schema(default_planes=4, max_planes=4),
            required_device_types={
                'h100_node': ('h100-gpu-server', 'h100-server', 'nvidia-h100-node'),
                'leaf_switch': ('roce-leaf-switch', 'leaf-switch', 'sn5600-roce-leaf'),
            },
            metadata={
                'family': 'h100',
                'transfer_geometry': 'direct_attach',
                'stamp_template_slugs': [H100_DIRECT_ATTACH_STAMP_TEMPLATE_SLUG],
                'executor_primitive': 'roce_direct_attach_mini_proof',
            },
            stamp_templates={
                H100_DIRECT_ATTACH_STAMP_TEMPLATE_SLUG: {
                    'slug': H100_DIRECT_ATTACH_STAMP_TEMPLATE_SLUG,
                    'name': 'RoCE 4-plane H100 direct-attach mini proof',
                    'template': deepcopy(H100_DIRECT_ATTACH_STAMP_TEMPLATE),
                    'metadata': {'fixture': True, 'connection_geometry': 'direct_attach'},
                },
            },
        ),
        BlueprintRegistryEntry(
            definition=gb300_8plane,
            parameter_schema=_base_parameter_schema(default_planes=8, max_planes=8),
            required_device_types={
                'gpu_tray': ('gb300-tray', 'gb300-gpu-tray', 'gb300-nvl72-tray'),
                'leaf_switch': ('roce-leaf-switch', 'leaf-switch', 'sn5600-roce-leaf'),
                'shuffle_cassette': ('gpu-leaf-shuffle-1x4', 'shuffle-cassette', 'gb300-shuffle-cassette'),
            },
            metadata={
                'family': 'gb300',
                'transfer_geometry': 'shuffle_2x2',
                'topology_parameters': {
                    'plane_count': 8,
                    'leaf_count_per_plane': 1,
                },
                'stamp_template_slugs': [GB300_8PLANE_STAMP_TEMPLATE_SLUG],
                'executor_primitive': 'roce_gb300_shuffle_mini_proof',
            },
            stamp_templates={
                GB300_8PLANE_STAMP_TEMPLATE_SLUG: {
                    'slug': GB300_8PLANE_STAMP_TEMPLATE_SLUG,
                    'name': 'RoCE 8-plane GB300 mini proof',
                    'template': deepcopy(GB300_8PLANE_STAMP_TEMPLATE),
                    'metadata': {
                        'fixture': True,
                        'topology_parameters': {
                            'plane_count': 8,
                            'leaf_count_per_plane': 1,
                        },
                    },
                },
            },
        ),
    )


def get_default_blueprint_registry() -> BlueprintRegistry:
    return _DEFAULT_BLUEPRINT_REGISTRY


def _base_parameter_schema(*, default_planes: int, max_planes: int) -> dict[str, Any]:
    return {
        '$schema': JSON_SCHEMA_DRAFT,
        'title': 'MPF blueprint stamp parameters',
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'topology_parameters': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'plane_count': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': max_planes,
                        'default': default_planes,
                    },
                    'gpu_tray_count': {'type': 'integer', 'minimum': 1, 'default': 1},
                    'leaf_count_per_plane': {'type': 'integer', 'minimum': 1, 'default': 1},
                    'racks_per_pod': {'type': 'integer', 'minimum': 1},
                    'pods_per_fabric': {'type': 'integer', 'minimum': 1},
                },
            },
            'wavelength_plan': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'band': {'type': 'string', 'enum': ['o_band', 'c_band', 'direct_detect']},
                    'channel_count': {'type': 'integer', 'minimum': 1},
                    'channels': {
                        'type': 'array',
                        'items': {'type': 'number'},
                        'minItems': 1,
                    },
                },
            },
            'name_patterns': {
                'type': 'object',
                'additionalProperties': {'type': 'string'},
            },
            'dark_position_overrides': {
                'type': 'object',
                'additionalProperties': {
                    'type': 'array',
                    'items': {'type': 'integer', 'minimum': 1},
                    'uniqueItems': True,
                },
            },
            'allocation_rule_override': {'type': 'string'},
        },
    }


def _validate_lifecycle(value: str) -> None:
    if value not in BLUEPRINT_LIFECYCLE_VALUES:
        raise ValueError(f'Unknown blueprint lifecycle value: {value!r}.')


def _version_sort_key(version: str) -> tuple[Any, ...]:
    parts = re.findall(r'\d+|[A-Za-z]+', str(version))
    key = []
    for part in parts:
        key.append(int(part) if part.isdigit() else part)
    return tuple(key or [str(version)])


def _normalize_required_device_types(required_device_types: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    if not isinstance(required_device_types, Mapping):
        return normalized
    for role_slug, slugs in required_device_types.items():
        if isinstance(slugs, str):
            values = (slugs,)
        elif isinstance(slugs, Sequence):
            values = tuple(str(slug) for slug in slugs if slug)
        else:
            values = ()
        normalized[str(role_slug)] = tuple(sorted(set(values)))
    return normalized


def _fallback_validate_parameter_schema(schema: Mapping[str, Any]) -> tuple[BlueprintParameterIssue, ...]:
    issues: list[BlueprintParameterIssue] = []
    _validate_schema_node(schema, 'parameter_schema', issues)
    return tuple(issues)


def _validate_schema_node(schema: Any, path: str, issues: list[BlueprintParameterIssue]) -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, Mapping):
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.schema_invalid',
                path=path,
                message='JSON Schema nodes must be objects or booleans.',
            )
        )
        return
    schema_type = schema.get('type')
    if schema_type is not None:
        allowed = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}
        raw_types = schema_type if isinstance(schema_type, Sequence) and not isinstance(schema_type, str) else [schema_type]
        for raw_type in raw_types:
            if raw_type not in allowed:
                issues.append(
                    BlueprintParameterIssue(
                        code='blueprint_parameter.schema_invalid',
                        path=f'{path}.type',
                        message=f'Unsupported JSON Schema type {raw_type!r}.',
                    )
                )
    properties = schema.get('properties')
    if properties is not None:
        if not isinstance(properties, Mapping):
            issues.append(
                BlueprintParameterIssue(
                    code='blueprint_parameter.schema_invalid',
                    path=f'{path}.properties',
                    message='JSON Schema properties must be an object.',
                )
            )
        else:
            for name, child_schema in properties.items():
                _validate_schema_node(child_schema, f'{path}.properties.{name}', issues)
    required = schema.get('required')
    if required is not None and (
        not isinstance(required, Sequence)
        or isinstance(required, str)
        or any(not isinstance(item, str) for item in required)
    ):
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.schema_invalid',
                path=f'{path}.required',
                message='JSON Schema required must be an array of strings.',
            )
        )
    additional = schema.get('additionalProperties')
    if isinstance(additional, Mapping) or isinstance(additional, bool):
        if isinstance(additional, Mapping):
            _validate_schema_node(additional, f'{path}.additionalProperties', issues)
    elif additional is not None:
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.schema_invalid',
                path=f'{path}.additionalProperties',
                message='JSON Schema additionalProperties must be a boolean or schema object.',
            )
        )
    items = schema.get('items')
    if items is not None:
        _validate_schema_node(items, f'{path}.items', issues)


def _validate_json_value(value: Any, schema: Mapping[str, Any], path: str) -> list[BlueprintParameterIssue]:
    issues: list[BlueprintParameterIssue] = []
    schema_type = schema.get('type')
    if schema_type and not _json_type_matches(value, schema_type):
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.type',
                path=path,
                message=f'{path} must be {_type_label(schema_type)}.',
                context={'expected_type': schema_type},
            )
        )
        return issues

    enum_values = schema.get('enum')
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, str) and value not in enum_values:
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.enum',
                path=path,
                message=f'{path} must be one of: {", ".join(str(item) for item in enum_values)}.',
                context={'allowed_values': list(enum_values)},
            )
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get('minimum')
        maximum = schema.get('maximum')
        if minimum is not None and value < minimum:
            issues.append(
                BlueprintParameterIssue(
                    code='blueprint_parameter.minimum',
                    path=path,
                    message=f'{path} must be greater than or equal to {minimum}.',
                    context={'minimum': minimum, 'actual': value},
                )
            )
        if maximum is not None and value > maximum:
            issues.append(
                BlueprintParameterIssue(
                    code='blueprint_parameter.maximum',
                    path=path,
                    message=f'{path} must be less than or equal to {maximum}.',
                    context={'maximum': maximum, 'actual': value},
                )
            )

    pattern = schema.get('pattern')
    if isinstance(value, str) and isinstance(pattern, str) and re.search(pattern, value) is None:
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.pattern',
                path=path,
                message=f'{path} does not match the required pattern.',
                context={'pattern': pattern},
            )
        )

    if isinstance(value, Mapping):
        issues.extend(_validate_json_object(value, schema, path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        issues.extend(_validate_json_array(value, schema, path))
    return issues


def _validate_json_object(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
    path: str,
) -> list[BlueprintParameterIssue]:
    issues: list[BlueprintParameterIssue] = []
    properties = schema.get('properties') if isinstance(schema.get('properties'), Mapping) else {}
    required = schema.get('required') if isinstance(schema.get('required'), Sequence) else ()
    for name in required:
        if name not in value:
            issues.append(
                BlueprintParameterIssue(
                    code='blueprint_parameter.required',
                    path=f'{path}.{name}',
                    message=f'{path}.{name} is required.',
                    context={'required_property': name},
                )
            )

    additional = schema.get('additionalProperties', True)
    for name, child_value in value.items():
        child_path = f'{path}.{name}'
        child_schema = properties.get(name)
        if child_schema is None:
            if additional is False:
                issues.append(
                    BlueprintParameterIssue(
                        code='blueprint_parameter.additional_property',
                        path=child_path,
                        message=f'{child_path} is not accepted by this blueprint.',
                        context={'property': name},
                    )
                )
            elif isinstance(additional, Mapping):
                issues.extend(_validate_json_value(child_value, additional, child_path))
            continue
        if isinstance(child_schema, Mapping):
            issues.extend(_validate_json_value(child_value, child_schema, child_path))
    return issues


def _validate_json_array(
    value: Sequence[Any],
    schema: Mapping[str, Any],
    path: str,
) -> list[BlueprintParameterIssue]:
    issues: list[BlueprintParameterIssue] = []
    min_items = schema.get('minItems')
    max_items = schema.get('maxItems')
    if min_items is not None and len(value) < min_items:
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.min_items',
                path=path,
                message=f'{path} must contain at least {min_items} item(s).',
                context={'minItems': min_items, 'actual': len(value)},
            )
        )
    if max_items is not None and len(value) > max_items:
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.max_items',
                path=path,
                message=f'{path} must contain no more than {max_items} item(s).',
                context={'maxItems': max_items, 'actual': len(value)},
            )
        )
    if schema.get('uniqueItems') is True and len({repr(item) for item in value}) != len(value):
        issues.append(
            BlueprintParameterIssue(
                code='blueprint_parameter.unique_items',
                path=path,
                message=f'{path} must contain unique items.',
            )
        )
    item_schema = schema.get('items')
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(value, start=1):
            issues.extend(_validate_json_value(item, item_schema, f'{path}[{index}]'))
    return issues


def _json_type_matches(value: Any, schema_type: str | Sequence[str]) -> bool:
    raw_types = schema_type if isinstance(schema_type, Sequence) and not isinstance(schema_type, str) else [schema_type]
    return any(_json_type_matches_one(value, raw_type) for raw_type in raw_types)


def _json_type_matches_one(value: Any, schema_type: str) -> bool:
    if schema_type == 'object':
        return isinstance(value, Mapping)
    if schema_type == 'array':
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    if schema_type == 'string':
        return isinstance(value, str)
    if schema_type == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == 'boolean':
        return isinstance(value, bool)
    if schema_type == 'null':
        return value is None
    return True


def _type_label(schema_type: str | Sequence[str]) -> str:
    if isinstance(schema_type, str):
        return f'a JSON {schema_type}'
    return 'one of: ' + ', '.join(str(item) for item in schema_type)


def _jsonable_sequence(value: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [deepcopy(dict(item)) for item in value]


def _required_string(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'architecture definition field {field_name} must be a non-empty string')
    return value.strip()


def _required_int(payload: Mapping[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'architecture definition field {field_name} must be an integer')
    return value


def _optional_int(payload: Mapping[str, Any], field_name: str, *, default: int) -> int:
    value = payload.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'architecture definition field {field_name} must be an integer')
    return value


def _optional_int_or_default(payload: Mapping[str, Any], field_name: str, *, default: int) -> int:
    value = payload.get(field_name, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'architecture definition field {field_name} must be an integer')
    return value


def _required_device_types_from_payload(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError('architecture definition field required_device_types must be an object')
    normalized = {}
    for role_slug, slugs in value.items():
        if not isinstance(slugs, Sequence) or isinstance(slugs, (str, bytes, bytearray)):
            raise ValueError(
                f'architecture definition field required_device_types.{role_slug} must be an array'
            )
        normalized[str(role_slug)] = tuple(str(slug) for slug in slugs)
    return normalized


def _required_sequence(payload: Mapping[str, Any], field_name: str) -> Sequence[Any]:
    value = payload.get(field_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f'architecture definition field {field_name} must be an array')
    return value


def _required_mapping(payload: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    value = payload.get(field_name)
    if not isinstance(value, Mapping):
        raise ValueError(f'architecture definition field {field_name} must be an object')
    return value


_DEFAULT_BLUEPRINT_REGISTRY = BlueprintRegistry(build_builtin_blueprint_entries())


__all__ = (
    'BLUEPRINT_LIFECYCLE_ACTIVE',
    'BLUEPRINT_LIFECYCLE_DEPRECATED',
    'BLUEPRINT_LIFECYCLE_RETIRED',
    'BlueprintCompatibilityIssue',
    'BlueprintCompatibilityResult',
    'BlueprintParameterIssue',
    'BlueprintRegistry',
    'BlueprintRegistryEntry',
    'architecture_definition_from_payload',
    'architecture_definition_to_payload',
    'build_builtin_blueprint_entries',
    'check_all_blueprint_device_type_compatibility',
    'check_blueprint_device_type_compatibility',
    'check_blueprint_lifecycle',
    'get_default_blueprint_registry',
    'validate_blueprint_parameters',
    'validate_parameter_schema',
)
