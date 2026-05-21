from __future__ import annotations

from collections import Counter

from dcim.models import Interface
from netbox_plant_graph.models import Endpoint, OpticalLane, TransportChannel, TransportChannelPositionMap


FABRIC_SLUG = 'mad-1-roce-fabric'
MAD_SITE_SLUG = 'mad-1'


def main() -> None:
    osfp_endpoints = Endpoint.objects.filter(
        fabric__slug=FABRIC_SLUG,
        endpoint_kind='netbox_port',
        connector_kind='osfp',
        source_type__model='interface',
        source_id__isnull=False,
    )
    osfp_source_ids = list(osfp_endpoints.values_list('source_id', flat=True))
    child_interfaces = Interface.objects.filter(parent_id__in=osfp_source_ids)
    optical_lanes = OpticalLane.objects.filter(fabric__slug=FABRIC_SLUG, endpoint__in=osfp_endpoints)
    channels = TransportChannel.objects.filter(fabric__slug=FABRIC_SLUG, endpoint__in=osfp_endpoints)
    position_maps = TransportChannelPositionMap.objects.filter(channel__in=channels)

    counters = Counter()
    counters['osfp_parent_endpoints'] = osfp_endpoints.count()
    counters['osfp_parent_interfaces'] = len(set(osfp_source_ids))
    counters['child_subinterfaces'] = child_interfaces.count()
    counters['transport_channels'] = channels.count()
    counters['transport_channels_with_subinterface'] = channels.exclude(source_subinterface=None).count()
    counters['transport_channel_position_maps'] = position_maps.count()
    counters['optical_lanes'] = optical_lanes.count()
    counters['optical_lanes_with_channel'] = optical_lanes.exclude(channel=None).count()
    counters['optical_lanes_missing_channel'] = optical_lanes.filter(channel=None).count()

    expected_channels = counters['osfp_parent_endpoints'] * 4
    expected_position_maps = counters['osfp_parent_endpoints'] * 16
    expected_child_subinterfaces = counters['osfp_parent_interfaces'] * 4
    counters['expected_transport_channels'] = expected_channels
    counters['expected_transport_channel_position_maps'] = expected_position_maps
    counters['expected_child_subinterfaces'] = expected_child_subinterfaces
    counters['missing_transport_channels'] = max(expected_channels - counters['transport_channels'], 0)
    counters['missing_transport_channel_position_maps'] = max(expected_position_maps - counters['transport_channel_position_maps'], 0)
    counters['missing_child_subinterfaces'] = max(expected_child_subinterfaces - counters['child_subinterfaces'], 0)

    print('Madison transport channel coverage report')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    if (
        counters['missing_transport_channels']
        or counters['missing_transport_channel_position_maps']
        or counters['missing_child_subinterfaces']
        or counters['optical_lanes_missing_channel']
    ):
        print('coverage=INCOMPLETE')
    else:
        print('coverage=PASS')


main()
