from collections import OrderedDict
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from urllib.parse import urlencode

from django import forms as django_forms
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from django.urls import reverse
from django.utils.html import format_html
from django.views import View
from django.views.generic import TemplateView
from dcim.models import Device, FrontPort, Interface, RearPort
from netbox.views import generic

from . import filtersets, forms, tables
from .models import (
    AuditEvent,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    OperationRun,
    Plane,
    StampRun,
    StampTemplate,
    StrandTermination,
    SuppressionRule,
    TransferMap,
    TransportChannel,
)
from .services.audit import record_audit_event
from .services.operations import execute_operation_profile
from .services import resolver as resolver_service
from .services.resolver import resolve_optical_lane_path
from .services.stamp_preview import build_v2_stamp_template_preview
from .services.stamping import execute_stamp_template, stamp_roce_4plane_mini_fabric
from .v2_registry import V2_OBJECT_SPECS, get_v2_object_spec_for_model


def _build_architecture_semantics_context(architecture: FabricArchitecture):
    roles = tuple(architecture.roles.order_by('slug'))
    transfer_patterns = []
    for pattern in architecture.transfer_patterns.order_by('slug'):
        rule = pattern.rule or {}
        pair_rows = tuple(rule.get('pairs') or ())
        transfer_patterns.append({
            'pattern': pattern,
            'rule_json': json.dumps(rule, indent=2, sort_keys=True),
            'pair_rows': pair_rows,
            'bidirectional': bool(rule.get('bidirectional')),
            'kind': rule.get('type') or pattern.pattern_kind,
        })

    allocation_rule_sets = []
    for rule_set in architecture.allocation_rule_sets.order_by('slug'):
        allocation_rule_sets.append({
            'rule_set': rule_set,
            'rule_json': json.dumps(rule_set.rule or {}, indent=2, sort_keys=True),
        })

    stamp_templates = []
    for template in architecture.stamp_templates.order_by('slug', 'pk'):
        template_spec = template.template or {}
        stamp_templates.append({
            'template': template,
            'executor': template_spec.get('executor') or {},
            'planes': tuple(template_spec.get('planes') or ()),
            'proof_paths': tuple(template_spec.get('proof_paths') or ()),
            'source_bindings': tuple(template_spec.get('source_bindings') or ()),
            'template_json': json.dumps(template_spec, indent=2, sort_keys=True),
        })

    return {
        'architecture_metadata_json': json.dumps(architecture.metadata or {}, indent=2, sort_keys=True),
        'roles': roles,
        'transfer_patterns': tuple(transfer_patterns),
        'allocation_rule_sets': tuple(allocation_rule_sets),
        'stamp_templates': tuple(stamp_templates),
    }


class V2RegisteredObjectView(generic.ObjectView):
    template_name = 'netbox_plant_graph/v2_object.html'

    def get_extra_context(self, request, instance):
        spec = get_v2_object_spec_for_model(instance.__class__)
        detail_fields = []
        for field_name in spec.resolved_detail_fields:
            value = getattr(instance, field_name)
            try:
                label = instance._meta.get_field(field_name).verbose_name.title()
            except FieldDoesNotExist:
                label = field_name.replace('_', ' ').title()
            detail_fields.append({
                'name': field_name,
                'label': label,
                'value': value,
                'is_empty': value in (None, ''),
            })
        extra_context = {
            'object_spec': spec,
            'detail_fields': detail_fields,
            'stamp_template_execute_url': (
                'plugins:netbox_plant_graph:stamptemplate_execute'
                if spec.registry_key == 'stamptemplate'
                else None
            ),
            'suppression_revoke_url': (
                reverse('plugins:netbox_plant_graph:suppressionrule_revoke', kwargs={'pk': instance.pk})
                if spec.registry_key == 'suppressionrule'
                else None
            ),
        }
        if isinstance(instance, FabricArchitecture):
            extra_context['architecture_semantics'] = _build_architecture_semantics_context(instance)
        return extra_context


class HomeView(TemplateView):
    template_name = 'netbox_plant_graph/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'architecture_count': FabricArchitecture.objects.count(),
            'fabric_count': Fabric.objects.count(),
            'plane_count': Plane.objects.count(),
            'optical_lane_count': OpticalLane.objects.count(),
            'transfer_map_count': TransferMap.objects.count(),
            'suppression_count': SuppressionRule.objects.count(),
            'audit_event_count': AuditEvent.objects.count(),
            'operation_run_count': OperationRun.objects.count(),
            'v2_object_links': tuple(
                {
                    'label': spec.label_plural,
                    'url_name': f'plugins:netbox_plant_graph:{spec.model._meta.model_name}_list',
                }
                for spec in V2_OBJECT_SPECS
            ),
        })
        return context


class GraphOverviewView(TemplateView):
    template_name = 'netbox_plant_graph/graph_overview.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics, selected_fabric = _selected_fabric_from_request(
            self.request,
            param_name='fabric_id',
            default_first=True,
        )
        graph_counts = ()
        graph_summary = ()
        lane_counts = ()
        plane_rows = ()
        audit_events = ()
        operations = ()
        if selected_fabric is not None:
            lane_qs = OpticalLane.objects.filter(fabric=selected_fabric).select_related('plane')
            plane_total = Plane.objects.filter(fabric=selected_fabric).count()
            selected_fabric.expected_plane_count = plane_total
            graph_summary = (
                CompatNamespace(label='Planes', name='planes', value=plane_total, count=plane_total),
                CompatNamespace(
                    label='Nodes',
                    name='nodes',
                    value=FabricNode.objects.filter(fabric=selected_fabric).count(),
                    count=FabricNode.objects.filter(fabric=selected_fabric).count(),
                ),
                CompatNamespace(
                    label='Endpoints',
                    name='endpoints',
                    value=Endpoint.objects.filter(fabric=selected_fabric).count(),
                    count=Endpoint.objects.filter(fabric=selected_fabric).count(),
                ),
                CompatNamespace(label='Optical Lanes', name='optical_lanes', value=lane_qs.count(), count=lane_qs.count()),
                CompatNamespace(
                    label='Transfer Maps',
                    name='transfer_maps',
                    value=TransferMap.objects.filter(fabric=selected_fabric).count(),
                    count=TransferMap.objects.filter(fabric=selected_fabric).count(),
                ),
            )
            graph_counts = graph_summary
            lane_counts = tuple(
                CompatNamespace(
                    label=f'Plane {row["plane__plane_number"]}' if row['plane__plane_number'] is not None else 'Unassigned',
                    name=f'plane-{row["plane__plane_number"]}' if row['plane__plane_number'] is not None else 'unassigned',
                    plane=row['plane__plane_number'],
                    total=row['total'],
                    value=row['total'],
                    count=row['total'],
                )
                for row in lane_qs.values('plane__plane_number').annotate(total=Count('id')).order_by('plane__plane_number')
            )
            finding_counts_by_plane = {
                row['payload__plane_id']: row['total']
                for row in AuditEvent.objects.filter(
                    fabric=selected_fabric,
                    event_type='policy_eval',
                )
                .values('payload__plane_id')
                .annotate(total=Count('id'))
            }
            plane_rows = tuple(
                CompatNamespace(
                    plane=f'Plane {plane.plane_number}',
                    name=plane.label or f'Plane {plane.plane_number}',
                    label=plane.label or f'Plane {plane.plane_number}',
                    status='ok',
                    lanes=lane_qs.filter(plane=plane).count(),
                    lane_count=lane_qs.filter(plane=plane).count(),
                    findings=finding_counts_by_plane.get(plane.pk, 0),
                    finding_count=finding_counts_by_plane.get(plane.pk, 0),
                )
                for plane in Plane.objects.filter(fabric=selected_fabric).order_by('plane_number', 'pk')
            )
            audit_events = tuple(
                AuditEvent.objects.filter(fabric=selected_fabric).order_by('-created', '-pk')[:10]
            )
            operations = tuple(
                OperationRun.objects.filter(fabric=selected_fabric).order_by('-created', '-pk')[:10]
            )
        context.update(
            {
                'page_title': 'Graph Overview',
                'fabrics': fabrics,
                'selected_fabric': selected_fabric,
                'graph_counts': graph_counts,
                'graph_summary': graph_summary,
                'lane_counts': lane_counts,
                'plane_rows': plane_rows,
                'audit_events': audit_events,
                'operations': operations,
            }
        )
        return context


class FabricOnboardView(View):
    template_name = 'netbox_plant_graph/fabric_onboard.html'

    def get(self, request):
        form = forms.FabricOnboardForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = forms.FabricOnboardForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        data = form.cleaned_data
        fabric = Fabric.objects.create(
            architecture=data.get('architecture'),
            name=data['name'],
            slug=data['slug'],
            status='active' if data.get('activate_fabric') else 'planned',
            tenant=data.get('tenant'),
            scope_site=data.get('site'),
            scope_location=data.get('location'),
            metadata={
                'description': data.get('description') or '',
                'tier_depth': data.get('tier_depth'),
                'disjointness_policy': data.get('disjointness_policy'),
                'default_stamp_template_id': (
                    data['default_stamp_template'].pk if data.get('default_stamp_template') is not None else None
                ),
            },
        )
        plane_count = data['expected_plane_count']
        for plane_number in range(1, plane_count + 1):
            Plane.objects.create(
                fabric=fabric,
                plane_number=plane_number,
                label=f'Plane {plane_number}',
            )

        record_audit_event(
            event_type='stamp',
            fabric=fabric,
            actor=request.user,
            subject=fabric,
            outcome='ok',
            message=f'Fabric {fabric.name} onboarded.',
            payload={
                'fabric_id': fabric.pk,
                'plane_count': plane_count,
                'architecture_id': data['architecture'].pk if data.get('architecture') is not None else None,
            },
        )

        if data.get('trigger_initial_rebuild'):
            try:
                execution = execute_operation_profile(
                    profile='generic_roce',
                    fabric=fabric,
                    parameters={'trigger': 'fabric_onboard'},
                    actor=request.user,
                )
                run = execution.run
                if execution.reused_existing:
                    messages.info(request, f'Fabric created. Reused operation run #{run.pk}.')
                else:
                    messages.success(request, f'Fabric created and initial rebuild run #{run.pk} completed.')
            except Exception as exc:
                messages.warning(request, f'Fabric created, but initial rebuild failed: {exc}')
        else:
            messages.success(request, f'Fabric "{fabric.name}" created with {plane_count} planes.')

        return redirect(fabric.get_absolute_url())


def _fabric_plane_assignment_unit(endpoint: Endpoint):
    node = endpoint.node
    return SimpleNamespace(
        pk=endpoint.pk,
        name=endpoint.name or endpoint.address,
        get_absolute_url=endpoint.get_absolute_url(),
        node=SimpleNamespace(
            name=node.name or node.address,
            address=node.address,
            get_absolute_url=node.get_absolute_url(),
        ),
    )


class FabricPlaneAssignmentView(View):
    template_name = 'netbox_plant_graph/fabric_plane_assignment.html'

    def _get_fabric(self, pk):
        return get_object_or_404(Fabric.objects.select_related('tenant'), pk=pk)

    def _units(self, fabric):
        return list(
            Endpoint.objects.filter(
                fabric=fabric,
                parent__isnull=True,
            )
            .select_related('node')
            .order_by('address', 'pk')
        )

    def _context(self, fabric):
        planes = list(Plane.objects.filter(fabric=fabric).order_by('plane_number', 'pk'))
        for plane in planes:
            plane.position = plane.plane_number
        endpoints = self._units(fabric)
        assigned_pairs = set(
            OpticalLane.objects.filter(
                fabric=fabric,
                plane_id__isnull=False,
                endpoint_id__in=[endpoint.pk for endpoint in endpoints],
            ).values_list('endpoint_id', 'plane_id')
        )
        matrix_rows = []
        for endpoint in endpoints:
            matrix_rows.append(
                {
                    'unit': _fabric_plane_assignment_unit(endpoint),
                    'cells': tuple(
                        {
                            'plane': plane,
                            'assigned': (endpoint.pk, plane.pk) in assigned_pairs,
                        }
                        for plane in planes
                    ),
                }
            )
        return {
            'page_title': f'Plane Assignment - {fabric.name}',
            'fabric': fabric,
            'planes': planes,
            'plane_rows': planes,
            'endpoints': tuple(endpoints),
            'attachment_units': tuple(endpoints),
            'matrix_rows': tuple(matrix_rows),
        }

    def get(self, request, pk):
        fabric = self._get_fabric(pk)
        return render(request, self.template_name, self._context(fabric))

    def post(self, request, pk):
        fabric = self._get_fabric(pk)
        form = forms.FabricPlaneAssignmentActionForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Invalid plane-assignment action.')
            return redirect(reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': fabric.pk}))

        data = form.cleaned_data
        endpoint = get_object_or_404(Endpoint, pk=data['unit_pk'], fabric=fabric)
        lanes = OpticalLane.objects.filter(fabric=fabric, endpoint=endpoint).order_by('lane_index', 'direction', 'pk')
        if not lanes.exists():
            messages.warning(request, f'{endpoint.address} has no optical lanes to assign.')
            return redirect(reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': fabric.pk}))

        action = data['action']
        if action == 'toggle':
            plane = get_object_or_404(Plane, pk=data['plane_pk'], fabric=fabric)
            if lanes.filter(plane=plane).exists():
                updated = lanes.filter(plane=plane).update(plane=None)
                messages.success(request, f'Removed {updated} lane assignments from Plane {plane.plane_number}.')
            else:
                updated = lanes.update(plane=plane)
                messages.success(request, f'Assigned {updated} lanes to Plane {plane.plane_number}.')
        elif action == 'assign_all':
            planes = list(Plane.objects.filter(fabric=fabric).order_by('plane_number', 'pk'))
            if not planes:
                messages.error(request, 'No planes exist for this fabric.')
            else:
                lane_ids_by_index = {}
                for lane in lanes:
                    lane_ids_by_index.setdefault(lane.lane_index, []).append(lane.pk)
                updated = 0
                for index, lane_index in enumerate(sorted(lane_ids_by_index)):
                    target_plane = planes[index % len(planes)]
                    lane_ids = lane_ids_by_index[lane_index]
                    updated += OpticalLane.objects.filter(pk__in=lane_ids).update(plane=target_plane)
                messages.success(request, f'Assigned {updated} lanes across {len(planes)} planes.')
        elif action == 'remove_all':
            updated = lanes.update(plane=None)
            messages.success(request, f'Removed plane assignment from {updated} lanes.')
        else:
            messages.error(request, 'Unsupported plane-assignment action.')

        return redirect(reverse('plugins:netbox_plant_graph:fabric_assign_planes', kwargs={'pk': fabric.pk}))


def _fabric_operations_object(fabric):
    metadata = fabric.metadata or {}
    policy = (metadata.get('disjointness_policy') or 'strict').replace('_', ' ')
    return CompatNamespace(
        pk=fabric.pk,
        name=fabric.name,
        description=metadata.get('description') or '',
        resolved_tenant=fabric.tenant,
        expected_plane_count=Plane.objects.filter(fabric=fabric).count(),
        tier_depth=metadata.get('tier_depth') or 3,
        get_disjointness_policy_display=policy.title(),
        get_absolute_url=fabric.get_absolute_url(),
    )


def _fabric_operation_run_row(run):
    finding_count = (run.result or {}).get('finding_count')
    if finding_count is None:
        finding_count = (run.result or {}).get('paths_failed', 0)
    run.started_at = run.started_at or run.created
    run.completed_at = run.completed_at
    run.finding_count = finding_count
    run.resolved_tenant = getattr(run.fabric, 'tenant', None)
    return run


def _fabric_stamp_run_row(run):
    completed_at = run.created if run.status == 'completed' else None
    run.started_at = run.created
    run.completed_at = completed_at
    run.resolved_tenant = getattr(run.fabric, 'tenant', None)
    return run


class FabricOperationsView(View):
    template_name = 'netbox_plant_graph/fabric_operations.html'

    def _fabric(self, pk):
        return get_object_or_404(Fabric.objects.select_related('tenant'), pk=pk)

    def _render(self, request, fabric):
        recent_builds = tuple(
            _fabric_stamp_run_row(run)
            for run in StampRun.objects.filter(fabric=fabric).order_by('-created', '-pk')[:10]
        )
        recent_audits = tuple(
            _fabric_operation_run_row(run)
            for run in OperationRun.objects.filter(fabric=fabric).order_by('-created', '-pk')[:10]
        )
        return render(
            request,
            self.template_name,
            {
                'object': _fabric_operations_object(fabric),
                'selected_fabric': fabric,
                'fabrics': Fabric.objects.order_by('name', 'pk'),
                'recent_builds': recent_builds,
                'recent_audits': recent_audits,
            },
        )

    def get(self, request, pk):
        return self._render(request, self._fabric(pk))

    def post(self, request, pk):
        fabric = self._fabric(pk)
        form = forms.FabricOperationsActionForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Invalid fabric operation action.')
            return redirect(reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': fabric.pk}))

        action = form.cleaned_data['action']
        if action == 'rebuild':
            profile = 'generic_roce'
        else:
            profile = 'madison_default'
        try:
            execution = execute_operation_profile(
                profile=profile,
                fabric=fabric,
                parameters={'trigger': 'fabric_operations', 'action': action},
                actor=request.user,
            )
            if execution.reused_existing:
                messages.info(request, f'Reused existing {action} run #{execution.run.pk}.')
            else:
                messages.success(request, f'Completed {action} run #{execution.run.pk}.')
        except Exception as exc:
            messages.error(request, f'Unable to run {action}: {exc}')
        return redirect(reverse('plugins:netbox_plant_graph:fabric_operations', kwargs={'pk': fabric.pk}))


class SeedV2ProofView(View):
    def post(self, request):
        result = stamp_roce_4plane_mini_fabric(actor=request.user)
        failures = [path for path in result.resolved_paths if not path.path_found]
        if failures:
            messages.error(request, f'Stamped {result.fabric.name}, but {len(failures)} proof paths failed.')
        else:
            messages.success(request, f'Stamped {result.fabric.name} with {len(result.resolved_paths)} resolved paths.')
        return redirect('plugins:netbox_plant_graph:home')


class StampTemplateExecuteView(TemplateView):
    template_name = 'netbox_plant_graph/stamp_template_execute.html'

    def dispatch(self, request, *args, **kwargs):
        self.template = StampTemplate.objects.get(pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def _form(self, data=None):
        return forms.StampTemplateExecuteForm(
            data=data,
            initial=forms.StampTemplateExecuteForm.initial_from_template(self.template),
            template=self.template,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get('form') or self._form()
        preview_parameters = {
            'fabric_name': form.initial.get('fabric_name'),
            'fabric_slug': form.initial.get('fabric_slug'),
        }
        if form.is_bound and form.is_valid():
            preview_parameters = {
                'fabric_name': form.cleaned_data['fabric_name'],
                'fabric_slug': form.cleaned_data['fabric_slug'],
            }
        context.update({
            'template': self.template,
            'form': form,
            'preview': build_v2_stamp_template_preview(self.template, preview_parameters),
        })
        return context

    def post(self, request, *args, **kwargs):
        form = self._form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        result = execute_stamp_template(
            template=self.template,
            fabric_name=form.cleaned_data['fabric_name'],
            fabric_slug=form.cleaned_data['fabric_slug'],
            source_bindings=form.source_bindings(),
            creation_options=form.creation_options(),
            actor=request.user,
        )
        failures = [path for path in result.resolved_paths if not path.path_found]
        if failures:
            messages.error(request, f'Stamped {result.fabric.name}, but {len(failures)} proof paths failed.')
        else:
            messages.success(
                request,
                f'Stamped {result.fabric.name} with {len(result.resolved_paths)} resolved paths.',
            )
        object_counts = result.stamp_run.result.get('object_counts') or {}
        messages.info(
            request,
            format_html(
                (
                    'Stamp run <a href="{}">#{}</a> completed with {} failures. '
                    '{} nodes, {} endpoints, {} optical lanes. '
                    '<a href="{}">Open fabric</a>.'
                ),
                result.stamp_run.get_absolute_url(),
                result.stamp_run.pk,
                len(failures),
                object_counts.get('nodes', 0),
                object_counts.get('endpoints', 0),
                object_counts.get('optical_lanes', 0),
                reverse('plugins:netbox_plant_graph:fabric', kwargs={'pk': result.fabric.pk}),
            ),
        )
        return redirect(result.fabric.get_absolute_url())


_BUILDER_NODE_TYPE_CHOICES = (
    ('root', 'Root'),
    ('group', 'Group'),
    ('rack', 'Rack'),
    ('device', 'Device'),
    ('other', 'Other'),
)
_BUILDER_NODE_TYPE_LABELS = dict(_BUILDER_NODE_TYPE_CHOICES)
_BUILDER_ENUMERATION_MODE_CHOICES = (
    ('sequential', 'Sequential'),
    ('reverse', 'Reverse'),
    ('custom', 'Custom'),
)
_BUILDER_ENUMERATION_MODE_LABELS = dict(_BUILDER_ENUMERATION_MODE_CHOICES)


class CompatNamespace(SimpleNamespace):
    def __getattr__(self, name):
        return ''


def _positive_int(value, default=1):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return parsed


def _template_payload(stamp_template):
    return dict(stamp_template.template or {})


def _save_template_payload(stamp_template, payload):
    stamp_template.template = payload
    stamp_template.save(update_fields=['template', 'last_updated'])


def _normalize_builder_connector(raw, *, fallback_id):
    connector_id = _positive_int(raw.get('id'), fallback_id)
    connector_number = _positive_int(raw.get('connector_number'), fallback_id)
    connector_type = raw.get('connector_type') or 'mpo-12'
    if connector_type not in dict(forms.BUILDER_CONNECTOR_TYPE_CHOICES):
        connector_type = 'custom'
    return {
        'id': connector_id,
        'connector_number': connector_number,
        'label': (raw.get('label') or '').strip(),
        'connector_type': connector_type,
        'position_count': _positive_int(raw.get('position_count'), 12),
    }


def _template_builder_state(stamp_template):
    payload = _template_payload(stamp_template)
    raw_state = payload.get('builder') or {}
    raw_a = raw_state.get('a_connectors') or ()
    raw_b = raw_state.get('b_connectors') or ()
    raw_mappings = raw_state.get('mappings') or ()

    seen_ids = set()

    def normalize_connectors(raw_connectors):
        normalized = []
        for index, raw in enumerate(raw_connectors, start=1):
            if not isinstance(raw, dict):
                continue
            connector = _normalize_builder_connector(raw, fallback_id=index)
            while connector['id'] in seen_ids:
                connector['id'] += 1
            seen_ids.add(connector['id'])
            normalized.append(connector)
        normalized.sort(key=lambda row: (row['connector_number'], row['id']))
        return normalized

    a_connectors = normalize_connectors(raw_a)
    b_connectors = normalize_connectors(raw_b)
    a_ids = {row['id'] for row in a_connectors}
    b_ids = {row['id'] for row in b_connectors}

    mappings = set()
    for raw in raw_mappings:
        if not isinstance(raw, dict):
            continue
        a_pk = _positive_int(raw.get('a_connector_pk'), 0)
        b_pk = _positive_int(raw.get('b_connector_pk'), 0)
        if a_pk in a_ids and b_pk in b_ids:
            mappings.add((a_pk, b_pk))

    return {
        'a_connectors': a_connectors,
        'b_connectors': b_connectors,
        'mappings': mappings,
    }


def _save_template_builder_state(stamp_template, builder_state):
    payload = _template_payload(stamp_template)
    payload['builder'] = {
        'a_connectors': [
            {
                'id': row['id'],
                'connector_number': row['connector_number'],
                'label': row['label'],
                'connector_type': row['connector_type'],
                'position_count': row['position_count'],
            }
            for row in builder_state['a_connectors']
        ],
        'b_connectors': [
            {
                'id': row['id'],
                'connector_number': row['connector_number'],
                'label': row['label'],
                'connector_type': row['connector_type'],
                'position_count': row['position_count'],
            }
            for row in builder_state['b_connectors']
        ],
        'mappings': [
            {
                'a_connector_pk': pair[0],
                'b_connector_pk': pair[1],
            }
            for pair in sorted(builder_state['mappings'])
        ],
    }
    _save_template_payload(stamp_template, payload)


def _builder_connector_namespace(row):
    return CompatNamespace(
        pk=row['id'],
        connector_number=row['connector_number'],
        label=row['label'],
        connector_type=row['connector_type'],
        position_count=row['position_count'],
        get_connector_type_display=dict(forms.BUILDER_CONNECTOR_TYPE_CHOICES).get(row['connector_type'], row['connector_type']),
        resolved_tenant=None,
    )


def _builder_matrix_rows(a_connectors, b_connectors, mappings):
    a_rows = [_builder_connector_namespace(row) for row in a_connectors]
    b_rows = [_builder_connector_namespace(row) for row in b_connectors]
    matrix_rows = []
    for a_row in a_rows:
        cells = []
        for b_row in b_rows:
            cells.append(
                SimpleNamespace(
                    b=b_row,
                    mapped=(a_row.pk, b_row.pk) in mappings,
                )
            )
        matrix_rows.append(SimpleNamespace(a=a_row, cells=tuple(cells)))
    return tuple(a_rows), tuple(b_rows), tuple(matrix_rows)


def _apply_builder_preset(builder_state, preset_name):
    a_rows = builder_state['a_connectors']
    b_rows = builder_state['b_connectors']
    limit = min(len(a_rows), len(b_rows))
    if limit == 0:
        builder_state['mappings'] = set()
        return
    if preset_name == 'straight':
        pairs = [
            (a_rows[index]['id'], b_rows[index]['id'])
            for index in range(limit)
        ]
    elif preset_name == 'reversed':
        pairs = [
            (a_rows[index]['id'], b_rows[limit - index - 1]['id'])
            for index in range(limit)
        ]
    elif preset_name == 'cross':
        pairs = [
            (a_rows[index]['id'], b_rows[(index + 1) % limit]['id'])
            for index in range(limit)
        ]
    else:
        pairs = []
    builder_state['mappings'] = set(pairs)


def _template_run_rows(stamp_template, *, limit=10):
    runs = StampRun.objects.filter(template=stamp_template).select_related('fabric').order_by('-created', '-pk')[:limit]
    rows = []
    for run in runs:
        rows.append(
            CompatNamespace(
                pk=run.pk,
                name=f'Run #{run.pk}',
                label=f'Run #{run.pk}',
                status=run.get_status_display(),
                get_status_display=run.get_status_display(),
                started_at=run.created,
                created=run.created,
                created_at=run.created,
                get_absolute_url=run.get_absolute_url(),
                result=run.fabric,
            )
        )
    return tuple(rows)


def _legacy_template_context(stamp_template):
    payload = _template_payload(stamp_template)
    builder_state = _template_builder_state(stamp_template)
    node_count = len((payload.get('spatial_compose') or {}).get('nodes') or ())
    return CompatNamespace(
        pk=stamp_template.pk,
        name=stamp_template.name,
        description=stamp_template.description,
        get_absolute_url=stamp_template.get_absolute_url(),
        assembly_type=(payload.get('assembly_type') or 'passive_assembly'),
        get_assembly_type_display=(payload.get('assembly_type') or 'passive_assembly').replace('_', ' ').title(),
        manufacturer=(payload.get('manufacturer') or ''),
        part_number=(payload.get('part_number') or ''),
        device_type=(payload.get('device_type') or ''),
        a_connectors=CompatNamespace(count=len(builder_state['a_connectors'])),
        b_connectors=CompatNamespace(count=len(builder_state['b_connectors'])),
        a_connector_count=len(builder_state['a_connectors']),
        b_connector_count=len(builder_state['b_connectors']),
        root_node_type=(payload.get('root_node_type') or 'fabric'),
        get_root_node_type_display=(payload.get('root_node_type') or 'fabric').replace('_', ' ').title(),
        nodes=CompatNamespace(count=node_count),
        node_count=node_count,
    )


class TemplateLibraryView(TemplateView):
    template_name = 'netbox_plant_graph/template_library.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run_rows = tuple(
            CompatNamespace(
                pk=run.pk,
                name=f'Run #{run.pk}',
                label=f'Run #{run.pk}',
                status=run.get_status_display(),
                get_status_display=run.get_status_display(),
                started_at=run.created,
                created=run.created,
                created_at=run.created,
                get_absolute_url=run.get_absolute_url(),
                result=run.fabric,
            )
            for run in StampRun.objects.select_related('fabric').order_by('-created', '-pk')[:10]
        )
        sections = (
            {
                'label': 'Stamp Templates',
                'count': StampTemplate.objects.count(),
                'recent': tuple(StampTemplate.objects.order_by('-created', '-pk')[:6]),
                'list_url': reverse('plugins:netbox_plant_graph:stamptemplate_list'),
                'add_url': reverse('plugins:netbox_plant_graph:stamptemplate_add'),
            },
            {
                'label': 'Stamp Runs',
                'count': StampRun.objects.count(),
                'recent': tuple(StampRun.objects.select_related('template', 'fabric').order_by('-created', '-pk')[:6]),
                'list_url': reverse('plugins:netbox_plant_graph:stamprun_list'),
                'add_url': None,
            },
            {
                'label': 'Fabrics',
                'count': Fabric.objects.count(),
                'recent': tuple(Fabric.objects.order_by('-created', '-pk')[:6]),
                'list_url': reverse('plugins:netbox_plant_graph:fabric_list'),
                'add_url': reverse('plugins:netbox_plant_graph:fabric_add'),
            },
        )
        context.update(
            {
                'sections': sections,
                'recent_runs': run_rows,
                'stamp_runs': run_rows,
            }
        )
        return context


class AssemblyTemplateBuildView(View):
    template_name = 'netbox_plant_graph/assembly_template_build.html'

    def _template(self, pk):
        return get_object_or_404(StampTemplate, pk=pk)

    def _render(self, request, stamp_template):
        state = _template_builder_state(stamp_template)
        a_connectors, b_connectors, matrix_rows = _builder_matrix_rows(
            state['a_connectors'],
            state['b_connectors'],
            state['mappings'],
        )
        template_context = _legacy_template_context(stamp_template)
        run_rows = _template_run_rows(stamp_template)
        return render(
            request,
            self.template_name,
            {
                'page_title': 'Template Builder',
                'assembly_template': template_context,
                'template': template_context,
                'a_connectors': a_connectors,
                'b_connectors': b_connectors,
                'matrix_rows': matrix_rows,
                'recent_runs': run_rows,
                'stamp_runs': run_rows,
            },
        )

    def get(self, request, pk):
        return self._render(request, self._template(pk))

    def post(self, request, pk):
        stamp_template = self._template(pk)
        state = _template_builder_state(stamp_template)
        action = (request.POST.get('action') or '').strip()

        if action == 'add_connector':
            form = forms.StampTemplateBuilderConnectorForm(request.POST)
            if form.is_valid():
                side = form.cleaned_data['side']
                connectors = state['a_connectors'] if side == 'A' else state['b_connectors']
                connector_id = max([row['id'] for row in state['a_connectors'] + state['b_connectors']] + [0]) + 1
                connectors.append(
                    {
                        'id': connector_id,
                        'connector_number': form.cleaned_data['connector_number'],
                        'label': form.cleaned_data['label'],
                        'connector_type': form.cleaned_data['connector_type'],
                        'position_count': form.cleaned_data['position_count'],
                    }
                )
                messages.success(request, f'Added {side}-side connector #{form.cleaned_data["connector_number"]}.')
            else:
                messages.error(request, 'Unable to add connector.')

        elif action == 'remove_connector':
            connector_pk = _positive_int(request.POST.get('connector_pk'), 0)
            removed = False
            for connector_key in ('a_connectors', 'b_connectors'):
                original = state[connector_key]
                filtered = [row for row in original if row['id'] != connector_pk]
                if len(filtered) != len(original):
                    removed = True
                    state[connector_key] = filtered
            if removed:
                state['mappings'] = {
                    (a_pk, b_pk)
                    for (a_pk, b_pk) in state['mappings']
                    if a_pk != connector_pk and b_pk != connector_pk
                }
                messages.success(request, 'Connector removed.')
            else:
                messages.error(request, 'Connector not found.')

        elif action == 'toggle_mapping':
            form = forms.StampTemplateBuilderToggleMappingForm(request.POST)
            if form.is_valid():
                pair = (form.cleaned_data['a_connector_pk'], form.cleaned_data['b_connector_pk'])
                if pair in state['mappings']:
                    state['mappings'].remove(pair)
                    messages.success(request, 'Mapping removed.')
                else:
                    a_ids = {row['id'] for row in state['a_connectors']}
                    b_ids = {row['id'] for row in state['b_connectors']}
                    if pair[0] in a_ids and pair[1] in b_ids:
                        state['mappings'].add(pair)
                        messages.success(request, 'Mapping added.')
                    else:
                        messages.error(request, 'Mapping references unknown connectors.')
            else:
                messages.error(request, 'Unable to toggle mapping.')

        elif action == 'apply_preset':
            form = forms.StampTemplateBuilderPresetForm(request.POST)
            if form.is_valid():
                _apply_builder_preset(state, form.cleaned_data['preset_name'])
                messages.success(request, f'Applied {form.cleaned_data["preset_name"]} preset.')
            else:
                messages.error(request, 'Unable to apply preset.')

        else:
            messages.error(request, 'Unsupported builder action.')

        _save_template_builder_state(stamp_template, state)
        return redirect(reverse('plugins:netbox_plant_graph:assembly_template_build', kwargs={'pk': stamp_template.pk}))


class StampTemplateWizardBaseView(TemplateView):
    template_name = 'netbox_plant_graph/assembly_stamp_wizard.html'
    page_title = 'Stamp Template'

    def dispatch(self, request, *args, **kwargs):
        self.template_obj = get_object_or_404(StampTemplate, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def _form(self, data=None):
        return forms.StampTemplateExecuteForm(
            data=data,
            initial=forms.StampTemplateExecuteForm.initial_from_template(self.template_obj),
            template=self.template_obj,
        )

    def _extra_context(self):
        return {}

    def _adapt_result(self, execution_result):
        return execution_result

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        template_context = _legacy_template_context(self.template_obj)
        run_rows = _template_run_rows(self.template_obj)
        context.update(
            {
                'page_title': self.page_title,
                'template': template_context,
                'form': kwargs.get('form') or self._form(),
                'recent_runs': run_rows,
                'stamp_runs': run_rows,
                **self._extra_context(),
            }
        )
        if 'result' in kwargs:
            context['result'] = kwargs['result']
        return context

    def post(self, request, *args, **kwargs):
        form = self._form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            execution_result = execute_stamp_template(
                template=self.template_obj,
                fabric_name=form.cleaned_data['fabric_name'],
                fabric_slug=form.cleaned_data['fabric_slug'],
                source_bindings=form.source_bindings(),
                creation_options=form.creation_options(),
                actor=request.user,
            )
        except Exception as exc:
            messages.error(request, f'Stamp execution failed: {exc}')
            return self.render_to_response(self.get_context_data(form=form))

        failures = [path for path in execution_result.resolved_paths if not path.path_found]
        if failures:
            messages.error(request, f'Stamped {execution_result.fabric.name}, but {len(failures)} proof paths failed.')
        else:
            messages.success(
                request,
                f'Stamped {execution_result.fabric.name} with {len(execution_result.resolved_paths)} resolved paths.',
            )
        return self.render_to_response(self.get_context_data(form=form, result=self._adapt_result(execution_result)))


class AssemblyStampWizardView(StampTemplateWizardBaseView):
    template_name = 'netbox_plant_graph/assembly_stamp_wizard.html'
    page_title = 'Stamp Passive Device'

    def _adapt_result(self, execution_result):
        return SimpleNamespace(
            device=execution_result.fabric,
            rear_ports=tuple(execution_result.destination_lanes),
            front_ports=tuple(execution_result.source_lanes),
            port_mappings=tuple(execution_result.resolved_paths),
        )


class AssemblyGraphStampWizardView(StampTemplateWizardBaseView):
    template_name = 'netbox_plant_graph/assembly_graph_stamp_wizard.html'
    page_title = 'Stamp Graph Assembly'

    def _adapt_result(self, execution_result):
        fabric_node = FabricNode.objects.filter(fabric=execution_result.fabric).order_by('address', 'pk').first()
        if fabric_node is None:
            fabric_node = execution_result.fabric
        return SimpleNamespace(
            fabric_node=fabric_node,
            connector_endpoints=tuple(
                Endpoint.objects.filter(
                    fabric=execution_result.fabric,
                    endpoint_kind='subconnector',
                ).order_by('address', 'pk')
            ),
            port_endpoints=tuple(
                Endpoint.objects.filter(
                    fabric=execution_result.fabric,
                    endpoint_kind='plugin_port',
                ).order_by('address', 'pk')
            ),
            transfer_maps=tuple(TransferMap.objects.filter(fabric=execution_result.fabric).order_by('pk')),
        )


class BreakoutStampWizardView(StampTemplateWizardBaseView):
    template_name = 'netbox_plant_graph/breakout_stamp_wizard.html'
    page_title = 'Apply Breakout Template'

    def _extra_context(self):
        template_spec = self.template_obj.template or {}
        return {'specs': tuple(template_spec.get('breakout_specs') or ())}

    def _adapt_result(self, execution_result):
        created_ids = (
            ((execution_result.stamp_run.result or {}).get('netbox_created_objects') or {}).get('interfaces')
            or []
        )
        created = tuple(Interface.objects.filter(pk__in=created_ids).order_by('device__name', 'name', 'pk'))
        return SimpleNamespace(created=created, skipped=tuple())


class SpatialStampWizardView(StampTemplateWizardBaseView):
    template_name = 'netbox_plant_graph/spatial_stamp_wizard.html'
    page_title = 'Stamp Spatial Template'

    def _extra_context(self):
        return {'node_tree': tuple()}

    def _adapt_result(self, execution_result):
        placements = tuple(
            Endpoint.objects.filter(fabric=execution_result.fabric).order_by('address', 'pk')[:200]
        )
        return SimpleNamespace(
            locations=tuple(),
            racks=tuple(),
            placements=placements,
        )


class RackPopulationStampWizardView(StampTemplateWizardBaseView):
    template_name = 'netbox_plant_graph/rack_population_stamp_wizard.html'
    page_title = 'Stamp Rack Population Template'

    def _extra_context(self):
        template_spec = self.template_obj.template or {}
        return {'slots': tuple(template_spec.get('rack_slots') or ())}

    def _adapt_result(self, execution_result):
        created_ids = (
            ((execution_result.stamp_run.result or {}).get('netbox_created_objects') or {}).get('devices')
            or []
        )
        return SimpleNamespace(
            devices=tuple(FabricNode.objects.filter(fabric=execution_result.fabric, source_id__in=created_ids)),
            assembly_results=tuple(),
        )


def _spatial_compose_nodes(stamp_template):
    payload = _template_payload(stamp_template)
    state = payload.get('spatial_compose') or {}
    nodes = []
    for index, raw in enumerate(state.get('nodes') or (), start=1):
        if not isinstance(raw, dict):
            continue
        node_id = _positive_int(raw.get('id'), index)
        nodes.append(
            {
                'id': node_id,
                'name_pattern': (raw.get('name_pattern') or '').strip(),
                'node_type': raw.get('node_type') if raw.get('node_type') in _BUILDER_NODE_TYPE_LABELS else 'other',
                'quantity': _positive_int(raw.get('quantity'), 1),
                'parent_id': _positive_int(raw.get('parent_id'), 0),
            }
        )
    nodes.sort(key=lambda row: (row['id'], row['name_pattern']))
    return nodes


def _save_spatial_compose_nodes(stamp_template, nodes):
    payload = _template_payload(stamp_template)
    payload['spatial_compose'] = {
        'nodes': [
            {
                'id': row['id'],
                'name_pattern': row['name_pattern'],
                'node_type': row['node_type'],
                'quantity': row['quantity'],
                'parent_id': row['parent_id'],
            }
            for row in nodes
        ]
    }
    _save_template_payload(stamp_template, payload)


def _flatten_spatial_nodes(nodes):
    node_by_id = {row['id']: row for row in nodes}
    children = {}
    for row in nodes:
        parent_id = row['parent_id']
        if parent_id and parent_id in node_by_id and parent_id != row['id']:
            children.setdefault(parent_id, []).append(row)
        else:
            children.setdefault(0, []).append(row)

    entries = []

    def walk(parent_id, depth):
        for row in sorted(children.get(parent_id, ()), key=lambda item: (item['id'], item['name_pattern'])):
            parent = node_by_id.get(row['parent_id'])
            parent_obj = SimpleNamespace(
                pk=parent['id'],
                name_pattern=parent['name_pattern'],
            ) if parent is not None else None
            node_obj = SimpleNamespace(
                pk=row['id'],
                id=row['id'],
                name_pattern=row['name_pattern'],
                node_type=row['node_type'],
                quantity=row['quantity'],
                parent=parent_obj,
                get_node_type_display=_BUILDER_NODE_TYPE_LABELS.get(row['node_type'], row['node_type']),
            )
            entries.append(SimpleNamespace(node=node_obj, depth=depth))
            walk(row['id'], depth + 1)

    walk(0, 0)
    return tuple(entries)


class SpatialTemplateComposeView(View):
    template_name = 'netbox_plant_graph/spatial_template_compose.html'

    def _template(self, pk):
        return get_object_or_404(StampTemplate, pk=pk)

    def _render(self, request, stamp_template):
        flat_nodes = _flatten_spatial_nodes(_spatial_compose_nodes(stamp_template))
        template_context = _legacy_template_context(stamp_template)
        run_rows = _template_run_rows(stamp_template)
        return render(
            request,
            self.template_name,
            {
                'page_title': 'Spatial Template Composer',
                'spatial_template': template_context,
                'flat_nodes': flat_nodes,
                'node_type_choices': _BUILDER_NODE_TYPE_CHOICES,
                'recent_runs': run_rows,
                'stamp_runs': run_rows,
            },
        )

    def get(self, request, pk):
        return self._render(request, self._template(pk))

    def post(self, request, pk):
        stamp_template = self._template(pk)
        nodes = _spatial_compose_nodes(stamp_template)
        action = (request.POST.get('action') or '').strip()

        if action == 'add_node':
            name_pattern = (request.POST.get('name_pattern') or '').strip()
            node_type = (request.POST.get('node_type') or '').strip()
            if not name_pattern:
                messages.error(request, 'Name Pattern is required.')
            elif node_type not in _BUILDER_NODE_TYPE_LABELS:
                messages.error(request, 'Node Type is required.')
            else:
                node_id = max([row['id'] for row in nodes] + [0]) + 1
                parent_id = _positive_int(request.POST.get('parent_pk'), 0)
                if parent_id not in {row['id'] for row in nodes}:
                    parent_id = 0
                nodes.append(
                    {
                        'id': node_id,
                        'name_pattern': name_pattern,
                        'node_type': node_type,
                        'quantity': _positive_int(request.POST.get('quantity'), 1),
                        'parent_id': parent_id,
                    }
                )
                messages.success(request, 'Node added.')
        elif action == 'remove_node':
            target_id = _positive_int(request.POST.get('node_pk'), 0)
            descendants = {target_id}
            changed = True
            while changed:
                changed = False
                for row in nodes:
                    if row['parent_id'] in descendants and row['id'] not in descendants:
                        descendants.add(row['id'])
                        changed = True
            before = len(nodes)
            nodes = [row for row in nodes if row['id'] not in descendants]
            if len(nodes) < before:
                messages.success(request, 'Node removed.')
            else:
                messages.error(request, 'Node not found.')
        else:
            messages.error(request, 'Unsupported compose action.')

        _save_spatial_compose_nodes(stamp_template, nodes)
        return redirect(reverse('plugins:netbox_plant_graph:spatial_template_compose', kwargs={'pk': stamp_template.pk}))


def _connection_builder_state(stamp_template):
    payload = _template_payload(stamp_template)
    state = payload.get('connection_builder') or {}
    connections = []
    for index, raw in enumerate(state.get('connections') or (), start=1):
        if not isinstance(raw, dict):
            continue
        connection_id = _positive_int(raw.get('id'), index)
        mode = raw.get('enumeration_mode') if raw.get('enumeration_mode') in _BUILDER_ENUMERATION_MODE_LABELS else 'sequential'
        connections.append(
            {
                'id': connection_id,
                'name': (raw.get('name') or '').strip(),
                'description': (raw.get('description') or '').strip(),
                'assembly_template_pk': _positive_int(raw.get('assembly_template_pk'), 0),
                'source_node_pk': _positive_int(raw.get('source_node_pk'), 0),
                'dest_node_pk': _positive_int(raw.get('dest_node_pk'), 0),
                'source_slot_index': _positive_int(raw.get('source_slot_index'), 1),
                'source_connector_number': _positive_int(raw.get('source_connector_number'), 1),
                'dest_slot_index': _positive_int(raw.get('dest_slot_index'), 1),
                'dest_connector_number': _positive_int(raw.get('dest_connector_number'), 1),
                'enumeration_mode': mode,
                'label_pattern': (raw.get('label_pattern') or '').strip(),
            }
        )
    connections.sort(key=lambda row: (row['id'], row['name']))
    return connections


def _save_connection_builder_state(stamp_template, connections):
    payload = _template_payload(stamp_template)
    payload['connection_builder'] = {
        'connections': list(connections),
    }
    _save_template_payload(stamp_template, payload)


class ConnectionTemplateBuilderView(View):
    template_name = 'netbox_plant_graph/connection_template_builder.html'

    def _template(self, pk):
        return get_object_or_404(StampTemplate, pk=pk)

    def _node_lookup(self, stamp_template):
        flat_nodes = _flatten_spatial_nodes(_spatial_compose_nodes(stamp_template))
        nodes = [entry.node for entry in flat_nodes]
        by_pk = {node.pk: node for node in nodes}
        return nodes, by_pk

    def _render(self, request, stamp_template):
        nodes, node_by_pk = self._node_lookup(stamp_template)
        template_index = {obj.pk: obj for obj in StampTemplate.objects.order_by('name', 'pk')}
        template_context = _legacy_template_context(stamp_template)
        run_rows = _template_run_rows(stamp_template)
        connections = []
        for row in _connection_builder_state(stamp_template):
            assembly_template = template_index.get(row['assembly_template_pk'])
            source_node = node_by_pk.get(row['source_node_pk'])
            dest_node = node_by_pk.get(row['dest_node_pk'])
            connections.append(
                SimpleNamespace(
                    pk=row['id'],
                    name=row['name'],
                    description=row['description'],
                    assembly_template=assembly_template or '-',
                    source_node=source_node.name_pattern if source_node is not None else '-',
                    dest_node=dest_node.name_pattern if dest_node is not None else '-',
                    source_slot_index=row['source_slot_index'],
                    source_connector_number=row['source_connector_number'],
                    dest_slot_index=row['dest_slot_index'],
                    dest_connector_number=row['dest_connector_number'],
                    enumeration_mode=row['enumeration_mode'],
                    get_enumeration_mode_display=_BUILDER_ENUMERATION_MODE_LABELS.get(row['enumeration_mode'], row['enumeration_mode']),
                    label_pattern=row['label_pattern'],
                )
            )
        return render(
            request,
            self.template_name,
            {
                'page_title': 'Connection Template Builder',
                'spatial_template': template_context,
                'connections': tuple(connections),
                'assembly_templates': tuple(StampTemplate.objects.order_by('name', 'pk')),
                'nodes': tuple(nodes),
                'enumeration_mode_choices': _BUILDER_ENUMERATION_MODE_CHOICES,
                'recent_runs': run_rows,
                'stamp_runs': run_rows,
            },
        )

    def get(self, request, pk):
        return self._render(request, self._template(pk))

    def post(self, request, pk):
        stamp_template = self._template(pk)
        connections = _connection_builder_state(stamp_template)
        action = (request.POST.get('action') or '').strip()

        if action == 'add_connection':
            name = (request.POST.get('name') or '').strip()
            assembly_template_pk = _positive_int(request.POST.get('assembly_template_pk'), 0)
            source_node_pk = _positive_int(request.POST.get('source_node_pk'), 0)
            dest_node_pk = _positive_int(request.POST.get('dest_node_pk'), 0)
            enumeration_mode = (request.POST.get('enumeration_mode') or '').strip()

            if not name:
                messages.error(request, 'Name is required.')
            elif assembly_template_pk == 0:
                messages.error(request, 'Assembly Template is required.')
            elif source_node_pk == 0 or dest_node_pk == 0:
                messages.error(request, 'Source and destination nodes are required.')
            elif enumeration_mode not in _BUILDER_ENUMERATION_MODE_LABELS:
                messages.error(request, 'Enumeration mode is required.')
            else:
                connection_id = max([row['id'] for row in connections] + [0]) + 1
                connections.append(
                    {
                        'id': connection_id,
                        'name': name,
                        'description': (request.POST.get('description') or '').strip(),
                        'assembly_template_pk': assembly_template_pk,
                        'source_node_pk': source_node_pk,
                        'dest_node_pk': dest_node_pk,
                        'source_slot_index': _positive_int(request.POST.get('source_slot_index'), 1),
                        'source_connector_number': _positive_int(request.POST.get('source_connector_number'), 1),
                        'dest_slot_index': _positive_int(request.POST.get('dest_slot_index'), 1),
                        'dest_connector_number': _positive_int(request.POST.get('dest_connector_number'), 1),
                        'enumeration_mode': enumeration_mode,
                        'label_pattern': (request.POST.get('label_pattern') or '').strip(),
                    }
                )
                messages.success(request, 'Connection template added.')
        elif action == 'remove_connection':
            target_pk = _positive_int(request.POST.get('connection_pk'), 0)
            before = len(connections)
            connections = [row for row in connections if row['id'] != target_pk]
            if len(connections) < before:
                messages.success(request, 'Connection template removed.')
            else:
                messages.error(request, 'Connection template not found.')
        else:
            messages.error(request, 'Unsupported connection action.')

        _save_connection_builder_state(stamp_template, connections)
        return redirect(reverse('plugins:netbox_plant_graph:connection_template_builder', kwargs={'pk': stamp_template.pk}))


def _stamp_run_steps(stamp_run):
    result = stamp_run.result or {}
    path_rows = result.get('resolved_paths') or ()
    steps = []
    for index, row in enumerate(path_rows, start=1):
        if isinstance(row, dict):
            status = 'stamped' if stamp_run.status == 'completed' else 'pending'
            steps.append(
                SimpleNamespace(
                    pk=index,
                    status=status,
                    get_status_display=status.replace('_', ' ').title(),
                    template_type=SimpleNamespace(model='path'),
                    template=stamp_run.template,
                    result=stamp_run.fabric,
                    stamped_at=stamp_run.created if stamp_run.status == 'completed' else None,
                    stamped_by=None,
                    error_detail=row.get('error') or '',
                    parameters=row,
                )
            )
    if not steps:
        status = 'stamped' if stamp_run.status == 'completed' else stamp_run.status
        steps.append(
            SimpleNamespace(
                pk=1,
                status=status,
                get_status_display=status.replace('_', ' ').title(),
                template_type=SimpleNamespace(model='stamp_run'),
                template=stamp_run.template,
                result=stamp_run.fabric,
                stamped_at=stamp_run.created if stamp_run.status == 'completed' else None,
                stamped_by=None,
                error_detail=stamp_run.error_detail,
                parameters=stamp_run.parameters or {},
            )
        )
    return tuple(steps)


def _rerun_stamp_template_from_run(stamp_run, *, actor=None, preview=False):
    if stamp_run.template is None:
        raise ValueError('Stamp run has no template. Cannot execute deployment action.')
    params = forms.StampTemplateRerunForm.initial_from_run(stamp_run)
    fabric_name = params['fabric_name']
    fabric_slug = params['fabric_slug']
    if preview:
        suffix = timezone.now().strftime('%Y%m%d%H%M%S')
        fabric_name = f'{fabric_name} Preview {suffix}'
        fabric_slug = slugify(f'{fabric_slug}-preview-{suffix}')[:200]
    execution_result = execute_stamp_template(
        template=stamp_run.template,
        fabric_name=fabric_name,
        fabric_slug=fabric_slug,
        actor=actor,
    )
    return execution_result.stamp_run


def _rollback_stamp_run_objects(stamp_run, *, actor=None):
    managed_objects = (stamp_run.result or {}).get('managed_objects') or {}
    deletion_order = (
        ('transfer_maps', TransferMap),
        ('strand_terminations', StrandTermination),
        ('optical_lanes', OpticalLane),
        ('fiber_strands', FiberStrand),
        ('fiber_segments', FiberSegment),
        ('transport_channels', TransportChannel),
        ('connector_positions', ConnectorPosition),
        ('endpoints', Endpoint),
        ('nodes', FabricNode),
        ('planes', Plane),
    )
    deleted_total = 0
    for key, model in deletion_order:
        object_ids = managed_objects.get(key) or ()
        if not object_ids:
            continue
        deleted_count, _ = model.objects.filter(pk__in=object_ids).delete()
        deleted_total += deleted_count

    for fabric_id in managed_objects.get('fabrics') or ():
        has_other_runs = StampRun.objects.exclude(pk=stamp_run.pk).filter(fabric_id=fabric_id).exists()
        if has_other_runs:
            continue
        deleted_count, _ = Fabric.objects.filter(pk=fabric_id).delete()
        deleted_total += deleted_count

    metadata = dict(stamp_run.metadata or {})
    metadata['rollback'] = {
        'deleted_total': deleted_total,
        'rolled_back_at': timezone.now().isoformat(),
    }
    stamp_run.metadata = metadata
    stamp_run.status = 'failed'
    stamp_run.error_detail = 'Rolled back from deployment workflow.'
    stamp_run.save(update_fields=['status', 'error_detail', 'metadata', 'last_updated'])
    record_audit_event(
        event_type='stamp',
        fabric=stamp_run.fabric,
        actor=actor,
        subject=stamp_run,
        outcome='ok',
        message=f'Stamp run #{stamp_run.pk} rollback completed.',
        payload={'stamp_run_id': stamp_run.pk, 'deleted_total': deleted_total},
    )
    return deleted_total


class DeploymentPlanWorkflowView(View):
    template_name = 'netbox_plant_graph/deployment_plan_workflow.html'

    def _stamp_run(self, pk):
        return get_object_or_404(StampRun.objects.select_related('template', 'fabric'), pk=pk)

    def _render(self, request, stamp_run):
        stamps = _stamp_run_steps(stamp_run)
        total_steps = len(stamps)
        current_step = _positive_int(request.GET.get('step'), 1) - 1
        if total_steps:
            current_step = max(0, min(current_step, total_steps - 1))
        else:
            current_step = 0
        current_stamp = stamps[current_step] if total_steps else None
        completed_steps = sum(1 for stamp in stamps if stamp.status in {'stamped', 'validated', 'completed'})
        next_stamp = stamps[current_step + 1] if total_steps and current_step + 1 < total_steps else None
        recent_rows = []
        for run in StampRun.objects.select_related('fabric').order_by('-created', '-pk')[:10]:
            recent_rows.append(
                CompatNamespace(
                    pk=run.pk,
                    name=f'Run #{run.pk}',
                    label=f'Run #{run.pk}',
                    status=run.get_status_display(),
                    get_status_display=run.get_status_display(),
                    started_at=run.created,
                    created=run.created,
                    created_at=run.created,
                    get_absolute_url=run.get_absolute_url(),
                    result=run.fabric,
                )
            )
        return render(
            request,
            self.template_name,
            {
                'page_title': 'Deployment Plan Workflow',
                'deployment_plan': stamp_run,
                'stamps': stamps,
                'total_steps': total_steps,
                'current_step': current_step,
                'completed_steps': completed_steps,
                'current_stamp': current_stamp,
                'next_stamp': next_stamp,
                'recent_runs': tuple(recent_rows),
                'stamp_runs': tuple(recent_rows),
            },
        )

    def get(self, request, pk):
        return self._render(request, self._stamp_run(pk))

    def post(self, request, pk):
        stamp_run = self._stamp_run(pk)
        form = forms.StampTemplateDeploymentActionForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Invalid deployment workflow action.')
            return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': stamp_run.pk}))

        action = form.cleaned_data['action']
        current_step = form.cleaned_data.get('current_step') or 0
        if action == 'skip_stamp':
            return redirect(
                f'{reverse("plugins:netbox_plant_graph:deployment_plan_workflow", kwargs={"pk": stamp_run.pk})}?{urlencode({"step": current_step + 2})}'
            )
        try:
            preview = action == 'preview_stamp'
            new_run = _rerun_stamp_template_from_run(stamp_run, actor=request.user, preview=preview)
        except Exception as exc:
            messages.error(request, f'Unable to execute deployment action: {exc}')
            return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': stamp_run.pk}))

        messages.success(request, f'Created stamp run #{new_run.pk} from deployment workflow.')
        return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': new_run.pk}))


class DeploymentPlanExecuteView(View):
    def post(self, request, pk):
        stamp_run = get_object_or_404(StampRun.objects.select_related('template', 'fabric'), pk=pk)
        try:
            new_run = _rerun_stamp_template_from_run(stamp_run, actor=request.user, preview=False)
        except Exception as exc:
            messages.error(request, f'Unable to execute deployment plan: {exc}')
            return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': stamp_run.pk}))
        messages.success(request, f'Created stamp run #{new_run.pk}.')
        return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': new_run.pk}))


class DeploymentPlanRollbackView(View):
    def post(self, request, pk):
        stamp_run = get_object_or_404(StampRun.objects.select_related('template', 'fabric'), pk=pk)
        try:
            deleted_total = _rollback_stamp_run_objects(stamp_run, actor=request.user)
        except Exception as exc:
            messages.error(request, f'Unable to roll back deployment plan: {exc}')
            return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': stamp_run.pk}))
        messages.success(request, f'Rolled back deployment plan objects ({deleted_total} deleted rows).')
        return redirect(reverse('plugins:netbox_plant_graph:deployment_plan_workflow', kwargs={'pk': stamp_run.pk}))


def _build_list_view(spec):
    return type(
        spec.list_view_name,
        (generic.ObjectListView,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
            'table': getattr(tables, spec.table_name),
            'filterset': getattr(filtersets, spec.filterset_name),
            'filterset_form': getattr(forms, spec.filter_form_name),
        },
    )


def _build_detail_view(spec):
    return type(
        spec.detail_view_name,
        (V2RegisteredObjectView,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
        },
    )


def _build_edit_view(spec):
    return type(
        spec.edit_view_name,
        (generic.ObjectEditView,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
            'form': getattr(forms, spec.form_name),
        },
    )


def _build_queryset_view(spec, view_name, base_class):
    return type(
        view_name,
        (base_class,),
        {
            '__module__': __name__,
            'queryset': spec.model.objects.all(),
        },
    )


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.list_view_name] = _build_list_view(_spec)
    globals()[_spec.detail_view_name] = _build_detail_view(_spec)
    globals()[_spec.edit_view_name] = _build_edit_view(_spec)
    globals()[_spec.delete_view_name] = _build_queryset_view(_spec, _spec.delete_view_name, generic.ObjectDeleteView)
    globals()[_spec.changelog_view_name] = _build_queryset_view(
        _spec, _spec.changelog_view_name, generic.ObjectChangeLogView
    )
    globals()[_spec.journal_view_name] = _build_queryset_view(_spec, _spec.journal_view_name, generic.ObjectJournalView)


def _cable_assembly_reference(cable, *, fallback_site_id=None, fallback_cable_id=''):
    if cable is None:
        site_label = f'Site #{fallback_site_id}' if fallback_site_id else 'Unknown Site'
        display = f'{site_label} / {fallback_cable_id}' if fallback_cable_id else site_label
        return SimpleNamespace(
            pk=None,
            url=None,
            display=display,
            model='cableassembly',
            site=site_label,
            site_id=fallback_site_id,
            cable_id=fallback_cable_id,
            manufacturer='',
            serial_number='',
            model_id='',
            description='',
        )
    return SimpleNamespace(
        pk=cable.pk,
        url=cable.get_absolute_url(),
        display=f'{cable.site.name} / {cable.cable_id}',
        model='cableassembly',
        site=cable.site.name,
        site_id=cable.site_id,
        cable_id=cable.cable_id,
        manufacturer=cable.manufacturer,
        serial_number=cable.serial_number,
        model_id=cable.model_id,
        description=cable.description,
    )


def _cable_assembly_map_for_pairs(pairs):
    normalized_pairs = {
        (int(site_id), str(cable_id))
        for site_id, cable_id in pairs
        if site_id and cable_id
    }
    if not normalized_pairs:
        return {}
    site_ids = {site_id for site_id, _ in normalized_pairs}
    cable_ids = {cable_id for _, cable_id in normalized_pairs}
    cables = CableAssembly.objects.select_related('site').filter(
        site_id__in=site_ids,
        cable_id__in=cable_ids,
    )
    return {
        (cable.site_id, cable.cable_id): cable
        for cable in cables
    }


def _cable_references_by_mpo_position_ids(position_ids):
    normalized_position_ids = {
        int(position_id)
        for position_id in position_ids
        if position_id not in (None, '')
    }
    if not normalized_position_ids:
        return {}

    terminations = StrandTermination.objects.select_related('strand').filter(
        mpo_position_id__in=normalized_position_ids
    )
    pair_by_position_id = {}
    all_pairs = set()
    for termination in terminations:
        strand = termination.strand
        pair = (strand.cable_site_id, strand.cable_id)
        if not pair[0] or not pair[1]:
            continue
        pairs = pair_by_position_id.setdefault(termination.mpo_position_id, set())
        pairs.add(pair)
        all_pairs.add(pair)

    cable_by_pair = _cable_assembly_map_for_pairs(all_pairs)
    references_by_position_id = {}
    for position_id, pairs in pair_by_position_id.items():
        references_by_position_id[position_id] = tuple(
            _cable_assembly_reference(
                cable_by_pair.get((site_id, cable_id)),
                fallback_site_id=site_id,
                fallback_cable_id=cable_id,
            )
            for site_id, cable_id in sorted(pairs, key=lambda item: (item[0], item[1]))
        )
    return references_by_position_id


def _path_step_cable_reference(
    *,
    object_type,
    object_id,
    metadata,
    strand_by_id,
    cable_by_pair,
):
    if object_type != 'fiber_strand':
        return None

    strand = strand_by_id.get(object_id)
    if strand is not None and strand.cable_site_id and strand.cable_id:
        pair = (strand.cable_site_id, strand.cable_id)
        return _cable_assembly_reference(
            cable_by_pair.get(pair),
            fallback_site_id=strand.cable_site_id,
            fallback_cable_id=strand.cable_id,
        )

    site_id = metadata.get('cable_site_id') if isinstance(metadata, dict) else None
    cable_id = metadata.get('cable_id') if isinstance(metadata, dict) else ''
    if site_id and cable_id:
        return _cable_assembly_reference(
            cable_by_pair.get((site_id, cable_id)),
            fallback_site_id=site_id,
            fallback_cable_id=cable_id,
        )
    return None


def _normalize_path_step(step, *, strand_by_id=None, cable_by_pair=None):
    kind = (
        getattr(step, 'step_type', None)
        or getattr(step, 'kind', None)
        or getattr(step, 'step_kind', None)
        or 'step'
    )
    object_type = getattr(step, 'object_type', None)
    object_id = getattr(step, 'object_id', None)
    label = getattr(step, 'label', None) or getattr(step, 'display', None)
    metadata = getattr(step, 'metadata', None) or {}
    endpoint_context = getattr(step, 'endpoint_context', None)
    if endpoint_context is None and object_type:
        endpoint_context = object_type
    cable_assembly = _path_step_cable_reference(
        object_type=object_type,
        object_id=object_id,
        metadata=metadata,
        strand_by_id=strand_by_id or {},
        cable_by_pair=cable_by_pair or {},
    )
    return SimpleNamespace(
        step_type=kind,
        kind=kind,
        step_kind=kind,
        object_type=object_type,
        object_id=object_id,
        label=label,
        display=label,
        metadata=metadata,
        endpoint_context=endpoint_context,
        cable_assembly=cable_assembly,
        edge_type=getattr(step, 'edge_type', None),
        object=getattr(step, 'object', None),
        url=getattr(step, 'url', None),
    )


def _normalize_resolved_path_payload(path_payload):
    if path_payload is None:
        return None
    raw_steps = tuple(getattr(path_payload, 'steps', ()) or ())
    strand_ids = set()
    for step in raw_steps:
        if getattr(step, 'object_type', None) != 'fiber_strand':
            continue
        object_id = getattr(step, 'object_id', None)
        if object_id in (None, ''):
            continue
        try:
            strand_ids.add(int(object_id))
        except (TypeError, ValueError):
            continue
    strand_by_id = {
        strand.pk: strand
        for strand in FiberStrand.objects.filter(pk__in=strand_ids)
    } if strand_ids else {}
    cable_pairs = {
        (strand.cable_site_id, strand.cable_id)
        for strand in strand_by_id.values()
        if strand.cable_site_id and strand.cable_id
    }
    cable_by_pair = _cable_assembly_map_for_pairs(cable_pairs)
    steps = tuple(
        _normalize_path_step(
            step,
            strand_by_id=strand_by_id,
            cable_by_pair=cable_by_pair,
        )
        for step in raw_steps
    )
    return SimpleNamespace(
        path_found=bool(getattr(path_payload, 'path_found', False)),
        source_lane_id=getattr(path_payload, 'source_lane_id', None),
        destination_lane_id=getattr(path_payload, 'destination_lane_id', None),
        steps=steps,
        path=steps,
        error=getattr(path_payload, 'error', ''),
    )


class PathQueryView(TemplateView):
    template_name = 'netbox_plant_graph/path_query.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics = Fabric.objects.order_by('name', 'pk')
        fabric_id = self.request.GET.get('fabric')
        selected_fabric = fabrics.filter(pk=fabric_id).first() if fabric_id else None

        source_lanes = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'local_mpo_endpoint',
            'local_mpo_position',
            'plane',
        ).filter(direction='send').order_by('fabric__name', 'endpoint__address', 'lane_index')
        destination_lanes = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'local_mpo_endpoint',
            'local_mpo_position',
            'plane',
        ).filter(direction='receive').order_by('fabric__name', 'endpoint__address', 'lane_index')
        if selected_fabric is not None:
            source_lanes = source_lanes.filter(fabric=selected_fabric)
            destination_lanes = destination_lanes.filter(fabric=selected_fabric)

        selected_source = None
        selected_destination = None
        resolved_path = None
        source_id = self.request.GET.get('source_lane')
        destination_id = self.request.GET.get('destination_lane')

        if source_id:
            selected_source = source_lanes.filter(pk=source_id).first()
            if selected_source is None:
                messages.error(self.request, 'Selected source lane was not found.')
            else:
                destination_queryset = destination_lanes.filter(fabric=selected_source.fabric)
                if destination_id:
                    selected_destination = destination_queryset.filter(pk=destination_id).first()
                    if selected_destination is None:
                        messages.error(self.request, 'Selected destination lane was not found in the source fabric.')
                resolved_path = resolve_optical_lane_path(
                    source=selected_source,
                    destination=selected_destination,
                    actor=self.request.user,
                )
                resolved_path = _normalize_resolved_path_payload(resolved_path)
                destination_lanes = destination_queryset

        context.update({
            'fabrics': fabrics,
            'selected_fabric': selected_fabric,
            'source_lanes': source_lanes,
            'destination_lanes': destination_lanes,
            'selected_source': selected_source,
            'selected_destination': selected_destination,
            'source_lane': selected_source,
            'destination_lane': selected_destination,
            'resolved_path': resolved_path,
        })
        return context


def _trace_step_style(step_type: str) -> tuple[str, str]:
    styles = {
        'source_lane': ('Source lane', 'text-bg-primary'),
        'source_position': ('Ingress MPO position', 'text-bg-light'),
        'connector_position': ('Connector position', 'text-bg-light'),
        'fiber_strand': ('Fiber strand', 'text-bg-info'),
        'transfer_map': ('Shuffle/stagger map', 'text-bg-warning'),
        'destination_position': ('Egress MPO position', 'text-bg-light'),
        'destination_lane': ('Destination lane', 'text-bg-success'),
    }
    label, css = styles.get(step_type, (step_type.replace('_', ' ').title(), 'text-bg-secondary'))
    return label, css


def _remote_device_name_for_lane(destination_lane: OpticalLane | None) -> str:
    if destination_lane is None:
        return ''
    channel_subinterface = getattr(destination_lane.channel, 'source_subinterface', None)
    if channel_subinterface is not None and getattr(channel_subinterface, 'device', None):
        if channel_subinterface.device.name:
            return channel_subinterface.device.name
    endpoint = destination_lane.endpoint
    source = endpoint.source
    if isinstance(source, Interface) and getattr(source, 'device', None):
        if source.device.name:
            return source.device.name
    source_device = getattr(source, 'device', None)
    if source_device is not None and getattr(source_device, 'name', None):
        return source_device.name
    node_source = endpoint.node.source
    if isinstance(node_source, Interface) and getattr(node_source, 'device', None):
        if node_source.device.name:
            return node_source.device.name
    if isinstance(node_source, Device):
        return node_source.name
    if endpoint.node.name:
        return endpoint.node.name
    return ''


def _remote_interface_name_for_lane(destination_lane: OpticalLane | None) -> str:
    if destination_lane is None:
        return ''
    channel_subinterface = getattr(destination_lane.channel, 'source_subinterface', None)
    if channel_subinterface is not None and getattr(channel_subinterface, 'name', None):
        return channel_subinterface.name
    endpoint_source = destination_lane.endpoint.source
    if isinstance(endpoint_source, Interface):
        return endpoint_source.name
    node_source = destination_lane.endpoint.node.source
    if isinstance(node_source, Interface):
        return node_source.name
    if getattr(destination_lane.endpoint, 'name', None):
        return destination_lane.endpoint.name
    return ''


def _remote_attachment_label(destination_lane: OpticalLane | None) -> str:
    remote_device_name = _remote_device_name_for_lane(destination_lane)
    remote_interface_name = _remote_interface_name_for_lane(destination_lane)
    if remote_device_name and remote_interface_name:
        return f'{remote_device_name}-{remote_interface_name}'
    if remote_interface_name:
        return remote_interface_name
    if remote_device_name:
        return remote_device_name
    return 'Unresolved destination'


def _subinterface_name_for_lane(lane: OpticalLane | None) -> str:
    if lane is None or lane.channel is None:
        return ''
    source_subinterface = getattr(lane.channel, 'source_subinterface', None)
    if isinstance(source_subinterface, Interface):
        return source_subinterface.name
    if lane.channel.channel_index:
        return f'channel-{lane.channel.channel_index}'
    return ''


def _object_absolute_url(obj) -> str:
    if obj is None or not hasattr(obj, 'get_absolute_url'):
        return ''
    try:
        return obj.get_absolute_url()
    except Exception:
        return ''


def _subinterface_for_lane(lane: OpticalLane | None) -> Interface | None:
    if lane is None or lane.channel is None:
        return None
    source_subinterface = getattr(lane.channel, 'source_subinterface', None)
    return source_subinterface if isinstance(source_subinterface, Interface) else None


def _remote_interface_for_lane(destination_lane: OpticalLane | None) -> Interface | None:
    if destination_lane is None:
        return None
    channel_subinterface = getattr(destination_lane.channel, 'source_subinterface', None)
    if isinstance(channel_subinterface, Interface):
        return channel_subinterface
    endpoint_source = destination_lane.endpoint.source
    if isinstance(endpoint_source, Interface):
        return endpoint_source
    node_source = destination_lane.endpoint.node.source
    if isinstance(node_source, Interface):
        return node_source
    return None


def _position_number_from_step_label(label: str | None) -> int | None:
    if not label:
        return None
    tail = str(label).rsplit(':', 1)[-1].strip()
    try:
        position = int(tail)
    except (TypeError, ValueError):
        return None
    if position < 1:
        return None
    return position


def _schematic_cable_assembly_payload(cable_assembly) -> dict | None:
    if cable_assembly is None:
        return None
    cable_id = getattr(cable_assembly, 'cable_id', '') or ''
    site_id = getattr(cable_assembly, 'site_id', None)
    display = str(getattr(cable_assembly, 'display', '') or cable_id)
    label = cable_id or display
    key = f'{site_id}:{cable_id}' if site_id and cable_id else display
    url = getattr(cable_assembly, 'url', None) or _object_absolute_url(cable_assembly)
    return {
        'key': key,
        'label': label,
        'display': display,
        'site': str(getattr(cable_assembly, 'site', '') or ''),
        'site_id': site_id,
        'cable_id': cable_id,
        'url': url,
    }


def _extract_schematic_path(*, source_lane: OpticalLane, destination_lane: OpticalLane | None, resolved_path) -> dict:
    steps = tuple(getattr(resolved_path, 'steps', ()) or ())
    connector_position_ids = {
        getattr(step, 'object_id', None)
        for step in steps
        if getattr(step, 'object_type', '') == 'connector_position' and getattr(step, 'object_id', None)
    }
    connector_positions_by_id = {
        position.pk: position
        for position in ConnectorPosition.objects.filter(pk__in=connector_position_ids).select_related('endpoint')
    }
    source_position = None
    destination_position = None
    middle_positions = []
    connector_hops = []
    pending_cable_spans = []
    cable_spans = []
    first_fiber_label = ''
    second_fiber_label = ''
    transfer_map_label = ''

    for step in steps:
        if getattr(step, 'object_type', '') == 'fiber_strand':
            label = str(getattr(step, 'label', '') or '')
            if label:
                if not first_fiber_label:
                    first_fiber_label = label
                elif not second_fiber_label and label != first_fiber_label:
                    second_fiber_label = label
            cable_assembly = _schematic_cable_assembly_payload(getattr(step, 'cable_assembly', None))
            if cable_assembly and connector_hops:
                pending_cable_spans.append(
                    {
                        'from_hop_index': len(connector_hops) - 1,
                        'cable_assembly': cable_assembly,
                        'strand_label': label,
                    }
                )
        elif getattr(step, 'object_type', '') == 'transfer_map':
            transfer_map_label = str(getattr(step, 'label', '') or '')

        if getattr(step, 'object_type', '') != 'connector_position':
            continue
        position = _position_number_from_step_label(getattr(step, 'label', None))
        if position is None:
            continue
        position_id = getattr(step, 'object_id', None)
        position_obj = connector_positions_by_id.get(position_id)
        endpoint_obj = position_obj.endpoint if position_obj is not None else None
        endpoint_id = getattr(endpoint_obj, 'pk', None) or (getattr(step, 'metadata', {}) or {}).get('endpoint_id')
        step_type = getattr(step, 'step_type', '')
        if step_type == 'source_position':
            source_position = position
        elif step_type == 'destination_position':
            destination_position = position
        elif step_type == 'connector_position':
            middle_positions.append(position)
        endpoint_label = ''
        raw_label = getattr(step, 'label', None)
        if raw_label:
            endpoint_label = str(raw_label).rsplit(':', 1)[0]
        if connector_hops:
            previous = connector_hops[-1]
            if previous.get('endpoint_label') == endpoint_label and previous.get('position') == position:
                continue
        connector_hops.append(
            {
                'endpoint_id': endpoint_id,
                'endpoint_label': endpoint_label,
                'endpoint_url': _object_absolute_url(endpoint_obj),
                'position_id': position_id,
                'position_url': _object_absolute_url(position_obj),
                'position': position,
                'step_type': step_type,
            }
        )
        if pending_cable_spans:
            current_hop_index = len(connector_hops) - 1
            unresolved_spans = []
            for pending_span in pending_cable_spans:
                if current_hop_index <= pending_span['from_hop_index']:
                    unresolved_spans.append(pending_span)
                    continue
                cable_spans.append(
                    {
                        'from_hop_index': pending_span['from_hop_index'],
                        'to_hop_index': current_hop_index,
                        'cable_assembly': pending_span['cable_assembly'],
                        'strand_label': pending_span['strand_label'],
                    }
                )
            pending_cable_spans = unresolved_spans

    shuffle_front_position = middle_positions[0] if middle_positions else source_position
    shuffle_rear_position = middle_positions[-1] if middle_positions else destination_position
    source_subinterface = _subinterface_for_lane(source_lane)
    destination_subinterface = _subinterface_for_lane(destination_lane)
    remote_interface = _remote_interface_for_lane(destination_lane)

    return {
        'lane_index': source_lane.lane_index,
        'source_lane_id': source_lane.pk,
        'source_lane_url': _object_absolute_url(source_lane),
        'destination_lane_id': getattr(destination_lane, 'pk', None),
        'destination_lane_url': _object_absolute_url(destination_lane),
        'wavelength_nm': str(source_lane.wavelength_nm),
        'path_found': bool(getattr(resolved_path, 'path_found', False)),
        'source_position': source_position,
        'shuffle_front_position': shuffle_front_position,
        'shuffle_rear_position': shuffle_rear_position,
        'destination_position': destination_position,
        'connector_hops': tuple(connector_hops),
        'cable_spans': tuple(cable_spans),
        'first_fiber_label': first_fiber_label,
        'second_fiber_label': second_fiber_label,
        'transfer_map_label': transfer_map_label,
        'shuffle_is_identity': (
            shuffle_front_position is not None
            and shuffle_rear_position is not None
            and shuffle_front_position == shuffle_rear_position
        ),
        'remote_attachment_label': _remote_attachment_label(destination_lane),
        'source_subinterface_label': _subinterface_name_for_lane(source_lane),
        'source_subinterface_url': _object_absolute_url(source_subinterface),
        'destination_subinterface_label': _subinterface_name_for_lane(destination_lane),
        'destination_subinterface_url': _object_absolute_url(destination_subinterface),
        'destination_interface_layer_label': _remote_attachment_label(destination_lane),
        'destination_interface_layer_url': _object_absolute_url(
            remote_interface or destination_subinterface or getattr(destination_lane, 'endpoint', None)
        ),
    }


def _schematic_connector_entry(endpoint: Endpoint) -> dict:
    return {
        'endpoint_id': endpoint.pk,
        'endpoint_label': endpoint.address,
        'endpoint_url': endpoint.get_absolute_url(),
        'parent_endpoint_id': endpoint.parent_id,
        'parent_endpoint_url': _object_absolute_url(endpoint.parent),
        'position_count': endpoint.position_count or 12,
        'positions': tuple(
            {
                'position': position.position_number,
                'position_id': position.pk,
                'url': position.get_absolute_url(),
            }
            for position in endpoint.positions.all()
        ),
    }


def _connector_address_family_prefix(endpoint: Endpoint) -> str | None:
    prefix, separator, suffix = endpoint.address.rpartition('-')
    if not separator or not suffix.isdigit():
        return None
    return f'{prefix}-'


def _expanded_schematic_stage_connectors(paths: tuple[dict, ...]) -> tuple[dict, ...]:
    touched_by_stage = OrderedDict()
    for path in paths:
        for stage_index, hop in enumerate(path.get('connector_hops') or ()):
            endpoint_id = hop.get('endpoint_id')
            if endpoint_id:
                touched_by_stage.setdefault(stage_index, set()).add(endpoint_id)

    if not touched_by_stage:
        return ()

    endpoints = {
        endpoint.pk: endpoint
        for endpoint in Endpoint.objects.filter(
            pk__in={
                endpoint_id
                for endpoint_ids in touched_by_stage.values()
                for endpoint_id in endpoint_ids
            }
        ).select_related('parent', 'node')
    }
    expanded_endpoint_ids_by_stage = OrderedDict()
    for stage_index, endpoint_ids in touched_by_stage.items():
        expanded_ids = set(endpoint_ids)
        touched_endpoints = [endpoints[endpoint_id] for endpoint_id in endpoint_ids if endpoint_id in endpoints]

        parent_ids = {
            endpoint.parent_id
            for endpoint in touched_endpoints
            if endpoint.parent_id is not None
        }
        if parent_ids:
            expanded_ids.update(
                Endpoint.objects.filter(parent_id__in=parent_ids)
                .order_by('address')
                .values_list('pk', flat=True)
            )

        family_prefixes = {
            (endpoint.node_id, prefix)
            for endpoint in touched_endpoints
            if (prefix := _connector_address_family_prefix(endpoint)) is not None
        }
        for node_id, prefix in family_prefixes:
            expanded_ids.update(
                Endpoint.objects.filter(node_id=node_id, address__startswith=prefix)
                .order_by('address')
                .values_list('pk', flat=True)
            )

        for endpoint in touched_endpoints:
            role_slug = (endpoint.metadata or {}).get('role_slug')
            if not role_slug or not role_slug.startswith('shuffle_'):
                continue
            expanded_ids.update(
                sibling.pk
                for sibling in Endpoint.objects.filter(node_id=endpoint.node_id).order_by('address')
                if (sibling.metadata or {}).get('role_slug') == role_slug
            )

        expanded_endpoint_ids_by_stage[stage_index] = expanded_ids

    all_expanded_ids = {
        endpoint_id
        for endpoint_ids in expanded_endpoint_ids_by_stage.values()
        for endpoint_id in endpoint_ids
    }
    expanded_endpoints = {
        endpoint.pk: endpoint
        for endpoint in Endpoint.objects.filter(pk__in=all_expanded_ids)
        .select_related('parent')
        .prefetch_related('positions')
        .order_by('address')
    }
    return tuple(
        {
            'stage_index': stage_index,
            'connectors': tuple(
                _schematic_connector_entry(expanded_endpoints[endpoint_id])
                for endpoint_id in sorted(
                    endpoint_ids,
                    key=lambda candidate_id: expanded_endpoints[candidate_id].address,
                )
                if endpoint_id in expanded_endpoints
            ),
        }
        for stage_index, endpoint_ids in expanded_endpoint_ids_by_stage.items()
    )


class InterfaceFanoutTraceView(TemplateView):
    template_name = 'netbox_plant_graph/interface_fanout_trace.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        devices = Device.objects.order_by('name', 'pk')
        selected_device = None
        selected_interface = None
        interfaces = Interface.objects.none()
        trace_rows = ()
        subinterface_summaries = ()
        fanout_pairs = ()
        source_endpoints = ()
        destination_endpoints = ()
        unresolved_count = 0
        resolved_trace_count = 0
        trace_path_count = 0
        aggregate_schematic_paths = ()
        aggregate_has_shuffle_crossover = False
        aggregate_schematic_stage_connectors = ()

        selected_device_id = self.request.GET.get('device')
        selected_interface_id = self.request.GET.get('interface')
        trace_requested = (self.request.GET.get('trace') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        trace_mode = (self.request.GET.get('trace_mode') or '').strip().lower()
        consolidate_flag = (self.request.GET.get('consolidate') or '').strip().lower() in {
            '1',
            'true',
            'yes',
            'on',
        }
        consolidate_requested = trace_mode == 'consolidated' or consolidate_flag

        if selected_device_id:
            selected_device = devices.filter(pk=selected_device_id).first()
            if selected_device is None:
                messages.error(self.request, 'Selected device was not found.')

        if selected_device is not None:
            interfaces = Interface.objects.filter(
                device=selected_device,
                parent__isnull=True,
            ).order_by('name', 'pk')

        if selected_interface_id:
            interface_qs = Interface.objects.select_related('device').filter(pk=selected_interface_id)
            if selected_device is not None:
                interface_qs = interface_qs.filter(device=selected_device)
            selected_interface = interface_qs.first()
            if selected_interface is None:
                messages.error(self.request, 'Selected interface was not found on the selected device.')
            elif selected_device is None:
                selected_device = selected_interface.device
                interfaces = Interface.objects.filter(
                    device=selected_device,
                    parent__isnull=True,
                ).order_by('name', 'pk')

        if selected_interface is not None:
            source_endpoints = tuple(
                _endpoints_for_source_object(selected_interface)
                .select_related('fabric', 'node')
                .order_by('address', 'pk')
            )
            subinterfaces = tuple(
                Interface.objects.filter(parent=selected_interface).order_by('name', 'pk')
            )
            lanes = list(
                OpticalLane.objects.filter(
                    endpoint__in=source_endpoints,
                    direction='send',
                )
                .select_related(
                    'fabric',
                    'endpoint',
                    'endpoint__node',
                    'plane',
                    'local_mpo_endpoint',
                    'local_mpo_position',
                    'channel',
                    'channel__source_subinterface',
                )
                .order_by(
                    'channel__channel_index',
                    'lane_index',
                    'wavelength_nm',
                    'pk',
                )
            )

            if trace_requested:
                if not lanes:
                    messages.info(
                        self.request,
                        'No source optical lanes are materialized for the selected physical interface.',
                    )
                else:
                    resolved_paths = []
                    destination_lane_ids = set()
                    for source_lane in lanes:
                        resolved = _normalize_resolved_path_payload(
                            resolve_optical_lane_path(
                                source=source_lane,
                                destination=None,
                                actor=self.request.user,
                            )
                        )
                        resolved_paths.append((source_lane, resolved))
                        if resolved is not None and resolved.destination_lane_id:
                            destination_lane_ids.add(resolved.destination_lane_id)

                    destination_lane_lookup = {
                        lane.pk: lane
                        for lane in OpticalLane.objects.filter(pk__in=destination_lane_ids)
                        .select_related(
                            'endpoint',
                            'endpoint__node',
                            'channel',
                            'channel__source_subinterface',
                            'local_mpo_endpoint',
                            'local_mpo_position',
                        )
                    }

                    row_payloads = []
                    destination_endpoint_by_id = {}
                    for source_lane, resolved in resolved_paths:
                        destination_lane = None
                        if resolved is not None and resolved.destination_lane_id:
                            destination_lane = destination_lane_lookup.get(resolved.destination_lane_id)
                            if destination_lane is not None:
                                destination_endpoint_by_id[destination_lane.endpoint_id] = destination_lane.endpoint
                        if resolved is None or not resolved.path_found:
                            unresolved_count += 1

                        raw_steps = tuple(getattr(resolved, 'steps', ()) or ())
                        visual_steps = []
                        for step in raw_steps:
                            step_label, step_css = _trace_step_style(step.step_type)
                            visual_steps.append(
                                {
                                    'label': step_label,
                                    'css': step_css,
                                    'step': step,
                                }
                            )

                        schematic_path = _extract_schematic_path(
                            source_lane=source_lane,
                            destination_lane=destination_lane,
                            resolved_path=resolved,
                        )
                        row_payloads.append(
                            {
                                'source_lane': source_lane,
                                'source_subinterface': getattr(source_lane.channel, 'source_subinterface', None),
                                'destination_lane': destination_lane,
                                'remote_device_name': _remote_device_name_for_lane(destination_lane),
                                'remote_interface_name': _remote_interface_name_for_lane(destination_lane),
                                'remote_attachment_label': _remote_attachment_label(destination_lane),
                                'destination_subinterface': (
                                    getattr(destination_lane.channel, 'source_subinterface', None)
                                    if destination_lane is not None else None
                                ),
                                'resolved_path': resolved,
                                'visual_steps': tuple(visual_steps),
                                'path_found': bool(getattr(resolved, 'path_found', False)),
                                'schematic_paths': tuple((schematic_path,)),
                                'has_shuffle_crossover': not schematic_path.get('shuffle_is_identity', True),
                            }
                        )

                    grouped = OrderedDict()
                    for row in row_payloads:
                        subif = row['source_subinterface']
                        if subif is None:
                            group_key = 'unbound'
                            group_label = 'Unbound source sub-interface'
                        else:
                            group_key = f'subif-{subif.pk}'
                            group_label = subif.name
                        group = grouped.setdefault(
                            group_key,
                            {
                                'label': group_label,
                                'subinterface': subif,
                                'rows': [],
                            }
                        )
                        group['rows'].append(row)

                    raw_trace_rows = tuple(row_payloads)
                    trace_rows = raw_trace_rows
                    trace_path_count = len(raw_trace_rows)
                    resolved_path_count = len(raw_trace_rows) - unresolved_count
                    if consolidate_requested:
                        grouped_trace_rows = OrderedDict()
                        for row in trace_rows:
                            source_subinterface = row.get('source_subinterface')
                            if source_subinterface is None:
                                group_key = 'unbound'
                                group_label = 'Unbound source sub-interface'
                            else:
                                group_key = f'subif-{source_subinterface.pk}'
                                group_label = source_subinterface.name
                            group = grouped_trace_rows.setdefault(
                                group_key,
                                {
                                    'source_subinterface': source_subinterface,
                                    'source_label': group_label,
                                    'rows': [],
                                },
                            )
                            group['rows'].append(row)

                        consolidated_rows = []
                        for group in grouped_trace_rows.values():
                            member_rows = tuple(group['rows'])
                            representative = member_rows[0]
                            destination_subinterface_names = tuple(
                                sorted(
                                    {
                                        row['destination_subinterface'].name
                                        for row in member_rows
                                        if row.get('destination_subinterface') is not None
                                    }
                                )
                            )
                            remote_device_names = tuple(
                                sorted(
                                    {
                                        row['remote_device_name']
                                        for row in member_rows
                                        if row.get('remote_device_name')
                                    }
                                )
                            )
                            consolidated_rows.append(
                                {
                                    **representative,
                                    'is_consolidated': True,
                                    'member_rows': member_rows,
                                    'path_total': len(member_rows),
                                    'resolved_total': sum(1 for row in member_rows if row.get('path_found')),
                                    'destination_subinterface_names': destination_subinterface_names,
                                    'remote_device_names': remote_device_names,
                                    'schematic_paths': tuple(
                                        row['schematic_paths'][0]
                                        for row in member_rows
                                        if row.get('schematic_paths')
                                    ),
                                    'has_shuffle_crossover': any(
                                        not row['schematic_paths'][0].get('shuffle_is_identity', True)
                                        for row in member_rows
                                        if row.get('schematic_paths')
                                    ),
                                }
                            )
                        trace_rows = tuple(consolidated_rows)

                    if consolidate_requested:
                        aggregate_schematic_paths = tuple(
                            {
                                **row['schematic_paths'][0],
                                'bundle_label': (
                                    row['source_subinterface'].name
                                    if row.get('source_subinterface') is not None
                                    else 'Unbound source'
                                ),
                                'bundle_size': row.get('path_total') or len(row.get('member_rows') or ()),
                            }
                            for row in trace_rows
                            if row.get('schematic_paths')
                        )
                    else:
                        aggregate_schematic_paths = tuple(
                            row['schematic_paths'][0]
                            for row in trace_rows
                            if row.get('schematic_paths')
                        )
                    aggregate_has_shuffle_crossover = any(
                        not path.get('shuffle_is_identity', True)
                        for path in aggregate_schematic_paths
                    )
                    aggregate_schematic_stage_connectors = _expanded_schematic_stage_connectors(
                        aggregate_schematic_paths
                    )

                    trace_rows = tuple(
                        {
                            **row,
                            'schematic_paths_json': json.dumps(
                                list(row.get('schematic_paths') or ()),
                                sort_keys=True,
                            ),
                        }
                        for row in trace_rows
                    )

                    resolved_trace_count = resolved_path_count
                    subinterface_summaries = tuple(
                        {
                            'label': group['label'],
                            'subinterface': group['subinterface'],
                            'trace_count': len(group['rows']),
                            'resolved_count': sum(1 for row in group['rows'] if row['path_found']),
                        }
                        for group in grouped.values()
                    )
                    destination_endpoints = tuple(
                        endpoint
                        for _, endpoint in sorted(
                            destination_endpoint_by_id.items(),
                            key=lambda pair: pair[1].address,
                        )
                    )
                    pair_counts = {}
                    for row in raw_trace_rows:
                        source_subinterface = row.get('source_subinterface')
                        destination_lane = row.get('destination_lane')
                        source_label = source_subinterface.name if source_subinterface is not None else 'Unbound source'
                        destination_label = _remote_attachment_label(destination_lane)
                        key = (source_label, destination_label)
                        entry = pair_counts.setdefault(key, {'count': 0})
                        entry['count'] += 1
                    fanout_pairs = tuple(
                        {
                            'source_label': source_label,
                            'destination_label': destination_label,
                            'path_count': payload['count'],
                        }
                        for (source_label, destination_label), payload in sorted(
                            pair_counts.items(),
                            key=lambda item: (item[0][0], item[0][1]),
                        )
                    )

        context.update(
            {
                'page_title': 'Interface Fanout Trace',
                'devices': devices,
                'interfaces': interfaces,
                'selected_device': selected_device,
                'selected_interface': selected_interface,
                'trace_requested': trace_requested,
                'trace_mode': 'consolidated' if consolidate_requested else 'expanded',
                'consolidate_requested': consolidate_requested,
                'source_endpoints': source_endpoints,
                'subinterface_summaries': subinterface_summaries,
                'fanout_pairs': fanout_pairs,
                'trace_rows': trace_rows,
                'destination_endpoints': destination_endpoints,
                'unresolved_count': unresolved_count,
                'resolved_trace_count': resolved_trace_count,
                'trace_path_count': trace_path_count,
                'aggregate_schematic_paths_json': json.dumps(list(aggregate_schematic_paths), sort_keys=True),
                'aggregate_schematic_stage_connectors_json': json.dumps(
                    list(aggregate_schematic_stage_connectors),
                    sort_keys=True,
                ),
                'aggregate_has_shuffle_crossover': aggregate_has_shuffle_crossover,
            }
        )
        return context


FINDING_STATUS_DEFAULT = 'open'
FINDING_STATUS_ACTIVE = {'open', 'acknowledged', 'in_progress', 'suppressed'}


def _parse_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_next_url(request, fallback_url):
    next_url = (request.POST.get('next') or '').strip()
    if next_url.startswith('/') and not next_url.startswith('//'):
        return next_url
    return fallback_url


def _selected_fabric_from_request(request, *, param_name='fabric_id', default_first=False):
    fabrics = Fabric.objects.order_by('name', 'pk')
    selected = None
    raw = request.GET.get(param_name)
    if raw:
        selected = fabrics.filter(pk=raw).first()
    elif default_first:
        selected = fabrics.first()
    return fabrics, selected


def _selected_plane_from_request(request):
    plane_id = request.GET.get('plane_id')
    if not plane_id:
        return None
    return Plane.objects.select_related('fabric').filter(pk=plane_id).first()


_LANE_ANALYSIS_REGISTRY_CHOICES = (
    {'value': 'opticallane', 'label': 'Optical Lane'},
    {'value': 'endpoint', 'label': 'Endpoint'},
    {'value': 'plane', 'label': 'Plane'},
    {'value': 'fabric', 'label': 'Fabric'},
    {'value': 'interface', 'label': 'Interface'},
    {'value': 'frontport', 'label': 'Front Port'},
    {'value': 'rearport', 'label': 'Rear Port'},
)

_BLAST_RADIUS_RESOLUTION_CHOICES = (
    {'value': 'attachment_unit', 'label': 'Endpoint Scope'},
    {'value': 'signal_lane', 'label': 'Optical Lane Scope'},
)

_BLAST_FAILURE_MODE_CHOICES = (
    {'value': 'connector_unplug', 'label': 'Connector Unplugged'},
    {'value': 'cable_cut', 'label': 'Cable Assembly Cut'},
)


def _path_with_query(route_name, params):
    filtered = {
        key: value
        for key, value in params.items()
        if value not in (None, '')
    }
    url = reverse(f'plugins:netbox_plant_graph:{route_name}')
    if not filtered:
        return url
    return f'{url}?{urlencode(filtered)}'


def _lane_analysis_registry_key(obj):
    if isinstance(obj, OpticalLane):
        return 'opticallane'
    if isinstance(obj, Endpoint):
        return 'endpoint'
    if isinstance(obj, Plane):
        return 'plane'
    if isinstance(obj, Fabric):
        return 'fabric'
    if isinstance(obj, Interface):
        return 'interface'
    if isinstance(obj, FrontPort):
        return 'frontport'
    if isinstance(obj, RearPort):
        return 'rearport'
    return ''


def _blast_failure_mode_label(mode):
    for choice in _BLAST_FAILURE_MODE_CHOICES:
        if choice['value'] == mode:
            return choice['label']
    return 'Failure Scenario'


def _lane_analysis_target_fabric(target):
    if isinstance(target, Fabric):
        return target
    fabric = getattr(target, 'fabric', None)
    if isinstance(fabric, Fabric):
        return fabric
    fabric_id = getattr(target, 'fabric_id', None)
    if fabric_id:
        return Fabric.objects.filter(pk=fabric_id).first()
    if isinstance(target, (Interface, FrontPort, RearPort)):
        endpoint = _endpoints_for_source_object(target).first()
        if endpoint is not None:
            return endpoint.fabric
    return None


def _endpoints_for_source_object(source_object):
    if source_object is None or getattr(source_object, 'pk', None) is None:
        return Endpoint.objects.none()
    source_ct = ContentType.objects.get_for_model(source_object, for_concrete_model=False)
    return Endpoint.objects.filter(source_type=source_ct, source_id=source_object.pk).order_by('pk')


def _resolve_lane_analysis_target(registry_key, object_id):
    object_pk = _parse_int(object_id)
    if object_pk is None:
        return None
    normalized_key = (registry_key or '').strip().lower()
    if normalized_key in {'opticallane', 'optical_lane', 'signal_lane', 'signallane'}:
        return OpticalLane.objects.select_related('fabric', 'endpoint', 'plane').filter(pk=object_pk).first()
    if normalized_key == 'endpoint':
        return Endpoint.objects.select_related('fabric', 'node').filter(pk=object_pk).first()
    if normalized_key in {'plane', 'fabricplane'}:
        return Plane.objects.select_related('fabric').filter(pk=object_pk).first()
    if normalized_key == 'fabric':
        return Fabric.objects.filter(pk=object_pk).first()
    if normalized_key == 'interface':
        return Interface.objects.filter(pk=object_pk).first()
    if normalized_key == 'frontport':
        return FrontPort.objects.filter(pk=object_pk).first()
    if normalized_key == 'rearport':
        return RearPort.objects.filter(pk=object_pk).first()
    return None


def _lanes_for_target(target):
    if target is None:
        return []
    queryset = OpticalLane.objects.select_related(
        'fabric',
        'endpoint',
        'endpoint__node',
        'plane',
        'local_mpo_endpoint',
        'local_mpo_position',
    ).order_by('endpoint__address', 'lane_index', 'direction', 'pk')
    if isinstance(target, OpticalLane):
        return list(queryset.filter(pk=target.pk))
    if isinstance(target, Endpoint):
        return list(queryset.filter(endpoint=target))
    if isinstance(target, Plane):
        return list(queryset.filter(plane=target))
    if isinstance(target, Fabric):
        return list(queryset.filter(fabric=target))
    if isinstance(target, (Interface, FrontPort, RearPort)):
        endpoints = _endpoints_for_source_object(target)
        if not endpoints.exists():
            return []
        return list(queryset.filter(endpoint__in=endpoints))

    endpoint = getattr(target, 'endpoint', None)
    if isinstance(endpoint, Endpoint):
        return list(queryset.filter(endpoint=endpoint))
    endpoint_id = getattr(target, 'endpoint_id', None)
    if endpoint_id:
        return list(queryset.filter(endpoint_id=endpoint_id))

    fabric = _lane_analysis_target_fabric(target)
    if fabric is not None:
        return list(queryset.filter(fabric=fabric))
    return []


def _pick_lane_for_target(target, *, preferred_direction='send'):
    lanes = _lanes_for_target(target)
    if not lanes:
        return None
    directed = [lane for lane in lanes if lane.direction == preferred_direction]
    if directed:
        return directed[0]
    return lanes[0]


def _lane_analysis_object_reference(obj):
    registry_key = _lane_analysis_registry_key(obj)
    obj_pk = getattr(obj, 'pk', None)
    model_name = getattr(getattr(obj, '_meta', None), 'model_name', obj.__class__.__name__.lower())
    fabric = _lane_analysis_target_fabric(obj)
    fabric_id = fabric.pk if fabric is not None else None
    lane_workspace_url = _path_with_query(
        'lane_workspace',
        {
            'fabric': fabric_id,
            'plane': getattr(obj, 'plane_id', None),
        },
    )
    signal_path_resolver_url = None
    signal_blast_radius_url = None
    lane_drilldown_url = None
    if registry_key and obj_pk is not None:
        signal_path_resolver_url = _path_with_query(
            'path_resolver',
            {
                'source_registry_key': registry_key,
                'source_id': obj_pk,
                'resolution': 'signal_lane',
            },
        )
        lane_drilldown_url = _path_with_query(
            'lane_drilldown',
            {
                'target_registry_key': registry_key,
                'target_id': obj_pk,
            },
        )
        signal_blast_radius_url = _path_with_query(
            'blast_radius',
            {
                'target_registry_key': registry_key,
                'target_id': obj_pk,
                'resolution': 'signal_lane',
            },
        )
    endpoint_context = ''
    cable_assemblies = ()
    if isinstance(obj, OpticalLane):
        endpoint_context = (
            f'{obj.endpoint.address} / lane {obj.lane_index} / '
            f'{obj.direction} / {obj.wavelength_nm} nm'
        )
        cable_assemblies = _cable_references_by_mpo_position_ids(
            {obj.local_mpo_position_id}
        ).get(obj.local_mpo_position_id, ())
        if obj.local_mpo_endpoint_id:
            signal_blast_radius_url = _path_with_query(
                'blast_radius',
                {
                    'fabric': obj.fabric_id,
                    'failure_mode': 'connector_unplug',
                    'connector_endpoint_id': obj.local_mpo_endpoint_id,
                },
            )
    elif isinstance(obj, Endpoint):
        endpoint_context = obj.address
        if obj.position_count:
            signal_blast_radius_url = _path_with_query(
                'blast_radius',
                {
                    'fabric': obj.fabric_id,
                    'failure_mode': 'connector_unplug',
                    'connector_endpoint_id': obj.pk,
                },
            )
    elif isinstance(obj, Plane):
        endpoint_context = f'{obj.fabric.name} / plane {obj.plane_number}'
    elif isinstance(obj, Fabric):
        endpoint_context = obj.name
    return SimpleNamespace(
        pk=obj_pk,
        display=str(obj),
        url=obj.get_absolute_url() if hasattr(obj, 'get_absolute_url') else None,
        model=model_name,
        registry_key=registry_key,
        endpoint_context=endpoint_context,
        cable_assemblies=cable_assemblies,
        lane_workspace_url=lane_workspace_url,
        signal_path_resolver_url=signal_path_resolver_url,
        signal_blast_radius_url=signal_blast_radius_url,
        lane_drilldown_url=lane_drilldown_url,
    )


def _build_v2_lane_drilldown(*, target, lane_index=None):
    lanes = _lanes_for_target(target)
    lane_indexes = sorted({lane.lane_index for lane in lanes})
    if lane_index is not None:
        lanes = [lane for lane in lanes if lane.lane_index == lane_index]

    groups = {}
    for lane in lanes:
        group = groups.setdefault(
            lane.endpoint_id,
            {
                'attachment_unit': _lane_analysis_object_reference(lane.endpoint),
                'lanes': [],
            },
        )
        group['lanes'].append(_lane_analysis_object_reference(lane))

    endpoint_rows = []
    for group in sorted(groups.values(), key=lambda item: item['attachment_unit'].display):
        endpoint_rows.append(
            {
                'endpoint': group['attachment_unit'],
                'attachment_unit': group['attachment_unit'],
                'lane_count': len(group['lanes']),
                'lanes': tuple(group['lanes']),
            }
        )

    # Backward-compatible alias retained for non-updated helper paths.
    attachment_units = tuple(
        {
            'attachment_unit': row['endpoint'],
            'lane_count': row['lane_count'],
            'lanes': row['lanes'],
        }
        for row in endpoint_rows
    )

    return {
        'target': _lane_analysis_object_reference(target),
        'lane_index': lane_index,
        'endpoints': tuple(endpoint_rows),
        'attachment_units': attachment_units,
        'total_endpoints': len(endpoint_rows),
        'total_attachment_units': len(endpoint_rows),
        'total_signal_lanes': sum(item['lane_count'] for item in endpoint_rows),
        'available_lane_indexes': lane_indexes,
    }


def _normalize_lane_drilldown_payload(payload):
    if payload is None:
        return None

    if isinstance(payload, dict):
        normalized = dict(payload)
        raw_endpoints = normalized.get('endpoints')
        if raw_endpoints is None:
            raw_endpoints = normalized.get('attachment_units') or ()
        endpoint_rows = []
        for row in raw_endpoints:
            endpoint = row.get('endpoint')
            if endpoint is None:
                endpoint = row.get('attachment_unit')
            endpoint_rows.append(
                {
                    **row,
                    'endpoint': endpoint,
                    'attachment_unit': endpoint,
                }
            )
        normalized['endpoints'] = tuple(endpoint_rows)
        normalized.setdefault('total_endpoints', len(endpoint_rows))
        normalized.setdefault('total_attachment_units', len(endpoint_rows))
        return normalized

    return payload


def _normalize_lane_compare_payload(payload):
    if payload is None:
        return None

    if isinstance(payload, dict):
        normalized = dict(payload)
        domain_rows = []
        for row in normalized.get('policy_domain_deltas') or ():
            endpoint_count = row.get('endpoint_count')
            if endpoint_count is None:
                endpoint_count = row.get('attachment_unit_count')
            domain_rows.append(
                {
                    **row,
                    'endpoint_count': endpoint_count,
                }
            )
        normalized['policy_domain_deltas'] = tuple(domain_rows)

        node_rows = []
        for row in normalized.get('node_diffs') or ():
            baseline_endpoint_count = row.get('baseline_endpoint_count')
            if baseline_endpoint_count is None:
                baseline_endpoint_count = row.get('baseline_attachment_units')
            candidate_endpoint_count = row.get('candidate_endpoint_count')
            if candidate_endpoint_count is None:
                candidate_endpoint_count = row.get('candidate_attachment_units')
            node_rows.append(
                {
                    **row,
                    'baseline_endpoint_count': baseline_endpoint_count,
                    'candidate_endpoint_count': candidate_endpoint_count,
                }
            )
        normalized['node_diffs'] = tuple(node_rows)
        return normalized

    return payload


def _status_for_delta(*, baseline_value, candidate_value, higher_is_better):
    if candidate_value == baseline_value:
        return 'unchanged'
    if higher_is_better is None:
        return 'changed'
    if higher_is_better:
        return 'improved' if candidate_value > baseline_value else 'regressed'
    return 'improved' if candidate_value < baseline_value else 'regressed'


def _lane_compare_summary(lanes):
    lane_total = len(lanes)
    mapped_total = sum(1 for lane in lanes if lane.pair_key)
    if lane_total == 0:
        lane_map_consistency = 'empty'
    elif mapped_total == lane_total:
        lane_map_consistency = 'consistent'
    elif mapped_total > 0:
        lane_map_consistency = 'partial'
    else:
        lane_map_consistency = 'missing'
    plane_ids = sorted({lane.plane_id for lane in lanes if lane.plane_id is not None})
    if not plane_ids:
        plane_consistency = 'unassigned'
    elif len(plane_ids) == 1:
        plane_consistency = 'consistent'
    else:
        plane_consistency = 'mixed'
    return SimpleNamespace(
        expected_lane_total=lane_total,
        present_lane_total=lane_total,
        mapped_lane_total=mapped_total,
        lane_map_consistency=lane_map_consistency,
        plane_consistency=plane_consistency,
        plane_ids=tuple(plane_ids),
    )


def _endpoint_lane_groups(lanes):
    groups = {}
    for lane in lanes:
        key = lane.endpoint.address
        group = groups.setdefault(
            key,
            {
                'compare_key': key,
                'attachment': _lane_analysis_object_reference(lane.endpoint),
                'present_lane_count': 0,
                'mapped_lane_count': 0,
                'plane_ids': set(),
                'status': 'consistent',
                'lane_indexes': [],
            },
        )
        group['present_lane_count'] += 1
        if lane.pair_key:
            group['mapped_lane_count'] += 1
        if lane.plane_id is not None:
            group['plane_ids'].add(lane.plane_id)
        group['lane_indexes'].append(lane.lane_index)
        if lane.direction == 'receive' and group['status'] == 'consistent':
            group['status'] = 'receive-present'
    for group in groups.values():
        group['plane_ids'] = tuple(sorted(group['plane_ids']))
        group['lane_indexes'] = tuple(sorted(set(group['lane_indexes'])))
    return groups


def _target_durable_finding_link(target):
    fabric = _lane_analysis_target_fabric(target)
    if fabric is None:
        return None
    return _path_with_query('audit_triage', {'fabric_id': fabric.pk})


def _build_v2_lane_compare(*, baseline_target, candidate_target):
    baseline_lanes = _lanes_for_target(baseline_target)
    candidate_lanes = _lanes_for_target(candidate_target)
    baseline_summary = _lane_compare_summary(baseline_lanes)
    candidate_summary = _lane_compare_summary(candidate_lanes)

    metric_inputs = (
        ('present_lane_total', baseline_summary.present_lane_total, candidate_summary.present_lane_total, True),
        ('mapped_lane_total', baseline_summary.mapped_lane_total, candidate_summary.mapped_lane_total, True),
        (
            'unmapped_lane_total',
            baseline_summary.present_lane_total - baseline_summary.mapped_lane_total,
            candidate_summary.present_lane_total - candidate_summary.mapped_lane_total,
            False,
        ),
        ('plane_count', len(baseline_summary.plane_ids), len(candidate_summary.plane_ids), None),
    )
    metrics = tuple(
        SimpleNamespace(
            name=name,
            baseline_value=baseline_value,
            candidate_value=candidate_value,
            delta=candidate_value - baseline_value,
            status=_status_for_delta(
                baseline_value=baseline_value,
                candidate_value=candidate_value,
                higher_is_better=higher_is_better,
            ),
        )
        for name, baseline_value, candidate_value, higher_is_better in metric_inputs
    )
    regressions = tuple(
        f'{metric.name} regressed ({metric.baseline_value} -> {metric.candidate_value}).'
        for metric in metrics
        if metric.status == 'regressed'
    )

    baseline_groups = _endpoint_lane_groups(baseline_lanes)
    candidate_groups = _endpoint_lane_groups(candidate_lanes)
    all_group_keys = sorted(set(baseline_groups) | set(candidate_groups))
    attachment_diffs = []
    representative_reviews = []
    for compare_key in all_group_keys:
        baseline_group = baseline_groups.get(compare_key)
        candidate_group = candidate_groups.get(compare_key)
        changed_fields = []
        if baseline_group is None or candidate_group is None:
            changed_fields.append('attachment_presence')
        else:
            if baseline_group['present_lane_count'] != candidate_group['present_lane_count']:
                changed_fields.append('present_lane_count')
            if baseline_group['mapped_lane_count'] != candidate_group['mapped_lane_count']:
                changed_fields.append('mapped_lane_count')
            if baseline_group['plane_ids'] != candidate_group['plane_ids']:
                changed_fields.append('plane_ids')
            if baseline_group['status'] != candidate_group['status']:
                changed_fields.append('status')

        attachment_diffs.append(
            SimpleNamespace(
                compare_key=compare_key,
                baseline_attachment=baseline_group['attachment'] if baseline_group is not None else None,
                candidate_attachment=candidate_group['attachment'] if candidate_group is not None else None,
                baseline_status=baseline_group['status'] if baseline_group is not None else None,
                candidate_status=candidate_group['status'] if candidate_group is not None else None,
                baseline_plane_ids=baseline_group['plane_ids'] if baseline_group is not None else (),
                candidate_plane_ids=candidate_group['plane_ids'] if candidate_group is not None else (),
                baseline_present_lane_count=baseline_group['present_lane_count'] if baseline_group is not None else 0,
                candidate_present_lane_count=candidate_group['present_lane_count'] if candidate_group is not None else 0,
                baseline_mapped_lane_count=baseline_group['mapped_lane_count'] if baseline_group is not None else 0,
                candidate_mapped_lane_count=candidate_group['mapped_lane_count'] if candidate_group is not None else 0,
                changed_fields=tuple(changed_fields),
            )
        )

        if changed_fields:
            lane_index = None
            if baseline_group and baseline_group['lane_indexes']:
                lane_index = baseline_group['lane_indexes'][0]
            elif candidate_group and candidate_group['lane_indexes']:
                lane_index = candidate_group['lane_indexes'][0]

            baseline_action = None
            if baseline_group is not None:
                baseline_action = SimpleNamespace(
                    label='Lane Drilldown',
                    url=_path_with_query(
                        'lane_drilldown',
                        {
                            'target_registry_key': 'endpoint',
                            'target_id': baseline_group['attachment'].pk,
                            'lane_index': lane_index,
                        },
                    ),
                )
            candidate_action = None
            if candidate_group is not None:
                candidate_action = SimpleNamespace(
                    label='Lane Drilldown',
                    url=_path_with_query(
                        'lane_drilldown',
                        {
                            'target_registry_key': 'endpoint',
                            'target_id': candidate_group['attachment'].pk,
                            'lane_index': lane_index,
                        },
                    ),
                )
            representative_reviews.append(
                SimpleNamespace(
                    compare_key=compare_key,
                    lane_index=lane_index,
                    baseline_attachment=baseline_group['attachment'] if baseline_group is not None else None,
                    candidate_attachment=candidate_group['attachment'] if candidate_group is not None else None,
                    baseline_action=baseline_action,
                    candidate_action=candidate_action,
                    reason=', '.join(changed_fields),
                )
            )

    baseline_node_totals = {}
    candidate_node_totals = {}
    for lane in baseline_lanes:
        node_key = lane.endpoint.node.address
        baseline_node_totals.setdefault(node_key, {'attachment_units': set(), 'present': 0, 'mapped': 0})
        baseline_node_totals[node_key]['attachment_units'].add(lane.endpoint_id)
        baseline_node_totals[node_key]['present'] += 1
        if lane.pair_key:
            baseline_node_totals[node_key]['mapped'] += 1
    for lane in candidate_lanes:
        node_key = lane.endpoint.node.address
        candidate_node_totals.setdefault(node_key, {'attachment_units': set(), 'present': 0, 'mapped': 0})
        candidate_node_totals[node_key]['attachment_units'].add(lane.endpoint_id)
        candidate_node_totals[node_key]['present'] += 1
        if lane.pair_key:
            candidate_node_totals[node_key]['mapped'] += 1

    node_diffs = []
    for node_key in sorted(set(baseline_node_totals) | set(candidate_node_totals)):
        baseline_entry = baseline_node_totals.get(node_key, {'attachment_units': set(), 'present': 0, 'mapped': 0})
        candidate_entry = candidate_node_totals.get(node_key, {'attachment_units': set(), 'present': 0, 'mapped': 0})
        node_diffs.append(
            SimpleNamespace(
                node_label=node_key,
                baseline_reference=None,
                candidate_reference=None,
                baseline_attachment_units=len(baseline_entry['attachment_units']),
                candidate_attachment_units=len(candidate_entry['attachment_units']),
                baseline_endpoint_count=len(baseline_entry['attachment_units']),
                candidate_endpoint_count=len(candidate_entry['attachment_units']),
                baseline_present_lane_total=baseline_entry['present'],
                candidate_present_lane_total=candidate_entry['present'],
                baseline_mapped_lane_total=baseline_entry['mapped'],
                candidate_mapped_lane_total=candidate_entry['mapped'],
                changed=baseline_entry != candidate_entry,
            )
        )

    plane_ids_added = tuple(sorted(set(candidate_summary.plane_ids) - set(baseline_summary.plane_ids)))
    plane_ids_removed = tuple(sorted(set(baseline_summary.plane_ids) - set(candidate_summary.plane_ids)))

    return SimpleNamespace(
        baseline_target=_lane_analysis_object_reference(baseline_target),
        candidate_target=_lane_analysis_object_reference(candidate_target),
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        metrics=metrics,
        regressions=regressions,
        plane_ids_added=plane_ids_added,
        plane_ids_removed=plane_ids_removed,
        policy_regressions=(),
        policy_domain_deltas=(),
        attachment_diffs=tuple(attachment_diffs),
        node_diffs=tuple(node_diffs),
        representative_reviews=tuple(representative_reviews),
    )


def _build_v2_blast_radius(*, target, resolution='attachment_unit'):
    impacted_rows = []
    seen = set()

    def add(distance, obj):
        if obj is None:
            return
        model = getattr(getattr(obj, '_meta', None), 'model_name', obj.__class__.__name__.lower())
        key = (model, getattr(obj, 'pk', None))
        if key in seen:
            return
        seen.add(key)
        impacted_rows.append(
            SimpleNamespace(
                distance=distance,
                object=_lane_analysis_object_reference(obj),
            )
        )

    add(0, target)
    lanes = _lanes_for_target(target)
    if resolution == 'signal_lane':
        for lane in lanes[:256]:
            add(1, lane)
            if lane.pair_key:
                peer_lanes = OpticalLane.objects.filter(
                    fabric_id=lane.fabric_id,
                    pair_key=lane.pair_key,
                ).exclude(pk=lane.pk)[:64]
                for peer in peer_lanes:
                    add(2, peer)
    else:
        for lane in lanes[:256]:
            add(1, lane.endpoint)
            add(2, lane)

    impacted_rows.sort(key=lambda row: (row.distance, row.object.model, row.object.display))
    return {
        'target': getattr(target, 'pk', target),
        'resolution': resolution,
        'impacted_paths': tuple(impacted_rows),
    }


def _blast_connector_candidates(*, fabric=None):
    queryset = (
        Endpoint.objects.select_related('fabric')
        .annotate(position_total=Count('positions'))
        .filter(position_total__gt=0)
        .order_by('fabric__name', 'address', 'pk')
    )
    if fabric is not None:
        queryset = queryset.filter(fabric=fabric)
    return list(queryset)


def _blast_cable_candidates(*, fabric=None):
    if fabric is None:
        return list(
            CableAssembly.objects.select_related('site').order_by('site__name', 'cable_id', 'pk')
        )

    cable_pairs = set(
        FiberStrand.objects.filter(segment__fabric=fabric)
        .exclude(cable_site_id__isnull=True)
        .exclude(cable_id='')
        .values_list('cable_site_id', 'cable_id')
    )
    cable_by_pair = _cable_assembly_map_for_pairs(cable_pairs)
    return sorted(
        cable_by_pair.values(),
        key=lambda cable: (cable.site.name, cable.cable_id, cable.pk),
    )


def _blast_unavailable_reference(unavailable):
    if isinstance(unavailable, ConnectorPosition):
        return SimpleNamespace(
            pk=unavailable.pk,
            url=unavailable.get_absolute_url(),
            display=str(unavailable),
            model='connectorposition',
            cable_assembly=None,
        )
    if isinstance(unavailable, FiberStrand):
        cable_reference = None
        if unavailable.cable_site_id and unavailable.cable_id:
            cable_reference = _cable_assembly_reference(
                unavailable.cable_assembly,
                fallback_site_id=unavailable.cable_site_id,
                fallback_cable_id=unavailable.cable_id,
            )
        return SimpleNamespace(
            pk=unavailable.pk,
            url=unavailable.get_absolute_url(),
            display=str(unavailable),
            model='fiberstrand',
            cable_assembly=cable_reference,
        )
    return SimpleNamespace(
        pk=getattr(unavailable, 'pk', None),
        url=unavailable.get_absolute_url() if hasattr(unavailable, 'get_absolute_url') else None,
        display=str(unavailable),
        model=getattr(getattr(unavailable, '_meta', None), 'model_name', unavailable.__class__.__name__.lower()),
        cable_assembly=None,
    )


def _lane_context_reference(*, lane_context, lane=None):
    if lane is not None:
        return _lane_analysis_object_reference(lane)
    if lane_context is None:
        return None

    lane_id = lane_context.get('lane_id')
    endpoint_label = lane_context.get('endpoint_label') or f'Endpoint #{lane_context.get("endpoint_id")}'
    lane_index = lane_context.get('lane_index')
    direction = lane_context.get('direction')
    wavelength = lane_context.get('wavelength_nm')
    display = f'{endpoint_label}:lane-{lane_index}:{direction}:{wavelength}nm'
    return SimpleNamespace(
        pk=lane_id,
        display=display,
        url=None,
        model='opticallane',
        registry_key='opticallane',
        endpoint_context=f'{endpoint_label} / lane {lane_index} / {direction} / {wavelength} nm',
        cable_assemblies=(),
        lane_workspace_url=_path_with_query(
            'lane_workspace',
            {
                'fabric': lane_context.get('fabric_id'),
                'plane': lane_context.get('plane_id'),
            },
        ),
        signal_path_resolver_url=_path_with_query(
            'path_resolver',
            {
                'source_registry_key': 'opticallane',
                'source_id': lane_id,
                'resolution': 'signal_lane',
            },
        ),
        signal_blast_radius_url=_path_with_query(
            'blast_radius',
            {
                'target_registry_key': 'opticallane',
                'target_id': lane_id,
                'resolution': 'signal_lane',
            },
        ),
        lane_drilldown_url=_path_with_query(
            'lane_drilldown',
            {
                'target_registry_key': 'opticallane',
                'target_id': lane_id,
            },
        ),
    )


def _blast_match_step(*, step, connector_position_ids, fiber_strand_ids):
    if step.object_type == 'connector_position' and step.object_id in connector_position_ids:
        return ('connector_position', step.object_id)
    if step.object_type == 'fiber_strand' and step.object_id in fiber_strand_ids:
        return ('fiber_strand', step.object_id)
    return None


def _build_v2_failure_scenario_blast_radius(
    *,
    failure_mode,
    connector_endpoint=None,
    cable_assembly=None,
    selected_fabric=None,
    actor=None,
):
    connector_ids_by_fabric = {}
    strand_ids_by_fabric = {}
    unavailable_objects = []
    scenario_target = None

    if failure_mode == 'connector_unplug':
        if connector_endpoint is None:
            raise ValueError('Connector selection is required for connector-unplug simulation.')
        scenario_target = _lane_analysis_object_reference(connector_endpoint)
        positions = list(
            ConnectorPosition.objects.filter(endpoint=connector_endpoint).order_by('position_number', 'pk')
        )
        unavailable_objects.extend(positions)
        if positions:
            connector_ids_by_fabric[connector_endpoint.fabric_id] = {position.pk for position in positions}
    elif failure_mode == 'cable_cut':
        if cable_assembly is None:
            raise ValueError('Cable assembly selection is required for cable-cut simulation.')
        scenario_target = _cable_assembly_reference(cable_assembly)
        strands = FiberStrand.objects.filter(
            cable_site_id=cable_assembly.site_id,
            cable_id=cable_assembly.cable_id,
        ).select_related('segment')
        if selected_fabric is not None:
            strands = strands.filter(segment__fabric=selected_fabric)
        strands = list(strands.order_by('segment__fabric_id', 'segment__name', 'strand_index', 'pk'))
        unavailable_objects.extend(strands)
        for strand in strands:
            strand_ids = strand_ids_by_fabric.setdefault(strand.segment.fabric_id, set())
            strand_ids.add(strand.pk)
    else:
        raise ValueError(f'Unsupported failure mode: {failure_mode}')

    impacted_rows_by_source_lane = {}
    endpoint_stats = {}
    lane_ids = set()
    endpoint_ids = set()

    for fabric_id in sorted(set(connector_ids_by_fabric) | set(strand_ids_by_fabric)):
        connector_position_ids = connector_ids_by_fabric.get(fabric_id, set())
        fiber_strand_ids = strand_ids_by_fabric.get(fabric_id, set())
        if not connector_position_ids and not fiber_strand_ids:
            continue

        resolver_rows = resolver_service.build_path_resolver_matrix(
            fabric_id=fabric_id,
            actor=actor,
        )
        for resolver_row in resolver_rows:
            matched_components = set()
            for step in resolver_row.resolved_path.steps:
                matched = _blast_match_step(
                    step=step,
                    connector_position_ids=connector_position_ids,
                    fiber_strand_ids=fiber_strand_ids,
                )
                if matched is not None:
                    matched_components.add(matched)
            if not matched_components:
                continue

            source_lane = resolver_row.source_lane or {}
            destination_lane = resolver_row.destination_lane or {}
            source_lane_id = source_lane.get('lane_id')
            if source_lane_id is None:
                continue
            destination_lane_id = destination_lane.get('lane_id')

            entry = impacted_rows_by_source_lane.setdefault(
                source_lane_id,
                {
                    'source_lane_context': source_lane,
                    'destination_lane_context': destination_lane if destination_lane_id else None,
                    'matched_components': set(),
                    'path_found': resolver_row.path_found,
                    'error': resolver_row.error,
                    'path_url': _path_with_query(
                        'path_query',
                        {
                            'source_lane': source_lane_id,
                            'destination_lane': destination_lane_id,
                        },
                    ),
                },
            )
            entry['matched_components'].update(matched_components)

            lane_ids.add(source_lane_id)
            source_endpoint_id = source_lane.get('endpoint_id')
            if source_endpoint_id:
                endpoint_ids.add(source_endpoint_id)
                stat = endpoint_stats.setdefault(
                    source_endpoint_id,
                    {'lane_ids': set(), 'path_count': 0, 'sample_path_url': None},
                )
                stat['lane_ids'].add(source_lane_id)
                stat['path_count'] += 1
                if stat['sample_path_url'] is None:
                    stat['sample_path_url'] = entry['path_url']

            if destination_lane_id:
                lane_ids.add(destination_lane_id)
                destination_endpoint_id = destination_lane.get('endpoint_id')
                if destination_endpoint_id:
                    endpoint_ids.add(destination_endpoint_id)
                    stat = endpoint_stats.setdefault(
                        destination_endpoint_id,
                        {'lane_ids': set(), 'path_count': 0, 'sample_path_url': None},
                    )
                    stat['lane_ids'].add(destination_lane_id)
                    stat['path_count'] += 1
                    if stat['sample_path_url'] is None:
                        stat['sample_path_url'] = entry['path_url']

    optical_lanes = OpticalLane.objects.select_related(
        'fabric',
        'endpoint',
        'endpoint__node',
        'plane',
        'local_mpo_endpoint',
        'local_mpo_position',
    ).filter(pk__in=lane_ids)
    lane_by_id = {lane.pk: lane for lane in optical_lanes}

    endpoints = Endpoint.objects.select_related('fabric', 'node').filter(pk__in=endpoint_ids)
    endpoint_by_id = {endpoint.pk: endpoint for endpoint in endpoints}

    connector_position_ids = {
        object_id
        for entries in impacted_rows_by_source_lane.values()
        for object_type, object_id in entries['matched_components']
        if object_type == 'connector_position'
    }
    fiber_strand_ids = {
        object_id
        for entries in impacted_rows_by_source_lane.values()
        for object_type, object_id in entries['matched_components']
        if object_type == 'fiber_strand'
    }
    connector_by_id = {
        position.pk: position
        for position in ConnectorPosition.objects.filter(pk__in=connector_position_ids)
    }
    strand_by_id = {
        strand.pk: strand
        for strand in FiberStrand.objects.filter(pk__in=fiber_strand_ids).select_related('segment')
    }

    impacted_path_rows = []
    for source_lane_id, row in impacted_rows_by_source_lane.items():
        source_lane = lane_by_id.get(source_lane_id)
        destination_lane_context = row['destination_lane_context']
        destination_lane_id = None
        if destination_lane_context is not None:
            destination_lane_id = destination_lane_context.get('lane_id')
        destination_lane = lane_by_id.get(destination_lane_id)

        matched_references = []
        for object_type, object_id in sorted(row['matched_components'], key=lambda item: (item[0], item[1])):
            if object_type == 'connector_position':
                connector = connector_by_id.get(object_id)
                if connector is None:
                    matched_references.append(
                        SimpleNamespace(
                            pk=object_id,
                            url=None,
                            display=f'Connector position #{object_id}',
                            model='connectorposition',
                            cable_assembly=None,
                        )
                    )
                else:
                    matched_references.append(_blast_unavailable_reference(connector))
            elif object_type == 'fiber_strand':
                strand = strand_by_id.get(object_id)
                if strand is None:
                    matched_references.append(
                        SimpleNamespace(
                            pk=object_id,
                            url=None,
                            display=f'Fiber strand #{object_id}',
                            model='fiberstrand',
                            cable_assembly=None,
                        )
                    )
                else:
                    matched_references.append(_blast_unavailable_reference(strand))

        impacted_path_rows.append(
            SimpleNamespace(
                source_lane=_lane_context_reference(
                    lane_context=row['source_lane_context'],
                    lane=source_lane,
                ),
                destination_lane=_lane_context_reference(
                    lane_context=destination_lane_context,
                    lane=destination_lane,
                ),
                path_url=row['path_url'],
                matched_components=tuple(matched_references),
                path_found=row['path_found'],
                error=row['error'],
            )
        )

    impacted_path_rows.sort(
        key=lambda row: (
            row.source_lane.display if row.source_lane is not None else '',
            row.destination_lane.display if row.destination_lane is not None else '',
        )
    )

    impacted_endpoints = []
    for endpoint_id, stat in endpoint_stats.items():
        endpoint = endpoint_by_id.get(endpoint_id)
        if endpoint is None:
            continue
        endpoint_reference = _lane_analysis_object_reference(endpoint)
        impacted_endpoints.append(
            SimpleNamespace(
                endpoint=endpoint_reference,
                impacted_lane_total=len(stat['lane_ids']),
                impacted_path_total=stat['path_count'],
                sample_path_url=stat['sample_path_url'],
            )
        )
    impacted_endpoints.sort(key=lambda row: row.endpoint.display)

    return {
        'mode': 'scenario',
        'failure_mode': failure_mode,
        'failure_mode_label': _blast_failure_mode_label(failure_mode),
        'target': scenario_target,
        'resolution': 'endpoint',
        'simulated_component_count': len(unavailable_objects),
        'simulated_components': tuple(
            _blast_unavailable_reference(unavailable)
            for unavailable in unavailable_objects
        ),
        'impacted_endpoint_count': len(impacted_endpoints),
        'impacted_path_count': len(impacted_path_rows),
        'impacted_endpoints': tuple(impacted_endpoints),
        'impacted_paths': tuple(impacted_path_rows),
    }


class PathResolverView(PathQueryView):
    """
    Legacy lane-analysis route retained for parity.

    This aliases the V2 path query UI while accepting legacy selector params.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET

        source_lane = context.get('selected_source')
        destination_lane = context.get('selected_destination')
        resolved_path = context.get('resolved_path')

        source_registry_key = query.get('source_registry_key', '')
        source_id = query.get('source_id', '')
        destination_registry_key = query.get('destination_registry_key', '')
        destination_id = query.get('destination_id', '')
        resolution = (query.get('resolution') or 'signal_lane').strip()

        if source_lane is None and source_id:
            source_target = _resolve_lane_analysis_target(source_registry_key, source_id)
            if source_target is None:
                messages.error(self.request, 'Selected source object was not found.')
            else:
                preferred_direction = 'send' if resolution == 'signal_lane' else ''
                source_lane = _pick_lane_for_target(source_target, preferred_direction=preferred_direction or 'send')
                if source_lane is None:
                    messages.error(self.request, 'No optical lanes were found for the selected source object.')

        if source_lane is not None and destination_lane is None and destination_id:
            destination_target = _resolve_lane_analysis_target(destination_registry_key, destination_id)
            if destination_target is None:
                messages.error(self.request, 'Selected destination object was not found.')
            else:
                destination_lane = _pick_lane_for_target(destination_target, preferred_direction='receive')
                if destination_lane is None:
                    messages.error(self.request, 'No optical lanes were found for the selected destination object.')

        if source_lane is not None and resolved_path is None and source_id:
            if destination_lane is not None and destination_lane.fabric_id != source_lane.fabric_id:
                messages.error(self.request, 'Destination lane must be in the same fabric as the source lane.')
            else:
                resolved_path = resolve_optical_lane_path(
                    source=source_lane,
                    destination=destination_lane,
                    actor=self.request.user,
                )
                resolved_path = _normalize_resolved_path_payload(resolved_path)

        if source_lane is not None:
            selected_fabric = source_lane.fabric
            source_lanes = context['source_lanes'].filter(fabric=selected_fabric)
            destination_lanes = context['destination_lanes'].filter(fabric=selected_fabric)
            context.update(
                {
                    'selected_fabric': selected_fabric,
                    'source_lanes': source_lanes,
                    'destination_lanes': destination_lanes,
                    'selected_source': source_lane,
                    'selected_destination': destination_lane,
                    'source_lane': source_lane,
                    'destination_lane': destination_lane,
                    'resolved_path': resolved_path,
                }
            )
        return context


class LaneDrilldownView(TemplateView):
    template_name = 'netbox_plant_graph/lane_drilldown.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = {
            'target_registry_key': self.request.GET.get('target_registry_key', 'endpoint'),
            'target_id': self.request.GET.get('target_id', ''),
            'lane_index': self.request.GET.get('lane_index', ''),
        }
        result = None
        error = None
        target = _resolve_lane_analysis_target(query['target_registry_key'], query['target_id'])
        lane_index = _parse_int(query['lane_index'])
        if query['target_id']:
            if target is None:
                error = 'Select a valid target object.'
            else:
                helper = getattr(resolver_service, 'build_lane_drilldown', None)
                if callable(helper):
                    try:
                        result = helper(target=target, lane_index=lane_index)
                    except Exception:
                        result = None
                if result is None:
                    result = _build_v2_lane_drilldown(target=target, lane_index=lane_index)
                result = _normalize_lane_drilldown_payload(result)

        context.update(
            {
                'page_title': 'Lane Drilldown',
                'registry_choices': _LANE_ANALYSIS_REGISTRY_CHOICES,
                'query': query,
                'result': result,
                'error': error,
            }
        )
        return context


class LaneCompareView(TemplateView):
    template_name = 'netbox_plant_graph/lane_compare.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = {
            'baseline_registry_key': self.request.GET.get('baseline_registry_key', 'endpoint'),
            'baseline_id': self.request.GET.get('baseline_id', ''),
            'candidate_registry_key': self.request.GET.get('candidate_registry_key', 'endpoint'),
            'candidate_id': self.request.GET.get('candidate_id', ''),
        }
        compare_result = None
        error = None
        durable_finding_links = {'baseline': None, 'candidate': None}
        baseline_target = _resolve_lane_analysis_target(query['baseline_registry_key'], query['baseline_id'])
        candidate_target = _resolve_lane_analysis_target(query['candidate_registry_key'], query['candidate_id'])

        if query['baseline_id'] or query['candidate_id']:
            if baseline_target is None:
                error = 'Select a valid baseline object.'
            elif candidate_target is None:
                error = 'Select a valid candidate object.'
            else:
                helper = getattr(resolver_service, 'compare_lane_allocations', None)
                if callable(helper):
                    try:
                        compare_result = helper(
                            baseline_target=baseline_target,
                            candidate_target=candidate_target,
                        )
                    except Exception:
                        compare_result = None
                if compare_result is None:
                    compare_result = _build_v2_lane_compare(
                        baseline_target=baseline_target,
                        candidate_target=candidate_target,
                    )
                compare_result = _normalize_lane_compare_payload(compare_result)
                durable_finding_links = {
                    'baseline': _target_durable_finding_link(baseline_target),
                    'candidate': _target_durable_finding_link(candidate_target),
                }

        context.update(
            {
                'page_title': 'Lane Compare',
                'registry_choices': _LANE_ANALYSIS_REGISTRY_CHOICES,
                'query': query,
                'compare_result': compare_result,
                'comparison': compare_result,
                'durable_finding_links': durable_finding_links,
                'error': error,
            }
        )
        return context


class BlastRadiusView(TemplateView):
    template_name = 'netbox_plant_graph/blast_radius.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics = Fabric.objects.order_by('name', 'pk')
        selected_fabric = None
        selected_fabric_raw = self.request.GET.get('fabric') or self.request.GET.get('fabric_id') or ''
        if selected_fabric_raw:
            selected_fabric = fabrics.filter(pk=selected_fabric_raw).first()

        failure_mode = (self.request.GET.get('failure_mode') or 'connector_unplug').strip().lower()
        valid_failure_modes = {choice['value'] for choice in _BLAST_FAILURE_MODE_CHOICES}
        if failure_mode not in valid_failure_modes:
            failure_mode = 'connector_unplug'

        query = {
            'fabric': str(selected_fabric.pk) if selected_fabric is not None else selected_fabric_raw,
            'failure_mode': failure_mode,
            'connector_endpoint_id': self.request.GET.get('connector_endpoint_id', ''),
            'cable_assembly_id': self.request.GET.get('cable_assembly_id', ''),
            'target_registry_key': self.request.GET.get('target_registry_key', 'endpoint'),
            'target_id': self.request.GET.get('target_id', ''),
            'resolution': self.request.GET.get('resolution', 'attachment_unit'),
        }

        connector_candidates = _blast_connector_candidates(fabric=selected_fabric)
        connector_choices = tuple(
            SimpleNamespace(
                value=connector.pk,
                label=f'{connector.fabric.name} / {connector.address} ({connector.position_total} positions)',
            )
            for connector in connector_candidates
        )
        connector_by_id = {
            connector.pk: connector
            for connector in connector_candidates
        }

        cable_candidates = _blast_cable_candidates(fabric=selected_fabric)
        cable_choices = tuple(
            SimpleNamespace(
                value=cable.pk,
                label=f'{cable.site.name} / {cable.cable_id}',
            )
            for cable in cable_candidates
        )
        cable_by_id = {
            cable.pk: cable
            for cable in cable_candidates
        }

        result = None
        error = None
        scenario_requested = bool(query['connector_endpoint_id'] or query['cable_assembly_id'])
        if scenario_requested:
            connector_endpoint = connector_by_id.get(_parse_int(query['connector_endpoint_id']))
            cable_assembly = cable_by_id.get(_parse_int(query['cable_assembly_id']))

            if query['failure_mode'] == 'connector_unplug':
                if connector_endpoint is None:
                    error = 'Select a valid connector endpoint to simulate unplugging.'
                else:
                    try:
                        result = _build_v2_failure_scenario_blast_radius(
                            failure_mode='connector_unplug',
                            connector_endpoint=connector_endpoint,
                            selected_fabric=selected_fabric,
                            actor=self.request.user,
                        )
                    except Exception as exc:
                        error = f'Unable to compute connector-unplug blast radius: {exc}'
            elif query['failure_mode'] == 'cable_cut':
                if cable_assembly is None:
                    error = 'Select a valid cable assembly to simulate a cable cut.'
                else:
                    try:
                        result = _build_v2_failure_scenario_blast_radius(
                            failure_mode='cable_cut',
                            cable_assembly=cable_assembly,
                            selected_fabric=selected_fabric,
                            actor=self.request.user,
                        )
                    except Exception as exc:
                        error = f'Unable to compute cable-cut blast radius: {exc}'
        else:
            target = _resolve_lane_analysis_target(query['target_registry_key'], query['target_id'])
            if query['target_id']:
                if target is None:
                    error = 'Select a valid target object.'
                else:
                    helper = getattr(resolver_service, 'compute_blast_radius', None)
                    if callable(helper):
                        try:
                            result = helper(target=target, resolution=query['resolution'])
                        except Exception:
                            result = None
                    if result is None:
                        result = _build_v2_blast_radius(target=target, resolution=query['resolution'])

        context.update(
            {
                'page_title': 'Physical Cable Blast Radius',
                'fabrics': fabrics,
                'selected_fabric': selected_fabric,
                'failure_mode_choices': _BLAST_FAILURE_MODE_CHOICES,
                'connector_choices': connector_choices,
                'cable_choices': cable_choices,
                'registry_choices': _LANE_ANALYSIS_REGISTRY_CHOICES,
                'resolution_choices': _BLAST_RADIUS_RESOLUTION_CHOICES,
                'query': query,
                'result': result,
                'error': error,
            }
        )
        return context


def _object_reference(obj):
    if obj is None:
        return None
    return SimpleNamespace(
        url=obj.get_absolute_url() if hasattr(obj, 'get_absolute_url') else None,
        display=str(obj),
    )


def _finding_metadata(finding_event: AuditEvent) -> dict:
    return dict(finding_event.metadata or {})


def _finding_status(finding_event: AuditEvent) -> str:
    status = _finding_metadata(finding_event).get('finding_status')
    return status or FINDING_STATUS_DEFAULT


def _finding_type(finding_event: AuditEvent) -> str:
    metadata = _finding_metadata(finding_event)
    return metadata.get('finding_type') or finding_event.event_type


def _finding_severity(finding_event: AuditEvent) -> str:
    metadata = _finding_metadata(finding_event)
    severity = metadata.get('severity')
    if severity:
        return severity
    if finding_event.outcome in {'failed', 'error'}:
        return 'error'
    return 'info'


def _finding_queryset_for_fabric(fabric):
    queryset = AuditEvent.objects.select_related('fabric', 'actor').order_by('-created', '-pk')
    if fabric is not None:
        queryset = queryset.filter(fabric=fabric)
    return queryset


def _finding_is_primary_record(finding_event: AuditEvent) -> bool:
    metadata = _finding_metadata(finding_event)
    return not metadata.get('finding_transition', False)


def _active_findings(fabric):
    findings = [
        event
        for event in _finding_queryset_for_fabric(fabric)
        if _finding_is_primary_record(event)
    ]
    return [event for event in findings if _finding_status(event) in FINDING_STATUS_ACTIVE]


def _finding_transition_events(finding_event: AuditEvent, *, limit=20):
    related_events = []
    for event in _finding_queryset_for_fabric(finding_event.fabric)[:500]:
        metadata = event.metadata or {}
        if metadata.get('finding_pk') == finding_event.pk and metadata.get('finding_transition'):
            related_events.append(event)
            if len(related_events) >= limit:
                break
    return related_events


def _finding_to_triage_row(finding_event: AuditEvent):
    metadata = _finding_metadata(finding_event)
    return SimpleNamespace(
        pk=finding_event.pk,
        finding_type=_finding_type(finding_event),
        severity=_finding_severity(finding_event),
        status=_finding_status(finding_event),
        message=finding_event.message,
        fabric=finding_event.fabric,
        resolved_tenant=getattr(finding_event.fabric, 'tenant', None),
        object=finding_event.subject,
        first_seen_at=finding_event.created,
        last_seen_at=finding_event.last_updated,
        assigned_to=metadata.get('assigned_to_display'),
    )


def _finding_actions(finding_event: AuditEvent):
    status = _finding_status(finding_event)
    actions = []
    if status == 'open':
        actions.append(('Acknowledge', 'audit_finding_acknowledge'))
    if status in {'open', 'acknowledged'}:
        actions.append(('Start Remediation', 'audit_finding_start_remediation'))
    if status in {'open', 'acknowledged', 'in_progress'}:
        actions.append(('Resolve', 'audit_finding_resolve'))
    if status != 'suppressed':
        actions.append(('Suppress', 'audit_finding_suppress'))
    if status == 'suppressed':
        actions.append(('Unsuppress', 'audit_finding_unsuppress'))
    if status in {'resolved', 'suppressed'}:
        actions.append(('Reopen', 'audit_finding_reopen'))
    return tuple(
        {
            'label': label,
            'url': reverse(f'plugins:netbox_plant_graph:{route_name}', kwargs={'pk': finding_event.pk}),
        }
        for label, route_name in actions
    )


def _parse_optional_datetime(raw_value):
    raw_value = (raw_value or '').strip()
    if not raw_value:
        return None
    parsed_dt = parse_datetime(raw_value)
    if parsed_dt is not None:
        if timezone.is_naive(parsed_dt):
            return timezone.make_aware(parsed_dt, timezone.get_current_timezone())
        return parsed_dt
    parsed_date = parse_date(raw_value)
    if parsed_date is None:
        return None
    return timezone.make_aware(
        datetime.combine(parsed_date, datetime.min.time()),
        timezone.get_current_timezone(),
    )


def _record_finding_transition(*, finding_event, actor, action_name, old_status, new_status, note=''):
    record_audit_event(
        event_type='policy_eval',
        fabric=finding_event.fabric,
        actor=actor,
        subject=finding_event,
        outcome='ok',
        message=f'Finding #{finding_event.pk} {action_name}.',
        payload={
            'finding_pk': finding_event.pk,
            'action': action_name,
            'old_status': old_status,
            'new_status': new_status,
            'note': note,
        },
        metadata={
            'finding_transition': True,
            'finding_pk': finding_event.pk,
            'old_status': old_status,
            'new_status': new_status,
        },
    )


def _set_finding_status(*, finding_event, actor, new_status, action_name, note=''):
    old_status = _finding_status(finding_event)
    metadata = _finding_metadata(finding_event)
    metadata['finding_status'] = new_status
    metadata['last_action'] = action_name
    if note:
        metadata['last_note'] = note
    if action_name == 'start_remediation' and actor is not None:
        metadata['assigned_to_id'] = actor.pk
        metadata['assigned_to_display'] = getattr(actor, 'username', str(actor))
    if action_name == 'resolve':
        metadata['resolved_at'] = timezone.now().isoformat()
    if action_name == 'reopen':
        metadata.pop('resolved_at', None)
    finding_event.metadata = metadata
    finding_event.save(update_fields=['metadata', 'last_updated'])
    _record_finding_transition(
        finding_event=finding_event,
        actor=actor,
        action_name=action_name,
        old_status=old_status,
        new_status=new_status,
        note=note,
    )


def _active_finding_suppressions(finding_event: AuditEvent):
    if finding_event.fabric_id is None:
        return ()
    rules = SuppressionRule.objects.filter(
        fabric_id=finding_event.fabric_id,
        policy_key='audit_finding',
        status='active',
        revoked_at__isnull=True,
    ).order_by('-created', '-pk')
    return tuple(
        rule
        for rule in rules
        if (rule.metadata or {}).get('finding_pk') == finding_event.pk
    )


def _apply_finding_action(*, finding_event, actor, action_name, note='', expires_at=None):
    if action_name == 'acknowledge':
        _set_finding_status(
            finding_event=finding_event,
            actor=actor,
            new_status='acknowledged',
            action_name=action_name,
            note=note,
        )
        return True, 'Audit finding acknowledged.'

    if action_name == 'start_remediation':
        _set_finding_status(
            finding_event=finding_event,
            actor=actor,
            new_status='in_progress',
            action_name=action_name,
            note=note,
        )
        return True, 'Audit finding marked in progress.'

    if action_name == 'resolve':
        _set_finding_status(
            finding_event=finding_event,
            actor=actor,
            new_status='resolved',
            action_name=action_name,
            note=note,
        )
        return True, 'Audit finding resolved.'

    if action_name == 'reopen':
        _set_finding_status(
            finding_event=finding_event,
            actor=actor,
            new_status='open',
            action_name=action_name,
            note=note,
        )
        return True, 'Audit finding reopened.'

    if action_name == 'suppress':
        if finding_event.fabric_id is None:
            return False, 'Cannot suppress a finding without an associated fabric.'
        metadata = _finding_metadata(finding_event)
        plane = Plane.objects.filter(
            pk=metadata.get('plane_id'),
            fabric_id=finding_event.fabric_id,
        ).first()
        optical_lane = OpticalLane.objects.filter(
            pk=metadata.get('optical_lane_id'),
            fabric_id=finding_event.fabric_id,
        ).first()
        rule = SuppressionRule.objects.create(
            fabric=finding_event.fabric,
            plane=plane,
            optical_lane=optical_lane,
            policy_key='audit_finding',
            status='active',
            reason=note or 'Suppressed from audit workflow.',
            created_by=actor if actor and actor.is_authenticated else None,
            approved_by=actor if actor and actor.is_authenticated else None,
            approved_at=timezone.now(),
            expires_at=expires_at,
            metadata={
                'kind': 'audit_finding',
                'finding_pk': finding_event.pk,
            },
        )
        record_audit_event(
            event_type='suppression_change',
            fabric=rule.fabric,
            actor=actor,
            subject=rule,
            outcome='ok',
            message=f'Suppression rule #{rule.pk} created for finding #{finding_event.pk}.',
            payload={'suppression_rule_id': rule.pk, 'finding_pk': finding_event.pk},
            metadata={'finding_transition': True, 'finding_pk': finding_event.pk},
        )
        _set_finding_status(
            finding_event=finding_event,
            actor=actor,
            new_status='suppressed',
            action_name=action_name,
            note=note,
        )
        return True, f'Audit finding suppressed via rule #{rule.pk}.'

    if action_name == 'unsuppress':
        rules = _active_finding_suppressions(finding_event)
        if not rules:
            return False, 'No active suppressions were found for this finding.'
        now = timezone.now()
        for rule in rules:
            metadata = dict(rule.metadata or {})
            metadata['unsuppress_note'] = note
            rule.status = 'revoked'
            rule.revoked_at = now
            rule.metadata = metadata
            rule.save(update_fields=['status', 'revoked_at', 'metadata', 'last_updated'])
            record_audit_event(
                event_type='suppression_change',
                fabric=rule.fabric,
                actor=actor,
                subject=rule,
                outcome='ok',
                message=f'Suppression rule #{rule.pk} revoked from finding #{finding_event.pk}.',
                payload={'suppression_rule_id': rule.pk, 'finding_pk': finding_event.pk},
                metadata={'finding_transition': True, 'finding_pk': finding_event.pk},
            )
        _set_finding_status(
            finding_event=finding_event,
            actor=actor,
            new_status='open',
            action_name=action_name,
            note=note,
        )
        return True, f'Revoked {len(rules)} suppression rule(s).'

    return False, 'Unsupported audit finding action.'


def _triage_url(*, fabric_id=None, severity='', finding_type='', status='', index=0):
    params = {'index': index}
    if fabric_id:
        params['fabric_id'] = fabric_id
    if severity:
        params['severity'] = severity
    if finding_type:
        params['finding_type'] = finding_type
    if status:
        params['status'] = status
    base = reverse('plugins:netbox_plant_graph:audit_triage')
    return f'{base}?{urlencode(params)}'


def _is_disjointness_exception_rule(rule: SuppressionRule) -> bool:
    metadata = rule.metadata or {}
    return metadata.get('kind') == 'disjointness_exception' or rule.policy_key == 'disjointness_exception'


class DisjointnessExceptionRequestFallbackForm(django_forms.Form):
    fabric = django_forms.ModelChoiceField(queryset=Fabric.objects.none())
    exception_type = django_forms.ChoiceField(
        choices=(
            ('shared_passive_artifact', 'Shared Passive Artifact'),
            ('cross_plane_fine_edge', 'Cross-Plane Fine Edge'),
            ('contamination_domain', 'Contamination Domain'),
        ),
    )
    plane_a = django_forms.ModelChoiceField(queryset=Plane.objects.none(), required=False)
    plane_b = django_forms.ModelChoiceField(queryset=Plane.objects.none(), required=False)
    scope_kind = django_forms.ChoiceField(
        choices=(
            ('plane_pair', 'Plane Pair'),
            ('target', 'Target'),
            ('domain', 'Domain'),
        ),
        required=False,
    )
    reason = django_forms.CharField(required=False, widget=django_forms.Textarea(attrs={'rows': 3}))
    expires_at = django_forms.DateTimeField(required=False, input_formats=['%Y-%m-%d', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M'])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fabric_qs = Fabric.objects.order_by('name', 'pk')
        plane_qs = Plane.objects.select_related('fabric').order_by('fabric__name', 'plane_number', 'pk')
        self.fields['fabric'].queryset = fabric_qs
        self.fields['plane_a'].queryset = plane_qs
        self.fields['plane_b'].queryset = plane_qs
        for field_name, field in self.fields.items():
            css_class = 'form-select' if isinstance(field.widget, django_forms.Select) else 'form-control'
            field.widget.attrs.setdefault('class', css_class)
            if field_name == 'expires_at':
                field.widget.attrs.setdefault('placeholder', 'YYYY-MM-DD or YYYY-MM-DD HH:MM')

    def clean(self):
        cleaned_data = super().clean()
        fabric = cleaned_data.get('fabric')
        plane_a = cleaned_data.get('plane_a')
        plane_b = cleaned_data.get('plane_b')
        if plane_a is not None and fabric is not None and plane_a.fabric_id != fabric.pk:
            self.add_error('plane_a', 'Plane A must belong to the selected fabric.')
        if plane_b is not None and fabric is not None and plane_b.fabric_id != fabric.pk:
            self.add_error('plane_b', 'Plane B must belong to the selected fabric.')
        if plane_a is not None and plane_b is not None and plane_a.pk == plane_b.pk:
            self.add_error('plane_b', 'Plane B must differ from Plane A.')
        return cleaned_data


def _disjointness_request_form(*, data=None, initial=None):
    form_class = getattr(forms, 'DisjointnessExceptionRequestForm', DisjointnessExceptionRequestFallbackForm)
    form = form_class(data=data, initial=initial)
    for field in form.fields.values():
        if 'class' in field.widget.attrs:
            continue
        field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, django_forms.Select) else 'form-control'
    return form


class HealthView(View):
    def get(self, request):
        target = reverse('plugins:netbox_plant_graph:audit_dashboard')
        fabric_id = request.GET.get('fabric_id')
        if fabric_id:
            target = f'{target}?{urlencode({"fabric_id": fabric_id})}'
        return HttpResponseRedirect(target)


class AuditDashboardView(TemplateView):
    template_name = 'netbox_plant_graph/audit_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics, selected_fabric = _selected_fabric_from_request(self.request, default_first=True)
        if selected_fabric is None:
            context.update(
                {
                    'fabrics': fabrics,
                    'selected_fabric': None,
                    'workflow_summary': None,
                    'policy_dashboard': None,
                    'unresolved_dashboard': None,
                    'finding_links': {},
                    'unresolved_links': {},
                    'status_count_rows': (),
                    'severity_count_rows': (),
                    'type_count_rows': (),
                    'unresolved_cause_rows': (),
                    'unresolved_oldest_rows': (),
                    'churn_window_rows': (),
                    'all_events_url': None,
                    'policy_plane_pair_rows': (),
                    'policy_domain_rows': (),
                }
            )
            return context

        now = timezone.now()
        all_findings = [
            event
            for event in _finding_queryset_for_fabric(selected_fabric)
            if _finding_is_primary_record(event)
        ]
        active_findings = [event for event in all_findings if _finding_status(event) in FINDING_STATUS_ACTIVE]
        resolved_findings = [event for event in all_findings if _finding_status(event) == 'resolved']

        status_counts = {}
        severity_counts = {}
        type_counts = {}
        for event in active_findings:
            status_counts[_finding_status(event)] = status_counts.get(_finding_status(event), 0) + 1
            severity_counts[_finding_severity(event)] = severity_counts.get(_finding_severity(event), 0) + 1
            type_counts[_finding_type(event)] = type_counts.get(_finding_type(event), 0) + 1

        status_count_rows = tuple(
            {
                'name': name,
                'value': value,
                'url': _triage_url(fabric_id=selected_fabric.pk, status=name),
            }
            for name, value in sorted(status_counts.items())
        )
        severity_count_rows = tuple(
            {
                'name': name,
                'value': value,
                'url': _triage_url(fabric_id=selected_fabric.pk, severity=name),
            }
            for name, value in sorted(severity_counts.items())
        )
        type_count_rows = tuple(
            {
                'name': name,
                'value': value,
                'url': _triage_url(fabric_id=selected_fabric.pk, finding_type=name),
            }
            for name, value in sorted(type_counts.items())
        )

        transition_events = [
            event
            for event in _finding_queryset_for_fabric(selected_fabric)
            if (event.metadata or {}).get('finding_transition')
        ]
        churn_windows = []
        for days, label in ((1, '24h'), (7, '7d'), (30, '30d')):
            since = now - timedelta(days=days)
            events = [event for event in transition_events if event.created >= since]
            action_counts = {}
            for event in events:
                action = (event.payload or {}).get('action') or ''
                action_counts[action] = action_counts.get(action, 0) + 1
            churn_windows.append(
                {
                    'label': label,
                    'opened': {'count': action_counts.get('open', 0), 'url': reverse('plugins:netbox_plant_graph:auditevent_list')},
                    'reopened': {'count': action_counts.get('reopen', 0), 'url': reverse('plugins:netbox_plant_graph:auditevent_list')},
                    'resolved': {'count': action_counts.get('resolve', 0), 'url': reverse('plugins:netbox_plant_graph:auditevent_list')},
                    'auto_resolved': {'count': 0, 'url': reverse('plugins:netbox_plant_graph:auditevent_list')},
                    'suppressed': {'count': action_counts.get('suppress', 0), 'url': reverse('plugins:netbox_plant_graph:auditevent_list')},
                }
            )

        recent_events = []
        for event in transition_events[:10]:
            finding_pk = (event.metadata or {}).get('finding_pk')
            finding_event = next((candidate for candidate in all_findings if candidate.pk == finding_pk), None)
            recent_events.append(
                SimpleNamespace(
                    finding=_object_reference(finding_event) if finding_event is not None else _object_reference(event),
                    event_type=(event.payload or {}).get('action') or event.event_type,
                    actor_display=getattr(event.actor, 'username', None),
                    created_at=event.created,
                    message=event.message,
                )
            )

        oldest_active_findings = tuple(
            SimpleNamespace(
                finding=_object_reference(event),
                affected_object=_object_reference(event.subject),
                status=_finding_status(event),
                severity=_finding_severity(event),
                age_days=(now.date() - event.created.date()).days,
                first_seen_at=event.created,
                last_seen_at=event.last_updated,
            )
            for event in sorted(active_findings, key=lambda finding: finding.created)[:10]
        )

        expiring_suppressions = []
        for rule in SuppressionRule.objects.filter(
            fabric=selected_fabric,
            status='active',
            revoked_at__isnull=True,
            expires_at__isnull=False,
        ).order_by('expires_at', 'pk')[:10]:
            finding_pk = (rule.metadata or {}).get('finding_pk')
            finding_event = next((candidate for candidate in all_findings if candidate.pk == finding_pk), None)
            remaining_days = max(0, (rule.expires_at.date() - now.date()).days)
            expiring_suppressions.append(
                SimpleNamespace(
                    finding=_object_reference(finding_event) if finding_event is not None else _object_reference(rule),
                    reason=rule.reason,
                    expires_at=rule.expires_at,
                    remaining_days=remaining_days,
                )
            )

        recent_runs = tuple(
            SimpleNamespace(
                run=_object_reference(run),
                status=run.status,
                trigger_mode=(run.metadata or {}).get('trigger_mode', 'operation'),
                started_at=run.started_at,
                completed_at=run.completed_at,
                finding_count=(run.result or {}).get('finding_count', 0),
                new_count=(run.result or {}).get('new_count', 0),
                reopened_count=(run.result or {}).get('reopened_count', 0),
                resolved_count=(run.result or {}).get('resolved_count', 0),
            )
            for run in OperationRun.objects.filter(fabric=selected_fabric).order_by('-created', '-pk')[:10]
        )

        workflow_summary = SimpleNamespace(
            total_findings=len(all_findings),
            active_findings=len(active_findings),
            resolved_findings=len(resolved_findings),
            suppressed_findings=status_counts.get('suppressed', 0),
            stale_findings_7d=sum(1 for event in active_findings if (now - event.created).days >= 7),
            stale_findings_30d=sum(1 for event in active_findings if (now - event.created).days >= 30),
            recent_runs=recent_runs,
            recent_events=tuple(recent_events),
            expiring_suppressions=tuple(expiring_suppressions),
            oldest_active_findings=oldest_active_findings,
        )

        disjoint_rules = [
            rule
            for rule in SuppressionRule.objects.filter(fabric=selected_fabric).order_by('-created', '-pk')
            if _is_disjointness_exception_rule(rule)
        ]
        active_exception_count = sum(1 for rule in disjoint_rules if rule.is_effective)
        policy_dashboard = SimpleNamespace(
            contamination_domain_count=0,
            uncovered_domain_count=0,
            uncovered_edge_bridge_count=0,
            uncovered_artifact_share_count=0,
            active_exception_count=active_exception_count,
            covered_exception_count=active_exception_count,
            drifted_exception_count=0,
            oldest_active_findings=(),
        )

        finding_links = {
            'active': _triage_url(fabric_id=selected_fabric.pk),
            'resolved': _triage_url(fabric_id=selected_fabric.pk, status='resolved'),
            'suppressed': _triage_url(fabric_id=selected_fabric.pk, status='suppressed'),
            'stale_7d': _triage_url(fabric_id=selected_fabric.pk),
            'stale_30d': _triage_url(fabric_id=selected_fabric.pk),
        }
        all_events_url = reverse('plugins:netbox_plant_graph:auditevent_list')
        all_events_url = f'{all_events_url}?{urlencode({"fabric": selected_fabric.pk})}'

        context.update(
            {
                'fabrics': fabrics,
                'selected_fabric': selected_fabric,
                'workflow_summary': workflow_summary,
                'policy_dashboard': policy_dashboard,
                'unresolved_dashboard': None,
                'finding_links': finding_links,
                'unresolved_links': {},
                'status_count_rows': status_count_rows,
                'severity_count_rows': severity_count_rows,
                'type_count_rows': type_count_rows,
                'unresolved_cause_rows': (),
                'unresolved_oldest_rows': (),
                'churn_window_rows': tuple(churn_windows),
                'all_events_url': all_events_url,
                'policy_plane_pair_rows': (),
                'policy_domain_rows': (),
            }
        )
        return context


class AuditTriageView(View):
    template_name = 'netbox_plant_graph/audit_triage.html'

    def _query_findings(self, *, fabric_id=None, severity='', finding_type='', status=''):
        fabric = Fabric.objects.filter(pk=fabric_id).first() if fabric_id else None
        findings = [
            event
            for event in _finding_queryset_for_fabric(fabric)
            if _finding_is_primary_record(event)
        ]
        if severity:
            findings = [event for event in findings if _finding_severity(event) == severity]
        if finding_type:
            findings = [event for event in findings if _finding_type(event) == finding_type]
        if status:
            findings = [event for event in findings if _finding_status(event) == status]
        else:
            findings = [event for event in findings if _finding_status(event) in FINDING_STATUS_ACTIVE]
        return findings

    def get(self, request):
        fabric_id = _parse_int(request.GET.get('fabric_id'))
        severity = (request.GET.get('severity') or '').strip()
        finding_type = (request.GET.get('finding_type') or '').strip()
        status = (request.GET.get('status') or '').strip()
        index = max(0, _parse_int(request.GET.get('index'), 0))
        findings = self._query_findings(
            fabric_id=fabric_id,
            severity=severity,
            finding_type=finding_type,
            status=status,
        )
        total_count = len(findings)
        finding_event = None
        finding_row = None
        recent_events = ()
        active_suppression = None
        if total_count:
            index = min(index, total_count - 1)
            finding_event = findings[index]
            finding_row = _finding_to_triage_row(finding_event)
            recent_events = tuple(
                SimpleNamespace(
                    event_type=(event.payload or {}).get('action') or event.event_type,
                    old_status=(event.payload or {}).get('old_status'),
                    new_status=(event.payload or {}).get('new_status'),
                    actor=event.actor,
                    created=event.created,
                )
                for event in _finding_transition_events(finding_event, limit=5)
            )
            suppressions = _active_finding_suppressions(finding_event)
            active_suppression = suppressions[0] if suppressions else None

        prev_index = index - 1 if total_count and index > 0 else None
        next_index = index + 1 if total_count and index < total_count - 1 else None
        dashboard_url = reverse('plugins:netbox_plant_graph:audit_dashboard')
        if fabric_id:
            dashboard_url = f'{dashboard_url}?{urlencode({"fabric_id": fabric_id})}'
        return self._render(
            request,
            fabric_id=fabric_id,
            severity=severity,
            finding_type=finding_type,
            status=status,
            finding=finding_row,
            index=index,
            total_count=total_count,
            recent_events=recent_events,
            active_suppression=active_suppression,
            prev_index=prev_index,
            next_index=next_index,
            audit_dashboard_url=dashboard_url,
        )

    def _render(
        self,
        request,
        *,
        fabric_id=None,
        severity='',
        finding_type='',
        status='',
        finding=None,
        index=0,
        total_count=0,
        recent_events=(),
        active_suppression=None,
        prev_index=None,
        next_index=None,
        audit_dashboard_url='',
    ):
        return render(
            request,
            self.template_name,
            {
                'fabrics': Fabric.objects.order_by('name', 'pk'),
                'fabric_id': fabric_id,
                'severity': severity,
                'finding_type': finding_type,
                'status': status,
                'finding': finding,
                'index': index,
                'total_count': total_count,
                'recent_events': recent_events,
                'active_suppression': active_suppression,
                'prev_index': prev_index,
                'next_index': next_index,
                'audit_dashboard_url': audit_dashboard_url or reverse('plugins:netbox_plant_graph:audit_dashboard'),
            },
        )

    def post(self, request):
        action = (request.POST.get('action') or '').strip()
        finding_pk = _parse_int(request.POST.get('finding_pk'))
        next_index = max(0, _parse_int(request.POST.get('next_index'), 0))
        fabric_id = _parse_int(request.POST.get('fabric_id'))
        severity = (request.POST.get('severity') or '').strip()
        finding_type = (request.POST.get('finding_type') or '').strip()
        status = (request.POST.get('status') or '').strip()
        if action == 'skip':
            return redirect(
                _triage_url(
                    fabric_id=fabric_id,
                    severity=severity,
                    finding_type=finding_type,
                    status=status,
                    index=next_index,
                )
            )
        if finding_pk is None:
            messages.error(request, 'No finding specified.')
            return redirect(
                _triage_url(
                    fabric_id=fabric_id,
                    severity=severity,
                    finding_type=finding_type,
                    status=status,
                    index=next_index,
                )
            )
        finding_event = get_object_or_404(AuditEvent, pk=finding_pk)
        note = (
            request.POST.get('resolution_summary')
            or request.POST.get('reason')
            or request.POST.get('note')
            or ''
        ).strip()
        expires_at = _parse_optional_datetime(request.POST.get('expires_at'))
        ok, message_text = _apply_finding_action(
            finding_event=finding_event,
            actor=request.user,
            action_name=action,
            note=note,
            expires_at=expires_at,
        )
        if ok:
            messages.success(request, message_text)
        else:
            messages.error(request, message_text)
        return redirect(
            _triage_url(
                fabric_id=fabric_id,
                severity=severity,
                finding_type=finding_type,
                status=status,
                index=next_index,
            )
        )


class AuditFindingActionView(View):
    action_name = ''
    success_message = 'Audit finding updated.'

    def post(self, request, pk):
        finding_event = get_object_or_404(AuditEvent, pk=pk)
        note = (
            request.POST.get('resolution_summary')
            or request.POST.get('reason')
            or request.POST.get('note')
            or ''
        ).strip()
        expires_at = _parse_optional_datetime(request.POST.get('expires_at'))
        ok, message_text = _apply_finding_action(
            finding_event=finding_event,
            actor=request.user,
            action_name=self.action_name,
            note=note,
            expires_at=expires_at,
        )
        if ok:
            messages.success(request, self.success_message)
        else:
            messages.error(request, message_text)
        fallback_url = finding_event.get_absolute_url()
        return redirect(_safe_next_url(request, fallback_url))


class AuditFindingAcknowledgeView(AuditFindingActionView):
    action_name = 'acknowledge'
    success_message = 'Audit finding acknowledged.'


class AuditFindingStartRemediationView(AuditFindingActionView):
    action_name = 'start_remediation'
    success_message = 'Audit finding marked in progress.'


class AuditFindingSuppressView(AuditFindingActionView):
    action_name = 'suppress'
    success_message = 'Audit finding suppressed.'


class AuditFindingUnsuppressView(AuditFindingActionView):
    action_name = 'unsuppress'
    success_message = 'Audit finding unsuppressed.'


class AuditFindingResolveView(AuditFindingActionView):
    action_name = 'resolve'
    success_message = 'Audit finding resolved.'


class AuditFindingReopenView(AuditFindingActionView):
    action_name = 'reopen'
    success_message = 'Audit finding reopened.'


class PolicyReviewView(TemplateView):
    template_name = 'netbox_plant_graph/policy_review.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics, selected_fabric = _selected_fabric_from_request(self.request, default_first=True)
        selected_plane = _selected_plane_from_request(self.request)
        context.update(
            {
                'fabrics': fabrics,
                'plane_choices': Plane.objects.select_related('fabric').order_by('fabric__name', 'plane_number', 'pk'),
                'selected_fabric': selected_fabric,
                'selected_plane': selected_plane,
                'evaluation': None,
                'exception_review': None,
                'plane_filter_note': None,
                'domain_rows': (),
                'exception_rows': (),
            }
        )
        return context


class PlaneAuditView(TemplateView):
    template_name = 'netbox_plant_graph/plane_audit.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics, selected_fabric = _selected_fabric_from_request(self.request, default_first=True)
        if selected_fabric is None:
            context.update(
                {
                    'fabrics': fabrics,
                    'selected_fabric': None,
                    'result': None,
                    'finding_counts': {'by_severity': (), 'by_type': ()},
                    'finding_rows': (),
                }
            )
            return context

        findings = [event for event in _active_findings(selected_fabric)]
        by_severity = {}
        by_type = {}
        for finding in findings:
            by_severity[_finding_severity(finding)] = by_severity.get(_finding_severity(finding), 0) + 1
            by_type[_finding_type(finding)] = by_type.get(_finding_type(finding), 0) + 1

        finding_rows = []
        for finding in findings:
            finding_rows.append(
                CompatNamespace(
                    finding=CompatNamespace(
                        pk=finding.pk,
                        severity=_finding_severity(finding),
                        finding_type=_finding_type(finding),
                        status=_finding_status(finding),
                        object=_object_reference(finding.subject) or CompatNamespace(url=None, display='-'),
                        message=finding.message or '',
                    ),
                    detail=CompatNamespace(
                        summary=finding.message or '',
                        remediation_hints=(),
                        action_links=(),
                        metadata=finding.metadata or {},
                        impact=CompatNamespace(
                            plane_ids=(),
                            affected_attachment_units=None,
                            present_lane_total=None,
                            expected_lane_total=None,
                            mapped_lane_total=None,
                            unmatched_peer_positions=(),
                            lane_map_consistency=None,
                            plane_consistency=None,
                            related_targets=(),
                        ),
                    ),
                    durable_finding=CompatNamespace(
                        status=_finding_status(finding),
                        active=_finding_status(finding) in FINDING_STATUS_ACTIVE,
                        assigned_to=(_finding_metadata(finding).get('assigned_to_display') or ''),
                        acknowledged_by='',
                        get_absolute_url=finding.get_absolute_url,
                    ),
                    durable_actions=_finding_actions(finding),
                    active_suppression=(_active_finding_suppressions(finding)[0] if _active_finding_suppressions(finding) else None),
                    related_unresolved_summaries=(),
                )
            )

        context.update(
            {
                'fabrics': fabrics,
                'selected_fabric': selected_fabric,
                'result': {'finding_count': len(findings)},
                'finding_counts': {
                    'by_severity': tuple(sorted(by_severity.items())),
                    'by_type': tuple(sorted(by_type.items())),
                },
                'finding_rows': tuple(finding_rows),
            }
        )
        return context


class DisjointnessExceptionRequestView(View):
    template_name = 'netbox_plant_graph/disjointness_exception_request.html'

    def get(self, request):
        fabric_id = _parse_int(request.GET.get('fabric_id'))
        initial = {'fabric': fabric_id} if fabric_id else {}
        form = _disjointness_request_form(initial=initial)
        return self._render(request, form=form)

    def post(self, request):
        form = _disjointness_request_form(data=request.POST)
        if form.is_valid():
            data = form.cleaned_data
            rule = SuppressionRule.objects.create(
                fabric=data['fabric'],
                plane=data.get('plane_a'),
                policy_key='disjointness_exception',
                status='pending',
                reason=(data.get('reason') or '').strip(),
                created_by=request.user if request.user.is_authenticated else None,
                expires_at=data.get('expires_at'),
                metadata={
                    'kind': 'disjointness_exception',
                    'exception_type': data.get('exception_type') or 'contamination_domain',
                    'scope_kind': data.get('scope_kind') or 'domain',
                    'plane_a_id': data.get('plane_a').pk if data.get('plane_a') else None,
                    'plane_b_id': data.get('plane_b').pk if data.get('plane_b') else None,
                    'exception_status': 'draft',
                },
            )
            record_audit_event(
                event_type='suppression_change',
                fabric=rule.fabric,
                actor=request.user,
                subject=rule,
                outcome='ok',
                message=f'Disjointness exception request #{rule.pk} submitted.',
                payload={'suppression_rule_id': rule.pk},
            )
            messages.success(request, 'Disjointness exception request submitted.')
            return redirect(rule.get_absolute_url())
        return self._render(request, form=form)

    def _render(self, request, *, form):
        return render(
            request,
            self.template_name,
            {
                'form': form,
                'fabrics': Fabric.objects.order_by('name', 'pk'),
            },
        )


class DisjointnessExceptionActionView(View):
    action_name = ''
    success_message = 'Disjointness exception updated.'

    def post(self, request, pk):
        rule = get_object_or_404(SuppressionRule, pk=pk)
        if not _is_disjointness_exception_rule(rule):
            messages.error(request, 'Selected suppression rule is not a disjointness exception.')
            return redirect(_safe_next_url(request, rule.get_absolute_url()))

        metadata = dict(rule.metadata or {})
        now = timezone.now()
        if self.action_name == 'approve':
            rule.status = 'active'
            rule.approved_by = request.user if request.user.is_authenticated else None
            rule.approved_at = now
            rule.revoked_at = None
            metadata['exception_status'] = 'approved'
        elif self.action_name == 'expire':
            rule.status = 'expired'
            rule.revoked_at = now
            metadata['exception_status'] = 'expired'
        elif self.action_name == 'reactivate':
            rule.status = 'active'
            rule.revoked_at = None
            if rule.expires_at is not None and rule.expires_at <= now:
                rule.expires_at = None
            metadata['exception_status'] = 'active'
        else:
            messages.error(request, 'Unsupported disjointness exception action.')
            return redirect(_safe_next_url(request, rule.get_absolute_url()))

        rule.metadata = metadata
        rule.save(
            update_fields=[
                'status',
                'approved_by',
                'approved_at',
                'revoked_at',
                'expires_at',
                'metadata',
                'last_updated',
            ]
        )
        record_audit_event(
            event_type='suppression_change',
            fabric=rule.fabric,
            actor=request.user,
            subject=rule,
            outcome='ok',
            message=f'Disjointness exception #{rule.pk} {self.action_name}.',
            payload={'suppression_rule_id': rule.pk, 'action': self.action_name},
        )
        messages.success(request, self.success_message)
        return redirect(_safe_next_url(request, rule.get_absolute_url()))


class DisjointnessExceptionApproveView(DisjointnessExceptionActionView):
    action_name = 'approve'
    success_message = 'Disjointness exception approved.'


class DisjointnessExceptionExpireView(DisjointnessExceptionActionView):
    action_name = 'expire'
    success_message = 'Disjointness exception expired.'


class DisjointnessExceptionReactivateView(DisjointnessExceptionActionView):
    action_name = 'reactivate'
    success_message = 'Disjointness exception reactivated.'


class SuppressionRuleRevokeView(View):
    def post(self, request, pk):
        rule = SuppressionRule.objects.get(pk=pk)
        form = forms.SuppressionRuleRevokeForm(request.POST)
        if form.is_valid():
            rule.status = 'revoked'
            rule.revoked_at = rule.revoked_at or timezone.now()
            reason = form.cleaned_data.get('reason', '').strip()
            if reason:
                rule.metadata = {
                    **(rule.metadata or {}),
                    'revoke_reason': reason,
                }
            rule.save(update_fields=['status', 'revoked_at', 'metadata', 'last_updated'])
            record_audit_event(
                event_type='suppression_change',
                fabric=rule.fabric,
                actor=request.user,
                subject=rule,
                outcome='ok',
                message='Suppression rule revoked.',
                payload={'suppression_rule_id': rule.pk},
            )
            messages.success(request, f'Revoked suppression rule #{rule.pk}.')
        else:
            messages.error(request, 'Unable to revoke suppression rule.')
        return redirect(rule.get_absolute_url())


class LaneWorkspaceView(TemplateView):
    template_name = 'netbox_plant_graph/lane_workspace_v2.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics = Fabric.objects.order_by('name', 'pk')
        fabric_id = self.request.GET.get('fabric')
        plane_id = self.request.GET.get('plane')
        direction = self.request.GET.get('direction')
        queryset = OpticalLane.objects.select_related(
            'fabric',
            'endpoint',
            'plane',
            'local_mpo_endpoint',
            'local_mpo_position',
        ).order_by('fabric__name', 'endpoint__address', 'lane_index', 'direction')
        if fabric_id:
            queryset = queryset.filter(fabric_id=fabric_id)
        if plane_id:
            queryset = queryset.filter(plane_id=plane_id)
        if direction in {'send', 'receive'}:
            queryset = queryset.filter(direction=direction)
        lanes = list(queryset[:1000])
        cable_references_by_position_id = _cable_references_by_mpo_position_ids(
            {
                lane.local_mpo_position_id
                for lane in lanes
                if lane.local_mpo_position_id is not None
            }
        )
        active_rules = SuppressionRule.objects.filter(status='active', revoked_at__isnull=True)
        suppressed_lane_ids = set(active_rules.exclude(optical_lane_id__isnull=True).values_list('optical_lane_id', flat=True))
        plane_rule_ids = set(active_rules.exclude(plane_id__isnull=True).values_list('plane_id', flat=True))
        lane_rows = []
        for lane in lanes:
            lane_rows.append(
                {
                    'lane': lane,
                    'suppressed': lane.pk in suppressed_lane_ids or (lane.plane_id in plane_rule_ids),
                    'cable_assemblies': cable_references_by_position_id.get(lane.local_mpo_position_id, ()),
                }
            )
        selected_fabric = fabrics.filter(pk=fabric_id).first() if fabric_id else None
        plane_counts = (
            queryset.values('plane_id')
            .annotate(total=Count('id'))
            .order_by('plane_id')
        )
        context.update(
            {
                'fabrics': fabrics,
                'selected_fabric': selected_fabric,
                'selected_plane_id': plane_id,
                'selected_direction': direction or '',
                'lane_rows': lane_rows,
                'lane_count': len(lane_rows),
                'plane_counts': plane_counts,
            }
        )
        return context


class PolicyDashboardView(TemplateView):
    template_name = 'netbox_plant_graph/policy_dashboard_v2.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fabrics = Fabric.objects.order_by('name', 'pk')
        fabric_id = self.request.GET.get('fabric')
        selected_fabric = fabrics.filter(pk=fabric_id).first() if fabric_id else None
        rules = SuppressionRule.objects.order_by('-created', '-pk')
        if selected_fabric is not None:
            rules = rules.filter(fabric=selected_fabric)
        policy_groups = (
            rules.exclude(policy_key='')
            .values('policy_key', 'status')
            .annotate(total=Count('id'))
            .order_by('policy_key', 'status')
        )
        context.update(
            {
                'fabrics': fabrics,
                'selected_fabric': selected_fabric,
                'policy_groups': policy_groups,
                'rule_count': rules.count(),
                'active_rule_count': rules.filter(status='active').count(),
                'revoked_rule_count': rules.filter(status='revoked').count(),
            }
        )
        return context


class OperationsCenterView(TemplateView):
    template_name = 'netbox_plant_graph/operations_center_v2.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get('form') or forms.OperationExecuteForm()
        recent_runs = OperationRun.objects.select_related('fabric', 'initiated_by').order_by('-created', '-pk')[:25]
        context.update({'form': form, 'recent_runs': recent_runs})
        return context

    def post(self, request, *args, **kwargs):
        form = forms.OperationExecuteForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        execution = execute_operation_profile(
            profile=form.cleaned_data['profile'],
            fabric=form.cleaned_data['fabric'],
            parameters=form.cleaned_data['parameters_json'],
            actor=request.user,
        )
        if execution.reused_existing:
            messages.info(request, f'Reused prior completed operation run #{execution.run.pk}.')
        else:
            messages.success(request, f'Completed operation run #{execution.run.pk}.')
        return redirect(execution.run.get_absolute_url())


class CoordinateLayoutView(TemplateView):
    template_name = 'netbox_plant_graph/coordinate_layout.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get('form') or forms.CoordinateLayoutUpdateForm()
        fabric_id = self.request.GET.get('fabric')
        endpoints = Endpoint.objects.select_related('fabric').order_by('fabric__name', 'address', 'pk')
        if fabric_id:
            endpoints = endpoints.filter(fabric_id=fabric_id)
        rows = []
        for endpoint in endpoints[:1000]:
            spatial = (endpoint.metadata or {}).get('spatial') or {}
            rows.append(
                {
                    'endpoint': endpoint,
                    'x': spatial.get('x'),
                    'y': spatial.get('y'),
                    'z': spatial.get('z'),
                    'orientation': spatial.get('orientation'),
                    'unit': spatial.get('unit') or 'm',
                }
            )
        invalid_rows = [
            row for row in rows
            if row['x'] is None or row['y'] is None
        ]
        context.update(
            {
                'form': form,
                'fabrics': Fabric.objects.order_by('name', 'pk'),
                'selected_fabric_id': fabric_id or '',
                'rows': rows,
                'row_count': len(rows),
                'invalid_count': len(invalid_rows),
                'spatial_placement_list_url': reverse('plugins:netbox_plant_graph:endpoint_list'),
                'page_title': 'Coordinate Layout',
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        form = forms.CoordinateLayoutUpdateForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        endpoint = form.save()
        messages.success(request, f'Updated spatial metadata for {endpoint.address}.')
        return redirect(reverse('plugins:netbox_plant_graph:coordinate_layout'))
