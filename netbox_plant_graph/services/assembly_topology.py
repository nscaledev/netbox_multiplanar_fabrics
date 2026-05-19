from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device, FrontPort, RearPort

from netbox_plant_graph.models import (
    AssemblyTemplate,
    AttachmentUnit,
    Fabric,
    LaneMap,
    PlantNode,
    SignalLane,
    TerminationPoint,
    TransferMap,
)


class AssemblyTopologyError(ValueError):
    pass


@dataclass(frozen=True)
class AssemblyTopologyResult:
    plant_node: PlantNode | None
    termination_points: int
    attachment_units: int
    transfer_maps: int
    signal_lanes: int = 0
    lane_maps: int = 0


def _generic_fk_fields(prefix: str, obj) -> dict[str, object]:
    if obj is None:
        return {f'{prefix}_type': None, f'{prefix}_id': None}
    return {
        f'{prefix}_type': ContentType.objects.get_for_model(obj, for_concrete_model=False),
        f'{prefix}_id': obj.pk,
    }


def _device_location(device: Device):
    return getattr(device, 'location', None) or getattr(device, 'rack', None) or getattr(device, 'site', None)


def _node_type(device: Device) -> str:
    haystack = ' '.join(
        str(part).lower()
        for part in (
            device.name,
            getattr(getattr(device, 'device_type', None), 'model', ''),
            getattr(getattr(device, 'device_type', None), 'slug', ''),
            getattr(getattr(device, 'role', None), 'name', ''),
            getattr(getattr(device, 'role', None), 'slug', ''),
        )
        if part
    )
    if 'cassette' in haystack:
        return 'cassette'
    if 'shuffle' in haystack:
        return 'shuffle_module'
    return 'passive_device'


def _source_role(device: Device, template: AssemblyTemplate) -> str:
    return (
        getattr(getattr(device, 'role', None), 'slug', '')
        or getattr(getattr(device, 'role', None), 'name', '')
        or template.assembly_type
    )[:50]


def _connector_label_by_number(template: AssemblyTemplate, side: str) -> dict[int, str]:
    labels = {}
    for connector in template.connectors.filter(side=side).order_by('connector_number'):
        labels[connector.connector_number] = connector.label or f'{side}{connector.connector_number}'
    return labels


def _connector_by_number(template: AssemblyTemplate, side: str) -> dict[int, object]:
    return {
        connector.connector_number: connector
        for connector in template.connectors.filter(side=side).order_by('connector_number')
    }


def _resolve_index(mapping: dict, *keys) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return int(value)
    return None


def _resolve_policy_transfer_maps(template: AssemblyTemplate) -> tuple[dict, ...]:
    policy = (template.metadata or {}).get('transfer_policy') or {}
    transfer_maps = []

    for group in policy.get('groups', ()):
        for mapping in group.get('transfer_maps', ()):
            front_number = _resolve_index(mapping, 'front_mpo', 'front_connector', 'b_connector')
            rear_number = _resolve_index(mapping, 'rear_mpo', 'rear_connector', 'a_connector')
            if front_number is None or rear_number is None:
                raise AssemblyTopologyError(
                    f'Transfer policy mapping on {template} must include front_mpo and rear_mpo numbers.'
                )
            transfer_maps.append({
                'group': group.get('name') or group.get('id') or '',
                'front_number': front_number,
                'rear_number': rear_number,
                'metadata': mapping,
            })

    if not transfer_maps:
        raise AssemblyTopologyError(f'Assembly template {template} has no metadata.transfer_policy transfer maps.')

    return tuple(transfer_maps)


def _connector_lane_count(connector) -> int:
    metadata = connector.metadata or {}
    for key in ('strand_count', 'fiber_count', 'lane_count'):
        if metadata.get(key) is not None:
            return max(1, int(metadata[key]))
    connector_type = connector.connector_type or ''
    if connector_type == 'mpo-8':
        return 8
    if connector_type.startswith('mpo-') or connector_type.startswith('mtp-'):
        try:
            return max(1, int(connector_type.split('-', 1)[1]))
        except (IndexError, TypeError, ValueError):
            return 1
    return 1


def _normalize_lane_pairs(source_mapping: dict, lane_count: int) -> tuple[tuple[int, int], ...]:
    explicit_map = (
        source_mapping.get('lane_map')
        or source_mapping.get('strand_map')
        or source_mapping.get('fiber_map')
    )
    if explicit_map is None:
        return tuple((index, index) for index in range(lane_count))

    pairs = []
    if isinstance(explicit_map, dict):
        iterable = explicit_map.items()
        for src, dst in iterable:
            pairs.append((int(src), int(dst)))
    else:
        for index, item in enumerate(explicit_map):
            if isinstance(item, dict):
                src = item.get('src') if item.get('src') is not None else item.get('front')
                dst = item.get('dst') if item.get('dst') is not None else item.get('rear')
                pairs.append((int(src), int(dst)))
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                pairs.append((int(item[0]), int(item[1])))
            else:
                pairs.append((index, int(item)))
    return tuple(pairs)


def _upsert_node(*, template: AssemblyTemplate, device: Device, fabric: Fabric, tenant=None) -> PlantNode:
    source_fields = _generic_fk_fields('source', device)
    location_fields = _generic_fk_fields('location', _device_location(device))
    node, _ = PlantNode.objects.update_or_create(
        fabric=fabric,
        name=device.name,
        defaults={
            'node_type': _node_type(device),
            'role': _source_role(device, template),
            'status': getattr(device, 'status', '') or '',
            'tenant': tenant or getattr(device, 'tenant', None) or getattr(fabric, 'tenant', None),
            'metadata': {
                'assembly_template': template.slug,
                'assembly_type': template.assembly_type,
                'source_device_id': device.pk,
            },
            **source_fields,
            **location_fields,
        },
    )
    return node


def _upsert_termination(
    *,
    node: PlantNode,
    port,
    template: AssemblyTemplate,
    connector_side: str,
    connector_number: int,
):
    connector_type = str(getattr(port, 'type', '') or '')
    channel_capacity = max(getattr(port, 'positions', 1), 1) if isinstance(port, RearPort) else 1
    return TerminationPoint.objects.update_or_create(
        plant_node=node,
        name=port.name,
        defaults={
            'tp_type': 'rear_port' if isinstance(port, RearPort) else 'front_port',
            'connector_type': connector_type,
            'channel_capacity': channel_capacity,
            'speed_gbps': None,
            'metadata': {
                'assembly_template': template.slug,
                'assembly_connector_side': connector_side,
                'assembly_connector_number': connector_number,
            },
            **_generic_fk_fields('source', port),
        },
    )[0]


def _upsert_attachment_unit(*, termination: TerminationPoint, port, side_label: str):
    return AttachmentUnit.objects.update_or_create(
        termination_point=termination,
        ordinal=1,
        defaults={
            'name': f'{termination.name}:1',
            'unit_type': 'passive_group',
            'speed_gbps': None,
            'topology_role': side_label[:50],
            'active': True,
            'metadata': {
                'position': 1,
                'source_port_name': port.name,
            },
            **_generic_fk_fields('source', port),
        },
    )[0]


def _upsert_signal_lanes(
    *,
    attachment_unit: AttachmentUnit,
    connector,
    side_label: str,
    template: AssemblyTemplate,
) -> dict[int, SignalLane]:
    lanes = {}
    lane_count = _connector_lane_count(connector)
    for lane_index in range(lane_count):
        lane, _ = SignalLane.objects.update_or_create(
            attachment_unit=attachment_unit,
            lane_index=lane_index,
            defaults={
                'name': f'{attachment_unit.name}:strand-{lane_index + 1:02d}',
                'lane_kind': 'optical_tx',
                'signaling': 'unknown',
                'nominal_rate_gbps': None,
                'direction_role': 'passive_strand',
                'wavelength_nm': 1310,
                'wavelength_group': '',
                'source_anchor': f'{attachment_unit.termination_point.name}:{lane_index + 1}',
                'metadata': {
                    'assembly_transfer_policy': True,
                    'optical_lane_semantics': 'single_fiber_strand',
                    'assembly_template': template.slug,
                    'assembly_connector_side': connector.side,
                    'assembly_connector_number': connector.connector_number,
                    'connector_type': connector.connector_type,
                    'side_label': side_label,
                    'strand_index': lane_index,
                    'strand_number': lane_index + 1,
                },
            },
        )
        lanes[lane_index] = lane
    return lanes


@transaction.atomic
def stamp_assembly_transfer_policy(
    template: AssemblyTemplate,
    device: Device,
    fabric: Fabric,
    *,
    tenant=None,
    prune_existing: bool = True,
) -> AssemblyTopologyResult:
    """
    Materialize an AssemblyTemplate metadata.transfer_policy onto one passive device.

    This is intended for passive artifacts whose internal topology is richer than
    NetBox native front/rear port mappings can express. The Madison 2x2 MPO
    shuffle cassette uses two independent 2x2 full-fanout groups.
    """
    if template.device_type_id and device.device_type_id != template.device_type_id:
        raise AssemblyTopologyError(
            f'Device {device} uses {device.device_type}; template {template} expects {template.device_type}.'
        )

    policy_maps = _resolve_policy_transfer_maps(template)
    front_labels = _connector_label_by_number(template, 'B')
    rear_labels = _connector_label_by_number(template, 'A')
    front_connectors = _connector_by_number(template, 'B')
    rear_connectors = _connector_by_number(template, 'A')

    node = _upsert_node(template=template, device=device, fabric=fabric, tenant=tenant)

    front_ports = {port.name: port for port in FrontPort.objects.filter(device=device)}
    rear_ports = {port.name: port for port in RearPort.objects.filter(device=device)}
    attachment_units = {}
    signal_lanes = {}

    for number, label in rear_labels.items():
        port = rear_ports.get(label)
        if port is None:
            raise AssemblyTopologyError(f'Device {device} is missing rear port {label!r}.')
        termination = _upsert_termination(
            node=node, port=port, template=template, connector_side='A', connector_number=number
        )
        attachment_units[('rear', number)] = _upsert_attachment_unit(
            termination=termination, port=port, side_label='shuffle-rear'
        )
        signal_lanes[('rear', number)] = _upsert_signal_lanes(
            attachment_unit=attachment_units[('rear', number)],
            connector=rear_connectors[number],
            side_label='shuffle-rear',
            template=template,
        )

    for number, label in front_labels.items():
        port = front_ports.get(label)
        if port is None:
            raise AssemblyTopologyError(f'Device {device} is missing front port {label!r}.')
        termination = _upsert_termination(
            node=node, port=port, template=template, connector_side='B', connector_number=number
        )
        attachment_units[('front', number)] = _upsert_attachment_unit(
            termination=termination, port=port, side_label='shuffle-front'
        )
        signal_lanes[('front', number)] = _upsert_signal_lanes(
            attachment_unit=attachment_units[('front', number)],
            connector=front_connectors[number],
            side_label='shuffle-front',
            template=template,
        )

    if prune_existing:
        LaneMap.objects.filter(
            owner_node=node,
            metadata__assembly_transfer_policy=True,
            metadata__assembly_template=template.slug,
        ).delete()
        TransferMap.objects.filter(
            owner_node=node,
            metadata__assembly_transfer_policy=True,
            metadata__assembly_template=template.slug,
        ).delete()

    created_maps = 0
    created_lane_maps = 0
    policy = (template.metadata or {}).get('transfer_policy') or {}
    for policy_map in policy_maps:
        src = attachment_units.get(('front', policy_map['front_number']))
        dst = attachment_units.get(('rear', policy_map['rear_number']))
        if src is None or dst is None:
            raise AssemblyTopologyError(f'Transfer policy on {template} references an unknown connector.')
        transfer_map = TransferMap.objects.create(
            owner_node=node,
            src_attachment_unit=src,
            dst_attachment_unit=dst,
            mapping_type='shuffle',
            metadata={
                'assembly_transfer_policy': True,
                'assembly_template': template.slug,
                'policy_type': policy.get('policy_type', ''),
                'group': policy_map['group'],
                'front_mpo': policy_map['front_number'],
                'rear_mpo': policy_map['rear_number'],
                'source_mapping': policy_map['metadata'],
            },
        )
        created_maps += 1

        src_lanes = signal_lanes.get(('front', policy_map['front_number']), {})
        dst_lanes = signal_lanes.get(('rear', policy_map['rear_number']), {})
        lane_count = min(len(src_lanes), len(dst_lanes))
        for src_lane_index, dst_lane_index in _normalize_lane_pairs(policy_map['metadata'], lane_count):
            src_lane = src_lanes.get(src_lane_index)
            dst_lane = dst_lanes.get(dst_lane_index)
            if src_lane is None or dst_lane is None:
                raise AssemblyTopologyError(
                    f'Transfer policy on {template} references lane {src_lane_index}->{dst_lane_index}, '
                    'but that lane does not exist on the stamped MPO endpoints.'
                )
            LaneMap.objects.create(
                owner_node=node,
                src_lane=src_lane,
                dst_lane=dst_lane,
                mapping_type='lane_shuffle',
                metadata={
                    'assembly_transfer_policy': True,
                    'assembly_template': template.slug,
                    'transfer_map_id': transfer_map.pk,
                    'policy_type': policy.get('policy_type', ''),
                    'group': policy_map['group'],
                    'front_mpo': policy_map['front_number'],
                    'rear_mpo': policy_map['rear_number'],
                    'front_lane_index': src_lane_index,
                    'rear_lane_index': dst_lane_index,
                    'strand_mapping': 'explicit' if (
                        policy_map['metadata'].get('lane_map')
                        or policy_map['metadata'].get('strand_map')
                        or policy_map['metadata'].get('fiber_map')
                    ) else 'inferred_identity_pending_vendor_pinout',
                    'source_mapping': policy_map['metadata'],
                },
            )
            created_lane_maps += 1

    return AssemblyTopologyResult(
        plant_node=node,
        termination_points=TerminationPoint.objects.filter(plant_node=node).count(),
        attachment_units=AttachmentUnit.objects.filter(termination_point__plant_node=node).count(),
        transfer_maps=TransferMap.objects.filter(
            owner_node=node,
            metadata__assembly_transfer_policy=True,
            metadata__assembly_template=template.slug,
        ).count() if prune_existing else created_maps,
        signal_lanes=SignalLane.objects.filter(attachment_unit__termination_point__plant_node=node).count(),
        lane_maps=LaneMap.objects.filter(
            owner_node=node,
            metadata__assembly_transfer_policy=True,
            metadata__assembly_template=template.slug,
        ).count() if prune_existing else created_lane_maps,
    )
