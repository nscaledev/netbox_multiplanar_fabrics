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
from extras.models import Tag, TaggedItem
from tenancy.models import Tenant

from netbox_plant_graph.models import PlantNode


MAD_SITE_SLUG = 'gs001'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'

BOX_ROLE_SLUG = 'sb'
TRAY_ROLE_SLUG = 'shuffle-tray'
CASSETTE_ROLE_SLUG = 'shuffle-cassette'
BOX_TYPE_SLUG = 'sb'
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
BATCH_SIZE = 5000


def chunks(items, size=BATCH_SIZE):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison shuffle placement manifest not found in: {MANIFEST_PATHS}')


def read_manifest() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1412:
        raise RuntimeError(f'Expected 1412 physical shuffle box placements; found {len(rows)}.')
    return rows


def old_cassette_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'gs001-{slot}-{ru}-{CASSETTE_TYPE_SLUG}'


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


def marker_context(row: dict[str, str]) -> dict:
    return {
        SOURCE_MARKER: {
            'physical_slot': row['physical_slot'],
            'source_cell': f'{row["row_sheet"]}!{row["source_cell"]}',
            'source_label': row['source_label'],
            'ru': int(row['ru_top']),
            'populated_cassettes': int(row['populated_cassettes']),
            'logical_shuffle_box': int(row['logical_shuffle_box']) if row['logical_shuffle_box'] else None,
            'nic_index_zero': int(row['nic_index_zero']) if row['nic_index_zero'] else None,
            'side': row['side'] or '',
        }
    }


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


def upsert_device_type(slug, model, u_height, description, comments, subdevice_role, counters):
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


def upsert_bay_template(device_type, name, label, description, counters):
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


def upsert_catalog(counters):
    box_role = upsert_role(BOX_ROLE_SLUG, 'Shuffle Box', '00bcd4', 'Passive 1RU shuffle box containing three trays.', counters)
    tray_role = upsert_role(TRAY_ROLE_SLUG, 'Shuffle Tray', '4dd0e1', 'Passive tray containing up to six 2x2 shuffle cassettes.', counters)
    cassette_role = upsert_role(CASSETTE_ROLE_SLUG, 'Shuffle Cassette', '80deea', 'Passive 2x2 MPO shuffle cassette.', counters)

    box_type = upsert_device_type(
        BOX_TYPE_SLUG,
        '1RU 3-Tray 18-Cassette Shuffle Box',
        '1.0',
        'Passive 1RU shuffle box with 3 tray slots and 18 total cassette capacity.',
        'Madison correction: workbook row-elevation shuffle labels represent physical 1RU boxes. MPO endpoints live on child cassette devices.',
        'parent',
        counters,
    )
    tray_type = upsert_device_type(
        TRAY_TYPE_SLUG,
        '6-Cassette Shuffle Tray',
        '0.0',
        'Passive shuffle tray with six cassette bays.',
        'A tray is installed in a 1RU shuffle box and contains up to six 2x2 shuffle cassettes.',
        'parent',
        counters,
    )
    cassette_type = upsert_device_type(
        CASSETTE_TYPE_SLUG,
        '2x2 MPO Shuffle Cassette',
        '0.0',
        'Passive 2x2 MPO shuffle cassette.',
        'A cassette is installed in a shuffle tray and exposes four front MPOs and four rear MPOs.',
        'child',
        counters,
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
    for index in range(1, 5):
        rear, rear_created = RearPortTemplate.objects.get_or_create(
            device_type=cassette_type,
            name=f'rear-mpo-{index:02d}',
            defaults={'type': 'mpo', 'positions': 1, 'description': 'MPO rear-side termination staged from Madison passive plant sources.', 'color': '2196f3'},
        )
        if rear_created:
            counters['rear_port_templates_created'] += 1
        _, front_created = FrontPortTemplate.objects.get_or_create(
            device_type=cassette_type,
            name=f'front-mpo-{index:02d}',
            defaults={'type': 'mpo', 'rear_port': rear, 'rear_port_position': 1, 'description': 'MPO front-side termination staged from Madison passive plant sources.', 'color': '4caf50'},
        )
        if front_created:
            counters['front_port_templates_created'] += 1

    return box_type, tray_type, cassette_type, box_role, tray_role, cassette_role


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


def cassette_name(box_name: str, ordinal: int) -> tuple[str, str, int]:
    tray_index = ((ordinal - 1) // 6) + 1
    cassette_index = ((ordinal - 1) % 6) + 1
    tray_name = f'{box_name}-tray-{tray_index:02d}'
    return f'{tray_name}-cassette-{cassette_index:02d}', tray_name, cassette_index


def bulk_create_missing(model, objects, counters, key):
    created = 0
    for batch in chunks(objects):
        model.objects.bulk_create(batch, batch_size=BATCH_SIZE, ignore_conflicts=True)
        created += len(batch)
    counters[key] += created


def add_row_tags(rows, box_devices, tray_devices, cassette_devices, row_tags, counters):
    device_ct = ContentType.objects.get_for_model(Device)
    tagged_items = []

    def append_tag(device_id, tag):
        if tag is not None:
            tagged_items.append(TaggedItem(content_type=device_ct, object_id=device_id, tag=tag))

    for row in rows:
        tag = row_tags.get(row['physical_slot'])
        append_tag(box_devices[row['box_name']].pk, tag)
        for tray_index in range(1, 4):
            append_tag(tray_devices[f'{row["box_name"]}-tray-{tray_index:02d}'].pk, tag)
        for ordinal in range(1, int(row['populated_cassettes']) + 1):
            name, _, _ = cassette_name(row['box_name'], ordinal)
            append_tag(cassette_devices[name].pk, tag)

    for batch in chunks(tagged_items):
        TaggedItem.objects.bulk_create(batch, batch_size=BATCH_SIZE, ignore_conflicts=True)
        counters['row_tag_links_attempted'] += len(batch)


def main() -> None:
    rows = read_manifest()
    counters = Counter()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    box_type, tray_type, cassette_type, box_role, tray_role, cassette_role = upsert_catalog(counters)
    racks = rack_by_slot()
    row_tags = row_tags_by_slot()
    device_ct = ContentType.objects.get_for_model(Device)

    old_names = [old_cassette_name(row) for row in rows]
    new_box_names = [row['box_name'] for row in rows]
    all_names = [*old_names, *new_box_names]

    with transaction.atomic():
        name_to_device = {device.name: device for device in Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=all_names)}
        legacy_by_box_name = {}
        missing = []
        for row in rows:
            device = name_to_device.get(row['box_name']) or name_to_device.get(old_cassette_name(row))
            if device is None:
                missing.append(old_cassette_name(row))
            else:
                legacy_by_box_name[row['box_name']] = device
        if missing:
            raise RuntimeError(f'Missing {len(missing)} legacy shuffle placement devices; first missing: {missing[0]}')

        legacy_ids = [device.pk for device in legacy_by_box_name.values()]
        if FrontPort.objects.filter(device_id__in=legacy_ids, cable__isnull=False).exists():
            raise RuntimeError('Refusing migration: a legacy shuffle cassette front port has a native cable attached.')
        if RearPort.objects.filter(device_id__in=legacy_ids, cable__isnull=False).exists():
            raise RuntimeError('Refusing migration: a legacy shuffle cassette rear port has a native cable attached.')

        deleted_nodes, _ = PlantNode.objects.filter(source_type=device_ct, source_id__in=legacy_ids).delete()
        counters['legacy_plant_graph_objects_deleted'] += deleted_nodes
        counters['legacy_front_ports_deleted'] += FrontPort.objects.filter(device_id__in=legacy_ids).count()
        FrontPort.objects.filter(device_id__in=legacy_ids).delete()
        counters['legacy_rear_ports_deleted'] += RearPort.objects.filter(device_id__in=legacy_ids).count()
        RearPort.objects.filter(device_id__in=legacy_ids).delete()
        DeviceBay.objects.filter(installed_device_id__in=legacy_ids).update(installed_device=None)

        slots = sorted({row['physical_slot'] for row in rows})
        stale_box_names = [f'gs001-{slot.lower()}-sb-01' for slot in slots]
        stale_tray_names = [f'{box_name}-tray-{index:02d}' for box_name in stale_box_names for index in range(1, 4)]
        stale_tray_ids = list(Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=stale_tray_names).values_list('id', flat=True))
        stale_box_ids = list(Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=stale_box_names).values_list('id', flat=True))
        DeviceBay.objects.filter(installed_device_id__in=stale_tray_ids + stale_box_ids).update(installed_device=None)
        deleted_trays, _ = Device.objects.filter(id__in=stale_tray_ids).delete()
        deleted_boxes, _ = Device.objects.filter(id__in=stale_box_ids).delete()
        counters['stale_tray_objects_deleted'] += deleted_trays
        counters['stale_box_objects_deleted'] += deleted_boxes

        box_updates = []
        for row in rows:
            rack = racks.get(row['physical_slot'])
            if rack is None:
                raise RuntimeError(f'No GS001 rack found for shuffle box slot {row["physical_slot"]}.')
            box = legacy_by_box_name[row['box_name']]
            box.name = row['box_name']
            box.device_type = box_type
            box.role = box_role
            box.tenant = tenant
            box.site = rack.site
            box.location = rack.location
            box.rack = rack
            box.position = Decimal(row['ru_top'])
            box.face = 'front'
            box.status = PLANNED_STATUS
            box.description = f'1RU shuffle box; {row["populated_cassettes"]} populated cassettes from row-elevation label.'
            box.comments = marker_comments(row, 'sb')
            box.local_context_data = marker_context(row)
            box_updates.append(box)
        Device.objects.bulk_update(
            box_updates,
            ['name', 'device_type', 'role', 'tenant', 'site', 'location', 'rack', 'position', 'face', 'status', 'description', 'comments', 'local_context_data'],
            batch_size=BATCH_SIZE,
        )
        counters['shuffle_boxes_updated'] += len(box_updates)

        box_devices = {device.name: device for device in Device.objects.filter(id__in=legacy_ids)}

        existing_box_bays = set(DeviceBay.objects.filter(device_id__in=legacy_ids).values_list('device_id', 'name'))
        box_bays_to_create = []
        for box in box_devices.values():
            for index in range(1, 4):
                key = (box.pk, f'tray-{index:02d}')
                if key not in existing_box_bays:
                    box_bays_to_create.append(DeviceBay(device=box, name=key[1], label=f'Tray {index}', description=f'Shuffle tray slot {index}.'))
        bulk_create_missing(DeviceBay, box_bays_to_create, counters, 'box_device_bays_created')

        tray_names = [f'{row["box_name"]}-tray-{index:02d}' for row in rows for index in range(1, 4)]
        existing_tray_names = set(Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=tray_names).values_list('name', flat=True))
        trays_to_create = []
        for row in rows:
            rack = racks[row['physical_slot']]
            for index in range(1, 4):
                name = f'{row["box_name"]}-tray-{index:02d}'
                if name not in existing_tray_names:
                    trays_to_create.append(
                        Device(
                            site=rack.site,
                            name=name,
                            device_type=tray_type,
                            role=tray_role,
                            tenant=tenant,
                            status=PLANNED_STATUS,
                            description=f'Tray {index} in {row["box_name"]}.',
                            comments=marker_comments(row, f'shuffle-tray-{index:02d}'),
                        )
                    )
        bulk_create_missing(Device, trays_to_create, counters, 'shuffle_trays_created')
        tray_devices = {device.name: device for device in Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=tray_names)}

        existing_tray_bays = set(DeviceBay.objects.filter(device_id__in=[tray.pk for tray in tray_devices.values()]).values_list('device_id', 'name'))
        tray_bays_to_create = []
        for tray in tray_devices.values():
            for index in range(1, 7):
                key = (tray.pk, f'cassette-{index:02d}')
                if key not in existing_tray_bays:
                    tray_bays_to_create.append(DeviceBay(device=tray, name=key[1], label=f'Cassette {index}', description=f'Shuffle cassette bay {index}.'))
        bulk_create_missing(DeviceBay, tray_bays_to_create, counters, 'tray_device_bays_created')

        box_bays = {(bay.device_id, bay.name): bay for bay in DeviceBay.objects.filter(device_id__in=legacy_ids)}
        tray_bay_updates = []
        for row in rows:
            box = box_devices[row['box_name']]
            for index in range(1, 4):
                bay = box_bays[(box.pk, f'tray-{index:02d}')]
                tray = tray_devices[f'{row["box_name"]}-tray-{index:02d}']
                if bay.installed_device_id != tray.pk:
                    bay.installed_device_id = tray.pk
                    tray_bay_updates.append(bay)
        DeviceBay.objects.bulk_update(tray_bay_updates, ['installed_device'], batch_size=BATCH_SIZE)
        counters['trays_installed_in_boxes'] += len(tray_bay_updates)

        cassette_specs = []
        for row in rows:
            rack = racks[row['physical_slot']]
            for ordinal in range(1, int(row['populated_cassettes']) + 1):
                name, tray_name, cassette_index = cassette_name(row['box_name'], ordinal)
                cassette_specs.append((row, rack, name, tray_name, cassette_index, ordinal))
        cassette_names = [name for _, _, name, _, _, _ in cassette_specs]
        existing_cassette_names = set(Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=cassette_names).values_list('name', flat=True))
        cassettes_to_create = []
        for row, rack, name, _, _, ordinal in cassette_specs:
            if name in existing_cassette_names:
                continue
            cassettes_to_create.append(
                Device(
                    site=rack.site,
                    name=name,
                    device_type=cassette_type,
                    role=cassette_role,
                    tenant=tenant,
                    status=PLANNED_STATUS,
                    description=f'Cassette {ordinal} in {row["box_name"]}.',
                    comments=marker_comments(row, f'shuffle-cassette-{ordinal:02d}'),
                )
            )
        bulk_create_missing(Device, cassettes_to_create, counters, 'shuffle_cassettes_created')
        cassette_devices = {device.name: device for device in Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=cassette_names)}

        tray_bays = {
            (bay.device_id, bay.name): bay
            for bay in DeviceBay.objects.filter(device_id__in=[tray.pk for tray in tray_devices.values()])
        }
        cassette_bay_updates = []
        for _, _, name, tray_name, cassette_index, _ in cassette_specs:
            tray = tray_devices[tray_name]
            bay = tray_bays[(tray.pk, f'cassette-{cassette_index:02d}')]
            cassette = cassette_devices[name]
            if bay.installed_device_id != cassette.pk:
                bay.installed_device_id = cassette.pk
                cassette_bay_updates.append(bay)
        DeviceBay.objects.bulk_update(cassette_bay_updates, ['installed_device'], batch_size=BATCH_SIZE)
        counters['cassettes_installed_in_trays'] += len(cassette_bay_updates)

        cassette_ids = [device.pk for device in cassette_devices.values()]
        existing_rear = set(RearPort.objects.filter(device_id__in=cassette_ids).values_list('device_id', 'name'))
        rear_to_create = []
        for cassette in cassette_devices.values():
            for index in range(1, 5):
                name = f'rear-mpo-{index:02d}'
                if (cassette.pk, name) not in existing_rear:
                    rear_to_create.append(RearPort(device=cassette, name=name, type='mpo', positions=1, description='MPO rear-side termination staged from Madison passive plant sources.', color='2196f3'))
        bulk_create_missing(RearPort, rear_to_create, counters, 'rear_ports_created')

        rear_ports = {(port.device_id, port.name): port for port in RearPort.objects.filter(device_id__in=cassette_ids)}
        existing_front = set(FrontPort.objects.filter(device_id__in=cassette_ids).values_list('device_id', 'name'))
        front_to_create = []
        for cassette in cassette_devices.values():
            for index in range(1, 5):
                name = f'front-mpo-{index:02d}'
                if (cassette.pk, name) not in existing_front:
                    front_to_create.append(
                        FrontPort(
                            device=cassette,
                            name=name,
                            type='mpo',
                            rear_port=rear_ports[(cassette.pk, f'rear-mpo-{index:02d}')],
                            rear_port_position=1,
                            description='MPO front-side termination staged from Madison passive plant sources.',
                            color='4caf50',
                        )
                    )
        bulk_create_missing(FrontPort, front_to_create, counters, 'front_ports_created')

        add_row_tags(rows, box_devices, tray_devices, cassette_devices, row_tags, counters)

    print('Madison shuffle elevation fast migration complete.')
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
