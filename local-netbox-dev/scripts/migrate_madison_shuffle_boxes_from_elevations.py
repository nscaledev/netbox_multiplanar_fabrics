from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import (
    Device,
    DeviceBay,
    DeviceBayTemplate,
    DeviceRole,
    DeviceType,
    FrontPort,
    FrontPortTemplate,
    Manufacturer,
    Rack,
    RearPort,
    RearPortTemplate,
)
from extras.models import Tag
from tenancy.models import Tenant

from netbox_plant_graph.models import PlantNode


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'

BOX_ROLE_SLUG = 'shuffle-box'
TRAY_ROLE_SLUG = 'shuffle-tray'
CASSETTE_ROLE_SLUG = 'shuffle-cassette'
BOX_TYPE_SLUG = 'shuffle-box-3tray-18cassette'
TRAY_TYPE_SLUG = 'shuffle-tray-6cassette'
CASSETTE_TYPE_SLUG = 'shuffle-cassette-2x2-mpo'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'

SOURCE_MARKER = 'madison_shuffle_elevation_migration_v1'
SOURCE_MARKER_BEGIN = '<!-- madison-shuffle-elevation-migration:start -->'
SOURCE_MARKER_END = '<!-- madison-shuffle-elevation-migration:end -->'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_shuffle_box_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_shuffle_box_placement_manifest.csv'),
]


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison shuffle placement manifest not found in: {MANIFEST_PATHS}')


def old_cassette_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'mad1-{slot}-{ru}-{CASSETTE_TYPE_SLUG}'


def read_manifest() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError('Madison shuffle placement manifest is empty.')
    return rows


def upsert_role(slug: str, name: str, color: str, description: str, counters: Counter) -> DeviceRole:
    role = DeviceRole.objects.filter(slug=slug).first() or DeviceRole(slug=slug)
    created = role.pk is None
    changed = created
    for field, value in {'name': name, 'color': color, 'description': description}.items():
        if getattr(role, field) != value:
            setattr(role, field, value)
            changed = True
    if changed:
        role.full_clean()
        role.save()
    counters['roles_created' if created else 'roles_updated' if changed else 'roles_unchanged'] += 1
    return role


def upsert_device_type(
    slug: str,
    model: str,
    u_height: str,
    description: str,
    comments: str,
    counters: Counter,
    *,
    subdevice_role: str | None,
) -> DeviceType:
    manufacturer = Manufacturer.objects.get(slug='nscale')
    device_type = DeviceType.objects.filter(manufacturer=manufacturer, slug=slug).first() or DeviceType(
        manufacturer=manufacturer,
        slug=slug,
    )
    created = device_type.pk is None
    changed = created
    desired = {
        'model': model,
        'part_number': '',
        'u_height': Decimal(u_height),
        'is_full_depth': False,
        'airflow': 'passive',
        'subdevice_role': subdevice_role,
        'description': description,
        'comments': comments,
    }
    for field, value in desired.items():
        if getattr(device_type, field) != value:
            setattr(device_type, field, value)
            changed = True
    if changed:
        device_type.full_clean()
        device_type.save()
    counters['device_types_created' if created else 'device_types_updated' if changed else 'device_types_unchanged'] += 1
    return device_type


def upsert_bay_template(device_type: DeviceType, name: str, label: str, description: str, counters: Counter) -> None:
    template, created = DeviceBayTemplate.objects.get_or_create(
        device_type=device_type,
        name=name,
        defaults={'label': label, 'description': description},
    )
    changed = created
    for field, value in {'label': label, 'description': description}.items():
        if getattr(template, field) != value:
            setattr(template, field, value)
            changed = True
    if changed:
        template.full_clean()
        template.save()
    counters['device_bay_templates_created' if created else 'device_bay_templates_updated' if changed else 'device_bay_templates_unchanged'] += 1


def upsert_mpo_templates(cassette_type: DeviceType, counters: Counter) -> None:
    for index in range(1, 5):
        rear, rear_created = RearPortTemplate.objects.get_or_create(
            device_type=cassette_type,
            name=f'rear-mpo-{index:02d}',
            defaults={
                'type': 'mpo',
                'positions': 1,
                'description': 'MPO rear-side termination staged from Madison passive plant sources.',
                'color': '2196f3',
            },
        )
        if rear_created:
            counters['rear_port_templates_created'] += 1
        front, front_created = FrontPortTemplate.objects.get_or_create(
            device_type=cassette_type,
            name=f'front-mpo-{index:02d}',
            defaults={
                'type': 'mpo',
                'rear_port': rear,
                'rear_port_position': 1,
                'description': 'MPO front-side termination staged from Madison passive plant sources.',
                'color': '4caf50',
            },
        )
        if front_created:
            counters['front_port_templates_created'] += 1


def upsert_catalog(counters: Counter) -> tuple[DeviceType, DeviceType, DeviceType, DeviceRole, DeviceRole, DeviceRole]:
    box_role = upsert_role(BOX_ROLE_SLUG, 'Shuffle Box', '00bcd4', 'Passive 1RU shuffle box containing three trays.', counters)
    tray_role = upsert_role(TRAY_ROLE_SLUG, 'Shuffle Tray', '4dd0e1', 'Passive tray containing up to six 2x2 shuffle cassettes.', counters)
    cassette_role = upsert_role(CASSETTE_ROLE_SLUG, 'Shuffle Cassette', '80deea', 'Passive 2x2 MPO shuffle cassette.', counters)

    box_type = upsert_device_type(
        BOX_TYPE_SLUG,
        '1RU 3-Tray 18-Cassette Shuffle Box',
        '1.0',
        'Passive 1RU shuffle box with 3 tray slots and 18 total cassette capacity.',
        'Madison correction: workbook row-elevation shuffle labels represent physical 1RU boxes. MPO endpoints live on child cassette devices.',
        counters,
        subdevice_role='parent',
    )
    tray_type = upsert_device_type(
        TRAY_TYPE_SLUG,
        '6-Cassette Shuffle Tray',
        '0.0',
        'Passive shuffle tray with six cassette bays.',
        'A tray is installed in a 1RU shuffle box and contains up to six 2x2 shuffle cassettes.',
        counters,
        subdevice_role='parent',
    )
    cassette_type = upsert_device_type(
        CASSETTE_TYPE_SLUG,
        '2x2 MPO Shuffle Cassette',
        '0.0',
        'Passive 2x2 MPO shuffle cassette.',
        'A cassette is installed in a shuffle tray and exposes four front MPOs and four rear MPOs.',
        counters,
        subdevice_role='child',
    )

    if FrontPort.objects.filter(device__device_type=box_type, cable__isnull=False).exists():
        raise RuntimeError('Refusing to remove box-level front ports while native cables are attached.')
    if RearPort.objects.filter(device__device_type=box_type, cable__isnull=False).exists():
        raise RuntimeError('Refusing to remove box-level rear ports while native cables are attached.')
    counters['box_front_ports_deleted'] += FrontPort.objects.filter(device__device_type=box_type).count()
    FrontPort.objects.filter(device__device_type=box_type).delete()
    counters['box_rear_ports_deleted'] += RearPort.objects.filter(device__device_type=box_type).count()
    RearPort.objects.filter(device__device_type=box_type).delete()
    FrontPortTemplate.objects.filter(device_type=box_type).delete()
    RearPortTemplate.objects.filter(device_type=box_type).delete()

    for index in range(1, 4):
        upsert_bay_template(box_type, f'tray-{index:02d}', f'Tray {index}', f'Shuffle tray slot {index}.', counters)
    for index in range(1, 7):
        upsert_bay_template(tray_type, f'cassette-{index:02d}', f'Cassette {index}', f'Shuffle cassette bay {index}.', counters)
    upsert_mpo_templates(cassette_type, counters)
    return box_type, tray_type, cassette_type, box_role, tray_role, cassette_role


def current_parent_bay(device: Device):
    try:
        return device.parent_bay
    except Device.parent_bay.RelatedObjectDoesNotExist:
        return None


def ensure_device_bays(device: Device, counters: Counter) -> None:
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
            bay.save()
        counters['device_bays_created' if created else 'device_bays_updated' if changed else 'device_bays_unchanged'] += 1


def ensure_cassette_ports(cassette: Device, counters: Counter) -> None:
    for index in range(1, 5):
        rear, rear_created = RearPort.objects.get_or_create(
            device=cassette,
            name=f'rear-mpo-{index:02d}',
            defaults={
                'type': 'mpo',
                'positions': 1,
                'description': 'MPO rear-side termination staged from Madison passive plant sources.',
                'color': '2196f3',
            },
        )
        if rear_created:
            counters['rear_ports_created'] += 1
        front, front_created = FrontPort.objects.get_or_create(
            device=cassette,
            name=f'front-mpo-{index:02d}',
            defaults={
                'type': 'mpo',
                'rear_port': rear,
                'rear_port_position': 1,
                'description': 'MPO front-side termination staged from Madison passive plant sources.',
                'color': '4caf50',
            },
        )
        if front_created:
            counters['front_ports_created'] += 1


def rack_by_slot() -> dict[str, Rack]:
    racks = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').select_related('site', 'location'):
        row_tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith(ROW_ID_TAG_PREFIX)]
        if len(row_tags) == 1:
            racks[row_tags[0][len(ROW_ID_TAG_PREFIX):].upper()] = rack
    return racks


def row_tags_by_slot() -> dict[str, Tag]:
    return {
        tag.slug[len(ROW_ID_TAG_PREFIX):].upper(): tag
        for tag in Tag.objects.filter(slug__startswith=ROW_ID_TAG_PREFIX)
    }


def marker_comments(row: dict[str, str], role: str) -> str:
    logical = row.get('logical_shuffle_box') or ''
    nic = row.get('nic_index_zero') or ''
    side = row.get('side') or ''
    return f"""\
{SOURCE_MARKER_BEGIN}
Madison shuffle hierarchy corrected from row-elevation workbook labels.
- source_cell: {row['row_sheet']}!{row['source_cell']}
- source_label: {row['source_label']}
- physical_slot: {row['physical_slot']}
- ru: {row['ru_top']}
- modeled_role: {role}
- populated_cassettes_in_box: {row['populated_cassettes']}
- logical_shuffle_box: {logical}
- nic_index_zero: {nic}
- side: {side}
{SOURCE_MARKER_END}"""


def set_row_tag(device: Device, tag: Tag | None) -> None:
    if tag is None:
        return
    device.tags.add(tag)


def install_device(child: Device, parent: Device, bay_name: str, counters: Counter) -> None:
    old_parent = current_parent_bay(child)
    if old_parent and (old_parent.device_id != parent.pk or old_parent.name != bay_name):
        old_parent.installed_device = None
        old_parent.save()
        counters['devices_removed_from_old_bays'] += 1

    bay = DeviceBay.objects.get(device=parent, name=bay_name)
    if bay.installed_device_id == child.pk:
        counters['devices_already_installed'] += 1
        return
    if bay.installed_device_id is not None:
        raise RuntimeError(f'{parent.name} {bay.name} already contains {bay.installed_device.name}; cannot install {child.name}.')
    bay.installed_device = child
    bay.save()
    counters['devices_installed'] += 1


def update_device(device: Device, counters: Counter, key: str, **desired) -> None:
    changed = False
    for field, value in desired.items():
        if getattr(device, field) != value:
            setattr(device, field, value)
            changed = True
    if changed:
        device.save()
    counters[f'{key}_updated' if changed else f'{key}_unchanged'] += 1


def prepare_legacy_devices(rows: list[dict[str, str]], box_type: DeviceType, counters: Counter) -> dict[str, Device]:
    old_names = [old_cassette_name(row) for row in rows]
    new_names = [row['box_name'] for row in rows]
    name_to_device = {device.name: device for device in Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=[*old_names, *new_names])}
    missing = [name for name in old_names if name not in name_to_device and name.replace(CASSETTE_TYPE_SLUG, 'shuffle-box') not in name_to_device]
    if missing:
        raise RuntimeError(f'Missing {len(missing)} legacy shuffle placement devices; first missing: {missing[0]}')

    devices = []
    for row in rows:
        devices.append(name_to_device.get(row['box_name']) or name_to_device[old_cassette_name(row)])
    device_ids = [device.pk for device in devices]

    if FrontPort.objects.filter(device_id__in=device_ids, cable__isnull=False).exists():
        raise RuntimeError('Refusing shuffle migration: a legacy shuffle cassette front port has a native cable attached.')
    if RearPort.objects.filter(device_id__in=device_ids, cable__isnull=False).exists():
        raise RuntimeError('Refusing shuffle migration: a legacy shuffle cassette rear port has a native cable attached.')

    device_ct = ContentType.objects.get_for_model(Device)
    deleted_nodes, _ = PlantNode.objects.filter(source_type=device_ct, source_id__in=device_ids).delete()
    counters['legacy_plant_graph_objects_deleted'] += deleted_nodes

    counters['legacy_front_ports_deleted'] += FrontPort.objects.filter(device_id__in=device_ids).count()
    FrontPort.objects.filter(device_id__in=device_ids).delete()
    counters['legacy_rear_ports_deleted'] += RearPort.objects.filter(device_id__in=device_ids).count()
    RearPort.objects.filter(device_id__in=device_ids).delete()
    DeviceBay.objects.filter(installed_device_id__in=device_ids).update(installed_device=None)

    return {row['box_name']: device for row, device in zip(rows, devices, strict=True)}


def delete_stale_containers(rows: list[dict[str, str]], counters: Counter) -> None:
    slots = sorted({row['physical_slot'] for row in rows})
    stale_box_names = [f'mad1-{slot.lower()}-shuffle-box-01' for slot in slots]
    stale_tray_names = [f'{box_name}-tray-{index:02d}' for box_name in stale_box_names for index in range(1, 4)]
    stale_trays = Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=stale_tray_names)
    stale_boxes = Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=stale_box_names)
    DeviceBay.objects.filter(installed_device_id__in=stale_trays.values_list('id', flat=True)).update(installed_device=None)
    DeviceBay.objects.filter(installed_device_id__in=stale_boxes.values_list('id', flat=True)).update(installed_device=None)
    deleted_trays, _ = stale_trays.delete()
    deleted_boxes, _ = stale_boxes.delete()
    counters['stale_tray_objects_deleted'] += deleted_trays
    counters['stale_box_objects_deleted'] += deleted_boxes


def cassette_name(box_name: str, ordinal: int) -> tuple[str, str, int]:
    tray_index = ((ordinal - 1) // 6) + 1
    cassette_index = ((ordinal - 1) % 6) + 1
    tray_name = f'{box_name}-tray-{tray_index:02d}'
    return f'{tray_name}-cassette-{cassette_index:02d}', tray_name, cassette_index


def main() -> None:
    rows = read_manifest()
    counters = Counter()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    box_type, tray_type, cassette_type, box_role, tray_role, cassette_role = upsert_catalog(counters)
    racks = rack_by_slot()
    row_tags = row_tags_by_slot()

    if len(rows) != 1412:
        raise RuntimeError(f'Expected 1412 physical shuffle box placements; found {len(rows)}.')

    with transaction.atomic():
        box_devices = prepare_legacy_devices(rows, box_type, counters)
        delete_stale_containers(rows, counters)

        for row in rows:
            slot = row['physical_slot']
            rack = racks.get(slot)
            if rack is None:
                raise RuntimeError(f'No MAD-1 rack found for shuffle box slot {slot}.')

            box = box_devices[row['box_name']]
            update_device(
                box,
                counters,
                'shuffle_boxes',
                name=row['box_name'],
                device_type=box_type,
                role=box_role,
                tenant=tenant,
                site=rack.site,
                location=rack.location,
                rack=rack,
                position=Decimal(row['ru_top']),
                face='front',
                status=PLANNED_STATUS,
                description=f'1RU shuffle box; {row["populated_cassettes"]} populated cassettes from row-elevation label.',
                comments=marker_comments(row, 'shuffle-box'),
                local_context_data={
                    SOURCE_MARKER: {
                        'physical_slot': slot,
                        'source_cell': f'{row["row_sheet"]}!{row["source_cell"]}',
                        'source_label': row['source_label'],
                        'ru': int(row['ru_top']),
                        'populated_cassettes': int(row['populated_cassettes']),
                        'logical_shuffle_box': int(row['logical_shuffle_box']) if row['logical_shuffle_box'] else None,
                        'nic_index_zero': int(row['nic_index_zero']) if row['nic_index_zero'] else None,
                        'side': row['side'] or '',
                    }
                },
            )
            set_row_tag(box, row_tags.get(slot))
            ensure_device_bays(box, counters)

            for tray_index in range(1, 4):
                tray_name = f'{box.name}-tray-{tray_index:02d}'
                tray, created = Device.objects.get_or_create(
                    site=rack.site,
                    name=tray_name,
                    defaults={
                        'device_type': tray_type,
                        'role': tray_role,
                        'tenant': tenant,
                        'status': PLANNED_STATUS,
                    },
                )
                counters['shuffle_trays_created' if created else 'shuffle_trays_found'] += 1
                update_device(
                    tray,
                    counters,
                    'shuffle_trays',
                    device_type=tray_type,
                    role=tray_role,
                    tenant=tenant,
                    site=rack.site,
                    location=None,
                    rack=None,
                    position=None,
                    face='',
                    status=PLANNED_STATUS,
                    description=f'Tray {tray_index} in {box.name}.',
                    comments=marker_comments(row, f'shuffle-tray-{tray_index:02d}'),
                )
                set_row_tag(tray, row_tags.get(slot))
                ensure_device_bays(tray, counters)
                install_device(tray, box, f'tray-{tray_index:02d}', counters)

            for ordinal in range(1, int(row['populated_cassettes']) + 1):
                name, tray_name, cassette_index = cassette_name(box.name, ordinal)
                cassette, created = Device.objects.get_or_create(
                    site=rack.site,
                    name=name,
                    defaults={
                        'device_type': cassette_type,
                        'role': cassette_role,
                        'tenant': tenant,
                        'status': PLANNED_STATUS,
                    },
                )
                counters['shuffle_cassettes_created' if created else 'shuffle_cassettes_found'] += 1
                update_device(
                    cassette,
                    counters,
                    'shuffle_cassettes',
                    device_type=cassette_type,
                    role=cassette_role,
                    tenant=tenant,
                    site=rack.site,
                    location=None,
                    rack=None,
                    position=None,
                    face='',
                    status=PLANNED_STATUS,
                    description=f'Cassette {ordinal} in {box.name}.',
                    comments=marker_comments(row, f'shuffle-cassette-{ordinal:02d}'),
                )
                set_row_tag(cassette, row_tags.get(slot))
                ensure_cassette_ports(cassette, counters)
                tray = Device.objects.get(site=rack.site, name=tray_name)
                install_device(cassette, tray, f'cassette-{cassette_index:02d}', counters)

    print('Madison shuffle elevation migration complete.')
    print(f'shuffle_box_placements={len(rows)}')
    print(f'expected_trays={len(rows) * 3}')
    print(f'expected_populated_cassettes={sum(int(row["populated_cassettes"]) for row in rows)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'shuffle_boxes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=BOX_TYPE_SLUG).count()}')
    print(f'shuffle_boxes_racked={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=BOX_TYPE_SLUG, rack__isnull=False).count()}')
    print(f'shuffle_trays={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=TRAY_TYPE_SLUG).count()}')
    print(f'shuffle_cassettes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassette_front_ports={FrontPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassette_rear_ports={RearPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=CASSETTE_TYPE_SLUG).count()}')


main()
