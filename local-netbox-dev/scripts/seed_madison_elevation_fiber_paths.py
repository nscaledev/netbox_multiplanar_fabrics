from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path
import sys

from django.db import transaction

from dcim.models import Device, Rack
from extras.models import Tag
from netbox_plant_graph.models import FiberSegment, TransferMap


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_v2_graph import (  # noqa: E402
    MAD_SITE_SLUG,
    ensure_fabric,
    ensure_surfaces_for_devices,
    stamp_path_segments,
)


SOURCE_MARKER = 'madison_elevation_fiber_paths_v2'
PATH_KEY = 'madison-elevation-authoritative-fiber-paths-v2'
PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]
OUTPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_fiber_path_map.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_fiber_path_map.csv'),
]


def pattern_input_path() -> Path:
    for path in PATTERN_INPUT_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison elevation shuffle/leaf pattern CSV not found in: {PATTERN_INPUT_PATHS}')


def output_path() -> Path:
    for path in OUTPUT_PATHS:
        if path.parent.exists():
            return path
    OUTPUT_PATHS[-1].parent.mkdir(parents=True, exist_ok=True)
    return OUTPUT_PATHS[-1]


def natural_key(value: str):
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r'(\d+)', value))


def su_number_from_slug(slug: str) -> int:
    value = slug.rsplit('_', 1)[-1]
    if not value.isdigit():
        raise RuntimeError(f'Unexpected SU tag slug: {slug}')
    return int(value)


def single_su_number(value: str) -> int:
    su_tags = [item for item in value.split(',') if item]
    if len(su_tags) != 1:
        raise RuntimeError(f'Expected exactly one SU tag in pattern row, got {value!r}')
    return su_number_from_slug(su_tags[0])


def read_pattern_rows() -> list[dict[str, str]]:
    with pattern_input_path().open(newline='') as handle:
        rows = [row for row in csv.DictReader(handle)]
    bad_rows = [row for row in rows if row['status'] != 'ok']
    if bad_rows:
        raise RuntimeError(f'Pattern report has {len(bad_rows)} non-ok rows; regenerate/fix before seeding paths.')
    return rows


def gb300_racks_by_su() -> dict[int, list[Rack]]:
    grouped: dict[int, list[Rack]] = {}
    for tag in Tag.objects.filter(slug__startswith='nv_su_').order_by('slug'):
        racks = list(
            Rack.objects.filter(site__slug=MAD_SITE_SLUG, role__slug='nvl72_poweredgexe9712', tags=tag)
            .prefetch_related('tags')
            .order_by('name')
        )
        if racks:
            grouped[su_number_from_slug(tag.slug)] = sorted(racks, key=lambda rack: natural_key(rack.name))
    return grouped


def gb300_trays_by_rack(rack_ids: list[int]) -> dict[int, list[Device]]:
    grouped: dict[int, list[Device]] = {}
    for device in (
        Device.objects.filter(site__slug=MAD_SITE_SLUG, rack_id__in=rack_ids, device_type__slug='gb300ct')
        .select_related('rack')
        .order_by('rack__name', 'position', 'name')
    ):
        grouped.setdefault(device.rack_id, []).append(device)
    return grouped


def cassettes_for_box(box_name: str) -> list[Device]:
    pattern = re.compile(r'-cassette-(?P<tray>\d+)\.(?P<slot>\d+)$')
    cassettes = list(
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            name__startswith=f'{box_name}-cassette-',
            device_type__slug='shuffle-cassette-2x2-mpo',
        ).order_by('name')
    )
    return sorted(
        cassettes,
        key=lambda device: (
            int(pattern.search(device.name).group('tray')),
            int(pattern.search(device.name).group('slot')),
        ),
    )


def all_pattern_cassettes(pattern_rows: list[dict[str, str]]) -> dict[str, list[Device]]:
    box_names = sorted({row['shuffle_18_box'] for row in pattern_rows} | {row['shuffle_14_box'] for row in pattern_rows})
    return {box_name: cassettes_for_box(box_name) for box_name in box_names}


def gb_endpoint_sequence(su: int, gb_racks: dict[int, list[Rack]], trays_by_rack: dict[int, list[Device]], osfp_name: str, gb_mpo: int) -> list[dict]:
    rows = []
    for rack_index, rack in enumerate(gb_racks.get(su, []), start=1):
        for tray_index, tray in enumerate(trays_by_rack.get(rack.pk, []), start=1):
            rows.append(
                {
                    'rack_index': rack_index,
                    'rack': rack,
                    'tray_index': tray_index,
                    'device': tray,
                    'interface_name': osfp_name,
                    'mpo': gb_mpo,
                }
            )
    return rows


def cassette_mpo_sequence(pattern_row: dict[str, str], cassette_lookup: dict[str, list[Device]]) -> list[dict]:
    cassettes = [*cassette_lookup[pattern_row['shuffle_18_box']], *cassette_lookup[pattern_row['shuffle_14_box']]]
    rows = []
    for cassette_index, cassette in enumerate(cassettes, start=1):
        for mpo in range(1, 5):
            rows.append(
                {
                    'cassette_index': cassette_index,
                    'mpo': mpo,
                    'device': cassette,
                    'front': (cassette.name, f'front-mpo-{mpo:02d}', 1),
                    'rear': (cassette.name, f'rear-mpo-{mpo:02d}', 1),
                }
            )
    return rows


def leaf_endpoint_sequence(pattern_row: dict[str, str]) -> list[dict]:
    leaf_devices = []
    planes = [int(value) for value in pattern_row['planes'].split(',')]
    for leaf_device_name, plane in zip(pattern_row['leaf_devices'].split('|'), planes, strict=True):
        leaf_devices.append({'plane': plane, 'device_name': leaf_device_name})

    rows = []
    for cage in range(1, 33):
        rows.extend(
            [
                {'plane': leaf_devices[0]['plane'], 'device_name': leaf_devices[0]['device_name'], 'interface_name': f'swp{cage}', 'mpo': 1},
                {'plane': leaf_devices[1]['plane'], 'device_name': leaf_devices[1]['device_name'], 'interface_name': f'swp{cage}', 'mpo': 1},
                {'plane': leaf_devices[0]['plane'], 'device_name': leaf_devices[0]['device_name'], 'interface_name': f'swp{cage}', 'mpo': 2},
                {'plane': leaf_devices[1]['plane'], 'device_name': leaf_devices[1]['device_name'], 'interface_name': f'swp{cage}', 'mpo': 2},
            ]
        )
    return rows


def endpoint(device_name: str, port_name: str, mpo: int) -> tuple[str, str, int]:
    return (device_name, port_name, mpo)


def path_metadata(pattern_row: dict[str, str], *, segment: str, sequence_index: int, cassette_row: dict, extra: dict) -> dict:
    metadata = {
        SOURCE_MARKER: True,
        'modeled_status': 'planned',
        'source_authority': 'Madison workbook rack elevation sheets',
        'architecture_reference': 'ROCE 4-plane Shuffle Cabling Patterns',
        'segment': segment,
        'su': single_su_number(pattern_row['su_tags']),
        'row_id_tags': pattern_row['row_id_tags'],
        'be_rack': pattern_row['rack'],
        'side': pattern_row['side'],
        'nic_index_zero': int(pattern_row['nic_index_zero']),
        'leaf_index_bottom_up': int(pattern_row['leaf_index_bottom_up']),
        'sequence_index': sequence_index,
        'shuffle_cassette': cassette_row['device'].name,
        'shuffle_cassette_index_in_pair': cassette_row['cassette_index'],
        'shuffle_mpo': cassette_row['mpo'],
    }
    metadata.update(extra)
    return metadata


def build_path_rows_and_segments(pattern_rows: list[dict[str, str]], counters: Counter):
    gb_racks = gb300_racks_by_su()
    rack_ids = [rack.pk for racks in gb_racks.values() for rack in racks]
    trays_by_rack = gb300_trays_by_rack(rack_ids)
    cassette_lookup = all_pattern_cassettes(pattern_rows)
    report_rows = []
    segments = []
    involved_devices = set()

    for pattern_row in pattern_rows:
        su = single_su_number(pattern_row['su_tags'])
        su_gb_racks = gb_racks.get(su, [])
        if len(su_gb_racks) != 7:
            counters['pattern_groups_skipped_incomplete_gb300_racks'] += 1
            report_rows.append({'status': 'skipped', 'skip_reason': f'SU {su} has {len(su_gb_racks)} NVL72/GB300 racks; expected 7.', 'segment': 'pattern_group', 'su': su})
            continue
        incomplete = [rack.name for rack in su_gb_racks if len(trays_by_rack.get(rack.pk, [])) != 18]
        if incomplete:
            counters['pattern_groups_skipped_incomplete_gb300_trays'] += 1
            report_rows.append({'status': 'skipped', 'skip_reason': f'SU {su} has NVL72 racks without 18 GB300 trays: {", ".join(incomplete)}', 'segment': 'pattern_group', 'su': su})
            continue

        gb_rows = gb_endpoint_sequence(su, gb_racks, trays_by_rack, pattern_row['gb300_osfp'], int(pattern_row['gb300_mpo']))
        cassette_rows = cassette_mpo_sequence(pattern_row, cassette_lookup)
        leaf_rows = leaf_endpoint_sequence(pattern_row)
        if len(gb_rows) != 126 or len(cassette_rows) < 126 or len(leaf_rows) < 126:
            raise RuntimeError(f'Pattern row {pattern_row} produced invalid endpoint cardinality.')

        for sequence_index, (gb_row, cassette_row) in enumerate(zip(gb_rows, cassette_rows[:126], strict=True), start=1):
            involved_devices.update([gb_row['device'], cassette_row['device']])
            metadata = path_metadata(
                pattern_row,
                segment='gb300_to_shuffle_front',
                sequence_index=sequence_index,
                cassette_row=cassette_row,
                extra={
                    'gb300_rack_index': gb_row['rack_index'],
                    'gb300_rack': gb_row['rack'].name,
                    'gb300_tray_index': gb_row['tray_index'],
                    'gb300_device': gb_row['device'].name,
                    'gb300_interface': gb_row['interface_name'],
                    'gb300_mpo': gb_row['mpo'],
                },
            )
            segments.append(
                {
                    'a': endpoint(gb_row['device'].name, gb_row['interface_name'], gb_row['mpo']),
                    'b': cassette_row['front'],
                    'segment_role': 'gb300_to_shuffle_front',
                    'metadata': metadata,
                }
            )
            report_rows.append({**metadata, 'status': 'planned', 'a_endpoint': f"{gb_row['device'].name}:{gb_row['interface_name']}:mpo-{gb_row['mpo']}", 'b_endpoint': f"{cassette_row['device'].name}:front-mpo-{cassette_row['mpo']:02d}"})

        for sequence_index, (cassette_row, leaf_row) in enumerate(zip(cassette_rows[:126], leaf_rows[:126], strict=True), start=1):
            leaf = Device.objects.get(site__slug=MAD_SITE_SLUG, name=leaf_row['device_name'])
            involved_devices.update([cassette_row['device'], leaf])
            metadata = path_metadata(
                pattern_row,
                segment='shuffle_rear_to_leaf',
                sequence_index=sequence_index,
                cassette_row=cassette_row,
                extra={
                    'plane': leaf_row['plane'],
                    'leaf_device': leaf_row['device_name'],
                    'leaf_interface': leaf_row['interface_name'],
                    'leaf_mpo': leaf_row['mpo'],
                },
            )
            segments.append(
                {
                    'a': cassette_row['rear'],
                    'b': endpoint(leaf_row['device_name'], leaf_row['interface_name'], leaf_row['mpo']),
                    'segment_role': 'shuffle_rear_to_leaf',
                    'metadata': metadata,
                }
            )
            report_rows.append({**metadata, 'status': 'planned', 'a_endpoint': f"{cassette_row['device'].name}:rear-mpo-{cassette_row['mpo']:02d}", 'b_endpoint': f"{leaf_row['device_name']}:{leaf_row['interface_name']}:mpo-{leaf_row['mpo']}"})

        for sequence_index, (cassette_row, leaf_row) in enumerate(zip(cassette_rows[126:], leaf_rows[126:], strict=True), start=127):
            report_rows.append(
                {
                    **path_metadata(pattern_row, segment='spare_position', sequence_index=sequence_index, cassette_row=cassette_row, extra={'plane': leaf_row['plane'], 'leaf_device': leaf_row['device_name']}),
                    'status': 'spare',
                }
            )
        counters['pattern_groups_processed'] += 1
    return report_rows, segments, involved_devices


def write_report(rows: list[dict]) -> Path:
    path = output_path()
    fieldnames = sorted({key for row in rows for key in row})
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return path


@transaction.atomic
def main() -> None:
    counters = Counter()
    fabric = ensure_fabric()
    pattern_rows = read_pattern_rows()
    report_rows, segments, involved_devices = build_path_rows_and_segments(pattern_rows, counters)
    ensure_surfaces_for_devices(fabric, sorted(involved_devices, key=lambda device: device.name), marker=SOURCE_MARKER, counters=counters)
    counters.update(stamp_path_segments(fabric, marker=SOURCE_MARKER, path_key=PATH_KEY, segments=segments))
    path = write_report(report_rows)

    print('Madison elevation-authoritative fiber path seeding complete for netbox_plant_graph v2.')
    print(f'pattern_rows={len(pattern_rows)}')
    print(f'report_rows={len(report_rows)}')
    print(f'wrote_csv={path}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_fiber_segments={FiberSegment.objects.filter(fabric=fabric, metadata__path_key=PATH_KEY).count()}')
    print(f'marked_transfer_maps={TransferMap.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')


main()
