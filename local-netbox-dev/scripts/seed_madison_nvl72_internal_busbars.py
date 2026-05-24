from __future__ import annotations

import os
from collections import Counter

from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from dcim.models import PowerPort, Rack
from netbox_power_plant.choices import (
    DesignStateChoices,
    InternalPowerBusAttachmentRoleChoices,
    InternalPowerBusRoleChoices,
    SupplyTypeChoices,
)
from netbox_power_plant.models import InternalPowerBus, InternalPowerBusAttachment, PowerSystem


MAD_SITE_SLUG = os.environ.get('MADISON_SITE_SLUG', 'gs001')
POWER_SYSTEM_NAME = os.environ.get('MADISON_POWER_SYSTEM_NAME', 'GS001 Electrical Plant')
ALLOW_PARTIAL = os.environ.get('MADISON_NVL72_BUSBAR_ALLOW_PARTIAL') == '1'
NVL72_RACK_ROLE_SLUG = 'nvl72_poweredgexe9712'
POWER_SHELF_DEVICE_TYPE_SLUGS = ('gb300ps', 'ps33-33kw-power-shelf')
POWER_SHELF_PORT_NAME = 'busbar-output-1'
LOAD_DEVICE_TYPE_SLUGS = (
    'gb300ct',
    'poweredge-xe9712-gb300-compute-tray',
    'gb300st',
    'gb300-nvl72-nvlink-switch-tray',
    'sn2201_m',
)
LOAD_POWER_PORT_NAMES = ('nvl72-busbar', 'busbar-input-1')
EXPECTED_SOURCE_PORTS_PER_RACK = 8
EXPECTED_LOAD_PORTS_PER_RACK = 29
def bus_name(rack):
    return f'NVL72 Busbar {rack.name}'


def attachment_name(bus, power_port, role):
    return f'{bus.name} {role} {power_port.device.name} {power_port.name}'[:100]


def power_ports_for_rack(rack, *, device_type_slugs, port_names):
    return list(
        PowerPort.objects.select_related(
            'device',
            'device__device_type',
            'device__rack',
            'device__parent_bay__device__rack',
        )
        .filter(
            device__site__slug=MAD_SITE_SLUG,
            device__device_type__slug__in=device_type_slugs,
            name__in=port_names,
        )
        .filter(Q(device__rack=rack) | Q(device__parent_bay__device__rack=rack))
        .order_by('device__local_context_data__madison_workbook__ru_bottom', 'device__position', 'device__name', 'name')
    )


def upsert_bus(power_system, rack, counters):
    name = bus_name(rack)
    bus, created = InternalPowerBus.objects.get_or_create(
        rack=rack,
        name=name,
        defaults={
            'slug': slugify(name),
            'power_system': power_system,
        },
    )
    desired = {
        'slug': slugify(name),
        'power_system': power_system,
        'rack': rack,
        'bus_role': InternalPowerBusRoleChoices.ROLE_BUSBAR,
        'supply_type': SupplyTypeChoices.SUPPLY_DC,
        'nominal_voltage': 50,
        'design_state': DesignStateChoices.STATE_PLANNED,
        'description': 'NVL72 rack-internal shared busbar distribution.',
    }
    changed = created
    for field, value in desired.items():
        if getattr(bus, field) != value:
            setattr(bus, field, value)
            changed = True
    if changed:
        bus.full_clean()
        bus.save()
    counters['buses_created' if created else 'buses_updated' if changed else 'buses_unchanged'] += 1
    return bus


def upsert_attachment(bus, power_port, role, position_index, counters):
    name = attachment_name(bus, power_port, role)
    attachment, created = InternalPowerBusAttachment.objects.get_or_create(
        internal_power_bus=bus,
        power_port=power_port,
        defaults={
            'name': name,
            'slug': slugify(name),
            'attachment_role': role,
        },
    )
    desired = {
        'name': name,
        'slug': slugify(name),
        'attachment_role': role,
        'position_index': position_index,
        'design_state': DesignStateChoices.STATE_PLANNED,
        'description': 'NVL72 rack-internal busbar attachment.',
    }
    changed = created
    for field, value in desired.items():
        if getattr(attachment, field) != value:
            setattr(attachment, field, value)
            changed = True
    if changed:
        attachment.full_clean()
        attachment.save()
    counters['attachments_created' if created else 'attachments_updated' if changed else 'attachments_unchanged'] += 1
    counters[f'attachments_{role}'] += 1
    return attachment


def main():
    power_system = PowerSystem.objects.get(name=POWER_SYSTEM_NAME, site__slug=MAD_SITE_SLUG)
    racks = Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__slug=NVL72_RACK_ROLE_SLUG).order_by('name')
    counters = Counter()
    skipped = []

    with transaction.atomic():
        for rack in racks:
            source_ports = power_ports_for_rack(
                rack,
                device_type_slugs=POWER_SHELF_DEVICE_TYPE_SLUGS,
                port_names=(POWER_SHELF_PORT_NAME,),
            )
            load_ports = power_ports_for_rack(
                rack,
                device_type_slugs=LOAD_DEVICE_TYPE_SLUGS,
                port_names=LOAD_POWER_PORT_NAMES,
            )
            if len(source_ports) != EXPECTED_SOURCE_PORTS_PER_RACK or len(load_ports) != EXPECTED_LOAD_PORTS_PER_RACK:
                skipped.append((rack.name, len(source_ports), len(load_ports)))
                continue

            bus = upsert_bus(power_system, rack, counters)
            for index, power_port in enumerate(source_ports, start=1):
                upsert_attachment(
                    bus,
                    power_port,
                    InternalPowerBusAttachmentRoleChoices.ROLE_SOURCE,
                    index,
                    counters,
                )
            for index, power_port in enumerate(load_ports, start=1):
                upsert_attachment(
                    bus,
                    power_port,
                    InternalPowerBusAttachmentRoleChoices.ROLE_LOAD,
                    index,
                    counters,
                )

    if skipped:
        for rack_name, source_count, load_count in skipped:
            print(f'skipped_rack={rack_name} source_ports={source_count} load_ports={load_count}')
        if not ALLOW_PARTIAL:
            raise RuntimeError('Refusing to complete NVL72 busbar seeding because one or more racks are not 8:29.')

    print('Madison NVL72 internal busbar seeding complete.')
    print(f'nvl72_racks={racks.count()}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'internal_power_buses={InternalPowerBus.objects.filter(power_system=power_system, rack__role__slug=NVL72_RACK_ROLE_SLUG).count()}')
    print(
        'internal_power_bus_attachments='
        f'{InternalPowerBusAttachment.objects.filter(internal_power_bus__power_system=power_system, internal_power_bus__rack__role__slug=NVL72_RACK_ROLE_SLUG).count()}'
    )


main()
