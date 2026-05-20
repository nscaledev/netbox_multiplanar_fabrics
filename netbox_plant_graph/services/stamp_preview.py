"""
Stamp preview service — returns a dry-run description of what would be created
by stamping a given template without persisting any changes.
"""
from __future__ import annotations

from typing import Any


def build_v2_stamp_template_preview(template, parameters: dict[str, Any]) -> dict[str, Any]:
    """
    Return a lightweight preview for a V2 StampTemplate.

    V2 stamping is hybrid: the template declares structure and names a plugin
    primitive for inference-heavy work. The preview mirrors that contract by
    deriving counts from declarative template fields and surfacing the primitive
    that will execute the stamp.
    """
    template_spec = template.template or {}
    executor = template_spec.get('executor') or {}
    primitive = executor.get('primitive') or 'unknown'
    planes = template_spec.get('planes') or []
    proof_paths = template_spec.get('proof_paths') or []
    gpu_spec = template_spec.get('gpu_tray') or {}
    shuffle_spec = template_spec.get('shuffle_cassettes') or {}
    leaf_spec = template_spec.get('leaf_ports') or {}

    gpu_osfps = int(gpu_spec.get('osfp_count') or 0)
    mpo_per_osfp = int(gpu_spec.get('mpo_per_osfp') or 0)
    positions_per_mpo = int(gpu_spec.get('positions_per_mpo') or 0)
    shuffle_count = int(shuffle_spec.get('count') or 0)
    shuffle_mpos = shuffle_count * (
        int(shuffle_spec.get('front_mpo_count') or 0) + int(shuffle_spec.get('rear_mpo_count') or 0)
    )
    leaf_count = int(leaf_spec.get('count') or 0)

    gpu_mpos = gpu_osfps * mpo_per_osfp
    leaf_mpos = leaf_count * mpo_per_osfp
    mpo_endpoint_count = gpu_mpos + shuffle_mpos + leaf_mpos
    optical_path_count = len(proof_paths)

    objects_to_create = (
        {'type': 'fabric', 'count': 1},
        {'type': 'plane', 'count': len(planes)},
        {'type': 'fabric_node', 'count': 1 + shuffle_count + leaf_count},
        {'type': 'endpoint', 'count': gpu_osfps + gpu_mpos + shuffle_mpos + leaf_count + leaf_mpos},
        {'type': 'connector_position', 'count': mpo_endpoint_count * positions_per_mpo},
        {'type': 'transport_channel', 'count': optical_path_count * 2},
        {'type': 'fiber_segment', 'count': optical_path_count * 2},
        {'type': 'fiber_strand', 'count': optical_path_count * 2},
        {'type': 'strand_termination', 'count': optical_path_count * 4},
        {'type': 'transfer_map', 'count': optical_path_count},
        {'type': 'optical_lane', 'count': optical_path_count * 2},
        {'type': 'stamp_run', 'count': 1},
    )

    return {
        'template_type': 'v2_stamp_template',
        'template_id': template.pk,
        'template_name': template.name,
        'executor': primitive,
        'fabric_name': parameters.get('fabric_name'),
        'fabric_slug': parameters.get('fabric_slug'),
        'proof_path_count': optical_path_count,
        'description': (
            f'Stamping will execute {primitive} for fabric '
            f'"{parameters.get("fabric_name")}" ({parameters.get("fabric_slug")}).'
        ),
        'objects_to_create': objects_to_create,
    }


def build_stamp_preview(template_type: str, template_id: int, parameters: dict[str, Any]) -> dict[str, Any]:
    """
    Return a description of what would be created by stamping the given template.

    ``template_type`` must be one of 'assembly', 'spatial', or 'rack_population'.
    ``template_id`` is the primary key of the template model instance.
    ``parameters`` is a dict of optional stamp parameters (site, rack, plan, etc.).

    Returns a dict with preview information.  Raises ``ValueError`` for unknown
    template_type values.
    """
    if template_type == 'assembly':
        return _preview_assembly(template_id, parameters)
    elif template_type == 'spatial':
        return _preview_spatial(template_id, parameters)
    elif template_type == 'rack_population':
        return _preview_rack_population(template_id, parameters)
    else:
        raise ValueError(f'Unknown template_type: {template_type!r}. Must be one of assembly, spatial, rack_population.')


def _preview_assembly(template_id: int, parameters: dict[str, Any]) -> dict[str, Any]:
    from netbox_plant_graph.models import AssemblyTemplate
    try:
        template = AssemblyTemplate.objects.get(pk=template_id)
    except AssemblyTemplate.DoesNotExist:
        return {'error': f'AssemblyTemplate with id={template_id} does not exist.'}
    a_count = template.connectors.filter(side='A').count()
    b_count = template.connectors.filter(side='B').count()
    mapping_count = template.mappings.count()
    return {
        'template_type': 'assembly',
        'template_id': template_id,
        'template_name': template.name,
        'description': (
            f'Stamping will create 1 device using AssemblyTemplate "{template.name}" '
            f'with {a_count} A-side connectors, {b_count} B-side connectors, '
            f'and {mapping_count} internal mappings.'
        ),
        'a_connector_count': a_count,
        'b_connector_count': b_count,
        'mapping_count': mapping_count,
        'objects_to_create': [
            {'type': 'device', 'count': 1},
        ],
    }


def _preview_spatial(template_id: int, parameters: dict[str, Any]) -> dict[str, Any]:
    from netbox_plant_graph.models import SpatialTemplate, SpatialTemplateNode
    try:
        template = SpatialTemplate.objects.get(pk=template_id)
    except SpatialTemplate.DoesNotExist:
        return {'error': f'SpatialTemplate with id={template_id} does not exist.'}
    nodes = list(template.nodes.order_by('sort_order', 'pk'))
    location_nodes = [n for n in nodes if n.node_type == 'location']
    rack_nodes = [n for n in nodes if n.node_type == 'rack']
    # Approximate total objects by summing quantities
    total_locations = sum(n.quantity for n in location_nodes)
    total_racks = sum(n.quantity for n in rack_nodes)
    return {
        'template_type': 'spatial',
        'template_id': template_id,
        'template_name': template.name,
        'description': (
            f'Stamping will create approximately {total_locations} location(s) and '
            f'{total_racks} rack(s) from SpatialTemplate "{template.name}".'
        ),
        'node_count': len(nodes),
        'objects_to_create': [
            {'type': 'location', 'count': total_locations},
            {'type': 'rack', 'count': total_racks},
        ],
    }


def _preview_rack_population(template_id: int, parameters: dict[str, Any]) -> dict[str, Any]:
    from netbox_plant_graph.models import RackPopulationTemplate
    try:
        template = RackPopulationTemplate.objects.get(pk=template_id)
    except RackPopulationTemplate.DoesNotExist:
        return {'error': f'RackPopulationTemplate with id={template_id} does not exist.'}
    slots = list(template.slots.select_related('device_type', 'device_role').order_by('u_position', 'face'))
    return {
        'template_type': 'rack_population',
        'template_id': template_id,
        'template_name': template.name,
        'description': (
            f'Stamping will place {len(slots)} device(s) into the target rack '
            f'using RackPopulationTemplate "{template.name}".'
        ),
        'slot_count': len(slots),
        'slots': [
            {
                'u_position': s.u_position,
                'face': s.face,
                'device_type': str(s.device_type) if s.device_type_id else None,
                'device_role': str(s.device_role) if s.device_role_id else None,
                'name_pattern': s.name_pattern,
            }
            for s in slots
        ],
        'objects_to_create': [
            {'type': 'device', 'count': len(slots)},
        ],
    }
