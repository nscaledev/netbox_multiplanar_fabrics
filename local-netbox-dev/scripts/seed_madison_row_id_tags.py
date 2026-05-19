from __future__ import annotations

import re
from collections import Counter

from django.db import transaction

from dcim.models import Rack
from extras.models import Tag


MAD_SITE_SLUG = 'mad-1'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
ROW_ID_TAG_COLOR = '607d8b'

# Workbook NC SU Mapping places GPU/NVL72 racks at slots 1-8 and 19-26.
GB300_SLOT_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 19, 20, 21, 22, 23, 24, 25, 26]
FE_SLOT_NUMBERS = [13, 14]

# Prior electrical-design import names grouped racks by a P-token. The P-token
# is only a source grouping; the workbook/blueprint row letter below is the
# row-id tag target.
COMPUTE_GROUP_ROWS = {
    1: ('Q', 'R'),
    2: ('U', 'V'),
    3: ('S', 'T'),
    4: ('W', 'X'),
    5: ('A', 'B'),
    6: ('E', 'F'),
    7: ('C', 'D'),
    8: ('G', 'H'),
}

NETWORK_GROUP_ROWS = {
    '1A': ('M', 'N'),
    '1B': ('O', 'P'),
    '2A': ('I', 'J'),
    '2B': ('K', 'L'),
}

# The old BE import has four racks per plane-role per P-token, while the
# workbook has two same-role BE leaf slots per physical row. Order them
# left-to-right within the first physical row, then left-to-right within the
# second physical row.
BE_SLOT_MAP = {
    1: {
        1: (0, 9),
        2: (0, 17),
        3: (1, 9),
        4: (1, 17),
    },
    2: {
        1: (0, 10),
        2: (0, 18),
        3: (1, 10),
        4: (1, 18),
    },
}

DIRECT_WORKBOOK_SLOT_PATTERN = re.compile(r'^[A-X](?:[1-9]|1\d|2[0-6])$')
COMPUTE_RACK_PATTERN = re.compile(
    r'^(?P<family>GB300|BE|FE)-P(?P<group>\d+)-R(?P<row>\d+)-C(?P<cabinet>\d+)$'
)
NETWORK_RACK_PATTERN = re.compile(
    r'^NW-P(?P<hall_section>[12][AB])-R(?P<row>\d+)-C(?P<cabinet>\d+)$'
)


def row_id_tag_name(slot):
    return f'{ROW_ID_TAG_PREFIX}{slot}'


def row_id_tag_slug(slot):
    return row_id_tag_name(slot).lower()


def get_row_id_tag(slot):
    tag, _ = Tag.objects.update_or_create(
        slug=row_id_tag_slug(slot),
        defaults={
            'name': row_id_tag_name(slot),
            'color': ROW_ID_TAG_COLOR,
            'description': f'Nscale Madison workbook row-id coordinate {slot}.',
        },
    )
    tag.full_clean()
    tag.save()
    return tag


def compute_slot_for_imported_rack(name):
    if DIRECT_WORKBOOK_SLOT_PATTERN.fullmatch(name):
        return name

    match = COMPUTE_RACK_PATTERN.fullmatch(name)
    if match:
        family = match.group('family')
        group = int(match.group('group'))
        row = int(match.group('row'))
        cabinet = int(match.group('cabinet'))
        physical_rows = COMPUTE_GROUP_ROWS.get(group)
        if not physical_rows:
            return None

        if family == 'GB300':
            if row not in (1, 2) or not 1 <= cabinet <= len(GB300_SLOT_NUMBERS):
                return None
            return f'{physical_rows[row - 1]}{GB300_SLOT_NUMBERS[cabinet - 1]}'

        if family == 'FE':
            if row not in (1, 2) or not 1 <= cabinet <= len(FE_SLOT_NUMBERS):
                return None
            return f'{physical_rows[row - 1]}{FE_SLOT_NUMBERS[cabinet - 1]}'

        if family == 'BE':
            plane_slot_map = BE_SLOT_MAP.get(row, {})
            row_index_and_slot = plane_slot_map.get(cabinet)
            if not row_index_and_slot:
                return None
            row_index, slot_number = row_index_and_slot
            return f'{physical_rows[row_index]}{slot_number}'

    match = NETWORK_RACK_PATTERN.fullmatch(name)
    if match:
        physical_rows = NETWORK_GROUP_ROWS.get(match.group('hall_section'))
        row = int(match.group('row'))
        cabinet = int(match.group('cabinet'))
        if not physical_rows or row not in (1, 2) or not 1 <= cabinet <= 7:
            return None
        return f'{physical_rows[row - 1]}{cabinet}'

    return None


def set_single_row_id_tag(rack, desired_tag, counters):
    current_tags = list(rack.tags.filter(slug__startswith=ROW_ID_TAG_PREFIX))
    changed = False
    for tag in current_tags:
        if tag != desired_tag:
            rack.tags.remove(tag)
            counters['stale_row_id_tags_removed'] += 1
            changed = True

    if desired_tag not in current_tags:
        rack.tags.add(desired_tag)
        counters['row_id_tags_added'] += 1
        changed = True
    else:
        counters['row_id_tags_already_correct'] += 1

    return changed


def main():
    racks = list(Rack.objects.filter(site__slug=MAD_SITE_SLUG).order_by('name'))
    counters = Counter()
    slot_by_rack_name = {}
    unmapped = []
    duplicate_slots = Counter()

    for rack in racks:
        slot = compute_slot_for_imported_rack(rack.name)
        if not slot:
            unmapped.append(rack.name)
            continue
        slot_by_rack_name[rack.name] = slot
        duplicate_slots[slot] += 1

    duplicates = sorted(slot for slot, count in duplicate_slots.items() if count > 1)
    if unmapped:
        raise RuntimeError(f'Could not derive row-id slot for racks: {unmapped}')
    if duplicates:
        raise RuntimeError(f'Derived duplicate row-id slots: {duplicates}')

    with transaction.atomic():
        for rack in racks:
            slot = slot_by_rack_name[rack.name]
            tag = get_row_id_tag(slot)
            set_single_row_id_tag(rack, tag, counters)
            counters['racks_tagged'] += 1
            counters[f'row_{slot[0]}_racks'] += 1

    print('Madison row-id tag seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'total_madison_racks={len(racks)}')
    print(f'unique_row_id_slots={len(set(slot_by_rack_name.values()))}')


main()
