#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet


DEFAULT_WORKBOOK = Path(
    os.environ.get(
        'MADISON_LAYOUT_WORKBOOK',
        Path(__file__).resolve().parents[1] / 'data' / 'source' / 'madison-layout-workbook.xlsx',
    )
)
DEFAULT_RACK_MANIFEST = Path(__file__).resolve().parents[1] / 'data' / 'generated' / 'madison_workbook_rack_manifest.json'
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'data' / 'generated'

ROW_SHEET_PATTERN = re.compile(r'^Row\s+([A-X])\b', re.IGNORECASE)
RACK_HEADER_PATTERN = re.compile(r'^Row\s+([A-X])\s*(\d{1,2})$', re.IGNORECASE)
RACK_LABEL_PATTERN = re.compile(r'^GB300\s+(?:SU\d+|v)$', re.IGNORECASE)
SU_PATTERN = re.compile(r'\bSU\s*(\d+)\b', re.IGNORECASE)
PLANE_PATTERN = re.compile(r'\bPL(?:ANE)?\s*([1-4])\b', re.IGNORECASE)

KNOWN_DEVICE_TYPE_SLUGS = {
    'arista-7280',
    'cm8148',
    'gb300ct',
    'gb300st',
    'gb300ps',
    'generic-ceph-storage-node',
    'generic-cpu-control-node',
    'generic-data-storage-node',
    'generic-leak-detection',
    'generic-meta-storage-node',
    'generic-nmx-server',
    'generic-openstack-control-node',
    'generic-rack-pdu-415v-60a',
    'generic-ufm-server',
    'generic-vast-cbox',
    'generic-vast-dbox',
    'generic-vast-leaf-switch',
    'generic-vast-spine-switch',
    'nokia-ixr-d5',
    'nokia-sr1',
    'opengear-om2224-24e-l',
    'palo-alto-pa-1410',
    'palo-alto-pa-1420',
    'palo-alto-pa-550',
    'sb',
    'shuffle-cassette-2x2-mpo',
    'sn4700',
    'sn5610',
    'sn2201',
    'sn2201_m',
    'sn5750x1200',
    'xdr-q3750x1200-ra',
}


@dataclass(frozen=True)
class DevicePlacement:
    physical_slot: str
    row_id_tag: str
    row_sheet: str
    source_cell: str
    rack_header_cell: str
    ru_top: int
    ru_bottom: int
    height_u: int
    source_label: str
    normalized_label: str
    device_type_slug: str
    device_role_slug: str
    device_type_defined: bool
    scalable_units: list[int]
    backend_planes: list[int]
    rack_role_slug: str
    rack_source_label: str
    rack_location: str
    merged_range: str
    notes: str


def clean_label(value: Any) -> str:
    return ' '.join(str(value or '').replace('\n', ' ').split())


def invisible_white_on_no_fill(cell) -> bool:
    font_color = cell.font.color
    font_is_white = (
        font_color is not None
        and font_color.type == 'rgb'
        and str(font_color.rgb).upper() in {'FFFFFFFF', '00FFFFFF'}
    )
    return font_is_white and cell.fill.fill_type is None


def int_ru(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and 1 <= value <= 48:
        return int(value)
    return None


def row_sheets(workbook) -> list[Worksheet]:
    return [worksheet for worksheet in workbook.worksheets if ROW_SHEET_PATTERN.match(worksheet.title)]


def find_rack_headers(worksheet: Worksheet) -> list[tuple[str, Any]]:
    headers = []
    for row in worksheet.iter_rows():
        for cell in row:
            label = clean_label(cell.value)
            match = RACK_HEADER_PATTERN.fullmatch(label)
            if not match:
                continue
            headers.append((f'{match.group(1).upper()}{int(match.group(2))}', cell))
    return sorted(headers, key=lambda item: int(item[0][1:]))


def top_left_merged_ranges(worksheet: Worksheet):
    return {
        merged_range.start_cell.coordinate: merged_range
        for merged_range in worksheet.merged_cells.ranges
    }


def parse_sus(label: str) -> list[int]:
    return sorted({int(match) for match in SU_PATTERN.findall(label)})


def parse_planes(label: str) -> list[int]:
    return sorted({int(match) for match in PLANE_PATTERN.findall(label)})


def classify_label(label: str) -> tuple[str, str, str, list[str]]:
    lower = label.lower()
    normalized = lower
    notes: list[str] = []

    if RACK_LABEL_PATTERN.fullmatch(label):
        return 'rack-label', '', '', ['Rack role/SU label at RU48; not a device placement.']

    if lower == 'gpu-node':
        return 'gb300ct', 'gb300ct', 'PowerEdge XE9712 GB300 Compute Tray', []
    if 'powershelf' in lower:
        return 'gb300ps', 'gb300ps', 'PS33 33kW Power Shelf', []
    if 'nvlink-shelf' in lower:
        return 'gb300st', 'gb300st', 'GB300 NVL72 NVLink Switch Tray', []
    if lower == 'in rack 1gb switch':
        return 'sn2201_m', 'oob-leaf', 'SN2201_M GPU OOB TOR', []

    if re.fullmatch(r'gpu row mgmt oob leaf\s*#?\d+', lower):
        return 'sn2201', 'oob-leaf', 'SN2201 GPU Row MGMT OOB Leaf', [
            'Workbook label omits explicit model; staged as SN2201 OOB leaf pending final SKU confirmation.'
        ]

    if 'sn2201' in lower:
        if 'spine' in lower:
            role = 'oob-spine'
        elif 'core' in lower:
            role = 'oob-core'
        elif 'edge' in lower:
            role = 'oob-edge'
        elif 'border' in lower:
            role = 'oob-border-leaf'
        else:
            role = 'oob-leaf'
        return 'sn2201', role, 'SN2201', []

    if 'sn4700' in lower:
        if 'spine' in lower:
            role = 'oob-spine'
        elif 'core' in lower:
            role = 'oob-core'
        elif 'border' in lower:
            role = 'oob-border-leaf'
        elif 'edge' in lower:
            role = 'oob-edge'
        else:
            role = 'oob-leaf'
        return 'sn4700', role, 'SN4700', ['Device type definition is not staged yet.']

    if 'sn5610' in lower:
        if 'be leaf' in lower:
            role = 'be-leaf-switch'
        elif 'fe leaf' in lower:
            role = 'fe-leaf-switch'
        elif 'fe spine' in lower:
            role = 'fe-spine-switch'
        elif 'storage spine' in lower:
            role = 'storage-spine'
        elif 'storage leaf' in lower:
            role = 'storage-leaf'
        elif 'control spine' in lower:
            role = 'control-spine'
        elif 'control leaf' in lower:
            role = 'control-leaf'
        else:
            role = 'edge-switch'
        return 'sn5610', role, 'SN5610', ['Device type definition is not staged yet.']

    if 'cm8148' in lower:
        return 'cm8148', 'console-server', 'CM8148 Console Server', ['Device type definition is not staged yet.']

    if lower.startswith('storage meta'):
        return 'generic-meta-storage-node', 'meta-storage-node', 'Storage Meta Node', []
    if re.match(r'^storage\s+#\d+', lower):
        return 'generic-data-storage-node', 'data-storage-node', 'Storage Data Node', []
    if 'openstack' in lower or re.search(r'\bosc\s*#?\d*', lower):
        return 'generic-openstack-control-node', 'openstack-controller', 'OpenStack Controller', []
    if 'ceph' in lower:
        return 'generic-ceph-storage-node', 'ceph-storage-node', 'Ceph Storage Node', []
    if 'control #' in lower or 'cpu control node' in lower or re.search(r'\bcsc\s*#?\d*', lower):
        return 'generic-cpu-control-node', 'control-node', 'Control Node', []
    if 'nmx' in lower:
        return 'generic-nmx-server', 'nmx-server', 'NMX Server', []
    if 'ufm' in lower:
        return 'generic-ufm-server', 'ufm-server', 'UFM Server', []

    if 'palo alto 1420' in lower:
        return 'palo-alto-pa-1420', 'nscale-firewall', 'Palo Alto PA-1420', []
    if 'palo alto 1410' in lower:
        return 'palo-alto-pa-1410', 'nscale-firewall', 'Palo Alto PA-1410', []
    if 'palo alto 550' in lower:
        return 'palo-alto-pa-550', 'nscale-firewall', 'Palo Alto PA-550', []
    if 'arista 7280' in lower:
        return 'arista-7280', 'edge-switch', 'Arista 7280', []
    if 'om2224' in lower or 'opengear' in lower:
        return 'opengear-om2224-24e-l', 'console-server', 'Opengear OM2224', []
    if 'nokia sr1' in lower or 'nokia sr-1' in lower:
        return 'nokia-sr1', 'edge-switch', 'Nokia SR-1', []
    if 'nokia ixr' in lower:
        return 'nokia-ixr-d5', 'edge-switch', 'Nokia IXR-D5', []
    if 'sn5750' in lower:
        return 'sn5750x1200', 'be-spine-switch', 'SN5750 1200', []
    if 'q3750' in lower or 'xdr' in lower:
        return 'xdr-q3750x1200-ra', 'be-spine-switch', 'XDR Q3750', []

    if 'fiber panel' in lower:
        notes.append('Fiber panel label is unresolved; do not map to 48f/64f/72f/96f MPO8 scaffold panel types.')
        return 'unresolved-fiber-panel', 'fiber-panel', 'Fiber Panel', notes
    if 'shuffle' in lower:
        notes.append('Workbook models shuffle assemblies at RU granularity; final NetBox representation may use 0U cassettes inside a 4U shuffle box.')
        return 'shuffle-cassette-2x2-mpo', 'shuffle-cassette', '2x2 Shuffle Cassette', notes
    if 'leak detection' in lower:
        return 'generic-leak-detection', 'leak-detection', 'Leak Detection Monitor', []
    if 'pdu' in lower:
        return 'generic-rack-pdu-415v-60a', 'pdu', 'Rack PDU', []

    return '', '', normalized, ['No classifier matched this workbook label.']


def load_rack_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {entry['physical_slot']: entry for entry in json.loads(path.read_text())}


def parse_placements(workbook_path: Path, rack_manifest_path: Path) -> tuple[list[DevicePlacement], Counter]:
    workbook = load_workbook(workbook_path, data_only=True, read_only=False)
    rack_manifest = load_rack_manifest(rack_manifest_path)
    placements: list[DevicePlacement] = []
    skipped = Counter()

    for worksheet in row_sheets(workbook):
        merged_ranges = top_left_merged_ranges(worksheet)
        for physical_slot, header_cell in find_rack_headers(worksheet):
            ru_column = header_cell.column - 1
            if ru_column < 1:
                continue

            for row_number in range(header_cell.row + 1, worksheet.max_row + 1):
                ru_top = int_ru(worksheet.cell(row_number, ru_column).value)
                if ru_top is None:
                    continue

                label_cell = worksheet.cell(row_number, header_cell.column)
                source_label = clean_label(label_cell.value)
                if not source_label or source_label == 'False':
                    continue
                if invisible_white_on_no_fill(label_cell):
                    skipped['invisible_white_on_no_fill_labels'] += 1
                    continue

                device_type_slug, device_role_slug, normalized_label, notes = classify_label(source_label)
                if device_type_slug == 'rack-label':
                    skipped['rack_labels'] += 1
                    continue

                merged_range = merged_ranges.get(label_cell.coordinate)
                if merged_range:
                    height_u = merged_range.max_row - merged_range.min_row + 1
                    bottom_ru = int_ru(worksheet.cell(merged_range.max_row, ru_column).value)
                    ru_bottom = bottom_ru if bottom_ru is not None else ru_top - height_u + 1
                    merged_range_text = str(merged_range)
                else:
                    height_u = 1
                    ru_bottom = ru_top
                    merged_range_text = ''

                if ru_bottom > ru_top:
                    ru_top, ru_bottom = ru_bottom, ru_top

                rack_info = rack_manifest.get(physical_slot, {})
                if device_type_slug and device_type_slug not in KNOWN_DEVICE_TYPE_SLUGS:
                    notes = [*notes, 'Missing from staged device type definitions.']

                placements.append(
                    DevicePlacement(
                        physical_slot=physical_slot,
                        row_id_tag=f'nscale-row-id-{physical_slot}',
                        row_sheet=worksheet.title,
                        source_cell=label_cell.coordinate,
                        rack_header_cell=header_cell.coordinate,
                        ru_top=ru_top,
                        ru_bottom=ru_bottom,
                        height_u=height_u,
                        source_label=source_label,
                        normalized_label=normalized_label,
                        device_type_slug=device_type_slug,
                        device_role_slug=device_role_slug,
                        device_type_defined=bool(device_type_slug and device_type_slug in KNOWN_DEVICE_TYPE_SLUGS),
                        scalable_units=parse_sus(source_label),
                        backend_planes=parse_planes(source_label),
                        rack_role_slug=rack_info.get('role_slug', ''),
                        rack_source_label=rack_info.get('source_label', ''),
                        rack_location=rack_info.get('location', ''),
                        merged_range=merged_range_text,
                        notes=' '.join(notes),
                    )
                )

    placements.sort(key=lambda placement: (placement.physical_slot[0], int(placement.physical_slot[1:]), -placement.ru_top, placement.source_cell))
    return placements, skipped


def write_outputs(placements: list[DevicePlacement], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / 'madison_device_placement_manifest.json'
    csv_path = output_dir / 'madison_device_placement_manifest.csv'

    json_path.write_text(json.dumps([asdict(placement) for placement in placements], indent=2) + '\n')

    with csv_path.open('w', newline='') as handle:
        fieldnames = list(asdict(placements[0]).keys()) if placements else [field.name for field in DevicePlacement.__dataclass_fields__.values()]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for placement in placements:
            row = asdict(placement)
            row['scalable_units'] = ','.join(str(su) for su in placement.scalable_units)
            row['backend_planes'] = ','.join(str(plane) for plane in placement.backend_planes)
            writer.writerow(row)

    return json_path, csv_path


def print_report(placements: list[DevicePlacement], skipped: Counter, *, include_unmatched: bool) -> None:
    racks_with_placements = {placement.physical_slot for placement in placements}
    unmatched = [placement for placement in placements if not placement.device_type_slug]
    missing_device_types = sorted(
        {
            placement.device_type_slug
            for placement in placements
            if placement.device_type_slug and not placement.device_type_defined
        }
    )

    print('Madison workbook device placement manifest')
    print(f'total_device_placements={len(placements)}')
    print(f'racks_with_device_placements={len(racks_with_placements)}')
    print(f'merged_label_placements={sum(1 for placement in placements if placement.merged_range)}')
    print(f'skipped_rack_labels={skipped["rack_labels"]}')
    print(f'skipped_invisible_white_on_no_fill_labels={skipped["invisible_white_on_no_fill_labels"]}')
    print(f'unmatched_label_placements={len(unmatched)}')

    print('\nBy device type:')
    for device_type_slug, count in sorted(Counter(placement.device_type_slug or 'unmatched' for placement in placements).items()):
        print(f'  {device_type_slug}: {count}')

    print('\nBy device role:')
    for device_role_slug, count in sorted(Counter(placement.device_role_slug or 'unmatched' for placement in placements).items()):
        print(f'  {device_role_slug}: {count}')

    print('\nBy rack role:')
    for rack_role_slug, count in sorted(Counter(placement.rack_role_slug or 'unknown-rack-role' for placement in placements).items()):
        print(f'  {rack_role_slug}: {count}')

    print('\nRows with placements:')
    by_row = defaultdict(Counter)
    for placement in placements:
        by_row[placement.physical_slot[0]][placement.device_type_slug or 'unmatched'] += 1
    for row_letter in sorted(by_row):
        total = sum(by_row[row_letter].values())
        racks = len({placement.physical_slot for placement in placements if placement.physical_slot[0] == row_letter})
        print(f'  Row {row_letter}: placements={total}, racks={racks}')

    if missing_device_types:
        print('\nDevice types still missing from staged definitions:')
        for slug in missing_device_types:
            count = sum(1 for placement in placements if placement.device_type_slug == slug)
            print(f'  {slug}: {count}')

    if unmatched:
        print('\nUnmatched label samples:')
        sample_counter = Counter(placement.source_label for placement in unmatched)
        for label, count in sample_counter.most_common(25 if include_unmatched else 10):
            first = next(placement for placement in unmatched if placement.source_label == label)
            print(f'  {count}x {label} ({first.row_sheet}!{first.source_cell}, {first.physical_slot} RU{first.ru_top})')


def main() -> None:
    parser = argparse.ArgumentParser(description='Build a device/RU placement manifest from Madison workbook row elevation sheets.')
    parser.add_argument('--workbook', type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument('--rack-manifest', type=Path, default=DEFAULT_RACK_MANIFEST)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--include-unmatched', action='store_true')
    parser.add_argument('--no-write', action='store_true')
    args = parser.parse_args()

    placements, skipped = parse_placements(args.workbook, args.rack_manifest)
    print_report(placements, skipped, include_unmatched=args.include_unmatched)

    if not args.no_write:
        json_path, csv_path = write_outputs(placements, args.output_dir)
        print(f'\nwrote_json={json_path}')
        print(f'wrote_csv={csv_path}')


if __name__ == '__main__':
    main()
