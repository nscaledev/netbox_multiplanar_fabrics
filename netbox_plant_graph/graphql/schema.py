from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from netbox_plant_graph.models import Fabric, FabricArchitecture, OpticalLane
from netbox_plant_graph.services.resolver import resolve_optical_lane_path


def _v2_status() -> JSON:
    return {
        'status': 'v2_kernel',
        'architecture_count': FabricArchitecture.objects.count(),
        'fabric_count': Fabric.objects.count(),
    }


def _optical_lane_path(source_id: strawberry.ID, destination_id: strawberry.ID | None = None) -> JSON:
    source = OpticalLane.objects.get(pk=source_id)
    destination = OpticalLane.objects.get(pk=destination_id) if destination_id is not None else None
    path = resolve_optical_lane_path(source=source, destination=destination)
    return {
        'path_found': path.path_found,
        'source_lane_id': path.source_lane_id,
        'destination_lane_id': path.destination_lane_id,
        'error': path.error,
        'steps': [
            {
                'step_type': step.step_type,
                'object_type': step.object_type,
                'object_id': step.object_id,
                'label': step.label,
                'metadata': step.metadata,
            }
            for step in path.steps
        ],
    }


@strawberry.type(name='Query')
class NetBoxPlantGraphQuery:
    v2_status: JSON = strawberry.field(resolver=_v2_status)
    optical_lane_path: JSON = strawberry.field(resolver=_optical_lane_path)


schema = [NetBoxPlantGraphQuery]
