from collections import defaultdict

from django.contrib.contenttypes.models import ContentType

from netbox_plant_graph.models import AttachmentUnit, Fabric, FabricPlane, FineEdge, PlaneMembership, SignalLane

from ..netbox.adapters import build_object_reference
from .audits import run_plane_audit


def _normalize_fabric(fabric=None):
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return Fabric.objects.order_by('name', 'pk').first()
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _plane_ids_for_finding(finding):
    plane_ids = set()
    obj = finding.get('object') or {}
    metadata = finding.get('metadata') or {}

    if obj.get('registry_key') == 'fabricplane' and obj.get('pk') is not None:
        plane_ids.add(obj['pk'])

    for metadata_key in ('plane_ids', 'left_plane_ids', 'right_plane_ids'):
        plane_ids.update(metadata.get(metadata_key, ()))

    return plane_ids


def compute_fabric_health(*, fabric=None):
    fabric = _normalize_fabric(fabric=fabric)
    if fabric is None:
        return {
            'fabric': None,
            'status': 'unknown',
            'summary': {},
            'planes': (),
            'findings': {'counts': {}, 'total': 0},
        }

    attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
    planes = list(fabric.planes.order_by('plane_number'))
    audit_result = run_plane_audit(fabric=fabric)
    findings = audit_result.get('findings', [])

    findings_by_severity = defaultdict(int)
    findings_by_plane = defaultdict(list)
    for finding in findings:
        findings_by_severity[finding.get('severity', 'unknown')] += 1
        for plane_id in _plane_ids_for_finding(finding):
            findings_by_plane[plane_id].append(finding)

    attachment_membership_counts = defaultdict(int)
    for plane_id, member_id in PlaneMembership.objects.filter(
        plane__fabric=fabric,
        member_type=attachment_type,
    ).values_list('plane_id', 'member_id'):
        attachment_membership_counts[plane_id] += 1

    plane_rows = []
    healthy_planes = 0
    for plane in planes:
        plane_findings = findings_by_plane.get(plane.pk, [])
        plane_error_count = sum(1 for finding in plane_findings if finding.get('severity') == 'error')
        plane_warning_count = sum(1 for finding in plane_findings if finding.get('severity') == 'warning')
        if plane_error_count:
            plane_status = 'error'
        elif plane_warning_count:
            plane_status = 'warning'
        else:
            plane_status = 'healthy'
            healthy_planes += 1

        plane_rows.append({
            'plane': build_object_reference(plane),
            'status': plane_status,
            'attachment_membership_count': attachment_membership_counts.get(plane.pk, 0),
            'finding_count': len(plane_findings),
            'error_count': plane_error_count,
            'warning_count': plane_warning_count,
        })

    error_count = findings_by_severity.get('error', 0)
    warning_count = findings_by_severity.get('warning', 0)
    if error_count:
        status = 'error'
    elif warning_count:
        status = 'warning'
    else:
        status = 'healthy'

    return {
        'fabric': build_object_reference(fabric),
        'status': status,
        'summary': {
            'expected_plane_count': fabric.expected_plane_count,
            'planes_total': len(planes),
            'healthy_planes': healthy_planes,
            'attachment_units': AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric).count(),
            'signal_lanes': SignalLane.objects.filter(attachment_unit__termination_point__plant_node__fabric=fabric).count(),
            'fine_edges': FineEdge.objects.filter(a_au__termination_point__plant_node__fabric=fabric).count(),
        },
        'planes': tuple(plane_rows),
        'findings': {
            'counts': dict(sorted(findings_by_severity.items())),
            'total': len(findings),
        },
    }
