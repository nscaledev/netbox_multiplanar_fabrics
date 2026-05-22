from __future__ import annotations

from collections import Counter, defaultdict

from django.contrib.contenttypes.models import ContentType

from netbox_plant_graph.models import (
    AttachmentUnit,
    CoarseEdge,
    Fabric,
    FabricPlane,
    FineEdge,
    PlaneMembership,
    SignalLane,
)


FABRIC_NAME = 'GS001 RoCE Fabric'


def managed(metadata: dict | None) -> bool:
    return bool((metadata or {}).get('graph_external_edge_stamp'))


def native_attachment_memberships(fabric: Fabric) -> dict[int, set[int]]:
    au_ct = ContentType.objects.get_for_model(AttachmentUnit)
    memberships = defaultdict(set)
    for membership in PlaneMembership.objects.filter(
        plane__fabric=fabric,
        member_type=au_ct,
        membership_role='native',
    ).select_related('plane'):
        memberships[membership.member_id].add(membership.plane.plane_number)
    return memberships


def native_signal_lane_memberships(fabric: Fabric) -> dict[int, set[int]]:
    signal_lane_type = ContentType.objects.get_for_model(SignalLane)
    memberships = defaultdict(set)
    for membership in PlaneMembership.objects.filter(
        plane__fabric=fabric,
        member_type=signal_lane_type,
        membership_role='native',
    ).select_related('plane'):
        memberships[membership.member_id].add(membership.plane.plane_number)
    return memberships


def audit() -> tuple[Counter, list[str]]:
    counters = Counter()
    findings = []
    fabric = Fabric.objects.get(name=FABRIC_NAME)
    planes = list(FabricPlane.objects.filter(fabric=fabric).order_by('plane_number'))
    memberships = native_attachment_memberships(fabric)
    lane_memberships = native_signal_lane_memberships(fabric)

    counters['expected_plane_count'] = fabric.expected_plane_count
    counters['fabric_planes'] = len(planes)
    counters['attachment_unit_plane_memberships'] = sum(len(value) for value in memberships.values())
    counters['signal_lane_plane_memberships'] = sum(len(value) for value in lane_memberships.values())
    counters['plane_memberships'] = counters['attachment_unit_plane_memberships'] + counters['signal_lane_plane_memberships']
    if len(planes) != fabric.expected_plane_count:
        findings.append(
            f'Fabric has expected_plane_count={fabric.expected_plane_count} '
            f'but {len(planes)} concrete FabricPlane rows.'
        )

    plane_membership_counts = Counter()
    for plane_numbers in memberships.values():
        for plane_number in plane_numbers:
            plane_membership_counts[plane_number] += 1
    for plane_numbers in lane_memberships.values():
        for plane_number in plane_numbers:
            plane_membership_counts[plane_number] += 1
    for plane in planes:
        counters[f'plane_{plane.plane_number}_memberships'] = plane_membership_counts[plane.plane_number]

    empty_planes = [plane.plane_number for plane in planes if plane_membership_counts[plane.plane_number] == 0]
    counters['empty_planes'] = len(empty_planes)
    if empty_planes:
        findings.append(f'Planes with zero native AttachmentUnit memberships: {empty_planes}.')

    multi_plane_aus = {
        au_id: sorted(plane_numbers)
        for au_id, plane_numbers in memberships.items()
        if len(plane_numbers) > 1
    }
    counters['multi_plane_attachment_units'] = len(multi_plane_aus)
    for au_id, plane_numbers in sorted(multi_plane_aus.items())[:10]:
        findings.append(f'AttachmentUnit id={au_id} has native memberships in planes {plane_numbers}.')

    managed_coarse_edges = [edge for edge in CoarseEdge.objects.all() if managed(edge.metadata)]
    managed_fine_edges = [edge for edge in FineEdge.objects.all() if managed(edge.metadata)]
    counters['managed_coarse_edges'] = len(managed_coarse_edges)
    counters['managed_fine_edges'] = len(managed_fine_edges)

    plane_blind_coarse = [edge.pk for edge in managed_coarse_edges if not (edge.metadata or {}).get('plane_number')]
    plane_blind_fine = [edge.pk for edge in managed_fine_edges if not (edge.metadata or {}).get('plane_number')]
    counters['plane_blind_coarse_edges'] = len(plane_blind_coarse)
    counters['plane_blind_fine_edges'] = len(plane_blind_fine)
    if plane_blind_coarse:
        findings.append(f'Managed CoarseEdges missing plane_number metadata: {plane_blind_coarse[:10]}.')
    if plane_blind_fine:
        findings.append(f'Managed FineEdges missing plane_number metadata: {plane_blind_fine[:10]}.')

    for edge in managed_fine_edges:
        metadata = edge.metadata or {}
        metadata_plane = metadata.get('plane_number')
        if edge.granularity == 'signal_lane' and edge.a_lane_id and edge.b_lane_id:
            a_planes = lane_memberships.get(edge.a_lane_id, set()) or memberships.get(edge.a_au_id, set())
            b_planes = lane_memberships.get(edge.b_lane_id, set()) or memberships.get(edge.b_au_id, set())
        else:
            a_planes = memberships.get(edge.a_au_id, set())
            b_planes = memberships.get(edge.b_au_id, set())
            if metadata.get('plane_membership_granularity') == 'signal_lane' and not (a_planes or b_planes):
                counters['attachment_fine_edges_delegated_to_signal_lane_memberships'] += 1
                continue
        shared = a_planes & b_planes
        if len(shared) != 1:
            counters['fine_edges_without_exactly_one_shared_plane'] += 1
            findings.append(
                f'FineEdge id={edge.pk} endpoints do not share exactly one native plane '
                f'(a={sorted(a_planes)}, b={sorted(b_planes)}).'
            )
            continue
        shared_plane = next(iter(shared))
        if metadata_plane != shared_plane:
            counters['fine_edges_with_plane_metadata_mismatch'] += 1
            findings.append(
                f'FineEdge id={edge.pk} metadata plane_number={metadata_plane!r} '
                f'but endpoint memberships resolve to plane {shared_plane}.'
            )
        if edge.granularity == 'signal_lane':
            counters['managed_signal_lane_fine_edges'] += 1
            if edge.a_lane_id is None or edge.b_lane_id is None:
                counters['signal_lane_edges_missing_lane_refs'] += 1
                findings.append(f'Signal-lane FineEdge id={edge.pk} is missing a_lane or b_lane.')
            elif edge.a_lane.lane_index != edge.b_lane.lane_index:
                counters['signal_lane_edges_lane_index_mismatch'] += 1
                findings.append(
                    f'Signal-lane FineEdge id={edge.pk} connects lane '
                    f'{edge.a_lane.lane_index} to lane {edge.b_lane.lane_index}.'
                )
            elif edge.a_lane.wavelength_nm != edge.b_lane.wavelength_nm:
                counters['optical_lane_edges_wavelength_mismatch'] += 1
                findings.append(
                    f'Optical-lane FineEdge id={edge.pk} connects '
                    f'{edge.a_lane.wavelength_nm}nm to {edge.b_lane.wavelength_nm}nm.'
                )
            else:
                counters[f'optical_lane_edges_{edge.a_lane.wavelength_nm}nm'] += 1

    return counters, findings


def main() -> None:
    counters, findings = audit()
    print('Madison plant graph plane audit')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    if findings:
        print('findings=FAIL')
        for finding in findings:
            print(f'- {finding}')
        raise SystemExit(1)
    print('findings=PASS')


main()
