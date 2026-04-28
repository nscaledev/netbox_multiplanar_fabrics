from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import FieldDoesNotExist
from django.urls import reverse
from django.utils import timezone
from django.views import View
from netbox.object_actions import AddObject, BulkExport
from netbox.views import generic

from .choices import GraphResolutionChoices, SpatialNodeTypeChoices
from .detail_specs import DETAIL_SPECS
from . import filtersets as filterset_module
from . import forms as forms_module
from .forms import *  # noqa: F401,F403
from .models import AssemblyConnectorTemplate, AssemblyMappingTemplate, AssemblyTemplate, AttachmentUnit, AuditFinding, AuditFindingEvent, AuditRun, AuditSuppression, CoarseEdge, ConnectionTemplate, DeploymentPlan, DisjointnessException, Fabric, FabricPlane, FineEdge, LaneMap, PlaneMembership, PlantNode, RackPopulationTemplate, SignalLane, SpatialPlacement, SpatialTemplate, SpatialTemplateNode, TerminationPoint, TransferMap, UnresolvedStateObservation, UnresolvedStateSummary
from .object_registry import VIEW_OBJECT_SPECS, get_object_spec
from .services import (
    acknowledge_audit_finding,
    apply_audit_retention,
    approve_disjointness_exception,
    build_audit_finding_detail,
    build_audit_finding_details,
    build_audit_workflow_summary,
    build_disjointness_exception_review,
    build_lane_allocation_summary,
    build_lane_drilldown,
    build_lane_workspace,
    build_policy_dashboard,
    build_lane_set,
    build_policy_evaluation,
    build_policy_summary,
    build_unresolved_state_dashboard,
    build_unresolved_state_overview,
    compare_lane_allocations,
    compute_blast_radius,
    compute_fabric_health,
    describe_signal_resolution_error,
    list_related_unresolved_summaries,
    expire_disjointness_exception,
    list_contamination_domains,
    reactivate_disjointness_exception,
    reopen_audit_finding,
    normalize_lane_workspace_query,
    resolve_path,
    resolve_audit_finding,
    run_plane_audit,
    start_audit_finding_remediation,
    suppress_audit_finding,
    unsuppress_audit_finding,
)
from .services.graph.resolution import DEFAULT_RESOLUTION
from .services.graph.finding_fingerprints import build_audit_finding_fingerprint
from .services.netbox.adapters import build_object_reference
from .services.netbox.lookup import get_registry_key_for_object, get_registry_label, resolve_registry_object
from . import tables as tables_module
from .tables import *  # noqa: F401,F403


class MetadataDrivenDetailView(generic.ObjectView):
    template_name = 'netbox_plant_graph/object_detail.html'
    detail_spec = None

    def get_detail_spec(self):
        if self.detail_spec is None:
            raise AttributeError(f'{self.__class__.__name__} must define detail_spec')
        return self.detail_spec

    def build_detail_field(self, field_spec, instance):
        value = getattr(instance, field_spec.name, None)
        url = getattr(value, 'get_absolute_url', None)
        return {
            'label': field_spec.label,
            'value': value,
            'url': url() if callable(url) else None,
            'is_empty': value in (None, ''),
        }

    def get_extra_context(self, request, instance):
        detail_spec = self.get_detail_spec()
        return {
            'detail_spec': detail_spec,
            'detail_fields': [self.build_detail_field(field_spec, instance) for field_spec in detail_spec.fields],
            'object': instance,
            'supplementary_cards': _build_supplementary_cards(instance),
        }


def build_generated_detail_spec(spec):
    custom_spec = DETAIL_SPECS.get(spec.registry_key)
    if custom_spec is None:
        fields = tuple(
            SimpleNamespace(name=field_name, label=str(spec.model._meta.get_field(field_name).verbose_name).title())
            for field_name in spec.api.fields
            if field_name not in {'id', 'url'}
        )
    else:
        fields = custom_spec

    existing_field_names = {field.name for field in fields}
    if 'tenant' not in existing_field_names and 'resolved_tenant' not in existing_field_names:
        try:
            spec.model._meta.get_field('tenant')
            tenant_field_name = 'tenant'
        except FieldDoesNotExist:
            tenant_field_name = 'resolved_tenant'

        tenant_field = SimpleNamespace(name=tenant_field_name, label='Tenant')
        insert_at = 0
        for index, field in enumerate(fields):
            if field.name in {'fabric', 'template', 'plan', 'name', 'target', 'summary', 'finding'}:
                insert_at = index + 1
                break
        fields = fields[:insert_at] + (tenant_field,) + fields[insert_at:]
    return SimpleNamespace(card_title=spec.labels.singular, fields=fields)


OPERATIONAL_REGISTRY_KEYS = (
    'interface',
    'frontport',
    'rearport',
    'attachmentunit',
    'signallane',
    'terminationpoint',
    'plantnode',
    'coarseedge',
)

LANE_WORKSPACE_REGISTRY_KEYS = OPERATIONAL_REGISTRY_KEYS + ('fabricplane',)


def _parse_int(value, default=None):
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _operational_registry_choices():
    return _registry_choices(OPERATIONAL_REGISTRY_KEYS)


def _lane_workspace_registry_choices():
    return _registry_choices(LANE_WORKSPACE_REGISTRY_KEYS)


def _registry_choices(registry_keys):
    return tuple(
        {
            'value': registry_key,
            'label': get_registry_label(registry_key),
        }
        for registry_key in registry_keys
    )


def _resolution_choices():
    allowed_values = {'attachment_unit', 'signal_lane'}
    return tuple(
        {'value': value, 'label': label}
        for value, label in GraphResolutionChoices.CHOICES
        if value in allowed_values
    )


def _fabric_choices():
    return tuple(Fabric.objects.order_by('name', 'pk'))


def _plane_choices():
    return tuple(FabricPlane.objects.select_related('fabric').order_by('fabric__name', 'plane_number', 'pk'))


def _selected_fabric(fabric_id):
    if fabric_id in (None, ''):
        return Fabric.objects.order_by('name', 'pk').first()
    return Fabric.objects.filter(pk=_parse_int(fabric_id)).first()


def _selected_plane(plane_id):
    if plane_id in (None, ''):
        return None
    return FabricPlane.objects.select_related('fabric').filter(pk=_parse_int(plane_id)).first()


def _audit_dashboard_url(fabric):
    if fabric is None:
        return reverse('plugins:netbox_plant_graph:audit_dashboard')
    return f"{reverse('plugins:netbox_plant_graph:audit_dashboard')}?fabric_id={fabric.pk}"


def _policy_review_url(*, fabric=None, plane=None):
    params = {}
    if fabric is not None:
        params['fabric_id'] = fabric.pk if hasattr(fabric, 'pk') else fabric
    if plane is not None:
        params['plane_id'] = plane.pk if hasattr(plane, 'pk') else plane
    querystring = urlencode(params)
    base_url = reverse('plugins:netbox_plant_graph:policy_review')
    return f'{base_url}?{querystring}' if querystring else base_url


def _audit_event_list_url(*, fabric=None, event_type=None, created_after=None, created_before=None, run=None):
    params = {}
    if fabric is not None:
        params['fabric'] = fabric.pk if hasattr(fabric, 'pk') else fabric
    if event_type:
        params['event_type'] = event_type
    if created_after is not None:
        params['created_after'] = created_after.isoformat() if hasattr(created_after, 'isoformat') else created_after
    if created_before is not None:
        params['created_before'] = created_before.isoformat() if hasattr(created_before, 'isoformat') else created_before
    if run is not None:
        params['run'] = run.pk if hasattr(run, 'pk') else run
    querystring = urlencode(params)
    base_url = reverse('plugins:netbox_plant_graph:audit-finding-event_list')
    return f'{base_url}?{querystring}' if querystring else base_url


def _durable_audit_findings_url(
    *,
    fabric=None,
    plane=None,
    target=None,
    active=True,
    status=None,
    severity=None,
    finding_type=None,
    suppressed=None,
    min_age_days=None,
):
    params = {}
    if fabric is not None:
        params['fabric'] = fabric.pk if hasattr(fabric, 'pk') else fabric
    if plane is not None:
        params['plane'] = plane.pk if hasattr(plane, 'pk') else plane
    if target is not None:
        params['object_type'] = ContentType.objects.get_for_model(target, for_concrete_model=False).pk
        params['object_id'] = target.pk
    if active is not None:
        params['active'] = 'true' if active else 'false'
    if status:
        params['status'] = status
    if severity:
        params['severity'] = severity
    if finding_type:
        params['finding_type'] = finding_type
    if suppressed is not None:
        params['suppressed'] = 'true' if suppressed else 'false'
    if min_age_days not in (None, ''):
        params['min_age_days'] = min_age_days
    querystring = urlencode(params)
    base_url = reverse('plugins:netbox_plant_graph:audit-finding_list')
    return f'{base_url}?{querystring}' if querystring else base_url


def _unresolved_state_summaries_url(
    *,
    fabric=None,
    plane=None,
    target=None,
    cause_code=None,
    active=True,
):
    params = {}
    if fabric is not None:
        params['fabric'] = fabric.pk if hasattr(fabric, 'pk') else fabric
    if plane is not None:
        params['plane'] = plane.pk if hasattr(plane, 'pk') else plane
    if target is not None:
        params['owner_object_type'] = ContentType.objects.get_for_model(target, for_concrete_model=False).pk
        params['owner_object_id'] = target.pk
    if cause_code:
        params['cause_code'] = cause_code
    if active is not None:
        params['active'] = 'true' if active else 'false'
    querystring = urlencode(params)
    base_url = reverse('plugins:netbox_plant_graph:unresolved-state-summary_list')
    return f'{base_url}?{querystring}' if querystring else base_url


def _unresolved_state_observations_url(*, summary=None, build=None, related_audit_run=None):
    params = {}
    if summary is not None:
        params['summary'] = summary.pk if hasattr(summary, 'pk') else summary
    if build is not None:
        params['build'] = build.pk if hasattr(build, 'pk') else build
    if related_audit_run is not None:
        params['related_audit_run'] = related_audit_run.pk if hasattr(related_audit_run, 'pk') else related_audit_run
    querystring = urlencode(params)
    base_url = reverse('plugins:netbox_plant_graph:unresolved-state-observation_list')
    return f'{base_url}?{querystring}' if querystring else base_url


def _unresolved_state_target_url(target):
    if target is None:
        return _unresolved_state_summaries_url(active=True)
    if isinstance(target, Fabric):
        return _unresolved_state_summaries_url(fabric=target, active=True)
    if isinstance(target, FabricPlane):
        return _unresolved_state_summaries_url(fabric=target.fabric, plane=target, active=True)
    return _unresolved_state_summaries_url(target=target, active=True)


def _fabric_for_object(obj):
    if obj is None:
        return None
    if isinstance(obj, Fabric):
        return obj
    if isinstance(obj, FabricPlane):
        return obj.fabric
    if isinstance(obj, PlantNode):
        return obj.fabric
    if isinstance(obj, TerminationPoint):
        return obj.plant_node.fabric
    if isinstance(obj, AttachmentUnit):
        return obj.termination_point.plant_node.fabric
    if isinstance(obj, SignalLane):
        return obj.attachment_unit.termination_point.plant_node.fabric
    if isinstance(obj, CoarseEdge):
        return obj.a_tp.plant_node.fabric

    source_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    attachment = AttachmentUnit.objects.filter(source_type=source_type, source_id=obj.pk).select_related(
        'termination_point__plant_node__fabric'
    ).first()
    if attachment is not None:
        return attachment.termination_point.plant_node.fabric

    termination = TerminationPoint.objects.filter(source_type=source_type, source_id=obj.pk).select_related(
        'plant_node__fabric'
    ).first()
    if termination is not None:
        return termination.plant_node.fabric

    node = PlantNode.objects.filter(source_type=source_type, source_id=obj.pk).select_related('fabric').first()
    if node is not None:
        return node.fabric

    edge = CoarseEdge.objects.filter(source_type=source_type, source_id=obj.pk).select_related(
        'a_tp__plant_node__fabric'
    ).first()
    if edge is not None:
        return edge.a_tp.plant_node.fabric

    return None


def _graph_counts_for_fabric(fabric):
    if fabric is None:
        return ()
    counts = (
        ('Fabric Planes', FabricPlane.objects.filter(fabric=fabric).count()),
        ('Plant Nodes', PlantNode.objects.filter(fabric=fabric).count()),
        ('Termination Points', TerminationPoint.objects.filter(plant_node__fabric=fabric).count()),
        ('Attachment Units', AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric).count()),
        ('Signal Lanes', SignalLane.objects.filter(attachment_unit__termination_point__plant_node__fabric=fabric).count()),
        ('Coarse Edges', CoarseEdge.objects.filter(a_tp__plant_node__fabric=fabric).count()),
        ('Fine Edges', FineEdge.objects.filter(a_au__termination_point__plant_node__fabric=fabric).count()),
        ('Signal-Lane Fine Edges', FineEdge.objects.filter(a_lane__attachment_unit__termination_point__plant_node__fabric=fabric).count()),
        ('Transfer Maps', TransferMap.objects.filter(owner_node__fabric=fabric).count()),
        ('Lane Maps', LaneMap.objects.filter(owner_node__fabric=fabric).count()),
        ('Plane Memberships', PlaneMembership.objects.filter(plane__fabric=fabric).count()),
    )
    return tuple({'label': label, 'value': value} for label, value in counts)


def _finding_counts(findings):
    by_severity = {}
    by_type = {}
    for finding in findings:
        severity = finding.get('severity', 'unknown')
        finding_type = finding.get('finding_type', 'unknown')
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_type[finding_type] = by_type.get(finding_type, 0) + 1
    return {
        'by_severity': tuple(sorted(by_severity.items())),
        'by_type': tuple(sorted(by_type.items())),
    }


def _audit_finding_actions(finding):
    actions = []
    if finding.status == 'suppressed':
        actions.append({'label': 'Unsuppress', 'url': reverse('plugins:netbox_plant_graph:audit_finding_unsuppress', args=[finding.pk])})
        if finding.active:
            actions.append({'label': 'Resolve', 'url': reverse('plugins:netbox_plant_graph:audit_finding_resolve', args=[finding.pk])})
        return tuple(actions)
    if finding.status in {'open', 'acknowledged', 'in_progress'}:
        if finding.status == 'open':
            actions.append({'label': 'Acknowledge', 'url': reverse('plugins:netbox_plant_graph:audit_finding_acknowledge', args=[finding.pk])})
        if finding.status != 'in_progress':
            actions.append({'label': 'Start Remediation', 'url': reverse('plugins:netbox_plant_graph:audit_finding_start_remediation', args=[finding.pk])})
        actions.append({'label': 'Suppress', 'url': reverse('plugins:netbox_plant_graph:audit_finding_suppress', args=[finding.pk])})
        actions.append({'label': 'Resolve', 'url': reverse('plugins:netbox_plant_graph:audit_finding_resolve', args=[finding.pk])})
    else:
        actions.append({'label': 'Reopen', 'url': reverse('plugins:netbox_plant_graph:audit_finding_reopen', args=[finding.pk])})
    return tuple(actions)


def _disjointness_exception_actions(exception):
    effective_status = exception.effective_status
    if effective_status == 'approved' and exception.active:
        return (
            {'label': 'Expire', 'url': reverse('plugins:netbox_plant_graph:disjointness_exception_expire', args=[exception.pk])},
        )
    if effective_status == 'expired':
        return (
            {'label': 'Reactivate', 'url': reverse('plugins:netbox_plant_graph:disjointness_exception_reactivate', args=[exception.pk])},
        )
    return (
        {'label': 'Approve', 'url': reverse('plugins:netbox_plant_graph:disjointness_exception_approve', args=[exception.pk])},
    )


def _durable_live_audit_matches(*, fabric, findings):
    if fabric is None or not findings:
        return {}
    fingerprints = {
        build_audit_finding_fingerprint(finding)
        for finding in findings
    }
    durable_findings = AuditFinding.objects.filter(
        fabric=fabric,
        fingerprint__in=fingerprints,
    ).select_related(
        'assigned_to',
        'acknowledged_by',
    ).prefetch_related('suppressions')
    return {
        durable_finding.fingerprint: durable_finding
        for durable_finding in durable_findings
        if durable_finding.fingerprint
    }


def _build_supplementary_cards(instance):
    cards = []

    def add_lane_card(title, lane_set, summary, links=()):
        plane_label = ', '.join(str(plane_id) for plane_id in lane_set.plane_ids) if lane_set.plane_ids else 'None'
        cards.append({
            'title': title,
            'lines': (
                f"Attachment units: {lane_set.total_attachment_units}",
                f"Lanes present: {lane_set.present_lane_total} / {lane_set.expected_lane_total}",
                f"Mapped lanes: {lane_set.mapped_lane_total}",
                f"Plane set: {plane_label}",
                f"Lane consistency: {summary.lane_map_consistency}",
                f"Plane consistency: {summary.plane_consistency}",
            ),
            'links': links,
        })

    def add_policy_card(title, summary, links=(), extra_lines=()):
        if summary is None:
            return
        cards.append({
            'title': title,
            'lines': (
                f"Policy mode: {summary.policy_mode}",
                f"Artifact shares: {summary.artifact_share_count}",
                f"Edge bridges: {summary.edge_bridge_count}",
                f"Domains: {summary.contamination_domain_count}",
                f"Plane pairs: {summary.plane_pair_count}",
            ) + tuple(extra_lines),
            'links': links,
        })

    def add_unresolved_card(title, overview, links=(), include_recent=True):
        if overview is None:
            return
        cause_summary = ', '.join(
            f'{metric.name}: {metric.value}'
            for metric in overview.cause_counts[:3]
        ) or 'None'
        lines = [
            f"Active summaries: {overview.active_total}",
            f"Resolved summaries: {overview.resolved_total}",
            f"Top causes: {cause_summary}",
        ]
        if include_recent and overview.summaries:
            first_summary = overview.summaries[0]
            lines.append(
                f"Most recent: {first_summary.cause_code} ({first_summary.scope_label or first_summary.summary.display})"
            )
        cards.append({
            'title': title,
            'lines': tuple(lines),
            'links': links,
        })

    if isinstance(instance, Fabric):
        health = compute_fabric_health(fabric=instance)
        workflow = build_audit_workflow_summary(fabric=instance)
        policy_summary = build_policy_summary(fabric=instance)
        unresolved_overview = build_unresolved_state_overview(fabric=instance, limit=5)
        cards.append({
            'title': 'Fabric Health',
            'lines': (
                f"Status: {health['status']}",
                f"Healthy planes: {health['summary']['healthy_planes']} / {health['summary']['planes_total']}",
                f"Audit findings: {health['findings']['total']}",
                f"Active durable findings: {workflow.active_findings}",
                f"Suppressed findings: {workflow.suppressed_findings}",
            ),
            'links': (
                {'label': 'Health Page', 'url': health['fabric']['health_url']},
                {'label': 'Plane Audit', 'url': f"{reverse('plugins:netbox_plant_graph:plane_audit')}?fabric_id={instance.pk}"},
                {'label': 'Audit Dashboard', 'url': _audit_dashboard_url(instance)},
                {'label': 'Policy Review', 'url': _policy_review_url(fabric=instance)},
                {'label': 'Active Findings', 'url': _durable_audit_findings_url(fabric=instance, active=True)},
                {'label': 'Graph Overview', 'url': f"{reverse('plugins:netbox_plant_graph:graph_overview')}?fabric_id={instance.pk}"},
                {'label': 'Fabric Operations', 'url': reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': instance.pk})},
                {'label': 'Assign Planes', 'url': reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': instance.pk})},
            ),
            'actions': (
                {
                    'label': 'Rebuild Graph',
                    'url': reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': instance.pk}),
                    'style': 'warning',
                    'hidden_fields': ({'name': 'action', 'value': 'rebuild'},),
                },
            ),
        })
        add_policy_card(
            'Policy Review',
            policy_summary,
            links=(
                {'label': 'Policy Review', 'url': _policy_review_url(fabric=instance)},
                {'label': 'Plane Audit', 'url': f"{reverse('plugins:netbox_plant_graph:plane_audit')}?fabric_id={instance.pk}"},
            ),
            extra_lines=(
                f"Largest domain attachments: {policy_summary.largest_domain_attachment_units}",
                f"Largest domain signal lanes: {policy_summary.largest_domain_signal_lanes}",
            ) if policy_summary is not None else (),
        )
        add_unresolved_card(
            'Durable Unresolved State',
            unresolved_overview,
            links=(
                {'label': 'Unresolved Summaries', 'url': _unresolved_state_summaries_url(fabric=instance, active=True)},
                {'label': 'Graph Build Runs', 'url': reverse('plugins:netbox_plant_graph:graph-build-run_list')},
            ),
        )
    elif isinstance(instance, FabricPlane):
        health = compute_fabric_health(fabric=instance.fabric)
        policy_summary = build_policy_summary(fabric=instance.fabric, plane=instance)
        unresolved_overview = build_unresolved_state_overview(plane=instance, limit=5)
        plane_row = next((row for row in health['planes'] if row['plane']['pk'] == instance.pk), None)
        if plane_row is not None:
            cards.append({
                'title': 'Plane Health',
                'lines': (
                    f"Status: {plane_row['status']}",
                    f"Attachment memberships: {plane_row['attachment_membership_count']}",
                f"Findings: {plane_row['finding_count']}",
            ),
            'links': (
                {'label': 'Health Page', 'url': health['fabric']['health_url']},
                {'label': 'Plane Audit', 'url': f"{reverse('plugins:netbox_plant_graph:plane_audit')}?fabric_id={instance.fabric_id}"},
                {'label': 'Policy Review', 'url': _policy_review_url(fabric=instance.fabric, plane=instance)},
                {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
            ),
        })
        add_policy_card(
            'Plane Isolation',
            policy_summary,
            links=(
                {'label': 'Policy Review', 'url': _policy_review_url(fabric=instance.fabric, plane=instance)},
                {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
            ),
        )
        lane_set = build_lane_set(instance)
        summary = build_lane_allocation_summary(target=instance)
        add_lane_card(
            'Lane Coverage',
            lane_set,
            summary,
            links=(
                {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
            ),
        )
        add_unresolved_card(
            'Plane Unresolved State',
            unresolved_overview,
            links=(
                {'label': 'Unresolved Summaries', 'url': _unresolved_state_summaries_url(fabric=instance.fabric, plane=instance, active=True)},
                {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
            ),
        )
    elif isinstance(instance, AttachmentUnit):
        lane_drilldown = build_lane_drilldown(target=instance)
        lane_set = build_lane_set(instance)
        summary = build_lane_allocation_summary(target=instance)
        if lane_drilldown['total_signal_lanes'] > 0:
            cards.append({
                'title': 'Lane Overview',
                'lines': (
                    f"Signal lanes: {lane_drilldown['total_signal_lanes']}",
                    f"Attachment units with lanes: {lane_drilldown['total_attachment_units']}",
                    f"Mapped lanes: {summary.mapped_lane_total}",
                ),
                'links': (
                    {'label': 'Lane Drilldown', 'url': lane_drilldown['target']['lane_drilldown_url']},
                    {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
                    {'label': 'Signal-Lane Path', 'url': lane_drilldown['target']['signal_path_resolver_url']},
                    {'label': 'Signal-Lane Radius', 'url': lane_drilldown['target']['signal_blast_radius_url']},
                ),
            })
    elif isinstance(instance, SignalLane):
        lane_ref = build_object_reference(instance)
        attachment_ref = build_object_reference(instance.attachment_unit)
        termination_ref = build_object_reference(instance.attachment_unit.termination_point)
        node_ref = build_object_reference(instance.attachment_unit.termination_point.plant_node)
        cards.append({
            'title': 'Lane Context',
            'lines': (
                f"Attachment unit: {attachment_ref['display']}",
                f"Termination point: {termination_ref['display']}",
                f"Plant node: {node_ref['display']}",
            ),
            'linked_lines': (
                {'label': 'Attachment unit', 'reference': attachment_ref},
                {'label': 'Termination point', 'reference': termination_ref},
                {'label': 'Plant node', 'reference': node_ref},
            ),
                'links': (
                    {'label': 'Lane Drilldown', 'url': lane_ref['lane_drilldown_url']},
                    {'label': 'Lane Workspace', 'url': lane_ref.get('lane_workspace_url')},
                    {'label': 'Signal-Lane Path', 'url': lane_ref['signal_path_resolver_url']},
                    {'label': 'Signal-Lane Radius', 'url': lane_ref['signal_blast_radius_url']},
            ),
        })
    elif isinstance(instance, CoarseEdge):
        lane_set = build_lane_set(instance)
        summary = build_lane_allocation_summary(target=instance)
        add_lane_card(
            'Lane Coverage',
            lane_set,
            summary,
            links=(
                {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
                {'label': 'Path Resolver', 'url': build_object_reference(instance).get('path_resolver_url')},
            ),
        )
    elif isinstance(instance, PlantNode):
        lane_set = build_lane_set(instance)
        summary = build_lane_allocation_summary(target=instance)
        add_lane_card(
                'Lane Coverage',
                lane_set,
                summary,
                links=(
                    {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
                    {'label': 'Physical Cable Blast Radius', 'url': build_object_reference(instance).get('blast_radius_url')},
                ),
            )
        if instance.node_type in {'patch_panel', 'shuffle_module', 'cassette', 'passive_device'}:
            policy_summary = build_policy_summary(fabric=instance.fabric)
            matching_domains = tuple(
                domain
                for domain in list_contamination_domains(fabric=instance.fabric)
                if any(reference.pk == instance.pk and reference.model == 'plantnode' for reference in domain.artifacts)
            )
            add_policy_card(
                'Policy Contamination',
                policy_summary,
                links=(
                    {'label': 'Policy Review', 'url': _policy_review_url(fabric=instance.fabric)},
                    {'label': 'Plane Audit', 'url': f"{reverse('plugins:netbox_plant_graph:plane_audit')}?fabric_id={instance.fabric_id}"},
                ),
                extra_lines=(
                    f"Domains touching node: {len(matching_domains)}",
                ),
            )
    elif isinstance(instance, TerminationPoint):
        lane_set = build_lane_set(instance)
        summary = build_lane_allocation_summary(target=instance)
        add_lane_card(
            'Lane Coverage',
            lane_set,
            summary,
            links=(
                {'label': 'Lane Workspace', 'url': build_object_reference(instance).get('lane_workspace_url')},
                {'label': 'Lane Drilldown', 'url': build_object_reference(instance).get('lane_drilldown_url')},
            ),
        )
    elif isinstance(instance, AuditRun):
        event_list_url = f"{reverse('plugins:netbox_plant_graph:audit-finding-event_list')}?run={instance.pk}"
        cards.append({
            'title': 'Run Summary',
            'lines': (
                f"Status: {instance.status}",
                f"Scope: {instance.scope_label or instance.fabric}",
                f"Findings: {instance.finding_count}",
                f"New / Reopened / Resolved: {instance.new_count} / {instance.reopened_count} / {instance.resolved_count}",
            ),
            'links': (
                {'label': 'Fabric Detail', 'url': instance.fabric.get_absolute_url() if instance.fabric_id else None},
                {'label': 'Audit Dashboard', 'url': _audit_dashboard_url(instance.fabric) if instance.fabric_id else None},
                {'label': 'Active Findings', 'url': _durable_audit_findings_url(fabric=instance.fabric, active=True) if instance.fabric_id else None},
                {'label': 'Run Events', 'url': event_list_url},
            ),
        })
    elif isinstance(instance, UnresolvedStateSummary):
        cards.append({
            'title': 'Summary Lifecycle',
            'lines': (
                f"Active: {instance.active}",
                f"Observations: {instance.observations.count()}",
                f"First seen build: {instance.first_seen_build or 'None'}",
                f"Last seen build: {instance.last_seen_build or 'None'}",
            ),
            'links': (
                {'label': 'Observations', 'url': _unresolved_state_observations_url(summary=instance)},
                {'label': 'All Active Summaries', 'url': _unresolved_state_summaries_url(fabric=instance.fabric, active=True)},
                {'label': 'Graph Build Runs', 'url': reverse('plugins:netbox_plant_graph:graph-build-run_list')},
            ),
        })
    elif isinstance(instance, UnresolvedStateObservation):
        cards.append({
            'title': 'Observation Context',
            'lines': (
                f"Observed at: {instance.observed_at or 'Unknown'}",
                f"Summary cause: {instance.summary.cause_code}",
                f"Build: {instance.build or 'None'}",
            ),
            'links': (
                {'label': 'Summary Detail', 'url': instance.summary.get_absolute_url()},
                {'label': 'Observation Timeline', 'url': _unresolved_state_observations_url(summary=instance.summary)},
                {'label': 'Related Audit Run', 'url': instance.related_audit_run.get_absolute_url() if instance.related_audit_run_id else None},
            ),
        })
    elif isinstance(instance, AuditFinding):
        finding_detail = build_audit_finding_detail(
            {
                'finding_type': instance.finding_type,
                'severity': instance.severity,
                'object': build_object_reference(instance.object),
                'message': instance.message,
                'metadata': instance.metadata,
            },
            source_finding_id=instance.pk,
        ) if instance.object is not None else None
        recent_events = tuple(instance.events.select_related('actor').all()[:5])
        active_suppression = instance.suppressions.filter(active=True).order_by('-created', '-pk').first()
        related_unresolved_summaries = list_related_unresolved_summaries(
            fabric=instance.fabric,
            audit_finding=instance,
            active_only=False,
            limit=5,
        )
        cards.append({
            'title': 'Workflow State',
            'lines': (
                f"Status: {instance.status}",
                f"Assigned to: {instance.assigned_to or 'None'}",
                f"Acknowledged by: {instance.acknowledged_by or 'None'}",
                f"First seen: {instance.first_seen_at or 'Unknown'}",
                f"Last seen: {instance.last_seen_at or 'Unknown'}",
                f"Suppression: {active_suppression.expires_at if active_suppression is not None else 'None'}",
            ),
            'links': tuple(
                {'label': action.label, 'url': action.url}
                for action in (finding_detail.action_links if finding_detail is not None else ())
            ),
            'actions': _audit_finding_actions(instance),
        })
        if recent_events:
            cards.append({
                'title': 'Recent Events',
                'lines': tuple(
                    f"{event.created}: {event.event_type} ({event.old_status or 'none'} -> {event.new_status or 'none'})"
                    + (f" by {event.actor}" if event.actor else '')
                    + (f" - {event.message}" if event.message else '')
                    for event in recent_events
                ),
                'links': (
                {'label': 'All Events', 'url': reverse('plugins:netbox_plant_graph:audit-finding-event_list')},
                ),
            })
        if related_unresolved_summaries:
            cards.append({
                'title': 'Related Unresolved Summaries',
                'linked_lines': tuple(
                    {
                        'label': summary.cause_code,
                        'reference': summary.summary,
                    }
                    for summary in related_unresolved_summaries
                ),
                'lines': tuple(
                    f"{summary.summary_kind}: {summary.scope_label or summary.summary.display}"
                    for summary in related_unresolved_summaries
                ),
                'links': (
                    {'label': 'All Unresolved Summaries', 'url': _unresolved_state_summaries_url(fabric=instance.fabric, active=True) if instance.fabric_id else None},
                ),
            })
        if active_suppression is not None:
            cards.append({
                'title': 'Active Suppression',
                'lines': (
                    f"Created by: {active_suppression.created_by or 'Unknown'}",
                    f"Expires at: {active_suppression.expires_at or 'Never'}",
                    f"Reason: {active_suppression.reason or 'None'}",
                ),
                'links': (
                    {'label': 'Suppression Detail', 'url': active_suppression.get_absolute_url()},
                ),
            })
    elif isinstance(instance, AuditSuppression):
        cards.append({
            'title': 'Suppression Summary',
            'lines': (
                f"Finding: {instance.finding}",
                f"Active: {instance.active}",
                f"Expires at: {instance.expires_at or 'Never'}",
            ),
            'linked_lines': (
                {'label': 'Finding', 'reference': build_object_reference(instance.finding)},
            ),
            'links': (
                {'label': 'Finding Detail', 'url': instance.finding.get_absolute_url()},
                {'label': 'Audit Dashboard', 'url': _audit_dashboard_url(instance.finding.fabric) if instance.finding.fabric_id else None},
            ),
            'actions': (
                ({'label': 'Unsuppress', 'url': reverse('plugins:netbox_plant_graph:audit_finding_unsuppress', args=[instance.finding_id])},)
                if instance.active else ()
            ),
        })
    elif isinstance(instance, AuditFindingEvent):
        finding_reference = build_object_reference(instance.finding)
        run_reference = build_object_reference(instance.run) if instance.run_id else None
        cards.append({
            'title': 'Event Summary',
            'linked_lines': tuple(
                line for line in (
                    {'label': 'Finding', 'reference': finding_reference},
                    {'label': 'Run', 'reference': run_reference} if run_reference is not None else None,
                ) if line is not None
            ),
            'lines': (
                f"Type: {instance.event_type}",
                f"Transition: {instance.old_status or 'none'} -> {instance.new_status or 'none'}",
                f"Actor: {instance.actor or 'System'}",
            ),
            'links': (
                {'label': 'Finding Detail', 'url': instance.finding.get_absolute_url()},
                {'label': 'Run Detail', 'url': instance.run.get_absolute_url() if instance.run_id else None},
            ),
        })
    elif isinstance(instance, DisjointnessException):
        target_reference = build_object_reference(instance.target) if instance.target is not None else None
        cards.append({
            'title': 'Exception Lifecycle',
            'linked_lines': tuple(
                line for line in (
                    {'label': 'Target', 'reference': target_reference} if target_reference is not None else None,
                ) if line is not None
            ),
            'lines': (
                f"Effective status: {instance.effective_status}",
                f"Plane pair: {instance.plane_a or 'None'} / {instance.plane_b or 'None'}",
                f"Scope kind: {instance.scope_kind}",
                f"Expires at: {instance.expires_at or 'Never'}",
                f"Approved by: {instance.approved_by or 'None'}",
            ),
            'links': (
                {'label': 'Policy Review', 'url': _policy_review_url(fabric=instance.fabric)},
                {'label': 'Fabric Detail', 'url': instance.fabric.get_absolute_url() if instance.fabric_id else None},
            ),
            'actions': _disjointness_exception_actions(instance),
        })
    elif isinstance(instance, AssemblyTemplate):
        a_count = instance.connectors.filter(side='A').count()
        b_count = instance.connectors.filter(side='B').count()
        mapping_count = instance.mappings.count()
        cards.append({
            'title': 'Assembly Summary',
            'lines': (
                f"A-side connectors: {a_count}",
                f"B-side connectors: {b_count}",
                f"Total mappings: {mapping_count}",
            ),
            'links': (
                {'label': 'Stamp Passive Device', 'url': reverse('plugins:netbox_plant_graph:assembly_stamp_wizard', kwargs={'pk': instance.pk})},
                {'label': 'Template Builder', 'url': reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': instance.pk})},
            ),
        })
    elif isinstance(instance, SpatialTemplate):
        root_count = instance.nodes.filter(parent__isnull=True).count()
        total_count = instance.nodes.count()
        cards.append({
            'title': 'Template Structure',
            'lines': (
                f"Root node type: {instance.get_root_node_type_display()}",
                f"Root nodes: {root_count}",
                f"Total nodes: {total_count}",
            ),
            'links': (
                {'label': 'Compose Template', 'url': reverse('plugins:netbox_plant_graph:spatial_template_compose', kwargs={'pk': instance.pk})},
                {'label': 'Stamp Spatial Template', 'url': reverse('plugins:netbox_plant_graph:spatial_stamp_wizard', kwargs={'pk': instance.pk})},
            ),
        })
    elif isinstance(instance, RackPopulationTemplate):
        slot_count = instance.slots.count()
        rack_type_label = str(instance.rack_type) if instance.rack_type_id else 'Any'
        cards.append({
            'title': 'Slot Summary',
            'lines': (
                f"Total slots: {slot_count}",
                f"Rack type: {rack_type_label}",
            ),
            'links': (
                {'label': 'Stamp Rack Population', 'url': reverse('plugins:netbox_plant_graph:rack_population_stamp_wizard', kwargs={'pk': instance.pk})},
            ),
        })
    elif isinstance(instance, DeploymentPlan):
        records = instance.stamp_records.all()
        pending = records.filter(status='pending').count()
        stamped = records.filter(status__in=('stamped', 'validated')).count()
        failed = records.filter(status='failed').count()
        cards.append({
            'title': 'Plan Summary',
            'lines': (
                f"Status: {instance.get_status_display()}",
                f"Pending stamps: {pending}",
                f"Stamped: {stamped}",
                f"Failed: {failed}",
            ),
            'links': (
                {'label': 'Template Library', 'url': reverse('plugins:netbox_plant_graph:template_library')},
                {'label': 'Workflow', 'url': reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': instance.pk})},
            ),
        })
    return tuple(cards)


def build_list_view_class(spec):
    actions = [BulkExport]
    if spec.view.supports_create:
        actions.insert(0, AddObject)

    return type(spec.view.list_class_name, (generic.ObjectListView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'table': getattr(tables_module, spec.table.class_name),
        'filterset': getattr(filterset_module, spec.filterset.class_name),
        'filterset_form': getattr(forms_module, spec.filter_form.class_name),
        'actions': tuple(actions),
    })


def build_detail_view_class(spec):
    return type(spec.view.detail_class_name, (MetadataDrivenDetailView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'detail_spec': build_generated_detail_spec(spec),
    })


def build_edit_view_class(spec):
    if spec.view.edit_class_name is None:
        return None
    return type(spec.view.edit_class_name, (generic.ObjectEditView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
        'form': getattr(forms_module, spec.form.class_name),
    })


def build_delete_view_class(spec):
    if spec.view.delete_class_name is None:
        return None
    return type(spec.view.delete_class_name, (generic.ObjectDeleteView,), {
        '__module__': __name__,
        'queryset': spec.model.objects.all(),
    })


for object_spec in VIEW_OBJECT_SPECS:
    globals()[object_spec.view.list_class_name] = build_list_view_class(object_spec)
    globals()[object_spec.view.detail_class_name] = build_detail_view_class(object_spec)
    edit_view = build_edit_view_class(object_spec)
    if edit_view is not None:
        globals()[object_spec.view.edit_class_name] = edit_view
    delete_view = build_delete_view_class(object_spec)
    if delete_view is not None:
        globals()[object_spec.view.delete_class_name] = delete_view


class GraphOverviewView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        return render(request, 'netbox_plant_graph/graph_overview.html', {
            'page_title': 'Graph Overview',
            'fabrics': _fabric_choices(),
            'selected_fabric': selected_fabric,
            'graph_counts': _graph_counts_for_fabric(selected_fabric),
        })


class HealthView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        health = compute_fabric_health(fabric=selected_fabric) if selected_fabric is not None else None
        unresolved_overview = build_unresolved_state_overview(fabric=selected_fabric, limit=5) if selected_fabric is not None else None
        if health is not None and selected_fabric is not None:
            for plane_row in health['planes']:
                plane_row['durable_findings_url'] = _durable_audit_findings_url(
                    fabric=selected_fabric,
                    plane=plane_row['plane']['pk'],
                    active=True,
                )
        return render(request, 'netbox_plant_graph/health.html', {
            'page_title': 'Health',
            'fabrics': _fabric_choices(),
            'selected_fabric': selected_fabric,
            'health': health,
            'unresolved_overview': unresolved_overview,
            'unresolved_summaries_url': _unresolved_state_target_url(selected_fabric) if selected_fabric is not None else None,
        })


class AuditDashboardView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        workflow_summary = build_audit_workflow_summary(fabric=selected_fabric) if selected_fabric is not None else None
        policy_dashboard = build_policy_dashboard(fabric=selected_fabric) if selected_fabric is not None else None
        unresolved_dashboard = build_unresolved_state_dashboard(fabric=selected_fabric) if selected_fabric is not None else None
        finding_links = {}
        status_count_rows = ()
        severity_count_rows = ()
        type_count_rows = ()
        churn_window_rows = ()
        all_events_url = None
        policy_plane_pair_rows = ()
        policy_domain_rows = ()
        unresolved_links = {}
        unresolved_cause_rows = ()
        unresolved_oldest_rows = ()
        if selected_fabric is not None and workflow_summary is not None:
            now = timezone.now()
            finding_links = {
                'active': _durable_audit_findings_url(fabric=selected_fabric, active=True),
                'resolved': _durable_audit_findings_url(fabric=selected_fabric, active=False, status='resolved'),
                'suppressed': _durable_audit_findings_url(fabric=selected_fabric, active=True, suppressed=True),
                'stale_7d': _durable_audit_findings_url(fabric=selected_fabric, active=True, min_age_days=7),
                'stale_30d': _durable_audit_findings_url(fabric=selected_fabric, active=True, min_age_days=30),
            }
            all_events_url = _audit_event_list_url(fabric=selected_fabric)
            status_count_rows = tuple(
                {
                    'name': metric.name,
                    'value': metric.value,
                    'url': _durable_audit_findings_url(fabric=selected_fabric, active=True, status=metric.name),
                }
                for metric in workflow_summary.status_counts
            )
            severity_count_rows = tuple(
                {
                    'name': metric.name,
                    'value': metric.value,
                    'url': _durable_audit_findings_url(fabric=selected_fabric, active=True, severity=metric.name),
                }
                for metric in workflow_summary.severity_counts
            )
            type_count_rows = tuple(
                {
                    'name': metric.name,
                    'value': metric.value,
                    'url': _durable_audit_findings_url(fabric=selected_fabric, active=True, finding_type=metric.name),
                }
                for metric in workflow_summary.type_counts
            )
            churn_window_rows = tuple(
                {
                    'label': window.label,
                    'days': window.days,
                    'opened': {
                        'count': window.opened_count,
                        'url': _audit_event_list_url(
                            fabric=selected_fabric,
                            event_type='opened',
                            created_after=now - timedelta(days=window.days),
                        ),
                    },
                    'reopened': {
                        'count': window.reopened_count,
                        'url': _audit_event_list_url(
                            fabric=selected_fabric,
                            event_type='reopened',
                            created_after=now - timedelta(days=window.days),
                        ),
                    },
                    'resolved': {
                        'count': window.resolved_count,
                        'url': _audit_event_list_url(
                            fabric=selected_fabric,
                            event_type='resolved',
                            created_after=now - timedelta(days=window.days),
                        ),
                    },
                    'auto_resolved': {
                        'count': window.auto_resolved_count,
                        'url': _audit_event_list_url(
                            fabric=selected_fabric,
                            event_type='auto_resolved',
                            created_after=now - timedelta(days=window.days),
                        ),
                    },
                    'suppressed': {
                        'count': window.suppressed_count,
                        'url': _audit_event_list_url(
                            fabric=selected_fabric,
                            event_type='suppressed',
                            created_after=now - timedelta(days=window.days),
                        ),
                    },
                }
                for window in workflow_summary.churn_windows
            )
        if selected_fabric is not None and policy_dashboard is not None:
            policy_plane_pair_rows = tuple(
                {
                    'plane_pair_label': f"Planes {row.plane_pair_ids[0]} / {row.plane_pair_ids[1]}",
                    'row': row,
                    'policy_review_url': _policy_review_url(fabric=selected_fabric),
                }
                for row in policy_dashboard.plane_pairs
            )
            policy_domain_rows = tuple(
                {
                    'domain': row,
                    'workspace_url': next(
                        (
                            reference.lane_workspace_url
                            for reference in row.artifacts + row.representative_targets
                            if reference.lane_workspace_url
                        ),
                        _policy_review_url(fabric=selected_fabric),
                    ),
                    'policy_review_url': _policy_review_url(fabric=selected_fabric),
                }
                for row in policy_dashboard.top_domains
            )
        if selected_fabric is not None and unresolved_dashboard is not None:
            unresolved_links = {
                'active': _unresolved_state_summaries_url(fabric=selected_fabric, active=True),
                'stale_7d': _unresolved_state_summaries_url(fabric=selected_fabric, active=True),
            }
            unresolved_cause_rows = tuple(
                {
                    'name': metric.name,
                    'value': metric.value,
                    'url': _unresolved_state_summaries_url(
                        fabric=selected_fabric,
                        cause_code=metric.name,
                        active=True,
                    ),
                }
                for metric in unresolved_dashboard.cause_counts
            )
            unresolved_oldest_rows = tuple(
                {
                    'summary': summary,
                    'workspace_url': next(
                        (
                            reference.get('lane_workspace_url')
                            for reference in (summary.owner_object, summary.representative_object)
                            if reference is not None and reference.get('lane_workspace_url')
                        ),
                        None,
                    ),
                }
                for summary in unresolved_dashboard.oldest_active_summaries
            )
        return render(request, 'netbox_plant_graph/audit_dashboard.html', {
            'page_title': 'Audit Dashboard',
            'fabrics': _fabric_choices(),
            'selected_fabric': selected_fabric,
            'workflow_summary': workflow_summary,
            'policy_dashboard': policy_dashboard,
            'unresolved_dashboard': unresolved_dashboard,
            'finding_links': finding_links,
            'unresolved_links': unresolved_links,
            'status_count_rows': status_count_rows,
            'severity_count_rows': severity_count_rows,
            'type_count_rows': type_count_rows,
            'unresolved_cause_rows': unresolved_cause_rows,
            'unresolved_oldest_rows': unresolved_oldest_rows,
            'churn_window_rows': churn_window_rows,
            'all_events_url': all_events_url,
            'policy_plane_pair_rows': policy_plane_pair_rows,
            'policy_domain_rows': policy_domain_rows,
        })


class PolicyReviewView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        selected_plane = _selected_plane(request.GET.get('plane_id'))
        evaluation = None
        exception_review = None
        plane_filter_note = None
        active_plane_filter = None
        if selected_fabric is not None:
            evaluation = build_policy_evaluation(fabric=selected_fabric)
            exception_review = build_disjointness_exception_review(
                fabric=selected_fabric,
                evaluation=evaluation,
            )
            if selected_plane is not None and selected_plane.fabric_id == selected_fabric.pk:
                active_plane_filter = selected_plane.pk
                plane_filter_note = f'Plane {selected_plane.plane_number} selected for focused review.'
        domain_rows = ()
        exception_rows = ()
        if evaluation is not None:
            domain_matches = exception_review['domain_matches'] if exception_review is not None else {}
            domain_rows = tuple(
                {
                    'domain': domain,
                    'matched_exceptions': domain_matches.get(domain.domain_key, ()),
                    'workspace_url': next(
                        (
                            reference.lane_workspace_url
                            for reference in domain.artifacts
                            if reference.lane_workspace_url
                        ),
                        _policy_review_url(fabric=selected_fabric),
                    ),
                    'finding_url': _durable_audit_findings_url(
                        fabric=selected_fabric,
                        active=True,
                    ),
                }
                for domain in evaluation.contamination_domains
                if active_plane_filter is None or active_plane_filter in domain.plane_ids
            )
            exception_rows = tuple(
                {
                    'exception': exception,
                    'target_reference': build_object_reference(exception.target) if exception.target is not None else None,
                    'covered': exception.pk in exception_review['covered_exception_ids'],
                    'actions': _disjointness_exception_actions(exception),
                }
                for exception in exception_review['active_exceptions']
                if active_plane_filter is None or active_plane_filter in exception.plane_pair_ids
            )
        return render(request, 'netbox_plant_graph/policy_review.html', {
            'page_title': 'Policy Review',
            'fabrics': _fabric_choices(),
            'plane_choices': _plane_choices(),
            'selected_fabric': selected_fabric,
            'selected_plane': selected_plane,
            'evaluation': evaluation,
            'exception_review': exception_review,
            'plane_filter_note': plane_filter_note,
            'domain_rows': domain_rows,
            'exception_rows': exception_rows,
        })


class PathResolverView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def _build_form_query(self, query, source, destination):
        from .forms import path_resolver_selector_field_name

        form_query = query.copy()
        if source is not None:
            form_query[path_resolver_selector_field_name('source', query['source_registry_key'])] = str(source.pk)
        if destination is not None:
            form_query[path_resolver_selector_field_name('destination', query['destination_registry_key'])] = str(
                destination.pk
            )
        return form_query

    def get(self, request):
        from .forms import PathResolverForm, path_resolver_selector_specs

        query = {
            'source_registry_key': request.GET.get('source_registry_key', 'attachmentunit'),
            'source_id': request.GET.get('source_id', ''),
            'destination_registry_key': request.GET.get('destination_registry_key', 'attachmentunit'),
            'destination_id': request.GET.get('destination_id', ''),
            'plane_id': request.GET.get('plane_id', ''),
            'resolution': request.GET.get('resolution', DEFAULT_RESOLUTION),
            'max_depth': request.GET.get('max_depth', '128'),
        }
        result = None
        error = None

        source = resolve_registry_object(query['source_registry_key'], query['source_id'])
        destination_requested = bool(query['destination_id'])
        destination = resolve_registry_object(query['destination_registry_key'], query['destination_id']) if destination_requested else None
        plane = _selected_plane(query['plane_id'])
        max_depth = _parse_int(query['max_depth'], 128) or 128
        form = PathResolverForm(data=self._build_form_query(query, source, destination) if request.GET else None)

        if query['source_id']:
            if source is None:
                error = 'Select a valid source object.'
            elif destination_requested and destination is None:
                error = 'Select a valid destination object.'
            elif query['plane_id'] and plane is None:
                error = 'Select a valid plane filter.'
            elif query['resolution'] == 'signal_lane':
                error = describe_signal_resolution_error(source)
                if error is None and destination_requested:
                    error = describe_signal_resolution_error(destination)
                if error is None:
                    result = resolve_path(
                        source=source,
                        destination=destination,
                        plane=plane,
                        resolution=query['resolution'],
                        max_depth=max_depth,
                    )
            else:
                result = resolve_path(
                    source=source,
                    destination=destination,
                    plane=plane,
                    resolution=query['resolution'],
                    max_depth=max_depth,
                )

        return render(request, 'netbox_plant_graph/path_detail.html', {
            'page_title': 'Path Resolver',
            'form': form,
            'selector_specs': path_resolver_selector_specs(),
            'query': query,
            'result': result,
            'error': error,
        })


class PlaneAuditView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        selected_fabric = _selected_fabric(request.GET.get('fabric_id'))
        result = run_plane_audit(fabric=selected_fabric) if selected_fabric is not None else None
        findings = result.get('findings', []) if result is not None else []
        durable_matches = _durable_live_audit_matches(fabric=selected_fabric, findings=findings)
        finding_rows = []
        for finding in findings:
            durable_finding = durable_matches.get(build_audit_finding_fingerprint(finding))
            detail = build_audit_finding_details(
                (finding,),
                source_finding_id=durable_finding.pk if durable_finding is not None else None,
            )[0]
            active_suppression = None
            if durable_finding is not None:
                active_suppression = durable_finding.suppressions.filter(active=True).order_by('-created', '-pk').first()
            related_unresolved_summaries = list_related_unresolved_summaries(
                fabric=selected_fabric,
                finding=finding,
                audit_finding=durable_finding,
                active_only=True,
                limit=5,
            )
            finding_rows.append({
                'finding': finding,
                'detail': detail,
                'durable_finding': durable_finding,
                'durable_actions': _audit_finding_actions(durable_finding) if durable_finding is not None else (),
                'active_suppression': active_suppression,
                'related_unresolved_summaries': related_unresolved_summaries,
            })
        return render(request, 'netbox_plant_graph/plane_audit.html', {
            'page_title': 'Plane Audit',
            'fabrics': _fabric_choices(),
            'selected_fabric': selected_fabric,
            'result': result,
            'finding_counts': _finding_counts(findings),
            'finding_rows': tuple(finding_rows),
        })


class LaneDrilldownView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        query = {
            'target_registry_key': request.GET.get('target_registry_key', 'attachmentunit'),
            'target_id': request.GET.get('target_id', ''),
            'lane_index': request.GET.get('lane_index', ''),
        }
        result = None
        error = None
        target = resolve_registry_object(query['target_registry_key'], query['target_id'])
        lane_index = _parse_int(query['lane_index'])
        if query['target_id']:
            if target is None:
                error = 'Select a valid target object.'
            else:
                result = build_lane_drilldown(target=target, lane_index=lane_index)

        return render(request, 'netbox_plant_graph/lane_drilldown.html', {
            'page_title': 'Lane Drilldown',
            'registry_choices': _operational_registry_choices(),
            'query': query,
            'result': result,
            'error': error,
        })


def _lane_workspace_export_rows(workspace):
    if workspace.group_by == 'path':
        return [
            {
                'group_key': group.key,
                'label': group.label,
                'attachment_units': group.attachment_unit_count,
                'lanes': group.lane_count,
                'plane_ids': ','.join(str(item) for item in group.plane_ids),
                'representative_lane_index': group.representative_lane_index,
                'path_summary': group.path_summary,
            }
            for group in workspace.path_groups
        ]

    groups = workspace.attachment_groups
    if workspace.group_by == 'node':
        groups = workspace.node_groups
    elif workspace.group_by == 'plane':
        groups = workspace.plane_groups
    return [
        {
            'group_key': group.key,
            'label': group.label,
            'attachment_units': group.attachment_unit_count,
            'expected_lanes': group.expected_lane_total,
            'present_lanes': group.present_lane_total,
            'mapped_lanes': group.mapped_lane_total,
            'selected': group.selected,
        }
        for group in groups
    ]


def _lane_workspace_csv_response(*, workspace):
    rows = _lane_workspace_export_rows(workspace)
    if workspace.group_by == 'path':
        fieldnames = ('group_key', 'label', 'attachment_units', 'lanes', 'plane_ids', 'representative_lane_index', 'path_summary')
    else:
        fieldnames = ('group_key', 'label', 'attachment_units', 'expected_lanes', 'present_lanes', 'mapped_lanes', 'selected')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="lane-workspace.csv"'
    response.write(','.join(fieldnames) + '\n')
    for row in rows:
        response.write(','.join(str(row.get(field, '')) for field in fieldnames) + '\n')
    return response


def _lane_workspace_json_response(*, workspace):
    return JsonResponse(asdict(workspace))


class LaneWorkspaceView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        raw_query = normalize_lane_workspace_query(
            target_registry_key=request.GET.get('target_registry_key', 'attachmentunit'),
            target_id=request.GET.get('target_id', ''),
            group_by=request.GET.get('group_by'),
            mode=request.GET.get('mode'),
            focus=request.GET.get('focus'),
            group_key=request.GET.get('group_key'),
            lane_index=request.GET.get('lane_index'),
            path_lane_index=request.GET.get('path_lane_index'),
            plane_id=request.GET.get('plane_id'),
            compare_registry_key=request.GET.get('compare_registry_key'),
            compare_id=request.GET.get('compare_id', ''),
            source_finding_id=request.GET.get('source_finding_id'),
            export_format=request.GET.get('export'),
        )
        lane_set = None
        allocation_summary = None
        workspace = None
        unresolved_overview = None
        error = None
        target = resolve_registry_object(raw_query.target_registry_key, raw_query.target_id)
        compare_target = resolve_registry_object(
            request.GET.get('compare_registry_key', ''),
            request.GET.get('compare_id', ''),
        ) if request.GET.get('compare_id') else None
        source_finding = AuditFinding.objects.filter(pk=raw_query.source_finding_id).first() if raw_query.source_finding_id is not None else None
        query = normalize_lane_workspace_query(
            target_registry_key=request.GET.get('target_registry_key', raw_query.target_registry_key),
            target_id=request.GET.get('target_id', raw_query.target_id),
            group_by=request.GET.get('group_by'),
            mode=request.GET.get('mode'),
            focus=request.GET.get('focus'),
            group_key=request.GET.get('group_key'),
            lane_index=request.GET.get('lane_index'),
            path_lane_index=request.GET.get('path_lane_index'),
            plane_id=request.GET.get('plane_id'),
            compare_registry_key=request.GET.get('compare_registry_key'),
            compare_id=request.GET.get('compare_id', ''),
            source_finding_id=request.GET.get('source_finding_id'),
            export_format=request.GET.get('export'),
            target=target,
        )
        if raw_query.target_id:
            if target is None:
                error = 'Select a valid target object.'
            else:
                workspace = build_lane_workspace(
                    target=target,
                    compare_target=compare_target,
                    source_finding=source_finding,
                    query=query,
                )
                lane_set = workspace.lane_set
                allocation_summary = workspace.allocation_summary
                query = workspace.query
                unresolved_overview = build_unresolved_state_overview(target=target, limit=8)
                if query.export_format == 'csv':
                    return _lane_workspace_csv_response(workspace=workspace)
                if query.export_format == 'json':
                    return _lane_workspace_json_response(workspace=workspace)

        return render(request, 'netbox_plant_graph/lane_workspace.html', {
            'page_title': 'Lane Workspace',
            'registry_choices': _lane_workspace_registry_choices(),
            'query': query,
            'workspace': workspace,
            'lane_set': lane_set,
            'allocation_summary': allocation_summary,
            'unresolved_overview': unresolved_overview,
            'unresolved_summaries_url': _unresolved_state_target_url(target) if target is not None else None,
            'error': error,
        })


class LaneCompareView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        query = {
            'baseline_registry_key': request.GET.get('baseline_registry_key', 'attachmentunit'),
            'baseline_id': request.GET.get('baseline_id', ''),
            'candidate_registry_key': request.GET.get('candidate_registry_key', 'attachmentunit'),
            'candidate_id': request.GET.get('candidate_id', ''),
        }
        compare_result = None
        error = None
        baseline_target = resolve_registry_object(query['baseline_registry_key'], query['baseline_id'])
        candidate_target = resolve_registry_object(query['candidate_registry_key'], query['candidate_id'])
        durable_finding_links = None
        if query['baseline_id'] or query['candidate_id']:
            if baseline_target is None:
                error = 'Select a valid baseline object.'
            elif candidate_target is None:
                error = 'Select a valid candidate object.'
            else:
                compare_result = compare_lane_allocations(
                    baseline_target=baseline_target,
                    candidate_target=candidate_target,
                )
                baseline_registry_key = get_registry_key_for_object(baseline_target)
                candidate_registry_key = get_registry_key_for_object(candidate_target)
                baseline_fabric = _fabric_for_object(baseline_target)
                candidate_fabric = _fabric_for_object(candidate_target)
                durable_finding_links = {
                    'baseline': _durable_audit_findings_url(
                        fabric=baseline_fabric,
                        target=baseline_target if baseline_registry_key not in {'interface', 'frontport', 'rearport'} else None,
                        active=True,
                    ) if baseline_fabric is not None else None,
                    'candidate': _durable_audit_findings_url(
                        fabric=candidate_fabric,
                        target=candidate_target if candidate_registry_key not in {'interface', 'frontport', 'rearport'} else None,
                        active=True,
                    ) if candidate_fabric is not None else None,
                }

        return render(request, 'netbox_plant_graph/lane_compare.html', {
            'page_title': 'Lane Compare',
            'registry_choices': _lane_workspace_registry_choices(),
            'query': query,
            'compare_result': compare_result,
            'durable_finding_links': durable_finding_links,
            'error': error,
        })


class BlastRadiusView(generic.ObjectView):
    queryset = Fabric.objects.none()

    def get(self, request):
        query = {
            'target_registry_key': request.GET.get('target_registry_key', 'attachmentunit'),
            'target_id': request.GET.get('target_id', ''),
            'resolution': request.GET.get('resolution', DEFAULT_RESOLUTION),
        }
        result = None
        error = None
        target = resolve_registry_object(query['target_registry_key'], query['target_id'])
        if query['target_id']:
            if target is None:
                error = 'Select a valid target object.'
            else:
                result = compute_blast_radius(target=target, resolution=query['resolution'])

        return render(request, 'netbox_plant_graph/blast_radius.html', {
            'page_title': 'Physical Cable Blast Radius',
            'registry_choices': _operational_registry_choices(),
            'resolution_choices': _resolution_choices(),
            'query': query,
            'result': result,
            'error': error,
        })


class AuditFindingActionView(View):
    action_name = ''

    def post(self, request, pk):
        finding = get_object_or_404(AuditFinding, pk=pk)
        actor = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        note = request.POST.get('note', '').strip()
        if self.action_name == 'acknowledge':
            acknowledge_audit_finding(finding=finding, actor=actor, note=note)
            messages.success(request, 'Audit finding acknowledged.')
        elif self.action_name == 'start_remediation':
            start_audit_finding_remediation(finding=finding, actor=actor, note=note)
            messages.success(request, 'Audit finding marked in progress.')
        elif self.action_name == 'resolve':
            resolve_audit_finding(finding=finding, actor=actor, note=note)
            messages.success(request, 'Audit finding resolved.')
        elif self.action_name == 'reopen':
            reopen_audit_finding(finding=finding, actor=actor, note=note)
            messages.success(request, 'Audit finding reopened.')
        elif self.action_name == 'suppress':
            suppress_audit_finding(finding=finding, actor=actor, reason=note)
            messages.success(request, 'Audit finding suppressed.')
        elif self.action_name == 'unsuppress':
            unsuppress_audit_finding(finding=finding, actor=actor, reason=note)
            messages.success(request, 'Audit finding unsuppressed.')
        else:
            messages.error(request, 'Unsupported audit finding action.')
        return redirect(request.POST.get('next') or finding.get_absolute_url())


class AuditFindingAcknowledgeView(AuditFindingActionView):
    action_name = 'acknowledge'


class AuditFindingStartRemediationView(AuditFindingActionView):
    action_name = 'start_remediation'


class AuditFindingResolveView(AuditFindingActionView):
    action_name = 'resolve'


class AuditFindingReopenView(AuditFindingActionView):
    action_name = 'reopen'


class AuditFindingSuppressView(AuditFindingActionView):
    action_name = 'suppress'


class AuditFindingUnsuppressView(AuditFindingActionView):
    action_name = 'unsuppress'


class DisjointnessExceptionActionView(View):
    action_name = ''

    def post(self, request, pk):
        exception = get_object_or_404(DisjointnessException, pk=pk)
        actor = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
        if self.action_name == 'approve':
            approve_disjointness_exception(exception=exception, actor=actor)
            messages.success(request, 'Disjointness exception approved.')
        elif self.action_name == 'expire':
            expire_disjointness_exception(exception=exception, actor=actor)
            messages.success(request, 'Disjointness exception expired.')
        elif self.action_name == 'reactivate':
            reactivate_disjointness_exception(exception=exception, actor=actor)
            messages.success(request, 'Disjointness exception reactivated.')
        else:
            messages.error(request, 'Unsupported disjointness exception action.')
        return redirect(request.POST.get('next') or exception.get_absolute_url())


class DisjointnessExceptionApproveView(DisjointnessExceptionActionView):
    action_name = 'approve'


class DisjointnessExceptionExpireView(DisjointnessExceptionActionView):
    action_name = 'expire'


class DisjointnessExceptionReactivateView(DisjointnessExceptionActionView):
    action_name = 'reactivate'


# ---------------------------------------------------------------------------
# Planning views — Phase 6
# ---------------------------------------------------------------------------


def _build_matrix(a_connectors, b_connectors, mappings):
    """Build a matrix representation: rows = A positions, columns = B positions."""
    if not a_connectors or not b_connectors:
        return [], []

    # Build headers: "B1:1", "B1:2", ...
    b_headers = []
    b_col_index = {}
    col = 0
    for b_conn in b_connectors:
        for pos in range(1, b_conn.position_count + 1):
            b_headers.append(f'{b_conn}:{pos}')
            b_col_index[(b_conn.pk, pos)] = col
            col += 1

    # Build a lookup of mapped cells from mapping rows
    mapped_cells = set()
    for m in mappings:
        key = (m.b_connector_id, m.b_position)
        b_col = b_col_index.get(key)
        a_row_label = f'{m.a_connector}:{m.a_position}'
        if b_col is not None:
            mapped_cells.add((a_row_label, b_col))

    # Build rows
    rows = []
    for a_conn in a_connectors:
        for a_pos in range(1, a_conn.position_count + 1):
            row_label = f'{a_conn}:{a_pos}'
            cells = [
                {'mapped': (row_label, i) in mapped_cells}
                for i in range(len(b_headers))
            ]
            rows.append({'label': row_label, 'cells': cells})

    return b_headers, rows


class AssemblyTemplateDetailView(MetadataDrivenDetailView):
    """Custom detail view for AssemblyTemplate with connector/mapping tabs + matrix."""
    template_name = 'netbox_plant_graph/assembly_template_detail.html'

    def get_detail_spec(self):
        from .object_registry import get_object_spec
        spec = get_object_spec('assemblytemplate')
        return build_generated_detail_spec(spec)

    def get_extra_context(self, request, instance):
        context = super().get_extra_context(request, instance)
        a_connectors = list(instance.connectors.filter(side='A').order_by('connector_number'))
        b_connectors = list(instance.connectors.filter(side='B').order_by('connector_number'))
        mappings = list(
            instance.mappings.select_related('a_connector', 'b_connector').order_by('a_connector', 'a_position')
        )
        b_headers, matrix_rows = _build_matrix(a_connectors, b_connectors, mappings)
        context.update({
            'a_connectors': a_connectors,
            'b_connectors': b_connectors,
            'mappings': mappings,
            'matrix_b_headers': b_headers,
            'matrix_rows': matrix_rows,
        })
        return context


# Override the registry-generated detail view for assemblytemplate.
AssemblyTemplateDetailView.queryset = AssemblyTemplate.objects.all()
from .object_registry import get_object_spec as _get_spec
_at_spec = _get_spec('assemblytemplate')
globals()[_at_spec.view.detail_class_name] = AssemblyTemplateDetailView


class AssemblyTemplateBuilderView(View):
    """Builder page for managing connector and mapping configurations on an AssemblyTemplate."""

    def get(self, request, pk):
        assembly_template = get_object_or_404(AssemblyTemplate, pk=pk)
        a_connectors = list(assembly_template.connectors.filter(side='A').order_by('connector_number'))
        b_connectors = list(assembly_template.connectors.filter(side='B').order_by('connector_number'))
        mappings = list(assembly_template.mappings.select_related('a_connector', 'b_connector').all())
        mapped_pairs = {(m.a_connector_id, m.b_connector_id) for m in mappings}
        matrix_rows = [
            {
                'a': a,
                'cells': [
                    {'b': b, 'mapped': (a.pk, b.pk) in mapped_pairs}
                    for b in b_connectors
                ],
            }
            for a in a_connectors
        ]
        return render(request, 'netbox_plant_graph/assembly_template_build.html', {
            'assembly_template': assembly_template,
            'a_connectors': a_connectors,
            'b_connectors': b_connectors,
            'matrix_rows': matrix_rows,
            'page_title': f'Build: {assembly_template.name}',
        })

    def post(self, request, pk):
        assembly_template = get_object_or_404(AssemblyTemplate, pk=pk)
        action = request.POST.get('action')

        if action == 'add_connector':
            side = request.POST.get('side', 'A')
            connector_number_raw = request.POST.get('connector_number', '')
            connector_type = request.POST.get('connector_type', 'mpo-12')
            position_count_raw = request.POST.get('position_count', '8')
            label = request.POST.get('label', '')
            try:
                connector_number = int(connector_number_raw)
                position_count = max(1, int(position_count_raw))
            except (TypeError, ValueError):
                messages.error(request, 'Invalid connector number or position count.')
                return redirect(reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': pk}))
            AssemblyConnectorTemplate.objects.create(
                template=assembly_template,
                side=side,
                connector_number=connector_number,
                connector_type=connector_type,
                position_count=position_count,
                label=label,
            )

        elif action == 'remove_connector':
            connector_pk = request.POST.get('connector_pk')
            AssemblyConnectorTemplate.objects.filter(pk=connector_pk, template=assembly_template).delete()

        elif action == 'toggle_mapping':
            a_connector_pk = request.POST.get('a_connector_pk')
            b_connector_pk = request.POST.get('b_connector_pk')
            existing = AssemblyMappingTemplate.objects.filter(
                template=assembly_template,
                a_connector_id=a_connector_pk,
                b_connector_id=b_connector_pk,
            )
            if existing.exists():
                existing.delete()
            else:
                AssemblyMappingTemplate.objects.create(
                    template=assembly_template,
                    a_connector_id=a_connector_pk,
                    b_connector_id=b_connector_pk,
                    a_position=1,
                    b_position=1,
                )

        elif action == 'apply_preset':
            preset_name = request.POST.get('preset_name', 'straight')
            a_connectors = list(assembly_template.connectors.filter(side='A').order_by('connector_number'))
            b_connectors = list(assembly_template.connectors.filter(side='B').order_by('connector_number'))
            assembly_template.mappings.all().delete()
            pairs = []
            n_a = len(a_connectors)
            n_b = len(b_connectors)
            if preset_name == 'straight':
                pairs = [(a_connectors[i], b_connectors[i]) for i in range(min(n_a, n_b))]
            elif preset_name == 'reversed':
                n = min(n_a, n_b)
                pairs = [(a_connectors[i], b_connectors[n_b - 1 - i]) for i in range(n)]
            elif preset_name == 'cross':
                for i in range(n_a):
                    b_idx = n_b - 1 - (i * 2)
                    if 0 <= b_idx < n_b:
                        pairs.append((a_connectors[i], b_connectors[b_idx]))
            for a_conn, b_conn in pairs:
                AssemblyMappingTemplate.objects.create(
                    template=assembly_template,
                    a_connector=a_conn,
                    b_connector=b_conn,
                    a_position=1,
                    b_position=1,
                )

        return redirect(reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': pk}))


class DeploymentPlanDetailView(MetadataDrivenDetailView):
    """Custom detail view for DeploymentPlan with stamp records and action buttons."""
    template_name = 'netbox_plant_graph/deployment_plan_detail.html'
    queryset = DeploymentPlan.objects.all()

    def get_detail_spec(self):
        spec = _get_spec('deploymentplan')
        return build_generated_detail_spec(spec)

    def get_extra_context(self, request, instance):
        context = super().get_extra_context(request, instance)
        stamp_records = list(
            instance.stamp_records.select_related('template_type', 'result_type', 'stamped_by').order_by('pk')
        )
        # Resolve GFK objects for display
        from django.contrib.contenttypes.models import ContentType
        for record in stamp_records:
            if record.template_type_id and record.template_id:
                try:
                    model = record.template_type.model_class()
                    record._template_obj = model.objects.filter(pk=record.template_id).first() if model else None
                except Exception:
                    record._template_obj = None
            else:
                record._template_obj = None
            if record.result_type_id and record.result_id:
                try:
                    model = record.result_type.model_class()
                    record._result_obj = model.objects.filter(pk=record.result_id).first() if model else None
                except Exception:
                    record._result_obj = None
            else:
                record._result_obj = None

        pending_count = sum(1 for r in stamp_records if r.status == 'pending')
        stamped_count = sum(1 for r in stamp_records if r.status in ('stamped', 'validated'))
        failed_count = sum(1 for r in stamp_records if r.status == 'failed')
        rolled_back_count = sum(1 for r in stamp_records if r.status == 'rolled_back')

        can_execute = instance.status == 'approved' and pending_count > 0
        can_rollback = stamped_count > 0 and instance.status not in ('rolled_back',)

        context.update({
            'stamp_records': stamp_records,
            'stamp_stats': (
                {'label': 'Pending', 'count': pending_count},
                {'label': 'Stamped', 'count': stamped_count},
                {'label': 'Failed', 'count': failed_count},
                {'label': 'Rolled Back', 'count': rolled_back_count},
            ),
            'can_execute': can_execute,
            'can_rollback': can_rollback,
        })
        return context


_dp_spec = _get_spec('deploymentplan')
globals()[_dp_spec.view.detail_class_name] = DeploymentPlanDetailView


class DeploymentPlanExecuteView(View):
    """POST-only view that executes a DeploymentPlan, then redirects back."""

    def post(self, request, pk):
        from .services.plan_execution import execute_plan
        plan = get_object_or_404(DeploymentPlan, pk=pk)
        try:
            execute_plan(plan, user=request.user)
            messages.success(request, f'Plan "{plan.name}" executed successfully.')
        except ValueError as exc:
            messages.error(request, f'Execute failed: {exc}')
        except Exception as exc:
            messages.error(request, f'Execute error: {exc}')
        return redirect(request.POST.get('next') or plan.get_absolute_url())


class DeploymentPlanRollbackView(View):
    """POST-only view that rolls back a DeploymentPlan, then redirects back."""

    def post(self, request, pk):
        from .services.plan_execution import rollback_plan
        plan = get_object_or_404(DeploymentPlan, pk=pk)
        try:
            rollback_plan(plan, user=request.user)
            messages.success(request, f'Plan "{plan.name}" rolled back.')
        except ValueError as exc:
            messages.error(request, f'Rollback failed: {exc}')
        except Exception as exc:
            messages.error(request, f'Rollback error: {exc}')
        return redirect(request.POST.get('next') or plan.get_absolute_url())


class DeploymentPlanWorkflowView(View):
    """Step-by-step guided workflow for executing a deployment plan."""

    def get(self, request, pk):
        plan = get_object_or_404(DeploymentPlan, pk=pk)
        stamps = list(plan.stamp_records.order_by('pk'))
        total_steps = len(stamps)

        try:
            current_step = max(0, int(request.GET.get('step', 0)))
        except (ValueError, TypeError):
            current_step = 0

        if current_step >= total_steps:
            current_step = max(0, total_steps - 1)

        current_stamp = stamps[current_step] if stamps else None
        previous_stamp = stamps[current_step - 1] if current_step > 0 else None
        next_stamp = stamps[current_step + 1] if current_step + 1 < total_steps else None

        completed_steps = sum(1 for s in stamps if s.status in ('stamped', 'validated'))

        return render(request, 'netbox_plant_graph/deployment_plan_workflow.html', {
            'deployment_plan': plan,
            'stamps': stamps,
            'current_stamp': current_stamp,
            'current_step': current_step,
            'total_steps': total_steps,
            'completed_steps': completed_steps,
            'previous_stamp': previous_stamp,
            'next_stamp': next_stamp,
            'page_title': f'Workflow: {plan.name}',
        })

    def post(self, request, pk):
        plan = get_object_or_404(DeploymentPlan, pk=pk)
        action = request.POST.get('action', '')
        stamps = list(plan.stamp_records.order_by('pk'))

        try:
            current_step = max(0, int(request.POST.get('current_step', 0)))
        except (ValueError, TypeError):
            current_step = 0

        workflow_url = reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': plan.pk})

        if action in ('execute_stamp', 'preview_stamp'):
            from .services.plan_execution import execute_plan
            try:
                execute_plan(plan, user=request.user)
                messages.success(request, f'Plan "{plan.name}" execution triggered.')
                next_step = min(current_step + 1, len(stamps) - 1)
                return redirect(f'{workflow_url}?step={next_step}')
            except ValueError as exc:
                messages.error(request, f'Execute failed: {exc}')
            except Exception as exc:
                messages.error(request, f'Execute error: {exc}')
            return redirect(f'{workflow_url}?step={current_step}')

        elif action == 'skip_stamp':
            next_step = min(current_step + 1, max(0, len(stamps) - 1))
            return redirect(f'{workflow_url}?step={next_step}')

        elif action == 'execute_plan':
            from .services.plan_execution import execute_plan
            try:
                execute_plan(plan, user=request.user)
                messages.success(request, f'Plan "{plan.name}" executed successfully.')
            except ValueError as exc:
                messages.error(request, f'Execute failed: {exc}')
            except Exception as exc:
                messages.error(request, f'Execute error: {exc}')
            return redirect(f'{workflow_url}?step={current_step}')

        return redirect(f'{workflow_url}?step={current_step}')


class AssemblyStampWizardView(View):
    """GET/POST wizard for stamping a passive device from an AssemblyTemplate."""

    def get(self, request, pk):
        from .forms import AssemblyStampForm
        template = get_object_or_404(AssemblyTemplate, pk=pk)
        form = AssemblyStampForm()
        return render(request, 'netbox_plant_graph/assembly_stamp_wizard.html', {
            'template': template,
            'form': form,
            'result': None,
        })

    def post(self, request, pk):
        from .forms import AssemblyStampForm
        from .services import stamp_passive_device
        template = get_object_or_404(AssemblyTemplate, pk=pk)
        form = AssemblyStampForm(request.POST)
        result = None
        if form.is_valid():
            data = form.cleaned_data
            plan = data.get('plan')
            action = data.get('action', 'execute_now')
            if action == 'add_to_plan' and plan:
                from django.contrib.contenttypes.models import ContentType
                from .models import StampRecord
                template_ct = ContentType.objects.get_for_model(template)
                StampRecord.objects.create(
                    plan=plan,
                    template_type=template_ct,
                    template_id=template.pk,
                    status='pending',
                    parameters={
                        'stamp_type': 'assembly_passive',
                        'template_id': template.pk,
                        'name': data['name'],
                        'site_pk': data['site'].pk if data.get('site') else None,
                        'location_pk': data['location'].pk if data.get('location') else None,
                        'rack_pk': data['rack'].pk if data.get('rack') else None,
                        'device_role_pk': data['device_role'].pk if data.get('device_role') else None,
                        'position': str(data['position']) if data.get('position') is not None else None,
                        'face': data.get('face') or 'front',
                    },
                )
                messages.success(request, f'Stamp added to plan "{plan}".')
            else:
                try:
                    result = stamp_passive_device(
                        template=template,
                        site=data['site'],
                        location=data.get('location'),
                        rack=data.get('rack'),
                        position=data.get('position'),
                        face=data.get('face') or 'front',
                        device_role=data['device_role'],
                        name=data['name'],
                        plan=plan,
                        user=request.user,
                    )
                    messages.success(request, f'Device "{result.device}" stamped successfully.')
                except Exception as exc:
                    messages.error(request, f'Stamp failed: {exc}')
        return render(request, 'netbox_plant_graph/assembly_stamp_wizard.html', {
            'template': template,
            'form': form,
            'result': result,
        })


class SpatialStampWizardView(View):
    """GET/POST wizard for stamping a SpatialTemplate."""

    def _build_node_tree(self, nodes, parent_id=None):
        """Recursively build a tree structure for template preview."""
        result = []
        for node in nodes:
            node_parent_id = node.parent_id
            if node_parent_id == parent_id:
                children = self._build_node_tree(nodes, parent_id=node.pk)
                result.append(SimpleNamespace(
                    name_pattern=node.name_pattern,
                    node_type=node.node_type,
                    quantity=node.quantity,
                    children=children,
                ))
        return result

    def get(self, request, pk):
        from .forms import SpatialStampForm
        template = get_object_or_404(SpatialTemplate, pk=pk)
        form = SpatialStampForm(template=template)
        all_nodes = list(template.nodes.order_by('sort_order', 'pk'))
        node_tree = self._build_node_tree(all_nodes, parent_id=None)
        return render(request, 'netbox_plant_graph/spatial_stamp_wizard.html', {
            'template': template,
            'form': form,
            'result': None,
            'floorplan_urls': None,
            'node_tree': node_tree,
        })

    def post(self, request, pk):
        from .forms import SpatialStampForm
        from .services import stamp_spatial_template
        template = get_object_or_404(SpatialTemplate, pk=pk)
        form = SpatialStampForm(request.POST, template=template)
        result = None
        floorplan_urls = None
        all_nodes = list(template.nodes.order_by('sort_order', 'pk'))
        node_tree = self._build_node_tree(all_nodes, parent_id=None)
        if form.is_valid():
            data = form.cleaned_data
            plan = data.get('plan')
            action = data.get('action', 'execute_now')
            variables = forms_module.collect_template_variables(data)
            # The scope is the parent_location if provided, otherwise the site.
            scope = data.get('parent_location') or data['site']
            if action == 'add_to_plan' and plan:
                from django.contrib.contenttypes.models import ContentType
                from .models import StampRecord
                template_ct = ContentType.objects.get_for_model(template)
                scope_ct = ContentType.objects.get_for_model(scope)
                StampRecord.objects.create(
                    plan=plan,
                    template_type=template_ct,
                    template_id=template.pk,
                    status='pending',
                    parameters={
                        'stamp_type': 'spatial',
                        'template_id': template.pk,
                        'scope_type': scope_ct.model,
                        'scope_id': scope.pk,
                        'variables': variables,
                    },
                )
                messages.success(request, f'Stamp added to plan "{plan}".')
            else:
                try:
                    result = stamp_spatial_template(
                        template,
                        scope,
                        variables=variables,
                        plan=plan,
                        user=request.user,
                    )
                    messages.success(
                        request,
                        f'Stamped {len(result.locations)} location(s) and {len(result.racks)} rack(s) successfully.',
                    )
                    try:
                        from .services.floorplan_bridge import get_floorplan_urls_for_scope

                        floorplan_urls = get_floorplan_urls_for_scope(scope)
                    except Exception:
                        floorplan_urls = None
                except Exception as exc:
                    messages.error(request, f'Stamp failed: {exc}')
        return render(request, 'netbox_plant_graph/spatial_stamp_wizard.html', {
            'template': template,
            'form': form,
            'result': result,
            'floorplan_urls': floorplan_urls,
            'node_tree': node_tree,
        })


class RackPopulationStampWizardView(View):
    """GET/POST wizard for stamping a RackPopulationTemplate into a rack."""

    def get(self, request, pk):
        from .forms import RackPopulationStampForm
        template = get_object_or_404(RackPopulationTemplate, pk=pk)
        slots = list(template.slots.select_related('device_type', 'device_role').order_by('u_position', 'face'))
        form = RackPopulationStampForm(template=template)
        return render(request, 'netbox_plant_graph/rack_population_stamp_wizard.html', {
            'template': template,
            'slots': slots,
            'form': form,
            'result': None,
        })

    def post(self, request, pk):
        from .forms import RackPopulationStampForm
        from .services.rack_population_stamp import stamp_rack_population
        template = get_object_or_404(RackPopulationTemplate, pk=pk)
        slots = list(template.slots.select_related('device_type', 'device_role').order_by('u_position', 'face'))
        form = RackPopulationStampForm(request.POST, template=template)
        result = None
        if form.is_valid():
            data = form.cleaned_data
            plan = data.get('plan')
            action = data.get('action', 'execute_now')
            variables = forms_module.collect_template_variables(data)
            if action == 'add_to_plan' and plan:
                from django.contrib.contenttypes.models import ContentType
                from .models import StampRecord
                template_ct = ContentType.objects.get_for_model(template)
                StampRecord.objects.create(
                    plan=plan,
                    template_type=template_ct,
                    template_id=template.pk,
                    status='pending',
                    parameters={
                        'stamp_type': 'rack_population',
                        'template_id': template.pk,
                        'rack_pk': data['rack'].pk,
                        'variables': variables,
                    },
                )
                messages.success(request, f'Stamp added to plan "{plan}".')
            else:
                try:
                    result = stamp_rack_population(
                        template,
                        data['rack'],
                        variables=variables,
                        plan=plan,
                        user=request.user,
                    )
                    messages.success(request, f'Populated rack with {len(result.devices)} device(s).')
                except Exception as exc:
                    messages.error(request, f'Stamp failed: {exc}')
        return render(request, 'netbox_plant_graph/rack_population_stamp_wizard.html', {
            'template': template,
            'slots': slots,
            'form': form,
            'result': result,
        })


class BreakoutStampWizardView(View):
    """GET/POST wizard for applying a DeviceBreakoutTemplate to an existing device."""

    def get(self, request, pk):
        from .forms import BreakoutStampForm
        from .models import DeviceBreakoutTemplate
        template = get_object_or_404(DeviceBreakoutTemplate, pk=pk)
        specs = list(template.child_specs.order_by('sort_order', 'parent_interface_name'))
        form = BreakoutStampForm()
        return render(request, 'netbox_plant_graph/breakout_stamp_wizard.html', {
            'template': template,
            'specs': specs,
            'form': form,
            'result': None,
        })

    def post(self, request, pk):
        from .forms import BreakoutStampForm
        from .models import DeviceBreakoutTemplate
        from .services.assembly_stamp import create_child_interfaces_from_breakout_spec
        template = get_object_or_404(DeviceBreakoutTemplate, pk=pk)
        specs = list(template.child_specs.order_by('sort_order', 'parent_interface_name'))
        form = BreakoutStampForm(request.POST)
        result = None
        if form.is_valid():
            data = form.cleaned_data
            plan = data.get('plan')
            action = data.get('action', 'execute_now')
            if action == 'add_to_plan' and plan:
                from .models import StampRecord
                template_ct = ContentType.objects.get_for_model(template)
                StampRecord.objects.create(
                    plan=plan,
                    template_type=template_ct,
                    template_id=template.pk,
                    status='pending',
                    parameters={
                        'stamp_type': 'breakout',
                        'template_id': template.pk,
                        'device_pk': data['device'].pk,
                    },
                )
                messages.success(request, f'Stamp added to plan "{plan}".')
            else:
                try:
                    result = create_child_interfaces_from_breakout_spec(
                        device=data['device'],
                        breakout_template=template,
                    )
                    messages.success(
                        request,
                        f'Created {len(result.created)} child interface(s); '
                        f'{len(result.skipped)} skipped (already exist).',
                    )
                except Exception as exc:
                    messages.error(request, f'Stamp failed: {exc}')
        return render(request, 'netbox_plant_graph/breakout_stamp_wizard.html', {
            'template': template,
            'specs': specs,
            'form': form,
            'result': result,
        })


class CoordinateLayoutView(generic.ObjectView):
    """Deprecated entrypoint that now hands off site/location layout to floorplan."""
    queryset = SpatialPlacement.objects.none()

    def get(self, request):
        frame_type = request.GET.get('frame_type', '')
        frame_id = _parse_int(request.GET.get('frame_id', ''))
        scope = None
        floorplan_list_url = None
        scope_label = None

        try:
            floorplan_list_url = reverse('plugins:netbox_floorplan:floorplan_list')
        except Exception:
            floorplan_list_url = None

        if frame_type in ('site', 'location') and frame_id:
            model_class = Site if frame_type == 'site' else Location
            scope = model_class.objects.filter(pk=frame_id).first()
            if scope is not None:
                try:
                    from .services.floorplan_bridge import get_floorplan_urls_for_scope

                    floorplan_urls = get_floorplan_urls_for_scope(scope)
                    return redirect(floorplan_urls.edit_url or floorplan_urls.add_url)
                except Exception as exc:
                    messages.warning(request, f'Floorplan handoff unavailable: {exc}')
                scope_label = str(scope)
            else:
                scope_label = f'{frame_type} #{frame_id}'

        return render(request, 'netbox_plant_graph/coordinate_layout.html', {
            'page_title': 'Coordinate Layout (Deprecated)',
            'selected_frame_type': frame_type,
            'selected_frame_id': frame_id or '',
            'scope_label': scope_label,
            'floorplan_list_url': floorplan_list_url,
            'spatial_placement_list_url': reverse('plugins:netbox_plant_graph:spatial-placement_list'),
        })


class TemplateLibraryView(generic.ObjectView):
    """Unified browse page across all planning template types."""
    queryset = AssemblyTemplate.objects.none()

    def get(self, request):
        from .models import RackPopulationTemplate, SpatialTemplate
        sections = [
            {
                'label': 'Assembly Templates',
                'count': AssemblyTemplate.objects.count(),
                'recent': list(AssemblyTemplate.objects.order_by('-created', '-pk')[:5]),
                'list_url': reverse('plugins:netbox_plant_graph:assembly-template_list'),
                'add_url': reverse('plugins:netbox_plant_graph:assembly-template_add'),
            },
            {
                'label': 'Spatial Templates',
                'count': SpatialTemplate.objects.count(),
                'recent': list(SpatialTemplate.objects.order_by('-created', '-pk')[:5]),
                'list_url': reverse('plugins:netbox_plant_graph:spatial-template_list'),
                'add_url': reverse('plugins:netbox_plant_graph:spatial-template_add'),
            },
            {
                'label': 'Rack Population Templates',
                'count': RackPopulationTemplate.objects.count(),
                'recent': list(RackPopulationTemplate.objects.order_by('-created', '-pk')[:5]),
                'list_url': reverse('plugins:netbox_plant_graph:rack-population-template_list'),
                'add_url': reverse('plugins:netbox_plant_graph:rack-population-template_add'),
            },
            {
                'label': 'Deployment Plans',
                'count': DeploymentPlan.objects.count(),
                'recent': list(DeploymentPlan.objects.order_by('-created', '-pk')[:5]),
                'list_url': reverse('plugins:netbox_plant_graph:deployment-plan_list'),
                'add_url': reverse('plugins:netbox_plant_graph:deployment-plan_add'),
            },
        ]
        return render(request, 'netbox_plant_graph/template_library.html', {
            'page_title': 'Template Library',
            'sections': sections,
        })


class FabricOnboardView(View):
    """GET/POST wizard for onboarding a new Fabric with initial FabricPlanes."""

    def get(self, request):
        from .forms import FabricOnboardForm
        form = FabricOnboardForm()
        return render(request, 'netbox_plant_graph/fabric_onboard.html', {'form': form})

    def post(self, request):
        from .forms import FabricOnboardForm
        from .services.sync import rebuild_graph
        from django.db import transaction

        form = FabricOnboardForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                with transaction.atomic():
                    fabric = Fabric.objects.create(
                        name=data['name'],
                        description=data.get('description') or '',
                        expected_plane_count=data['expected_plane_count'],
                        tier_depth=data['tier_depth'],
                        disjointness_policy=data['disjointness_policy'],
                        tenant=data.get('tenant'),
                        scope_site=data.get('site'),
                        scope_location=data.get('location'),
                    )
                    for i in range(1, data['expected_plane_count'] + 1):
                        FabricPlane.objects.create(fabric=fabric, plane_number=i)
                if data.get('trigger_initial_rebuild'):
                    try:
                        rebuild_graph(scope={'fabric': fabric})
                    except Exception as exc:
                        messages.warning(request, f'Fabric created but rebuild failed: {exc}')
                messages.success(request, f'Fabric "{fabric.name}" created with {data["expected_plane_count"]} planes.')
                return redirect(fabric.get_absolute_url())
            except Exception as exc:
                messages.error(request, f'Onboard failed: {exc}')
        return render(request, 'netbox_plant_graph/fabric_onboard.html', {'form': form})


class FabricPlaneAssignmentView(View):
    """Matrix-style page to bulk-assign/remove AttachmentUnits from FabricPlanes."""

    def _get_fabric(self, pk):
        return get_object_or_404(Fabric, pk=pk)

    def _get_context(self, fabric):
        planes = list(fabric.planes.order_by('plane_number'))
        au_ct = ContentType.objects.get_for_model(AttachmentUnit)
        attachment_units = list(
            AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric
            ).select_related('termination_point__plant_node').order_by(
                'termination_point__plant_node__name', 'termination_point__name', 'ordinal', 'name'
            )
        )
        if attachment_units:
            au_ids = [au.pk for au in attachment_units]
            memberships = PlaneMembership.objects.filter(
                plane__fabric=fabric,
                member_type=au_ct,
                member_id__in=au_ids,
            ).values_list('plane_id', 'member_id')
            assigned_set = set(memberships)
        else:
            assigned_set = set()

        plane_id_set = {p.pk for p in planes}
        matrix_rows = []
        for au in attachment_units:
            cells = [
                {'plane': plane, 'assigned': (plane.pk, au.pk) in assigned_set}
                for plane in planes
            ]
            matrix_rows.append({'unit': au, 'cells': cells})

        return {
            'fabric': fabric,
            'planes': planes,
            'attachment_units': attachment_units,
            'matrix_rows': matrix_rows,
            'page_title': f'Plane Assignment — {fabric.name}',
        }

    def get(self, request, pk):
        fabric = self._get_fabric(pk)
        return render(request, 'netbox_plant_graph/fabric_plane_assignment.html', self._get_context(fabric))

    def post(self, request, pk):
        fabric = self._get_fabric(pk)
        action = request.POST.get('action')
        au_ct = ContentType.objects.get_for_model(AttachmentUnit)

        if action == 'toggle':
            unit_pk = _parse_int(request.POST.get('unit_pk'))
            plane_pk = _parse_int(request.POST.get('plane_pk'))
            if unit_pk and plane_pk:
                plane = get_object_or_404(FabricPlane, pk=plane_pk, fabric=fabric)
                existing = PlaneMembership.objects.filter(
                    plane=plane, member_type=au_ct, member_id=unit_pk
                ).first()
                if existing:
                    existing.delete()
                else:
                    PlaneMembership.objects.create(
                        plane=plane,
                        member_type=au_ct,
                        member_id=unit_pk,
                        membership_role='native',
                    )

        elif action == 'assign_all':
            unit_pk = _parse_int(request.POST.get('unit_pk'))
            if unit_pk:
                for plane in fabric.planes.all():
                    PlaneMembership.objects.get_or_create(
                        plane=plane,
                        member_type=au_ct,
                        member_id=unit_pk,
                        defaults={'membership_role': 'native'},
                    )

        elif action == 'remove_all':
            unit_pk = _parse_int(request.POST.get('unit_pk'))
            if unit_pk:
                PlaneMembership.objects.filter(
                    plane__fabric=fabric,
                    member_type=au_ct,
                    member_id=unit_pk,
                ).delete()

        return redirect(reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': pk}))


class FabricOperationsView(generic.ObjectView):
    """Fabric-scoped operations page: view recent builds/audits and trigger actions."""
    queryset = Fabric.objects.all()
    template_name = 'netbox_plant_graph/fabric_operations.html'

    def get_extra_context(self, request, instance):
        from .models import AuditRun, GraphBuildRun
        recent_builds = list(GraphBuildRun.objects.filter(fabric=instance).order_by('-started_at', '-pk')[:10])
        recent_audits = list(AuditRun.objects.filter(fabric=instance).order_by('-started_at', '-pk')[:10])
        return {
            'recent_builds': recent_builds,
            'recent_audits': recent_audits,
        }

    def post(self, request, pk):
        from .services.sync import rebuild_graph
        fabric = get_object_or_404(Fabric, pk=pk)
        action = request.POST.get('action')
        if action == 'rebuild':
            try:
                rebuild_graph(scope={'fabric': fabric})
                messages.success(request, f'Graph rebuild triggered for "{fabric.name}".')
            except Exception as exc:
                messages.error(request, f'Rebuild failed: {exc}')
        elif action == 'audit':
            try:
                run_plane_audit(fabric=fabric)
                messages.success(request, f'Audit triggered for "{fabric.name}".')
            except Exception as exc:
                messages.error(request, f'Audit failed: {exc}')
        else:
            messages.warning(request, f'Unknown action: {action!r}')
        return redirect(reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': pk}))


class AuditTriageView(View):
    """One-at-a-time audit finding triage queue."""

    def _build_queryset(self, fabric_id, severity, finding_type):
        qs = AuditFinding.objects.filter(active=True).select_related(
            'fabric', 'assigned_to', 'acknowledged_by',
        ).prefetch_related('suppressions', 'events')
        if fabric_id:
            qs = qs.filter(fabric_id=fabric_id)
        if severity:
            qs = qs.filter(severity=severity)
        if finding_type:
            qs = qs.filter(finding_type=finding_type)
        return qs.order_by('-severity', 'pk')

    def _triage_url(self, request, *, index=None, fabric_id=None, severity=None, finding_type=None):
        params = {}
        fabric_id = fabric_id or request.GET.get('fabric_id') or request.POST.get('fabric_id')
        severity = severity or request.GET.get('severity') or request.POST.get('severity')
        finding_type = finding_type or request.GET.get('finding_type') or request.POST.get('finding_type')
        if fabric_id:
            params['fabric_id'] = fabric_id
        if severity:
            params['severity'] = severity
        if finding_type:
            params['finding_type'] = finding_type
        if index is not None:
            params['index'] = index
        base = reverse('plugins:netbox_plant_graph:audit_triage')
        return f'{base}?{urlencode(params)}' if params else base

    def get(self, request):
        fabric_id = _parse_int(request.GET.get('fabric_id'))
        severity = request.GET.get('severity', '').strip()
        finding_type = request.GET.get('finding_type', '').strip()
        index = max(0, _parse_int(request.GET.get('index'), default=0))

        filter_form = forms_module.AuditTriageFilterForm(initial={
            'fabric_id': fabric_id,
            'severity': severity,
            'finding_type': finding_type,
        })

        findings_qs = self._build_queryset(fabric_id, severity, finding_type)
        total_count = findings_qs.count()

        finding = None
        recent_events = ()
        active_suppression = None

        if total_count > 0:
            clamped_index = min(index, total_count - 1)
            finding = findings_qs[clamped_index]
            recent_events = tuple(
                finding.events.select_related('actor').order_by('-created', '-pk')[:5]
            )
            active_suppression = finding.suppressions.filter(active=True).order_by('-created', '-pk').first()
        else:
            clamped_index = 0

        prev_index = clamped_index - 1 if clamped_index > 0 else None
        next_index = clamped_index + 1 if clamped_index < total_count - 1 else None

        return render(request, 'netbox_plant_graph/audit_triage.html', {
            'page_title': 'Audit Triage',
            'filter_form': filter_form,
            'fabrics': _fabric_choices(),
            'fabric_id': fabric_id,
            'severity': severity,
            'finding_type': finding_type,
            'finding': finding,
            'index': clamped_index,
            'total_count': total_count,
            'recent_events': recent_events,
            'active_suppression': active_suppression,
            'prev_index': prev_index,
            'next_index': next_index,
            'audit_dashboard_url': _audit_dashboard_url(finding.fabric if finding else None),
        })

    def post(self, request):
        action = request.POST.get('action', '').strip()
        finding_pk = _parse_int(request.POST.get('finding_pk'))
        next_index = _parse_int(request.POST.get('next_index'), default=0)

        fabric_id = _parse_int(request.POST.get('fabric_id'))
        severity = request.POST.get('severity', '').strip()
        finding_type = request.POST.get('finding_type', '').strip()

        if not finding_pk:
            messages.error(request, 'No finding specified.')
            return redirect(self._triage_url(request, index=next_index))

        finding = get_object_or_404(AuditFinding, pk=finding_pk)
        actor = request.user if request.user.is_authenticated else None

        try:
            if action == 'acknowledge':
                acknowledge_audit_finding(finding=finding, actor=actor)
                messages.success(request, 'Finding acknowledged.')
            elif action == 'start_remediation':
                start_audit_finding_remediation(finding=finding, actor=actor)
                messages.success(request, 'Finding marked in progress.')
            elif action == 'resolve':
                note = request.POST.get('resolution_summary', '').strip()
                resolve_audit_finding(finding=finding, actor=actor, note=note)
                messages.success(request, 'Finding resolved.')
            elif action == 'reopen':
                reopen_audit_finding(finding=finding, actor=actor)
                messages.success(request, 'Finding reopened.')
            elif action == 'suppress':
                reason = request.POST.get('reason', '').strip()
                expires_at_raw = request.POST.get('expires_at', '').strip()
                days = None
                if expires_at_raw:
                    from django.utils.dateparse import parse_datetime, parse_date
                    parsed = parse_datetime(expires_at_raw) or parse_date(expires_at_raw)
                    if parsed is not None:
                        from datetime import date as _date
                        if isinstance(parsed, _date) and not hasattr(parsed, 'hour'):
                            from django.utils.timezone import make_aware
                            from datetime import datetime as _datetime
                            parsed = make_aware(_datetime.combine(parsed, _datetime.min.time()))
                        now = timezone.now()
                        delta = parsed - now
                        days = max(0, delta.days)
                suppress_audit_finding(finding=finding, actor=actor, reason=reason, days=days)
                messages.success(request, 'Finding suppressed.')
            elif action == 'unsuppress':
                unsuppress_audit_finding(finding=finding, actor=actor)
                messages.success(request, 'Finding unsuppressed.')
            elif action == 'skip':
                pass
            else:
                messages.error(request, f'Unknown action: {action!r}')
        except Exception as exc:
            messages.error(request, f'Action failed: {exc}')

        params = {}
        if fabric_id:
            params['fabric_id'] = fabric_id
        if severity:
            params['severity'] = severity
        if finding_type:
            params['finding_type'] = finding_type
        params['index'] = next_index
        base = reverse('plugins:netbox_plant_graph:audit_triage')
        return redirect(f'{base}?{urlencode(params)}')


class ConnectionTemplateBuilderView(View):
    """Builder page for managing ConnectionTemplate rows under a SpatialTemplate."""

    def get(self, request, pk):
        spatial_template = get_object_or_404(SpatialTemplate, pk=pk)
        nodes = list(spatial_template.nodes.order_by('sort_order', 'pk'))
        assembly_templates = list(AssemblyTemplate.objects.order_by('name', 'pk'))
        connections = list(
            spatial_template.connections.select_related(
                'assembly_template', 'source_node', 'dest_node',
            ).order_by('sort_order', 'pk')
        )
        return render(request, 'netbox_plant_graph/connection_template_builder.html', {
            'spatial_template': spatial_template,
            'nodes': nodes,
            'assembly_templates': assembly_templates,
            'connections': connections,
            'enumeration_mode_choices': ConnectionTemplate._meta.get_field('enumeration_mode').choices,
            'page_title': f'Connections: {spatial_template.name}',
        })

    def post(self, request, pk):
        spatial_template = get_object_or_404(SpatialTemplate, pk=pk)
        action = request.POST.get('action', '')

        if action == 'remove_connection':
            connection_pk = _parse_int(request.POST.get('connection_pk'))
            ConnectionTemplate.objects.filter(pk=connection_pk, spatial_template=spatial_template).delete()
            messages.success(request, 'Connection removed.')
            return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': pk}))

        if action == 'add_connection':
            source_node_pk = _parse_int(request.POST.get('source_node_pk'))
            dest_node_pk = _parse_int(request.POST.get('dest_node_pk'))
            assembly_template_pk = _parse_int(request.POST.get('assembly_template_pk'))
            source_slot_index = _parse_int(request.POST.get('source_slot_index'))
            dest_slot_index = _parse_int(request.POST.get('dest_slot_index'))
            source_connector_number = _parse_int(request.POST.get('source_connector_number'), default=1)
            dest_connector_number = _parse_int(request.POST.get('dest_connector_number'), default=1)
            enumeration_mode = request.POST.get('enumeration_mode', 'one_to_one')
            name = (request.POST.get('name', '') or '').strip()
            description = (request.POST.get('description', '') or '').strip()
            label_pattern = (request.POST.get('label_pattern', '') or '').strip()

            if not name:
                messages.error(request, 'Connection name is required.')
                return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': pk}))

            source_node = SpatialTemplateNode.objects.filter(pk=source_node_pk, template=spatial_template).first()
            dest_node = SpatialTemplateNode.objects.filter(pk=dest_node_pk, template=spatial_template).first()
            assembly_template = AssemblyTemplate.objects.filter(pk=assembly_template_pk).first()

            if source_node is None or dest_node is None or assembly_template is None:
                messages.error(request, 'Source node, destination node, and assembly template are required.')
                return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': pk}))

            if source_slot_index is None or source_slot_index < 1 or dest_slot_index is None or dest_slot_index < 1:
                messages.error(request, 'Source and destination slot indices must be positive integers.')
                return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': pk}))

            if source_connector_number is None or source_connector_number < 1:
                source_connector_number = 1
            if dest_connector_number is None or dest_connector_number < 1:
                dest_connector_number = 1

            try:
                ConnectionTemplate.objects.create(
                    spatial_template=spatial_template,
                    name=name,
                    description=description,
                    assembly_template=assembly_template,
                    source_node=source_node,
                    source_slot_index=source_slot_index,
                    source_connector_number=source_connector_number,
                    dest_node=dest_node,
                    dest_slot_index=dest_slot_index,
                    dest_connector_number=dest_connector_number,
                    enumeration_mode=enumeration_mode,
                    label_pattern=label_pattern,
                    sort_order=spatial_template.connections.count() + 1,
                )
                messages.success(request, 'Connection added.')
            except Exception as exc:
                messages.error(request, f'Could not add connection: {exc}')

            return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': pk}))

        messages.error(request, f'Unknown action: {action!r}')
        return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': pk}))


class SpatialTemplateComposerView(View):
    """Tree editor for defining the node hierarchy of a SpatialTemplate."""

    def _flatten_tree(self, nodes_by_parent, parent_id=None, depth=0):
        result = []
        for node in nodes_by_parent.get(parent_id, []):
            result.append({'node': node, 'depth': depth})
            result.extend(self._flatten_tree(nodes_by_parent, parent_id=node.pk, depth=depth + 1))
        return result

    def get(self, request, pk):
        spatial_template = get_object_or_404(SpatialTemplate, pk=pk)
        nodes = list(spatial_template.nodes.order_by('sort_order', 'pk'))
        nodes_by_parent = {}
        for node in nodes:
            nodes_by_parent.setdefault(node.parent_id, []).append(node)
        flat_nodes = self._flatten_tree(nodes_by_parent, parent_id=None, depth=0)
        return render(request, 'netbox_plant_graph/spatial_template_compose.html', {
            'spatial_template': spatial_template,
            'flat_nodes': flat_nodes,
            'page_title': f'Compose: {spatial_template.name}',
            'node_type_choices': SpatialNodeTypeChoices.CHOICES,
        })

    def post(self, request, pk):
        spatial_template = get_object_or_404(SpatialTemplate, pk=pk)
        action = request.POST.get('action')

        if action == 'add_node':
            name_pattern = request.POST.get('name_pattern', '').strip()
            node_type = request.POST.get('node_type', '')
            quantity_raw = request.POST.get('quantity', '1')
            parent_pk = request.POST.get('parent_pk', '').strip()
            if not name_pattern or not node_type:
                messages.error(request, 'Name pattern and node type are required.')
                return redirect(reverse('plugins:netbox_plant_graph:spatial_template_compose', kwargs={'pk': pk}))
            try:
                quantity = max(1, int(quantity_raw))
            except (TypeError, ValueError):
                quantity = 1
            parent = None
            if parent_pk:
                parent = SpatialTemplateNode.objects.filter(pk=parent_pk, template=spatial_template).first()
            existing_orders = list(spatial_template.nodes.values_list('sort_order', flat=True))
            max_sort = max(existing_orders) if existing_orders else 0
            SpatialTemplateNode.objects.create(
                template=spatial_template,
                parent=parent,
                name_pattern=name_pattern,
                node_type=node_type,
                quantity=quantity,
                sort_order=max_sort + 1,
            )
            messages.success(request, f'Node "{name_pattern}" added.')

        elif action == 'remove_node':
            node_pk = request.POST.get('node_pk')
            SpatialTemplateNode.objects.filter(pk=node_pk, template=spatial_template).delete()

        return redirect(reverse('plugins:netbox_plant_graph:spatial_template_compose', kwargs={'pk': pk}))


class DisjointnessExceptionRequestView(View):
    """Guided single-page form for requesting a disjointness policy exception."""

    def get(self, request):
        from .forms import DisjointnessExceptionRequestForm
        fabric_id = _parse_int(request.GET.get('fabric_id'))
        initial = {}
        if fabric_id:
            initial['fabric'] = fabric_id
        form = DisjointnessExceptionRequestForm(initial=initial)
        return render(request, 'netbox_plant_graph/disjointness_exception_request.html', {
            'form': form,
            'page_title': 'Request Disjointness Exception',
            'fabrics': _fabric_choices(),
        })

    def post(self, request):
        from .forms import DisjointnessExceptionRequestForm
        form = DisjointnessExceptionRequestForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            actor = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
            exception = DisjointnessException.objects.create(
                fabric=data['fabric'],
                exception_type=data['exception_type'],
                plane_a=data['plane_a'],
                plane_b=data['plane_b'],
                scope_kind=data['scope_kind'],
                reason=data.get('reason') or '',
                expires_at=data.get('expires_at'),
                status='draft',
                active=False,
                created_by=actor,
            )
            messages.success(request, 'Disjointness exception request submitted.')
            return redirect(exception.get_absolute_url())
        return render(request, 'netbox_plant_graph/disjointness_exception_request.html', {
            'form': form,
            'page_title': 'Request Disjointness Exception',
            'fabrics': _fabric_choices(),
        })
