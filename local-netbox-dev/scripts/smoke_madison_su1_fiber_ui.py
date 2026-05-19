from __future__ import annotations

from collections import Counter
from html import unescape
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from netbox_plant_graph.models import CoarseEdge, FineEdge


PATH_KEY = 'madison-su1-leaf16-four-plane-lane-aware-v1'


def page_text(response) -> str:
    return unescape(response.content.decode(response.charset or 'utf-8', errors='replace'))


def assert_contains(text: str, snippets: list[str], *, label: str, findings: list[str]) -> None:
    missing = [snippet for snippet in snippets if snippet not in text]
    if missing:
        findings.append(f'{label} missing snippets: {missing}')


def client() -> Client:
    User = get_user_model()
    user = User.objects.filter(username='admin').first() or User.objects.filter(is_superuser=True).first()
    if user is None:
        raise RuntimeError('No admin/superuser account exists for UI smoke testing.')
    test_client = Client(HTTP_HOST='localhost')
    test_client.force_login(user)
    return test_client


def lane_edge_sample_for_plane(plane_number: int) -> dict:
    leaf_edges = (
        FineEdge.objects.filter(
            parent_coarse_edge__metadata__path_key=PATH_KEY,
            parent_coarse_edge__metadata__segment_kind='shuffle_to_leaf',
            parent_coarse_edge__metadata__plane_number=plane_number,
            granularity='signal_lane',
        )
        .select_related(
            'a_lane',
            'b_lane',
            'a_au__termination_point__plant_node',
            'b_au__termination_point__plant_node',
            'parent_coarse_edge',
        )
        .order_by('parent_coarse_edge__metadata__sequence_index', 'metadata__lane_index', 'pk')
    )
    leaf_edge = leaf_edges.first()
    if leaf_edge is None:
        raise RuntimeError(f'No shuffle-to-leaf signal-lane edge found for plane {plane_number}.')
    metadata = leaf_edge.parent_coarse_edge.metadata or {}
    gb_edge = (
        FineEdge.objects.filter(
            parent_coarse_edge__metadata__path_key=PATH_KEY,
            parent_coarse_edge__metadata__segment_kind='gb300_to_shuffle',
            parent_coarse_edge__metadata__plane_number=plane_number,
            parent_coarse_edge__metadata__sequence_index=metadata['sequence_index'],
            parent_coarse_edge__metadata__side=metadata['side'],
            parent_coarse_edge__metadata__nic_index_zero=metadata['nic_index_zero'],
            metadata__lane_index=leaf_edge.metadata['lane_index'],
            granularity='signal_lane',
        )
        .select_related(
            'a_lane',
            'b_lane',
            'a_au__termination_point__plant_node',
            'b_au__termination_point__plant_node',
            'parent_coarse_edge',
        )
        .order_by('pk')
        .first()
    )
    if gb_edge is None:
        raise RuntimeError(f'No matching GB300-to-shuffle signal-lane edge found for plane {plane_number}.')
    return {
        'plane': plane_number,
        'lane_index': leaf_edge.metadata['lane_index'],
        'gb_lane': gb_edge.a_lane,
        'cassette_front_lane': gb_edge.b_lane,
        'cassette_rear_lane': leaf_edge.a_lane,
        'leaf_lane': leaf_edge.b_lane,
        'gb_au': gb_edge.a_au,
        'cassette_front_au': gb_edge.b_au,
        'cassette_rear_au': leaf_edge.a_au,
        'leaf_au': leaf_edge.b_au,
        'metadata': metadata,
    }


def get_page(test_client: Client, path: str, *, label: str, findings: list[str]):
    response = test_client.get(path)
    if response.status_code != 200:
        findings.append(f'{label} returned HTTP {response.status_code} for {path}')
    return response, page_text(response)


def smoke_plane(test_client: Client, plane_number: int, counters: Counter, findings: list[str]) -> None:
    sample = lane_edge_sample_for_plane(plane_number)
    metadata = sample['metadata']
    snippets = [
        metadata['gb300_device'],
        metadata['leaf_device'],
        metadata['shuffle_cassette'],
        metadata['gb300_interface'],
        metadata['leaf_interface'],
        f'plane: {plane_number}',
        '1310nm',
    ]

    resolver_query = urlencode(
        {
            'source_registry_key': 'signallane',
            'source_id': sample['gb_lane'].pk,
            'destination_registry_key': 'signallane',
            'destination_id': sample['leaf_lane'].pk,
            'resolution': 'signal_lane',
            'max_depth': 128,
        }
    )
    resolver_path = f"{reverse('plugins:netbox_plant_graph:path_resolver')}?{resolver_query}"
    _, resolver_text = get_page(test_client, resolver_path, label=f'plane {plane_number} Path Resolver', findings=findings)
    assert_contains(
        resolver_text,
        ['Path Resolver', 'Path Found', 'Yes', 'Path Steps', *snippets],
        label=f'plane {plane_number} Path Resolver',
        findings=findings,
    )

    workspace_query = urlencode(
        {
            'target_registry_key': 'attachmentunit',
            'target_id': sample['gb_au'].pk,
            'group_by': 'path',
            'mode': 'grouped',
            'path_lane_index': sample['gb_lane'].lane_index,
            'plane_id': plane_number,
            'focus': 'paths',
        }
    )
    workspace_path = f"{reverse('plugins:netbox_plant_graph:lane_workspace')}?{workspace_query}"
    _, workspace_text = get_page(test_client, workspace_path, label=f'plane {plane_number} Lane Workspace', findings=findings)
    assert_contains(
        workspace_text,
        [
            'Lane Workspace',
            'Representative Path Details',
            'Path Found',
            'Yes',
            metadata['gb300_device'],
            metadata['leaf_device'],
            metadata['shuffle_cassette'],
            f'plane: {plane_number}',
            '1310nm',
        ],
        label=f'plane {plane_number} Lane Workspace',
        findings=findings,
    )

    drilldown_query = urlencode(
        {
            'target_registry_key': 'attachmentunit',
            'target_id': sample['gb_au'].pk,
            'lane_index': sample['gb_lane'].lane_index,
        }
    )
    drilldown_path = f"{reverse('plugins:netbox_plant_graph:lane_drilldown')}?{drilldown_query}"
    _, drilldown_text = get_page(test_client, drilldown_path, label=f'plane {plane_number} Lane Drilldown', findings=findings)
    assert_contains(
        drilldown_text,
        ['Lane Drilldown', metadata['gb300_device'], metadata['gb300_interface'], f'plane: {plane_number}', '1310nm'],
        label=f'plane {plane_number} Lane Drilldown',
        findings=findings,
    )

    print(
        f"plane={plane_number} lane={sample['lane_index'] + 1} "
        f"{metadata['gb300_device']}:{metadata['gb300_interface']}:mpo-{metadata['gb300_mpo']} "
        f"-> {metadata['shuffle_cassette']}:mpo-{metadata['shuffle_mpo']} "
        f"-> {metadata['leaf_device']}:{metadata['leaf_interface']}:mpo-{metadata['leaf_mpo']}"
    )
    counters['planes_checked'] += 1
    counters['ui_pages_checked'] += 3


def main() -> None:
    counters = Counter()
    findings = []
    coarse_edges = CoarseEdge.objects.filter(metadata__path_key=PATH_KEY).count()
    fine_edges = FineEdge.objects.filter(metadata__path_key=PATH_KEY).count()
    print('Madison SU1 fiber UI smoke')
    print(f'path_key={PATH_KEY}')
    print(f'coarse_edges={coarse_edges}')
    print(f'fine_edges={fine_edges}')
    test_client = client()
    for plane_number in (1, 2, 3, 4):
        smoke_plane(test_client, plane_number, counters, findings)
    for key in sorted(counters):
        print(f'{key}={counters[key]}')
    if findings:
        print('findings=FAIL')
        for finding in findings:
            print(f'- {finding}')
        raise SystemExit(1)
    print('findings=PASS')


main()
