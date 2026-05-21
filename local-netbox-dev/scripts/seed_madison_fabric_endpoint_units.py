from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

from django.db import transaction
from django.db.models import Q

from dcim.models import Device
from netbox_plant_graph.models import ConnectorPosition, Endpoint, FabricNode, OpticalLane, TransferMap


for candidate in (
    Path(__file__).resolve().parent if '__file__' in globals() else None,
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
):
    if candidate and candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break

from madison_v2_graph import (  # noqa: E402
    MAD_SITE_SLUG,
    ensure_fabric,
    ensure_surfaces_for_devices,
)


SOURCE_MARKER = 'madison_fabric_endpoint_units_v2'
PASSIVE_MPO_DEVICE_TYPE_SLUGS = {'shuffle-cassette-2x2-mpo'}


def target_devices():
    return (
        Device.objects.filter(site__slug=MAD_SITE_SLUG)
        .filter(Q(interfaces__type__icontains='osfp') | Q(device_type__slug__in=PASSIVE_MPO_DEVICE_TYPE_SLUGS))
        .select_related('role', 'device_type', 'rack', 'site')
        .distinct()
        .order_by('name')
    )


@transaction.atomic
def main() -> None:
    counters = Counter()
    fabric = ensure_fabric()
    devices = list(target_devices())
    ensure_surfaces_for_devices(fabric, devices, marker=SOURCE_MARKER, counters=counters)

    print('Madison fabric endpoint unit seeding complete for netbox_plant_graph v2.')
    print(f'target_devices={len(devices)}')
    for key, value in sorted(counters.items()):
        print(f'{key}={value}')
    print(f'fabric={fabric.name}')
    print(f'endpoint_fabric_nodes={FabricNode.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'endpoint_endpoints={Endpoint.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'endpoint_connector_positions={ConnectorPosition.objects.filter(endpoint__fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'endpoint_optical_lanes={OpticalLane.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')
    print(f'endpoint_transfer_maps={TransferMap.objects.filter(fabric=fabric, metadata__has_key=SOURCE_MARKER).count()}')


if __name__ == '__main__':
    main()
