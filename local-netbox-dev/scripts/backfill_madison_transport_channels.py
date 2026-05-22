from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
import sys

from django.db import transaction

from dcim.models import Device
from netbox_plant_graph.models import Endpoint, OpticalLane, TransportChannel, TransportChannelPositionMap


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_v2_graph import ensure_surfaces_for_devices, get_fabric  # noqa: E402


FABRIC_SLUG = 'gs001-roce-fabric'
MAD_SITE_SLUG = 'gs001'
SOURCE_MARKER = 'madison_transport_channel_backfill_v1'


def apply_enabled() -> bool:
    return os.environ.get('MADISON_TRANSPORT_CHANNEL_BACKFILL_APPLY') == '1'


def devices_with_osfp_endpoint_surfaces() -> list[Device]:
    source_ids = list(
        Endpoint.objects.filter(
            fabric__slug=FABRIC_SLUG,
            endpoint_kind='netbox_port',
            connector_kind='osfp',
            source_type__model='interface',
            source_id__isnull=False,
        ).values_list('source_id', flat=True)
    )
    return list(
        Device.objects.filter(site__slug=MAD_SITE_SLUG, interfaces__pk__in=source_ids)
        .distinct()
        .order_by('name')
    )


def coverage_counters() -> Counter:
    counters = Counter()
    osfp_endpoints = Endpoint.objects.filter(
        fabric__slug=FABRIC_SLUG,
        endpoint_kind='netbox_port',
        connector_kind='osfp',
        source_type__model='interface',
        source_id__isnull=False,
    )
    osfp_source_ids = set(osfp_endpoints.values_list('source_id', flat=True))
    channels = TransportChannel.objects.filter(fabric__slug=FABRIC_SLUG, endpoint__in=osfp_endpoints)
    position_maps = TransportChannelPositionMap.objects.filter(channel__in=channels)
    lanes = OpticalLane.objects.filter(fabric__slug=FABRIC_SLUG, endpoint__in=osfp_endpoints)
    counters['osfp_parent_endpoints'] = osfp_endpoints.count()
    counters['osfp_parent_interfaces'] = len(osfp_source_ids)
    counters['transport_channels'] = channels.count()
    counters['transport_channels_with_subinterface'] = channels.exclude(source_subinterface=None).count()
    counters['transport_channel_position_maps'] = position_maps.count()
    counters['optical_lanes'] = lanes.count()
    counters['optical_lanes_with_channel'] = lanes.exclude(channel=None).count()
    counters['optical_lanes_missing_channel'] = lanes.filter(channel=None).count()
    counters['expected_transport_channels'] = counters['osfp_parent_endpoints'] * 4
    counters['expected_transport_channel_position_maps'] = counters['osfp_parent_endpoints'] * 16
    return counters


def print_counters(prefix: str, counters: Counter) -> None:
    print(prefix)
    for key in sorted(counters):
        print(f'{key}={counters[key]}')


def main() -> None:
    devices = devices_with_osfp_endpoint_surfaces()
    before = coverage_counters()
    print('Madison transport channel backfill')
    print(f'target_devices={len(devices)}')
    print_counters('before:', before)
    if not apply_enabled():
        print('apply=false; set MADISON_TRANSPORT_CHANNEL_BACKFILL_APPLY=1 to create channel/sub-interface bindings.')
        return

    fabric = get_fabric()
    counters = Counter()
    with transaction.atomic():
        ensure_surfaces_for_devices(fabric, devices, marker=SOURCE_MARKER, counters=counters)
    after = coverage_counters()
    print('backfill write counters:')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    print_counters('after:', after)


main()
