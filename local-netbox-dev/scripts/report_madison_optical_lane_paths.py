from __future__ import annotations

from netbox_plant_graph.models import Fabric, FabricPlane, SignalLane
from netbox_plant_graph.services.graph.resolver import resolve_path
from netbox_plant_graph.services.graph_external_edges import resolve_attachment_unit


FABRIC_NAME = 'MAD-1 RoCE Fabric'

SAMPLE_PATHS = [
    {
        'plane_number': 1,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 1),
        'leaf_endpoint': ('mad1-a9-u12-13-sn5610', 'swp1', 1),
        'label': 'PL1 NIC0A',
    },
    {
        'plane_number': 2,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 2),
        'leaf_endpoint': ('mad1-a9-u14-15-sn5610', 'swp1', 1),
        'label': 'PL2 NIC0A',
    },
    {
        'plane_number': 3,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 1),
        'leaf_endpoint': ('mad1-a10-u12-13-sn5610', 'swp1', 1),
        'label': 'PL3 NIC0B',
    },
    {
        'plane_number': 4,
        'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 2),
        'leaf_endpoint': ('mad1-a10-u14-15-sn5610', 'swp1', 1),
        'label': 'PL4 NIC0B',
    },
]


def lane_for(attachment_unit, lane_index: int) -> SignalLane:
    return SignalLane.objects.select_related(
        'attachment_unit__termination_point__plant_node',
    ).get(attachment_unit=attachment_unit, lane_index=lane_index)


def step_label(step: dict) -> str:
    if step.get('endpoint_context'):
        return step['endpoint_context']
    for key in ('attachment_unit', 'termination_point', 'plant_node', 'parent_coarse_edge', 'owner_node', 'owner_edge'):
        nested = step.get(key)
        if nested and nested.get('endpoint_context'):
            return nested['endpoint_context']
        if nested and nested.get('display'):
            return nested['display']
    if step.get('display'):
        return step['display']
    if step.get('object') and step['object'].get('display'):
        return step['object']['display']
    return step.get('kind') or step.get('edge_type') or 'step'


def main() -> None:
    fabric = Fabric.objects.get(name=FABRIC_NAME)
    print('Madison optical lane path traces')
    print(f'fabric={fabric.name}')
    for sample in SAMPLE_PATHS:
        plane = FabricPlane.objects.get(fabric=fabric, plane_number=sample['plane_number'])
        source_au = resolve_attachment_unit(
            fabric=fabric,
            plant_node_name=sample['gb300_endpoint'][0],
            termination_point_name=sample['gb300_endpoint'][1],
            ordinal=sample['gb300_endpoint'][2],
        )
        destination_au = resolve_attachment_unit(
            fabric=fabric,
            plant_node_name=sample['leaf_endpoint'][0],
            termination_point_name=sample['leaf_endpoint'][1],
            ordinal=sample['leaf_endpoint'][2],
        )
        print('')
        print(f"plane={sample['plane_number']} label={sample['label']}")
        for lane_index in range(8):
            source_lane = lane_for(source_au, lane_index)
            destination_lane = lane_for(destination_au, lane_index)
            result = resolve_path(
                source=source_lane,
                destination=destination_lane,
                plane=plane,
                resolution='signal_lane',
            )
            status = 'FOUND' if result['path_found'] else 'MISSING'
            summary = result.get('summary') or {}
            print(
                f"  lane={lane_index + 1:02d} status={status} "
                f"coarse={summary.get('coarse_edges_crossed', 0)} "
                f"lane_maps={summary.get('lane_maps_crossed', 0)} "
                f"shuffle={summary.get('shuffle_modules_crossed', 0)}"
            )
            for idx, step in enumerate(result.get('path') or (), start=1):
                print(f'    {idx:02d}. {step_label(step)}')


main()
