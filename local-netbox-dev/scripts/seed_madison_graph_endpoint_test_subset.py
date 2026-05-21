from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
import sys

from django.db import transaction

from dcim.models import Device, FrontPort, Interface, RearPort
from netbox_plant_graph.models import FiberSegment, OpticalLane, TransferMap


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


SOURCE_MARKER = 'madison_graph_endpoint_test_subset_v2'
ACTIVE_ENDPOINT_DEVICE_TYPE_SLUGS = {
    'poweredge-xe9712-gb300-compute-tray',
    'sn5610',
}
SHUFFLE_RACKS = {'A9', 'A10'}
SAMPLE_PATH_KEY = 'madison-test-a2-gpu01-four-plane-leaf1-via-cassettes-v2'

SAMPLE_PATHS = [
    {
        'plane_number': 1,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 1),
        'cassette': 'mad1-a9-u16-shuffle-box-cassette-1.1',
        'leaf_endpoint': ('mad1-a9-u12-13-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL1',
    },
    {
        'plane_number': 2,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 2),
        'cassette': 'mad1-a9-u16-shuffle-box-cassette-1.2',
        'leaf_endpoint': ('mad1-a9-u14-15-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL2',
    },
    {
        'plane_number': 3,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 1),
        'cassette': 'mad1-a10-u16-shuffle-box-cassette-1.1',
        'leaf_endpoint': ('mad1-a10-u12-13-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL3',
    },
    {
        'plane_number': 4,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 2),
        'cassette': 'mad1-a10-u16-shuffle-box-cassette-1.2',
        'leaf_endpoint': ('mad1-a10-u14-15-sn5610', 'swp1', 1),
        'cassette_port': '01',
        'plane_label': 'PL4',
    },
]


def apply_enabled() -> bool:
    return os.environ.get('MADISON_GRAPH_TEST_APPLY') == '1'


def active_endpoint_devices():
    return (
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            local_context_data__madison_active_endpoint_test_subset=True,
            device_type__slug__in=ACTIVE_ENDPOINT_DEVICE_TYPE_SLUGS,
        )
        .select_related('device_type', 'role', 'rack', 'site', 'tenant')
        .order_by('name')
    )


def cassette_devices():
    return (
        Device.objects.filter(
            site__slug=MAD_SITE_SLUG,
            device_type__slug='shuffle-cassette-2x2-mpo',
            parent_bay__device__rack__name__in=SHUFFLE_RACKS,
        )
        .select_related('device_type', 'role', 'rack', 'site', 'tenant', 'parent_bay__device__rack')
        .order_by('name')
    )


def sample_segments() -> list[dict]:
    segments = []
    for path in SAMPLE_PATHS:
        cassette_port = path['cassette_port']
        metadata = {
            SOURCE_MARKER: True,
            'plane_number': path['plane_number'],
            'plane_label': path['plane_label'],
            'modeled_status': 'planned',
        }
        segments.extend(
            [
                {
                    'a': path['gb300_endpoint'],
                    'b': (path['cassette'], f'front-mpo-{cassette_port}', 1),
                    'segment_role': 'gb300_to_shuffle_mpo12_active_lanes',
                    'metadata': {**metadata, 'segment_kind': 'gb300_to_shuffle'},
                },
                {
                    'a': (path['cassette'], f'rear-mpo-{cassette_port}', 1),
                    'b': path['leaf_endpoint'],
                    'segment_role': 'shuffle_to_leaf_mpo12_active_lanes',
                    'metadata': {**metadata, 'segment_kind': 'shuffle_to_leaf'},
                },
            ]
        )
    return segments


def print_dry_run() -> None:
    active = list(active_endpoint_devices())
    cassettes = list(cassette_devices())
    active_osfps = Interface.objects.filter(device__in=active, type__icontains='osfp').count()
    cassette_ports = FrontPort.objects.filter(device__in=cassettes).count() + RearPort.objects.filter(device__in=cassettes).count()
    print('Madison graph endpoint test subset dry run for netbox_plant_graph v2')
    print(f'active_endpoint_devices={len(active)}')
    print(f'active_osfp_parent_endpoints={active_osfps}')
    print(f'active_osfp_child_mpo_endpoints={active_osfps * 2}')
    print(f'shuffle_racks={sorted(SHUFFLE_RACKS)}')
    print(f'shuffle_cassettes={len(cassettes)}')
    print(f'cassette_mpo_endpoints={cassette_ports}')
    print(f'cassette_transfer_maps={len(cassettes) * 2 * 2 * 2 * 8}')
    print(f'sample_path_key={SAMPLE_PATH_KEY}')
    print(f'sample_path_segments={len(SAMPLE_PATHS) * 2}')


def main() -> None:
    print_dry_run()
    if not apply_enabled():
        print('apply=false; set MADISON_GRAPH_TEST_APPLY=1 to stamp graph endpoint surfaces')
        return

    fabric = ensure_fabric()
    counters = Counter()
    with transaction.atomic():
        ensure_surfaces_for_devices(fabric, list(active_endpoint_devices()), marker=SOURCE_MARKER, counters=counters)
        ensure_surfaces_for_devices(fabric, list(cassette_devices()), marker=SOURCE_MARKER, counters=counters)
        counters.update(stamp_path_segments(fabric, marker=SOURCE_MARKER, path_key=SAMPLE_PATH_KEY, segments=sample_segments()))

    print('Madison graph endpoint test subset seed complete for netbox_plant_graph v2.')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print(f'fabric={fabric.name}')
    print(f'fiber_segments={FiberSegment.objects.filter(fabric=fabric, metadata__path_key=SAMPLE_PATH_KEY).count()}')
    print(f'optical_lanes={OpticalLane.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'transfer_maps={TransferMap.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')


main()
