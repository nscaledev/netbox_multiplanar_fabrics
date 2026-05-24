from __future__ import annotations

from collections import Counter
from decimal import Decimal

from dcim.models import Interface, Module, ModuleBay, ModuleType

from netbox_plant_graph.models import (
    Endpoint,
    FabricArchitecture,
    TransceiverConnector,
    TransceiverConnectorProfile,
    TransceiverLaneProfile,
    TransceiverProfile,
    TransceiverProfileModuleType,
)
from netbox_plant_graph.services.architecture import CHANNEL_MAP_MATRIX, MPO_POSITION_COUNT


DEFAULT_WAVELENGTH_NM = Decimal('1310.000')
PROFILE_SOURCE = 'madison_network_bom_2026_04_20'
DEFAULT_OSFP_4X200_PROFILE_SLUG = 'osfp-dual-mpo12-apc-800g-4x200g-dr4'

ROLE_HINT_BY_ENDPOINT_ROLE = {
    'gpu_osfp': 'gb300_compute_osfp',
    'h100_osfp': 'gb300_compute_osfp',
    'leaf_osfp': 'backend_leaf_osfp',
    'spine_osfp': 'backend_spine_osfp',
}


TRANSCEIVER_PROFILE_DEFINITIONS = (
    {
        'slug': 'osfp-dual-mpo12-apc-800g-4x200g-dr4',
        'name': 'OSFP dual-MPO12/APC 4x200G DR4 operating mode',
        'form_factor': 'other',
        'media_type': '2dr4',
        'aggregate_rate_gbps': 800,
        'channel_count': 4,
        'channel_rate_gbps': 200,
        'connector_count': 2,
        'connector_family': 'mpo-12',
        'position_count': MPO_POSITION_COUNT,
        'polish': 'apc',
        'pinning': 'not_specified',
        'channel_map_matrix': tuple(dict(entry) for entry in CHANNEL_MAP_MATRIX),
        'module_part_numbers': (
            'MMS4X00-NM',
            'MMS4X00-NM-T',
            'MMS4X00-NM-FLT',
            'MMS4A00-XM',
        ),
        'role_hints': ('gb300_compute_osfp', 'backend_leaf_osfp', 'backend_spine_osfp'),
    },
    {
        'slug': 'osfp224-mpo12-apc-800g-4x200g-dr4',
        'name': 'OSFP224 MPO12/APC 800G DR4 in 4x200G mode',
        'form_factor': 'osfp224',
        'media_type': 'dr4',
        'aggregate_rate_gbps': 800,
        'channel_count': 4,
        'channel_rate_gbps': 200,
        'connector_count': 1,
        'connector_family': 'mpo-12',
        'position_count': MPO_POSITION_COUNT,
        'polish': 'apc',
        'pinning': 'not_specified',
        'channel_map_matrix': (),
        'module_part_numbers': ('MMS4A20-XM800',),
        'role_hints': (),
    },
    {
        'slug': 'osfp224-dual-mpo12-apc-1600g-8x200g-2dr4',
        'name': 'OSFP224 dual-MPO12/APC 1600G 2DR4 in 8x200G mode',
        'form_factor': 'osfp224',
        'media_type': '2dr4',
        'aggregate_rate_gbps': 1600,
        'channel_count': 8,
        'channel_rate_gbps': 200,
        'connector_count': 2,
        'connector_family': 'mpo-12',
        'position_count': MPO_POSITION_COUNT,
        'polish': 'apc',
        'pinning': 'not_specified',
        'channel_map_matrix': (),
        'module_part_numbers': ('MMS4A00-XM',),
        'role_hints': (),
    },
    {
        'slug': 'osfp112-mpo12-apc-400g-4x100g-dr4',
        'name': 'OSFP112 MPO12/APC 400G DR4',
        'form_factor': 'osfp112',
        'media_type': 'dr4',
        'aggregate_rate_gbps': 400,
        'channel_count': 4,
        'channel_rate_gbps': 100,
        'connector_count': 1,
        'connector_family': 'mpo-12',
        'position_count': MPO_POSITION_COUNT,
        'polish': 'apc',
        'pinning': 'not_specified',
        'channel_map_matrix': (),
        'module_part_numbers': ('MMS4X00-NS400',),
        'role_hints': (),
    },
    {
        'slug': 'qsfp112-mpo12-apc-400g-dr4',
        'name': 'QSFP112 MPO12/APC 400G DR4',
        'form_factor': 'qsfp112',
        'media_type': 'dr4',
        'aggregate_rate_gbps': 400,
        'channel_count': 4,
        'channel_rate_gbps': 100,
        'connector_count': 1,
        'connector_family': 'mpo-12',
        'position_count': MPO_POSITION_COUNT,
        'polish': 'apc',
        'pinning': 'not_specified',
        'channel_map_matrix': (),
        'module_part_numbers': ('MMS1X00-NS400',),
        'role_hints': ('backend_spine_qsfp',),
    },
    {
        'slug': 'qsfpdd-mpo12-apc-400g-dr4',
        'name': 'QSFP-DD MPO12/APC 400G DR4',
        'form_factor': 'qsfpdd',
        'media_type': 'dr4',
        'aggregate_rate_gbps': 400,
        'channel_count': 4,
        'channel_rate_gbps': 100,
        'connector_count': 1,
        'connector_family': 'mpo-12',
        'position_count': MPO_POSITION_COUNT,
        'polish': 'apc',
        'pinning': 'not_specified',
        'channel_map_matrix': (),
        'module_part_numbers': ('MMS1V00-WM',),
        'role_hints': ('oob_qsfpdd',),
    },
    {
        'slug': 'qsfp28-lc-upc-100g-dr1',
        'name': 'QSFP28 LC/UPC 100G DR1',
        'form_factor': 'qsfp28',
        'media_type': 'dr1',
        'aggregate_rate_gbps': 100,
        'channel_count': 1,
        'channel_rate_gbps': 100,
        'connector_count': 1,
        'connector_family': 'lc',
        'position_count': 2,
        'polish': 'upc',
        'pinning': 'not_applicable',
        'channel_map_matrix': (),
        'module_part_numbers': ('MMS1V70-CM',),
        'role_hints': ('sn2201_tor',),
    },
)


def ensure_builtin_transceiver_profiles(*, architecture: FabricArchitecture | None = None) -> Counter:
    counters: Counter = Counter()
    for definition in TRANSCEIVER_PROFILE_DEFINITIONS:
        profile = _upsert_profile(definition, architecture=architecture, counters=counters)
        _upsert_connector_profiles(profile, definition, counters=counters)
        _upsert_module_type_mappings(profile, definition, counters=counters)
    return counters


def transceiver_context_for_interface(interface: Interface | None) -> dict:
    """Return installed module/profile context for an OSFP-facing NetBox interface."""
    if interface is None or interface.pk is None:
        return {
            'module': None,
            'module_type': None,
            'profile': None,
            'profile_mappings': (),
            'connectors': (),
            'status': 'unselected',
        }

    module = (
        Module.objects.filter(
            device=interface.device,
            module_bay__name=interface.name,
        )
        .select_related('device', 'module_bay', 'module_type', 'module_type__manufacturer')
        .order_by('pk')
        .first()
    )
    if module is None:
        return {
            'module': None,
            'module_type': None,
            'profile': None,
            'profile_mappings': (),
            'connectors': (),
            'status': 'missing_module',
        }

    mappings = tuple(
        TransceiverProfileModuleType.objects.filter(module_type=module.module_type)
        .select_related('profile', 'module_type')
        .order_by('-is_default', 'role_hint', 'profile__name', 'pk')
    )
    profile = mappings[0].profile if mappings else None
    connectors = tuple(
        TransceiverConnector.objects.filter(module=module)
        .select_related('connector_profile', 'connector_profile__profile', 'endpoint')
        .order_by('connector_profile__connector_index', 'connector_profile__name', 'pk')
    )
    if profile is None:
        status = 'missing_profile'
    elif not connectors:
        status = 'missing_connector_bindings'
    else:
        status = 'bound'

    return {
        'module': module,
        'module_type': module.module_type,
        'profile': profile,
        'profile_mappings': mappings,
        'connectors': connectors,
        'status': status,
    }


def bind_transceiver_for_osfp_endpoint(
    *,
    endpoint: Endpoint,
    mpo_endpoints: dict[int, Endpoint],
    profile_slug: str | None = None,
    module_type: ModuleType | None = None,
    module_type_part_number: str | None = None,
    role_hint: str | None = None,
    create_module: bool = True,
) -> dict:
    ensure_builtin_transceiver_profiles(architecture=endpoint.fabric.architecture)
    source = endpoint.source
    if not isinstance(source, Interface):
        return {
            'status': 'skipped_no_interface_source',
            'endpoint_id': endpoint.pk,
            'module': None,
            'profile': None,
            'connectors': (),
            'created': {'module_bay_ids': [], 'module_ids': [], 'transceiver_connector_ids': []},
        }

    role_hint = _resolved_role_hint(endpoint, role_hint)
    module = _module_for_interface(source)
    if module_type is None:
        module_type = _resolve_module_type(
            module=module,
            module_type_part_number=module_type_part_number,
            profile_slug=profile_slug,
            role_hint=role_hint,
        )
    profile = _resolve_profile(
        profile_slug=profile_slug,
        module_type=module_type or getattr(module, 'module_type', None),
        role_hint=role_hint,
    )

    created = {'module_bay_ids': [], 'module_ids': [], 'transceiver_connector_ids': []}
    if module is None and create_module and module_type is not None:
        module_bay, module_bay_created = ModuleBay.objects.get_or_create(
            device=source.device,
            name=source.name,
            defaults={'position': _module_bay_position(source.name)},
        )
        if module_bay_created:
            created['module_bay_ids'].append(module_bay.pk)
        module, module_created = Module.objects.get_or_create(
            device=source.device,
            module_bay=module_bay,
            defaults={'module_type': module_type},
        )
        if module_created:
            created['module_ids'].append(module.pk)
    elif module is not None and module_type is None:
        module_type = module.module_type

    if profile is None:
        return {
            'status': 'missing_profile',
            'endpoint_id': endpoint.pk,
            'module': module,
            'module_type': module_type,
            'profile': None,
            'connectors': (),
            'created': created,
        }
    if module is None:
        return {
            'status': 'missing_module',
            'endpoint_id': endpoint.pk,
            'module': None,
            'module_type': module_type,
            'profile': profile,
            'connectors': (),
            'created': created,
        }

    connectors = []
    for connector_profile in profile.connector_profiles.order_by('connector_index', 'name'):
        mpo_endpoint = mpo_endpoints.get(connector_profile.connector_index)
        if mpo_endpoint is None:
            continue
        stale_for_endpoint = TransceiverConnector.objects.filter(endpoint=mpo_endpoint).exclude(
            module=module,
            connector_profile=connector_profile,
        )
        stale_for_endpoint.delete()
        connector, connector_created = TransceiverConnector.objects.update_or_create(
            module=module,
            connector_profile=connector_profile,
            defaults={
                'endpoint': mpo_endpoint,
                'metadata': {
                    'fixture': True,
                    'role_hint': role_hint or '',
                    'source_endpoint_id': endpoint.pk,
                },
            },
        )
        if connector_created:
            created['transceiver_connector_ids'].append(connector.pk)
        connectors.append(connector)

    status = 'bound' if connectors else 'missing_connector_bindings'
    return {
        'status': status,
        'endpoint_id': endpoint.pk,
        'module': module,
        'module_type': module_type,
        'profile': profile,
        'connectors': tuple(connectors),
        'created': created,
    }


def _resolved_role_hint(endpoint: Endpoint, role_hint: str | None) -> str:
    if role_hint:
        return role_hint
    metadata = endpoint.metadata if isinstance(endpoint.metadata, dict) else {}
    endpoint_role = metadata.get('role_slug') or ''
    return ROLE_HINT_BY_ENDPOINT_ROLE.get(endpoint_role, endpoint_role)


def _module_for_interface(interface: Interface) -> Module | None:
    return (
        Module.objects.filter(device=interface.device, module_bay__name=interface.name)
        .select_related('device', 'module_bay', 'module_type')
        .order_by('pk')
        .first()
    )


def _module_bay_position(interface_name: str) -> str:
    digits = ''.join(character for character in reversed(interface_name) if character.isdigit())
    return ''.join(reversed(digits)) or interface_name


def _resolve_module_type(
    *,
    module: Module | None,
    module_type_part_number: str | None,
    profile_slug: str | None,
    role_hint: str,
) -> ModuleType | None:
    if module is not None:
        return module.module_type
    if module_type_part_number:
        module_type = ModuleType.objects.filter(part_number=module_type_part_number).order_by('pk').first()
        if module_type is not None:
            return module_type
    profile = TransceiverProfile.objects.filter(slug=profile_slug or DEFAULT_OSFP_4X200_PROFILE_SLUG).first()
    if profile is None:
        return None
    mappings = profile.module_type_mappings.select_related('module_type').order_by('-is_default', 'role_hint', 'pk')
    if role_hint:
        role_mapping = mappings.filter(role_hint=role_hint).first()
        if role_mapping is not None:
            return role_mapping.module_type
    mapping = mappings.first()
    return mapping.module_type if mapping is not None else None


def _resolve_profile(
    *,
    profile_slug: str | None,
    module_type: ModuleType | None,
    role_hint: str,
) -> TransceiverProfile | None:
    if profile_slug:
        return TransceiverProfile.objects.filter(slug=profile_slug).first()
    if module_type is not None:
        mappings = TransceiverProfileModuleType.objects.filter(module_type=module_type).select_related('profile')
        if role_hint:
            mapping = mappings.filter(role_hint=role_hint).order_by('-is_default', 'profile__name', 'pk').first()
            if mapping is not None:
                return mapping.profile
        mapping = mappings.order_by('-is_default', 'role_hint', 'profile__name', 'pk').first()
        if mapping is not None:
            return mapping.profile
    return TransceiverProfile.objects.filter(slug=DEFAULT_OSFP_4X200_PROFILE_SLUG).first()


def _upsert_profile(definition: dict, *, architecture: FabricArchitecture | None, counters: Counter) -> TransceiverProfile:
    profile, created = TransceiverProfile.objects.update_or_create(
        slug=definition['slug'],
        defaults={
            'architecture': architecture,
            'name': definition['name'],
            'status': 'active',
            'form_factor': definition['form_factor'],
            'media_type': definition['media_type'],
            'aggregate_rate_gbps': definition['aggregate_rate_gbps'],
            'channel_count': definition['channel_count'],
            'channel_rate_gbps': definition['channel_rate_gbps'],
            'wavelength_plan': {
                'default_wavelength_nm': str(DEFAULT_WAVELENGTH_NM),
                'mode': 'single_lambda_per_modeled_lane',
            },
            'metadata': {
                'source': PROFILE_SOURCE,
                'module_part_numbers': list(definition.get('module_part_numbers') or ()),
                'role_hints': list(definition.get('role_hints') or ()),
                'channel_map_matrix': [dict(entry) for entry in definition.get('channel_map_matrix') or ()],
            },
        },
    )
    counters['transceiver_profiles_created' if created else 'transceiver_profiles_updated'] += 1
    return profile


def _upsert_connector_profiles(profile: TransceiverProfile, definition: dict, *, counters: Counter) -> None:
    connector_count = int(definition['connector_count'])
    expected_names = {f'MPO-{index}' for index in range(1, connector_count + 1)}
    if definition['connector_family'] == 'lc':
        expected_names = {'line'}

    profile.connector_profiles.exclude(name__in=expected_names).delete()
    for connector_index, name in enumerate(sorted(expected_names), start=1):
        connector_profile, created = TransceiverConnectorProfile.objects.update_or_create(
            profile=profile,
            name=name,
            defaults={
                'connector_index': connector_index,
                'connector_family': definition['connector_family'],
                'position_count': int(definition['position_count']),
                'polish': definition['polish'],
                'pinning': definition['pinning'],
                'key_orientation': 'key_down' if definition['connector_family'].startswith('mpo-') else '',
                'metadata': {'source': PROFILE_SOURCE},
            },
        )
        counters['transceiver_connector_profiles_created' if created else 'transceiver_connector_profiles_updated'] += 1
        _upsert_lane_profiles(connector_profile, connector_index=connector_index, definition=definition, counters=counters)


def _upsert_lane_profiles(
    connector_profile: TransceiverConnectorProfile,
    *,
    connector_index: int,
    definition: dict,
    counters: Counter,
) -> None:
    matrix = [
        dict(entry)
        for entry in definition.get('channel_map_matrix') or ()
        if int(entry.get('mpo_index') or 1) == connector_index
    ]
    if not matrix and definition['connector_family'] == 'lc':
        matrix = [{'subinterface_index': 1, 'mpo_index': 1, 'positions': [1, 2]}]

    kept_ids = set()
    lane_offset = (connector_index - 1) * 8
    lane_number = lane_offset
    for entry in matrix:
        channel_index = int(entry['subinterface_index'])
        for position in entry.get('positions') or ():
            lane_number += 1
            for direction in ('send', 'receive'):
                lane_profile, created = TransceiverLaneProfile.objects.update_or_create(
                    connector_profile=connector_profile,
                    channel_index=channel_index,
                    direction=direction,
                    mpo_position=int(position),
                    defaults={
                        'lane_index': lane_number,
                        'wavelength_nm': DEFAULT_WAVELENGTH_NM,
                        'nominal_rate_gbps': definition['channel_rate_gbps'],
                        'metadata': {
                            'source': PROFILE_SOURCE,
                            'mpo_index': connector_index,
                        },
                    },
                )
                kept_ids.add(lane_profile.pk)
                counters['transceiver_lane_profiles_created' if created else 'transceiver_lane_profiles_updated'] += 1

    stale = connector_profile.lane_profiles.exclude(id__in=kept_ids)
    deleted_count, _ = stale.delete()
    counters['transceiver_lane_profiles_deleted'] += deleted_count


def _upsert_module_type_mappings(profile: TransceiverProfile, definition: dict, *, counters: Counter) -> None:
    part_numbers = tuple(definition.get('module_part_numbers') or ())
    if not part_numbers:
        return
    module_types = ModuleType.objects.filter(part_number__in=part_numbers)
    role_hints = tuple(definition.get('role_hints') or ('',))
    for module_type in module_types:
        for role_hint in role_hints:
            _, created = TransceiverProfileModuleType.objects.update_or_create(
                profile=profile,
                module_type=module_type,
                role_hint=role_hint,
                defaults={
                    'is_default': True,
                    'metadata': {
                        'source': PROFILE_SOURCE,
                        'matched_by': 'part_number',
                        'part_number': module_type.part_number,
                    },
                },
            )
            counters['transceiver_module_type_mappings_created' if created else 'transceiver_module_type_mappings_updated'] += 1
