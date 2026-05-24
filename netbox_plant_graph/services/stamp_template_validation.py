from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from string import Formatter


SUPPORTED_SOURCE_BINDING_MODELS = frozenset({
    'dcim.device',
    'dcim.interface',
})

_REQUIRED_TEMPLATE_KEYS = (
    'kind',
    'architecture_slug',
    'architecture_version',
    'executor',
    'planes',
    'gpu_tray',
    'leaf_ports',
    'proof_paths',
    'source_bindings',
)

_DERIVABLE_TEMPLATE_KEYS = frozenset({'planes'})
SUPPORTED_CONNECTION_GEOMETRIES = frozenset({'shuffle_2x2', 'direct_attach'})

_REQUIRED_SOURCE_BINDING_KEYS = (
    'kind',
    'address',
    'field_name',
    'model',
)

_REQUIRED_PROOF_PATH_KEYS = (
    'plane',
    'gpu_osfp',
    'cassette',
    'front_position',
    'rear_position',
    'leaf',
)

_DIRECT_ATTACH_PROOF_PATH_KEYS = (
    'plane',
    'gpu_osfp',
    'front_position',
    'rear_position',
    'leaf',
)

SUPPORTED_TOPOLOGY_PARAMETERS = frozenset({
    'plane_count',
    'gpu_tray_count',
    'leaf_count_per_plane',
    'spine_count_per_plane',
    'leaf_spine_osfp_cages_per_leaf',
    'racks_per_pod',
    'pods_per_fabric',
})

TOPOLOGY_PARAMETER_BOUNDS = {
    'plane_count': (1, 16),
    'gpu_tray_count': (1, 10000),
    'leaf_count_per_plane': (1, 256),
    'spine_count_per_plane': (1, 1024),
    'leaf_spine_osfp_cages_per_leaf': (1, 512),
    'racks_per_pod': (1, 1024),
    'pods_per_fabric': (1, 1024),
}

SUPPORTED_WAVELENGTH_BANDS = frozenset({'o_band', 'c_band', 'direct_detect'})

SUPPORTED_NAME_PATTERN_VARIABLES = frozenset({
    'rack_id',
    'tray_index',
    'plane_index',
    'port_index',
    'channel_index',
    'node_address',
    'fabric_slug',
})

SUPPORTED_NAME_PATTERN_KINDS = frozenset({
    'device',
    'gpu_tray',
    'leaf_switch',
    'interface',
    'endpoint',
    'channel_subinterface',
    'transport_channel',
})


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_mapping(value, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f'{path} must be an object.')
    return value


def _require_sequence(value, path: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f'{path} must be an array.')
    return value


def _require_non_empty_string(value, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{path} must be a non-empty string.')
    return value.strip()


def _require_positive_int(value, path: str) -> int:
    if not _is_positive_int(value):
        raise ValueError(f'{path} must be a positive integer.')
    return value


def _require_keys(mapping: dict, keys: tuple[str, ...], path: str) -> None:
    for key in keys:
        if key not in mapping:
            raise ValueError(f'{path}.{key} is required.')


def _require_template_keys(template_spec: dict) -> None:
    for key in _REQUIRED_TEMPLATE_KEYS:
        if key in template_spec:
            continue
        if key in _DERIVABLE_TEMPLATE_KEYS and _has_topology_parameter(template_spec, 'plane_count'):
            continue
        raise ValueError(f'StampTemplate.template.{key} is required.')
    if _connection_geometry(template_spec) != 'direct_attach' and 'shuffle_cassettes' not in template_spec:
        raise ValueError('StampTemplate.template.shuffle_cassettes is required.')


def _connection_geometry(template_spec: dict) -> str:
    raw_geometry = template_spec.get('connection_geometry') or template_spec.get('transfer_geometry') or 'shuffle_2x2'
    if not isinstance(raw_geometry, str):
        return ''
    return raw_geometry.strip()


def _validate_connection_geometry(template_spec: dict) -> str:
    geometry = _connection_geometry(template_spec)
    if geometry not in SUPPORTED_CONNECTION_GEOMETRIES:
        raise ValueError(
            'StampTemplate.template.connection_geometry must be one of: '
            f'{", ".join(sorted(SUPPORTED_CONNECTION_GEOMETRIES))}.'
        )
    return geometry


def _validate_executor(template_spec: dict) -> None:
    executor = _require_mapping(template_spec['executor'], 'StampTemplate.template.executor')
    mode = executor.get('mode')
    if mode != 'hybrid':
        raise ValueError('StampTemplate.template.executor.mode must be "hybrid".')
    primitive = executor.get('primitive')
    if not primitive:
        raise ValueError('StampTemplate.template.executor.primitive is required.')


def _validate_template_identity(template_spec: dict) -> None:
    _require_non_empty_string(template_spec['kind'], 'StampTemplate.template.kind')
    _require_non_empty_string(template_spec['architecture_slug'], 'StampTemplate.template.architecture_slug')
    _require_non_empty_string(template_spec['architecture_version'], 'StampTemplate.template.architecture_version')


def _validate_planes(template_spec: dict) -> list[int]:
    planes = _require_sequence(template_spec['planes'], 'StampTemplate.template.planes')
    if not planes:
        raise ValueError('StampTemplate.template.planes must have at least one plane.')
    normalized = []
    for index, plane_number in enumerate(planes, start=1):
        normalized.append(_require_positive_int(plane_number, f'StampTemplate.template.planes[{index}]'))
    if len(set(normalized)) != len(normalized):
        raise ValueError('StampTemplate.template.planes must not contain duplicates.')
    return normalized


def _has_topology_parameter(template_spec: dict, key: str) -> bool:
    topology_parameters = template_spec.get('topology_parameters') or {}
    return isinstance(topology_parameters, dict) and key in topology_parameters


def _parse_topology_parameter(raw_value, path: str, default_bounds: tuple[int, int]) -> tuple[int, int, int]:
    minimum, maximum = default_bounds
    if isinstance(raw_value, dict):
        if raw_value.get('min') is not None:
            minimum = _require_positive_int(raw_value['min'], f'{path}.min')
        if raw_value.get('max') is not None:
            maximum = _require_positive_int(raw_value['max'], f'{path}.max')
        if minimum > maximum:
            raise ValueError(f'{path}.min must be <= {path}.max.')
        if raw_value.get('value') is not None:
            value = _require_positive_int(raw_value['value'], f'{path}.value')
        elif raw_value.get('default') is not None:
            value = _require_positive_int(raw_value['default'], f'{path}.default')
        else:
            raise ValueError(f'{path}.value or {path}.default is required.')
    else:
        value = _require_positive_int(raw_value, path)
    if value < minimum or value > maximum:
        raise ValueError(f'{path} must be between {minimum} and {maximum}.')
    return value, minimum, maximum


def _legacy_group_count(template_spec: dict, group_name: str, field_name: str, fallback: int) -> int:
    group = template_spec.get(group_name) or {}
    if isinstance(group, dict) and _is_positive_int(group.get(field_name)):
        return int(group[field_name])
    return fallback


def _resolved_topology_parameters(template_spec: dict) -> dict:
    topology_parameters = template_spec.get('topology_parameters') or {}
    if not isinstance(topology_parameters, dict):
        raise ValueError('StampTemplate.template.topology_parameters must be an object.')
    for key in topology_parameters:
        if key not in SUPPORTED_TOPOLOGY_PARAMETERS:
            raise ValueError(
                f'StampTemplate.template.topology_parameters.{key} is not supported. '
                f'Supported parameters are: {", ".join(sorted(SUPPORTED_TOPOLOGY_PARAMETERS))}.'
            )

    raw_planes = template_spec.get('planes')
    legacy_plane_count = len(raw_planes) if isinstance(raw_planes, list) else None
    if legacy_plane_count is None and 'plane_count' not in topology_parameters:
        raise ValueError('StampTemplate.template.planes is required.')

    defaults = {
        'plane_count': legacy_plane_count or 4,
        'gpu_tray_count': _legacy_group_count(template_spec, 'gpu_tray', 'count', 1),
        'leaf_count_per_plane': 1,
        'spine_count_per_plane': 126,
        'leaf_spine_osfp_cages_per_leaf': 32,
        'racks_per_pod': 1,
        'pods_per_fabric': 1,
    }
    leaf_count = _legacy_group_count(template_spec, 'leaf_ports', 'count', defaults['plane_count'])
    if defaults['plane_count'] and leaf_count % defaults['plane_count'] == 0:
        defaults['leaf_count_per_plane'] = max(1, leaf_count // defaults['plane_count'])

    resolved: dict[str, int] = {}
    metadata: dict[str, dict[str, int]] = {}
    for key in sorted(SUPPORTED_TOPOLOGY_PARAMETERS):
        bounds = TOPOLOGY_PARAMETER_BOUNDS[key]
        if key in topology_parameters:
            value, minimum, maximum = _parse_topology_parameter(
                topology_parameters[key],
                f'StampTemplate.template.topology_parameters.{key}',
                bounds,
            )
        else:
            value = defaults[key]
            minimum, maximum = bounds
            if value < minimum or value > maximum:
                raise ValueError(
                    f'StampTemplate.template.topology_parameters.{key} default must be between '
                    f'{minimum} and {maximum}.'
                )
        resolved[key] = value
        metadata[key] = {
            'value': value,
            'min': minimum,
            'max': maximum,
            'source': 'template' if key in topology_parameters else 'legacy',
        }

    if isinstance(raw_planes, list) and len(raw_planes) != resolved['plane_count']:
        raise ValueError(
            'StampTemplate.template.topology_parameters.plane_count must match '
            'the number of StampTemplate.template.planes entries.'
        )

    resolved['leaf_count'] = resolved['leaf_count_per_plane'] * resolved['plane_count']
    resolved['_metadata'] = metadata
    return resolved


def _apply_topology_parameters(template_spec: dict, topology_parameters: dict) -> None:
    topology_overrides_enabled = bool(template_spec.get('topology_parameters'))
    if 'planes' not in template_spec:
        template_spec['planes'] = list(range(1, topology_parameters['plane_count'] + 1))

    gpu_tray = template_spec.setdefault('gpu_tray', {})
    if isinstance(gpu_tray, dict) and topology_overrides_enabled:
        gpu_tray['count'] = topology_parameters['gpu_tray_count']

    leaf_ports = template_spec.setdefault('leaf_ports', {})
    if isinstance(leaf_ports, dict) and topology_overrides_enabled:
        leaf_ports['count'] = topology_parameters['leaf_count']
        assignment = leaf_ports.get('plane_assignment')
        expected_leaf_indexes = set(range(1, topology_parameters['leaf_count'] + 1))
        should_generate_assignment = not isinstance(assignment, dict)
        if isinstance(assignment, dict):
            try:
                normalized_assignment = {
                    _coerce_positive_int(key, 'leaf plane assignment key'): _coerce_positive_int(
                        value,
                        'leaf plane assignment value',
                    )
                    for key, value in assignment.items()
                }
            except ValueError:
                normalized_assignment = {}
            legacy_identity_assignment = (
                normalized_assignment
                and set(normalized_assignment) == set(range(1, len(normalized_assignment) + 1))
                and all(leaf_index == plane for leaf_index, plane in normalized_assignment.items())
            )
            if legacy_identity_assignment and (
                set(normalized_assignment) != expected_leaf_indexes
                or any(plane not in set(template_spec['planes']) for plane in normalized_assignment.values())
            ):
                should_generate_assignment = True
        if should_generate_assignment:
            generated = {}
            index = 1
            for plane in template_spec['planes']:
                for _ in range(topology_parameters['leaf_count_per_plane']):
                    generated[str(index)] = int(plane)
                    index += 1
            leaf_ports['plane_assignment'] = generated


def _validate_stamp_phases(template_spec: dict, planes: list[int], phase: str | None = None) -> dict:
    raw_phases = template_spec.get('stamp_phases') or []
    if raw_phases in ({}, None):
        raw_phases = []
    if not isinstance(raw_phases, list):
        raise ValueError('StampTemplate.template.stamp_phases must be an array.')

    normalized = []
    valid_planes = set(planes)
    names = set()
    for index, raw_phase in enumerate(raw_phases, start=1):
        phase_spec = _require_mapping(raw_phase, f'StampTemplate.template.stamp_phases[{index}]')
        raw_name = phase_spec.get('name') or phase_spec.get('slug')
        name = _require_non_empty_string(raw_name, f'StampTemplate.template.stamp_phases[{index}].name')
        if name in names:
            raise ValueError(f'StampTemplate.template.stamp_phases[{index}].name duplicates an earlier phase.')
        names.add(name)
        phase_planes = _require_sequence(
            phase_spec.get('planes'),
            f'StampTemplate.template.stamp_phases[{index}].planes',
        )
        if not phase_planes:
            raise ValueError(f'StampTemplate.template.stamp_phases[{index}].planes must not be empty.')
        normalized_planes = []
        for plane_index, raw_plane in enumerate(phase_planes, start=1):
            plane_number = _require_positive_int(
                raw_plane,
                f'StampTemplate.template.stamp_phases[{index}].planes[{plane_index}]',
            )
            if plane_number not in valid_planes:
                raise ValueError(
                    f'StampTemplate.template.stamp_phases[{index}].planes[{plane_index}] '
                    'must reference a plane in StampTemplate.template.planes.'
                )
            normalized_planes.append(plane_number)
        if len(set(normalized_planes)) != len(normalized_planes):
            raise ValueError(f'StampTemplate.template.stamp_phases[{index}].planes must not contain duplicates.')
        normalized.append({'name': name, 'planes': normalized_planes})

    if phase is None:
        return {
            'selected': None,
            'active_planes': list(planes),
            'planned_phases': normalized,
        }

    selected_phase_name = _require_non_empty_string(phase, 'phase')
    for phase_spec in normalized:
        if phase_spec['name'] == selected_phase_name:
            return {
                'selected': phase_spec,
                'active_planes': list(phase_spec['planes']),
                'planned_phases': normalized,
            }
    raise ValueError(f'phase {selected_phase_name!r} is not declared in StampTemplate.template.stamp_phases.')


def _validate_wavelength_plan(template_spec: dict) -> dict | None:
    wavelength_plan = template_spec.get('wavelength_plan')
    if wavelength_plan is None:
        return None
    wavelength_plan = _require_mapping(wavelength_plan, 'StampTemplate.template.wavelength_plan')
    band = _require_non_empty_string(wavelength_plan.get('band'), 'StampTemplate.template.wavelength_plan.band')
    if band not in SUPPORTED_WAVELENGTH_BANDS:
        raise ValueError(
            'StampTemplate.template.wavelength_plan.band must be one of: '
            f'{", ".join(sorted(SUPPORTED_WAVELENGTH_BANDS))}.'
        )
    raw_channels = wavelength_plan.get('channels')
    channels = []
    if raw_channels is not None:
        raw_channels = _require_sequence(raw_channels, 'StampTemplate.template.wavelength_plan.channels')
        for index, raw_channel in enumerate(raw_channels, start=1):
            try:
                channel = Decimal(str(raw_channel))
            except (InvalidOperation, ValueError):
                raise ValueError(f'StampTemplate.template.wavelength_plan.channels[{index}] must be numeric.')
            if channel < 0:
                raise ValueError(f'StampTemplate.template.wavelength_plan.channels[{index}] must be >= 0.')
            channels.append(str(channel))

    if wavelength_plan.get('channel_count') is None:
        if not channels:
            raise ValueError('StampTemplate.template.wavelength_plan.channel_count is required.')
        channel_count = len(channels)
    else:
        channel_count = _require_positive_int(
            wavelength_plan.get('channel_count'),
            'StampTemplate.template.wavelength_plan.channel_count',
        )
    if channels and len(channels) != channel_count:
        raise ValueError('StampTemplate.template.wavelength_plan.channels length must match channel_count.')

    channel_indexes = {
        int(entry['subinterface_index'])
        for entry in _channel_map_matrix(template_spec)
        if entry.get('subinterface_index') is not None
    }
    if channel_indexes and channel_count != len(channel_indexes):
        raise ValueError(
            'StampTemplate.template.wavelength_plan.channel_count must match the number of '
            'channel subinterface indexes.'
        )
    return {
        'band': band,
        'channel_count': channel_count,
        'channels': channels,
    }


def _pattern_variables(pattern: str, path: str) -> set[str]:
    variables = set()
    try:
        parsed = list(Formatter().parse(pattern))
    except ValueError as exc:
        raise ValueError(f'{path} is not a valid format pattern: {exc}.')
    for _, field_name, _, _ in parsed:
        if not field_name:
            continue
        root = field_name.split('.', 1)[0].split('[', 1)[0]
        variables.add(root)
    return variables


def _validate_name_patterns(template_spec: dict) -> dict:
    name_patterns = template_spec.get('name_patterns') or {}
    if not isinstance(name_patterns, dict):
        raise ValueError('StampTemplate.template.name_patterns must be an object.')
    normalized = {}
    for role_kind, raw_pattern in name_patterns.items():
        if role_kind not in SUPPORTED_NAME_PATTERN_KINDS:
            raise ValueError(
                f'StampTemplate.template.name_patterns.{role_kind} is not supported. '
                f'Supported pattern kinds are: {", ".join(sorted(SUPPORTED_NAME_PATTERN_KINDS))}.'
            )
        if isinstance(raw_pattern, dict):
            pattern = _require_non_empty_string(
                raw_pattern.get('pattern'),
                f'StampTemplate.template.name_patterns.{role_kind}.pattern',
            )
            sample_count = raw_pattern.get('sample_count')
            if sample_count is not None:
                sample_count = _require_positive_int(
                    sample_count,
                    f'StampTemplate.template.name_patterns.{role_kind}.sample_count',
                )
            else:
                sample_count = 3
        else:
            pattern = _require_non_empty_string(raw_pattern, f'StampTemplate.template.name_patterns.{role_kind}')
            sample_count = 3
        unsupported = _pattern_variables(pattern, f'StampTemplate.template.name_patterns.{role_kind}') - (
            SUPPORTED_NAME_PATTERN_VARIABLES
        )
        if unsupported:
            raise ValueError(
                f'StampTemplate.template.name_patterns.{role_kind} uses unsupported variable(s): '
                f'{", ".join(sorted(unsupported))}.'
            )
        normalized[role_kind] = {
            'pattern': pattern,
            'sample_count': sample_count,
        }
    return normalized


def _validate_allocation_rule_override(template_spec: dict) -> str | None:
    raw_override = template_spec.get('allocation_rule_override')
    if raw_override is None:
        return None
    if isinstance(raw_override, dict):
        raw_override = raw_override.get('slug')
    return _require_non_empty_string(raw_override, 'StampTemplate.template.allocation_rule_override')


def _channel_map_matrix(template_spec: dict) -> list[dict]:
    channel_subinterfaces = template_spec.get('channel_subinterfaces') or {}
    matrix = channel_subinterfaces.get('channel_map_matrix') or []
    return [entry for entry in matrix if isinstance(entry, dict)]


def _active_positions_from_channel_map(template_spec: dict) -> set[int]:
    active_positions = set()
    for entry in _channel_map_matrix(template_spec):
        active_positions.update(int(position) for position in entry.get('positions') or ())
    return active_positions


def _normalize_dark_override_key(raw_key, planes: list[int]) -> int:
    if isinstance(raw_key, int) and not isinstance(raw_key, bool):
        plane = raw_key
    else:
        raw_label = str(raw_key).strip()
        if raw_label.lower().startswith('plane '):
            raw_label = raw_label.split(' ', 1)[1]
        if not raw_label.isdigit():
            raise ValueError(f'dark_position_overrides key {raw_key!r} must be a plane number or "Plane N".')
        plane = int(raw_label, 10)
    if plane not in set(planes):
        raise ValueError(f'dark_position_overrides key {raw_key!r} must reference a declared plane.')
    return plane


def _validate_dark_position_overrides(template_spec: dict, planes: list[int]) -> dict[int, list[int]]:
    overrides = template_spec.get('dark_position_overrides') or {}
    if not isinstance(overrides, dict):
        raise ValueError('StampTemplate.template.dark_position_overrides must be an object.')
    if not overrides:
        return {}

    active_positions = _active_positions_from_channel_map(template_spec)
    position_count_candidates = [
        _legacy_group_count(template_spec, 'gpu_tray', 'positions_per_mpo', 12),
        *(active_positions or {0}),
    ]
    if isinstance(template_spec.get('shuffle_cassettes'), dict):
        position_count_candidates.append(
            _legacy_group_count(template_spec, 'shuffle_cassettes', 'positions_per_mpo', 12)
        )
    position_count = max(position_count_candidates)
    all_positions = set(range(1, position_count + 1))
    normalized: dict[int, list[int]] = {}
    for raw_plane, raw_positions in overrides.items():
        plane = _normalize_dark_override_key(raw_plane, planes)
        positions = _require_sequence(
            raw_positions,
            f'StampTemplate.template.dark_position_overrides[{raw_plane!r}]',
        )
        normalized_positions = []
        for index, raw_position in enumerate(positions, start=1):
            position = _require_positive_int(
                raw_position,
                f'StampTemplate.template.dark_position_overrides[{raw_plane!r}][{index}]',
            )
            if position > position_count:
                raise ValueError(
                    f'StampTemplate.template.dark_position_overrides[{raw_plane!r}][{index}] '
                    'exceeds the MPO position count.'
                )
            normalized_positions.append(position)
        if len(set(normalized_positions)) != len(normalized_positions):
            raise ValueError(f'StampTemplate.template.dark_position_overrides[{raw_plane!r}] must not contain duplicates.')
        dark_positions = set(normalized_positions)
        overlap = active_positions & dark_positions
        if overlap:
            raise ValueError(
                f'StampTemplate.template.dark_position_overrides[{raw_plane!r}] marks active position(s) '
                f'{", ".join(map(str, sorted(overlap)))} as dark.'
            )
        if active_positions | dark_positions != all_positions:
            raise ValueError(
                f'StampTemplate.template.dark_position_overrides[{raw_plane!r}] must cover every MPO position '
                'when combined with the active channel map positions.'
            )
        normalized[plane] = sorted(dark_positions)
    return normalized


def _validate_fabric_ownership(template_spec: dict) -> dict:
    ownership = template_spec.get('fabric_ownership') or {}
    if not isinstance(ownership, dict):
        raise ValueError('StampTemplate.template.fabric_ownership must be an object.')
    normalized = {}
    for key in ('tenant_slug', 'scope_site_slug', 'scope_location_slug'):
        if ownership.get(key) is not None:
            normalized[key] = _require_non_empty_string(
                ownership.get(key),
                f'StampTemplate.template.fabric_ownership.{key}',
            )
    return normalized


def _validate_group_spec(group_spec: dict, group_name: str, keys: tuple[str, ...]) -> None:
    group = _require_mapping(group_spec[group_name], f'StampTemplate.template.{group_name}')
    _require_keys(group, keys, f'StampTemplate.template.{group_name}')
    for key in keys:
        _require_positive_int(group[key], f'StampTemplate.template.{group_name}.{key}')


def _validate_source_bindings(template_spec: dict) -> None:
    source_bindings = _require_sequence(template_spec['source_bindings'], 'StampTemplate.template.source_bindings')
    by_address = {}
    addresses = set()
    field_names = set()
    for index, binding_spec in enumerate(source_bindings, start=1):
        binding = _require_mapping(binding_spec, f'StampTemplate.template.source_bindings[{index}]')
        _require_keys(binding, _REQUIRED_SOURCE_BINDING_KEYS, f'StampTemplate.template.source_bindings[{index}]')
        kind = binding['kind']
        if kind not in {'node', 'endpoint'}:
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].kind must be "node" or "endpoint".'
            )
        address = _require_non_empty_string(
            binding['address'],
            f'StampTemplate.template.source_bindings[{index}].address',
        )
        field_name = _require_non_empty_string(
            binding['field_name'],
            f'StampTemplate.template.source_bindings[{index}].field_name',
        )
        model = _require_non_empty_string(
            binding['model'],
            f'StampTemplate.template.source_bindings[{index}].model',
        )
        if model not in SUPPORTED_SOURCE_BINDING_MODELS:
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].model '
                f'must be one of: {", ".join(sorted(SUPPORTED_SOURCE_BINDING_MODELS))}.'
            )
        if address in addresses:
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].address duplicates an earlier binding.'
            )
        if field_name in field_names:
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].field_name duplicates an earlier binding.'
            )
        addresses.add(address)
        field_names.add(field_name)
        by_address[address] = binding

    for index, binding_spec in enumerate(source_bindings, start=1):
        binding = _require_mapping(binding_spec, f'StampTemplate.template.source_bindings[{index}]')
        device_binding_address = binding.get('device_binding_address')
        if device_binding_address is None:
            continue
        if binding.get('kind') != 'endpoint':
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].device_binding_address '
                'is only valid for endpoint bindings.'
            )
        if binding.get('model') != 'dcim.interface':
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].model must be "dcim.interface" '
                'when device_binding_address is set.'
            )
        device_binding_address = _require_non_empty_string(
            device_binding_address,
            f'StampTemplate.template.source_bindings[{index}].device_binding_address',
        )
        device_binding = by_address.get(device_binding_address)
        if device_binding is None:
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].device_binding_address '
                'must reference an existing source binding address.'
            )
        if device_binding.get('kind') != 'node' or device_binding.get('model') != 'dcim.device':
            raise ValueError(
                f'StampTemplate.template.source_bindings[{index}].device_binding_address '
                'must reference a node binding with model "dcim.device".'
            )


def _coerce_positive_int(raw_value, path: str) -> int:
    if _is_positive_int(raw_value):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip().isdigit():
        parsed = int(raw_value.strip(), 10)
        if parsed > 0:
            return parsed
    raise ValueError(f'{path} must be a positive integer.')


def _validate_leaf_plane_assignment(template_spec: dict, planes: list[int]) -> None:
    leaf_spec = _require_mapping(template_spec['leaf_ports'], 'StampTemplate.template.leaf_ports')
    assignment = _require_mapping(
        leaf_spec.get('plane_assignment'),
        'StampTemplate.template.leaf_ports.plane_assignment',
    )
    leaf_count = _require_positive_int(leaf_spec['count'], 'StampTemplate.template.leaf_ports.count')
    valid_planes = set(planes)

    normalized = {}
    for raw_leaf_index, raw_plane in assignment.items():
        leaf_index = _coerce_positive_int(
            raw_leaf_index,
            'StampTemplate.template.leaf_ports.plane_assignment key',
        )
        plane = _coerce_positive_int(
            raw_plane,
            f'StampTemplate.template.leaf_ports.plane_assignment[{raw_leaf_index}]',
        )
        if plane not in valid_planes:
            raise ValueError(
                f'StampTemplate.template.leaf_ports.plane_assignment[{raw_leaf_index}] '
                'must reference a plane in StampTemplate.template.planes.'
            )
        if leaf_index in normalized:
            raise ValueError(
                f'StampTemplate.template.leaf_ports.plane_assignment has duplicate leaf index {leaf_index}.'
            )
        normalized[leaf_index] = plane

    required_leaf_indexes = set(range(1, leaf_count + 1))
    if set(normalized) != required_leaf_indexes:
        raise ValueError(
            'StampTemplate.template.leaf_ports.plane_assignment must define exactly one entry for each leaf index.'
        )


def _validate_proof_paths(template_spec: dict, planes: list[int], *, connection_geometry: str) -> None:
    proof_paths = _require_sequence(template_spec['proof_paths'], 'StampTemplate.template.proof_paths')
    if not proof_paths:
        raise ValueError('StampTemplate.template.proof_paths must have at least one path.')

    gpu_spec = _require_mapping(template_spec['gpu_tray'], 'StampTemplate.template.gpu_tray')
    leaf_spec = _require_mapping(template_spec['leaf_ports'], 'StampTemplate.template.leaf_ports')

    valid_planes = set(planes)
    max_gpu_osfp = _require_positive_int(gpu_spec['osfp_count'], 'StampTemplate.template.gpu_tray.osfp_count')
    max_leaf = _require_positive_int(leaf_spec['count'], 'StampTemplate.template.leaf_ports.count')
    if connection_geometry == 'direct_attach':
        required_keys = _DIRECT_ATTACH_PROOF_PATH_KEYS
        max_cassette = None
        max_front_position = _require_positive_int(
            gpu_spec['positions_per_mpo'],
            'StampTemplate.template.gpu_tray.positions_per_mpo',
        )
    else:
        required_keys = _REQUIRED_PROOF_PATH_KEYS
        shuffle_spec = _require_mapping(template_spec['shuffle_cassettes'], 'StampTemplate.template.shuffle_cassettes')
        max_cassette = _require_positive_int(
            shuffle_spec['count'],
            'StampTemplate.template.shuffle_cassettes.count',
        )
        max_front_position = _require_positive_int(
            shuffle_spec['positions_per_mpo'],
            'StampTemplate.template.shuffle_cassettes.positions_per_mpo',
        )
    max_rear_position = max_front_position

    for index, proof_path_spec in enumerate(proof_paths, start=1):
        proof_path = _require_mapping(proof_path_spec, f'StampTemplate.template.proof_paths[{index}]')
        _require_keys(proof_path, required_keys, f'StampTemplate.template.proof_paths[{index}]')

        plane = _require_positive_int(
            proof_path['plane'],
            f'StampTemplate.template.proof_paths[{index}].plane',
        )
        if plane not in valid_planes:
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].plane must reference a plane in '
                'StampTemplate.template.planes.'
            )

        gpu_osfp = _require_positive_int(
            proof_path['gpu_osfp'],
            f'StampTemplate.template.proof_paths[{index}].gpu_osfp',
        )
        if gpu_osfp > max_gpu_osfp:
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].gpu_osfp exceeds '
                'StampTemplate.template.gpu_tray.osfp_count.'
            )

        if connection_geometry != 'direct_attach':
            cassette = _require_positive_int(
                proof_path['cassette'],
                f'StampTemplate.template.proof_paths[{index}].cassette',
            )
            if cassette > max_cassette:
                raise ValueError(
                    f'StampTemplate.template.proof_paths[{index}].cassette exceeds '
                    'StampTemplate.template.shuffle_cassettes.count.'
                )

        front_position = _require_positive_int(
            proof_path['front_position'],
            f'StampTemplate.template.proof_paths[{index}].front_position',
        )
        if front_position > max_front_position:
            position_limit_path = (
                'StampTemplate.template.gpu_tray.positions_per_mpo'
                if connection_geometry == 'direct_attach'
                else 'StampTemplate.template.shuffle_cassettes.positions_per_mpo'
            )
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].front_position exceeds '
                f'{position_limit_path}.'
            )

        rear_position = _require_positive_int(
            proof_path['rear_position'],
            f'StampTemplate.template.proof_paths[{index}].rear_position',
        )
        if rear_position > max_rear_position:
            position_limit_path = (
                'StampTemplate.template.gpu_tray.positions_per_mpo'
                if connection_geometry == 'direct_attach'
                else 'StampTemplate.template.shuffle_cassettes.positions_per_mpo'
            )
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].rear_position exceeds '
                f'{position_limit_path}.'
            )

        leaf = _require_positive_int(
            proof_path['leaf'],
            f'StampTemplate.template.proof_paths[{index}].leaf',
        )
        if leaf > max_leaf:
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].leaf exceeds '
                'StampTemplate.template.leaf_ports.count.'
            )


def _validate_channel_subinterfaces(template_spec: dict) -> None:
    channel_subinterfaces = _require_mapping(
        template_spec.get('channel_subinterfaces'),
        'StampTemplate.template.channel_subinterfaces',
    )
    enabled = channel_subinterfaces.get('enabled')
    if not isinstance(enabled, bool):
        raise ValueError('StampTemplate.template.channel_subinterfaces.enabled must be a boolean.')
    if not enabled:
        return

    _require_non_empty_string(
        channel_subinterfaces.get('name_pattern'),
        'StampTemplate.template.channel_subinterfaces.name_pattern',
    )
    _require_non_empty_string(
        channel_subinterfaces.get('type'),
        'StampTemplate.template.channel_subinterfaces.type',
    )
    _require_positive_int(
        channel_subinterfaces.get('speed_gbps'),
        'StampTemplate.template.channel_subinterfaces.speed_gbps',
    )

    gpu_spec = _require_mapping(template_spec['gpu_tray'], 'StampTemplate.template.gpu_tray')
    max_mpo_index = _require_positive_int(
        gpu_spec['mpo_per_osfp'],
        'StampTemplate.template.gpu_tray.mpo_per_osfp',
    )
    max_position = _require_positive_int(
        gpu_spec['positions_per_mpo'],
        'StampTemplate.template.gpu_tray.positions_per_mpo',
    )

    matrix = _require_sequence(
        channel_subinterfaces.get('channel_map_matrix'),
        'StampTemplate.template.channel_subinterfaces.channel_map_matrix',
    )
    if not matrix:
        raise ValueError('StampTemplate.template.channel_subinterfaces.channel_map_matrix must not be empty.')

    assigned_positions = set()
    for index, entry_spec in enumerate(matrix, start=1):
        entry = _require_mapping(
            entry_spec,
            f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}]',
        )
        subinterface_index = _require_positive_int(
            entry.get('subinterface_index'),
            f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].subinterface_index',
        )
        mpo_index = _require_positive_int(
            entry.get('mpo_index'),
            f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].mpo_index',
        )
        if mpo_index > max_mpo_index:
            raise ValueError(
                f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].mpo_index '
                'exceeds StampTemplate.template.gpu_tray.mpo_per_osfp.'
            )
        positions = _require_sequence(
            entry.get('positions'),
            f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].positions',
        )
        if not positions:
            raise ValueError(
                f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].positions '
                'must not be empty.'
            )
        normalized_positions = set()
        for pos_index, position in enumerate(positions, start=1):
            position_number = _require_positive_int(
                position,
                f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].positions[{pos_index}]',
            )
            if position_number > max_position:
                raise ValueError(
                    f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].positions[{pos_index}] '
                    'exceeds StampTemplate.template.gpu_tray.positions_per_mpo.'
                )
            if position_number in normalized_positions:
                raise ValueError(
                    f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}] has duplicate '
                    f'position {position_number}.'
                )
            normalized_positions.add(position_number)

            lane_key = (mpo_index, position_number)
            if lane_key in assigned_positions:
                raise ValueError(
                    f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}] duplicates '
                    f'MPO {mpo_index} position {position_number} in another mapping entry.'
                )
            assigned_positions.add(lane_key)

        if subinterface_index < 1:
            raise ValueError(
                f'StampTemplate.template.channel_subinterfaces.channel_map_matrix[{index}].subinterface_index '
                'must be >= 1.'
            )


def _normalize_stamp_template_spec(template_spec: dict, *, phase: str | None = None, filter_phase: bool = False) -> dict:
    if not isinstance(template_spec, dict):
        raise ValueError('StampTemplate.template must be an object.')
    normalized_spec = deepcopy(template_spec)
    _require_template_keys(normalized_spec)
    _validate_template_identity(normalized_spec)
    _validate_executor(normalized_spec)
    connection_geometry = _validate_connection_geometry(normalized_spec)
    topology_parameters = _resolved_topology_parameters(normalized_spec)
    _apply_topology_parameters(normalized_spec, topology_parameters)
    planes = _validate_planes(normalized_spec)
    _validate_group_spec(
        normalized_spec,
        'gpu_tray',
        ('count', 'osfp_count', 'mpo_per_osfp', 'positions_per_mpo'),
    )
    if connection_geometry != 'direct_attach':
        _validate_group_spec(
            normalized_spec,
            'shuffle_cassettes',
            ('count', 'front_mpo_count', 'rear_mpo_count', 'positions_per_mpo'),
        )
    _validate_group_spec(
        normalized_spec,
        'leaf_ports',
        ('count',),
    )
    _validate_leaf_plane_assignment(normalized_spec, planes)
    phase_manifest = _validate_stamp_phases(normalized_spec, planes, phase=phase)
    _validate_source_bindings(normalized_spec)
    _validate_channel_subinterfaces(normalized_spec)
    wavelength_plan = _validate_wavelength_plan(normalized_spec)
    name_patterns = _validate_name_patterns(normalized_spec)
    allocation_rule_override = _validate_allocation_rule_override(normalized_spec)
    dark_position_overrides = _validate_dark_position_overrides(normalized_spec, planes)
    fabric_ownership = _validate_fabric_ownership(normalized_spec)
    _validate_proof_paths(normalized_spec, planes, connection_geometry=connection_geometry)

    active_planes = phase_manifest['active_planes']
    normalized_spec['_resolved'] = {
        'connection_geometry': connection_geometry,
        'all_planes': list(planes),
        'active_planes': list(active_planes),
        'topology_parameters': {
            key: value
            for key, value in topology_parameters.items()
            if not key.startswith('_')
        },
        'topology_parameter_metadata': topology_parameters.get('_metadata') or {},
        'phase': phase_manifest['selected'],
        'stamp_phases': phase_manifest['planned_phases'],
        'wavelength_plan': wavelength_plan,
        'name_patterns': name_patterns,
        'allocation_rule_override': allocation_rule_override,
        'dark_position_overrides': {
            str(plane): positions
            for plane, positions in sorted(dark_position_overrides.items())
        },
        'fabric_ownership': fabric_ownership,
    }
    if filter_phase and phase_manifest['selected'] is not None:
        active_plane_set = set(active_planes)
        normalized_spec['planes'] = list(active_planes)
        normalized_spec['proof_paths'] = [
            proof_path
            for proof_path in normalized_spec.get('proof_paths') or []
            if proof_path.get('plane') in active_plane_set
        ]
    return normalized_spec


def resolve_stamp_template_spec(template_spec: dict, *, phase: str | None = None) -> dict:
    return _normalize_stamp_template_spec(template_spec, phase=phase, filter_phase=True)


def validate_stamp_template_spec(template_spec: dict, *, phase: str | None = None) -> None:
    _normalize_stamp_template_spec(template_spec, phase=phase, filter_phase=False)
