from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Q

from dcim.models import Device, DeviceBay, DeviceBayTemplate, Rack


NVL72_APPLIANCE_DEVICE_TYPE_SLUG = 'nvl72-rackscale-appliance'
NVL72_APPLIANCE_ROLE_SLUG = 'nvl72-appliance'
NVL72_RACK_ROLE_SLUG = 'nvl72_poweredgexe9712'

NVL72_CHILD_DEVICE_TYPE_SLUGS = {
    'gb300ct',
    'gb300ps',
    'gb300st',
    'sn2201_m',
}

NVL72_BAY_LABELS_BY_DEVICE_TYPE = {
    'sn2201_m': ('BMC-01', 'MGMT-01'),
    'gb300ps': tuple(f'PWR-SHLF-{index:02d}' for index in range(1, 9)),
    'gb300ct': tuple(f'GPU-NODE-{index:02d}' for index in range(1, 19)),
    'gb300st': tuple(f'NVL-SW-{index:02d}' for index in range(1, 10)),
}


def natural_key(value: str):
    import re

    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value))


def nvl72_appliance_name(rack_or_slot: Rack | str) -> str:
    slot = rack_or_slot.name if isinstance(rack_or_slot, Rack) else str(rack_or_slot)
    return f'gs001-{slot.lower()}-nvl72-rackscale-appliance'


def is_nvl72_child_row(row: dict[str, str]) -> bool:
    return row.get('device_type_slug') in NVL72_CHILD_DEVICE_TYPE_SLUGS


def row_key(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (
        (row.get('physical_slot') or '').upper(),
        row.get('row_sheet') or '',
        row.get('source_cell') or '',
        row.get('device_type_slug') or '',
        row.get('ru_bottom') or '',
        row.get('ru_top') or '',
    )


def row_ru_sort_key(row: dict[str, str]) -> tuple[int, str]:
    return (-int(row.get('ru_top') or row.get('ru_bottom') or 0), row.get('source_cell') or '')


def nvl72_bay_assignments(rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str, str, str], str]:
    """Assign workbook rows to FRSD-style NVL72 rackscale component bay names."""
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if is_nvl72_child_row(row):
            grouped[((row.get('physical_slot') or '').upper(), row['device_type_slug'])].append(row)

    assignments = {}
    for (_slot, device_type_slug), device_rows in grouped.items():
        labels = NVL72_BAY_LABELS_BY_DEVICE_TYPE.get(device_type_slug, ())
        for index, row in enumerate(sorted(device_rows, key=row_ru_sort_key), start=1):
            label = labels[index - 1] if index <= len(labels) else f'{device_type_slug.upper()}-{index:02d}'
            assignments[row_key(row)] = label
    return assignments


def nvl72_child_bay_name(row: dict[str, str], assignments: dict[tuple[str, str, str, str, str, str], str]) -> str | None:
    return assignments.get(row_key(row))


def parent_device_for_rack(rack: Rack) -> Device | None:
    return (
        Device.objects.filter(
            site=rack.site,
            name=nvl72_appliance_name(rack),
            device_type__slug=NVL72_APPLIANCE_DEVICE_TYPE_SLUG,
        )
        .select_related('device_type', 'role', 'rack', 'location', 'site')
        .first()
    )


def device_bay_for_child_row(
    *,
    rack: Rack,
    row: dict[str, str],
    assignments: dict[tuple[str, str, str, str, str, str], str],
) -> DeviceBay | None:
    parent = parent_device_for_rack(rack)
    if parent is None:
        return None
    bay_name = nvl72_child_bay_name(row, assignments)
    if not bay_name:
        return None
    return DeviceBay.objects.filter(device=parent, name=bay_name).first()


def ensure_device_bays_from_templates(device: Device, counters: Counter | None = None) -> dict[str, DeviceBay]:
    bays = {}
    for template in DeviceBayTemplate.objects.filter(device_type=device.device_type).order_by('name'):
        bay, created = DeviceBay.objects.get_or_create(
            device=device,
            name=template.name,
            defaults={'label': template.label, 'description': template.description},
        )
        changed = created
        for field, value in {'label': template.label, 'description': template.description}.items():
            if getattr(bay, field) != value:
                setattr(bay, field, value)
                changed = True
        if changed:
            bay.full_clean()
            bay.save()
        if counters is not None:
            counters['device_bays_created' if created else 'device_bays_updated' if changed else 'device_bays_unchanged'] += 1
        bays[bay.name] = bay
    return bays


def install_child_device_in_bay(device: Device, bay: DeviceBay, counters: Counter | None = None) -> None:
    if bay.installed_device_id == device.pk:
        if counters is not None:
            counters['nvl72_children_already_installed'] += 1
        return
    if bay.installed_device_id and bay.installed_device_id != device.pk:
        raise RuntimeError(f'NVL72 bay {bay.device.name}:{bay.name} already contains {bay.installed_device}.')
    bay.installed_device = device
    bay.full_clean()
    bay.save()
    if counters is not None:
        counters['nvl72_children_installed_in_device_bays'] += 1


def device_effective_rack(device: Device) -> Rack | None:
    if device.rack_id:
        return device.rack
    parent_bay = getattr(device, 'parent_bay', None)
    if parent_bay is not None and parent_bay.device_id:
        return parent_bay.device.rack
    return None


def device_effective_rack_filter(rack: Rack) -> Q:
    return Q(rack=rack) | Q(parent_bay__device__rack=rack)


def device_workbook_position(device: Device) -> Decimal | None:
    if device.position is not None:
        return device.position
    context = device.local_context_data if isinstance(device.local_context_data, dict) else {}
    workbook = context.get('madison_workbook') if isinstance(context.get('madison_workbook'), dict) else {}
    value = workbook.get('ru_bottom') or workbook.get('ru_top')
    if value is None:
        return None
    return Decimal(str(value))


def device_effective_position_sort_key(device: Device) -> tuple[Decimal, str]:
    return (device_workbook_position(device) or Decimal('0'), device.name)
