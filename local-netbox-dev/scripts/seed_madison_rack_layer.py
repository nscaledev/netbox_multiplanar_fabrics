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
SU_TAG_PREFIX = 'nv_su_'

TAG_COLORS = {
    1: '1f77b4',
    2: 'ff7f0e',
    3: '2ca02c',
    4: 'd62728',
    5: '9467bd',
    6: '8c564b',
    7: 'e377c2',
    8: '17becf',
}

RACK_NAME_PATTERN = re.compile(
    r'^(?P<family>GB300|BE|FE|NW)-P(?P<source_group>\d+[AB]?)-R(?P<row>\d+)-C(?P<cabinet>\d+)$'
)


def get_su_tag(number):
    tag, _ = Tag.objects.update_or_create(
        slug=f'{SU_TAG_PREFIX}{number}',
        defaults={
            'name': f'{SU_TAG_PREFIX}{number}',
            'color': TAG_COLORS[number],
            'description': (
                f'NVIDIA Scalable Unit {number}. Applied only to objects whose whole modeled '
                f'membership belongs to this SU; source worksheet grouping token is P{number}.'
            ),
        },
    )
    tag.full_clean()
    tag.save()
    return tag


def rack_role_for(rack_name):
    match = RACK_NAME_PATTERN.match(rack_name)
    if not match:
        return None

    family = match.group('family')
    row = int(match.group('row'))

    if family == 'GB300':
        return 'nvl72_poweredgexe9712'
    if family == 'BE':
        # Source workbook describes BE Leaf Rack 1 and BE Leaf Rack 2 as the
        # paired E/W leaf rack layouts for an SU. Existing rack names only
        # expose R1/R2, so keep this as a layout classification, not a plane
        # membership assertion on individual switches.
        return 'madison-t1-ew-plane-1-2' if row == 1 else 'madison-t1-ew-plane-3-4'
    if family == 'FE':
        return 'madison-t1-t2-ns'
    if family == 'NW':
        return 'network'
    return None


def rack_su_number_for_tagging(rack_name):
    match = RACK_NAME_PATTERN.match(rack_name)
    if not match:
        return None

    family = match.group('family')
    source_group = match.group('source_group')
    if not source_group.isdigit():
        return None

    su_number = int(source_group)
    if su_number not in range(1, 9):
        return None

    # Round 1 rack-level SU tags are intentionally conservative:
    # - GB300/NVL72 racks are single-SU racks.
    # - BE leaf racks are modeled as SU-local leaf racks in the source workbook.
    # - FE and NW racks are not tagged at the rack level because their rack
    #   layouts include or may include management/shared network elements.
    if family in {'GB300', 'BE'}:
        return su_number
    return None


def set_rack_su_tag(rack, su_tag_by_number, counters):
    desired_su_number = rack_su_number_for_tagging(rack.name)
    current_su_tags = list(rack.tags.filter(slug__startswith=SU_TAG_PREFIX))
    desired_tag = su_tag_by_number.get(desired_su_number) if desired_su_number else None

    changed = False
    for tag in current_su_tags:
        if tag != desired_tag:
            rack.tags.remove(tag)
            counters['rack_su_tags_removed'] += 1
            changed = True
    if desired_tag and desired_tag not in current_su_tags:
        rack.tags.add(desired_tag)
        counters['rack_su_tags_added'] += 1
        changed = True
    if desired_su_number:
        counters[f'racks_tagged_su_{desired_su_number}'] += 1
    else:
        counters['racks_not_su_tagged'] += 1
    return changed


def main():
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    roles_by_slug = {role.slug: role for role in RackRole.objects.all()}
    missing_roles = sorted({role for role in (rack_role_for(name) for name in Rack.objects.filter(site__slug=MAD_SITE_SLUG).values_list('name', flat=True)) if role and role not in roles_by_slug})
    if missing_roles:
        raise RuntimeError(f'Missing rack roles. Run seed_madison_device_definitions.py first: {missing_roles}')

    counters = Counter()
    with transaction.atomic():
        su_tag_by_number = {number: get_su_tag(number) for number in range(1, 9)}
        counters['su_tags_available'] = len(su_tag_by_number)

        for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG).order_by('name'):
            changed = False
            role_slug = rack_role_for(rack.name)
            role = roles_by_slug.get(role_slug) if role_slug else None

            if rack.tenant_id != tenant.pk:
                rack.tenant = tenant
                counters['racks_tenant_updated'] += 1
                changed = True
            if rack.status != PLANNED_STATUS:
                rack.status = PLANNED_STATUS
                counters['racks_status_updated'] += 1
                changed = True
            if role and rack.role_id != role.pk:
                rack.role = role
                counters[f'rack_roles_set_{role.slug}'] += 1
                changed = True

            if changed:
                rack.full_clean()
                rack.save()
            else:
                counters['racks_core_unchanged'] += 1

            set_rack_su_tag(rack, su_tag_by_number, counters)

    print('Madison rack-layer seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'total_madison_racks={Rack.objects.filter(site__slug=MAD_SITE_SLUG).count()}')
    print(f'madison_racks_nscale={Rack.objects.filter(site__slug=MAD_SITE_SLUG, tenant=tenant).count()}')
    print(f'madison_racks_planned={Rack.objects.filter(site__slug=MAD_SITE_SLUG, status=PLANNED_STATUS).count()}')
    print(f'madison_racks_with_role={Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__isnull=False).count()}')
    print(f'madison_racks_with_su_tag={Rack.objects.filter(site__slug=MAD_SITE_SLUG, tags__slug__startswith=SU_TAG_PREFIX).distinct().count()}')


main()
