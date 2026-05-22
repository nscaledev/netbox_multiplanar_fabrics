from __future__ import annotations

from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from dcim.models import Device, DeviceBay, FrontPort, RearPort
from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, FineEdge, PlantNode, TerminationPoint


MAD_SITE_SLUG = 'gs001'
CAPACITY_MARKER = 'madison_shuffle_full_cassette_capacity_v1'
PATH_MARKER = 'madison_fiber_path_resolution_v1'


@transaction.atomic
def main() -> None:
    counters = Counter()

    path_coarse_edges = CoarseEdge.objects.filter(metadata__has_key=PATH_MARKER)
    path_coarse_ids = list(path_coarse_edges.values_list('id', flat=True))
    deleted_fine, _ = FineEdge.objects.filter(parent_coarse_edge_id__in=path_coarse_ids).delete()
    deleted_coarse, _ = path_coarse_edges.delete()
    counters['path_fine_edges_deleted'] = deleted_fine
    counters['path_coarse_edges_deleted'] = deleted_coarse

    capacity_cassettes = Device.objects.filter(
        site__slug=MAD_SITE_SLUG,
        device_type__slug='shuffle-cassette-2x2-mpo',
        local_context_data__has_key=CAPACITY_MARKER,
    )
    cassette_ids = list(capacity_cassettes.values_list('id', flat=True))
    device_ct = ContentType.objects.get_for_model(Device)
    front_ct = ContentType.objects.get_for_model(FrontPort)
    rear_ct = ContentType.objects.get_for_model(RearPort)
    front_port_ids = list(FrontPort.objects.filter(device_id__in=cassette_ids).values_list('id', flat=True))
    rear_port_ids = list(RearPort.objects.filter(device_id__in=cassette_ids).values_list('id', flat=True))

    plant_nodes = PlantNode.objects.filter(source_type=device_ct, source_id__in=cassette_ids)
    plant_node_ids = list(plant_nodes.values_list('id', flat=True))
    tp_ids = list(
        TerminationPoint.objects.filter(plant_node_id__in=plant_node_ids).values_list('id', flat=True)
    )
    deleted_aus, _ = AttachmentUnit.objects.filter(termination_point_id__in=tp_ids).delete()
    deleted_tps, _ = TerminationPoint.objects.filter(id__in=tp_ids).delete()
    deleted_nodes, _ = plant_nodes.delete()
    counters['capacity_attachment_units_deleted'] = deleted_aus
    counters['capacity_termination_points_deleted'] = deleted_tps
    counters['capacity_plant_nodes_deleted'] = deleted_nodes

    extra_tps = TerminationPoint.objects.filter(
        source_type__in=[front_ct, rear_ct],
        source_id__in=[*front_port_ids, *rear_port_ids],
    )
    extra_tp_ids = list(extra_tps.values_list('id', flat=True))
    deleted_extra_aus, _ = AttachmentUnit.objects.filter(termination_point_id__in=extra_tp_ids).delete()
    deleted_extra_tps, _ = extra_tps.delete()
    counters['capacity_port_attachment_units_deleted'] = deleted_extra_aus
    counters['capacity_port_termination_points_deleted'] = deleted_extra_tps

    DeviceBay.objects.filter(installed_device_id__in=cassette_ids).update(installed_device=None)
    deleted_devices, _ = capacity_cassettes.delete()
    counters['capacity_cassette_devices_deleted'] = deleted_devices

    print('Madison single-worksheet fiber path cleanup complete.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'path_marked_coarse_edges={CoarseEdge.objects.filter(metadata__has_key=PATH_MARKER).count()}')
    print(f'path_marked_fine_edges={FineEdge.objects.filter(metadata__has_key=PATH_MARKER).count()}')
    print(f"capacity_cassettes_remaining={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug='shuffle-cassette-2x2-mpo', local_context_data__has_key=CAPACITY_MARKER).count()}")
    print(f"shuffle_cassettes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug='shuffle-cassette-2x2-mpo').count()}")
    print(f"shuffle_cassette_front_ports={FrontPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug='shuffle-cassette-2x2-mpo').count()}")
    print(f"shuffle_cassette_rear_ports={RearPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug='shuffle-cassette-2x2-mpo').count()}")


main()
