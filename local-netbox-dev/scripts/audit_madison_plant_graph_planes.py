from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import os

from django.db.models import Count

from netbox_plant_graph.models import (
    ConnectorPosition,
    Endpoint,
    Fabric,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StrandTermination,
    TransferMap,
    TransportChannel,
    TransportChannelPositionMap,
)


FABRIC_NAME = os.environ.get('MADISON_FABRIC_NAME', 'GS001 RoCE Fabric')
EXPECTED_PLANE_COUNT = int(os.environ.get('MADISON_EXPECTED_PLANE_COUNT', '4'))


def metadata_value(obj, key, default=None):
    metadata = obj.metadata or {}
    return metadata.get(key, default)


def plane_number_set(fabric):
    return set(Plane.objects.filter(fabric=fabric).values_list('plane_number', flat=True))


def position_numbers_for_segment(segment):
    values = metadata_value(segment, 'position_numbers', [])
    try:
        return sorted({int(value) for value in values})
    except (TypeError, ValueError):
        return []


def position_plane_numbers_for_segment(segment):
    values = metadata_value(segment, 'position_plane_numbers', {})
    try:
        return {
            int(position_number): int(plane_number)
            for position_number, plane_number in dict(values).items()
            if plane_number is not None
        }
    except (TypeError, ValueError):
        return {}


def optical_lanes_for_endpoint_positions(fabric, endpoint, position_numbers):
    return (
        OpticalLane.objects.filter(
            fabric=fabric,
            local_mpo_endpoint=endpoint,
            local_mpo_position__position_number__in=position_numbers,
        )
        .select_related('plane', 'local_mpo_position')
        .order_by('local_mpo_position__position_number', 'lane_index', 'direction')
    )


def audit_planes(fabric, counters, findings):
    planes = list(Plane.objects.filter(fabric=fabric).order_by('plane_number'))
    numbers = [plane.plane_number for plane in planes]
    expected = list(range(1, EXPECTED_PLANE_COUNT + 1))

    counters['expected_plane_count'] = EXPECTED_PLANE_COUNT
    counters['fabric_planes'] = len(planes)
    if numbers != expected:
        findings.append(f'Fabric planes are {numbers}; expected {expected}.')

    lane_counts = Counter(
        OpticalLane.objects.filter(fabric=fabric, plane__isnull=False).values_list('plane__plane_number', flat=True)
    )
    counters['optical_lanes_with_plane'] = sum(lane_counts.values())
    counters['optical_lanes_without_plane'] = OpticalLane.objects.filter(fabric=fabric, plane__isnull=True).count()
    for plane_number in expected:
        counters[f'plane_{plane_number}_optical_lanes'] = lane_counts[plane_number]
    empty_planes = [plane_number for plane_number in expected if lane_counts[plane_number] == 0]
    counters['empty_planes'] = len(empty_planes)
    if empty_planes:
        findings.append(f'Planes with zero assigned OpticalLane rows: {empty_planes}.')


def audit_fiber_segments(fabric, counters, findings):
    valid_planes = plane_number_set(fabric)
    segments = (
        FiberSegment.objects.filter(fabric=fabric)
        .select_related('a_endpoint', 'b_endpoint')
        .prefetch_related('strands__terminations')
        .order_by('name', 'pk')
    )
    counters['fiber_segments'] = segments.count()
    managed_segments = 0
    gb300_source_destinations = defaultdict(set)

    for segment in segments:
        metadata = segment.metadata or {}
        if metadata.get('path_key'):
            managed_segments += 1
        plane_number = metadata.get('plane_number')
        position_plane_numbers = position_plane_numbers_for_segment(segment)
        if metadata.get('path_key') and plane_number is None and not position_plane_numbers:
            counters['managed_fiber_segments_missing_plane'] += 1
            findings.append(f'FiberSegment {segment.name} has path_key but no plane_number or position_plane_numbers metadata.')
        if plane_number is not None and int(plane_number) not in valid_planes:
            counters['fiber_segments_invalid_plane'] += 1
            findings.append(f'FiberSegment {segment.name} references missing plane {plane_number}.')
        invalid_position_planes = sorted(
            {
                plane
                for plane in position_plane_numbers.values()
                if plane not in valid_planes
            }
        )
        if invalid_position_planes:
            counters['fiber_segments_invalid_position_plane'] += 1
            findings.append(f'FiberSegment {segment.name} references missing position planes {invalid_position_planes}.')

        position_numbers = position_numbers_for_segment(segment)
        if not position_numbers:
            counters['fiber_segments_missing_position_numbers'] += 1
            findings.append(f'FiberSegment {segment.name} has no valid position_numbers metadata.')
            continue

        strands = list(segment.strands.all())
        counters['fiber_strands'] += len(strands)
        if metadata.get('path_key', '').startswith('madison-') and metadata.get('segment_kind') in {'gb300_to_shuffle', 'shuffle_to_leaf'}:
            counters['madison_mpo_jumper_segments'] += 1
            if len(strands) != 8:
                counters['madison_mpo_jumper_segments_not_8_strands'] += 1
                findings.append(f'Madison MPO jumper {segment.name} has {len(strands)} strands; expected 8.')
            if metadata.get('segment_kind') == 'gb300_to_shuffle':
                gb300_source_destinations[(metadata.get('path_key'), segment.a_endpoint_id)].add(segment.b_endpoint_id)
        if len(strands) != len(position_numbers):
            counters['fiber_segments_strand_count_mismatch'] += 1
            findings.append(
                f'FiberSegment {segment.name} has {len(strands)} strands for '
                f'{len(position_numbers)} position_numbers.'
            )

        for strand in strands:
            terminations = list(strand.terminations.all())
            counters['strand_terminations'] += len(terminations)
            if len(terminations) != 2:
                counters['strands_not_two_terminated'] += 1
                findings.append(f'FiberStrand {strand} has {len(terminations)} terminations; expected 2.')
            for termination in terminations:
                if termination.mpo_endpoint_id not in {segment.a_endpoint_id, segment.b_endpoint_id}:
                    counters['strand_terminations_wrong_endpoint'] += 1
                    findings.append(f'StrandTermination {termination.pk} is not on either endpoint of {segment.name}.')
                if termination.mpo_position.endpoint_id != termination.mpo_endpoint_id:
                    counters['strand_terminations_position_endpoint_mismatch'] += 1
                    findings.append(f'StrandTermination {termination.pk} position is not on its MPO endpoint.')

        expected_plane_by_position = {}
        if position_plane_numbers:
            expected_plane_by_position = {
                position: plane
                for position, plane in position_plane_numbers.items()
                if position in set(position_numbers)
            }
            missing_position_plane_numbers = sorted(set(position_numbers) - set(expected_plane_by_position))
            if missing_position_plane_numbers:
                counters['fiber_segments_incomplete_position_plane_map'] += 1
                findings.append(
                    f'FiberSegment {segment.name} has no plane assignment for positions '
                    f'{missing_position_plane_numbers}.'
                )
        elif plane_number is not None:
            expected_plane_by_position = {position: int(plane_number) for position in position_numbers}

        if expected_plane_by_position:
            segment_lane_count = 0
            for endpoint in (segment.a_endpoint, segment.b_endpoint):
                lane_qs = optical_lanes_for_endpoint_positions(fabric, endpoint, position_numbers)
                lane_count = lane_qs.count()
                segment_lane_count += lane_count
                counters['fiber_segment_endpoint_position_lanes_checked'] += lane_count
                if lane_count == 0:
                    counters['fiber_segment_passive_or_unlaned_endpoints'] += 1
                    continue
                mismatched = [
                    lane.pk
                    for lane in lane_qs
                    if (
                        lane.local_mpo_position.position_number in expected_plane_by_position
                        and (
                            lane.plane_id is None
                            or lane.plane.plane_number != expected_plane_by_position[lane.local_mpo_position.position_number]
                        )
                    )
                ]
                if mismatched:
                    counters['fiber_segment_endpoint_lanes_plane_mismatch'] += len(mismatched)
                    findings.append(
                        f'FiberSegment {segment.name} endpoint {endpoint.address} has optical lanes '
                        f'outside expected per-position plane assignment: {mismatched[:10]}.'
                    )
            if segment_lane_count < len(expected_plane_by_position):
                counters['fiber_segments_without_active_lanes'] += 1
                findings.append(
                    f'FiberSegment {segment.name} has only {segment_lane_count} lane rows '
                    f'for {len(expected_plane_by_position)} plane-assigned positions across both endpoints.'
                )

    split_source_mpos = {
        key: destinations
        for key, destinations in gb300_source_destinations.items()
        if len(destinations) != 1
    }
    counters['madison_gb300_source_mpos'] = len(gb300_source_destinations)
    counters['madison_gb300_source_mpos_split_to_multiple_shuffle_fronts'] = len(split_source_mpos)
    if split_source_mpos:
        samples = sorted(split_source_mpos)[:10]
        findings.append(f'Madison GB300 source MPOs split to multiple shuffle front MPOs: {samples}.')

    counters['managed_fiber_segments'] = managed_segments


def audit_transfer_maps(fabric, counters, findings):
    maps = (
        TransferMap.objects.filter(fabric=fabric)
        .select_related('src_position__endpoint', 'dst_position__endpoint')
        .order_by('pk')
    )
    counters['transfer_maps'] = maps.count()
    for transfer_map in maps:
        if transfer_map.src_position_id == transfer_map.dst_position_id:
            counters['transfer_maps_self_loop'] += 1
            findings.append(f'TransferMap {transfer_map.pk} maps a position to itself.')
        if transfer_map.src_position.endpoint.fabric_id != fabric.pk:
            counters['transfer_maps_source_wrong_fabric'] += 1
            findings.append(f'TransferMap {transfer_map.pk} source position is outside the fabric.')
        if transfer_map.dst_position.endpoint.fabric_id != fabric.pk:
            counters['transfer_maps_destination_wrong_fabric'] += 1
            findings.append(f'TransferMap {transfer_map.pk} destination position is outside the fabric.')


def audit_transport_channels(fabric, counters, findings):
    channels = TransportChannel.objects.filter(fabric=fabric).select_related('endpoint', 'plane')
    counters['transport_channels'] = channels.count()
    counters['transport_channels_with_plane'] = channels.filter(plane__isnull=False).count()
    counters['transport_channel_position_maps'] = TransportChannelPositionMap.objects.filter(channel__fabric=fabric).count()

    for channel in channels:
        if channel.endpoint.fabric_id != fabric.pk:
            counters['transport_channels_endpoint_wrong_fabric'] += 1
            findings.append(f'TransportChannel {channel.pk} endpoint is outside the fabric.')
        if channel.plane_id and channel.plane.fabric_id != fabric.pk:
            counters['transport_channels_plane_wrong_fabric'] += 1
            findings.append(f'TransportChannel {channel.pk} plane is outside the fabric.')

    orphan_maps = (
        TransportChannelPositionMap.objects.filter(channel__fabric=fabric)
        .exclude(mpo_endpoint__fabric=fabric)
        .count()
    )
    counters['transport_channel_position_maps_wrong_fabric'] = orphan_maps
    if orphan_maps:
        findings.append(f'{orphan_maps} TransportChannelPositionMap rows point outside the fabric.')


def audit_cardinality(fabric, counters, findings):
    counters['fabric_nodes'] = fabric.nodes.count()
    counters['endpoints'] = Endpoint.objects.filter(fabric=fabric).count()
    counters['connector_positions'] = ConnectorPosition.objects.filter(endpoint__fabric=fabric).count()
    counters['optical_lanes'] = OpticalLane.objects.filter(fabric=fabric).count()

    duplicate_positions = (
        ConnectorPosition.objects.filter(endpoint__fabric=fabric)
        .values('endpoint_id', 'position_number')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .count()
    )
    counters['duplicate_connector_positions'] = duplicate_positions
    if duplicate_positions:
        findings.append(f'{duplicate_positions} endpoint/position pairs have duplicate ConnectorPosition rows.')

    bad_lane_wavelengths = OpticalLane.objects.filter(fabric=fabric).exclude(wavelength_nm=Decimal('1310.000')).count()
    counters['non_1310nm_optical_lanes'] = bad_lane_wavelengths
    if bad_lane_wavelengths:
        findings.append(f'{bad_lane_wavelengths} OpticalLane rows are not at 1310nm.')


def audit():
    counters = Counter()
    findings = []
    fabric = Fabric.objects.get(name=FABRIC_NAME)

    counters['fabric_id'] = fabric.pk
    audit_cardinality(fabric, counters, findings)
    audit_planes(fabric, counters, findings)
    audit_fiber_segments(fabric, counters, findings)
    audit_transfer_maps(fabric, counters, findings)
    audit_transport_channels(fabric, counters, findings)

    return counters, findings


def main() -> None:
    counters, findings = audit()
    print('Madison plant graph v2 plane audit')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    if findings:
        print('findings=FAIL')
        for finding in findings[:100]:
            print(f'- {finding}')
        if len(findings) > 100:
            print(f'- ... {len(findings) - 100} additional findings suppressed')
        raise SystemExit(1)
    print('findings=PASS')


main()
