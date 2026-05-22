from __future__ import annotations

import re
from collections import Counter

from django.db import transaction

from dcim.models import Rack, RackRole
from extras.models import Tag
from tenancy.models import Tenant


MAD_SITE_SLUG = 'gs001'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
SU_TAG_PREFIX = 'nv_su_'

SU_TAG_COLORS = [
    '1f77b4',
    'ff7f0e',
    '2ca02c',
    'd62728',
    '9467bd',
    '8c564b',
    'e377c2',
    '17becf',
    'bcbd22',
    '7f7f7f',
]

COMPUTE_ROW_CONFIG = {
    'A': {'left_edge': 17, 'left': 1, 'right': 2, 'right_edge': 18, 'fe': {13: [1, 2], 14: [1, 2]}},
    'B': {'left_edge': 17, 'left': 5, 'right': 6, 'right_edge': 17, 'extra': 17, 'fe': {13: [5, 6], 14: [5, 6]}},
    'C': {'left_edge': 17, 'left': 9, 'right': 10, 'right_edge': 17, 'fe': {13: [9, 10], 14: [9, 10]}},
    'D': {'left_edge': 17, 'left': 13, 'right': 14, 'right_edge': 17, 'fe': {13: [13, 14], 14: [13, 14]}},
    'E': {'left_edge': 18, 'left': 3, 'right': 4, 'right_edge': 37, 'fe': {13: [3, 4], 14: [3, 4]}},
    'F': {'left_edge': 18, 'left': 7, 'right': 8, 'right_edge': 37, 'extra': 18, 'fe': {13: [7, 8], 14: [7, 8]}},
    'G': {'left_edge': 18, 'left': 11, 'right': 12, 'right_edge': 18, 'fe': {13: [11, 12], 14: [11, 12]}},
    'H': {'left_edge': 18, 'left': 15, 'right': 16, 'right_edge': 18, 'fe': {13: [15, 16], 14: [15, 16]}},
    'Q': {'left_edge': 37, 'left': 22, 'right': 21, 'right_edge': 36, 'extra': 37, 'fe': {13: [21, 22], 14: [21, 22]}},
    'R': {'left_edge': 37, 'left': 26, 'right': 25, 'right_edge': 36, 'extra': 36, 'fe': {13: [25, 26], 14: [23, 24]}},
    'S': {'left_edge': 36, 'left': 30, 'right': 29, 'right_edge': 36, 'fe': {13: [29, 30], 14: [29, 30]}},
    'T': {'left_edge': 36, 'left': 34, 'right': 33, 'right_edge': 36, 'fe': {13: [33, 34], 14: [33, 34]}},
    'U': {'left_edge': 35, 'left': 20, 'right': 19, 'right_edge': 35, 'fe': {13: [19, 20], 14: [19, 20]}},
    'V': {'left_edge': 35, 'left': 24, 'right': 23, 'right_edge': 35, 'extra': 35, 'fe': {13: [23, 24], 14: [23, 24]}},
    'W': {'left_edge': 35, 'left': 28, 'right': 27, 'right_edge': 35, 'fe': {13: [27, 28], 14: [27, 28]}},
    'X': {'left_edge': 36, 'left': 32, 'right': 31, 'right_edge': 35, 'fe': {13: [31, 32], 14: [31, 32]}},
}

NETWORK_ROWS = {
    'I': {range(1, 8): ('madison-be-spine-plane-1', [])},
    'J': {range(1, 4): ('madison-t1-t2-storage', []), range(4, 8): ('madison-be-spine-plane-1', [])},
    'K': {range(1, 8): ('madison-be-spine-plane-2', [])},
    'L': {range(1, 4): ('madison-t1-t2-storage', []), range(4, 8): ('madison-be-spine-plane-2', [])},
    'M': {range(1, 2): ('madison-fe-core-edge', []), range(2, 4): ('madison-control', []), range(4, 8): ('madison-be-spine-plane-3', [])},
    'N': {range(1, 8): ('madison-be-spine-plane-3', [])},
    'O': {range(1, 2): ('madison-fe-core-edge', []), range(2, 4): ('madison-control', []), range(4, 8): ('madison-be-spine-plane-4', [])},
    'P': {range(1, 8): ('madison-be-spine-plane-4', [])},
}

SLOT_PATTERN = re.compile(r'^(?P<row>[A-X])(?P<number>[1-9]|1\d|2[0-6])$')


def get_or_update_su_tag(number):
    tag, _ = Tag.objects.update_or_create(
        slug=f'{SU_TAG_PREFIX}{number}',
        defaults={
            'name': f'{SU_TAG_PREFIX}{number}',
            'color': SU_TAG_COLORS[(number - 1) % len(SU_TAG_COLORS)],
            'description': f'NVIDIA Scalable Unit {number}. Workbook-authoritative SU tag.',
        },
    )
    tag.full_clean()
    tag.save()
    return tag


def desired_from_slot(slot):
    match = SLOT_PATTERN.fullmatch(slot)
    if not match:
        return None

    row = match.group('row')
    number = int(match.group('number'))
    config = COMPUTE_ROW_CONFIG.get(row)

    if config:
        if number == 1:
            return 'nvl72_poweredgexe9712', [config['left_edge']]
        if 2 <= number <= 8:
            return 'nvl72_poweredgexe9712', [config['left']]
        if number == 9:
            return 'madison-t1-ew-plane-1-2', [config['left']]
        if number == 10:
            return 'madison-t1-ew-plane-3-4', [config['left']]
        if number == 11 and config.get('extra'):
            return 'madison-t1-ew-plane-1-2', [config['extra']]
        if number == 12 and config.get('extra'):
            return 'madison-t1-ew-plane-3-4', [config['extra']]
        if number in config['fe']:
            return 'madison-t1-t2-ns', config['fe'][number]
        if number == 17:
            return 'madison-t1-ew-plane-1-2', [config['right']]
        if number == 18:
            return 'madison-t1-ew-plane-3-4', [config['right']]
        if 19 <= number <= 25:
            return 'nvl72_poweredgexe9712', [config['right']]
        if number == 26:
            return 'nvl72_poweredgexe9712', [config['right_edge']]
        return None

    network_config = NETWORK_ROWS.get(row)
    if network_config:
        for slot_range, desired in network_config.items():
            if number in slot_range:
                return desired

    return None


def row_id_slot_for_rack(rack):
    row_id_tags = list(rack.tags.filter(slug__startswith=ROW_ID_TAG_PREFIX))
    if len(row_id_tags) != 1:
        raise RuntimeError(f'Rack {rack.name} has {len(row_id_tags)} row-id tags; expected exactly one.')
    return row_id_tags[0].slug[len(ROW_ID_TAG_PREFIX):].upper()


def set_su_tags(rack, desired_tags, counters):
    current_tags = list(rack.tags.filter(slug__startswith=SU_TAG_PREFIX))
    desired_tag_ids = {tag.pk for tag in desired_tags}
    current_tag_ids = {tag.pk for tag in current_tags}

    for tag in current_tags:
        if tag.pk not in desired_tag_ids:
            rack.tags.remove(tag)
            counters['stale_su_tags_removed'] += 1

    for tag in desired_tags:
        if tag.pk not in current_tag_ids:
            rack.tags.add(tag)
            counters['su_tags_added'] += 1

    if not desired_tags:
        counters['racks_without_su_tags'] += 1
    elif len(desired_tags) > 1:
        counters['racks_with_multiple_su_tags'] += 1
    else:
        counters['racks_with_single_su_tag'] += 1


def main():
    roles_by_slug = {role.slug: role for role in RackRole.objects.all()}
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    missing_roles = sorted(
        {
            desired[0]
            for slot in (f'{row}{number}' for row in 'ABCDEFGHIJKLMNOPQRSTUVWX' for number in range(1, 27))
            if (desired := desired_from_slot(slot)) and desired[0] not in roles_by_slug
        }
    )
    if missing_roles:
        raise RuntimeError(f'Missing rack roles. Run seed_madison_device_definitions.py first: {missing_roles}')

    su_tags_by_number = {number: get_or_update_su_tag(number) for number in range(1, 38)}
    counters = Counter()
    with transaction.atomic():
        for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).prefetch_related('tags').order_by('name'):
            slot = row_id_slot_for_rack(rack)
            desired = desired_from_slot(slot)
            if not desired:
                raise RuntimeError(f'No workbook reconciliation rule for rack {rack.name} with row-id {slot}.')

            role_slug, scalable_units = desired
            role = roles_by_slug[role_slug]
            changed = False

            if rack.tenant_id != tenant.pk:
                rack.tenant = tenant
                counters['rack_tenant_set_nscale'] += 1
                changed = True

            if rack.role_id != role.pk:
                rack.role = role
                counters[f'role_set_{role_slug}'] += 1
                changed = True
            else:
                counters[f'role_already_{role_slug}'] += 1

            if rack.u_height != 48:
                rack.u_height = 48
                counters['rack_u_height_set_48'] += 1
                changed = True

            if rack.status != PLANNED_STATUS:
                rack.status = PLANNED_STATUS
                counters['rack_status_set_planned'] += 1
                changed = True

            if changed:
                rack.full_clean()
                rack.save()
            else:
                counters['rack_core_unchanged'] += 1

            desired_su_tags = [su_tags_by_number[number] for number in scalable_units]
            set_su_tags(rack, desired_su_tags, counters)
            counters['racks_reconciled'] += 1
            counters[f'row_{slot[0]}_racks'] += 1

    print('Madison rack reconciliation complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'total_madison_racks={Rack.objects.filter(site__slug=MAD_SITE_SLUG).count()}')
    print(f'madison_48u_racks={Rack.objects.filter(site__slug=MAD_SITE_SLUG, u_height=48).count()}')


main()
