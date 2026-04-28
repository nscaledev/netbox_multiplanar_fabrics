from django.db.models import Count

from netbox_plant_graph.models import AttachmentUnit, FineEdge, PlantNode, SignalLane

from ..netbox.adapters import build_object_reference
from .payloads import (
    ObjectReferencePayload,
    PolicyEvidenceArtifactSharePayload,
    PolicyEvidenceEdgeBridgePayload,
)


PASSIVE_NODE_TYPES = {'patch_panel', 'shuffle_module', 'cassette', 'passive_device'}


def _object_reference_payload(reference: dict) -> ObjectReferencePayload:
    return ObjectReferencePayload(
        app_label=reference['app_label'],
        model=reference['model'],
        pk=reference['pk'],
        display=reference['display'],
        registry_key=reference.get('registry_key'),
        url=reference.get('url'),
        path_resolver_url=reference.get('path_resolver_url'),
        blast_radius_url=reference.get('blast_radius_url'),
        lane_drilldown_url=reference.get('lane_drilldown_url'),
        lane_workspace_url=reference.get('lane_workspace_url'),
        signal_path_resolver_url=reference.get('signal_path_resolver_url'),
        signal_blast_radius_url=reference.get('signal_blast_radius_url'),
        health_url=reference.get('health_url'),
    )


def _reference_payload(obj) -> ObjectReferencePayload:
    return _object_reference_payload(build_object_reference(obj))


def _plane_pairs(*plane_sets) -> tuple[tuple[int, int], ...]:
    plane_ids = sorted({plane_id for plane_set in plane_sets for plane_id in plane_set})
    pairs = []
    for index, left_plane_id in enumerate(plane_ids):
        for right_plane_id in plane_ids[index + 1:]:
            pairs.append((left_plane_id, right_plane_id))
    return tuple(pairs)


def evaluate_shared_passive_artifact_evidence(*, attachment_units, plane_sets_by_attachment) -> tuple[PolicyEvidenceArtifactSharePayload, ...]:
    attachments_by_node = {}
    for attachment_unit in attachment_units:
        plant_node = attachment_unit.termination_point.plant_node
        if plant_node.node_type not in PASSIVE_NODE_TYPES:
            continue
        attachments_by_node.setdefault(plant_node.pk, {
            'plant_node': plant_node,
            'attachments': [],
            'plane_ids': set(),
        })
        attachments_by_node[plant_node.pk]['attachments'].append(attachment_unit)
        attachments_by_node[plant_node.pk]['plane_ids'].update(plane_sets_by_attachment.get(attachment_unit.pk, set()))

    lane_counts = dict(
        SignalLane.objects.filter(
            attachment_unit_id__in=[attachment_unit.pk for attachment_unit in attachment_units]
        ).values('attachment_unit_id').annotate(total=Count('pk')).values_list('attachment_unit_id', 'total')
    )

    evidence = []
    for entry in sorted(attachments_by_node.values(), key=lambda item: item['plant_node'].name):
        plane_ids = tuple(sorted(entry['plane_ids']))
        if len(plane_ids) <= 1:
            continue
        attachment_refs = tuple(
            _reference_payload(attachment_unit)
            for attachment_unit in sorted(entry['attachments'], key=lambda item: item.pk)
        )
        signal_lane_count = sum(lane_counts.get(attachment_unit.pk, 0) for attachment_unit in entry['attachments'])
        artifact_reference = _reference_payload(entry['plant_node'])
        evidence.append(
            PolicyEvidenceArtifactSharePayload(
                rule_id='shared_passive_artifact',
                artifact=artifact_reference,
                artifact_kind='plant_node',
                plane_ids=plane_ids,
                plane_pair_ids=_plane_pairs(plane_ids),
                attachment_unit_count=len(entry['attachments']),
                signal_lane_count=signal_lane_count,
                attachment_units=attachment_refs,
                representative_targets=(artifact_reference,) + attachment_refs[:2],
            )
        )
    return tuple(evidence)


def evaluate_cross_plane_edge_bridge_evidence(*, fabric, plane_sets_by_attachment) -> tuple[PolicyEvidenceEdgeBridgePayload, ...]:
    evidence = []
    fine_edges = FineEdge.objects.filter(
        granularity='attachment_unit',
        a_au__termination_point__plant_node__fabric=fabric,
    ).select_related('a_au', 'b_au', 'parent_coarse_edge')
    for fine_edge in fine_edges:
        if not fine_edge.a_au_id or not fine_edge.b_au_id:
            continue
        left_planes = tuple(sorted(plane_sets_by_attachment.get(fine_edge.a_au_id, set())))
        right_planes = tuple(sorted(plane_sets_by_attachment.get(fine_edge.b_au_id, set())))
        if not left_planes or not right_planes or not set(left_planes).isdisjoint(right_planes):
            continue
        attachment_refs = tuple(
            _reference_payload(attachment_unit)
            for attachment_unit in (fine_edge.a_au, fine_edge.b_au)
            if attachment_unit is not None
        )
        parent_artifacts = tuple(
            _reference_payload(obj)
            for obj in (fine_edge.parent_coarse_edge,)
            if obj is not None
        )
        edge_reference = _reference_payload(fine_edge)
        evidence.append(
            PolicyEvidenceEdgeBridgePayload(
                rule_id='cross_plane_fine_edge',
                edge=edge_reference,
                edge_kind=fine_edge.edge_type,
                left_plane_ids=left_planes,
                right_plane_ids=right_planes,
                plane_pair_ids=_plane_pairs(left_planes, right_planes),
                attachment_units=attachment_refs,
                parent_artifacts=parent_artifacts,
                representative_targets=(edge_reference,) + attachment_refs + parent_artifacts,
            )
        )
    return tuple(evidence)
