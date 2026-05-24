from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.utils.text import slugify
from dcim.models import Device, DeviceRole, DeviceType, Interface, Location, ModuleType, Site
from tenancy.models import Tenant

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
    OpticalLane,
    Plane,
    StampTemplate,
    StampRun,
    StrandTermination,
    TransferMap,
    TransportChannel,
    TransportChannelPositionMap,
    TransferPattern,
    TransceiverConnector,
)
from netbox_plant_graph.services.audit import record_audit_event
from netbox_plant_graph.services.architecture import ArchitectureFixtureResult, ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.architecture_schema import ARCHITECTURE_SCHEMA_CONTRACT_VERSION
from netbox_plant_graph.services.blueprint_registry import (
    architecture_definition_to_payload,
    get_default_blueprint_registry,
)
from netbox_plant_graph.services.resolver import OpticalLanePath, resolve_optical_lane_path
from netbox_plant_graph.services.stamp_template_validation import resolve_stamp_template_spec
from netbox_plant_graph.services.transceivers import bind_transceiver_for_osfp_endpoint


DEFAULT_WAVELENGTHS_NM = {
    1: Decimal('1311.000'),
    2: Decimal('1313.000'),
    3: Decimal('1315.000'),
    4: Decimal('1317.000'),
}


@dataclass(frozen=True)
class MiniFabricStampResult:
    fabric: Fabric
    stamp_run: StampRun
    source_lanes: tuple[OpticalLane, ...]
    destination_lanes: tuple[OpticalLane, ...]
    resolved_paths: tuple[OpticalLanePath, ...]


@dataclass(frozen=True)
class StampExecutionContext:
    fixture: ArchitectureFixtureResult
    template: object
    template_spec: dict
    fabric_name: str
    fabric_slug: str
    source_bindings: dict
    netbox_created_objects: dict
    creation_options: dict
    actor: object | None
    phase: str | None = None


HYBRID_STAMP_EXECUTORS = {}


def register_stamp_executor(name):
    def decorator(func):
        HYBRID_STAMP_EXECUTORS[name] = func
        return func

    return decorator


def _address_prefix(template_spec: dict, section: str, fallback: str) -> str:
    value = (template_spec.get(section) or {}).get('address_prefix')
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _address_with_prefix(template_spec: dict, section: str, fallback: str, index: int) -> str:
    return f'{_address_prefix(template_spec, section, fallback)}-{int(index)}'


def _role_metadata(definition: dict) -> dict:
    metadata = definition.get('metadata')
    return dict(metadata) if isinstance(metadata, dict) else {}


def _persist_blueprint_architecture_fixture(*, template, template_spec: dict) -> ArchitectureFixtureResult:
    template_architecture_slug = template_spec.get('architecture_slug')
    template_architecture_version = template_spec.get('architecture_version')
    if not template_architecture_slug or not template_architecture_version:
        if getattr(template, 'architecture_id', None):
            return _fixture_from_persisted_architecture(template.architecture, stamp_template=template)
        return ensure_roce_4plane_shuffle_architecture()

    registry = get_default_blueprint_registry()
    try:
        entry = registry.get_blueprint(template_architecture_slug, template_architecture_version)
    except KeyError:
        if getattr(template, 'architecture_id', None):
            architecture = template.architecture
            if architecture.slug == template_architecture_slug and architecture.version == template_architecture_version:
                return _fixture_from_persisted_architecture(architecture, stamp_template=template)
        raise ValueError(
            f'No blueprint registered for architecture {template_architecture_slug!r} '
            f'version {template_architecture_version!r}.'
        )

    definition = entry.definition
    architecture, _ = FabricArchitecture.objects.update_or_create(
        slug=definition.slug,
        version=definition.version,
        defaults={
            'name': _blueprint_architecture_name(definition.slug),
            'status': definition.status,
            'fabric_class': definition.fabric_class,
            'plane_count': definition.plane_count,
            'description': entry.metadata.get('description') or f'Executable blueprint for {definition.slug}.',
            'metadata': {
                'schema_contract_version': ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
                'fabric_class': definition.fabric_class,
                'plane_range': {
                    'min_planes': definition.min_planes,
                    'max_planes': definition.max_planes,
                    'default_planes': definition.default_planes,
                },
                'parameter_schema': dict(entry.parameter_schema),
                'required_device_types': {
                    role_slug: list(slugs)
                    for role_slug, slugs in entry.required_device_types.items()
                },
                'blueprint': {
                    'slug': entry.slug,
                    'version': entry.version,
                    'lifecycle': entry.lifecycle,
                    'successor_version': entry.successor_version,
                    'metadata': dict(entry.metadata),
                    'definition': architecture_definition_to_payload(definition),
                },
                'semantics': {
                    'optical_lane_scope': 'transceiver_local',
                    'fiber_path_scope': 'connector_position_graph',
                    'netbox_cables': 'forbidden_for_modeled_fabric',
                },
            },
        },
    )

    roles = _sync_architecture_roles(architecture, definition.roles)
    transfer_patterns = _sync_transfer_patterns(architecture, definition.transfer_patterns)
    allocation_rule_sets = _sync_allocation_rule_sets(architecture, definition.allocation_rule_sets)
    if isinstance(template, StampTemplate) and template.architecture_id != architecture.pk:
        template.architecture = architecture
        template.save(update_fields=['architecture'])
    return ArchitectureFixtureResult(
        architecture=architecture,
        roles=roles,
        transfer_patterns=transfer_patterns,
        allocation_rule_sets=allocation_rule_sets,
        stamp_template=template,
    )


def _blueprint_architecture_name(slug: str) -> str:
    return slug.replace('-', ' ').upper().replace('ROCE', 'RoCE')


def _sync_architecture_roles(architecture: FabricArchitecture, definitions) -> dict[str, ArchitectureRole]:
    roles = {}
    for definition in definitions:
        role, _ = ArchitectureRole.objects.update_or_create(
            architecture=architecture,
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'role_kind': definition.get('role_kind', ''),
                'description': definition.get('description', ''),
                'metadata': _role_metadata(definition),
            },
        )
        roles[definition['slug']] = role
    return roles


def _sync_transfer_patterns(architecture: FabricArchitecture, definitions) -> dict[str, TransferPattern]:
    transfer_patterns = {}
    for definition in definitions:
        pattern, _ = TransferPattern.objects.update_or_create(
            architecture=architecture,
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'pattern_kind': definition.get('pattern_kind', 'identity'),
                'rule': definition.get('rule') or {},
                'metadata': definition.get('metadata') or {},
            },
        )
        transfer_patterns[definition['slug']] = pattern
    return transfer_patterns


def _sync_allocation_rule_sets(architecture: FabricArchitecture, definitions) -> dict[str, AllocationRuleSet]:
    allocation_rule_sets = {}
    for definition in definitions:
        rule_set, _ = AllocationRuleSet.objects.update_or_create(
            architecture=architecture,
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'rule': definition.get('rule') or {},
                'metadata': definition.get('metadata') or {},
            },
        )
        allocation_rule_sets[definition['slug']] = rule_set
    return allocation_rule_sets


def _fixture_from_persisted_architecture(
    architecture: FabricArchitecture,
    *,
    stamp_template,
) -> ArchitectureFixtureResult:
    return ArchitectureFixtureResult(
        architecture=architecture,
        roles={role.slug: role for role in architecture.roles.all()},
        transfer_patterns={pattern.slug: pattern for pattern in architecture.transfer_patterns.all()},
        allocation_rule_sets={rule_set.slug: rule_set for rule_set in architecture.allocation_rule_sets.all()},
        stamp_template=stamp_template,
    )


def _stamp_executor_name(template_spec: dict) -> str:
    executor = template_spec.get('executor') or {}
    if executor.get('mode') != 'hybrid':
        raise ValueError('StampTemplate.template.executor.mode must be "hybrid".')
    primitive = executor.get('primitive')
    if not primitive:
        raise ValueError('StampTemplate.template.executor.primitive is required.')
    return primitive


def _source_defaults(source) -> dict:
    if source is None:
        return {
            'source_type': None,
            'source_id': None,
        }
    return {
        'source_type': ContentType.objects.get_for_model(source, for_concrete_model=False),
        'source_id': source.pk,
    }


def _source_binding(context: StampExecutionContext, binding_kind: str, address: str):
    return (context.source_bindings.get(binding_kind) or {}).get(address)


def _resolved_spec(template_spec: dict) -> dict:
    resolved = template_spec.get('_resolved')
    return resolved if isinstance(resolved, dict) else {}


def _active_planes(template_spec: dict) -> tuple[int, ...]:
    resolved = _resolved_spec(template_spec)
    planes = resolved.get('active_planes') or template_spec.get('planes') or ()
    return tuple(int(plane) for plane in planes)


def _all_planes(template_spec: dict) -> tuple[int, ...]:
    resolved = _resolved_spec(template_spec)
    planes = resolved.get('all_planes') or template_spec.get('planes') or ()
    return tuple(int(plane) for plane in planes)


def _topology_parameters(template_spec: dict) -> dict:
    return dict(_resolved_spec(template_spec).get('topology_parameters') or {})


def _leaf_plane_assignment(template_spec: dict) -> dict[int, int]:
    assignment = (template_spec.get('leaf_ports') or {}).get('plane_assignment') or {}
    normalized = {}
    for raw_leaf_index, raw_plane in assignment.items():
        try:
            normalized[int(raw_leaf_index)] = int(raw_plane)
        except (TypeError, ValueError):
            continue
    return normalized


def _leaf_indexes_for_template(template_spec: dict) -> tuple[int, ...]:
    leaf_count = int((template_spec.get('leaf_ports') or {}).get('count') or 0)
    assignment = _leaf_plane_assignment(template_spec)
    active_planes = set(_active_planes(template_spec))
    if not active_planes:
        return tuple(range(1, leaf_count + 1))
    return tuple(
        leaf_index
        for leaf_index in range(1, leaf_count + 1)
        if assignment.get(leaf_index, leaf_index) in active_planes
    )


def _field_exists(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return False
    return True


def _device_optional_defaults(*, tenant=None, location=None) -> dict:
    defaults = {}
    if tenant is not None and _field_exists(Device, 'tenant'):
        defaults['tenant'] = tenant
    if location is not None and _field_exists(Device, 'location'):
        defaults['location'] = location
    return defaults


def resolve_fabric_ownership(template_spec: dict) -> dict:
    ownership = _resolved_spec(template_spec).get('fabric_ownership')
    if not isinstance(ownership, dict):
        ownership = template_spec.get('fabric_ownership') or {}
    tenant = None
    site = None
    location = None
    missing = {}

    tenant_slug = ownership.get('tenant_slug')
    if tenant_slug:
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            missing['tenant_slug'] = tenant_slug

    site_slug = ownership.get('scope_site_slug')
    if site_slug:
        site = Site.objects.filter(slug=site_slug).first()
        if site is None:
            missing['scope_site_slug'] = site_slug

    location_slug = ownership.get('scope_location_slug')
    if location_slug:
        location = Location.objects.filter(slug=location_slug).first()
        if location is None:
            missing['scope_location_slug'] = location_slug

    return {
        'requested': dict(ownership),
        'tenant': tenant,
        'site': site,
        'location': location,
        'missing': missing,
        'resolved': {
            'tenant_id': getattr(tenant, 'pk', None),
            'tenant_slug': getattr(tenant, 'slug', None),
            'tenant_name': getattr(tenant, 'name', ''),
            'scope_site_id': getattr(site, 'pk', None),
            'scope_site_slug': getattr(site, 'slug', None),
            'scope_site_name': getattr(site, 'name', ''),
            'scope_location_id': getattr(location, 'pk', None),
            'scope_location_slug': getattr(location, 'slug', None),
            'scope_location_name': getattr(location, 'name', ''),
        },
    }


def _fabric_ownership_defaults(template_spec: dict) -> dict:
    ownership = resolve_fabric_ownership(template_spec)
    defaults = {}
    if ownership['tenant'] is not None:
        defaults['tenant'] = ownership['tenant']
    if ownership['site'] is not None:
        defaults['scope_site'] = ownership['site']
    if ownership['location'] is not None:
        defaults['scope_location'] = ownership['location']
    return defaults


def _fabric_ownership_manifest(template_spec: dict) -> dict:
    ownership = resolve_fabric_ownership(template_spec)
    return {
        'requested': ownership['requested'],
        'resolved': {
            key: value
            for key, value in ownership['resolved'].items()
            if value not in (None, '')
        },
        'missing': ownership['missing'],
    }


def _merge_source_bindings(*bindings: dict) -> dict:
    merged = {
        'nodes': {},
        'endpoints': {},
    }
    for binding in bindings:
        if not binding:
            continue
        merged['nodes'].update(binding.get('nodes') or {})
        merged['endpoints'].update(binding.get('endpoints') or {})
    return merged


def _netbox_name(prefix: str, address: str) -> str:
    return f'{prefix}-{slugify(address)}'


def _name_pattern(template_spec: dict, role_kind: str, fallback: str) -> str:
    patterns = _resolved_spec(template_spec).get('name_patterns') or {}
    raw_pattern = patterns.get(role_kind) or patterns.get('device' if role_kind in {'gpu_tray', 'leaf_switch'} else '')
    if isinstance(raw_pattern, dict) and raw_pattern.get('pattern'):
        return raw_pattern['pattern']
    return fallback


def _osfp_index_from_address(address: str) -> int:
    marker = '.OSFP-'
    if marker not in address:
        return 1
    tail = address.split(marker, 1)[1]
    value = tail.split('.', 1)[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def _name_pattern_context(
    *,
    fabric_slug: str,
    node_address: str,
    tray_index: int = 1,
    plane_index: int = 1,
    port_index: int = 1,
    channel_index: int = 1,
    rack_id: int = 1,
    parent_name: str = '',
) -> dict:
    return {
        'rack_id': rack_id,
        'tray_index': tray_index,
        'plane_index': plane_index,
        'port_index': port_index,
        'channel_index': channel_index,
        'node_address': node_address,
        'fabric_slug': fabric_slug,
        'parent_name': parent_name,
    }


def _render_name_pattern(pattern: str, context: dict) -> str:
    return pattern.format(**context)


def _wavelength_channels_for_plan(template_spec: dict) -> tuple[Decimal, ...]:
    plan = _resolved_spec(template_spec).get('wavelength_plan')
    if not isinstance(plan, dict):
        return tuple(DEFAULT_WAVELENGTHS_NM[index] for index in sorted(DEFAULT_WAVELENGTHS_NM))

    raw_channels = plan.get('channels') or []
    if raw_channels:
        return tuple(Decimal(str(channel)) for channel in raw_channels)

    channel_count = int(plan.get('channel_count') or len(DEFAULT_WAVELENGTHS_NM))
    band = plan.get('band') or 'o_band'
    if band == 'c_band':
        base = Decimal('1531.000')
        step = Decimal('1.000')
    else:
        base = Decimal('1311.000')
        step = Decimal('2.000')
    return tuple(base + (step * Decimal(index - 1)) for index in range(1, channel_count + 1))


def _wavelength_for_plane(template_spec: dict, plane_number: int) -> Decimal:
    channels = _wavelength_channels_for_plan(template_spec)
    if not channels:
        return DEFAULT_WAVELENGTHS_NM.get(plane_number, DEFAULT_WAVELENGTHS_NM[1])
    index = (int(plane_number) - 1) % len(channels)
    return channels[index]


def _create_or_bind_netbox_sources(
    *,
    template_spec: dict,
    fabric_slug: str,
    creation_options: dict,
) -> tuple[dict, dict]:
    if not creation_options.get('enabled'):
        return {'nodes': {}, 'endpoints': {}}, {'devices': [], 'interfaces': []}

    ownership = resolve_fabric_ownership(template_spec)
    site = ownership['site'] or creation_options.get('site')
    tenant = ownership['tenant']
    location = ownership['location']
    gpu_device_type = creation_options.get('gpu_device_type')
    gpu_role = creation_options.get('gpu_role')
    leaf_device_type = creation_options.get('leaf_device_type') or gpu_device_type
    leaf_role = creation_options.get('leaf_role') or gpu_role
    name_prefix = creation_options.get('name_prefix') or fabric_slug
    if not isinstance(site, Site):
        raise ValueError('creation_options.site must be a Site when creation_options.enabled is true.')
    if not isinstance(gpu_device_type, DeviceType):
        raise ValueError('creation_options.gpu_device_type must be a DeviceType when enabled.')
    if not isinstance(gpu_role, DeviceRole):
        raise ValueError('creation_options.gpu_role must be a DeviceRole when enabled.')
    if not isinstance(leaf_device_type, DeviceType):
        raise ValueError('creation_options.leaf_device_type must be a DeviceType when enabled.')
    if not isinstance(leaf_role, DeviceRole):
        raise ValueError('creation_options.leaf_role must be a DeviceRole when enabled.')

    devices_created = []
    interfaces_created = []
    source_bindings = {
        'nodes': {},
        'endpoints': {},
    }

    gpu_count = template_spec['gpu_tray']['count']
    gpu_osfp_count = template_spec['gpu_tray']['osfp_count']
    leaf_indexes = _leaf_indexes_for_template(template_spec)

    for index in range(1, gpu_count + 1):
        address = _address_with_prefix(template_spec, 'gpu_tray', 'GB300-TRAY', index)
        device_name = _render_name_pattern(
            _name_pattern(template_spec, 'gpu_tray', _netbox_name(name_prefix, address)),
            _name_pattern_context(
                fabric_slug=fabric_slug,
                node_address=address,
                tray_index=index,
                rack_id=index,
            ),
        )
        device, created = Device.objects.get_or_create(
            name=device_name,
            defaults={
                'site': site,
                'device_type': gpu_device_type,
                'role': gpu_role,
                **_device_optional_defaults(tenant=tenant, location=location),
            },
        )
        if created:
            devices_created.append(device.pk)
        source_bindings['nodes'][address] = device

        for osfp_index in range(1, gpu_osfp_count + 1):
            interface_name = f'OSFP-{osfp_index}'
            interface, created = Interface.objects.get_or_create(
                device=device,
                name=interface_name,
                defaults={
                    'type': '800gbase-x-osfp',
                },
            )
            if created:
                interfaces_created.append(interface.pk)
            source_bindings['endpoints'][f'{address}.OSFP-{osfp_index}'] = interface

    assignment = _leaf_plane_assignment(template_spec)
    for index in leaf_indexes:
        address = _address_with_prefix(template_spec, 'leaf_ports', 'LEAF', index)
        plane_index = assignment.get(index, index)
        device_name = _render_name_pattern(
            _name_pattern(template_spec, 'leaf_switch', _netbox_name(name_prefix, address)),
            _name_pattern_context(
                fabric_slug=fabric_slug,
                node_address=address,
                tray_index=1,
                plane_index=plane_index,
                port_index=index,
                rack_id=index,
            ),
        )
        device, created = Device.objects.get_or_create(
            name=device_name,
            defaults={
                'site': site,
                'device_type': leaf_device_type,
                'role': leaf_role,
                **_device_optional_defaults(tenant=tenant, location=location),
            },
        )
        if created:
            devices_created.append(device.pk)
        source_bindings['nodes'][address] = device

        interface, created = Interface.objects.get_or_create(
            device=device,
            name='OSFP-1',
            defaults={
                'type': '800gbase-x-osfp',
            },
        )
        if created:
            interfaces_created.append(interface.pk)
        source_bindings['endpoints'][f'{address}.OSFP-1'] = interface

    return source_bindings, {
        'devices': devices_created,
        'interfaces': interfaces_created,
    }


def _node(
    *,
    fabric: Fabric,
    role,
    name: str,
    address: str,
    node_kind: str,
    local_index: int | None = None,
    parent: FabricNode | None = None,
    source=None,
    metadata: dict | None = None,
) -> FabricNode:
    node, _ = FabricNode.objects.update_or_create(
        fabric=fabric,
        address=address,
        defaults={
            'role': role,
            'parent': parent,
            'name': name,
            'node_kind': node_kind,
            'local_index': local_index,
            **_source_defaults(source),
            'metadata': metadata or {},
        },
    )
    return node


def _endpoint(
    *,
    fabric: Fabric,
    node: FabricNode,
    name: str,
    address: str,
    endpoint_kind: str,
    connector_kind: str,
    position_count: int = 0,
    parent: Endpoint | None = None,
    source=None,
    metadata: dict | None = None,
) -> Endpoint:
    endpoint, _ = Endpoint.objects.update_or_create(
        fabric=fabric,
        address=address,
        defaults={
            'node': node,
            'parent': parent,
            'name': name,
            'endpoint_kind': endpoint_kind,
            'connector_kind': connector_kind,
            'position_count': position_count,
            **_source_defaults(source),
            'metadata': metadata or {},
        },
    )
    if position_count:
        for position_number in range(1, position_count + 1):
            ConnectorPosition.objects.update_or_create(
                endpoint=endpoint,
                position_number=position_number,
                defaults={'label': str(position_number)},
            )
    return endpoint


def _position(endpoint: Endpoint, position_number: int) -> ConnectorPosition:
    return ConnectorPosition.objects.get(endpoint=endpoint, position_number=position_number)


def _channel(
    *,
    fabric: Fabric,
    endpoint: Endpoint,
    plane: Plane,
    name: str,
    channel_index: int,
    source_subinterface: Interface | None = None,
) -> TransportChannel:
    channel, _ = TransportChannel.objects.update_or_create(
        endpoint=endpoint,
        channel_index=channel_index,
        defaults={
            'fabric': fabric,
            'plane': plane,
            'name': name,
            'source_subinterface': source_subinterface,
            'speed_gbps': 200,
            'metadata': {'fixture': True},
        },
    )
    return channel


def _channel_subinterface_spec(template_spec: dict) -> dict:
    return template_spec.get('channel_subinterfaces') or {}


def _channel_map_matrix(template_spec: dict) -> list[dict]:
    channel_subinterfaces = _channel_subinterface_spec(template_spec)
    matrix = channel_subinterfaces.get('channel_map_matrix') or []
    return [entry for entry in matrix if isinstance(entry, dict)]


def _allocation_rule_override_slug(template_spec: dict) -> str | None:
    override = _resolved_spec(template_spec).get('allocation_rule_override')
    if override:
        return str(override)
    raw_override = template_spec.get('allocation_rule_override')
    if isinstance(raw_override, dict):
        raw_override = raw_override.get('slug')
    if isinstance(raw_override, str) and raw_override.strip():
        return raw_override.strip()
    return None


def _apply_allocation_rule_override(template_spec: dict, fixture: ArchitectureFixtureResult) -> dict | None:
    override_slug = _allocation_rule_override_slug(template_spec)
    if not override_slug:
        return None
    rule_set = fixture.allocation_rule_sets.get(override_slug)
    if rule_set is None:
        rule_set = fixture.architecture.allocation_rule_sets.filter(slug=override_slug).first()
    if rule_set is None:
        return {
            'slug': override_slug,
            'exists': False,
            'applied': False,
        }
    rule = rule_set.rule if isinstance(rule_set.rule, dict) else {}
    matrix = rule.get('channel_map_matrix') or []
    applied = False
    if isinstance(matrix, list) and matrix:
        channel_subinterfaces = template_spec.setdefault('channel_subinterfaces', {})
        channel_subinterfaces['channel_map_matrix'] = [dict(entry) for entry in matrix if isinstance(entry, dict)]
        applied = True
    return {
        'slug': override_slug,
        'exists': True,
        'applied': applied,
        'rule_set_id': rule_set.pk,
        'rule_set_name': rule_set.name,
    }


def _channel_index_for_local_mpo_position(
    *,
    template_spec: dict,
    mpo_index: int,
    position_number: int,
) -> int:
    for entry in _channel_map_matrix(template_spec):
        if entry.get('mpo_index') != mpo_index:
            continue
        if position_number in set(entry.get('positions') or []):
            return int(entry['subinterface_index'])
    raise ValueError(
        f'No channel_subinterfaces.channel_map_matrix entry found for MPO {mpo_index} position {position_number}.'
    )


def _stamp_channel_subinterface(
    *,
    endpoint: Endpoint,
    channel_index: int,
    name_pattern: str = '{parent_name}/{channel_index}',
    interface_type: str = 'virtual',
    speed_gbps: int = 200,
) -> Interface | None:
    source = endpoint.source
    if not isinstance(source, Interface):
        return None
    child_name = _render_name_pattern(
        name_pattern,
        _name_pattern_context(
            fabric_slug=endpoint.fabric.slug,
            node_address=endpoint.node.address,
            tray_index=endpoint.node.local_index or 1,
            plane_index=channel_index,
            port_index=_osfp_index_from_address(endpoint.address),
            channel_index=channel_index,
            parent_name=source.name,
        ),
    )
    subinterface, _ = Interface.objects.update_or_create(
        device=source.device,
        name=child_name,
        defaults={
            'type': interface_type,
            'parent': source,
            'enabled': source.enabled,
            'speed': speed_gbps * 1000000,
            'description': f'Stamped {speed_gbps}G channel {channel_index} for {source.name}',
        },
    )
    return subinterface


def _channel_position_map(
    *,
    channel: TransportChannel,
    mpo_endpoint: Endpoint,
    mpo_position: ConnectorPosition,
) -> TransportChannelPositionMap:
    channel_position_map, _ = TransportChannelPositionMap.objects.update_or_create(
        channel=channel,
        mpo_position=mpo_position,
        defaults={
            'mpo_endpoint': mpo_endpoint,
            'metadata': {'fixture': True},
        },
    )
    return channel_position_map


def _ensure_channel_subinterfaces_for_endpoint(*, template_spec: dict, endpoint: Endpoint) -> dict[int, Interface]:
    channel_subinterfaces = _channel_subinterface_spec(template_spec)
    if not channel_subinterfaces.get('enabled'):
        return {}

    subinterface_indexes = sorted({
        int(entry['subinterface_index'])
        for entry in _channel_map_matrix(template_spec)
        if entry.get('subinterface_index') is not None
    })
    if not subinterface_indexes:
        return {}

    name_pattern = _name_pattern(
        template_spec,
        'channel_subinterface',
        channel_subinterfaces.get('name_pattern') or '{parent_name}/{channel_index}',
    )
    interface_type = channel_subinterfaces.get('type') or 'virtual'
    speed_gbps = int(channel_subinterfaces.get('speed_gbps') or 200)
    stamped = {}
    for channel_index in subinterface_indexes:
        subinterface = _stamp_channel_subinterface(
            endpoint=endpoint,
            channel_index=channel_index,
            name_pattern=name_pattern,
            interface_type=interface_type,
            speed_gbps=speed_gbps,
        )
        if subinterface is not None:
            stamped[channel_index] = subinterface
    return stamped


def _sync_channel_position_maps_for_endpoint(
    *,
    template_spec: dict,
    channel: TransportChannel,
    mpo_endpoints: dict[int, Endpoint],
) -> None:
    for entry in _channel_map_matrix(template_spec):
        if int(entry.get('subinterface_index', 0)) != channel.channel_index:
            continue
        mpo_index = int(entry['mpo_index'])
        mpo_endpoint = mpo_endpoints.get(mpo_index)
        if mpo_endpoint is None:
            continue
        for position_number in entry.get('positions') or []:
            mpo_position = _position(mpo_endpoint, int(position_number))
            _channel_position_map(
                channel=channel,
                mpo_endpoint=mpo_endpoint,
                mpo_position=mpo_position,
            )


def _transceiver_config(template_spec: dict) -> dict:
    config = _resolved_spec(template_spec).get('transceivers')
    if not isinstance(config, dict):
        config = template_spec.get('transceivers')
    return dict(config) if isinstance(config, dict) else {}


def _transceiver_role_config(template_spec: dict, role_slug: str) -> dict:
    config = _transceiver_config(template_spec)
    aliases = {
        'gpu_osfp': ('gpu_osfp', 'gpu_tray', 'gpu'),
        'h100_osfp': ('h100_osfp', 'gpu_osfp', 'gpu_tray', 'gpu'),
        'leaf_osfp': ('leaf_osfp', 'leaf_switch', 'leaf'),
        'spine_osfp': ('spine_osfp', 'spine_switch', 'spine'),
    }.get(role_slug, (role_slug,))
    role_config = {}
    for alias in aliases:
        value = config.get(alias)
        if isinstance(value, dict):
            role_config.update(value)
    return role_config


def _creation_option_module_type(creation_options: dict, role_slug: str):
    aliases = {
        'gpu_osfp': ('gpu_transceiver_module_type', 'gpu_osfp_module_type', 'transceiver_module_type'),
        'h100_osfp': ('gpu_transceiver_module_type', 'h100_transceiver_module_type', 'transceiver_module_type'),
        'leaf_osfp': ('leaf_transceiver_module_type', 'leaf_osfp_module_type', 'transceiver_module_type'),
        'spine_osfp': ('spine_transceiver_module_type', 'spine_osfp_module_type', 'transceiver_module_type'),
    }.get(role_slug, ('transceiver_module_type',))
    for alias in aliases:
        value = creation_options.get(alias)
        if isinstance(value, ModuleType):
            return value
    return None


def _transceiver_module_part_number(template_spec: dict, role_slug: str, creation_options: dict) -> str | None:
    for key in (
        f'{role_slug}_module_type_part_number',
        f'{role_slug}_part_number',
        'transceiver_module_type_part_number',
        'module_type_part_number',
    ):
        value = creation_options.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    role_config = _transceiver_role_config(template_spec, role_slug)
    config = _transceiver_config(template_spec)
    value = role_config.get('module_type_part_number') or role_config.get('part_number') or config.get(
        'module_type_part_number'
    )
    return str(value).strip() if value not in (None, '') else None


def _transceiver_profile_slug(template_spec: dict, role_slug: str) -> str | None:
    role_config = _transceiver_role_config(template_spec, role_slug)
    config = _transceiver_config(template_spec)
    value = role_config.get('profile_slug') or config.get('default_profile_slug') or config.get('profile_slug')
    return str(value).strip() if value not in (None, '') else None


def _transceivers_enabled(template_spec: dict) -> bool:
    config = _transceiver_config(template_spec)
    return config.get('enabled', True) is not False


def _bind_stamped_transceiver(
    *,
    context: StampExecutionContext,
    endpoint: Endpoint,
    mpo_endpoints: dict[int, Endpoint],
    role_slug: str,
) -> dict:
    if not _transceivers_enabled(context.template_spec):
        return {'status': 'disabled', 'endpoint_id': endpoint.pk, 'created': {}}
    result = bind_transceiver_for_osfp_endpoint(
        endpoint=endpoint,
        mpo_endpoints=mpo_endpoints,
        profile_slug=_transceiver_profile_slug(context.template_spec, role_slug),
        module_type=_creation_option_module_type(context.creation_options, role_slug),
        module_type_part_number=_transceiver_module_part_number(
            context.template_spec,
            role_slug,
            context.creation_options,
        ),
        role_hint=_transceiver_role_config(context.template_spec, role_slug).get('role_hint'),
        create_module=True,
    )
    created = result.get('created') or {}
    for result_key, created_key in (
        ('module_bay_ids', 'module_bays'),
        ('module_ids', 'modules'),
        ('transceiver_connector_ids', 'transceiver_connectors'),
    ):
        ids = list(created.get(result_key) or ())
        if ids:
            context.netbox_created_objects.setdefault(created_key, []).extend(ids)
    return {
        'endpoint_id': endpoint.pk,
        'endpoint_address': endpoint.address,
        'status': result.get('status'),
        'module_id': getattr(result.get('module'), 'pk', None),
        'module_type_id': getattr(result.get('module_type'), 'pk', None),
        'profile_id': getattr(result.get('profile'), 'pk', None),
        'profile_slug': getattr(result.get('profile'), 'slug', None),
        'connector_ids': [connector.pk for connector in result.get('connectors') or ()],
        'role_slug': role_slug,
    }


def _fallback_cable_site() -> Site:
    site, _ = Site.objects.get_or_create(
        slug='mpf-unassigned',
        defaults={
            'name': 'MPF Unassigned',
            'status': 'active',
        },
    )
    return site


def _resolved_cable_site(*, fabric: Fabric, a_endpoint: Endpoint, b_endpoint: Endpoint) -> Site:
    if fabric.scope_site_id:
        return fabric.scope_site
    for endpoint in (a_endpoint, b_endpoint):
        source = endpoint.source
        device = getattr(source, 'device', None)
        site = getattr(device, 'site', None) or getattr(source, 'site', None)
        if isinstance(site, Site):
            return site
        node_source = endpoint.node.source
        node_site = getattr(node_source, 'site', None)
        if isinstance(node_site, Site):
            return node_site
    return _fallback_cable_site()


def _cable_assembly(
    *,
    fabric: Fabric,
    segment_name: str,
    segment_kind: str,
    a_endpoint: Endpoint,
    b_endpoint: Endpoint,
    wavelength_nm: Decimal,
) -> CableAssembly:
    site = _resolved_cable_site(fabric=fabric, a_endpoint=a_endpoint, b_endpoint=b_endpoint)
    cable_id = f'{fabric.slug}:{segment_name}'
    cable, _ = CableAssembly.objects.update_or_create(
        site=site,
        cable_id=cable_id,
        defaults={
            'manufacturer': 'Unknown',
            'serial_number': '',
            'model_id': segment_kind,
            'description': f'{segment_kind} cable assembly for {segment_name}',
            'parent_cable': None,
            'metadata': {
                'fixture': True,
                'fabric_id': fabric.pk,
                'wavelengths_nm': [str(wavelength_nm)],
            },
        },
    )
    return cable


def _fiber_strand(
    *,
    fabric: Fabric,
    name: str,
    a_endpoint: Endpoint,
    a_position: ConnectorPosition,
    b_endpoint: Endpoint,
    b_position: ConnectorPosition,
    wavelength_nm: Decimal,
    segment_kind: str = 'trunk',
) -> FiberStrand:
    segment, _ = FiberSegment.objects.update_or_create(
        fabric=fabric,
        name=name,
        defaults={
            'segment_kind': segment_kind,
            'a_endpoint': a_endpoint,
            'b_endpoint': b_endpoint,
            'metadata': {
                'fixture': True,
                'wavelengths_nm': [str(wavelength_nm)],
            },
        },
    )
    cable_assembly = _cable_assembly(
        fabric=fabric,
        segment_name=name,
        segment_kind=segment_kind,
        a_endpoint=a_endpoint,
        b_endpoint=b_endpoint,
        wavelength_nm=wavelength_nm,
    )
    strand, _ = FiberStrand.objects.update_or_create(
        segment=segment,
        strand_index=1,
        defaults={
            'cable_site': cable_assembly.site,
            'cable_id': cable_assembly.cable_id,
            'label': f'{name}.strand-1',
            'metadata': {
                'fixture': True,
                'wavelengths_nm': [str(wavelength_nm)],
            },
        },
    )
    StrandTermination.objects.update_or_create(
        mpo_position=a_position,
        defaults={
            'strand': strand,
            'mpo_endpoint': a_endpoint,
            'termination_index': 1,
            'label': f'{strand.label}:A',
            'metadata': {'fixture': True},
        },
    )
    StrandTermination.objects.update_or_create(
        mpo_position=b_position,
        defaults={
            'strand': strand,
            'mpo_endpoint': b_endpoint,
            'termination_index': 2,
            'label': f'{strand.label}:B',
            'metadata': {'fixture': True},
        },
    )
    return strand


def _optical_lane(
    *,
    fabric: Fabric,
    endpoint: Endpoint,
    channel: TransportChannel,
    plane: Plane,
    local_mpo_endpoint: Endpoint,
    local_mpo_position: ConnectorPosition,
    lane_index: int,
    local_mpo_index: int,
    direction: str,
    wavelength_nm: Decimal,
    pair_key: str,
) -> OpticalLane:
    lane, _ = OpticalLane.objects.update_or_create(
        endpoint=endpoint,
        lane_index=lane_index,
        direction=direction,
        defaults={
            'fabric': fabric,
            'channel': channel,
            'plane': plane,
            'local_mpo_endpoint': local_mpo_endpoint,
            'local_mpo_position': local_mpo_position,
            'local_mpo_index': local_mpo_index,
            'wavelength_nm': wavelength_nm,
            'pair_key': pair_key,
            'nominal_rate_gbps': 200,
            'metadata': {'fixture': True},
        },
    )
    return lane


def _path_summary(path: OpticalLanePath) -> dict:
    return {
        'path_found': path.path_found,
        'source_lane_id': path.source_lane_id,
        'destination_lane_id': path.destination_lane_id,
        'error': path.error,
        'steps': [
            {
                'step_type': step.step_type,
                'object_type': step.object_type,
                'object_id': step.object_id,
                'label': step.label,
                'metadata': step.metadata,
            }
            for step in path.steps
        ],
    }


def _managed_object_ids(fabric: Fabric) -> dict[str, list[int]]:
    return {
        'fabrics': [fabric.pk],
        'planes': list(
            Plane.objects.filter(fabric=fabric).order_by('plane_number').values_list('pk', flat=True)
        ),
        'nodes': list(
            FabricNode.objects.filter(fabric=fabric).order_by('address').values_list('pk', flat=True)
        ),
        'endpoints': list(
            Endpoint.objects.filter(fabric=fabric).order_by('address').values_list('pk', flat=True)
        ),
        'connector_positions': list(
            ConnectorPosition.objects.filter(endpoint__fabric=fabric)
            .order_by('endpoint__address', 'position_number')
            .values_list('pk', flat=True)
        ),
        'transceiver_connectors': list(
            TransceiverConnector.objects.filter(endpoint__fabric=fabric)
            .order_by('endpoint__address', 'connector_profile__connector_index', 'pk')
            .values_list('pk', flat=True)
        ),
        'transport_channels': list(
            TransportChannel.objects.filter(fabric=fabric)
            .order_by('endpoint__address', 'channel_index')
            .values_list('pk', flat=True)
        ),
        'transport_channel_position_maps': list(
            TransportChannelPositionMap.objects.filter(channel__fabric=fabric)
            .order_by('channel__endpoint__address', 'channel__channel_index', 'mpo_position__position_number', 'pk')
            .values_list('pk', flat=True)
        ),
        'fiber_segments': list(
            FiberSegment.objects.filter(fabric=fabric).order_by('name').values_list('pk', flat=True)
        ),
        'cable_assemblies': list(
            CableAssembly.objects.filter(metadata__fabric_id=fabric.pk)
            .order_by('site__name', 'cable_id', 'pk')
            .values_list('pk', flat=True)
        ),
        'fiber_strands': list(
            FiberStrand.objects.filter(segment__fabric=fabric)
            .order_by('segment__name', 'strand_index')
            .values_list('pk', flat=True)
        ),
        'strand_terminations': list(
            StrandTermination.objects.filter(strand__segment__fabric=fabric)
            .order_by('strand__segment__name', 'strand__strand_index', 'termination_index', 'pk')
            .values_list('pk', flat=True)
        ),
        'transfer_maps': list(
            TransferMap.objects.filter(fabric=fabric).order_by('pk').values_list('pk', flat=True)
        ),
        'optical_lanes': list(
            OpticalLane.objects.filter(fabric=fabric)
            .order_by('endpoint__address', 'lane_index', 'direction', 'pk')
            .values_list('pk', flat=True)
        ),
    }


def _managed_object_counts(managed_objects: dict[str, list[int]]) -> dict[str, int]:
    return {
        object_type: len(object_ids)
        for object_type, object_ids in managed_objects.items()
    }


def _stamp_manifest(template_spec: dict, *, fabric: Fabric, phase: str | None) -> dict:
    resolved = _resolved_spec(template_spec)
    return {
        'phase': resolved.get('phase') or ({'name': phase, 'planes': list(_active_planes(template_spec))} if phase else None),
        'active_planes': list(_active_planes(template_spec)),
        'all_planes': list(_all_planes(template_spec)),
        'stamp_phases': resolved.get('stamp_phases') or [],
        'topology_parameters': resolved.get('topology_parameters') or {},
        'wavelength_plan': resolved.get('wavelength_plan'),
        'allocation_rule_override': resolved.get('allocation_rule_override'),
        'dark_position_overrides': resolved.get('dark_position_overrides') or {},
        'fabric_ownership': _fabric_ownership_manifest(template_spec),
        'executed_phases': _executed_phase_records(fabric=fabric, template_spec=template_spec, phase=phase),
    }


def _executed_phase_records(*, fabric: Fabric, template_spec: dict, phase: str | None) -> list[dict]:
    records: list[dict] = []
    for run in StampRun.objects.filter(fabric=fabric, status='completed').order_by('created', 'pk'):
        result = run.result if isinstance(run.result, dict) else {}
        manifest = result.get('stamp_manifest') if isinstance(result.get('stamp_manifest'), dict) else {}
        phase_payload = manifest.get('phase') or {}
        phase_name = phase_payload.get('name') or (run.parameters or {}).get('phase')
        if not phase_name:
            continue
        records.append(
            {
                'name': phase_name,
                'planes': phase_payload.get('planes') or (run.parameters or {}).get('active_planes') or [],
                'stamp_run_id': run.pk,
            }
        )
    if phase:
        records.append(
            {
                'name': phase,
                'planes': list(_active_planes(template_spec)),
                'stamp_run_id': None,
            }
        )
    return records


@register_stamp_executor('roce_4plane_mini_proof')
def _execute_roce_4plane_mini_proof(context: StampExecutionContext) -> MiniFabricStampResult:
    fixture = context.fixture
    template = context.template
    template_spec = context.template_spec

    fabric_name = context.fabric_name
    fabric_slug = context.fabric_slug

    fabric, _ = Fabric.objects.update_or_create(
        slug=fabric_slug,
        defaults={
            'architecture': fixture.architecture,
            'name': fabric_name,
            'status': 'planned',
            **_fabric_ownership_defaults(template_spec),
            'metadata': {
                'fixture': True,
                'template_slug': template.slug,
                'stamp_executor': _stamp_executor_name(template_spec),
                'netbox_cables': 'forbidden_for_modeled_fabric',
                'topology_parameters': _topology_parameters(template_spec),
                'active_planes': list(_active_planes(template_spec)),
                'stamp_phase': context.phase,
            },
        },
    )

    planes = {}
    for plane_number in _active_planes(template_spec):
        plane, _ = Plane.objects.update_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'label': f'Plane {plane_number}',
                'metadata': {'fixture': True},
            },
        )
        planes[plane_number] = plane

    gpu_trays = {}
    gpu_osfps = {}
    gpu_mpos = {}
    gpu_mpos_by_osfp = {}
    gpu_spec = template_spec['gpu_tray']
    for tray_index in range(1, gpu_spec['count'] + 1):
        tray_address = _address_with_prefix(template_spec, 'gpu_tray', 'GB300-TRAY', tray_index)
        gpu_tray = _node(
            fabric=fabric,
            role=fixture.roles['gpu_tray'],
            name=tray_address,
            address=tray_address,
            node_kind='active_device',
            local_index=tray_index,
            source=_source_binding(context, 'nodes', tray_address),
            metadata={'fixture': True},
        )
        gpu_trays[tray_index] = gpu_tray
        for osfp_index in range(1, gpu_spec['osfp_count'] + 1):
            osfp = _endpoint(
                fabric=fabric,
                node=gpu_tray,
                name=f'OSFP-{osfp_index}',
                address=f'{gpu_tray.address}.OSFP-{osfp_index}',
                endpoint_kind='plugin_port',
                connector_kind='osfp',
                source=_source_binding(context, 'endpoints', f'{gpu_tray.address}.OSFP-{osfp_index}'),
                metadata={'fixture': True, 'role_slug': 'gpu_osfp'},
            )
            gpu_osfps[(tray_index, osfp_index)] = osfp
            gpu_mpos_by_osfp[(tray_index, osfp_index)] = {}
            for mpo_index in range(1, gpu_spec['mpo_per_osfp'] + 1):
                mpo = _endpoint(
                    fabric=fabric,
                    node=gpu_tray,
                    parent=osfp,
                    name=f'OSFP-{osfp_index}.MPO-{mpo_index}',
                    address=f'{gpu_tray.address}.OSFP-{osfp_index}.MPO-{mpo_index}',
                    endpoint_kind='subconnector',
                    connector_kind='mpo-12',
                    position_count=gpu_spec['positions_per_mpo'],
                    metadata={'fixture': True, 'role_slug': 'gpu_mpo', 'mpo_index': mpo_index},
                )
                gpu_mpos[(tray_index, osfp_index, mpo_index)] = mpo
                gpu_mpos_by_osfp[(tray_index, osfp_index)][mpo_index] = mpo

    shuffle_nodes = {}
    shuffle_front_mpos = {}
    shuffle_rear_mpos = {}
    shuffle_spec = template_spec['shuffle_cassettes']
    for cassette_index in range(1, shuffle_spec['count'] + 1):
        cassette = _node(
            fabric=fabric,
            role=fixture.roles['shuffle_cassette'],
            name=f'SHUFFLE-CASSETTE-{cassette_index}',
            address=f'SHUFFLE-CASSETTE-{cassette_index}',
            node_kind='passive_assembly',
            local_index=cassette_index,
            metadata={'fixture': True},
        )
        shuffle_nodes[cassette_index] = cassette
        for mpo_index in range(1, shuffle_spec['front_mpo_count'] + 1):
            shuffle_front_mpos[(cassette_index, mpo_index)] = _endpoint(
                fabric=fabric,
                node=cassette,
                name=f'FRONT.MPO-{mpo_index}',
                address=f'{cassette.address}.FRONT.MPO-{mpo_index}',
                endpoint_kind='connector',
                connector_kind='mpo-12',
                position_count=shuffle_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'shuffle_front_mpo', 'mpo_index': mpo_index},
            )
        for mpo_index in range(1, shuffle_spec['rear_mpo_count'] + 1):
            shuffle_rear_mpos[(cassette_index, mpo_index)] = _endpoint(
                fabric=fabric,
                node=cassette,
                name=f'REAR.MPO-{mpo_index}',
                address=f'{cassette.address}.REAR.MPO-{mpo_index}',
                endpoint_kind='connector',
                connector_kind='mpo-12',
                position_count=shuffle_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'shuffle_rear_mpo', 'mpo_index': mpo_index},
            )

    leaf_nodes = {}
    leaf_osfps = {}
    leaf_mpos = {}
    leaf_mpos_by_leaf = {}
    leaf_assignment = _leaf_plane_assignment(template_spec)
    for leaf_index in _leaf_indexes_for_template(template_spec):
        leaf_plane_number = leaf_assignment.get(leaf_index, leaf_index)
        leaf_address = _address_with_prefix(template_spec, 'leaf_ports', 'LEAF', leaf_index)
        leaf = _node(
            fabric=fabric,
            role=fixture.roles['leaf_switch'],
            name=leaf_address,
            address=leaf_address,
            node_kind='active_device',
            local_index=leaf_index,
            source=_source_binding(context, 'nodes', leaf_address),
            metadata={'fixture': True, 'plane_number': leaf_plane_number},
        )
        leaf_nodes[leaf_index] = leaf
        osfp = _endpoint(
            fabric=fabric,
            node=leaf,
            name='OSFP-1',
            address=f'{leaf.address}.OSFP-1',
            endpoint_kind='plugin_port',
            connector_kind='osfp',
            source=_source_binding(context, 'endpoints', f'{leaf.address}.OSFP-1'),
            metadata={'fixture': True, 'role_slug': 'leaf_osfp'},
        )
        leaf_osfps[leaf_index] = osfp
        leaf_mpos_by_leaf[leaf_index] = {}
        for mpo_index in range(1, gpu_spec['mpo_per_osfp'] + 1):
            leaf_mpos[(leaf_index, mpo_index)] = _endpoint(
                fabric=fabric,
                node=leaf,
                parent=osfp,
                name=f'OSFP-1.MPO-{mpo_index}',
                address=f'{leaf.address}.OSFP-1.MPO-{mpo_index}',
                endpoint_kind='subconnector',
                connector_kind='mpo-12',
                position_count=gpu_spec['positions_per_mpo'],
                metadata={'fixture': True, 'role_slug': 'leaf_mpo', 'mpo_index': mpo_index},
            )
            leaf_mpos_by_leaf[leaf_index][mpo_index] = leaf_mpos[(leaf_index, mpo_index)]

    channel_subinterfaces_by_endpoint = {}
    for osfp_endpoint in list(gpu_osfps.values()) + list(leaf_osfps.values()):
        channel_subinterfaces_by_endpoint[osfp_endpoint.address] = _ensure_channel_subinterfaces_for_endpoint(
            template_spec=template_spec,
            endpoint=osfp_endpoint,
        )

    transceiver_bindings = []
    for key, osfp_endpoint in gpu_osfps.items():
        transceiver_bindings.append(
            _bind_stamped_transceiver(
                context=context,
                endpoint=osfp_endpoint,
                mpo_endpoints=gpu_mpos_by_osfp[key],
                role_slug='gpu_osfp',
            )
        )
    for key, osfp_endpoint in leaf_osfps.items():
        transceiver_bindings.append(
            _bind_stamped_transceiver(
                context=context,
                endpoint=osfp_endpoint,
                mpo_endpoints=leaf_mpos_by_leaf[key],
                role_slug='leaf_osfp',
            )
        )

    source_lanes = []
    destination_lanes = []
    resolved_paths = []
    transfer_pattern = fixture.transfer_patterns['shuffle_2x2']

    for proof_path in template_spec['proof_paths']:
        plane_number = proof_path['plane']
        plane = planes[plane_number]
        wavelength_nm = _wavelength_for_plane(template_spec, plane_number)
        gpu_tray_index = int(proof_path.get('gpu_tray') or 1)
        gpu_osfp = gpu_osfps[(gpu_tray_index, proof_path['gpu_osfp'])]
        gpu_mpo_index = 1
        gpu_mpo = gpu_mpos[(gpu_tray_index, proof_path['gpu_osfp'], gpu_mpo_index)]
        cassette = shuffle_nodes[proof_path['cassette']]
        shuffle_front_mpo = shuffle_front_mpos[(proof_path['cassette'], 1)]
        shuffle_rear_mpo = shuffle_rear_mpos[(proof_path['cassette'], 1)]
        leaf_osfp = leaf_osfps[proof_path['leaf']]
        leaf_mpo_index = 1
        leaf_mpo = leaf_mpos[(proof_path['leaf'], leaf_mpo_index)]

        gpu_position = _position(gpu_mpo, proof_path['front_position'])
        shuffle_front_position = _position(shuffle_front_mpo, proof_path['front_position'])
        shuffle_rear_position = _position(shuffle_rear_mpo, proof_path['rear_position'])
        leaf_position = _position(leaf_mpo, proof_path['rear_position'])
        gpu_channel_index = _channel_index_for_local_mpo_position(
            template_spec=template_spec,
            mpo_index=gpu_mpo_index,
            position_number=gpu_position.position_number,
        )
        leaf_channel_index = _channel_index_for_local_mpo_position(
            template_spec=template_spec,
            mpo_index=leaf_mpo_index,
            position_number=leaf_position.position_number,
        )

        pair_key = f'{fabric.slug}:plane-{plane_number}'
        gpu_channel = _channel(
            fabric=fabric,
            endpoint=gpu_osfp,
            plane=plane,
            name=f'GPU plane {plane_number}',
            channel_index=gpu_channel_index,
            source_subinterface=channel_subinterfaces_by_endpoint.get(gpu_osfp.address, {}).get(gpu_channel_index),
        )
        leaf_channel = _channel(
            fabric=fabric,
            endpoint=leaf_osfp,
            plane=plane,
            name=f'Leaf plane {plane_number}',
            channel_index=leaf_channel_index,
            source_subinterface=channel_subinterfaces_by_endpoint.get(leaf_osfp.address, {}).get(leaf_channel_index),
        )
        _sync_channel_position_maps_for_endpoint(
            template_spec=template_spec,
            channel=gpu_channel,
            mpo_endpoints=gpu_mpos_by_osfp[(gpu_tray_index, proof_path['gpu_osfp'])],
        )
        _sync_channel_position_maps_for_endpoint(
            template_spec=template_spec,
            channel=leaf_channel,
            mpo_endpoints=leaf_mpos_by_leaf[proof_path['leaf']],
        )

        _fiber_strand(
            fabric=fabric,
            name=f'P{plane_number}-GPU-to-SHUFFLE',
            a_endpoint=gpu_mpo,
            a_position=gpu_position,
            b_endpoint=shuffle_front_mpo,
            b_position=shuffle_front_position,
            wavelength_nm=wavelength_nm,
        )
        TransferMap.objects.update_or_create(
            fabric=fabric,
            owner_node=cassette,
            src_position=shuffle_front_position,
            dst_position=shuffle_rear_position,
            defaults={
                'owner_segment': None,
                'pattern': transfer_pattern,
                'map_kind': 'shuffle_2x2',
                'bidirectional': True,
                'group_key': pair_key,
                'metadata': {
                    'fixture': True,
                    'plane_number': plane_number,
                    'wavelength_nm': str(wavelength_nm),
                },
            },
        )
        _fiber_strand(
            fabric=fabric,
            name=f'P{plane_number}-SHUFFLE-to-LEAF',
            a_endpoint=shuffle_rear_mpo,
            a_position=shuffle_rear_position,
            b_endpoint=leaf_mpo,
            b_position=leaf_position,
            wavelength_nm=wavelength_nm,
        )

        source_lane = _optical_lane(
            fabric=fabric,
            endpoint=gpu_osfp,
            channel=gpu_channel,
            plane=plane,
            local_mpo_endpoint=gpu_mpo,
            local_mpo_position=gpu_position,
            lane_index=1,
            local_mpo_index=gpu_mpo_index,
            direction='send',
            wavelength_nm=wavelength_nm,
            pair_key=pair_key,
        )
        destination_lane = _optical_lane(
            fabric=fabric,
            endpoint=leaf_osfp,
            channel=leaf_channel,
            plane=plane,
            local_mpo_endpoint=leaf_mpo,
            local_mpo_position=leaf_position,
            lane_index=1,
            local_mpo_index=leaf_mpo_index,
            direction='receive',
            wavelength_nm=wavelength_nm,
            pair_key=pair_key,
        )
        path = resolve_optical_lane_path(source=source_lane, destination=destination_lane)

        source_lanes.append(source_lane)
        destination_lanes.append(destination_lane)
        resolved_paths.append(path)

    managed_objects = _managed_object_ids(fabric)
    stamp_run = StampRun.objects.create(
        template=template,
        fabric=fabric,
        status='completed',
        parameters={
            'fabric_name': fabric_name,
            'fabric_slug': fabric_slug,
            'template_slug': template.slug,
            'executor': _stamp_executor_name(template_spec),
            'source_binding_counts': {
                'nodes': len(context.source_bindings.get('nodes') or {}),
                'endpoints': len(context.source_bindings.get('endpoints') or {}),
            },
            'phase': context.phase,
            'active_planes': list(_active_planes(template_spec)),
            'all_planes': list(_all_planes(template_spec)),
            'topology_parameters': _topology_parameters(template_spec),
        },
        result={
            'fabric_id': fabric.pk,
            'managed_objects': managed_objects,
            'object_counts': _managed_object_counts(managed_objects),
            'netbox_created_objects': context.netbox_created_objects,
            'source_lane_ids': [lane.pk for lane in source_lanes],
            'destination_lane_ids': [lane.pk for lane in destination_lanes],
            'resolved_paths': [_path_summary(path) for path in resolved_paths],
            'stamp_manifest': _stamp_manifest(template_spec, fabric=fabric, phase=context.phase),
            'dark_position_overrides': _resolved_spec(template_spec).get('dark_position_overrides') or {},
            'wavelength_plan': _resolved_spec(template_spec).get('wavelength_plan'),
            'allocation_rule_override': _resolved_spec(template_spec).get('allocation_rule_override'),
            'fabric_ownership': _fabric_ownership_manifest(template_spec),
            'transceiver_bindings': transceiver_bindings,
        },
        metadata={'fixture': True},
    )
    record_audit_event(
        event_type='stamp',
        fabric=fabric,
        actor=context.actor,
        subject=stamp_run,
        outcome='ok',
        message=f'Stamp run completed for fabric {fabric.slug}.',
        payload={
            'stamp_run_id': stamp_run.pk,
            'template_id': template.pk,
            'source_lane_count': len(source_lanes),
            'destination_lane_count': len(destination_lanes),
        },
    )

    return MiniFabricStampResult(
        fabric=fabric,
        stamp_run=stamp_run,
        source_lanes=tuple(source_lanes),
        destination_lanes=tuple(destination_lanes),
        resolved_paths=tuple(resolved_paths),
    )


@register_stamp_executor('roce_gb300_shuffle_mini_proof')
def _execute_roce_gb300_shuffle_mini_proof(context: StampExecutionContext) -> MiniFabricStampResult:
    return _execute_roce_4plane_mini_proof(context)


@register_stamp_executor('roce_direct_attach_mini_proof')
def _execute_roce_direct_attach_mini_proof(context: StampExecutionContext) -> MiniFabricStampResult:
    fixture = context.fixture
    template = context.template
    template_spec = context.template_spec
    fabric_name = context.fabric_name
    fabric_slug = context.fabric_slug

    fabric, _ = Fabric.objects.update_or_create(
        slug=fabric_slug,
        defaults={
            'architecture': fixture.architecture,
            'name': fabric_name,
            'status': 'planned',
            **_fabric_ownership_defaults(template_spec),
            'metadata': {
                'fixture': True,
                'template_slug': template.slug,
                'stamp_executor': _stamp_executor_name(template_spec),
                'connection_geometry': 'direct_attach',
                'netbox_cables': 'forbidden_for_modeled_fabric',
                'topology_parameters': _topology_parameters(template_spec),
                'active_planes': list(_active_planes(template_spec)),
                'stamp_phase': context.phase,
            },
        },
    )

    planes = {}
    for plane_number in _active_planes(template_spec):
        plane, _ = Plane.objects.update_or_create(
            fabric=fabric,
            plane_number=plane_number,
            defaults={
                'label': f'Plane {plane_number}',
                'metadata': {'fixture': True},
            },
        )
        planes[plane_number] = plane

    gpu_role = fixture.roles.get('h100_node') or fixture.roles.get('gpu_tray')
    gpu_spec = template_spec['gpu_tray']
    gpu_nodes = {}
    gpu_osfps = {}
    gpu_mpos = {}
    gpu_mpos_by_osfp = {}
    for node_index in range(1, gpu_spec['count'] + 1):
        node_address = _address_with_prefix(template_spec, 'gpu_tray', 'H100-NODE', node_index)
        gpu_node = _node(
            fabric=fabric,
            role=gpu_role,
            name=node_address,
            address=node_address,
            node_kind='active_device',
            local_index=node_index,
            source=_source_binding(context, 'nodes', node_address),
            metadata={'fixture': True},
        )
        gpu_nodes[node_index] = gpu_node
        for osfp_index in range(1, gpu_spec['osfp_count'] + 1):
            osfp_address = f'{node_address}.OSFP-{osfp_index}'
            osfp = _endpoint(
                fabric=fabric,
                node=gpu_node,
                name=f'OSFP-{osfp_index}',
                address=osfp_address,
                endpoint_kind='plugin_port',
                connector_kind='osfp',
                source=_source_binding(context, 'endpoints', osfp_address),
                metadata={'fixture': True, 'role_slug': 'h100_osfp'},
            )
            gpu_osfps[(node_index, osfp_index)] = osfp
            gpu_mpos_by_osfp[(node_index, osfp_index)] = {}
            for mpo_index in range(1, gpu_spec['mpo_per_osfp'] + 1):
                mpo = _endpoint(
                    fabric=fabric,
                    node=gpu_node,
                    parent=osfp,
                    name=f'OSFP-{osfp_index}.MPO-{mpo_index}',
                    address=f'{osfp_address}.MPO-{mpo_index}',
                    endpoint_kind='subconnector',
                    connector_kind=f'mpo-{gpu_spec["positions_per_mpo"]}',
                    position_count=gpu_spec['positions_per_mpo'],
                    metadata={'fixture': True, 'role_slug': 'h100_mpo', 'mpo_index': mpo_index},
                )
                gpu_mpos[(node_index, osfp_index, mpo_index)] = mpo
                gpu_mpos_by_osfp[(node_index, osfp_index)][mpo_index] = mpo

    leaf_role = fixture.roles['leaf_switch']
    leaf_nodes = {}
    leaf_osfps = {}
    leaf_mpos = {}
    leaf_mpos_by_leaf = {}
    leaf_assignment = _leaf_plane_assignment(template_spec)
    positions_per_mpo = int(gpu_spec['positions_per_mpo'])
    mpo_per_osfp = int(gpu_spec['mpo_per_osfp'])
    for leaf_index in _leaf_indexes_for_template(template_spec):
        leaf_plane_number = leaf_assignment.get(leaf_index, leaf_index)
        leaf_address = _address_with_prefix(template_spec, 'leaf_ports', 'LEAF', leaf_index)
        leaf = _node(
            fabric=fabric,
            role=leaf_role,
            name=leaf_address,
            address=leaf_address,
            node_kind='active_device',
            local_index=leaf_index,
            source=_source_binding(context, 'nodes', leaf_address),
            metadata={'fixture': True, 'plane_number': leaf_plane_number},
        )
        leaf_nodes[leaf_index] = leaf
        osfp_address = f'{leaf.address}.OSFP-1'
        osfp = _endpoint(
            fabric=fabric,
            node=leaf,
            name='OSFP-1',
            address=osfp_address,
            endpoint_kind='plugin_port',
            connector_kind='osfp',
            source=_source_binding(context, 'endpoints', osfp_address),
            metadata={'fixture': True, 'role_slug': 'leaf_osfp'},
        )
        leaf_osfps[leaf_index] = osfp
        leaf_mpos_by_leaf[leaf_index] = {}
        for mpo_index in range(1, mpo_per_osfp + 1):
            mpo = _endpoint(
                fabric=fabric,
                node=leaf,
                parent=osfp,
                name=f'OSFP-1.MPO-{mpo_index}',
                address=f'{leaf.address}.OSFP-1.MPO-{mpo_index}',
                endpoint_kind='subconnector',
                connector_kind=f'mpo-{positions_per_mpo}',
                position_count=positions_per_mpo,
                metadata={'fixture': True, 'role_slug': 'leaf_mpo', 'mpo_index': mpo_index},
            )
            leaf_mpos[(leaf_index, mpo_index)] = mpo
            leaf_mpos_by_leaf[leaf_index][mpo_index] = mpo

    channel_subinterfaces_by_endpoint = {}
    for osfp_endpoint in list(gpu_osfps.values()) + list(leaf_osfps.values()):
        channel_subinterfaces_by_endpoint[osfp_endpoint.address] = _ensure_channel_subinterfaces_for_endpoint(
            template_spec=template_spec,
            endpoint=osfp_endpoint,
        )

    transceiver_bindings = []
    for key, osfp_endpoint in gpu_osfps.items():
        transceiver_bindings.append(
            _bind_stamped_transceiver(
                context=context,
                endpoint=osfp_endpoint,
                mpo_endpoints=gpu_mpos_by_osfp[key],
                role_slug='h100_osfp',
            )
        )
    for key, osfp_endpoint in leaf_osfps.items():
        transceiver_bindings.append(
            _bind_stamped_transceiver(
                context=context,
                endpoint=osfp_endpoint,
                mpo_endpoints=leaf_mpos_by_leaf[key],
                role_slug='leaf_osfp',
            )
        )

    source_lanes = []
    destination_lanes = []
    resolved_paths = []
    for proof_path in template_spec['proof_paths']:
        plane_number = int(proof_path['plane'])
        plane = planes[plane_number]
        wavelength_nm = _wavelength_for_plane(template_spec, plane_number)
        gpu_node_index = int(proof_path.get('gpu_tray') or 1)
        gpu_osfp_index = int(proof_path['gpu_osfp'])
        gpu_mpo_index = int(proof_path.get('gpu_mpo') or 1)
        leaf_index = int(proof_path['leaf'])
        leaf_mpo_index = int(proof_path.get('leaf_mpo') or 1)

        gpu_osfp = gpu_osfps[(gpu_node_index, gpu_osfp_index)]
        gpu_mpo = gpu_mpos[(gpu_node_index, gpu_osfp_index, gpu_mpo_index)]
        leaf_osfp = leaf_osfps[leaf_index]
        leaf_mpo = leaf_mpos[(leaf_index, leaf_mpo_index)]
        gpu_position = _position(gpu_mpo, int(proof_path['front_position']))
        leaf_position = _position(leaf_mpo, int(proof_path['rear_position']))
        gpu_channel_index = _channel_index_for_local_mpo_position(
            template_spec=template_spec,
            mpo_index=gpu_mpo_index,
            position_number=gpu_position.position_number,
        )
        leaf_channel_index = _channel_index_for_local_mpo_position(
            template_spec=template_spec,
            mpo_index=leaf_mpo_index,
            position_number=leaf_position.position_number,
        )

        pair_key = f'{fabric.slug}:plane-{plane_number}'
        gpu_channel = _channel(
            fabric=fabric,
            endpoint=gpu_osfp,
            plane=plane,
            name=f'H100 plane {plane_number}',
            channel_index=gpu_channel_index,
            source_subinterface=channel_subinterfaces_by_endpoint.get(gpu_osfp.address, {}).get(gpu_channel_index),
        )
        leaf_channel = _channel(
            fabric=fabric,
            endpoint=leaf_osfp,
            plane=plane,
            name=f'Leaf plane {plane_number}',
            channel_index=leaf_channel_index,
            source_subinterface=channel_subinterfaces_by_endpoint.get(leaf_osfp.address, {}).get(leaf_channel_index),
        )
        _sync_channel_position_maps_for_endpoint(
            template_spec=template_spec,
            channel=gpu_channel,
            mpo_endpoints=gpu_mpos_by_osfp[(gpu_node_index, gpu_osfp_index)],
        )
        _sync_channel_position_maps_for_endpoint(
            template_spec=template_spec,
            channel=leaf_channel,
            mpo_endpoints=leaf_mpos_by_leaf[leaf_index],
        )

        _fiber_strand(
            fabric=fabric,
            name=f'P{plane_number}-H100-to-LEAF-{leaf_index}',
            a_endpoint=gpu_mpo,
            a_position=gpu_position,
            b_endpoint=leaf_mpo,
            b_position=leaf_position,
            wavelength_nm=wavelength_nm,
            segment_kind='jumper',
        )

        source_lane = _optical_lane(
            fabric=fabric,
            endpoint=gpu_osfp,
            channel=gpu_channel,
            plane=plane,
            local_mpo_endpoint=gpu_mpo,
            local_mpo_position=gpu_position,
            lane_index=gpu_channel_index,
            local_mpo_index=gpu_mpo_index,
            direction='send',
            wavelength_nm=wavelength_nm,
            pair_key=pair_key,
        )
        destination_lane = _optical_lane(
            fabric=fabric,
            endpoint=leaf_osfp,
            channel=leaf_channel,
            plane=plane,
            local_mpo_endpoint=leaf_mpo,
            local_mpo_position=leaf_position,
            lane_index=leaf_channel_index,
            local_mpo_index=leaf_mpo_index,
            direction='receive',
            wavelength_nm=wavelength_nm,
            pair_key=pair_key,
        )
        path = resolve_optical_lane_path(source=source_lane, destination=destination_lane)
        source_lanes.append(source_lane)
        destination_lanes.append(destination_lane)
        resolved_paths.append(path)

    managed_objects = _managed_object_ids(fabric)
    stamp_run = StampRun.objects.create(
        template=template,
        fabric=fabric,
        status='completed',
        parameters={
            'fabric_name': fabric_name,
            'fabric_slug': fabric_slug,
            'template_slug': template.slug,
            'executor': _stamp_executor_name(template_spec),
            'source_binding_counts': {
                'nodes': len(context.source_bindings.get('nodes') or {}),
                'endpoints': len(context.source_bindings.get('endpoints') or {}),
            },
            'phase': context.phase,
            'active_planes': list(_active_planes(template_spec)),
            'all_planes': list(_all_planes(template_spec)),
            'topology_parameters': _topology_parameters(template_spec),
        },
        result={
            'fabric_id': fabric.pk,
            'managed_objects': managed_objects,
            'object_counts': _managed_object_counts(managed_objects),
            'netbox_created_objects': context.netbox_created_objects,
            'source_lane_ids': [lane.pk for lane in source_lanes],
            'destination_lane_ids': [lane.pk for lane in destination_lanes],
            'resolved_paths': [_path_summary(path) for path in resolved_paths],
            'stamp_manifest': _stamp_manifest(template_spec, fabric=fabric, phase=context.phase),
            'dark_position_overrides': _resolved_spec(template_spec).get('dark_position_overrides') or {},
            'wavelength_plan': _resolved_spec(template_spec).get('wavelength_plan'),
            'allocation_rule_override': _resolved_spec(template_spec).get('allocation_rule_override'),
            'fabric_ownership': _fabric_ownership_manifest(template_spec),
            'transceiver_bindings': transceiver_bindings,
        },
        metadata={'fixture': True},
    )
    record_audit_event(
        event_type='stamp',
        fabric=fabric,
        actor=context.actor,
        subject=stamp_run,
        outcome='ok',
        message=f'Direct-attach stamp run completed for fabric {fabric.slug}.',
        payload={
            'stamp_run_id': stamp_run.pk,
            'template_id': template.pk,
            'source_lane_count': len(source_lanes),
            'destination_lane_count': len(destination_lanes),
        },
    )

    return MiniFabricStampResult(
        fabric=fabric,
        stamp_run=stamp_run,
        source_lanes=tuple(source_lanes),
        destination_lanes=tuple(destination_lanes),
        resolved_paths=tuple(resolved_paths),
    )


@transaction.atomic
def execute_stamp_template(
    *,
    template,
    fabric_name: str,
    fabric_slug: str,
    source_bindings: dict | None = None,
    creation_options: dict | None = None,
    actor=None,
    phase: str | None = None,
) -> MiniFabricStampResult:
    template_spec = resolve_stamp_template_spec(template.template or {}, phase=phase)
    fixture = _persist_blueprint_architecture_fixture(template=template, template_spec=template_spec)
    if getattr(template, 'architecture_id', None) and template.architecture_id != fixture.architecture.pk:
        raise ValueError(
            'StampTemplate architecture does not match the architecture selected by '
            'template.template.architecture_slug/version.'
        )
    allocation_override = _apply_allocation_rule_override(template_spec, fixture)
    if allocation_override is not None:
        if not allocation_override.get('exists'):
            raise ValueError(
                f'Allocation rule override {allocation_override["slug"]!r} was not found on the target architecture.'
            )
        template_spec['_resolved']['allocation_rule_override_metadata'] = allocation_override
    executor_name = _stamp_executor_name(template_spec)
    executor = HYBRID_STAMP_EXECUTORS.get(executor_name)
    if executor is None:
        raise ValueError(f'Unknown hybrid stamp executor: {executor_name!r}.')

    netbox_source_bindings, netbox_created_objects = _create_or_bind_netbox_sources(
        template_spec=template_spec,
        fabric_slug=fabric_slug,
        creation_options=creation_options or {},
    )
    resolved_source_bindings = _merge_source_bindings(
        netbox_source_bindings,
        source_bindings or {},
    )

    context = StampExecutionContext(
        fixture=fixture,
        template=template,
        template_spec=template_spec,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        source_bindings=resolved_source_bindings,
        netbox_created_objects=netbox_created_objects,
        creation_options=creation_options or {},
        actor=actor,
        phase=phase,
    )
    return executor(context)


def stamp_roce_4plane_mini_fabric(
    *,
    fabric_name: str = 'RoCE 4-plane mini proof',
    fabric_slug: str = 'roce-4-plane-mini-proof',
    actor=None,
    phase: str | None = None,
) -> MiniFabricStampResult:
    fixture = ensure_roce_4plane_shuffle_architecture()
    return execute_stamp_template(
        template=fixture.stamp_template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        actor=actor,
        phase=phase,
    )
