from __future__ import annotations

import os
import re
from collections import Counter
from decimal import Decimal

from django.db import transaction

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    InterfaceTemplate,
    Manufacturer,
    PowerOutlet,
    PowerOutletTemplate,
    PowerPort,
    PowerPortTemplate,
    Rack,
)
from netbox_power_plant.models import PowerHandoffPoint
from tenancy.models import Tenant


MAD_SITE_SLUG = os.environ.get('MADISON_SITE_SLUG', 'gs001')
DEVICE_NAME_PREFIX = os.environ.get('MADISON_DEVICE_NAME_PREFIX', 'gs001')
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
NVL72_RACK_ROLE_SLUG = 'nvl72_poweredgexe9712'
MANUFACTURER_SLUG = 'apc'
DEVICE_TYPE_SLUG = 'apc-apdu11450me'
DEVICE_ROLE_SLUG = 'PDU'
INPUT_PORT_NAME = 'input'
INPUT_PORT_TYPE = 'iec-60309-560p6'
C13_C15_OUTLET_TYPE = 'iec-60320-c13'
COMBO_OUTLET_TYPE = 'iec-60320-c19'
MGMT_INTERFACE_NAME = 'mgmt'
MGMT_INTERFACE_TYPE = '1000base-t'
PDU_SIDES = ('a', 'b')
SOURCE_MARKER_BEGIN = '<!-- madison-apdu11450me-pdu:start -->'
SOURCE_MARKER_END = '<!-- madison-apdu11450me-pdu:end -->'


def row_id_slot(rack):
    row_tags = [tag for tag in rack.tags.all() if tag.slug.startswith(ROW_ID_TAG_PREFIX)]
    if len(row_tags) != 1:
        raise RuntimeError(f'Rack {rack.name} has {len(row_tags)} row-id tags; expected exactly one.')
    return row_tags[0].slug[len(ROW_ID_TAG_PREFIX):], row_tags[0]


def circuit_number(handoff_point):
    match = re.search(r'CKT(?P<number>\d+)$', handoff_point.name)
    if match is None:
        raise RuntimeError(f'Power handoff point does not end with CKT number: {handoff_point.name}')
    return int(match.group('number'))


def staged_comments(rack, side, slot):
    return f"""\
{SOURCE_MARKER_BEGIN}
APDU11450ME rack-mounted PDU inferred from Madison purchase order details.
- rack: {rack.name}
- nscale_row_id: {slot}
- pdu_side: {side.upper()}
- source: Schneider/APC APDU11450ME product details
{SOURCE_MARKER_END}"""


def merge_staged_comments(existing, staged):
    if SOURCE_MARKER_BEGIN in existing and SOURCE_MARKER_END in existing:
        prefix, rest = existing.split(SOURCE_MARKER_BEGIN, 1)
        _, suffix = rest.split(SOURCE_MARKER_END, 1)
        return f'{prefix.rstrip()}\n\n{staged}\n{suffix.lstrip()}'.strip()
    return f'{existing.rstrip()}\n\n{staged}'.strip() if existing else staged


def upsert_device_type(counters):
    manufacturer = Manufacturer.objects.get(slug=MANUFACTURER_SLUG)
    device_type, created = DeviceType.objects.get_or_create(
        manufacturer=manufacturer,
        slug=DEVICE_TYPE_SLUG,
        defaults={'model': 'APDU11450ME'},
    )
    values = {
        'model': 'APDU11450ME',
        'part_number': 'APDU11450ME',
        'u_height': Decimal('0.0'),
        'is_full_depth': False,
        'airflow': 'passive',
        'description': 'APC NetShelter Rack PDU Advanced Gen 2, metered, 3-phase, 415V 60A, 560P6, 42 outlets.',
        'comments': (
            'Schneider/APC product detail: metered 3-phase rack PDU, 34.6kW at 415V 60A, '
            '560P6 / IEC 60309 60A 3P+N+PE input, 21 C13/C15 outlets, '
            '21 C13/C15/C19/C21 combination outlets, vertical 0U rack-mounted.'
        ),
    }
    changed = created
    for field, value in values.items():
        if getattr(device_type, field) != value:
            setattr(device_type, field, value)
            changed = True
    if changed:
        device_type.full_clean()
        device_type.save()
    counters['device_types_created' if created else 'device_types_updated' if changed else 'device_types_unchanged'] += 1

    input_template, input_created = PowerPortTemplate.objects.get_or_create(
        device_type=device_type,
        name=INPUT_PORT_NAME,
        defaults={
            'type': INPUT_PORT_TYPE,
            'description': '560P6 / IEC 60309 60A 3P+N+PE input cord.',
            'maximum_draw': 34600,
        },
    )
    input_values = {
        'type': INPUT_PORT_TYPE,
        'description': '560P6 / IEC 60309 60A 3P+N+PE input cord.',
        'maximum_draw': 34600,
        'allocated_draw': None,
    }
    input_changed = input_created
    for field, value in input_values.items():
        if getattr(input_template, field) != value:
            setattr(input_template, field, value)
            input_changed = True
    if input_changed:
        input_template.full_clean()
        input_template.save()
    counters['power_port_templates_created' if input_created else 'power_port_templates_updated' if input_changed else 'power_port_templates_unchanged'] += 1

    for index in range(1, 22):
        upsert_outlet_template(
            device_type,
            input_template,
            f'c13-c15-{index:02d}',
            C13_C15_OUTLET_TYPE,
            'IEC 60320 C13/C15 outlet.',
            counters,
        )
    for index in range(1, 22):
        upsert_outlet_template(
            device_type,
            input_template,
            f'combo-{index:02d}',
            COMBO_OUTLET_TYPE,
            'IEC 60320 C13/C15/C19/C21 combination outlet.',
            counters,
        )

    interface_template, interface_created = InterfaceTemplate.objects.get_or_create(
        device_type=device_type,
        name=MGMT_INTERFACE_NAME,
        defaults={
            'type': MGMT_INTERFACE_TYPE,
            'mgmt_only': True,
            'description': 'APDU11450ME management Ethernet port.',
        },
    )
    interface_values = {
        'type': MGMT_INTERFACE_TYPE,
        'enabled': True,
        'mgmt_only': True,
        'description': 'APDU11450ME management Ethernet port.',
    }
    interface_changed = interface_created
    for field, value in interface_values.items():
        if getattr(interface_template, field) != value:
            setattr(interface_template, field, value)
            interface_changed = True
    if interface_changed:
        interface_template.full_clean()
        interface_template.save()
    counters['interface_templates_created' if interface_created else 'interface_templates_updated' if interface_changed else 'interface_templates_unchanged'] += 1

    return device_type


def upsert_outlet_template(device_type, input_template, name, outlet_type, description, counters):
    outlet, created = PowerOutletTemplate.objects.get_or_create(
        device_type=device_type,
        name=name,
        defaults={
            'type': outlet_type,
            'power_port': input_template,
            'description': description,
        },
    )
    desired = {
        'type': outlet_type,
        'power_port': input_template,
        'description': description,
    }
    changed = created
    for field, value in desired.items():
        if getattr(outlet, field) != value:
            setattr(outlet, field, value)
            changed = True
    if changed:
        outlet.full_clean()
        outlet.save()
    counters['power_outlet_templates_created' if created else 'power_outlet_templates_updated' if changed else 'power_outlet_templates_unchanged'] += 1


def upsert_power_port(device, template, counters):
    port, created = PowerPort.objects.get_or_create(
        device=device,
        name=template.name,
        defaults={
            'type': template.type,
            'description': template.description,
            'maximum_draw': template.maximum_draw,
            'allocated_draw': template.allocated_draw,
        },
    )
    desired = {
        'type': template.type,
        'description': template.description,
        'maximum_draw': template.maximum_draw,
        'allocated_draw': template.allocated_draw,
    }
    changed = created
    for field, value in desired.items():
        if getattr(port, field) != value:
            setattr(port, field, value)
            changed = True
    if changed:
        port.full_clean()
        port.save()
    counters['power_ports_created' if created else 'power_ports_updated' if changed else 'power_ports_unchanged'] += 1
    return port


def upsert_power_outlet(device, template, input_port, counters):
    outlet, created = PowerOutlet.objects.get_or_create(
        device=device,
        name=template.name,
        defaults={
            'type': template.type,
            'description': template.description,
            'power_port': input_port,
            'feed_leg': template.feed_leg,
        },
    )
    desired = {
        'type': template.type,
        'description': template.description,
        'power_port': input_port,
        'feed_leg': template.feed_leg,
    }
    changed = created
    for field, value in desired.items():
        if getattr(outlet, field) != value:
            setattr(outlet, field, value)
            changed = True
    if changed:
        outlet.full_clean()
        outlet.save()
    counters['power_outlets_created' if created else 'power_outlets_updated' if changed else 'power_outlets_unchanged'] += 1


def upsert_interface(device, template, counters):
    interface, created = Interface.objects.get_or_create(
        device=device,
        name=template.name,
        defaults={
            'type': template.type,
            'enabled': template.enabled,
            'mgmt_only': template.mgmt_only,
            'description': template.description,
        },
    )
    desired = {
        'type': template.type,
        'enabled': template.enabled,
        'mgmt_only': template.mgmt_only,
        'description': template.description,
    }
    changed = created
    for field, value in desired.items():
        if getattr(interface, field) != value:
            setattr(interface, field, value)
            changed = True
    if changed:
        interface.full_clean()
        interface.save()
    counters['interfaces_created' if created else 'interfaces_updated' if changed else 'interfaces_unchanged'] += 1


def upsert_pdu_device(rack, side, slot, row_tag, device_type, role, tenant, counters):
    name = f'{DEVICE_NAME_PREFIX}-{slot.lower()}-pdu-{side}'
    device, created = Device.objects.get_or_create(
        site=rack.site,
        name=name,
        defaults={
            'device_type': device_type,
            'role': role,
            'tenant': tenant,
            'location': rack.location,
            'rack': rack,
            'status': PLANNED_STATUS,
        },
    )
    desired = {
        'device_type': device_type,
        'role': role,
        'tenant': tenant,
        'site': rack.site,
        'location': rack.location,
        'rack': rack,
        'position': None,
        'face': None,
        'status': PLANNED_STATUS,
        'description': f'APDU11450ME rack-mounted PDU {side.upper()}',
        'comments': merge_staged_comments(device.comments or '', staged_comments(rack, side, slot)),
    }
    changed = created
    for field, value in desired.items():
        if getattr(device, field) != value:
            setattr(device, field, value)
            changed = True
    if changed:
        device.full_clean()
        device.save()
    if row_tag and not device.tags.filter(pk=row_tag.pk).exists():
        device.tags.add(row_tag)
        counters['row_id_tags_added'] += 1
    counters['pdu_devices_created' if created else 'pdu_devices_updated' if changed else 'pdu_devices_unchanged'] += 1
    return device


def bind_handoff_points(rack, pdu_by_side, counters):
    handoff_points = list(
        PowerHandoffPoint.objects.filter(
            power_system__site__slug=MAD_SITE_SLUG,
            power_port__device__rack=rack,
        )
        .select_related('power_port__device__rack')
        .order_by('name')
    )
    if not handoff_points:
        already_bound_count = PowerHandoffPoint.objects.filter(
            power_system__site__slug=MAD_SITE_SLUG,
            power_port__device__in=pdu_by_side.values(),
            power_port__name=INPUT_PORT_NAME,
        ).count()
        if already_bound_count == 2:
            counters['racks_with_handoff_points_already_bound'] += 1
        elif already_bound_count:
            raise RuntimeError(f'Rack {rack.name} has {already_bound_count} handoff points already bound to PDU inputs; expected 2.')
        else:
            counters['racks_without_handoff_points'] += 1
        return
    if len(handoff_points) != 2:
        raise RuntimeError(f'Rack {rack.name} has {len(handoff_points)} rack-derived handoff points; expected 2.')

    side_by_circuit = {1: 'a', 2: 'b'}
    for handoff_point in handoff_points:
        circuit = circuit_number(handoff_point)
        side = side_by_circuit.get(circuit)
        if side is None:
            raise RuntimeError(f'{handoff_point.name} has unexpected circuit number {circuit}; expected CKT1 or CKT2.')
        pdu = pdu_by_side[side]
        input_port = PowerPort.objects.get(device=pdu, name=INPUT_PORT_NAME)
        handoff_point.power_port = input_port
        handoff_point.full_clean()
        handoff_point.save()
        counters['handoff_points_bound_to_pdu_inputs'] += 1


def main():
    counters = Counter()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    role = DeviceRole.objects.get(slug=DEVICE_ROLE_SLUG)

    with transaction.atomic():
        device_type = upsert_device_type(counters)
        input_template = PowerPortTemplate.objects.get(device_type=device_type, name=INPUT_PORT_NAME)
        outlet_templates = list(PowerOutletTemplate.objects.filter(device_type=device_type).order_by('name'))
        interface_template = InterfaceTemplate.objects.get(device_type=device_type, name=MGMT_INTERFACE_NAME)
        racks = (
            Rack.objects.filter(site__slug=MAD_SITE_SLUG)
            .exclude(role__slug=NVL72_RACK_ROLE_SLUG)
            .select_related('site', 'location', 'role')
            .prefetch_related('tags')
            .order_by('name')
        )
        for rack in racks:
            slot, row_tag = row_id_slot(rack)
            pdu_by_side = {}
            for side in PDU_SIDES:
                device = upsert_pdu_device(rack, side, slot, row_tag, device_type, role, tenant, counters)
                input_port = upsert_power_port(device, input_template, counters)
                for outlet_template in outlet_templates:
                    upsert_power_outlet(device, outlet_template, input_port, counters)
                upsert_interface(device, interface_template, counters)
                pdu_by_side[side] = device
            bind_handoff_points(rack, pdu_by_side, counters)

    print('Madison APDU11450ME rack PDU seeding complete.')
    print(f'conventional_non_nvl72_racks={racks.count()}')
    print(f'expected_pdu_devices={racks.count() * len(PDU_SIDES)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'apdu11450me_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=DEVICE_TYPE_SLUG).count()}')
    print(f'apdu11450me_power_ports={PowerPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=DEVICE_TYPE_SLUG).count()}')
    print(f'apdu11450me_power_outlets={PowerOutlet.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=DEVICE_TYPE_SLUG).count()}')
    print(f'apdu11450me_interfaces={Interface.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=DEVICE_TYPE_SLUG).count()}')
    print(f'non_nvl72_rack_derived_handoff_points={PowerHandoffPoint.objects.filter(power_system__site__slug=MAD_SITE_SLUG, power_port__device__rack__isnull=False).exclude(power_port__device__rack__role__slug=NVL72_RACK_ROLE_SLUG).count()}')
    print(f'non_nvl72_pdu_input_handoff_points={PowerHandoffPoint.objects.filter(power_system__site__slug=MAD_SITE_SLUG, power_port__device__device_type__slug=DEVICE_TYPE_SLUG).count()}')


main()
