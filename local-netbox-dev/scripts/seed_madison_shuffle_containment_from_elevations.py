from __future__ import annotations

import csv
import os
from collections import Counter
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
    Rack,
    RearPort,
    RearPortTemplate,
)
from extras.models import Tag
from tenancy.models import Tenant


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'

BOX_ROLE_SLUG = 'shuffle-box'
CASSETTE_ROLE_SLUG = 'shuffle-cassette'
BOX_TYPE_SLUG = 'shuffle-box-3tray-18cassette'
CASSETTE_TYPE_SLUG = 'shuffle-cassette-2x2-mpo'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
SU_TAG_PREFIX = 'nv_su_'

SOURCE_MARKER = 'madison_shuffle_flattened_containment_v1'
SOURCE_MARKER_BEGIN = '<!-- madison-shuffle-flattened-containment:start -->'
SOURCE_MARKER_END = '<!-- madison-shuffle-flattened-containment:end -->'

MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_shuffle_box_placement_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_shuffle_box_placement_manifest.csv'),
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_SHUFFLE_APPLY') == '1'


def manifest_path() -> Path:
    for path in MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison shuffle placement manifest not found in: {MANIFEST_PATHS}')


def read_manifest() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError('Madison shuffle placement manifest is empty.')
    return rows


def is_first_pass_row(row: dict[str, str]) -> bool:
    return (
        row['populated_cassettes'] in {'14', '18'}
        and row['nic_index_zero'] != ''
        and row['side'] in {'A', 'B'}
        and row['rack_role_slug'] in {'madison-t1-ew-plane-1-2', 'madison-t1-ew-plane-3-4'}
    )


def target_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected = [row for row in rows if is_first_pass_row(row)]
    duplicate_names = [name for name, count in Counter(row['box_name'] for row in selected).items() if count > 1]
    if duplicate_names:
        raise RuntimeError(f'Duplicate shuffle box names in target set: {duplicate_names[:5]}')
    invalid = [row for row in selected if int(row['populated_cassettes']) > int(row['max_cassettes'])]
    if invalid:
        raise RuntimeError(f'Shuffle placement exceeds cassette capacity: {invalid[0]}')
    non_1u = [row for row in selected if row['height_u'] != '1' or row['ru_top'] != row['ru_bottom']]
    if non_1u:
        raise RuntimeError(f'Found non-1RU target shuffle placement: {non_1u[0]}')
    return selected


def rack_by_slot() -> dict[str, Rack]:
    racks = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').select_related('site', 'location'):
        row_tags = [tag.slug for tag in rack.tags.all() if tag.slug.startswith(ROW_ID_TAG_PREFIX)]
        for tag_slug in row_tags:
            racks[tag_slug[len(ROW_ID_TAG_PREFIX):].upper()] = rack
    return racks


def existing_tag(slug: str) -> Tag | None:
    return Tag.objects.filter(slug=slug).first()


def row_id_tag(row: dict[str, str]) -> Tag | None:
    return existing_tag(f'{ROW_ID_TAG_PREFIX}{row["physical_slot"].lower()}')


def su_tag(row: dict[str, str]) -> Tag | None:
    source = row.get('rack_source_label') or ''
    if not source.startswith('SU'):
        return None
    number = ''
    for char in source[2:]:
        if not char.isdigit():
            break
        number += char
    if not number:
        return None
    return existing_tag(f'{SU_TAG_PREFIX}{number}')


def cassette_position(ordinal: int) -> tuple[int, int, str]:
    tray = ((ordinal - 1) // 6) + 1
    slot = ((ordinal - 1) % 6) + 1
    return tray, slot, f'cassette-{tray}.{slot}'


def cassette_name(box_name: str, ordinal: int) -> str:
    tray, slot, _ = cassette_position(ordinal)
    return f'{box_name}-cassette-{tray}.{slot}'


def marker_comments(row: dict[str, str], modeled_role: str) -> str:
    return f"""\
{SOURCE_MARKER_BEGIN}
Madison shuffle containment from row-elevation workbook labels.
- source_cell: {row['row_sheet']}!{row['source_cell']}
- source_label: {row['source_label']}
- physical_slot: {row['physical_slot']}
- ru: {row['ru_top']}
- modeled_role: {modeled_role}
- populated_cassettes_in_box: {row['populated_cassettes']}
- logical_shuffle_box: {row['logical_shuffle_box']}
- nic_index_zero: {row['nic_index_zero']}
- side: {row['side']}
{SOURCE_MARKER_END}"""


def marker_context(row: dict[str, str], modeled_role: str, ordinal: int | None = None) -> dict:
    context = {
        SOURCE_MARKER: {
            'physical_slot': row['physical_slot'],
            'source_cell': f'{row["row_sheet"]}!{row["source_cell"]}',
            'source_label': row['source_label'],
            'ru': int(row['ru_top']),
            'modeled_role': modeled_role,
            'populated_cassettes': int(row['populated_cassettes']),
            'logical_shuffle_box': int(row['logical_shuffle_box']),
            'nic_index_zero': int(row['nic_index_zero']),
            'side': row['side'],
        }
    }
    if ordinal is not None:
        tray, slot, bay_name = cassette_position(ordinal)
        context[SOURCE_MARKER].update({
            'cassette_ordinal': ordinal,
            'tray_position': tray,
            'cassette_slot': slot,
            'parent_bay_name': bay_name,
        })
    return context


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
            bay.full_clean()
            bay.save()
        counters['device_bays_created' if created else 'device_bays_updated' if changed else 'device_bays_unchanged'] += 1


def ensure_cassette_ports(cassette: Device, counters: Counter) -> None:
    rear_templates = RearPortTemplate.objects.filter(device_type=cassette.device_type).order_by('name')
    rear_by_name = {}
    for template in rear_templates:
        rear, created = RearPort.objects.get_or_create(
            device=cassette,
            name=template.name,
            defaults={
                'type': template.type,
                'positions': template.positions,
                'description': template.description,
                'color': template.color,
            },
        )
        rear_by_name[rear.name] = rear
        counters['rear_ports_created' if created else 'rear_ports_unchanged'] += 1

    for template in FrontPortTemplate.objects.filter(device_type=cassette.device_type).select_related('rear_port').order_by('name'):
        rear = rear_by_name[template.rear_port.name]
        front, created = FrontPort.objects.get_or_create(
            device=cassette,
            name=template.name,
            defaults={
                'type': template.type,
                'rear_port': rear,
                'rear_port_position': template.rear_port_position,
                'description': template.description,
                'color': template.color,
            },
        )
        if not created and (front.rear_port_id != rear.pk or front.rear_port_position != template.rear_port_position):
            front.rear_port = rear
            front.rear_port_position = template.rear_port_position
            front.full_clean()
            front.save()
            counters['front_ports_updated'] += 1
        else:
            counters['front_ports_created' if created else 'front_ports_unchanged'] += 1


def apply_tags(device: Device, tags: list[Tag | None], counters: Counter) -> None:
    for tag in tags:
        if tag is not None and not device.tags.filter(pk=tag.pk).exists():
            device.tags.add(tag)
            counters['device_tags_added'] += 1


def dry_run_report(rows: list[dict[str, str]]) -> None:
    selected = target_rows(rows)
    skipped = [row for row in rows if row not in selected]
    print('Madison shuffle flattened containment dry run')
    print(f'total_manifest_rows={len(rows)}')
    print(f'target_leaf_nic_ab_boxes={len(selected)}')
    print(f'skipped_unclassified_or_non_leaf_shuffle_rows={len(skipped)}')
    print(f'target_populated_cassettes={sum(int(row["populated_cassettes"]) for row in selected)}')
    print('target_box_count_by_populated_cassettes=' + str(dict(sorted(Counter(row['populated_cassettes'] for row in selected).items()))))
    print('target_box_count_by_side=' + str(dict(sorted(Counter(row['side'] for row in selected).items()))))
    print('target_box_count_by_rack_role=' + str(dict(sorted(Counter(row['rack_role_slug'] for row in selected).items()))))
    print('skipped_count_by_populated_cassettes=' + str(dict(sorted(Counter(row['populated_cassettes'] for row in skipped).items()))))
    for row in selected[:8]:
        print(
            f'sample={row["box_name"]} slot={row["physical_slot"]} ru={row["ru_top"]} '
            f'populated={row["populated_cassettes"]} label="{row["source_label"]}"'
        )


def main() -> None:
    rows = read_manifest()
    selected = target_rows(rows)
    dry_run_report(rows)

    racks = rack_by_slot()
    missing_racks = sorted({row['physical_slot'] for row in selected if row['physical_slot'] not in racks})
    if missing_racks:
        raise RuntimeError(f'Missing racks for target shuffle placements: {missing_racks[:20]}')

    if not apply_enabled():
        print('apply=false; set MADISON_SHUFFLE_APPLY=1 to create/update NetBox objects.')
        return

    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    box_role = DeviceRole.objects.get(slug=BOX_ROLE_SLUG)
    cassette_role = DeviceRole.objects.get(slug=CASSETTE_ROLE_SLUG)
    box_type = DeviceType.objects.get(slug=BOX_TYPE_SLUG)
    cassette_type = DeviceType.objects.get(slug=CASSETTE_TYPE_SLUG)

    counters = Counter()
    with transaction.atomic():
        for row in selected:
            rack = racks[row['physical_slot']]
            box, box_created = Device.objects.update_or_create(
                site=rack.site,
                name=row['box_name'],
                defaults={
                    'device_type': box_type,
                    'role': box_role,
                    'tenant': tenant,
                    'location': rack.location,
                    'rack': rack,
                    'position': Decimal(row['ru_top']),
                    'face': 'front',
                    'status': PLANNED_STATUS,
                    'description': f'1RU shuffle box; {row["populated_cassettes"]} populated cassette positions from row-elevation label.',
                    'comments': marker_comments(row, 'shuffle-box'),
                    'local_context_data': marker_context(row, 'shuffle-box'),
                },
            )
            counters['shuffle_boxes_created' if box_created else 'shuffle_boxes_updated'] += 1
            ensure_device_bays(box, counters)
            common_tags = [row_id_tag(row), su_tag(row)]
            apply_tags(box, common_tags, counters)

            bay_by_name = {bay.name: bay for bay in DeviceBay.objects.filter(device=box)}
            for ordinal in range(1, int(row['populated_cassettes']) + 1):
                _, _, bay_name = cassette_position(ordinal)
                cassette, cassette_created = Device.objects.update_or_create(
                    site=rack.site,
                    name=cassette_name(row['box_name'], ordinal),
                    defaults={
                        'device_type': cassette_type,
                        'role': cassette_role,
                        'tenant': tenant,
                        'location': rack.location,
                        'status': PLANNED_STATUS,
                        'description': f'Cassette position {bay_name} in {row["box_name"]}.',
                        'comments': marker_comments(row, f'shuffle-cassette-{ordinal:02d}'),
                        'local_context_data': marker_context(row, 'shuffle-cassette', ordinal),
                    },
                )
                counters['shuffle_cassettes_created' if cassette_created else 'shuffle_cassettes_updated'] += 1
                ensure_cassette_ports(cassette, counters)
                apply_tags(cassette, common_tags, counters)

                bay = bay_by_name[bay_name]
                if bay.installed_device_id != cassette.pk:
                    bay.installed_device = cassette
                    bay.full_clean()
                    bay.save()
                    counters['cassettes_installed_in_box_bays'] += 1

    print('Madison shuffle flattened containment seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'shuffle_boxes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=BOX_TYPE_SLUG).count()}')
    print(f'shuffle_cassettes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassettes_parented={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG, parent_bay__isnull=False).count()}')
    print(f'shuffle_cassette_front_ports={FrontPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassette_rear_ports={RearPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=CASSETTE_TYPE_SLUG).count()}')


main()
