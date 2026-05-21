from __future__ import annotations

from pathlib import Path
import sys

from netbox_plant_graph.services.resolver import resolve_optical_lane_path


for candidate in (
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path(globals().get('__file__', '.')).resolve().parent,
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from madison_v2_graph import (  # noqa: E402
    get_fabric,
    mpo_endpoint_for_device_port,
    optical_lane_for,
)


SAMPLE_PATHS = [
    {'plane_number': 1, 'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 1), 'leaf_endpoint': ('mad1-a9-u12-13-sn5610', 'swp1', 1), 'label': 'PL1 NIC0A'},
    {'plane_number': 2, 'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp1', 2), 'leaf_endpoint': ('mad1-a9-u14-15-sn5610', 'swp1', 1), 'label': 'PL2 NIC0A'},
    {'plane_number': 3, 'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 1), 'leaf_endpoint': ('mad1-a10-u12-13-sn5610', 'swp1', 1), 'label': 'PL3 NIC0B'},
    {'plane_number': 4, 'gb300_endpoint': ('mad1-a2-u11-poweredge-xe9712-gb300-compute-tray', 'osfp2', 2), 'leaf_endpoint': ('mad1-a10-u14-15-sn5610', 'swp1', 1), 'label': 'PL4 NIC0B'},
]


def step_label(step) -> str:
    display = getattr(step, 'label', '') or getattr(step, 'display', '')
    object_type = getattr(step, 'object_type', '')
    object_id = getattr(step, 'object_id', '')
    return display or f'{object_type}:{object_id}'


def main() -> None:
    fabric = get_fabric()
    print('Madison optical lane path traces for netbox_plant_graph v2')
    print(f'fabric={fabric.name}')
    for sample in SAMPLE_PATHS:
        print('')
        print(f"plane={sample['plane_number']} label={sample['label']}")
        source_parent = mpo_endpoint_for_device_port(fabric, *sample['gb300_endpoint']).parent
        destination_parent = mpo_endpoint_for_device_port(fabric, *sample['leaf_endpoint']).parent
        for position_number in range(1, 9):
            source_lane = optical_lane_for(source_parent, mpo_index=sample['gb300_endpoint'][2], position_number=position_number, direction='send')
            destination_lane = optical_lane_for(destination_parent, mpo_index=sample['leaf_endpoint'][2], position_number=position_number, direction='receive')
            result = resolve_optical_lane_path(source=source_lane, destination=destination_lane)
            status = 'FOUND' if result.path_found else 'MISSING'
            print(f'  strand={position_number:02d} status={status}')
            for idx, step in enumerate(result.steps or (), start=1):
                print(f'    {idx:02d}. {step_label(step)}')


main()
