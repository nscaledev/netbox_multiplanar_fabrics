from __future__ import annotations

import hashlib
import os
import re
from collections import Counter

from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from dcim.models import PowerPort, Rack
from netbox_power_plant.choices import DesignStateChoices, NodeKindChoices, TerminalDirectionChoices
from netbox_power_plant.models import ElectricalNode, ElectricalSegment, PowerHandoffPoint, PowerSystem


MAD_SITE_SLUG = os.environ.get('MADISON_SITE_SLUG', 'gs001')
POWER_SYSTEM_NAME = os.environ.get('MADISON_POWER_SYSTEM_NAME', 'GS001 Electrical Plant')
APPLY = os.environ.get('MADISON_POWER_HANDOFF_APPLY') == '1'
ALLOW_PARTIAL = os.environ.get('MADISON_POWER_HANDOFF_ALLOW_PARTIAL') == '1'
STRICT = os.environ.get('MADISON_POWER_HANDOFF_STRICT') == '1'

ROW_ID_TAG_PREFIX = 'nscale-row-id-'
NVL72_RACK_ROLE_SLUG = 'nvl72_poweredgexe9712'
POWER_SHELF_DEVICE_TYPE_SLUGS = ('gb300ps', 'ps33-33kw-power-shelf')
POWER_SHELF_FACILITY_INPUT_NAME = 'facility-input'
APDU_DEVICE_TYPE_SLUG = 'apc-apdu11450me'
APDU_INPUT_PORT_NAME = 'input'

GB300_SLOT_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 19, 20, 21, 22, 23, 24, 25, 26]
FE_SLOT_NUMBERS = [13, 14]

COMPUTE_GROUP_ROWS = {
    1: ('Q', 'R'),
    2: ('U', 'V'),
    3: ('S', 'T'),
    4: ('W', 'X'),
    5: ('A', 'B'),
    6: ('E', 'F'),
    7: ('C', 'D'),
    8: ('G', 'H'),
}

NETWORK_GROUP_ROWS = {
    '1A': ('M', 'N'),
    '1B': ('O', 'P'),
    '2A': ('I', 'J'),
    '2B': ('K', 'L'),
}

BE_SLOT_MAP = {
    1: {
        1: (0, 9),
        2: (0, 17),
        3: (1, 9),
        4: (1, 17),
    },
    2: {
        1: (0, 10),
        2: (0, 18),
        3: (1, 10),
        4: (1, 18),
    },
}

DIRECT_WORKBOOK_SLOT_PATTERN = re.compile(r'^[A-X](?:[1-9]|1\d|2[0-6])$')
COMPUTE_RACK_PATTERN = re.compile(
    r'^(?P<family>GB300|BE|FE)-P(?P<group>\d+)-R(?P<row>\d+)-C(?P<cabinet>\d+)$'
)
NETWORK_RACK_PATTERN = re.compile(
    r'^NW-P(?P<hall_section>[12][AB])-R(?P<row>\d+)-C(?P<cabinet>\d+)$'
)
MODEL_RACK_PATTERN = re.compile(r'rack_id=(?P<rack_id>[^;]+);\s*circuit=(?P<circuit>\d+)')
NODE_RACK_PATTERN = re.compile(r'^(?P<rack_id>.+)-CKT(?P<circuit>\d+)$')


def stable_slug(*parts, max_length=100):
    raw = '-'.join(str(part) for part in parts if part is not None)
    base = slugify(raw) or 'object'
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
    suffix = f'-{digest}'
    return f'{base[: max_length - len(suffix)]}{suffix}'


def short_description(text):
    return text[:200]


def slot_for_electrical_rack_id(name):
    if DIRECT_WORKBOOK_SLOT_PATTERN.fullmatch(name):
        return name

    match = COMPUTE_RACK_PATTERN.fullmatch(name)
    if match:
        family = match.group('family')
        group = int(match.group('group'))
        row = int(match.group('row'))
        cabinet = int(match.group('cabinet'))
        physical_rows = COMPUTE_GROUP_ROWS.get(group)
        if not physical_rows:
            return None

        if family == 'GB300':
            if row not in (1, 2) or not 1 <= cabinet <= len(GB300_SLOT_NUMBERS):
                return None
            return f'{physical_rows[row - 1]}{GB300_SLOT_NUMBERS[cabinet - 1]}'

        if family == 'FE':
            if row not in (1, 2) or not 1 <= cabinet <= len(FE_SLOT_NUMBERS):
                return None
            return f'{physical_rows[row - 1]}{FE_SLOT_NUMBERS[cabinet - 1]}'

        if family == 'BE':
            row_index_and_slot = BE_SLOT_MAP.get(row, {}).get(cabinet)
            if not row_index_and_slot:
                return None
            row_index, slot_number = row_index_and_slot
            return f'{physical_rows[row_index]}{slot_number}'

    match = NETWORK_RACK_PATTERN.fullmatch(name)
    if match:
        physical_rows = NETWORK_GROUP_ROWS.get(match.group('hall_section'))
        row = int(match.group('row'))
        cabinet = int(match.group('cabinet'))
        if not physical_rows or row not in (1, 2) or not 1 <= cabinet <= 7:
            return None
        return f'{physical_rows[row - 1]}{cabinet}'

    return None


def parse_circuit_node(node):
    model_match = MODEL_RACK_PATTERN.search(node.model or '')
    if model_match:
        return model_match.group('rack_id'), int(model_match.group('circuit'))

    name_match = NODE_RACK_PATTERN.fullmatch(node.name)
    if name_match:
        return name_match.group('rack_id'), int(name_match.group('circuit'))

    return None, None


def rack_for_slot(slot):
    rack = Rack.objects.filter(site__slug=MAD_SITE_SLUG, name=slot).first()
    if rack is not None:
        return rack

    tag_slug = f'{ROW_ID_TAG_PREFIX}{slot}'.lower()
    return Rack.objects.filter(site__slug=MAD_SITE_SLUG, tags__slug=tag_slug).first()


def terminal_for_node(node):
    terminals = list(node.terminals.order_by('position_index', 'name'))
    sink_terminals = [
        terminal for terminal in terminals
        if terminal.direction in (TerminalDirectionChoices.DIRECTION_SINK, TerminalDirectionChoices.DIRECTION_BIDIRECTIONAL)
    ]
    return (sink_terminals or terminals or [None])[0]


def source_rpp_for_node(power_system, node):
    segment = (
        ElectricalSegment.objects
        .filter(power_system=power_system, to_terminal__node=node)
        .select_related('from_terminal__node')
        .order_by('name')
        .first()
    )
    if segment is None or segment.from_terminal is None:
        return ''
    return segment.from_terminal.node.name


def target_port_for_rack_circuit(rack, circuit):
    if rack.role and rack.role.slug == NVL72_RACK_ROLE_SLUG:
        if not 1 <= circuit <= 8:
            return None, f'nvl72_unsupported_circuit_{circuit}'
        ports = list(
            PowerPort.objects
            .filter(
                device__site__slug=MAD_SITE_SLUG,
                device__device_type__slug__in=POWER_SHELF_DEVICE_TYPE_SLUGS,
                name=POWER_SHELF_FACILITY_INPUT_NAME,
            )
            .filter(Q(device__rack=rack) | Q(device__parent_bay__device__rack=rack))
            .select_related('device', 'device__rack', 'device__parent_bay__device__rack', 'device__device_type')
            .order_by('device__local_context_data__madison_workbook__ru_bottom', 'device__position', 'device__name', 'name')
        )
        if len(ports) != 8:
            return None, f'nvl72_facility_input_count_{len(ports)}'
        return ports[circuit - 1], ''

    if circuit not in (1, 2):
        return None, f'conventional_unsupported_circuit_{circuit}'

    side = 'a' if circuit == 1 else 'b'
    ports = [
        port for port in PowerPort.objects
        .filter(
            device__site__slug=MAD_SITE_SLUG,
            device__rack=rack,
            device__device_type__slug=APDU_DEVICE_TYPE_SLUG,
            name=APDU_INPUT_PORT_NAME,
        )
        .select_related('device', 'device__rack', 'device__device_type')
        .order_by('device__name', 'name')
        if port.device.name.endswith(f'-pdu-{side}')
    ]
    if len(ports) != 1:
        return None, f'apdu_{side}_input_count_{len(ports)}'
    return ports[0], ''


def desired_handoff_fields(power_system, node, terminal, target_port, redundancy_group, source_rpp, electrical_rack_id, slot, circuit):
    feed_label = f'{source_rpp} CKT{circuit}' if source_rpp else f'CKT{circuit}'
    return {
        'name': node.name,
        'slug': stable_slug('power-handoff', node.name),
        'power_system': power_system,
        'electrical_node': node,
        'electrical_terminal': terminal,
        'power_port': target_port,
        'expected_redundancy_group': redundancy_group,
        'delivery_role': 'rack-circuit',
        'feed_label': feed_label[:100],
        'design_state': DesignStateChoices.STATE_PLANNED,
        'description': short_description(
            f'GS001 handoff from electrical rack id {electrical_rack_id} circuit {circuit} to NetBox rack {slot}.'
        ),
    }


def upsert_handoff(fields, counters):
    handoff = (
        PowerHandoffPoint.objects
        .filter(power_system=fields['power_system'], electrical_node=fields['electrical_node'])
        .first()
    )
    created = False
    if handoff is None:
        handoff, created = PowerHandoffPoint.objects.get_or_create(
            slug=fields['slug'],
            defaults={
                'name': fields['name'],
                'power_system': fields['power_system'],
                'electrical_node': fields['electrical_node'],
                'electrical_terminal': fields['electrical_terminal'],
                'power_port': fields['power_port'],
            },
        )

    changed = created
    for field, value in fields.items():
        if getattr(handoff, field) != value:
            setattr(handoff, field, value)
            changed = True

    if created or changed:
        handoff.full_clean()
        handoff.save()

    counters['handoff_points_created' if created else 'handoff_points_updated' if changed else 'handoff_points_unchanged'] += 1
    return handoff


def sample_add(samples, key, value, limit=10):
    values = samples.setdefault(key, [])
    if len(values) < limit:
        values.append(value)


def main():
    power_system = PowerSystem.objects.get(name=POWER_SYSTEM_NAME, site__slug=MAD_SITE_SLUG)
    redundancy_group = power_system.redundancy_groups.order_by('name').first()
    circuit_nodes = (
        ElectricalNode.objects
        .filter(power_system=power_system, node_kind=NodeKindChoices.KIND_RACK_CIRCUIT_TERMINATOR)
        .prefetch_related('terminals')
        .order_by('name')
    )
    counters = Counter()
    samples = {}
    ready = []

    for node in circuit_nodes:
        counters['circuit_nodes_seen'] += 1
        electrical_rack_id, circuit = parse_circuit_node(node)
        if not electrical_rack_id or circuit is None:
            counters['circuit_nodes_unparseable'] += 1
            sample_add(samples, 'unparseable', node.name)
            continue

        slot = slot_for_electrical_rack_id(electrical_rack_id)
        if slot is None:
            counters['electrical_rack_ids_unmapped'] += 1
            sample_add(samples, 'unmapped_electrical_rack_ids', electrical_rack_id)
            continue

        rack = rack_for_slot(slot)
        if rack is None:
            counters['netbox_racks_missing'] += 1
            sample_add(samples, 'missing_netbox_racks', f'{electrical_rack_id}->{slot}')
            continue

        target_port, reason = target_port_for_rack_circuit(rack, circuit)
        if target_port is None:
            counters[f'target_ports_missing_{reason}'] += 1
            sample_add(samples, 'missing_target_ports', f'{electrical_rack_id} CKT{circuit}->{rack.name}: {reason}')
            continue

        terminal = terminal_for_node(node)
        if terminal is None:
            counters['circuit_nodes_missing_terminal'] += 1
            sample_add(samples, 'missing_terminal', node.name)
            continue

        source_rpp = source_rpp_for_node(power_system, node)
        fields = desired_handoff_fields(
            power_system,
            node,
            terminal,
            target_port,
            redundancy_group,
            source_rpp,
            electrical_rack_id,
            slot,
            circuit,
        )
        ready.append(fields)
        counters['handoff_points_ready'] += 1
        parent_device = getattr(getattr(target_port.device, 'parent_bay', None), 'device', None)
        target_rack = target_port.device.rack or (parent_device.rack if parent_device is not None else None)
        if target_rack and target_rack.role and target_rack.role.slug == NVL72_RACK_ROLE_SLUG:
            counters['handoff_points_ready_nvl72'] += 1
        else:
            counters['handoff_points_ready_conventional'] += 1

    blockers = counters['circuit_nodes_seen'] - counters['handoff_points_ready']
    counters['handoff_points_blocked'] = blockers

    if blockers and APPLY and not ALLOW_PARTIAL:
        for key in sorted(samples):
            print(f'{key}_sample=' + '; '.join(samples[key]))
        raise RuntimeError(
            f'Refusing to apply partial GS001 power handoffs: ready={len(ready)} blocked={blockers}. '
            'Resolve target-port/rack blockers or set MADISON_POWER_HANDOFF_ALLOW_PARTIAL=1.'
        )

    if APPLY:
        with transaction.atomic():
            for fields in ready:
                upsert_handoff(fields, counters)
    else:
        existing = PowerHandoffPoint.objects.filter(power_system=power_system).count()
        counters['existing_handoff_points'] = existing

    print('Madison power handoff reconciliation complete.')
    print(f'apply={APPLY}')
    print(f'site={MAD_SITE_SLUG}')
    print(f'power_system={POWER_SYSTEM_NAME}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    for key in sorted(samples):
        print(f'{key}_sample=' + '; '.join(samples[key]))

    if STRICT and blockers:
        raise RuntimeError(f'GS001 power handoff coverage is incomplete: blocked={blockers}.')


main()
