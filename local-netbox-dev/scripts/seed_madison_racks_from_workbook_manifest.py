from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from django.db import transaction
from django.utils.text import slugify

from dcim.models import Location, Rack, RackRole, Site
from extras.models import Tag
from tenancy.models import Tenant


MAD_SITE_SLUG = 'gs001'
MAD_SITE_NAME = 'GS001'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
ROW_ID_TAG_PREFIX = 'nscale-row-id-'
SU_TAG_PREFIX = 'nv_su_'

RACK_MANIFEST_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_workbook_rack_manifest.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_workbook_rack_manifest.csv'),
]

ROLE_SLUG_MAP = {
    'nvl72-gb300': 'nvl72_poweredgexe9712',
}

TAG_COLORS = [
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


def manifest_path() -> Path:
    for path in RACK_MANIFEST_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison rack manifest not found in: {RACK_MANIFEST_PATHS}')


def read_rows() -> list[dict[str, str]]:
    with manifest_path().open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if row['role_slug'] != 'empty-or-spacer']


def get_site() -> Site:
    site, _ = Site.objects.update_or_create(
        slug=MAD_SITE_SLUG,
        defaults={
            'name': MAD_SITE_NAME,
            'status': 'active',
            'description': 'GS001 Madison, NC data center staging site.',
        },
    )
    site.full_clean()
    site.save()
    return site


def get_location(site: Site, name: str) -> Location:
    location, _ = Location.objects.update_or_create(
        site=site,
        slug=slugify(name),
        defaults={
            'name': name,
            'status': 'active',
            'description': 'Workbook-derived Madison data hall.',
        },
    )
    location.full_clean()
    location.save()
    return location


def tag_color(index: int) -> str:
    return TAG_COLORS[(index - 1) % len(TAG_COLORS)]


def get_row_id_tag(slot: str) -> Tag:
    tag, _ = Tag.objects.update_or_create(
        slug=f'{ROW_ID_TAG_PREFIX}{slot.lower()}',
        defaults={
            'name': f'{ROW_ID_TAG_PREFIX}{slot}',
            'color': '607d8b',
            'description': f'Nscale Madison workbook row-id coordinate {slot}.',
        },
    )
    tag.full_clean()
    tag.save()
    return tag


def get_su_tag(number: int) -> Tag:
    tag, _ = Tag.objects.update_or_create(
        slug=f'{SU_TAG_PREFIX}{number}',
        defaults={
            'name': f'{SU_TAG_PREFIX}{number}',
            'color': tag_color(number),
            'description': f'NVIDIA Scalable Unit {number}. Workbook-authoritative SU tag.',
        },
    )
    tag.full_clean()
    tag.save()
    return tag


def rack_dimensions(row: dict[str, str], role_slug: str) -> tuple[int, int]:
    if role_slug == 'nvl72_poweredgexe9712':
        return 750, 1200
    dimensions = row.get('dimensions') or ''
    if 'x' in dimensions:
        width, depth = dimensions.split('x', 1)
        return int(width), int(depth)
    return 750, 1200


def rack_comments(row: dict[str, str], role_slug: str) -> str:
    return f"""\
<!-- madison-workbook-rack-underlay:start -->
Madison rack created from workbook rack manifest.
- physical_slot: {row['physical_slot']}
- source_cell: {row['workbook_cell']}
- source_label: {row['source_label']}
- source_role_slug: {row['role_slug']}
- modeled_role_slug: {role_slug}
- scalable_units: {row['scalable_units']}
- dimensions_source: {row['dimensions']}
<!-- madison-workbook-rack-underlay:end -->"""


def main() -> None:
    rows = read_rows()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    roles_by_slug = {role.slug: role for role in RackRole.objects.all()}
    missing_roles = sorted(
        {
            ROLE_SLUG_MAP.get(row['role_slug'], row['role_slug'])
            for row in rows
            if ROLE_SLUG_MAP.get(row['role_slug'], row['role_slug']) not in roles_by_slug
        }
    )
    if missing_roles:
        raise RuntimeError(f'Missing rack roles. Run seed_madison_device_definitions.py first: {missing_roles}')

    counters = Counter()
    with transaction.atomic():
        site = get_site()
        locations = {name: get_location(site, name) for name in sorted({row['location'] for row in rows})}

        for row in rows:
            slot = row['physical_slot']
            role_slug = ROLE_SLUG_MAP.get(row['role_slug'], row['role_slug'])
            outer_width, outer_depth = rack_dimensions(row, role_slug)
            rack, created = Rack.objects.update_or_create(
                site=site,
                name=slot,
                defaults={
                    'tenant': tenant,
                    'location': locations[row['location']],
                    'status': PLANNED_STATUS,
                    'role': roles_by_slug[role_slug],
                    'u_height': 48,
                    'width': 19,
                    'outer_width': outer_width,
                    'outer_depth': outer_depth,
                    'outer_unit': 'mm',
                    'comments': rack_comments(row, role_slug),
                },
            )
            counters['racks_created' if created else 'racks_updated'] += 1

            row_tag = get_row_id_tag(slot)
            if not rack.tags.filter(pk=row_tag.pk).exists():
                rack.tags.add(row_tag)
                counters['row_tags_added'] += 1

            for tag_slug in re.split(r'[,|]', row.get('rack_level_su_tags') or ''):
                tag_slug = tag_slug.strip()
                if not tag_slug.startswith(SU_TAG_PREFIX):
                    continue
                su_number = int(tag_slug[len(SU_TAG_PREFIX):])
                su_tag = get_su_tag(su_number)
                if not rack.tags.filter(pk=su_tag.pk).exists():
                    rack.tags.add(su_tag)
                    counters['su_tags_added'] += 1

            rack.full_clean()
            rack.save()

    print('Madison workbook rack underlay seed complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'manifest_rack_rows={len(rows)}')
    print(f'total_madison_racks={Rack.objects.filter(site__slug=MAD_SITE_SLUG).count()}')
    print(f'total_madison_locations={Location.objects.filter(site__slug=MAD_SITE_SLUG).count()}')


main()
