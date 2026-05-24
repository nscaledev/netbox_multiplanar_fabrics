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
    shuffle_transfer_position_pairs,
)


SAMPLE_PATHS = [
    {'plane_number': 1, 'gb300_endpoint': ('gs001-a2-u11-gb300ct', 'osfp1', 1), 'leaf_endpoint': ('gs001-a9-u12-13-sn5610', 'swp1', 1), 'label': 'PL1 NIC0A'},
    {'plane_number': 2, 'gb300_endpoint': ('gs001-a2-u11-gb300ct', 'osfp1', 2), 'leaf_endpoint': ('gs001-a9-u14-15-sn5610', 'swp1', 1), 'label': 'PL2 NIC0A'},
    {'plane_number': 3, 'gb300_endpoint': ('gs001-a2-u11-gb300ct', 'osfp2', 1), 'leaf_endpoint': ('gs001-a10-u12-13-sn5610', 'swp1', 1), 'label': 'PL3 NIC0B'},
    {'plane_number': 4, 'gb300_endpoint': ('gs001-a2-u11-gb300ct', 'osfp2', 2), 'leaf_endpoint': ('gs001-a10-u14-15-sn5610', 'swp1', 1), 'label': 'PL4 NIC0B'},
]


def step_label(step) -> str:
    display = getattr(step, 'label', '') or getattr(step, 'display', '')
    object_type = getattr(step, 'object_type', '')
    object_id = getattr(step, 'object_id', '')
    return display or f'{object_type}:{object_id}'


def main() -> None:
    fabric = get_fabric()
    expected_paths = 0
    found_paths = 0
    missing_paths = 0
    print('Madison optical lane path traces for netbox_plant_graph v2')
    print(f'fabric={fabric.name}')
    for sample in SAMPLE_PATHS:
        print('')
        print(f"plane={sample['plane_number']} label={sample['label']}")
        source_parent = mpo_endpoint_for_device_port(fabric, *sample['gb300_endpoint']).parent
        destination_parent = mpo_endpoint_for_device_port(fabric, *sample['leaf_endpoint']).parent
        # The sample path lands on cassette rear MPO 1. The 2x2 shuffle cassette
        # rolls the position numbers, so destination leaf positions are not the
        # same as source GPU positions.
        for source_position, destination_position in shuffle_transfer_position_pairs(front_index=1, rear_index=1):
            expected_paths += 1
            source_lane = optical_lane_for(
                source_parent,
                mpo_index=sample['gb300_endpoint'][2],
                position_number=source_position,
                direction='send',
            )
            destination_lane = optical_lane_for(
                destination_parent,
                mpo_index=sample['leaf_endpoint'][2],
                position_number=destination_position,
                direction='receive',
            )
            result = resolve_optical_lane_path(source=source_lane, destination=destination_lane)
            status = 'FOUND' if result.path_found else 'MISSING'
            if result.path_found:
                found_paths += 1
            else:
                missing_paths += 1
            print(f'  source_position={source_position:02d} destination_position={destination_position:02d} status={status}')
            for idx, step in enumerate(result.steps or (), start=1):
                print(f'    {idx:02d}. {step_label(step)}')
    print('')
    print(f'expected_paths={expected_paths}')
    print(f'found_paths={found_paths}')
    print(f'missing_paths={missing_paths}')
    if missing_paths:
        raise SystemExit(1)


main()
