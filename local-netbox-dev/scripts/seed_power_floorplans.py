from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from dcim.models import Location, Rack, Site
from netbox_power_plant.choices import (
    PlacementLabelModeChoices,
    PlacementScopeChoices,
    SpatialAnchorChoices,
    SpatialAxisOrientationChoices,
    SpatialConfidenceChoices,
    SpatialPlacementKindChoices,
)
from netbox_power_plant.models import (
    ElectricalNode,
    ElectricalNodePlacement,
    ElectricalSegment,
    PowerHandoffPoint,
    PowerSystem,
    SpatialFrame,
    SpatialPlacement,
)


MAD_SITE_SLUG = 'gs001'
MARKER = 'Generated local-dev power spatial placement from blueprint-derived NetBox naming/topology.'
BLUEPRINT_SOURCE = '/Users/mencken/Documents/madison-temp-design-input-docs/madison-physical-layout.svg'

# Coordinates below are in the SVG coordinate system from madison-physical-layout.svg.
# Data Hall 1 and Data Hall 2 spatial frames translate the blueprint origin to
# the upper-left corner of the corresponding NetBox Location coordinate space.
BLUEPRINT_LOCATION_ORIGINS = {
    'Data Hall 1': (6320, 2430),
    'Data Hall 2': (18, 2420),
}

LOCATION_SIZES = {
    'Utility Switchyard': (700, 420),
    'Primary Electrical Yard': (920, 520),
    'Mechanical Plant': (1080, 700),
    'Data Hall 1': (5930, 2450),
    'Data Hall 2': (5762, 2460),
}

SITE_OFFSETS = {
    'Utility Switchyard': (50, 50),
    'Primary Electrical Yard': (50, 540),
    'Mechanical Plant': (1040, 50),
    'Data Hall 2': (18, 2420),
    'Data Hall 1': (6320, 2430),
}

BLUEPRINT_SCALABLE_UNIT_BLOCKS = {
    1: ('Data Hall 1', 7370, {1: 2960, 2: 3260}),
    2: ('Data Hall 1', 9810, {1: 2960, 2: 3260}),
    3: ('Data Hall 1', 7370, {1: 4280, 2: 4580}),
    4: ('Data Hall 1', 9810, {1: 4280, 2: 4580}),
    5: ('Data Hall 2', 860, {1: 2960, 2: 3260}),
    6: ('Data Hall 2', 3320, {1: 2960, 2: 3260}),
    7: ('Data Hall 2', 860, {1: 4280, 2: 4580}),
    8: ('Data Hall 2', 3320, {1: 4280, 2: 4580}),
}

BLUEPRINT_NW_BLOCKS = {
    '1A': ('Data Hall 1', 6560, {1: 2960, 2: 3260}),
    '1B': ('Data Hall 1', 6560, {1: 4280, 2: 4580}),
    '2A': ('Data Hall 2', 5240, {1: 2960, 2: 3260}),
    '2B': ('Data Hall 2', 5240, {1: 4280, 2: 4580}),
}

BLUEPRINT_SLOT_WIDTH = 60
BLUEPRINT_SLOT_HEIGHT = 100
BLUEPRINT_GB300_LABELS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 17, 18, 19, 20]
BLUEPRINT_BE_LABELS = [21, 22, 23, 24]
BLUEPRINT_FE_LABELS = [25, 26]

KIND_STYLE = {
    'utility_service': ('source', '1f77b4', 60, 34, 10),
    'mv_switchgear': ('distribution', '9467bd', 46, 28, 20),
    'transformer': ('distribution', '8c564b', 42, 26, 30),
    'generator': ('source', '2ca02c', 42, 26, 30),
    'ats': ('distribution', 'ff7f0e', 38, 24, 40),
    'lv_switchboard': ('distribution', 'd62728', 42, 26, 50),
    'ups': ('equipment', '17becf', 36, 24, 60),
    'pdu': ('equipment', 'bcbd22', 30, 22, 70),
    'rpp': ('equipment', 'e377c2', 24, 18, 80),
    'panelboard': ('distribution', '7f7f7f', 32, 22, 70),
    'custom': ('custom', 'aec7e8', 28, 20, 80),
    'rack_circuit_terminator': ('rack_boundary', 'ff9896', 10, 10, 90),
}

DATA_HALL_KIND_Y = {
    'generator': 70,
    'utility_service': 70,
    'mv_switchgear': 110,
    'transformer': 145,
    'ats': 185,
    'lv_switchboard': 235,
    'ups': 285,
    'pdu': 345,
    'rpp': 420,
    'rack_circuit_terminator': 0,
}

MECH_KIND_Y = {
    'generator': 75,
    'transformer': 135,
    'ats': 210,
    'lv_switchboard': 295,
    'panelboard': 400,
    'custom': 500,
}

YARD_KIND_Y = {
    'utility_service': 90,
    'mv_switchgear': 220,
    'transformer': 350,
}


def style_for_kind(kind):
    return KIND_STYLE.get(kind, ('equipment', 'cccccc', 24, 18, 80))


def blueprint_slot_center(location_name, x_start, row_y_by_number, row, physical_label):
    origin_x, origin_y = BLUEPRINT_LOCATION_ORIGINS[location_name]
    global_x = x_start + (physical_label - 1) * BLUEPRINT_SLOT_WIDTH + BLUEPRINT_SLOT_WIDTH / 2
    global_y = row_y_by_number[row] + BLUEPRINT_SLOT_HEIGHT / 2
    return global_x - origin_x, global_y - origin_y


def rack_local_coordinates(rack):
    name = rack.name
    loc_name = rack.location.name if rack.location else ''

    match = re.match(r'^(?P<prefix>BE|FE|GB300)-(?:SU|P)(?P<scalable_unit>\d+)-R(?P<row>\d+)-C(?P<col>\d+)$', name)
    if match:
        prefix = match.group('prefix')
        scalable_unit = int(match.group('scalable_unit'))
        row = int(match.group('row'))
        col = int(match.group('col'))
        blueprint_block = BLUEPRINT_SCALABLE_UNIT_BLOCKS.get(scalable_unit)
        if blueprint_block and blueprint_block[0] == loc_name:
            _, x_start, row_y_by_number = blueprint_block
            label_map = {
                'GB300': BLUEPRINT_GB300_LABELS,
                'BE': BLUEPRINT_BE_LABELS,
                'FE': BLUEPRINT_FE_LABELS,
            }[prefix]
            if 1 <= col <= len(label_map):
                return blueprint_slot_center(loc_name, x_start, row_y_by_number, row, label_map[col - 1])

    match = re.match(r'^NW-(?:SU|P)(?P<hall>\d+)(?P<section>[AB])-R(?P<row>\d+)-C(?P<col>\d+)$', name)
    if match:
        hall_section = f"{match.group('hall')}{match.group('section')}"
        row = int(match.group('row'))
        col = int(match.group('col'))
        blueprint_block = BLUEPRINT_NW_BLOCKS.get(hall_section)
        if blueprint_block and blueprint_block[0] == loc_name:
            _, x_start, row_y_by_number = blueprint_block
            return blueprint_slot_center(loc_name, x_start, row_y_by_number, row, col)

    # Fallback for unexpected racks in a powered location: deterministic grid, still room-local.
    siblings = list(Rack.objects.filter(location=rack.location).order_by('name').values_list('pk', flat=True))
    index = siblings.index(rack.pk) if rack.pk in siblings else 0
    return 90 + (index % 24) * 62, 760 + (index // 24) * 44


def ckt_offset(name):
    match = re.search(r'CKT(?P<number>\d+)$', name)
    number = int(match.group('number')) if match else 1
    slot = (number - 1) % 8
    return ((slot % 4) - 1.5) * 7, ((slot // 4) - 0.5) * 9


def fallback_node_coordinate(node, index_by_location_kind):
    loc_name = node.location.name if node.location else ''
    kind = node.node_kind
    letter_match = re.search(r'-(?:\d)?(?P<letter>[A-H])(?:-|$)', node.name)
    letter_index = ord(letter_match.group('letter')) - ord('A') if letter_match else None

    if loc_name.startswith('Data Hall'):
        lane_y = DATA_HALL_KIND_Y.get(kind, 820)
        if letter_index is not None:
            width = LOCATION_SIZES.get(loc_name, (1680, 980))[0]
            x = 420 + letter_index * ((width - 840) / 7)
        else:
            index = index_by_location_kind[(loc_name, kind)]
            index_by_location_kind[(loc_name, kind)] += 1
            width = LOCATION_SIZES.get(loc_name, (1680, 980))[0]
            x = 420 + (index % 8) * ((width - 840) / 7)
            lane_y += (index // 8) * 42
        return x, lane_y

    if loc_name == 'Mechanical Plant':
        lane_y = MECH_KIND_Y.get(kind, 570)
        if letter_index is not None:
            x = 120 + letter_index * 120
        else:
            index = index_by_location_kind[(loc_name, kind)]
            index_by_location_kind[(loc_name, kind)] += 1
            x = 120 + (index % 8) * 120
            lane_y += (index // 8) * 44
        return x, lane_y

    if loc_name in {'Primary Electrical Yard', 'Utility Switchyard'}:
        lane_y = YARD_KIND_Y.get(kind, 240)
        numbers = [int(value) for value in re.findall(r'\d+', node.name)]
        index = numbers[-1] - 1 if numbers else index_by_location_kind[(loc_name, kind)]
        index_by_location_kind[(loc_name, kind)] += 1
        return 120 + (index % 8) * 95, lane_y + (index // 8) * 58

    index = index_by_location_kind[(loc_name, kind)]
    index_by_location_kind[(loc_name, kind)] += 1
    return 80 + (index % 10) * 70, 80 + (index // 10) * 60


def refine_coordinates_from_topology(coords_by_node_id):
    children_by_node_id = defaultdict(list)
    for segment in ElectricalSegment.objects.select_related('from_terminal__node', 'to_terminal__node'):
        children_by_node_id[segment.from_terminal.node_id].append(segment.to_terminal.node_id)

    for _ in range(6):
        updates = {}
        for node in ElectricalNode.objects.select_related('location'):
            child_points = [coords_by_node_id[child_id] for child_id in children_by_node_id.get(node.pk, ()) if child_id in coords_by_node_id]
            if not child_points or node.node_kind == 'rack_circuit_terminator':
                continue
            avg_x = sum(point[0] for point in child_points) / len(child_points)
            if node.location and node.location.name.startswith('Data Hall'):
                avg_y = sum(point[1] for point in child_points) / len(child_points)
                upstream_offset_by_kind = {
                    'rpp': 0,
                    'pdu': 120,
                    'ups': 240,
                    'lv_switchboard': 340,
                    'ats': 440,
                    'transformer': 540,
                    'generator': 540,
                    'mv_switchgear': 620,
                    'utility_service': 700,
                }
                y = avg_y - upstream_offset_by_kind.get(node.node_kind, 80)
                location_height = LOCATION_SIZES.get(node.location.name, (0, 980))[1]
                y = max(60, min(location_height - 60, y))
                updates[node.pk] = (avg_x, y)
            elif node.location and node.location.name == 'Mechanical Plant':
                y = MECH_KIND_Y.get(node.node_kind, max(70, min(point[1] for point in child_points) - 46))
                updates[node.pk] = (avg_x, y)
            elif node.location and node.location.name in {'Primary Electrical Yard', 'Utility Switchyard'}:
                y = YARD_KIND_Y.get(node.node_kind, max(70, min(point[1] for point in child_points) - 46))
                updates[node.pk] = (avg_x, y)
            else:
                updates[node.pk] = (avg_x, max(45, sum(point[1] for point in child_points) / len(child_points) - 40))
        coords_by_node_id.update(updates)


def build_coordinates():
    rack_coords = {}
    for rack in Rack.objects.filter(site__slug=MAD_SITE_SLUG, location__name__in=LOCATION_SIZES).select_related('location').order_by('name'):
        rack_coords[rack.pk] = rack_local_coordinates(rack)

    coords_by_node_id = {}
    handoffs_by_node = PowerHandoffPoint.objects.select_related(
        'electrical_node',
        'power_port__device__rack',
    ).filter(
        electrical_node__isnull=False,
        power_port__device__rack__isnull=False,
    )
    for handoff in handoffs_by_node:
        rack = handoff.power_port.device.rack
        if rack.pk in rack_coords:
            rx, ry = rack_coords[rack.pk]
            ox, oy = ckt_offset(handoff.electrical_node.name)
            coords_by_node_id[handoff.electrical_node_id] = (rx + ox, ry + oy)

    index_by_location_kind = defaultdict(int)
    for node in ElectricalNode.objects.select_related('location').order_by('location__name', 'node_kind', 'name'):
        coords_by_node_id.setdefault(node.pk, fallback_node_coordinate(node, index_by_location_kind))

    refine_coordinates_from_topology(coords_by_node_id)
    return rack_coords, coords_by_node_id


def decimal_value(value):
    return Decimal(str(round(value, 3)))


def upsert_spatial_frame(*, site, location=None, parent_frame=None, origin_x=None, origin_y=None, width, height, name, slug, source_ref=''):
    frame, _ = SpatialFrame.objects.get_or_create(
        slug=slug,
        defaults={
            'name': name,
            'site': site,
        },
    )
    frame.name = name
    frame.site = site
    frame.location = location
    frame.parent_frame = parent_frame
    frame.origin_x_in_parent = decimal_value(origin_x) if origin_x is not None else None
    frame.origin_y_in_parent = decimal_value(origin_y) if origin_y is not None else None
    frame.width = decimal_value(width)
    frame.height = decimal_value(height)
    frame.units = 'madison_svg_unit'
    frame.axis_orientation = SpatialAxisOrientationChoices.ORIENTATION_UPPER_LEFT_X_RIGHT_Y_DOWN
    frame.source_document = BLUEPRINT_SOURCE
    frame.source_ref = source_ref
    frame.full_clean()
    frame.save()
    return frame


def upsert_spatial_placement(*, frame, assigned_object, name, slug, x, y, width=None, depth=None, height=None, rotation=0, placement_kind=None, confidence=None, metadata=None, source_ref=''):
    assigned_object_type = ContentType.objects.get_for_model(assigned_object)
    placement, _ = SpatialPlacement.objects.get_or_create(
        slug=slug,
        defaults={
            'name': name,
            'spatial_frame': frame,
            'assigned_object_type': assigned_object_type,
            'assigned_object_id': assigned_object.pk,
            'x': decimal_value(x),
            'y': decimal_value(y),
        },
    )
    placement.name = name
    placement.spatial_frame = frame
    placement.assigned_object_type = assigned_object_type
    placement.assigned_object_id = assigned_object.pk
    placement.x = decimal_value(x)
    placement.y = decimal_value(y)
    placement.z = Decimal('0.000')
    placement.width = decimal_value(width) if width is not None else None
    placement.depth = decimal_value(depth) if depth is not None else None
    placement.height = decimal_value(height) if height is not None else None
    placement.rotation_degrees = Decimal(str(rotation))
    placement.anchor = SpatialAnchorChoices.ANCHOR_CENTER
    placement.placement_kind = placement_kind or SpatialPlacementKindChoices.KIND_PHYSICAL
    placement.confidence = confidence or SpatialConfidenceChoices.CONFIDENCE_DERIVED
    placement.source_document = BLUEPRINT_SOURCE
    placement.source_ref = source_ref
    placement.metadata = metadata or {}
    placement.full_clean()
    placement.save()
    return placement


def save_spatial_layout(site, locations, rack_coords, coords_by_node_id):
    touched_frames = []
    touched_placements = []

    site_width = 12500
    site_height = 5300
    site_frame = upsert_spatial_frame(
        site=site,
        width=site_width,
        height=site_height,
        name=f'{site.name} electrical plant overview',
        slug=slugify(f'{site.slug}-electrical-plant-overview')[:100],
        source_ref='site-overview',
    )
    touched_frames.append(site_frame)
    touched_placements.append(upsert_spatial_placement(
        frame=site_frame,
        assigned_object=site,
        name=f'{site.name} site underlay',
        slug=slugify(f'{site.slug}-site-underlay')[:100],
        x=site_width / 2,
        y=site_height / 2,
        width=site_width,
        depth=site_height,
        height=0,
        placement_kind=SpatialPlacementKindChoices.KIND_SCHEMATIC,
        metadata={
            'marker': MARKER,
            'object_type': 'site_underlay',
            'source': BLUEPRINT_SOURCE,
        },
        source_ref='site-underlay',
    ))
    for location in locations:
        width, height = LOCATION_SIZES[location.name]
        ox, oy = SITE_OFFSETS[location.name]
        location_frame = upsert_spatial_frame(
            site=site,
            location=location,
            parent_frame=site_frame,
            origin_x=ox,
            origin_y=oy,
            width=width,
            height=height,
            name=f'{site.name} / {location.name}',
            slug=slugify(f'{site.slug}-{location.slug}-electrical-layout')[:100],
            source_ref=f'location:{location.name}',
        )
        touched_frames.append(location_frame)
        touched_placements.append(upsert_spatial_placement(
            frame=site_frame,
            assigned_object=location,
            name=f'{location.name} underlay',
            slug=slugify(f'{site.slug}-{location.slug}-underlay')[:100],
            x=ox + width / 2,
            y=oy + height / 2,
            width=width,
            depth=height,
            height=0,
            placement_kind=SpatialPlacementKindChoices.KIND_SCHEMATIC,
            metadata={
                'marker': MARKER,
                'object_type': 'location_underlay',
                'source': BLUEPRINT_SOURCE,
            },
            source_ref=f'location-underlay:{location.name}',
        ))

        for rack in Rack.objects.filter(location=location).order_by('name'):
            if rack.pk in rack_coords:
                rack_width = BLUEPRINT_SLOT_WIDTH if location.name in BLUEPRINT_LOCATION_ORIGINS else 18
                rack_depth = BLUEPRINT_SLOT_HEIGHT if location.name in BLUEPRINT_LOCATION_ORIGINS else 28
                x, y = rack_coords[rack.pk]
                touched_placements.append(upsert_spatial_placement(
                    frame=location_frame,
                    assigned_object=rack,
                    name=f'{rack.name} layout',
                    slug=slugify(f'{site.slug}-{rack.name}-rack-layout')[:100],
                    x=x,
                    y=y,
                    width=rack_width,
                    depth=rack_depth,
                    height=0,
                    metadata={
                        'marker': MARKER,
                        'object_type': 'rack',
                    },
                    source_ref=f'rack:{rack.name}',
                ))

        nodes = ElectricalNode.objects.filter(location=location).order_by('node_kind', 'name')
        for node in nodes:
            x, y = coords_by_node_id[node.pk]
            symbol, color, node_width, node_height, z_index = style_for_kind(node.node_kind)
            touched_placements.append(upsert_spatial_placement(
                frame=location_frame,
                assigned_object=node,
                name=f'{node.name} layout',
                slug=slugify(f'{site.slug}-{node.slug}-node-layout')[:100],
                x=x,
                y=y,
                width=node_width,
                depth=node_height,
                height=0,
                placement_kind=SpatialPlacementKindChoices.KIND_SCHEMATIC,
                metadata={
                    'marker': MARKER,
                    'object_type': 'electrical_node',
                    'node_kind': node.node_kind,
                    'symbol_kind': symbol,
                    'color': color,
                    'z_index': z_index,
                },
                source_ref=f'electrical-node:{node.slug}',
            ))

    return touched_frames, touched_placements


def save_node_placements(power_system, locations, coords_by_node_id):
    created = updated = 0
    locations_by_id = {location.pk: location for location in locations}
    for node in ElectricalNode.objects.filter(power_system=power_system, location__in=locations).select_related('site', 'location').order_by('name'):
        x, y = coords_by_node_id[node.pk]
        symbol, color, width, height, z_index = style_for_kind(node.node_kind)
        slug = slugify(f'layout-{node.slug}')[:92]
        if ElectricalNodePlacement.objects.exclude(electrical_node=node).filter(slug=slug).exists():
            slug = slugify(f'layout-{node.slug}-{node.pk}')[:100]
        placement, was_created = ElectricalNodePlacement.objects.update_or_create(
            power_system=power_system,
            electrical_node=node,
            site=node.site,
            location=locations_by_id[node.location_id],
            defaults={
                'name': f'Layout {node.name}'[:100],
                'slug': slug,
                'description': MARKER,
                'placement_scope_type': PlacementScopeChoices.SCOPE_LOCATION,
                'x': Decimal(str(round(x, 2))),
                'y': Decimal(str(round(y, 2))),
                'width': Decimal(str(width)),
                'height': Decimal(str(height)),
                'rotation_degrees': Decimal('0'),
                'symbol_kind': symbol,
                'label_mode': (
                    PlacementLabelModeChoices.MODE_HIDDEN
                    if node.node_kind == 'rack_circuit_terminator'
                    else PlacementLabelModeChoices.MODE_NAME
                ),
                'color': color,
                'z_index': z_index,
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1
    return created, updated


def main():
    site = Site.objects.get(slug=MAD_SITE_SLUG)
    power_system = PowerSystem.objects.get(site=site)
    locations = list(Location.objects.filter(site=site, name__in=LOCATION_SIZES).order_by('name'))
    missing = set(LOCATION_SIZES) - {location.name for location in locations}
    if missing:
        raise RuntimeError(f'Missing expected GS001 locations: {sorted(missing)}')

    rack_coords, coords_by_node_id = build_coordinates()
    spatial_frames, spatial_placements = save_spatial_layout(site, locations, rack_coords, coords_by_node_id)
    created, updated = save_node_placements(power_system, locations, coords_by_node_id)

    print(f'SpatialFrame rows touched: {len(spatial_frames)}')
    for frame in spatial_frames:
        scope = frame.location or frame.site
        print(f'  {frame.pk}: {scope} ({frame.width} x {frame.height} {frame.units})')
    print(f'SpatialPlacement rows touched: {len(spatial_placements)}')
    print(f'ElectricalNodePlacement rows created={created} updated={updated}')
    print(f'Electrical nodes with coordinates={len(coords_by_node_id)}')
    print(f'Rack spatial coordinates={len(rack_coords)}')


main()
