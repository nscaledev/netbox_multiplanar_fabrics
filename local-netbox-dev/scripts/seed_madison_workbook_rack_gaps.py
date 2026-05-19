from __future__ import annotations

from collections import Counter

from django.db import transaction

from dcim.models import Location, Rack, RackRole, Site
from extras.models import Tag
from tenancy.models import Tenant


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'

SOURCE_NOTE = (
    'Created from workbook-authoritative NC SU Mapping because this rack slot is present in '
    'META US NC Data Hall Layout 18k GB300 v2.0_5.01.2026 but absent from the imported '
    'electrical rack/circuit model. Electrical feeds/circuits are pending updated electrical design.'
)

SU_TAG_COLORS = {
    17: '1f77b4',
    18: 'ff7f0e',
    35: '2ca02c',
    36: 'd62728',
    37: '9467bd',
}

WORKBOOK_ONLY_BE_RACKS = [
    {
        'name': 'B11',
        'workbook_cell': 'M8',
        'location': 'Data Hall 2',
        'workbook_row_note': 'WestHall (Building)1 Row B',
        'su': 17,
        'role_slug': 'madison-t1-ew-plane-1-2',
    },
    {
        'name': 'B12',
        'workbook_cell': 'N8',
        'location': 'Data Hall 2',
        'workbook_row_note': 'WestHall (Building)1 Row B',
        'su': 17,
        'role_slug': 'madison-t1-ew-plane-3-4',
    },
    {
        'name': 'F11',
        'workbook_cell': 'AO8',
        'location': 'Data Hall 2',
        'workbook_row_note': 'WestHall (Building)1 Row F',
        'su': 18,
        'role_slug': 'madison-t1-ew-plane-1-2',
    },
    {
        'name': 'F12',
        'workbook_cell': 'AP8',
        'location': 'Data Hall 2',
        'workbook_row_note': 'WestHall (Building)1 Row F',
        'su': 18,
        'role_slug': 'madison-t1-ew-plane-3-4',
    },
    {
        'name': 'V11',
        'workbook_cell': 'DM8',
        'location': 'Data Hall 1',
        'workbook_row_note': 'EastHall Row V',
        'su': 35,
        'role_slug': 'madison-t1-ew-plane-1-2',
    },
    {
        'name': 'V12',
        'workbook_cell': 'DN8',
        'location': 'Data Hall 1',
        'workbook_row_note': 'EastHall Row V',
        'su': 35,
        'role_slug': 'madison-t1-ew-plane-3-4',
    },
    {
        'name': 'R11',
        'workbook_cell': 'CK8',
        'location': 'Data Hall 1',
        'workbook_row_note': 'EastHall Row R',
        'su': 36,
        'role_slug': 'madison-t1-ew-plane-1-2',
    },
    {
        'name': 'R12',
        'workbook_cell': 'CL8',
        'location': 'Data Hall 1',
        'workbook_row_note': 'EastHall Row R',
        'su': 36,
        'role_slug': 'madison-t1-ew-plane-3-4',
    },
    {
        'name': 'Q11',
        'workbook_cell': 'CK4',
        'location': 'Data Hall 1',
        'workbook_row_note': 'EastHall Row Q',
        'su': 37,
        'role_slug': 'madison-t1-ew-plane-1-2',
    },
    {
        'name': 'Q12',
        'workbook_cell': 'CL4',
        'location': 'Data Hall 1',
        'workbook_row_note': 'EastHall Row Q',
        'su': 37,
        'role_slug': 'madison-t1-ew-plane-3-4',
    },
]


def get_su_tag(number):
    tag, _ = Tag.objects.update_or_create(
        slug=f'nv_su_{number}',
        defaults={
            'name': f'nv_su_{number}',
            'color': SU_TAG_COLORS[number],
            'description': f'NVIDIA Scalable Unit {number}. Workbook-authoritative SU tag.',
        },
    )
    tag.full_clean()
    tag.save()
    return tag


def rack_comments(entry):
    return (
        f'{SOURCE_NOTE}\n\n'
        f'Workbook physical slot: {entry["name"]}\n'
        f'Workbook source cell: NC SU Mapping!{entry["workbook_cell"]}\n'
        f'Workbook label: SU{entry["su"]} BE - Leaf\n'
        f'Workbook row note: {entry["workbook_row_note"]}\n'
        'Rack dimensions from workbook: 750x1200mm.'
    )


def main():
    site = Site.objects.get(slug=MAD_SITE_SLUG)
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    roles_by_slug = {role.slug: role for role in RackRole.objects.all()}
    locations_by_name = {location.name: location for location in Location.objects.filter(site=site)}
    tags_by_su = {number: get_su_tag(number) for number in SU_TAG_COLORS}

    missing_roles = sorted({entry['role_slug'] for entry in WORKBOOK_ONLY_BE_RACKS if entry['role_slug'] not in roles_by_slug})
    if missing_roles:
        raise RuntimeError(f'Missing rack roles. Run seed_madison_device_definitions.py first: {missing_roles}')

    missing_locations = sorted({entry['location'] for entry in WORKBOOK_ONLY_BE_RACKS if entry['location'] not in locations_by_name})
    if missing_locations:
        raise RuntimeError(f'Missing MAD-1 locations: {missing_locations}')

    counters = Counter()
    with transaction.atomic():
        for entry in WORKBOOK_ONLY_BE_RACKS:
            defaults = {
                'tenant': tenant,
                'location': locations_by_name[entry['location']],
                'status': PLANNED_STATUS,
                'role': roles_by_slug[entry['role_slug']],
                'u_height': 48,
                'width': 19,
                'outer_width': 750,
                'outer_depth': 1200,
                'outer_unit': 'mm',
                'comments': rack_comments(entry),
            }
            rack, created = Rack.objects.update_or_create(
                site=site,
                name=entry['name'],
                defaults=defaults,
            )
            counters['racks_created' if created else 'racks_updated'] += 1

            desired_tag = tags_by_su[entry['su']]
            for existing_tag in rack.tags.filter(slug__startswith='nv_su_').exclude(pk=desired_tag.pk):
                rack.tags.remove(existing_tag)
                counters['stale_su_tags_removed'] += 1
            if not rack.tags.filter(pk=desired_tag.pk).exists():
                rack.tags.add(desired_tag)
                counters['su_tags_added'] += 1

            rack.full_clean()
            rack.save()
            counters[f'su_{entry["su"]}_racks'] += 1

    print('Madison workbook rack-gap seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'total_madison_racks={Rack.objects.filter(site=site).count()}')
    print(f'workbook_gap_racks={Rack.objects.filter(site=site, name__in=[entry["name"] for entry in WORKBOOK_ONLY_BE_RACKS]).count()}')


main()
