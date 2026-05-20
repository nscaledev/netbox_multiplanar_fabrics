from __future__ import annotations


SUPPORTED_SOURCE_BINDING_MODELS = frozenset({
    'dcim.device',
    'dcim.interface',
})

_REQUIRED_TEMPLATE_KEYS = (
    'executor',
    'planes',
    'gpu_tray',
    'shuffle_cassettes',
    'leaf_ports',
    'proof_paths',
    'source_bindings',
)

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


def _validate_executor(template_spec: dict) -> None:
    executor = _require_mapping(template_spec['executor'], 'StampTemplate.template.executor')
    mode = executor.get('mode')
    if mode != 'hybrid':
        raise ValueError('StampTemplate.template.executor.mode must be "hybrid".')
    primitive = executor.get('primitive')
    if not primitive:
        raise ValueError('StampTemplate.template.executor.primitive is required.')


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


def _validate_group_spec(group_spec: dict, group_name: str, keys: tuple[str, ...]) -> None:
    group = _require_mapping(group_spec[group_name], f'StampTemplate.template.{group_name}')
    _require_keys(group, keys, f'StampTemplate.template.{group_name}')
    for key in keys:
        _require_positive_int(group[key], f'StampTemplate.template.{group_name}.{key}')


def _validate_source_bindings(template_spec: dict) -> None:
    source_bindings = _require_sequence(template_spec['source_bindings'], 'StampTemplate.template.source_bindings')
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


def _validate_proof_paths(template_spec: dict, planes: list[int]) -> None:
    proof_paths = _require_sequence(template_spec['proof_paths'], 'StampTemplate.template.proof_paths')
    if not proof_paths:
        raise ValueError('StampTemplate.template.proof_paths must have at least one path.')

    gpu_spec = _require_mapping(template_spec['gpu_tray'], 'StampTemplate.template.gpu_tray')
    shuffle_spec = _require_mapping(template_spec['shuffle_cassettes'], 'StampTemplate.template.shuffle_cassettes')
    leaf_spec = _require_mapping(template_spec['leaf_ports'], 'StampTemplate.template.leaf_ports')

    valid_planes = set(planes)
    max_gpu_osfp = _require_positive_int(gpu_spec['osfp_count'], 'StampTemplate.template.gpu_tray.osfp_count')
    max_cassette = _require_positive_int(
        shuffle_spec['count'],
        'StampTemplate.template.shuffle_cassettes.count',
    )
    max_leaf = _require_positive_int(leaf_spec['count'], 'StampTemplate.template.leaf_ports.count')
    max_front_position = _require_positive_int(
        shuffle_spec['positions_per_mpo'],
        'StampTemplate.template.shuffle_cassettes.positions_per_mpo',
    )
    max_rear_position = max_front_position

    for index, proof_path_spec in enumerate(proof_paths, start=1):
        proof_path = _require_mapping(proof_path_spec, f'StampTemplate.template.proof_paths[{index}]')
        _require_keys(proof_path, _REQUIRED_PROOF_PATH_KEYS, f'StampTemplate.template.proof_paths[{index}]')

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
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].front_position exceeds '
                'StampTemplate.template.shuffle_cassettes.positions_per_mpo.'
            )

        rear_position = _require_positive_int(
            proof_path['rear_position'],
            f'StampTemplate.template.proof_paths[{index}].rear_position',
        )
        if rear_position > max_rear_position:
            raise ValueError(
                f'StampTemplate.template.proof_paths[{index}].rear_position exceeds '
                'StampTemplate.template.shuffle_cassettes.positions_per_mpo.'
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


def validate_stamp_template_spec(template_spec: dict) -> None:
    if not isinstance(template_spec, dict):
        raise ValueError('StampTemplate.template must be an object.')
    _require_keys(template_spec, _REQUIRED_TEMPLATE_KEYS, 'StampTemplate.template')
    _validate_executor(template_spec)
    planes = _validate_planes(template_spec)
    _validate_group_spec(
        template_spec,
        'gpu_tray',
        ('count', 'osfp_count', 'mpo_per_osfp', 'positions_per_mpo'),
    )
    _validate_group_spec(
        template_spec,
        'shuffle_cassettes',
        ('count', 'front_mpo_count', 'rear_mpo_count', 'positions_per_mpo'),
    )
    _validate_group_spec(
        template_spec,
        'leaf_ports',
        ('count',),
    )
    _validate_source_bindings(template_spec)
    _validate_proof_paths(template_spec, planes)
