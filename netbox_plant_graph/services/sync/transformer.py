from dataclasses import dataclass
from collections import defaultdict, deque

from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from dcim.models import Cable, FrontPort, Interface, RearPort

from ...breakout_profiles import (
    get_breakout_profile_for_cable,
    get_breakout_profile_name_for_cable,
    get_plugin_breakout_profile_for_cable,
)
from .extractor import SourceBundle


@dataclass(frozen=True)
class GraphInputs:
    fabrics: tuple = ()
    plane_numbers: tuple = ()
    nodes: tuple = ()
    terminations: tuple = ()
    attachment_units: tuple = ()
    signal_lanes: tuple = ()
    coarse_edges: tuple = ()
    fine_edges: tuple = ()
    transfer_maps: tuple = ()
    lane_maps: tuple = ()
    plane_memberships: tuple = ()


def _source_key(obj) -> str:
    return f'{obj._meta.label_lower}:{obj.pk}'


def _attachment_key(termination, position=None) -> str:
    suffix = 'base' if position is None else str(position)
    return f'{_source_key(termination)}:attachment:{suffix}'


def _termination_key(termination) -> str:
    return f'{_source_key(termination)}:termination'


def _node_key(device) -> str:
    return f'{_source_key(device)}:node'


def _canonical_pair(prefix: str, left: str, right: str, discriminator: str) -> str:
    first, second = sorted((left, right))
    return f'{prefix}:{discriminator}:{first}:{second}'


def _scope_value(scope, name, default=None):
    if scope is None:
        return default
    if isinstance(scope, dict):
        return scope.get(name, default)
    return getattr(scope, name, default)


def _plane_field_name() -> str:
    plugin_config = getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})
    return plugin_config.get('default_plane_field_name', 'fabric_plane')


def _materialize_signal_lanes_enabled() -> bool:
    plugin_config = getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})
    return plugin_config.get('materialize_signal_lanes', True)


def _normalize_plane_numbers(value) -> tuple[int, ...]:
    if value in (None, '', []):
        return ()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = (value,)

    plane_numbers = []
    for item in items:
        try:
            plane_numbers.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(plane_numbers)))


def _extract_plane_numbers(obj) -> tuple[int, ...]:
    custom_field_data = getattr(obj, 'custom_field_data', None) or {}
    plane_value = custom_field_data.get(_plane_field_name())
    return _normalize_plane_numbers(plane_value)


def _device_role_name(device) -> str:
    role = getattr(device, 'role', None)
    return getattr(role, 'slug', '') or getattr(role, 'name', '') or ''


def _classify_node_type(device) -> str:
    haystack = ' '.join(
        part.lower() for part in (
            device.name,
            getattr(getattr(device, 'device_type', None), 'model', ''),
            _device_role_name(device),
        ) if part
    )
    if 'patch' in haystack and 'panel' in haystack:
        return 'patch_panel'
    if 'shuffle' in haystack:
        return 'shuffle_module'
    if 'cassette' in haystack:
        return 'cassette'
    return 'device'


def _iter_attachment_positions(termination) -> tuple:
    if isinstance(termination, RearPort):
        return tuple(range(1, max(getattr(termination, 'positions', 1), 1) + 1))
    if isinstance(termination, FrontPort):
        return (1,)
    return (None,)


def _iter_path_positions(termination) -> tuple:
    cable_positions = tuple(getattr(termination, 'cable_positions', ()) or ())
    if cable_positions:
        return cable_positions
    return _iter_attachment_positions(termination)


def _can_identity_map_without_profile(left_terminations, right_terminations) -> bool:
    if len(left_terminations) != 1 or len(right_terminations) != 1:
        return False
    return len(_iter_path_positions(left_terminations[0])) == 1 and len(_iter_path_positions(right_terminations[0])) == 1


def _termination_type(termination) -> str:
    if isinstance(termination, FrontPort):
        return 'front_port'
    if isinstance(termination, RearPort):
        return 'rear_port'
    return 'interface'


def _connector_type(termination) -> str:
    connector = getattr(termination, 'type', None)
    if connector is None:
        return ''
    return str(connector)


def _channel_capacity(termination) -> int:
    if isinstance(termination, RearPort):
        return max(getattr(termination, 'positions', 0), 0)
    return 1


def _speed_gbps(termination):
    speed = getattr(termination, 'speed', None)
    if not speed:
        return None
    return int(speed / 1_000_000)


def _au_speed_gbps(source):
    speed = getattr(source, 'speed', None)
    if not speed:
        return None
    return int(speed / 1_000_000)


def _first_attachment_key(attachment_lookup, termination, position=None):
    source_key = _source_key(termination)
    candidates = [(source_key, position)]
    if position is None or not isinstance(termination, Interface):
        candidates.append((source_key, None))
    for candidate in candidates:
        attachment_key = attachment_lookup.get(candidate)
        if attachment_key:
            return attachment_key
    return None


def _record_plane_memberships(plane_memberships, attachment_key, source_obj):
    for plane_number in _extract_plane_numbers(source_obj):
        plane_memberships[(attachment_key, plane_number)] = {
            'plane_number': plane_number,
            'member_key': attachment_key,
            'membership_role': 'native',
            'metadata': {},
        }


def _child_interface_sort_key(child_interface):
    return (child_interface.name, child_interface.pk)


def _propagate_plane_memberships(plane_memberships, fine_edges, transfer_maps):
    adjacency = defaultdict(set)
    for fine_edge in fine_edges.values():
        if fine_edge.get('granularity') != 'attachment_unit':
            continue
        left_key = fine_edge.get('a_au_key')
        right_key = fine_edge.get('b_au_key')
        if not left_key or not right_key:
            continue
        adjacency[left_key].add(right_key)
        adjacency[right_key].add(left_key)

    for transfer_map in transfer_maps.values():
        left_key = transfer_map.get('src_attachment_key')
        right_key = transfer_map.get('dst_attachment_key')
        if not left_key or not right_key:
            continue
        adjacency[left_key].add(right_key)
        adjacency[right_key].add(left_key)

    seeded_planes = defaultdict(set)
    for membership in plane_memberships.values():
        seeded_planes[membership['member_key']].add(membership['plane_number'])

    seen = set()
    for attachment_key in set(adjacency) | set(seeded_planes):
        if attachment_key in seen:
            continue

        component = []
        queue = deque([attachment_key])
        seen.add(attachment_key)
        while queue:
            current_key = queue.popleft()
            component.append(current_key)
            for neighbor_key in adjacency.get(current_key, ()):
                if neighbor_key in seen:
                    continue
                seen.add(neighbor_key)
                queue.append(neighbor_key)

        component_planes = set()
        for member_key in component:
            component_planes.update(seeded_planes.get(member_key, set()))

        for plane_number in component_planes:
            for member_key in component:
                plane_memberships.setdefault((member_key, plane_number), {
                    'plane_number': plane_number,
                    'member_key': member_key,
                    'membership_role': 'transit',
                    'metadata': {'propagated': True},
                })


def _seed_lane_count(attachment_input) -> int:
    if attachment_input.get('topology_role') != 'child-interface':
        return 0
    speed_gbps = attachment_input.get('speed_gbps')
    if not speed_gbps:
        return 0
    return max(1, int(round(speed_gbps / 50)))


def _materialize_signal_lane_inputs(attachment_inputs, fine_edges, transfer_maps):
    if not _materialize_signal_lanes_enabled():
        return {}, {}, {}

    adjacency = defaultdict(set)
    for fine_edge in fine_edges.values():
        if fine_edge.get('granularity') != 'attachment_unit':
            continue
        left_key = fine_edge.get('a_au_key')
        right_key = fine_edge.get('b_au_key')
        if not left_key or not right_key:
            continue
        adjacency[left_key].add(right_key)
        adjacency[right_key].add(left_key)

    for transfer_map in transfer_maps.values():
        left_key = transfer_map.get('src_attachment_key')
        right_key = transfer_map.get('dst_attachment_key')
        if not left_key or not right_key:
            continue
        adjacency[left_key].add(right_key)
        adjacency[right_key].add(left_key)

    lane_seed_counts = {
        attachment_key: _seed_lane_count(attachment_input)
        for attachment_key, attachment_input in attachment_inputs.items()
    }

    lane_counts = {attachment_key: count for attachment_key, count in lane_seed_counts.items() if count > 0}
    seen = set()
    for attachment_key in set(adjacency) | set(attachment_inputs):
        if attachment_key in seen:
            continue

        component = []
        queue = deque([attachment_key])
        seen.add(attachment_key)
        while queue:
            current_key = queue.popleft()
            component.append(current_key)
            for neighbor_key in adjacency.get(current_key, ()):
                if neighbor_key in seen:
                    continue
                seen.add(neighbor_key)
                queue.append(neighbor_key)

        component_lane_count = max((lane_seed_counts.get(member_key, 0) for member_key in component), default=0)
        if component_lane_count <= 0:
            continue
        for member_key in component:
            lane_counts[member_key] = component_lane_count

    signal_lanes = {}
    lane_lookup = {}
    for attachment_key, lane_count in lane_counts.items():
        attachment_input = attachment_inputs[attachment_key]
        for lane_index in range(lane_count):
            lane_key = f'{attachment_key}:lane:{lane_index}'
            signal_lanes[lane_key] = {
                'key': lane_key,
                'attachment_key': attachment_key,
                'name': f"{attachment_input['name']}:l{lane_index}",
                'lane_index': lane_index,
                'lane_kind': 'electrical_tx',
                'signaling': 'pam4',
                'nominal_rate_gbps': 50,
                'direction_role': 'bidirectional',
                'wavelength_group': '',
                'source_anchor': attachment_input['name'],
                'metadata': {'attachment_lane_index': lane_index},
            }
            lane_lookup[(attachment_key, lane_index)] = lane_key

    lane_fine_edges = {}
    lane_maps = {}
    for fine_edge in fine_edges.values():
        if fine_edge.get('granularity') != 'attachment_unit':
            continue
        if fine_edge.get('edge_type') != 'derived_cable_segment':
            continue
        left_key = fine_edge.get('a_au_key')
        right_key = fine_edge.get('b_au_key')
        lane_count = min(lane_counts.get(left_key, 0), lane_counts.get(right_key, 0))
        if lane_count <= 0:
            continue
        for lane_index in range(lane_count):
            left_lane_key = lane_lookup[(left_key, lane_index)]
            right_lane_key = lane_lookup[(right_key, lane_index)]
            lane_fine_key = _canonical_pair('fine', left_lane_key, right_lane_key, f"{fine_edge['key']}:lane:{lane_index}")
            lane_fine_edges[lane_fine_key] = {
                'key': lane_fine_key,
                'granularity': 'signal_lane',
                'edge_type': 'lane_segment',
                'a_lane_key': min(left_lane_key, right_lane_key),
                'b_lane_key': max(left_lane_key, right_lane_key),
                'parent_coarse_key': fine_edge.get('parent_coarse_key'),
                'derived_from_profile': fine_edge.get('derived_from_profile', False),
                'metadata': {'attachment_fine_edge_key': fine_edge['key'], 'lane_index': lane_index},
            }

    for transfer_map in transfer_maps.values():
        left_key = transfer_map.get('src_attachment_key')
        right_key = transfer_map.get('dst_attachment_key')
        lane_count = min(lane_counts.get(left_key, 0), lane_counts.get(right_key, 0))
        if lane_count <= 0:
            continue
        for lane_index in range(lane_count):
            left_lane_key = lane_lookup[(left_key, lane_index)]
            right_lane_key = lane_lookup[(right_key, lane_index)]
            lane_map_key = f"{transfer_map['key']}:lane:{lane_index}"
            lane_maps[lane_map_key] = {
                'key': lane_map_key,
                'owner_node_key': transfer_map.get('owner_node_key'),
                'owner_edge_key': None,
                'src_lane_key': left_lane_key,
                'dst_lane_key': right_lane_key,
                'mapping_type': 'lane_shuffle' if transfer_map.get('mapping_type') == 'shuffle' else 'identity',
                'metadata': {'transfer_map_key': transfer_map['key'], 'lane_index': lane_index},
            }

    return signal_lanes, lane_fine_edges, lane_maps


def _build_assembly_mapping_lookup(cable_pks):
    """
    For cables stamped from an AssemblyTemplate, build a position-mapping
    lookup keyed by cable PK.

    Returns a dict:  {cable_pk: {(cable_end, connector_number, position): (peer_connector_number, peer_position)}}
    """
    from ...models import StampRecord, AssemblyTemplate

    cable_ct = ContentType.objects.get_for_model(Cable)
    template_ct = ContentType.objects.get_for_model(AssemblyTemplate)

    records = StampRecord.objects.filter(
        result_type=cable_ct,
        result_id__in=cable_pks,
        template_type=template_ct,
    ).values_list('result_id', 'template_id')

    if not records:
        return {}

    cable_to_template = {result_id: template_id for result_id, template_id in records}
    template_ids = set(cable_to_template.values())

    from ...models import AssemblyMappingTemplate
    mappings = AssemblyMappingTemplate.objects.filter(
        template_id__in=template_ids,
    ).select_related('a_connector', 'b_connector')

    template_mappings = defaultdict(list)
    for m in mappings:
        template_mappings[m.template_id].append(m)

    lookup = {}
    for cable_pk, template_id in cable_to_template.items():
        position_map = {}
        for m in template_mappings.get(template_id, ()):
            a_conn = m.a_connector.connector_number
            b_conn = m.b_connector.connector_number
            position_map[('A', a_conn, m.a_position)] = (b_conn, m.b_position)
            position_map[('B', b_conn, m.b_position)] = (a_conn, m.a_position)
        if position_map:
            lookup[cable_pk] = position_map
    return lookup


def transform_source_bundle(bundle: SourceBundle) -> GraphInputs:
    fabric = bundle.fabric
    fabric_name = getattr(fabric, 'name', None) or 'Default Fabric'
    expected_plane_count = getattr(fabric, 'expected_plane_count', 4) or 4
    scope_site = getattr(fabric, 'scope_site', None)
    scope_location = getattr(fabric, 'scope_location', None)
    tier_role_map: dict = getattr(fabric, 'tier_role_map', None) or {}

    node_inputs = {}
    termination_inputs = {}
    attachment_inputs = {}
    attachment_lookup = {}
    plane_memberships = {}
    coarse_edges = {}
    fine_edges = {}
    transfer_maps = {}

    child_interfaces_by_parent = {}
    for child_interface in bundle.child_interfaces:
        child_interfaces_by_parent.setdefault(child_interface.parent_id, []).append(child_interface)

    # Pre-compute assembly mapping fallback for cables without native profiles.
    cable_pks = {cable.pk for cable in bundle.cables}
    assembly_mapping_lookup = _build_assembly_mapping_lookup(cable_pks) if cable_pks else {}

    for device in bundle.devices:
        device_role_name = _device_role_name(device)
        tier_level = tier_role_map.get(device_role_name) if tier_role_map else None
        node_inputs[_node_key(device)] = {
            'key': _node_key(device),
            'name': device.name,
            'node_type': _classify_node_type(device),
            'role': device_role_name,
            'status': getattr(getattr(device, 'status', None), 'value', '') or getattr(device, 'status', '') or '',
            'source': device,
            'tenant': getattr(device, 'tenant', None),
            'location': getattr(device, 'location', None) or getattr(device, 'site', None),
            'metadata': {
                'device_type': getattr(getattr(device, 'device_type', None), 'model', ''),
                'tier_level': tier_level,
            },
        }

    supported_terminations = tuple(bundle.interfaces) + tuple(bundle.front_ports) + tuple(bundle.rear_ports)
    for termination in supported_terminations:
        parent_device = getattr(termination, 'device', None)
        node_key = _node_key(parent_device)
        termination_inputs[_termination_key(termination)] = {
            'key': _termination_key(termination),
            'node_key': node_key,
            'name': termination.name,
            'tp_type': _termination_type(termination),
            'connector_type': _connector_type(termination),
            'channel_capacity': _channel_capacity(termination),
            'speed_gbps': _speed_gbps(termination),
            'source': termination,
            'metadata': {},
        }

        attachment_positions = _iter_attachment_positions(termination)
        for ordinal, position in enumerate(attachment_positions):
            attachment_key = _attachment_key(termination, position)
            attachment_inputs[attachment_key] = {
                'key': attachment_key,
                'termination_key': _termination_key(termination),
                'name': termination.name if position is None else f'{termination.name}:{position}',
                'ordinal': ordinal,
                'unit_type': 'passive_group' if isinstance(termination, (FrontPort, RearPort)) else 'child_interface',
                'speed_gbps': _speed_gbps(termination),
                'topology_role': '',
                'active': True,
                'source': termination,
                'metadata': {'position': position} if position is not None else {},
            }
            attachment_lookup[(_source_key(termination), position)] = attachment_key
            attachment_lookup.setdefault((_source_key(termination), None), attachment_key)
            _record_plane_memberships(plane_memberships, attachment_key, termination)

        for child_ordinal, child_interface in enumerate(
            sorted(child_interfaces_by_parent.get(termination.pk, ()), key=_child_interface_sort_key),
            start=1,
        ):
            attachment_key = _attachment_key(termination, child_interface.pk)
            attachment_inputs[attachment_key] = {
                'key': attachment_key,
                'termination_key': _termination_key(termination),
                'name': child_interface.name,
                'ordinal': child_ordinal,
                'unit_type': 'child_interface',
                'speed_gbps': _au_speed_gbps(child_interface),
                'topology_role': 'child-interface',
                'active': True,
                'source': child_interface,
                'metadata': {
                    'child_interface_id': child_interface.pk,
                    'position': child_ordinal,
                },
            }
            attachment_lookup[(_source_key(termination), child_ordinal)] = attachment_key
            attachment_lookup[(_source_key(child_interface), None)] = attachment_key
            _record_plane_memberships(plane_memberships, attachment_key, child_interface)

    mapping_by_front = {}
    mapping_by_rear = {}
    for port_mapping in bundle.port_mappings:
        mapping_by_front[(port_mapping.front_port_id, port_mapping.front_port_position)] = port_mapping
        mapping_by_rear[(port_mapping.rear_port_id, port_mapping.rear_port_position)] = port_mapping

    def add_cable_segment(cable, left_termination, left_position, right_termination, right_position, *, derived_from_profile):
        left_attachment = _first_attachment_key(attachment_lookup, left_termination, left_position)
        right_attachment = _first_attachment_key(attachment_lookup, right_termination, right_position)
        if not left_attachment or not right_attachment or left_attachment == right_attachment:
            return

        left_tp_key = _termination_key(left_termination)
        right_tp_key = _termination_key(right_termination)
        coarse_key = _canonical_pair('coarse', left_tp_key, right_tp_key, f'cable:{cable.pk}')
        coarse_edges.setdefault(coarse_key, {
            'key': coarse_key,
            'edge_type': 'cable',
            'a_tp_key': min(left_tp_key, right_tp_key),
            'b_tp_key': max(left_tp_key, right_tp_key),
            'source': cable,
            'cable_profile_name': get_breakout_profile_name_for_cable(cable),
            'metadata': {'is_active': getattr(cable, 'is_active', lambda: True)()},
        })
        fine_key = _canonical_pair('fine', left_attachment, right_attachment, coarse_key)
        fine_edges.setdefault(fine_key, {
            'key': fine_key,
            'granularity': 'attachment_unit',
            'edge_type': 'derived_cable_segment',
            'a_au_key': min(left_attachment, right_attachment),
            'b_au_key': max(left_attachment, right_attachment),
            'parent_coarse_key': coarse_key,
            'derived_from_profile': derived_from_profile,
            'metadata': {},
        })

    def add_passthrough_segment(port_mapping, source_termination, source_position, destination_termination, destination_position):
        source_attachment = _first_attachment_key(attachment_lookup, source_termination, source_position)
        destination_attachment = _first_attachment_key(attachment_lookup, destination_termination, destination_position)
        if not source_attachment or not destination_attachment or source_attachment == destination_attachment:
            return

        transfer_key = f'transfer:{port_mapping.pk}'
        transfer_maps.setdefault(transfer_key, {
            'key': transfer_key,
            'owner_node_key': _node_key(port_mapping.device),
            'src_attachment_key': _attachment_key(port_mapping.front_port, port_mapping.front_port_position),
            'dst_attachment_key': _attachment_key(port_mapping.rear_port, port_mapping.rear_port_position),
            'mapping_type': 'identity',
            'source_port_mapping': getattr(port_mapping, 'pk', None),
            'metadata': {},
        })
        fine_key = _canonical_pair('fine', source_attachment, destination_attachment, transfer_key)
        fine_edges.setdefault(fine_key, {
            'key': fine_key,
            'granularity': 'attachment_unit',
            'edge_type': 'passthrough_map',
            'a_au_key': min(source_attachment, destination_attachment),
            'b_au_key': max(source_attachment, destination_attachment),
            'parent_coarse_key': None,
            'derived_from_profile': False,
            'metadata': {'transfer_map_key': transfer_key},
        })

    def add_passthrough_candidates(left_terminations, right_terminations):
        if not left_terminations or not right_terminations:
            return

        right_lookup = {_source_key(obj): obj for obj in right_terminations}
        for left_termination in left_terminations:
            for left_position in _iter_path_positions(left_termination):
                if isinstance(left_termination, FrontPort):
                    port_mapping = mapping_by_front.get((left_termination.pk, left_position or 1))
                    if port_mapping and _source_key(port_mapping.rear_port) in right_lookup:
                        add_passthrough_segment(
                            port_mapping,
                            left_termination,
                            left_position or 1,
                            port_mapping.rear_port,
                            port_mapping.rear_port_position,
                        )
                elif isinstance(left_termination, RearPort):
                    port_mapping = mapping_by_rear.get((left_termination.pk, left_position))
                    if port_mapping and _source_key(port_mapping.front_port) in right_lookup:
                        add_passthrough_segment(
                            port_mapping,
                            left_termination,
                            left_position,
                            port_mapping.front_port,
                            port_mapping.front_port_position,
                        )

    for cable_path in bundle.cable_paths:
        steps = tuple(cable_path.path_objects)
        for index in range(len(steps) - 2):
            left_step = steps[index]
            middle_step = steps[index + 1]
            right_step = steps[index + 2]

            left_terminations = [obj for obj in left_step if isinstance(obj, (Interface, FrontPort, RearPort))]
            middle_cables = [obj for obj in middle_step if isinstance(obj, Cable)]
            right_terminations = [obj for obj in right_step if isinstance(obj, (Interface, FrontPort, RearPort))]

            if left_terminations and middle_cables and right_terminations:
                cable = middle_cables[0]
                profile = get_breakout_profile_for_cable(cable)
                profile_pairs = 0
                if profile is not None and hasattr(profile, 'get_mapped_position'):
                    right_position_map = {}
                    for right_termination in right_terminations:
                        connector = getattr(right_termination, 'cable_connector', None)
                        for right_position in _iter_path_positions(right_termination):
                            right_position_map[(connector, right_position)] = right_termination
                    for left_termination in left_terminations:
                        for left_position in _iter_path_positions(left_termination):
                            try:
                                mapped_position = profile.get_mapped_position(
                                    left_termination.cable_end,
                                    left_termination.cable_connector,
                                    left_position,
                                )
                            except (TypeError, ValueError):
                                continue
                            if mapped_position is None:
                                continue
                            mapped_connector, peer_position = mapped_position
                            peer_termination = right_position_map.get((mapped_connector, peer_position))
                            if peer_termination is None:
                                continue
                            add_cable_segment(
                                cable,
                                left_termination,
                                left_position,
                                peer_termination,
                                peer_position,
                                derived_from_profile=True,
                            )
                            profile_pairs += 1
                if profile_pairs:
                    continue

                # Fallback: use AssemblyMappingTemplate from StampRecord provenance.
                assembly_map = assembly_mapping_lookup.get(cable.pk)
                if assembly_map:
                    right_position_map = {}
                    for right_termination in right_terminations:
                        connector = getattr(right_termination, 'cable_connector', None)
                        for right_position in _iter_path_positions(right_termination):
                            right_position_map[(connector, right_position)] = right_termination
                    assembly_pairs = 0
                    for left_termination in left_terminations:
                        for left_position in _iter_path_positions(left_termination):
                            mapped = assembly_map.get((
                                left_termination.cable_end,
                                left_termination.cable_connector,
                                left_position,
                            ))
                            if mapped is None:
                                continue
                            peer_connector, peer_position = mapped
                            peer_termination = right_position_map.get((peer_connector, peer_position))
                            if peer_termination is None:
                                continue
                            add_cable_segment(
                                cable,
                                left_termination,
                                left_position,
                                peer_termination,
                                peer_position,
                                derived_from_profile=True,
                            )
                            assembly_pairs += 1
                    if assembly_pairs:
                        continue

                breakout_profile = get_plugin_breakout_profile_for_cable(cable)
                if breakout_profile is not None:
                    sorted_right = sorted(right_terminations, key=lambda obj: (getattr(obj, 'name', ''), obj.pk))
                    bp_pairs = 0
                    for left_termination in left_terminations:
                        raw_positions = _iter_path_positions(left_termination)
                        if raw_positions == (None,) and breakout_profile.child_count > 1:
                            raw_positions = tuple(range(1, breakout_profile.child_count + 1))
                        # Interface-to-Interface breakout: both ends are single-position
                        # Interfaces but the profile declares multiple children.
                        # Expand to per-child positions so each child AU pair gets its
                        # own fine edge (e.g. 800G parent ↔ 800G parent with 4×200G
                        # children on each side).
                        if len(sorted_right) == 1:
                            peer_termination = sorted_right[0]
                            for position in raw_positions:
                                child_ordinal = breakout_profile.get_child_ordinal(position if position is not None else 1)
                                if child_ordinal is None:
                                    continue
                                peer_position = child_ordinal + 1
                                # Only count a hit when both child AUs exist — if either
                                # side has no child at this position the attachment lookup
                                # returns None and add_cable_segment would silently no-op,
                                # incorrectly blocking the identity-mapping fallback.
                                left_att = _first_attachment_key(attachment_lookup, left_termination, position)
                                right_att = _first_attachment_key(attachment_lookup, peer_termination, peer_position)
                                if not left_att or not right_att or left_att == right_att:
                                    continue
                                add_cable_segment(
                                    cable,
                                    left_termination,
                                    position,
                                    peer_termination,
                                    peer_position,
                                    derived_from_profile=True,
                                )
                                bp_pairs += 1
                        else:
                            for left_position in raw_positions:
                                child_ordinal = breakout_profile.get_child_ordinal(left_position if left_position is not None else 1)
                                if child_ordinal is None:
                                    continue
                                if not (0 <= child_ordinal < len(sorted_right)):
                                    continue
                                peer_termination = sorted_right[child_ordinal]
                                add_cable_segment(
                                    cable,
                                    left_termination,
                                    left_position,
                                    peer_termination,
                                    None,
                                    derived_from_profile=True,
                                )
                                bp_pairs += 1
                    if bp_pairs:
                        continue

                if not _can_identity_map_without_profile(left_terminations, right_terminations):
                    continue

                for left_termination, right_termination in zip(sorted(left_terminations, key=lambda obj: obj.pk), sorted(right_terminations, key=lambda obj: obj.pk)):
                    add_cable_segment(cable, left_termination, None, right_termination, None, derived_from_profile=False)

        for index in range(len(steps) - 1):
            left_step = steps[index]
            right_step = steps[index + 1]
            if any(isinstance(obj, Cable) for obj in left_step) or any(isinstance(obj, Cable) for obj in right_step):
                continue
            left_terminations = [obj for obj in left_step if isinstance(obj, (Interface, FrontPort, RearPort))]
            right_terminations = [obj for obj in right_step if isinstance(obj, (Interface, FrontPort, RearPort))]
            add_passthrough_candidates(left_terminations, right_terminations)

    _propagate_plane_memberships(plane_memberships, fine_edges, transfer_maps)
    signal_lanes, signal_lane_fine_edges, lane_maps = _materialize_signal_lane_inputs(
        attachment_inputs,
        fine_edges,
        transfer_maps,
    )
    fine_edges.update(signal_lane_fine_edges)

    discovered_planes = {membership['plane_number'] for membership in plane_memberships.values()}
    plane_numbers = tuple(sorted(discovered_planes | set(range(1, expected_plane_count + 1))))

    return GraphInputs(
        fabrics=(
            {
                'name': fabric_name,
                'description': getattr(fabric, 'description', ''),
                'expected_plane_count': expected_plane_count,
                'tier_depth': getattr(fabric, 'tier_depth', 3) or 3,
                'disjointness_policy': getattr(fabric, 'disjointness_policy', 'full') or 'full',
                'metadata': {
                    'scope_site_id': getattr(scope_site, 'pk', None),
                    'scope_location_id': getattr(scope_location, 'pk', None),
                },
                'scope_site': scope_site,
                'scope_location': scope_location,
                'instance': fabric,
            },
        ),
        plane_numbers=plane_numbers,
        nodes=tuple(node_inputs.values()),
        terminations=tuple(termination_inputs.values()),
        attachment_units=tuple(attachment_inputs.values()),
        signal_lanes=tuple(signal_lanes.values()),
        coarse_edges=tuple(coarse_edges.values()),
        fine_edges=tuple(fine_edges.values()),
        transfer_maps=tuple(transfer_maps.values()),
        lane_maps=tuple(lane_maps.values()),
        plane_memberships=tuple(plane_memberships.values()),
    )
