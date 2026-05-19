from __future__ import annotations

from collections import Counter

from django.db import transaction

from dcim.models import Device, PowerPort, PowerPortTemplate


MAD_SITE_SLUG = 'mad-1'

# Passive plant objects intentionally have no power ports.
PASSIVE_DEVICE_TYPE_SLUGS = {
    'shuffle-cassette-2x2-mpo',
}


def template_defaults(template):
    return {
        'label': template.label,
        'description': template.description,
        'type': template.type,
        'maximum_draw': template.maximum_draw,
        'allocated_draw': template.allocated_draw,
    }


def sync_power_port(device, template, counters):
    port, created = PowerPort.objects.get_or_create(
        device=device,
        name=template.name,
        defaults=template_defaults(template),
    )
    changed = False
    if not created:
        for field, value in template_defaults(template).items():
            if getattr(port, field) != value:
                setattr(port, field, value)
                changed = True

    if created or changed:
        port.full_clean()
        port.save()

    counters['power_ports_created' if created else 'power_ports_updated' if changed else 'power_ports_unchanged'] += 1


def main():
    devices = (
        Device.objects.filter(site__slug=MAD_SITE_SLUG)
        .select_related('device_type', 'device_type__manufacturer')
        .order_by('device_type__slug', 'name')
    )
    templates_by_device_type_id = {
        device_type_id: list(templates)
        for device_type_id, templates in _group_templates().items()
    }
    counters = Counter()
    active_types_without_templates = set()

    with transaction.atomic():
        for device in devices:
            templates = templates_by_device_type_id.get(device.device_type_id, [])
            counters[f'device_type_{device.device_type.slug}_devices'] += 1

            if not templates:
                if device.device_type.slug in PASSIVE_DEVICE_TYPE_SLUGS:
                    counters[f'device_type_{device.device_type.slug}_passive_no_power_ports'] += 1
                    continue
                active_types_without_templates.add(device.device_type.slug)
                counters[f'device_type_{device.device_type.slug}_missing_power_port_templates'] += 1
                continue

            for template in templates:
                sync_power_port(device, template, counters)

    if active_types_without_templates:
        raise RuntimeError(
            f'MAD-1 active device types without power port templates: {sorted(active_types_without_templates)}'
        )

    print('Madison power-port template sync complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'madison_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG).count()}')
    print(f'madison_power_ports={PowerPort.objects.filter(device__site__slug=MAD_SITE_SLUG).count()}')


def _group_templates():
    grouped = {}
    for template in PowerPortTemplate.objects.select_related('device_type').order_by('device_type_id', 'name'):
        grouped.setdefault(template.device_type_id, []).append(template)
    return grouped


main()
