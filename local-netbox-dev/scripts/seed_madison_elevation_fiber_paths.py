from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device, FrontPort, Interface, Rack, RearPort
from extras.models import Tag
from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, Fabric, FineEdge, TerminationPoint, TransferMap


MAD_SITE_SLUG = 'mad-1'
FABRIC_NAME = 'MAD-1 RoCE Fabric'
SOURCE_MARKER = 'madison_elevation_fiber_paths_v1'
SHUFFLE_PATTERN_MARKER = 'madison_shuffle_2x2_transfer_maps_v1'
PATTERN_INPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_shuffle_leaf_patterns.csv'),
]
OUTPUT_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_fiber_path_map.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_elevation_fiber_path_map.csv'),
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


def su_tags_for(obj) -> list[str]:
    return sorted(tag.slug for tag in obj.tags.all() if tag.slug.startswith('nv_su_'))


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
        Device.objects.filter(site__slug=MAD_SITE_SLUG, rack_id__in=rack_ids, device_type__slug='poweredge-xe9712-gb300-compute-tray')
        .select_related('rack')
        .order_by('rack__name', 'position', 'name')
    ):
        grouped.setdefault(device.rack_id, []).append(device)
    return grouped


def cassettes_for_box(box_name: str) -> list[Device]:
    cassette_pattern = re.compile(r'-tray-(?P<tray>\d+)-cassette-(?P<cassette>\d+)$')
    cassettes = list(
        Device.objects.filter(site__slug=MAD_SITE_SLUG, name__startswith=f'{box_name}-tray-', device_type__slug='shuffle-cassette-2x2-mpo')
        .select_related('rack')
        .order_by('name')
    )
    return sorted(
        cassettes,
        key=lambda device: (
            int(cassette_pattern.search(device.name).group('tray')),
            int(cassette_pattern.search(device.name).group('cassette')),
        ),
    )


def all_pattern_cassettes(pattern_rows: list[dict[str, str]]) -> dict[str, list[Device]]:
    box_names = sorted({row['shuffle_18_box'] for row in pattern_rows} | {row['shuffle_14_box'] for row in pattern_rows})
    return {box_name: cassettes_for_box(box_name) for box_name in box_names}


def build_indexes(fabric: Fabric):
    interface_ct = ContentType.objects.get_for_model(Interface)
    front_port_ct = ContentType.objects.get_for_model(FrontPort)
    rear_port_ct = ContentType.objects.get_for_model(RearPort)

    interfaces = {
        (interface.device_id, interface.name): interface
        for interface in Interface.objects.filter(device__site__slug=MAD_SITE_SLUG, type__icontains='osfp')
    }
    front_ports = {
        (port.device_id, port.name): port
        for port in FrontPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug='shuffle-cassette-2x2-mpo')
    }
    rear_ports = {
        (port.device_id, port.name): port
        for port in RearPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug='shuffle-cassette-2x2-mpo')
    }
    termination_points = {
        (tp.source_type_id, tp.source_id): tp
        for tp in TerminationPoint.objects.filter(plant_node__fabric=fabric, source_id__isnull=False)
        .select_related('plant_node')
    }
    attachment_units = {
        (au.termination_point_id, au.name): au
        for au in AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric)
        .select_related('termination_point', 'termination_point__plant_node')
    }
    return {
        'interface_ct': interface_ct,
        'front_port_ct': front_port_ct,
        'rear_port_ct': rear_port_ct,
        'interfaces': interfaces,
        'front_ports': front_ports,
        'rear_ports': rear_ports,
        'termination_points': termination_points,
        'attachment_units': attachment_units,
    }


def interface_au(device: Device, interface_name: str, mpo: int, indexes) -> AttachmentUnit:
    interface = indexes['interfaces'].get((device.pk, interface_name))
    if interface is None:
        raise RuntimeError(f'Missing interface {device.name}:{interface_name}')
    tp = indexes['termination_points'].get((indexes['interface_ct'].pk, interface.pk))
    if tp is None:
        raise RuntimeError(f'Missing plant termination point for {device.name}:{interface_name}')
    au = indexes['attachment_units'].get((tp.pk, f'mpo-{mpo}'))
    if au is None:
        raise RuntimeError(f'Missing attachment unit {device.name}:{interface_name}:mpo-{mpo}')
    return au


def port_au(device: Device, side: str, index: int, indexes) -> AttachmentUnit:
    if side == 'front':
        port = indexes['front_ports'].get((device.pk, f'front-mpo-{index:02d}'))
        source_ct = indexes['front_port_ct']
    else:
        port = indexes['rear_ports'].get((device.pk, f'rear-mpo-{index:02d}'))
        source_ct = indexes['rear_port_ct']
    if port is None:
        raise RuntimeError(f'Missing {side} MPO {index} on {device.name}')
    tp = indexes['termination_points'].get((source_ct.pk, port.pk))
    if tp is None:
        raise RuntimeError(f'Missing plant termination point for {device.name}:{side}-mpo-{index:02d}')
    au = indexes['attachment_units'].get((tp.pk, 'mpo'))
    if au is None:
        raise RuntimeError(f'Missing attachment unit for {device.name}:{side}-mpo-{index:02d}')
    return au


def gb_endpoint_sequence(su: int, gb_racks: dict[int, list[Rack]], trays_by_rack: dict[int, list[Device]], osfp_name: str, gb_mpo: int, indexes) -> list[dict]:
    racks = gb_racks.get(su, [])
    if len(racks) != 7:
        raise RuntimeError(f'SU {su} has {len(racks)} NVL72 racks; expected 7.')
    rows = []
    for rack_index, rack in enumerate(racks, start=1):
        trays = trays_by_rack.get(rack.pk, [])
        if len(trays) != 18:
            raise RuntimeError(f'SU {su} rack {rack.name} has {len(trays)} GB300 trays; expected 18.')
        for tray_index, tray in enumerate(trays, start=1):
            rows.append(
                {
                    'rack_index': rack_index,
                    'rack': rack,
                    'tray_index': tray_index,
                    'device': tray,
                    'interface_name': osfp_name,
                    'mpo': gb_mpo,
                    'au': interface_au(tray, osfp_name, gb_mpo, indexes),
                }
            )
    return rows


def cassette_mpo_sequence(pattern_row: dict[str, str], cassette_lookup: dict[str, list[Device]], indexes) -> list[dict]:
    cassettes = [*cassette_lookup[pattern_row['shuffle_18_box']], *cassette_lookup[pattern_row['shuffle_14_box']]]
    expected_count = int(pattern_row['total_pair_cassettes'])
    if len(cassettes) != expected_count:
        raise RuntimeError(
            f"{pattern_row['rack']} {pattern_row['side']} NIC{pattern_row['nic_index_zero']} has {len(cassettes)} cassettes; expected {expected_count}."
        )
    rows = []
    for cassette_index, cassette in enumerate(cassettes, start=1):
        for mpo in range(1, 5):
            rows.append(
                {
                    'cassette_index': cassette_index,
                    'mpo': mpo,
                    'device': cassette,
                    'front_au': port_au(cassette, 'front', mpo, indexes),
                    'rear_au': port_au(cassette, 'rear', mpo, indexes),
                }
            )
    return rows


def leaf_endpoint_sequence(pattern_row: dict[str, str], indexes) -> list[dict]:
    leaf_devices = []
    planes = [int(value) for value in pattern_row['planes'].split(',')]
    for leaf_device_name, plane in zip(pattern_row['leaf_devices'].split('|'), planes, strict=True):
        device = Device.objects.get(site__slug=MAD_SITE_SLUG, name=leaf_device_name)
        leaf_devices.append({'plane': plane, 'device': device})

    rows = []
    for cage in range(1, 33):
        rows.extend(
            [
                {
                    'plane': leaf_devices[0]['plane'],
                    'device': leaf_devices[0]['device'],
                    'interface_name': f'swp{cage}',
                    'mpo': 1,
                    'au': interface_au(leaf_devices[0]['device'], f'swp{cage}', 1, indexes),
                },
                {
                    'plane': leaf_devices[1]['plane'],
                    'device': leaf_devices[1]['device'],
                    'interface_name': f'swp{cage}',
                    'mpo': 1,
                    'au': interface_au(leaf_devices[1]['device'], f'swp{cage}', 1, indexes),
                },
                {
                    'plane': leaf_devices[0]['plane'],
                    'device': leaf_devices[0]['device'],
                    'interface_name': f'swp{cage}',
                    'mpo': 2,
                    'au': interface_au(leaf_devices[0]['device'], f'swp{cage}', 2, indexes),
                },
                {
                    'plane': leaf_devices[1]['plane'],
                    'device': leaf_devices[1]['device'],
                    'interface_name': f'swp{cage}',
                    'mpo': 2,
                    'au': interface_au(leaf_devices[1]['device'], f'swp{cage}', 2, indexes),
                },
            ]
        )
    return rows


def cable_metadata(pattern_row: dict[str, str], *, segment: str, sequence_index: int, cassette_row: dict, extra: dict) -> dict:
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


def make_coarse_and_fine(a_au: AttachmentUnit, b_au: AttachmentUnit, metadata: dict, cable_profile_name: str):
    coarse = CoarseEdge(
        edge_type='cable',
        a_tp=a_au.termination_point,
        b_tp=b_au.termination_point,
        cable_profile_name=cable_profile_name,
        metadata=metadata,
    )
    fine = FineEdge(
        granularity='attachment_unit',
        edge_type='derived_cable_segment',
        a_au=a_au,
        b_au=b_au,
        parent_coarse_edge=coarse,
        derived_from_profile=False,
        metadata={
            SOURCE_MARKER: True,
            'modeled_status': 'planned',
            'segment': metadata['segment'],
        },
    )
    return coarse, fine


def build_path_rows_and_edges(pattern_rows: list[dict[str, str]], counters: Counter):
    fabric = Fabric.objects.get(name=FABRIC_NAME)
    indexes = build_indexes(fabric)
    gb_racks = gb300_racks_by_su()
    rack_ids = [rack.pk for racks in gb_racks.values() for rack in racks]
    trays_by_rack = gb300_trays_by_rack(rack_ids)
    cassette_lookup = all_pattern_cassettes(pattern_rows)

    report_rows = []
    coarse_edges = []
    fine_edges = []
    transfer_maps = []
    used_cassette_ids = set()

    for pattern_row in pattern_rows:
        su = single_su_number(pattern_row['su_tags'])
        su_gb_racks = gb_racks.get(su, [])
        if len(su_gb_racks) != 7:
            counters['pattern_groups_skipped_incomplete_gb300_racks'] += 1
            report_rows.append(
                {
                    SOURCE_MARKER: True,
                    'status': 'skipped',
                    'skip_reason': f'SU {su} has {len(su_gb_racks)} NVL72/GB300 racks; expected 7.',
                    'segment': 'pattern_group',
                    'su': su,
                    'row_id_tags': pattern_row['row_id_tags'],
                    'be_rack': pattern_row['rack'],
                    'side': pattern_row['side'],
                    'nic_index_zero': int(pattern_row['nic_index_zero']),
                    'leaf_index_bottom_up': int(pattern_row['leaf_index_bottom_up']),
                    'source_authority': 'Madison workbook rack elevation sheets',
                    'architecture_reference': 'ROCE 4-plane Shuffle Cabling Patterns',
                    'modeled_status': 'planned',
                }
            )
            continue

        incomplete_tray_racks = [rack.name for rack in su_gb_racks if len(trays_by_rack.get(rack.pk, [])) != 18]
        if incomplete_tray_racks:
            counters['pattern_groups_skipped_incomplete_gb300_trays'] += 1
            report_rows.append(
                {
                    SOURCE_MARKER: True,
                    'status': 'skipped',
                    'skip_reason': f'SU {su} has NVL72 racks without 18 GB300 trays: {", ".join(incomplete_tray_racks)}',
                    'segment': 'pattern_group',
                    'su': su,
                    'row_id_tags': pattern_row['row_id_tags'],
                    'be_rack': pattern_row['rack'],
                    'side': pattern_row['side'],
                    'nic_index_zero': int(pattern_row['nic_index_zero']),
                    'leaf_index_bottom_up': int(pattern_row['leaf_index_bottom_up']),
                    'source_authority': 'Madison workbook rack elevation sheets',
                    'architecture_reference': 'ROCE 4-plane Shuffle Cabling Patterns',
                    'modeled_status': 'planned',
                }
            )
            continue

        gb_rows = gb_endpoint_sequence(su, gb_racks, trays_by_rack, pattern_row['gb300_osfp'], int(pattern_row['gb300_mpo']), indexes)
        cassette_rows = cassette_mpo_sequence(pattern_row, cassette_lookup, indexes)
        leaf_rows = leaf_endpoint_sequence(pattern_row, indexes)
        if len(gb_rows) != 126:
            raise RuntimeError(f'SU {su} pattern row produced {len(gb_rows)} GB endpoints; expected 126.')
        if len(cassette_rows) != 128 or len(leaf_rows) != 128:
            raise RuntimeError(f'SU {su} pattern row did not produce 128 cassette/leaf positions.')

        for sequence_index, (gb_row, cassette_row) in enumerate(zip(gb_rows, cassette_rows[:126], strict=True), start=1):
            metadata = cable_metadata(
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
            coarse, fine = make_coarse_and_fine(
                gb_row['au'],
                cassette_row['front_au'],
                metadata,
                'madison-gb300-to-shuffle-mpo8-elevation',
            )
            coarse_edges.append(coarse)
            fine_edges.append(fine)
            report_rows.append(
                {
                    **metadata,
                    'status': 'planned',
                    'a_endpoint': f"{gb_row['device'].name}:{gb_row['interface_name']}:mpo-{gb_row['mpo']}",
                    'b_endpoint': f"{cassette_row['device'].name}:front-mpo-{cassette_row['mpo']:02d}",
                }
            )

        for sequence_index, (cassette_row, leaf_row) in enumerate(zip(cassette_rows[:126], leaf_rows[:126], strict=True), start=1):
            metadata = cable_metadata(
                pattern_row,
                segment='shuffle_rear_to_leaf',
                sequence_index=sequence_index,
                cassette_row=cassette_row,
                extra={
                    'plane': leaf_row['plane'],
                    'leaf_device': leaf_row['device'].name,
                    'leaf_interface': leaf_row['interface_name'],
                    'leaf_mpo': leaf_row['mpo'],
                },
            )
            coarse, fine = make_coarse_and_fine(
                cassette_row['rear_au'],
                leaf_row['au'],
                metadata,
                'madison-shuffle-to-leaf-mpo8-elevation',
            )
            coarse_edges.append(coarse)
            fine_edges.append(fine)
            report_rows.append(
                {
                    **metadata,
                    'status': 'planned',
                    'a_endpoint': f"{cassette_row['device'].name}:rear-mpo-{cassette_row['mpo']:02d}",
                    'b_endpoint': f"{leaf_row['device'].name}:{leaf_row['interface_name']}:mpo-{leaf_row['mpo']}",
                }
            )

        for sequence_index, (cassette_row, leaf_row) in enumerate(zip(cassette_rows[126:], leaf_rows[126:], strict=True), start=127):
            report_rows.append(
                {
                    **cable_metadata(
                        pattern_row,
                        segment='spare_position',
                        sequence_index=sequence_index,
                        cassette_row=cassette_row,
                        extra={
                            'plane': leaf_row['plane'],
                            'leaf_device': leaf_row['device'].name,
                            'leaf_interface': leaf_row['interface_name'],
                            'leaf_mpo': leaf_row['mpo'],
                        },
                    ),
                    'status': 'spare',
                    'a_endpoint': f"{cassette_row['device'].name}:front/rear-mpo-{cassette_row['mpo']:02d}",
                    'b_endpoint': f"{leaf_row['device'].name}:{leaf_row['interface_name']}:mpo-{leaf_row['mpo']}",
                }
            )

        for cassette_row in cassette_rows:
            cassette = cassette_row['device']
            if cassette.pk in used_cassette_ids:
                continue
            used_cassette_ids.add(cassette.pk)
            node = cassette_row['front_au'].termination_point.plant_node
            front = {index: port_au(cassette, 'front', index, indexes) for index in range(1, 5)}
            rear = {index: port_au(cassette, 'rear', index, indexes) for index in range(1, 5)}
            for assembly_index, positions in enumerate(((1, 2), (3, 4)), start=1):
                for front_index in positions:
                    for rear_index in positions:
                        transfer_maps.append(
                            TransferMap(
                                owner_node=node,
                                src_attachment_unit=front[front_index],
                                dst_attachment_unit=rear[rear_index],
                                mapping_type='shuffle',
                                metadata={
                                    SOURCE_MARKER: True,
                                    SHUFFLE_PATTERN_MARKER: True,
                                    'modeled_status': 'planned',
                                    'source_authority': 'ROCE 4-plane Shuffle Cabling Patterns',
                                    'assembly_index': assembly_index,
                                    'front_mpo': front_index,
                                    'rear_mpo': rear_index,
                                    'pattern': '2x2 attachment-unit fanout',
                                },
                            )
                        )

        counters['pattern_groups_processed'] += 1
        counters['gb300_to_shuffle_cables_planned'] += 126
        counters['shuffle_to_leaf_cables_planned'] += 126
        counters['spare_positions'] += 2

    return report_rows, coarse_edges, fine_edges, transfer_maps


def write_report(rows: list[dict]) -> Path:
    path = output_path()
    fieldnames = [
        'status',
        'skip_reason',
        'segment',
        'su',
        'row_id_tags',
        'be_rack',
        'side',
        'nic_index_zero',
        'leaf_index_bottom_up',
        'sequence_index',
        'shuffle_cassette',
        'shuffle_cassette_index_in_pair',
        'shuffle_mpo',
        'a_endpoint',
        'b_endpoint',
        'gb300_rack_index',
        'gb300_rack',
        'gb300_tray_index',
        'gb300_device',
        'gb300_interface',
        'gb300_mpo',
        'plane',
        'leaf_device',
        'leaf_interface',
        'leaf_mpo',
        'source_authority',
        'architecture_reference',
        SOURCE_MARKER,
        'modeled_status',
    ]
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return path


@transaction.atomic
def main() -> None:
    counters = Counter()
    pattern_rows = read_pattern_rows()
    report_rows, coarse_edges, fine_edges, transfer_maps = build_path_rows_and_edges(pattern_rows, counters)

    stale_transfer = TransferMap.objects.filter(metadata__has_key=SOURCE_MARKER)
    counters['stale_transfer_maps_deleted'] = stale_transfer.count()
    stale_transfer.delete()

    stale_coarse = CoarseEdge.objects.filter(metadata__has_key=SOURCE_MARKER)
    stale_coarse_ids = list(stale_coarse.values_list('id', flat=True))
    counters['stale_fine_edges_deleted'], _ = FineEdge.objects.filter(parent_coarse_edge_id__in=stale_coarse_ids).delete()
    counters['stale_coarse_edges_deleted'], _ = stale_coarse.delete()

    for batch in chunks(coarse_edges):
        CoarseEdge.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['coarse_edges_created'] += len(batch)

    for batch in chunks(fine_edges):
        FineEdge.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['fine_edges_created'] += len(batch)

    for batch in chunks(transfer_maps):
        TransferMap.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['transfer_maps_created'] += len(batch)

    path = write_report(report_rows)
    print('Madison elevation-authoritative fiber path seeding complete.')
    print(f'pattern_rows={len(pattern_rows)}')
    print(f'report_rows={len(report_rows)}')
    print(f'wrote_csv={path}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_coarse_edges={CoarseEdge.objects.filter(metadata__has_key=SOURCE_MARKER).count()}')
    print(f'marked_fine_edges={FineEdge.objects.filter(metadata__has_key=SOURCE_MARKER).count()}')
    print(f'marked_transfer_maps={TransferMap.objects.filter(metadata__has_key=SOURCE_MARKER).count()}')


main()
