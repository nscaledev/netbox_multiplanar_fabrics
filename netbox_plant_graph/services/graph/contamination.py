import hashlib
import json

from django.db import models

from netbox_plant_graph.models import SignalLane

from ..netbox.adapters import build_object_reference
from .payloads import (
    ContaminationDomainPayload,
    ObjectReferencePayload,
    PolicyEvidenceArtifactSharePayload,
    PolicyEvidenceEdgeBridgePayload,
)


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


def _reference_identity(reference: ObjectReferencePayload) -> tuple[str | None, str, int]:
    return (reference.app_label, reference.model, reference.pk)


def _reference_sort_key(reference: ObjectReferencePayload) -> tuple[str | None, str, int]:
    return _reference_identity(reference)


def _evidence_key(evidence) -> str:
    if isinstance(evidence, PolicyEvidenceArtifactSharePayload):
        return f'artifact:{evidence.rule_id}:{evidence.artifact.app_label}:{evidence.artifact.model}:{evidence.artifact.pk}'
    return f'bridge:{evidence.rule_id}:{evidence.edge.app_label}:{evidence.edge.model}:{evidence.edge.pk}'


def _evidence_plane_pairs(evidence) -> set[tuple[int, int]]:
    return set(evidence.plane_pair_ids)


def _evidence_attachment_keys(evidence) -> set[tuple[str | None, str, int]]:
    return {_reference_identity(reference) for reference in evidence.attachment_units}


def _evidence_artifact_keys(evidence) -> set[tuple[str | None, str, int]]:
    if isinstance(evidence, PolicyEvidenceArtifactSharePayload):
        return {_reference_identity(evidence.artifact)}
    return {_reference_identity(reference) for reference in evidence.parent_artifacts}


def _connected(left, right) -> bool:
    if _evidence_plane_pairs(left).isdisjoint(_evidence_plane_pairs(right)):
        return False
    if _evidence_attachment_keys(left) & _evidence_attachment_keys(right):
        return True
    if _evidence_artifact_keys(left) & _evidence_artifact_keys(right):
        return True
    return False


def _domain_key(*, plane_ids, artifacts, bridges) -> str:
    payload = {
        'plane_ids': sorted(plane_ids),
        'artifacts': sorted(artifacts),
        'bridges': sorted(bridges),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def build_contamination_domains(
    *,
    fabric,
    artifact_shares: tuple[PolicyEvidenceArtifactSharePayload, ...] = (),
    edge_bridges: tuple[PolicyEvidenceEdgeBridgePayload, ...] = (),
) -> tuple[ContaminationDomainPayload, ...]:
    evidence = list(artifact_shares) + list(edge_bridges)
    if not evidence:
        return ()

    fabric_reference = _reference_payload(fabric)
    parents = {index: index for index in range(len(evidence))}

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left_index: int, right_index: int) -> None:
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root != right_root:
            parents[right_root] = left_root

    for left_index in range(len(evidence)):
        for right_index in range(left_index + 1, len(evidence)):
            if _connected(evidence[left_index], evidence[right_index]):
                union(left_index, right_index)

    components: dict[int, list] = {}
    for index, item in enumerate(evidence):
        components.setdefault(find(index), []).append(item)

    lane_counts = dict(
        SignalLane.objects.filter(
            attachment_unit_id__in=[
                reference.pk
                for component in components.values()
                for item in component
                for reference in item.attachment_units
            ]
        ).values('attachment_unit_id').annotate(total=models.Count('pk')).values_list('attachment_unit_id', 'total')
    )

    domains = []
    for component in components.values():
        artifacts = {}
        attachments = {}
        plane_ids = set()
        plane_pair_ids = set()
        evidence_keys = []
        artifact_shares_in_domain = []
        edge_bridges_in_domain = []
        bridge_keys = []
        artifact_keys = []

        for item in component:
            evidence_keys.append(_evidence_key(item))
            plane_pair_ids.update(item.plane_pair_ids)
            if isinstance(item, PolicyEvidenceArtifactSharePayload):
                plane_ids.update(item.plane_ids)
                artifacts[_reference_identity(item.artifact)] = item.artifact
                artifact_shares_in_domain.append(item)
                artifact_keys.append(f'{item.artifact.app_label}:{item.artifact.model}:{item.artifact.pk}')
            else:
                plane_ids.update(item.left_plane_ids)
                plane_ids.update(item.right_plane_ids)
                edge_bridges_in_domain.append(item)
                bridge_keys.append(f'{item.edge.app_label}:{item.edge.model}:{item.edge.pk}')
                for artifact in item.parent_artifacts:
                    artifacts[_reference_identity(artifact)] = artifact
                    artifact_keys.append(f'{artifact.app_label}:{artifact.model}:{artifact.pk}')
            for attachment in item.attachment_units:
                attachments[_reference_identity(attachment)] = attachment

        representative_targets = tuple(
            sorted(
                {
                    _reference_identity(reference): reference
                    for item in component
                    for reference in item.representative_targets
                }.values(),
                key=_reference_sort_key,
            )[:6]
        )
        domain_attachment_units = tuple(sorted(attachments.values(), key=_reference_sort_key))
        signal_lane_count = sum(lane_counts.get(reference.pk, 0) for reference in domain_attachment_units)
        domains.append(
            ContaminationDomainPayload(
                domain_key=_domain_key(
                    plane_ids=plane_ids,
                    artifacts=artifact_keys,
                    bridges=bridge_keys,
                ),
                fabric=fabric_reference,
                plane_ids=tuple(sorted(plane_ids)),
                plane_pair_ids=tuple(sorted(plane_pair_ids)),
                artifacts=tuple(sorted(artifacts.values(), key=_reference_sort_key)),
                attachment_units=domain_attachment_units,
                signal_lane_count=signal_lane_count,
                artifact_shares=tuple(sorted(artifact_shares_in_domain, key=lambda item: _reference_sort_key(item.artifact))),
                edge_bridges=tuple(sorted(edge_bridges_in_domain, key=lambda item: _reference_sort_key(item.edge))),
                representative_targets=representative_targets,
                evidence_keys=tuple(sorted(evidence_keys)),
            )
        )

    return tuple(sorted(domains, key=lambda item: (item.plane_ids, item.domain_key)))
