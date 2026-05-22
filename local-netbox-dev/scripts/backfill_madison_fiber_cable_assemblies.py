from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from django.db import transaction

from netbox_plant_graph.models import CableAssembly, Fabric, FiberSegment, FiberStrand

SCRIPT_DIRS = [
    Path(__file__).resolve().parent,
    Path('/opt/netbox/local-plugins/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
    Path('/Users/mencken/github-repos/netbox_multiplanar_fabrics/local-netbox-dev/scripts'),
]
for script_dir in SCRIPT_DIRS:
    if script_dir.exists() and str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

from madison_v2_graph import cable_assembly_for_segment


DEFAULT_PATH_KEY = 'madison-ready-sus-leaf16-four-plane-lane-aware-v2'
DEFAULT_FABRIC_SLUGS = ('gs001-roce-fabric', 'mad-1-roce-fabric')
APPLY_ENV = 'MADISON_FIBER_CABLE_ASSEMBLY_APPLY'


def selected_fabric() -> Fabric:
    requested = os.environ.get('MADISON_FABRIC_SLUG')
    slugs = [requested] if requested else list(DEFAULT_FABRIC_SLUGS)
    for slug in slugs:
        fabric = Fabric.objects.filter(slug=slug).first()
        if fabric is not None:
            return fabric
    raise RuntimeError(f'No Madison fabric found for slugs: {slugs}')


def selected_path_key() -> str:
    return os.environ.get('MADISON_PATH_KEY') or DEFAULT_PATH_KEY


def segment_position_numbers(segment: FiberSegment) -> list[int]:
    return list(
        FiberStrand.objects.filter(segment=segment)
        .order_by('strand_index')
        .values_list('strand_index', flat=True)
    )


def desired_segment_metadata(segment: FiberSegment, *, path_key: str) -> dict:
    metadata = dict(segment.metadata or {})
    metadata.setdefault('path_key', path_key)
    metadata.setdefault('segment_role', metadata.get('segment_role', ''))
    metadata.setdefault('a_endpoint', segment.a_endpoint.address)
    metadata.setdefault('b_endpoint', segment.b_endpoint.address)
    metadata.setdefault('segment_kind', segment.segment_kind)
    return metadata


def backfill_cable_assemblies(*, apply: bool) -> Counter:
    fabric = selected_fabric()
    path_key = selected_path_key()
    segments = list(
        FiberSegment.objects.filter(fabric=fabric, metadata__path_key=path_key)
        .select_related('a_endpoint__node', 'b_endpoint__node')
        .order_by('name')
    )
    counters = Counter()
    counters['fabric_slug'] = fabric.slug
    counters['path_key'] = path_key
    counters['fiber_segments'] = len(segments)

    if not apply:
        for segment in segments:
            counters['would_update_fiber_strands'] += FiberStrand.objects.filter(segment=segment).count()
            counters['would_upsert_cable_assemblies'] += 1
        return counters

    with transaction.atomic():
        for segment in segments:
            positions = segment_position_numbers(segment)
            cable = cable_assembly_for_segment(
                fabric,
                name=segment.name,
                a_endpoint=segment.a_endpoint,
                b_endpoint=segment.b_endpoint,
                marker=path_key,
                segment_kind=segment.segment_kind,
                position_numbers=positions,
                metadata=desired_segment_metadata(segment, path_key=path_key),
                counters=counters,
            )
            updated = FiberStrand.objects.filter(segment=segment).update(
                cable_site=cable.site,
                cable_id=cable.cable_id,
            )
            counters['fiber_strands_updated'] += updated

    counters['cable_assemblies_after'] = CableAssembly.objects.filter(metadata__path_key=path_key).count()
    counters['fiber_strands_with_cable_identity_after'] = (
        FiberStrand.objects.filter(segment__fabric=fabric, segment__metadata__path_key=path_key)
        .exclude(cable_id='')
        .count()
    )
    return counters


def main() -> None:
    apply = os.environ.get(APPLY_ENV) == '1'
    counters = backfill_cable_assemblies(apply=apply)
    print('Madison fiber CableAssembly backfill')
    print(f'apply={apply}; set {APPLY_ENV}=1 to write changes')
    for key, value in sorted(counters.items()):
        print(f'{key}={value}')


main()
