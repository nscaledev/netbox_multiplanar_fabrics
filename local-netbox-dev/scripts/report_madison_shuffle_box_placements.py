#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_PLACEMENT_MANIFEST = Path(__file__).resolve().parents[1] / 'data' / 'generated' / 'madison_device_placement_manifest.csv'
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'data' / 'generated'

SHUFFLE_LABEL_PATTERN = re.compile(
    r'^(?P<populated_cassettes>\d+)\s+2x\s+\(2x2\)\s*Shuffle'
    r'(?:\s+(?P<logical_shuffle_box>\d+))?'
    r'(?:-(?P<nic>NIC(?P<nic_index_zero>\d+))(?P<side>[AB]))?$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ShuffleBoxPlacement:
    box_name: str
    physical_slot: str
    row_id_tag: str
    row_sheet: str
    source_cell: str
    ru_top: int
    ru_bottom: int
    height_u: int
    source_label: str
    populated_cassettes: int
    tray_slots: int
    cassette_slots_per_tray: int
    max_cassettes: int
    logical_shuffle_box: int | None
    nic_index_zero: int | None
    cx8_number: int | None
    side: str
    rack_role_slug: str
    rack_source_label: str
    rack_location: str
    source_device_type_slug: str
    notes: str


def int_field(row: dict[str, str], field: str) -> int:
    return int(row[field])


def parse_shuffle_label(label: str) -> tuple[dict[str, int | str | None], str]:
    match = SHUFFLE_LABEL_PATTERN.fullmatch(label.strip())
    if not match:
        return {}, 'Could not parse shuffle row-elevation label.'

    populated_cassettes = int(match.group('populated_cassettes'))
    logical_shuffle_box = match.group('logical_shuffle_box')
    nic_index_zero = match.group('nic_index_zero')
    side = match.group('side') or ''

    notes: list[str] = []
    if populated_cassettes > 18:
        notes.append('Parsed populated cassette count exceeds the 18-cassette physical capacity.')
    if logical_shuffle_box is None:
        notes.append('No logical shuffle box number is present in the source label.')
    if nic_index_zero is None:
        notes.append('No NIC/CX-8 group is present in the source label.')
    if not side:
        notes.append('No A/B side is present in the source label.')

    return (
        {
            'populated_cassettes': populated_cassettes,
            'logical_shuffle_box': int(logical_shuffle_box) if logical_shuffle_box is not None else None,
            'nic_index_zero': int(nic_index_zero) if nic_index_zero is not None else None,
            'cx8_number': int(nic_index_zero) + 1 if nic_index_zero is not None else None,
            'side': side.upper(),
        },
        ' '.join(notes),
    )


def load_shuffle_placements(path: Path) -> list[ShuffleBoxPlacement]:
    placements: list[ShuffleBoxPlacement] = []

    with path.open(newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get('device_type_slug') != 'shuffle-cassette-2x2-mpo':
                continue

            parsed, notes = parse_shuffle_label(row['source_label'])
            if not parsed:
                parsed = {
                    'populated_cassettes': 0,
                    'logical_shuffle_box': None,
                    'nic_index_zero': None,
                    'cx8_number': None,
                    'side': '',
                }

            physical_slot = row['physical_slot']
            ru_top = int_field(row, 'ru_top')
            source_cell = row['source_cell']
            box_name = f'gs001-{physical_slot.lower()}-u{ru_top:02d}-sb'

            placements.append(
                ShuffleBoxPlacement(
                    box_name=box_name,
                    physical_slot=physical_slot,
                    row_id_tag=row['row_id_tag'],
                    row_sheet=row['row_sheet'],
                    source_cell=source_cell,
                    ru_top=ru_top,
                    ru_bottom=int_field(row, 'ru_bottom'),
                    height_u=int_field(row, 'height_u'),
                    source_label=row['source_label'],
                    populated_cassettes=int(parsed['populated_cassettes'] or 0),
                    tray_slots=3,
                    cassette_slots_per_tray=6,
                    max_cassettes=18,
                    logical_shuffle_box=parsed['logical_shuffle_box'],  # type: ignore[arg-type]
                    nic_index_zero=parsed['nic_index_zero'],  # type: ignore[arg-type]
                    cx8_number=parsed['cx8_number'],  # type: ignore[arg-type]
                    side=str(parsed['side'] or ''),
                    rack_role_slug=row['rack_role_slug'],
                    rack_source_label=row['rack_source_label'],
                    rack_location=row['rack_location'],
                    source_device_type_slug=row['device_type_slug'],
                    notes=notes,
                )
            )

    placements.sort(key=lambda item: (item.physical_slot[0], int(item.physical_slot[1:]), -item.ru_top, item.source_cell))
    return placements


def write_outputs(placements: list[ShuffleBoxPlacement], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / 'madison_shuffle_box_placement_manifest.json'
    csv_path = output_dir / 'madison_shuffle_box_placement_manifest.csv'

    json_path.write_text(json.dumps([asdict(placement) for placement in placements], indent=2) + '\n')

    fieldnames = list(asdict(placements[0]).keys()) if placements else [field.name for field in ShuffleBoxPlacement.__dataclass_fields__.values()]
    with csv_path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for placement in placements:
            writer.writerow(asdict(placement))

    return json_path, csv_path


def print_report(placements: list[ShuffleBoxPlacement], *, include_samples: bool) -> None:
    total_populated = sum(placement.populated_cassettes for placement in placements)
    total_capacity = sum(placement.max_cassettes for placement in placements)
    unparsed = [placement for placement in placements if placement.populated_cassettes == 0]
    non_1u = [placement for placement in placements if placement.height_u != 1 or placement.ru_top != placement.ru_bottom]
    missing_logical = [placement for placement in placements if placement.logical_shuffle_box is None]
    missing_nic = [placement for placement in placements if placement.nic_index_zero is None]
    missing_side = [placement for placement in placements if not placement.side]

    print('Madison shuffle box placement manifest')
    print(f'physical_shuffle_boxes={len(placements)}')
    print(f'populated_cassettes_from_labels={total_populated}')
    print(f'physical_cassette_capacity={total_capacity}')
    print(f'utilization={total_populated / total_capacity:.2%}' if total_capacity else 'utilization=0.00%')
    print(f'non_1u_shuffle_labels={len(non_1u)}')
    print(f'unparsed_shuffle_labels={len(unparsed)}')
    print(f'missing_logical_shuffle_box={len(missing_logical)}')
    print(f'missing_nic_group={len(missing_nic)}')
    print(f'missing_side={len(missing_side)}')

    print('\nBy populated cassette count:')
    for populated_cassettes, count in sorted(Counter(placement.populated_cassettes for placement in placements).items()):
        print(f'  {populated_cassettes}: {count}')

    print('\nBy row sheet:')
    by_row_sheet: dict[str, list[ShuffleBoxPlacement]] = defaultdict(list)
    for placement in placements:
        by_row_sheet[placement.row_sheet].append(placement)
    for row_sheet in sorted(by_row_sheet):
        row_placements = by_row_sheet[row_sheet]
        count_distribution = ', '.join(
            f'{populated}x{count}'
            for populated, count in sorted(Counter(placement.populated_cassettes for placement in row_placements).items())
        )
        print(
            f'  {row_sheet}: boxes={len(row_placements)}, '
            f'populated_cassettes={sum(placement.populated_cassettes for placement in row_placements)}, '
            f'counts={count_distribution}'
        )

    print('\nBy rack role:')
    for rack_role_slug, count in sorted(Counter(placement.rack_role_slug or 'unknown' for placement in placements).items()):
        print(f'  {rack_role_slug}: {count}')

    if include_samples:
        print('\nA9/A10 sample placements:')
        for placement in placements:
            if placement.physical_slot in {'A9', 'A10'}:
                logical = placement.logical_shuffle_box if placement.logical_shuffle_box is not None else ''
                nic = placement.nic_index_zero if placement.nic_index_zero is not None else ''
                print(
                    f'  {placement.physical_slot} RU{placement.ru_top}: '
                    f'label="{placement.source_label}", populated={placement.populated_cassettes}, '
                    f'shuffle={logical}, nic={nic}, side={placement.side}, box={placement.box_name}'
                )

    if unparsed:
        print('\nUnparsed shuffle label samples:')
        for placement in unparsed[:10]:
            print(f'  {placement.physical_slot} RU{placement.ru_top}: {placement.source_label}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract physical 1RU shuffle box placements from the Madison workbook row-elevation manifest.'
    )
    parser.add_argument('--placement-manifest', type=Path, default=DEFAULT_PLACEMENT_MANIFEST)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument('--include-samples', action='store_true')
    parser.add_argument('--no-write', action='store_true')
    args = parser.parse_args()

    placements = load_shuffle_placements(args.placement_manifest)
    print_report(placements, include_samples=args.include_samples)

    if not args.no_write:
        json_path, csv_path = write_outputs(placements, args.output_dir)
        print(f'\nwrote_json={json_path}')
        print(f'wrote_csv={csv_path}')


if __name__ == '__main__':
    main()
