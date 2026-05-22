from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

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


MAD_SITE_SLUG = 'gs001'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
SU_TAG_PREFIX = 'nv_su_'

BOX_ROLE_SLUG = 'sb'
TRAY_ROLE_SLUG = 'shuffle-tray'
CASSETTE_ROLE_SLUG = 'shuffle-cassette'
BOX_TYPE_SLUG = 'sb'
TRAY_TYPE_SLUG = 'shuffle-tray-6cassette'
CASSETTE_TYPE_SLUG = 'shuffle-cassette-2x2-mpo'

SOURCE_MARKER_BEGIN = '<!-- madison-shuffle-hierarchy:start -->'
SOURCE_MARKER_END = '<!-- madison-shuffle-hierarchy:end -->'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_device_placement_manifest.csv'),
    Path(__file__).resolve().parents[1] / 'data' / 'generated' / 'madison_device_placement_manifest.csv',
]


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison device placement manifest not found in: {MANIFEST_PATHS}')


def read_shuffle_rows():
    with manifest_path().open(newline='') as handle:
        rows = [row for row in csv.DictReader(handle) if row['device_type_slug'] == CASSETTE_TYPE_SLUG]
    if not rows:
        raise RuntimeError('No shuffle cassette rows found in Madison device placement manifest.')
    return rows


def device_name(row: dict[str, str]) -> str:
    slot = row['physical_slot'].lower()
    ru_top = int(row['ru_top'])
    ru_bottom = int(row['ru_bottom'])
    ru = f'u{ru_top:02d}' if ru_top == ru_bottom else f'u{ru_bottom:02d}-{ru_top:02d}'
    return f'gs001-{slot}-{ru}-{row["device_type_slug"]}'


def merge_staged_comments(existing, staged):
    if SOURCE_MARKER_BEGIN in existing and SOURCE_MARKER_END in existing:
        prefix, rest = existing.split(SOURCE_MARKER_BEGIN, 1)
        _, suffix = rest.split(SOURCE_MARKER_END, 1)
        return f'{prefix.rstrip()}\n\n{staged}\n{suffix.lstrip()}'.strip()
    return f'{existing.rstrip()}\n\n{staged}'.strip() if existing else staged


def staged_box_comments(slot, cassette_count):
    return f"""\
{SOURCE_MARKER_BEGIN}
Madison shuffle hierarchy container inferred from workbook shuffle cassette placements and ROCE 4-plane Shuffle Cabling Patterns.
- nscale_row_id: {slot}
- modeled_role: shuffle box
- capacity: 3 trays / 18 cassettes
- populated_cassettes: {cassette_count}
{SOURCE_MARKER_END}"""


def staged_tray_comments(slot, tray_index, cassette_count):
    return f"""\
{SOURCE_MARKER_BEGIN}
Madison shuffle hierarchy container inferred from workbook shuffle cassette placements and ROCE 4-plane Shuffle Cabling Patterns.
- nscale_row_id: {slot}
- modeled_role: shuffle tray
- tray_index: {tray_index}
- capacity: 6 cassettes
- populated_cassettes: {cassette_count}
{SOURCE_MARKER_END}"""


def upsert_role(slug, name, color, description, counters):
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


def upsert_device_type(slug, model, u_height, description, comments, counters, *, subdevice_role=None):
    manufacturer = Manufacturer.objects.get(slug='nscale')
    device_type = DeviceType.objects.filter(manufacturer=manufacturer, slug=slug).first() or DeviceType(
        manufacturer=manufacturer,
        slug=slug,
    )
    created = device_type.pk is None
    changed = created
    values = {
        'model': model,
        'part_number': '',
        'u_height': Decimal(str(u_height)),
        'is_full_depth': False,
        'airflow': 'passive',
        'subdevice_role': subdevice_role,
        'description': description,
    }
    if created:
        values['comments'] = comments
    for field, value in values.items():
        if getattr(device_type, field) != value:
            setattr(device_type, field, value)
            changed = True
    if changed:
        device_type.full_clean()
        device_type.save()
    counters['device_types_created' if created else 'device_types_updated' if changed else 'device_types_unchanged'] += 1
    return device_type


def upsert_bay_template(device_type, name, label, description, counters):
    bay, created = DeviceBayTemplate.objects.get_or_create(
        device_type=device_type,
        name=name,
        defaults={'label': label, 'description': description},
    )
    changed = created
    for field, value in {'label': label, 'description': description}.items():
        if getattr(bay, field) != value:
            setattr(bay, field, value)
            changed = True
    if changed:
        bay.full_clean()
        bay.save()
    counters['device_bay_templates_created' if created else 'device_bay_templates_updated' if changed else 'device_bay_templates_unchanged'] += 1
    return bay


def ensure_bays_from_templates(device, counters):
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
        counters['device_bays_created' if created else 'device_bays_updated' if changed else 'device_bays_unchanged'] += 1


def ensure_no_box_mpo_templates(box_type, counters):
    actual_front = FrontPort.objects.filter(device__device_type=box_type)
    actual_rear = RearPort.objects.filter(device__device_type=box_type)
    cabled_actual = actual_front.filter(cable__isnull=False).count() + actual_rear.filter(cable__isnull=False).count()
    if cabled_actual:
        raise RuntimeError(f'{box_type.slug} has {cabled_actual} cabled MPO ports; refusing to remove box-level port model.')

    counters['box_front_ports_deleted'] += actual_front.count()
    actual_front.delete()
    counters['box_rear_ports_deleted'] += actual_rear.count()
    actual_rear.delete()

    front_templates = FrontPortTemplate.objects.filter(device_type=box_type)
    rear_templates = RearPortTemplate.objects.filter(device_type=box_type)
    counters['box_front_port_templates_deleted'] += front_templates.count()
    front_templates.delete()
    counters['box_rear_port_templates_deleted'] += rear_templates.count()
    rear_templates.delete()


def upsert_catalog(counters):
    upsert_role(TRAY_ROLE_SLUG, 'Shuffle Tray', '4dd0e1', 'Passive tray containing up to six 2x2 shuffle cassettes.', counters)
    box_type = upsert_device_type(
        BOX_TYPE_SLUG,
        '3-Tray 18-Cassette Shuffle Box',
        '4.0',
        'Passive shuffle box with 3 trays and 18 total cassette capacity.',
        """\
Notion architecture: one shuffle box contains 3 trays; each tray contains 6 cassettes; each cassette exposes 4 front MPOs and 4 rear MPOs.
This definition models physical containment only. MPO endpoints live on child cassette devices; internal shuffle behavior remains graph-model data.
""",
        counters,
        subdevice_role='parent',
    )
    tray_type = upsert_device_type(
        TRAY_TYPE_SLUG,
        '6-Cassette Shuffle Tray',
        '0.0',
        'Passive shuffle tray with six cassette bays.',
        'Notion architecture: trays are installed in shuffle boxes and contain up to six 2x2 shuffle cassettes.',
        counters,
        subdevice_role='parent',
    )
    cassette_type = DeviceType.objects.get(slug=CASSETTE_TYPE_SLUG)
    if cassette_type.subdevice_role != 'child':
        cassette_type.subdevice_role = 'child'
        cassette_type.full_clean()
        cassette_type.save()
        counters['cassette_device_types_marked_child'] += 1

    ensure_no_box_mpo_templates(box_type, counters)

    for index in range(1, 4):
        upsert_bay_template(box_type, f'tray-{index:02d}', f'Tray {index}', f'Shuffle tray bay {index}.', counters)
    for index in range(1, 7):
        upsert_bay_template(tray_type, f'cassette-{index:02d}', f'Cassette {index}', f'Shuffle cassette bay {index}.', counters)

    return box_type, tray_type, cassette_type


def rack_by_slot():
    racks = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').select_related('site', 'location'):
        row_tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith(ROW_ID_TAG_PREFIX)]
        if len(row_tags) != 1:
            raise RuntimeError(f'Rack {rack.name} has {len(row_tags)} row-id tags; expected exactly one.')
        racks[row_tags[0][len(ROW_ID_TAG_PREFIX):].upper()] = rack
    return racks


def replace_tags(device, tags):
    desired_ids = {tag.pk for tag in tags}
    current = list(device.tags.filter(slug__startswith=ROW_ID_TAG_PREFIX)) + list(device.tags.filter(slug__startswith=SU_TAG_PREFIX))
    for tag in current:
        if tag.pk not in desired_ids:
            device.tags.remove(tag)
    current_ids = {tag.pk for tag in device.tags.filter(pk__in=desired_ids)}
    for tag in tags:
        if tag.pk not in current_ids:
            device.tags.add(tag)


def child_su_tags(devices):
    tags = {}
    for device in devices:
        for tag in device.tags.filter(slug__startswith=SU_TAG_PREFIX):
            tags[tag.slug] = tag
    return [tags[slug] for slug in sorted(tags)]


def current_parent_bay(device):
    try:
        return device.parent_bay
    except Device.parent_bay.RelatedObjectDoesNotExist:
        return None


def upsert_box(slot, rack, cassette_count, box_type, role, tenant, counters):
    name = f'gs001-{slot.lower()}-sb-01'
    box, created = Device.objects.get_or_create(
        site=rack.site,
        name=name,
        defaults={
            'device_type': box_type,
            'role': role,
            'tenant': tenant,
            'location': rack.location,
            'rack': rack,
            'position': None,
            'face': '',
            'status': PLANNED_STATUS,
        },
    )
    desired = {
        'device_type': box_type,
        'role': role,
        'tenant': tenant,
        'site': rack.site,
        'location': rack.location,
        'rack': rack,
        'position': None,
        'face': '',
        'status': PLANNED_STATUS,
        'description': f'Shuffle box for {slot}; {cassette_count} populated cassette placements.',
        'comments': merge_staged_comments(box.comments or '', staged_box_comments(slot, cassette_count)),
        'local_context_data': {
            **(box.local_context_data or {}),
            'madison_shuffle_hierarchy': {
                'physical_slot': slot,
                'container_role': 'sb',
                'tray_capacity': 3,
                'cassette_capacity': 18,
                'populated_cassettes': cassette_count,
            },
        },
    }
    changed = created
    for field, value in desired.items():
        if getattr(box, field) != value:
            setattr(box, field, value)
            changed = True
    if changed:
        box.full_clean()
        box.save()
    ensure_bays_from_templates(box, counters)
    counters['shuffle_boxes_created' if created else 'shuffle_boxes_updated' if changed else 'shuffle_boxes_unchanged'] += 1
    return box


def upsert_tray(slot, box, tray_index, cassette_count, tray_type, role, tenant, counters):
    name = f'{box.name}-tray-{tray_index:02d}'
    tray, created = Device.objects.get_or_create(
        site=box.site,
        name=name,
        defaults={
            'device_type': tray_type,
            'role': role,
            'tenant': tenant,
            'location': None,
            'rack': None,
            'position': None,
            'face': '',
            'status': PLANNED_STATUS,
        },
    )
    desired = {
        'device_type': tray_type,
        'role': role,
        'tenant': tenant,
        'site': box.site,
        'location': None,
        'rack': None,
        'position': None,
        'face': '',
        'status': PLANNED_STATUS,
        'description': f'Shuffle tray {tray_index} in {box.name}; {cassette_count} populated cassettes.',
        'comments': merge_staged_comments(tray.comments or '', staged_tray_comments(slot, tray_index, cassette_count)),
        'local_context_data': {
            **(tray.local_context_data or {}),
            'madison_shuffle_hierarchy': {
                'physical_slot': slot,
                'container_role': 'shuffle-tray',
                'parent_box': box.name,
                'tray_index': tray_index,
                'cassette_capacity': 6,
                'populated_cassettes': cassette_count,
            },
        },
    }
    changed = created
    for field, value in desired.items():
        if getattr(tray, field) != value:
            setattr(tray, field, value)
            changed = True
    if changed:
        tray.full_clean()
        tray.save()

    box_bay = DeviceBay.objects.get(device=box, name=f'tray-{tray_index:02d}')
    if box_bay.installed_device_id != tray.pk:
        if box_bay.installed_device_id is not None:
            raise RuntimeError(f'{box.name} {box_bay.name} already contains {box_bay.installed_device.name}; cannot install {tray.name}.')
        box_bay.installed_device = tray
        box_bay.full_clean()
        box_bay.save()
        counters['trays_installed_in_box_bays'] += 1

    ensure_bays_from_templates(tray, counters)
    counters['shuffle_trays_created' if created else 'shuffle_trays_updated' if changed else 'shuffle_trays_unchanged'] += 1
    return tray


def install_cassette(cassette, tray, bay_index, counters):
    bay = DeviceBay.objects.get(device=tray, name=f'cassette-{bay_index:02d}')
    parent_bay = current_parent_bay(cassette)
    if parent_bay and parent_bay.pk != bay.pk:
        old_bay = parent_bay
        old_bay.installed_device = None
        old_bay.full_clean()
        old_bay.save()
        counters['cassettes_removed_from_old_bays'] += 1

    changed = False
    desired = {
        'rack': None,
        'location': None,
        'position': None,
        'face': '',
        'status': PLANNED_STATUS,
    }
    for field, value in desired.items():
        if getattr(cassette, field) != value:
            setattr(cassette, field, value)
            changed = True
    if changed:
        cassette.full_clean()
        cassette.save()
        counters['cassettes_updated_for_containment'] += 1

    if bay.installed_device_id != cassette.pk:
        if bay.installed_device_id is not None:
            raise RuntimeError(f'{tray.name} {bay.name} already contains {bay.installed_device.name}; cannot install {cassette.name}.')
        bay.installed_device = cassette
        bay.full_clean()
        bay.save()
        counters['cassettes_installed_in_tray_bays'] += 1


def row_sort_key(row):
    return (int(row['ru_top']), row['source_cell'])


def main():
    rows = read_shuffle_rows()
    rows_by_slot = defaultdict(list)
    for row in rows:
        rows_by_slot[row['physical_slot']].append(row)

    counters = Counter()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    box_role = DeviceRole.objects.get(slug=BOX_ROLE_SLUG)
    tray_role = upsert_role(TRAY_ROLE_SLUG, 'Shuffle Tray', '4dd0e1', 'Passive tray containing up to six 2x2 shuffle cassettes.', counters)
    cassette_role = DeviceRole.objects.get(slug=CASSETTE_ROLE_SLUG)
    box_type, tray_type, cassette_type = upsert_catalog(counters)
    racks = rack_by_slot()
    row_tags = {tag.slug[len(ROW_ID_TAG_PREFIX):].upper(): tag for tag in Tag.objects.filter(slug__startswith=ROW_ID_TAG_PREFIX)}

    with transaction.atomic():
        for slot, slot_rows in sorted(rows_by_slot.items()):
            rack = racks.get(slot)
            if rack is None:
                raise RuntimeError(f'No GS001 rack found for shuffle slot {slot}.')

            ordered_rows = sorted(slot_rows, key=row_sort_key)
            cassettes = []
            for row in ordered_rows:
                cassette = Device.objects.get(site=rack.site, name=device_name(row))
                if cassette.device_type_id != cassette_type.pk:
                    raise RuntimeError(f'{cassette.name} is {cassette.device_type.slug}; expected {CASSETTE_TYPE_SLUG}.')
                if cassette.role_id != cassette_role.pk:
                    cassette.role = cassette_role
                    cassette.full_clean()
                    cassette.save()
                    counters['cassette_roles_corrected'] += 1
                cassettes.append(cassette)

            box = upsert_box(slot, rack, len(cassettes), box_type, box_role, tenant, counters)
            required_tray_count = (len(cassettes) + 5) // 6
            if required_tray_count > 3:
                raise RuntimeError(f'{slot} needs {required_tray_count} trays for {len(cassettes)} cassettes; box capacity is 3 trays.')

            row_tag = row_tags.get(slot)
            box_tags = ([row_tag] if row_tag else []) + child_su_tags(cassettes)
            replace_tags(box, box_tags)

            for tray_number in range(1, 4):
                tray_cassettes = cassettes[(tray_number - 1) * 6:tray_number * 6]
                tray = upsert_tray(slot, box, tray_number, len(tray_cassettes), tray_type, tray_role, tenant, counters)
                tray_tags = ([row_tag] if row_tag else []) + child_su_tags(tray_cassettes)
                replace_tags(tray, tray_tags)
                for cassette_index, cassette in enumerate(tray_cassettes, start=1):
                    install_cassette(cassette, tray, cassette_index, counters)

    print('Madison shuffle hierarchy seeding complete.')
    print(f'shuffle_slots={len(rows_by_slot)}')
    print(f'shuffle_cassette_rows={len(rows)}')
    print(f'expected_shuffle_boxes={len(rows_by_slot)}')
    print(f'expected_fixed_trays={len(rows_by_slot) * 3}')
    print(f'expected_populated_trays={sum(1 for slot_rows in rows_by_slot.values() for index in range(0, 18, 6) if slot_rows[index:index + 6])}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'shuffle_boxes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=BOX_TYPE_SLUG).count()}')
    print(f'shuffle_trays={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=TRAY_TYPE_SLUG).count()}')
    print(f'shuffle_cassettes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassettes_parented={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG, parent_bay__isnull=False).count()}')
    print(f'shuffle_cassettes_directly_racked={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG, rack__isnull=False).count()}')


main()
