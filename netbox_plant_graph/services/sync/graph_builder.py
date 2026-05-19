import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, Fabric, FabricPlane, FineEdge, LaneMap, PlaneMembership, PlantNode, SignalLane, TerminationPoint, TransferMap

from .transformer import GraphInputs

logger = logging.getLogger('netbox_plant_graph')

# Schema of keys that may appear in GraphBuildRun.stats.
GRAPH_BUILD_STATS_SCHEMA = {
    'fabrics': int,
    'fabric_planes': int,
    'nodes': int,
    'terminations': int,
    'attachment_units': int,
    'signal_lanes': int,
    'coarse_edges': int,
    'fine_edges': int,
    'signal_lane_fine_edges': int,
    'transfer_maps': int,
    'lane_maps': int,
    'plane_memberships': int,
    'dry_run': int,
    'fabric_id': int,
    'graph_revision': str,
    'warnings': list,   # list of {code: str, ...} dicts
}


def _generic_fk_fields(prefix: str, obj) -> dict[str, object]:
    if obj is None:
        return {f'{prefix}_type': None, f'{prefix}_id': None}
    return {
        f'{prefix}_type': ContentType.objects.get_for_model(obj, for_concrete_model=False),
        f'{prefix}_id': obj.pk,
    }


def _count_fine_edges(inputs: GraphInputs, granularity: str) -> int:
    return sum(1 for fine_edge in inputs.fine_edges if fine_edge.get('granularity') == granularity)


def build_graph(inputs: GraphInputs, *, dry_run: bool = False) -> dict[str, int]:
    stats = {
        'fabrics': len(inputs.fabrics),
        'fabric_planes': len(inputs.plane_numbers),
        'nodes': len(inputs.nodes),
        'terminations': len(inputs.terminations),
        'attachment_units': len(inputs.attachment_units),
        'signal_lanes': len(inputs.signal_lanes),
        'coarse_edges': len(inputs.coarse_edges),
        'fine_edges': _count_fine_edges(inputs, 'attachment_unit'),
        'signal_lane_fine_edges': _count_fine_edges(inputs, 'signal_lane'),
        'transfer_maps': len(inputs.transfer_maps),
        'lane_maps': len(inputs.lane_maps),
        'plane_memberships': len(inputs.plane_memberships),
        'dry_run': int(dry_run),
    }

    if dry_run or not inputs.fabrics:
        return stats

    fabric_input = inputs.fabrics[0]

    with transaction.atomic():
        fabric = fabric_input.get('instance')
        if fabric is None:
            fabric, _ = Fabric.objects.get_or_create(name=fabric_input['name'])
        graph_revision = timezone.now().isoformat()

        Fabric.objects.filter(pk=fabric.pk).update(
            description=fabric_input['description'],
            expected_plane_count=fabric_input['expected_plane_count'],
            tier_depth=fabric_input['tier_depth'],
            disjointness_policy=fabric_input['disjointness_policy'],
            metadata=fabric_input['metadata'],
            scope_site=fabric_input['scope_site'],
            scope_location=fabric_input['scope_location'],
        )
        fabric.refresh_from_db()

        fabric.plant_nodes.all().delete()
        fabric.planes.all().delete()

        plane_map = {}
        for plane_number in inputs.plane_numbers:
            plane_map[plane_number] = FabricPlane.objects.create(fabric=fabric, plane_number=plane_number)

        node_map = {}
        for node_input in sorted(inputs.nodes, key=lambda item: item['key']):
            node_map[node_input['key']] = PlantNode.objects.create(
                fabric=fabric,
                name=node_input['name'],
                node_type=node_input['node_type'],
                role=node_input['role'],
                status=node_input['status'],
                tenant=node_input.get('tenant'),
                metadata=node_input['metadata'],
                **_generic_fk_fields('source', node_input['source']),
                **_generic_fk_fields('location', node_input['location']),
            )

        termination_map = {}
        for termination_input in sorted(inputs.terminations, key=lambda item: item['key']):
            termination_map[termination_input['key']] = TerminationPoint.objects.create(
                plant_node=node_map[termination_input['node_key']],
                name=termination_input['name'],
                tp_type=termination_input['tp_type'],
                connector_type=termination_input['connector_type'],
                channel_capacity=termination_input['channel_capacity'],
                speed_gbps=termination_input['speed_gbps'],
                metadata=termination_input['metadata'],
                **_generic_fk_fields('source', termination_input['source']),
            )

        attachment_map = {}
        for attachment_input in sorted(inputs.attachment_units, key=lambda item: item['key']):
            attachment_map[attachment_input['key']] = AttachmentUnit.objects.create(
                termination_point=termination_map[attachment_input['termination_key']],
                name=attachment_input['name'],
                ordinal=attachment_input['ordinal'],
                unit_type=attachment_input['unit_type'],
                speed_gbps=attachment_input['speed_gbps'],
                topology_role=attachment_input['topology_role'],
                active=attachment_input['active'],
                metadata=attachment_input['metadata'],
                **_generic_fk_fields('source', attachment_input['source']),
            )

        signal_lane_map = {}
        for signal_lane_input in sorted(inputs.signal_lanes, key=lambda item: item['key']):
            signal_lane_map[signal_lane_input['key']] = SignalLane.objects.create(
                attachment_unit=attachment_map[signal_lane_input['attachment_key']],
                name=signal_lane_input['name'],
                lane_index=signal_lane_input['lane_index'],
                lane_kind=signal_lane_input['lane_kind'],
                signaling=signal_lane_input['signaling'],
                nominal_rate_gbps=signal_lane_input['nominal_rate_gbps'],
                direction_role=signal_lane_input['direction_role'],
                wavelength_nm=signal_lane_input.get('wavelength_nm', 1310),
                wavelength_group=signal_lane_input['wavelength_group'],
                source_anchor=signal_lane_input['source_anchor'],
                metadata=signal_lane_input['metadata'],
            )

        coarse_edge_map = {}
        for coarse_edge_input in sorted(inputs.coarse_edges, key=lambda item: item['key']):
            coarse_edge_map[coarse_edge_input['key']] = CoarseEdge.objects.create(
                edge_type=coarse_edge_input['edge_type'],
                a_tp=termination_map[coarse_edge_input['a_tp_key']],
                b_tp=termination_map[coarse_edge_input['b_tp_key']],
                cable_profile_name=coarse_edge_input['cable_profile_name'],
                metadata=coarse_edge_input['metadata'],
                **_generic_fk_fields('source', coarse_edge_input['source']),
            )

        for fine_edge_input in sorted(inputs.fine_edges, key=lambda item: item['key']):
            fine_edge_kwargs = {
                'granularity': fine_edge_input['granularity'],
                'edge_type': fine_edge_input['edge_type'],
                'parent_coarse_edge': coarse_edge_map.get(fine_edge_input.get('parent_coarse_key')),
                'derived_from_profile': fine_edge_input['derived_from_profile'],
                'metadata': fine_edge_input['metadata'],
            }
            if fine_edge_input['granularity'] == 'signal_lane':
                fine_edge_kwargs.update({
                    'a_lane': signal_lane_map[fine_edge_input['a_lane_key']],
                    'b_lane': signal_lane_map[fine_edge_input['b_lane_key']],
                })
            else:
                fine_edge_kwargs.update({
                    'a_au': attachment_map[fine_edge_input['a_au_key']],
                    'b_au': attachment_map[fine_edge_input['b_au_key']],
                })
            FineEdge.objects.create(**fine_edge_kwargs)

        for transfer_map_input in sorted(inputs.transfer_maps, key=lambda item: item['key']):
            TransferMap.objects.create(
                owner_node=node_map[transfer_map_input['owner_node_key']],
                src_attachment_unit=attachment_map[transfer_map_input['src_attachment_key']],
                dst_attachment_unit=attachment_map[transfer_map_input['dst_attachment_key']],
                mapping_type=transfer_map_input['mapping_type'],
                source_port_mapping=transfer_map_input.get('source_port_mapping'),
                metadata=transfer_map_input['metadata'],
            )

        for lane_map_input in sorted(inputs.lane_maps, key=lambda item: item['key']):
            LaneMap.objects.create(
                owner_node=node_map.get(lane_map_input.get('owner_node_key')),
                owner_edge=coarse_edge_map.get(lane_map_input.get('owner_edge_key')),
                src_lane=signal_lane_map[lane_map_input['src_lane_key']],
                dst_lane=signal_lane_map[lane_map_input['dst_lane_key']],
                mapping_type=lane_map_input['mapping_type'],
                metadata=lane_map_input['metadata'],
            )

        attachment_content_type = ContentType.objects.get_for_model(AttachmentUnit)

        # B3: Warn when plane_memberships reference a plane_number not in plane_map.
        requested_membership_planes = {m['plane_number'] for m in inputs.plane_memberships}
        missing_planes = requested_membership_planes - set(plane_map)
        if missing_planes:
            logger.warning(
                'Fabric %s: plane numbers %s appear in attachment-unit plane memberships '
                'but no matching FabricPlane records exist. '
                'PlaneMembership rows for these planes will be skipped.',
                fabric, sorted(missing_planes),
            )
            stats.setdefault('warnings', []).append({
                'code': 'missing_fabric_plane_records',
                'plane_numbers': sorted(missing_planes),
            })

        for membership_input in sorted(inputs.plane_memberships, key=lambda item: (item['plane_number'], item['member_key'])):
            if membership_input['plane_number'] not in plane_map:
                continue
            member = attachment_map[membership_input['member_key']]
            PlaneMembership.objects.create(
                plane=plane_map[membership_input['plane_number']],
                member_type=attachment_content_type,
                member_id=member.pk,
                membership_role=membership_input['membership_role'],
                metadata=membership_input['metadata'],
            )

        fabric.metadata = {
            **(fabric.metadata or {}),
            'graph_revision': graph_revision,
        }
        fabric.save(update_fields=('metadata', 'last_updated'))

    stats['fabric_id'] = fabric.pk
    stats['graph_revision'] = graph_revision

    # B4: Warn when attachment units were built but zero plane memberships resulted.
    if stats.get('attachment_units', 0) > 0 and stats.get('plane_memberships', 0) == 0:
        logger.warning(
            'Rebuild completed with %d AttachmentUnit(s) but ZERO PlaneMembership records. '
            'Check: (1) fabric_plane custom field exists on dcim.Interface, '
            '(2) child interfaces have fabric_plane values set, '
            '(3) FabricPlane records exist for this fabric.',
            stats['attachment_units'],
        )
        stats.setdefault('warnings', []).append({'code': 'zero_plane_memberships'})

    return stats
