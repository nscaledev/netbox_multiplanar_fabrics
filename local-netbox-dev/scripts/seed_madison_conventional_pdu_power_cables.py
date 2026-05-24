from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Cable, CableTermination, Device, PowerOutlet, PowerPort, Rack
from tenancy.models import Tenant


MAD_SITE_SLUG = os.environ.get('MADISON_SITE_SLUG', 'gs001')
NSCALE_TENANT_SLUG = 'nscale'
PDU_DEVICE_TYPE_SLUG = 'apc-apdu11450me'
NVL72_RACK_ROLE_SLUG = 'nvl72_poweredgexe9712'
PDU_SIDES = ('a', 'b')
POWER_CABLE_STATUS = 'planned'
POWER_CABLE_TYPE = 'power'
SOURCE_MARKER_BEGIN = '<!-- madison-conventional-pdu-power:start -->'
SOURCE_MARKER_END = '<!-- madison-conventional-pdu-power:end -->'


def merge_staged_comments(existing, staged):
    if SOURCE_MARKER_BEGIN in existing and SOURCE_MARKER_END in existing:
        prefix, rest = existing.split(SOURCE_MARKER_BEGIN, 1)
        _, suffix = rest.split(SOURCE_MARKER_END, 1)
        return f'{prefix.rstrip()}\n\n{staged}\n{suffix.lstrip()}'.strip()
    return f'{existing.rstrip()}\n\n{staged}'.strip() if existing else staged


def staged_comments(rack, pdu, outlet, port):
    return f"""\
{SOURCE_MARKER_BEGIN}
Madison conventional-rack PDU outlet assignment.
- rack: {rack.name}
- source_pdu: {pdu.name}
- source_outlet: {outlet.name}
- destination_device: {port.device.name}
- destination_power_port: {port.name}
{SOURCE_MARKER_END}"""


def port_sort_key(port):
    return (
        port.device.position is None,
        port.device.position or 0,
        port.device.face or '',
        port.device.name,
        port.name,
    )


def pdu_side(device):
    match = re.search(r'-pdu-([ab])$', device.name)
    if match is None:
        raise RuntimeError(f'Cannot infer PDU side from device name {device.name!r}.')
    return match.group(1)


def desired_side(port, index):
    name = port.name.lower()
    if 'left' in name:
        return 'a'
    if 'right' in name:
        return 'b'

    match = re.search(r'(\d+)$', name)
    if match is not None:
        return 'a' if int(match.group(1)) % 2 else 'b'

    return 'a' if index % 2 == 0 else 'b'


def compatible_outlet_type(port):
    if port.type in {'iec-60320-c20', 'iec-60320-c22'}:
        return 'iec-60320-c19'
    return 'iec-60320-c13'


def cable_label(outlet, port):
    label = f'{outlet.device.name}:{outlet.name}->{port.device.name}:{port.name}'
    return label[:100]


def get_pdu_pair(rack):
    pdus = list(
        Device.objects.filter(rack=rack, device_type__slug=PDU_DEVICE_TYPE_SLUG)
        .select_related('rack')
        .order_by('name')
    )
    by_side = {pdu_side(pdu): pdu for pdu in pdus}
    if len(pdus) != 2 or set(by_side) != set(PDU_SIDES):
        raise RuntimeError(f'Rack {rack.name} has PDU sides {sorted(by_side)} from {len(pdus)} APDU11450ME devices; expected A and B.')
    return by_side


def available_outlets_by_side(pdus_by_side):
    outlets = {}
    for side, pdu in pdus_by_side.items():
        side_outlets = list(
            PowerOutlet.objects.filter(device=pdu, cable__isnull=True)
            .select_related('device')
            .order_by('name')
        )
        by_type = defaultdict(list)
        for outlet in side_outlets:
            by_type[outlet.type].append(outlet)
        outlets[side] = by_type
    return outlets


def downstream_power_ports_by_device(rack):
    ports_by_device = defaultdict(list)
    ports = (
        PowerPort.objects.filter(device__rack=rack, cable__isnull=True)
        .exclude(device__device_type__slug=PDU_DEVICE_TYPE_SLUG)
        .select_related('device', 'device__rack')
        .order_by('device__name', 'name')
    )
    for port in ports:
        ports_by_device[port.device_id].append(port)
    return ports_by_device


def all_downstream_power_ports_by_device(rack):
    ports_by_device = defaultdict(list)
    ports = (
        PowerPort.objects.filter(device__rack=rack)
        .exclude(device__device_type__slug=PDU_DEVICE_TYPE_SLUG)
        .select_related('device', 'device__rack', 'cable')
        .order_by('device__name', 'name')
    )
    for port in ports:
        ports_by_device[port.device_id].append(port)
    return ports_by_device


def reserve_outlet(outlets_by_side, side, port):
    preferred_type = compatible_outlet_type(port)
    if outlets_by_side[side][preferred_type]:
        return outlets_by_side[side][preferred_type].pop(0)

    if preferred_type == 'iec-60320-c13' and outlets_by_side[side]['iec-60320-c19']:
        return outlets_by_side[side]['iec-60320-c19'].pop(0)

    raise RuntimeError(
        f'No available {preferred_type} outlet on PDU side {side.upper()} for '
        f'{port.device.name} {port.name} ({port.type}).'
    )


def create_power_cable(outlet, port, tenant, counters):
    cable = Cable.objects.create(
        type=POWER_CABLE_TYPE,
        status=POWER_CABLE_STATUS,
        tenant=tenant,
        label=cable_label(outlet, port),
        comments=staged_comments(port.device.rack, outlet.device, outlet, port),
    )
    outlet_type = ContentType.objects.get_for_model(outlet)
    port_type = ContentType.objects.get_for_model(port)
    CableTermination.objects.create(
        cable=cable,
        cable_end='A',
        termination_type=outlet_type,
        termination_id=outlet.pk,
    )
    CableTermination.objects.create(
        cable=cable,
        cable_end='B',
        termination_type=port_type,
        termination_id=port.pk,
    )
    cable.full_clean()
    cable.save()
    counters['power_cables_created'] += 1
    counters[f'power_cables_to_pdu_{pdu_side(outlet.device)}'] += 1
    counters[f'power_cables_from_{outlet.type}'] += 1
    counters[f'power_cables_to_{port.type}'] += 1


def opposite_pdu_side(port):
    if port.cable_id is None:
        return None
    terminations = list(port.cable.a_terminations) + list(port.cable.b_terminations)
    outlets = [termination for termination in terminations if isinstance(termination, PowerOutlet)]
    if len(outlets) != 1:
        raise RuntimeError(f'{port.device.name} {port.name} cable {port.cable_id} does not terminate on exactly one PowerOutlet.')
    return pdu_side(outlets[0].device)


def validate_split_power(rack):
    failures = []
    for ports in all_downstream_power_ports_by_device(rack).values():
        device = ports[0].device
        if len(ports) < 2:
            failures.append(f'{device.name}: has only {len(ports)} power port(s)')
            continue
        sides = {opposite_pdu_side(port) for port in ports}
        if None in sides:
            failures.append(f'{device.name}: has uncabled power ports')
            continue
        if not set(PDU_SIDES).issubset(sides):
            failures.append(f'{device.name}: connected PDU sides={sorted(sides)}')
    if failures:
        raise RuntimeError(f'Rack {rack.name} has devices without split A/B PDU power: {failures[:10]}')


def connect_rack(rack, tenant, counters):
    pdus_by_side = get_pdu_pair(rack)
    outlets_by_side = available_outlets_by_side(pdus_by_side)
    ports_by_device = downstream_power_ports_by_device(rack)

    assignments = []
    for ports in ports_by_device.values():
        ordered_ports = sorted(ports, key=port_sort_key)
        if len(ordered_ports) == 1:
            raise RuntimeError(f'{ordered_ports[0].device.name} in rack {rack.name} has only one uncabled power port.')
        for index, port in enumerate(ordered_ports):
            side = desired_side(port, index)
            assignments.append((side, port))

    # Assign C20/C22 loads first so high-current inlets reserve the combo outlets.
    assignments.sort(key=lambda item: (compatible_outlet_type(item[1]) != 'iec-60320-c19', port_sort_key(item[1])))

    with transaction.atomic():
        for side, port in assignments:
            outlet = reserve_outlet(outlets_by_side, side, port)
            create_power_cable(outlet, port, tenant, counters)
        validate_split_power(rack)

    counters['racks_connected'] += 1


def main():
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    racks = list(
        Rack.objects.filter(site__slug=MAD_SITE_SLUG)
        .exclude(role__slug=NVL72_RACK_ROLE_SLUG)
        .filter(devices__device_type__slug=PDU_DEVICE_TYPE_SLUG)
        .distinct()
        .order_by('name')
    )
    counters = Counter()

    for rack in racks:
        connect_rack(rack, tenant, counters)

    print('Madison conventional-rack PDU power cable seeding complete.')
    print(f'conventional_racks_with_pdus={len(racks)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(
        'conventional_downstream_power_ports_cabled='
        f'{PowerPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__rack__isnull=False, cable__isnull=False).exclude(device__rack__role__slug=NVL72_RACK_ROLE_SLUG).exclude(device__device_type__slug=PDU_DEVICE_TYPE_SLUG).count()}'
    )
    print(
        'conventional_pdu_outlets_cabled='
        f'{PowerOutlet.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=PDU_DEVICE_TYPE_SLUG, cable__isnull=False).count()}'
    )


main()
