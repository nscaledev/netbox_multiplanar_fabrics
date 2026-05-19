from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device, FrontPort, RearPort
from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, FineEdge, TerminationPoint


SOURCE_MARKER = 'madison_fiber_path_resolution_v1'
RESOLUTION_PATHS = [
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_fiber_path_resolution.csv'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/data/generated/madison_fiber_path_resolution.csv'),
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


def resolution_path() -> Path:
    for path in RESOLUTION_PATHS:
        if path.exists():
            return path
    raise RuntimeError(f'Madison fiber path resolution CSV not found in: {RESOLUTION_PATHS}')


def read_rows() -> list[dict[str, str]]:
    with resolution_path().open(newline='') as handle:
        return list(csv.DictReader(handle))


def cassette_name(box_name: str, tray: str, cassette: str) -> str:
    return f'{box_name}-tray-{int(tray):02d}-cassette-{int(cassette):02d}'


def termination_points_by_source(source_type, source_ids):
    return {
        tp.source_id: tp
        for tp in TerminationPoint.objects.filter(source_type=source_type, source_id__in=source_ids)
    }


def attachment_units_by_tp(termination_points):
    return {
        (au.termination_point_id, au.name): au
        for au in AttachmentUnit.objects.filter(termination_point_id__in=[tp.pk for tp in termination_points])
    }


def edge_metadata(row: dict[str, str], *, segment: str, a_label: str, b_label: str) -> dict:
    return {
        SOURCE_MARKER: True,
        'segment': segment,
        'modeled_status': 'planned',
        'su': int(row['su']),
        'worksheet_path_index': int(row['path_index']),
        'nvl72_rack_ordinal': int(row['nvl72_rack_ordinal']),
        'compute_tray_ordinal': int(row['compute_tray_ordinal']),
        'cx8': int(row['cx8']),
        'gb300_mpo': int(row['gb300_mpo']),
        'shuffle_box_ordinal': int(row['shuffle_box_ordinal']),
        'shuffle_tray': int(row['shuffle_tray']),
        'shuffle_cassette': int(row['shuffle_cassette']),
        'shuffle_mpo': int(row['shuffle_mpo']),
        'plane': int(row['plane']),
        'leaf_switch': int(row['leaf_switch']),
        'leaf_cage': int(row['leaf_cage']),
        'leaf_mpo': int(row['leaf_mpo']),
        'a': a_label,
        'b': b_label,
    }


@transaction.atomic
def main() -> None:
    rows = [row for row in read_rows() if row['status'] == 'fully_resolved_by_label']
    counters = Counter()

    stale_coarse = CoarseEdge.objects.filter(metadata__has_key=SOURCE_MARKER)
    stale_coarse_ids = list(stale_coarse.values_list('id', flat=True))
    deleted_fine, _ = FineEdge.objects.filter(parent_coarse_edge_id__in=stale_coarse_ids).delete()
    deleted_coarse, _ = stale_coarse.delete()
    counters['stale_fine_edges_deleted'] = deleted_fine
    counters['stale_coarse_edges_deleted'] = deleted_coarse

    front_ct = ContentType.objects.get_for_model(FrontPort)
    rear_ct = ContentType.objects.get_for_model(RearPort)

    box_names = sorted({row['shuffle_label_matching_candidate_boxes'] for row in rows})
    cassette_names = sorted({cassette_name(row['shuffle_label_matching_candidate_boxes'], row['shuffle_tray'], row['shuffle_cassette']) for row in rows})
    cassettes = {
        device.name: device
        for device in Device.objects.filter(name__in=cassette_names, device_type__slug='shuffle-cassette-2x2-mpo')
    }
    if len(cassettes) != len(cassette_names):
        missing = sorted(set(cassette_names) - set(cassettes))
        raise RuntimeError(f'Missing {len(missing)} shuffle cassettes; first missing: {missing[0] if missing else ""}')

    front_ports = {
        (port.device.name, port.name): port
        for port in FrontPort.objects.filter(device_id__in=[device.pk for device in cassettes.values()])
        .select_related('device')
    }
    rear_ports = {
        (port.device.name, port.name): port
        for port in RearPort.objects.filter(device_id__in=[device.pk for device in cassettes.values()])
        .select_related('device')
    }
    front_tps = termination_points_by_source(front_ct, [port.pk for port in front_ports.values()])
    rear_tps = termination_points_by_source(rear_ct, [port.pk for port in rear_ports.values()])
    port_tps = list(front_tps.values()) + list(rear_tps.values())
    port_aus = attachment_units_by_tp(port_tps)

    active_au_ids = {int(row['gb300_attachment_unit_id']) for row in rows} | {int(row['leaf_attachment_unit_id']) for row in rows}
    active_aus = {au.pk: au for au in AttachmentUnit.objects.filter(pk__in=active_au_ids).select_related('termination_point')}

    coarse_to_create = []
    fine_specs = []
    for row in rows:
        box_name = row['shuffle_label_matching_candidate_boxes']
        cassette = cassettes[cassette_name(box_name, row['shuffle_tray'], row['shuffle_cassette'])]
        port_suffix = f"{int(row['shuffle_mpo']):02d}"
        front_port = front_ports[(cassette.name, f'front-mpo-{port_suffix}')]
        rear_port = rear_ports[(cassette.name, f'rear-mpo-{port_suffix}')]
        front_tp = front_tps[front_port.pk]
        rear_tp = rear_tps[rear_port.pk]
        front_au = port_aus[(front_tp.pk, 'mpo')]
        rear_au = port_aus[(rear_tp.pk, 'mpo')]
        gb_au = active_aus[int(row['gb300_attachment_unit_id'])]
        leaf_au = active_aus[int(row['leaf_attachment_unit_id'])]

        host_edge = CoarseEdge(
            edge_type='cable',
            a_tp=gb_au.termination_point,
            b_tp=front_tp,
            cable_profile_name='madison-gb300-to-shuffle-mpo8',
            metadata=edge_metadata(row, segment='gb300_to_shuffle', a_label=row['gb300_device'], b_label=cassette.name),
        )
        leaf_edge = CoarseEdge(
            edge_type='cable',
            a_tp=rear_tp,
            b_tp=leaf_au.termination_point,
            cable_profile_name='madison-shuffle-to-leaf-mpo8',
            metadata=edge_metadata(row, segment='shuffle_to_leaf', a_label=cassette.name, b_label=row['leaf_device']),
        )
        coarse_to_create.extend([host_edge, leaf_edge])
        fine_specs.append((host_edge, gb_au, front_au, 'gb300_to_shuffle'))
        fine_specs.append((leaf_edge, rear_au, leaf_au, 'shuffle_to_leaf'))

    for batch in chunks(coarse_to_create):
        CoarseEdge.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['coarse_edges_created'] += len(batch)

    fine_to_create = [
        FineEdge(
            granularity='attachment_unit',
            edge_type='derived_cable_segment',
            a_au=a_au,
            b_au=b_au,
            parent_coarse_edge=coarse_edge,
            derived_from_profile=False,
            metadata={
                SOURCE_MARKER: True,
                'segment': segment,
                'modeled_status': 'planned',
            },
        )
        for coarse_edge, a_au, b_au, segment in fine_specs
    ]
    for batch in chunks(fine_to_create):
        FineEdge.objects.bulk_create(batch, batch_size=BATCH_SIZE)
        counters['fine_edges_created'] += len(batch)

    print('Madison fiber path edge seeding complete.')
    print(f'input_fully_resolved_rows={len(rows)}')
    print(f'unique_shuffle_boxes={len(box_names)}')
    print(f'unique_shuffle_cassettes={len(cassettes)}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'marked_coarse_edges={CoarseEdge.objects.filter(metadata__has_key=SOURCE_MARKER).count()}')
    print(f'marked_fine_edges={FineEdge.objects.filter(metadata__has_key=SOURCE_MARKER).count()}')


main()
