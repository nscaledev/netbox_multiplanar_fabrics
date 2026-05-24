from __future__ import annotations

import csv
import os
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
import sys

from django.db import transaction

from dcim.models import Device, DeviceRole, DeviceType, Rack
from tenancy.models import Tenant


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_nvl72_appliance import (  # noqa: E402
    NVL72_APPLIANCE_DEVICE_TYPE_SLUG,
    NVL72_APPLIANCE_ROLE_SLUG,
    NVL72_CHILD_DEVICE_TYPE_SLUGS,
    NVL72_RACK_ROLE_SLUG,
    device_bay_for_child_row,
    ensure_device_bays_from_templates,
    install_child_device_in_bay,
    natural_key,
    nvl72_appliance_name,
    nvl72_bay_assignments,
)


MAD_SITE_SLUG = os.environ.get('MADISON_SITE_SLUG', 'gs001')
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
SOURCE_MARKER_BEGIN = '<!-- madison-nvl72-rackscale-appliance:start -->'
SOURCE_MARKER_END = '<!-- madison-nvl72-rackscale-appliance:end -->'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_NVL72_APPLIANCE_APPLY') == '1'


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison device placement manifest not found in: {MANIFEST_PATHS}')


def read_manifest() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        return list(csv.DictReader(handle))


def selected_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row for row in rows
        if row['rack_role_slug'] == NVL72_RACK_ROLE_SLUG
        and row['device_type_slug'] in NVL72_CHILD_DEVICE_TYPE_SLUGS
    ]


def device_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'{MAD_SITE_SLUG}-{slot}-{ru}-{row["device_type_slug"]}'


def staged_comments(rack: Rack, rows: list[dict[str, str]]) -> str:
    rack_labels = sorted({row['rack_source_label'] for row in rows})
    su_tags = sorted({value for row in rows for value in (row.get('scalable_units') or '').split(',') if value})
    return f"""\
{SOURCE_MARKER_BEGIN}
Madison NVL72 rack-scale parent appliance created from workbook rack/device manifests and FRSD component authority.
- physical_slot: {rack.name}
- rack_source_labels: {', '.join(rack_labels) if rack_labels else 'unknown'}
- scalable_units: {', '.join(su_tags) if su_tags else 'from rack tags'}
- child_bay_authority: DCS0013344-GB300-Nscale, North Carolina-X01 - FRSD.pdf
{SOURCE_MARKER_END}"""


def print_dry_run(rows: list[dict[str, str]]) -> None:
    by_slot = defaultdict(Counter)
    for row in rows:
        by_slot[row['physical_slot'].upper()][row['device_type_slug']] += 1
    print('Madison NVL72 rack-scale appliance dry run')
    print(f'nvl72_racks={len(by_slot)}')
    for slot in sorted(by_slot, key=natural_key)[:20]:
        print(f'  {slot}: {dict(sorted(by_slot[slot].items()))}')
    if len(by_slot) > 20:
        print(f'  ... {len(by_slot) - 20} more racks')


def ensure_parent_device(
    *,
    rack: Rack,
    rows: list[dict[str, str]],
    tenant: Tenant,
    device_type: DeviceType,
    role: DeviceRole,
    counters: Counter,
) -> Device:
    name = nvl72_appliance_name(rack)
    parent, created = Device.objects.get_or_create(
        site=rack.site,
        name=name,
        defaults={
            'device_type': device_type,
            'role': role,
            'tenant': tenant,
            'site': rack.site,
            'location': rack.location,
            'rack': rack,
            'position': Decimal('1'),
            'face': 'front',
            'status': PLANNED_STATUS,
            'description': f'NVL72 rack-scale parent appliance for rack {rack.name}.',
            'comments': staged_comments(rack, rows),
            'local_context_data': {
                'madison_nvl72_rackscale_appliance': True,
                'physical_slot': rack.name,
            },
        },
    )
    desired = {
        'device_type': device_type,
        'role': role,
        'tenant': tenant,
        'site': rack.site,
        'location': rack.location,
        'rack': rack,
        'position': Decimal('1'),
        'face': 'front',
        'status': PLANNED_STATUS,
        'description': f'NVL72 rack-scale parent appliance for rack {rack.name}.',
        'comments': staged_comments(rack, rows),
        'local_context_data': {
            'madison_nvl72_rackscale_appliance': True,
            'physical_slot': rack.name,
        },
    }
    changed = created
    for field, value in desired.items():
        if getattr(parent, field) != value:
            setattr(parent, field, value)
            changed = True
    if created or changed:
        parent.full_clean()
        parent.save()
    counters['nvl72_parent_devices_created' if created else 'nvl72_parent_devices_updated' if changed else 'nvl72_parent_devices_unchanged'] += 1
    for tag in rack.tags.all():
        if not parent.tags.filter(pk=tag.pk).exists():
            parent.tags.add(tag)
            counters['nvl72_parent_tags_added'] += 1
    return parent


def unmount_existing_children(rack: Rack, counters: Counter) -> dict[str, Device]:
    children = {
        device.name: device
        for device in Device.objects.filter(
            site=rack.site,
            rack=rack,
            device_type__slug__in=NVL72_CHILD_DEVICE_TYPE_SLUGS,
        ).select_related('device_type', 'role', 'rack', 'location', 'site')
    }
    for child in children.values():
        child.rack = None
        child.position = None
        child.face = ''
        child.full_clean()
        child.save()
        counters['nvl72_children_unmounted_from_rack_units'] += 1
    return children


def install_existing_children(
    *,
    rack: Rack,
    rows: list[dict[str, str]],
    assignments: dict,
    preloaded_children: dict[str, Device],
    counters: Counter,
) -> None:
    for row in rows:
        child = preloaded_children.get(device_name(row)) or Device.objects.filter(site=rack.site, name=device_name(row)).first()
        if child is None:
            counters['nvl72_child_devices_missing_for_bays'] += 1
            continue
        bay = device_bay_for_child_row(rack=rack, row=row, assignments=assignments)
        if bay is None:
            counters['nvl72_child_bays_missing'] += 1
            continue
        child.site = rack.site
        child.location = rack.location
        child.rack = None
        child.position = None
        child.face = ''
        child.full_clean()
        child.save()
        install_child_device_in_bay(child, bay, counters)


def main() -> None:
    rows = selected_rows(read_manifest())
    print_dry_run(rows)
    if not apply_enabled():
        print('apply=false; set MADISON_NVL72_APPLIANCE_APPLY=1 to create/update parent appliance devices and bay bindings.')
        return

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    device_type = DeviceType.objects.get(slug=NVL72_APPLIANCE_DEVICE_TYPE_SLUG)
    role = DeviceRole.objects.get(slug=NVL72_APPLIANCE_ROLE_SLUG)
    racks = {
        rack.name.upper(): rack
        for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__slug=NVL72_RACK_ROLE_SLUG)
        .prefetch_related('tags')
        .select_related('site', 'location')
    }
    rows_by_slot = defaultdict(list)
    for row in rows:
        rows_by_slot[row['physical_slot'].upper()].append(row)

    missing_racks = sorted(set(rows_by_slot) - set(racks), key=natural_key)
    if missing_racks:
        raise RuntimeError(f'Missing NVL72 NetBox racks for slots: {missing_racks[:20]}')

    assignments = nvl72_bay_assignments(rows)
    counters = Counter()
    with transaction.atomic():
        for slot in sorted(rows_by_slot, key=natural_key):
            rack = racks[slot]
            preloaded_children = unmount_existing_children(rack, counters)
            parent = ensure_parent_device(
                rack=rack,
                rows=rows_by_slot[slot],
                tenant=tenant,
                device_type=device_type,
                role=role,
                counters=counters,
            )
            ensure_device_bays_from_templates(parent, counters)
            install_existing_children(
                rack=rack,
                rows=rows_by_slot[slot],
                assignments=assignments,
                preloaded_children=preloaded_children,
                counters=counters,
            )

    print('Madison NVL72 rack-scale appliance seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(
        f'nvl72_parent_devices={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=NVL72_APPLIANCE_DEVICE_TYPE_SLUG).count()}'
    )


main()
