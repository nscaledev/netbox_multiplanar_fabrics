from collections import Counter
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView

from netbox_plant_graph.api import serializers as api_serializers
from netbox_plant_graph.models import (
    AuditEvent,
    Fabric,
    OperationRun,
    OpticalLane,
    Plane,
    StampRun,
    StampTemplate,
    SuppressionRule,
)
from netbox_plant_graph.services.audit import (
    FINDING_EVENT_TYPE,
    FINDING_STATUS_RESOLVED,
    FINDING_STATUS_SUPPRESSED,
    InvalidDisjointnessExceptionTransition,
    InvalidFindingTransition,
    acknowledge_audit_finding,
    approve_disjointness_exception,
    expire_disjointness_exception,
    get_audit_finding,
    list_audit_findings,
    reactivate_disjointness_exception,
    request_disjointness_exception,
    resolve_audit_finding,
    reopen_audit_finding,
    start_audit_finding_remediation,
    suppress_audit_finding,
    unsuppress_audit_finding,
)
from netbox_plant_graph.services.plan_execution import (
    execute_template_plan,
    rollback_stamp_run,
)
from netbox_plant_graph.services.resolver import resolve_optical_lane_path
from netbox_plant_graph.services.stamp_preview import build_v2_stamp_template_preview
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


class RootView(APIRootView):
    def get_view_name(self):
        return 'multiplanar-fabrics'


def _build_viewset(spec):
    serializer_class = getattr(api_serializers, spec.serializer_name)
    return type(
        spec.viewset_name,
        (NetBoxModelViewSet,),
        {
            'queryset': spec.model.objects.all(),
            'serializer_class': serializer_class,
        },
    )


for _spec in V2_OBJECT_SPECS:
    globals()[_spec.viewset_name] = _build_viewset(_spec)


ACTIVE_FINDING_STATUSES = frozenset({'open', 'acknowledged', 'in_progress', FINDING_STATUS_SUPPRESSED})


def _validated(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _resolve_fabric_plane_scope(*, fabric_id=None, plane_id=None):
    fabric = None
    plane = None

    if fabric_id is not None:
        fabric = Fabric.objects.filter(pk=fabric_id).first()
        if fabric is None:
            raise ValidationError({'fabric': f'Fabric {fabric_id!r} was not found.'})

    if plane_id is not None:
        plane = Plane.objects.select_related('fabric').filter(pk=plane_id).first()
        if plane is None:
            raise ValidationError({'plane': f'Plane {plane_id!r} was not found.'})
        if fabric is not None and plane.fabric_id != fabric.pk:
            raise ValidationError({'plane': 'Plane must belong to the selected fabric.'})
        if fabric is None:
            fabric = plane.fabric

    return fabric, plane


def _finding_timestamp(raw_value, *, fallback):
    if isinstance(raw_value, str):
        parsed = parse_datetime(raw_value)
        if parsed is not None:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
    return fallback


def _finding_to_record(finding):
    return {
        'id': finding.finding_id,
        'fabric_id': finding.fabric_id,
        'plane_id': finding.plane_id,
        'finding_type': finding.finding_type,
        'severity': finding.severity,
        'status': finding.status,
        'suppressed': finding.suppressed,
        'message': finding.message,
        'first_seen_at': finding.first_seen_at or '',
        'last_seen_at': finding.last_seen_at or '',
        'suppression_rule_id': (finding.lifecycle or {}).get('suppression_rule_id'),
    }


def _run_row(run):
    return {
        'id': run.pk,
        'profile': run.profile,
        'status': run.status,
        'fabric_id': run.fabric_id,
        'initiated_by_id': run.initiated_by_id,
        'started_at': run.started_at,
        'completed_at': run.completed_at,
    }


def _serialize_operation_runs(queryset):
    serializer = api_serializers.OperationRunSummaryItemSerializer(
        [_run_row(run) for run in queryset],
        many=True,
    )
    return serializer.data


def _suppression_rule_to_record(rule: SuppressionRule) -> dict:
    return {
        'id': rule.pk,
        'fabric_id': rule.fabric_id,
        'plane_id': rule.plane_id,
        'optical_lane_id': rule.optical_lane_id,
        'path_hop_object_type': rule.path_hop_object_type or '',
        'path_hop_object_id': rule.path_hop_object_id,
        'policy_key': rule.policy_key or '',
        'status': rule.status,
        'reason': rule.reason or '',
        'expires_at': rule.expires_at,
        'revoked_at': rule.revoked_at,
        'approved_at': rule.approved_at,
        'approved_by_id': rule.approved_by_id,
        'metadata': dict(rule.metadata or {}),
    }


class PathQueryAPIView(APIView):
    queryset = OpticalLane.objects.all()

    def get(self, request):
        query_serializer = api_serializers.PathQueryRequestSerializer(data=request.query_params)
        if not query_serializer.is_valid():
            if 'source_lane' in query_serializer.errors and not request.query_params.get('source_lane'):
                return Response(
                    {'detail': 'source_lane query parameter is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(query_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        source_lane_id = query_serializer.validated_data['source_lane']
        destination_lane_id = query_serializer.validated_data.get('destination_lane')

        source_lane = OpticalLane.objects.filter(pk=source_lane_id).first()
        if source_lane is None:
            return Response(
                {'detail': f'Source lane {source_lane_id!r} was not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        destination_lane = None
        if destination_lane_id is not None:
            destination_lane = OpticalLane.objects.filter(pk=destination_lane_id).first()
            if destination_lane is None:
                return Response(
                    {'detail': f'Destination lane {destination_lane_id!r} was not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        resolved_path = resolve_optical_lane_path(source=source_lane, destination=destination_lane, actor=request.user)
        serializer = api_serializers.PathQueryResponseSerializer(resolved_path)
        return Response(serializer.data)


class SuppressionSummaryAPIView(APIView):
    queryset = SuppressionRule.objects.all()

    def get(self, request):
        validated = _validated(api_serializers.FabricQuerySerializer, request.query_params)
        queryset = SuppressionRule.objects.order_by('-created', '-pk')
        if validated.get('fabric') is not None:
            queryset = queryset.filter(fabric_id=validated['fabric'])
        serializer = api_serializers.SuppressionSummaryItemSerializer(queryset[:2000], many=True)
        return Response(serializer.data)


class AuditTimelineAPIView(APIView):
    queryset = AuditEvent.objects.all()

    def get(self, request):
        validated = _validated(api_serializers.FabricQuerySerializer, request.query_params)
        queryset = AuditEvent.objects.order_by('-created', '-pk')
        if validated.get('fabric') is not None:
            queryset = queryset.filter(fabric_id=validated['fabric'])
        serializer = api_serializers.AuditTimelineItemSerializer(queryset[:2000], many=True)
        return Response(serializer.data)


class WorkflowSummaryAPIView(APIView):
    queryset = AuditEvent.objects.all()

    def get(self, request):
        validated = _validated(api_serializers.WorkflowSummaryQuerySerializer, request.query_params)
        fabric, plane = _resolve_fabric_plane_scope(
            fabric_id=validated.get('fabric'),
            plane_id=validated.get('plane'),
        )

        findings = list(list_audit_findings(fabric=fabric, plane=plane))
        now = timezone.now()

        active_findings = [finding for finding in findings if finding.status in ACTIVE_FINDING_STATUSES]
        resolved_findings = [finding for finding in findings if finding.status == FINDING_STATUS_RESOLVED]

        status_counts = Counter(finding.status for finding in active_findings)
        severity_counts = Counter(finding.severity for finding in active_findings)
        type_counts = Counter(finding.finding_type for finding in active_findings)

        stale_7d = 0
        stale_30d = 0
        for finding in active_findings:
            first_seen = _finding_timestamp(finding.first_seen_at, fallback=finding.event.created)
            if first_seen <= now - timedelta(days=7):
                stale_7d += 1
            if first_seen <= now - timedelta(days=30):
                stale_30d += 1

        oldest_active_findings = sorted(
            active_findings,
            key=lambda finding: (
                _finding_timestamp(finding.first_seen_at, fallback=finding.event.created),
                finding.finding_id,
            ),
        )[:10]
        oldest_rows = []
        for finding in oldest_active_findings:
            first_seen = _finding_timestamp(finding.first_seen_at, fallback=finding.event.created)
            oldest_rows.append(
                {
                    **_finding_to_record(finding),
                    'age_days': (now.date() - first_seen.date()).days if first_seen is not None else None,
                }
            )

        transition_events = AuditEvent.objects.filter(event_type='suppression_change').order_by('-created', '-pk')
        if fabric is not None:
            transition_events = transition_events.filter(fabric_id=fabric.pk)
        finding_ids = {finding.finding_id for finding in findings}
        recent_events = []
        for event in transition_events[:500]:
            payload = event.payload or {}
            metadata = event.metadata or {}
            finding_id = payload.get('finding_id') or payload.get('finding_pk') or metadata.get('finding_pk')
            if plane is not None and finding_id not in finding_ids:
                continue
            recent_events.append(
                {
                    'id': event.pk,
                    'finding_id': finding_id,
                    'event_type': payload.get('finding_action') or payload.get('action') or event.event_type,
                    'outcome': event.outcome,
                    'actor_id': event.actor_id,
                    'created': event.created,
                    'message': event.message,
                    'old_status': payload.get('old_status'),
                    'new_status': payload.get('new_status'),
                }
            )
            if len(recent_events) >= 10:
                break

        suppression_qs = SuppressionRule.objects.filter(
            status='active',
            revoked_at__isnull=True,
            expires_at__isnull=False,
        ).order_by('expires_at', 'pk')
        if fabric is not None:
            suppression_qs = suppression_qs.filter(fabric_id=fabric.pk)
        if plane is not None:
            suppression_qs = suppression_qs.filter(
                Q(plane_id=plane.pk) | Q(plane_id__isnull=True, optical_lane__plane_id=plane.pk)
            )
        expiring_suppressions = [
            {
                'id': rule.pk,
                'fabric_id': rule.fabric_id,
                'plane_id': rule.plane_id,
                'optical_lane_id': rule.optical_lane_id,
                'finding_id': (rule.metadata or {}).get('finding_id') or (rule.metadata or {}).get('finding_pk'),
                'reason': rule.reason,
                'expires_at': rule.expires_at,
                'remaining_days': (rule.expires_at.date() - now.date()).days if rule.expires_at is not None else None,
            }
            for rule in suppression_qs[:10]
        ]

        run_qs = OperationRun.objects.order_by('-created', '-pk')
        if fabric is not None:
            run_qs = run_qs.filter(fabric_id=fabric.pk)

        payload = {
            'fabric_id': getattr(fabric, 'pk', None),
            'plane_id': getattr(plane, 'pk', None),
            'total_findings': len(findings),
            'active_findings': len(active_findings),
            'resolved_findings': len(resolved_findings),
            'suppressed_findings': status_counts.get(FINDING_STATUS_SUPPRESSED, 0),
            'stale_findings_7d': stale_7d,
            'stale_findings_30d': stale_30d,
            'status_counts': [{'name': name, 'value': value} for name, value in sorted(status_counts.items())],
            'severity_counts': [{'name': name, 'value': value} for name, value in sorted(severity_counts.items())],
            'type_counts': [{'name': name, 'value': value} for name, value in sorted(type_counts.items())],
            'recent_runs': _serialize_operation_runs(run_qs[:10]),
            'recent_events': recent_events,
            'oldest_active_findings': oldest_rows,
            'expiring_suppressions': expiring_suppressions,
        }
        serializer = api_serializers.WorkflowSummaryResponseSerializer(payload)
        return Response(serializer.data)


class WorkflowFindingsAPIView(APIView):
    queryset = AuditEvent.objects.all()

    def get(self, request):
        validated = _validated(api_serializers.WorkflowFindingsQuerySerializer, request.query_params)
        fabric, plane = _resolve_fabric_plane_scope(
            fabric_id=validated.get('fabric'),
            plane_id=validated.get('plane'),
        )

        findings = list_audit_findings(
            fabric=fabric,
            plane=plane,
            status=validated.get('status'),
            severity=validated.get('severity'),
            finding_type=validated.get('finding_type'),
            suppressed=validated.get('suppressed') if 'suppressed' in request.query_params else None,
        )
        limit = validated.get('limit', 200)
        serializer = api_serializers.WorkflowFindingRecordSerializer(
            [_finding_to_record(finding) for finding in findings[:limit]],
            many=True,
        )
        return Response(serializer.data)


class WorkflowFindingDetailAPIView(APIView):
    queryset = AuditEvent.objects.all()

    def get(self, request, pk):
        try:
            finding = get_audit_finding(finding=pk)
        except AuditEvent.DoesNotExist:
            return Response({'detail': f'Workflow finding {pk!r} was not found.'}, status=status.HTTP_404_NOT_FOUND)

        transition_qs = AuditEvent.objects.filter(event_type='suppression_change').order_by('-created', '-pk')
        if finding.fabric_id is not None:
            transition_qs = transition_qs.filter(fabric_id=finding.fabric_id)

        transitions = []
        for event in transition_qs[:500]:
            payload = event.payload or {}
            metadata = event.metadata or {}
            finding_id = payload.get('finding_id') or payload.get('finding_pk') or metadata.get('finding_pk')
            if finding_id != finding.finding_id:
                continue
            transitions.append(
                {
                    'id': event.pk,
                    'finding_id': finding_id,
                    'event_type': payload.get('finding_action') or payload.get('action') or event.event_type,
                    'outcome': event.outcome,
                    'actor_id': event.actor_id,
                    'created': event.created,
                    'message': event.message,
                    'old_status': payload.get('old_status'),
                    'new_status': payload.get('new_status'),
                }
            )
            if len(transitions) >= 20:
                break

        payload = {
            'finding': _finding_to_record(finding),
            'transitions': transitions,
        }
        serializer = api_serializers.WorkflowFindingDetailResponseSerializer(payload)
        return Response(serializer.data)


class WorkflowFindingActionAPIView(APIView):
    queryset = AuditEvent.objects.all()
    action_name = ''

    def _apply(self, *, finding_event, actor, payload):
        note = (
            payload.get('resolution_summary')
            or payload.get('reason')
            or payload.get('note')
            or ''
        ).strip()
        if self.action_name == 'acknowledge':
            return acknowledge_audit_finding(finding=finding_event, actor=actor, note=note)
        if self.action_name == 'start_remediation':
            return start_audit_finding_remediation(finding=finding_event, actor=actor, note=note)
        if self.action_name == 'suppress':
            return suppress_audit_finding(
                finding=finding_event,
                actor=actor,
                reason=(payload.get('reason') or note).strip(),
                days=payload.get('days'),
            )
        if self.action_name == 'unsuppress':
            return unsuppress_audit_finding(finding=finding_event, actor=actor, reason=(payload.get('reason') or note).strip())
        if self.action_name == 'resolve':
            return resolve_audit_finding(finding=finding_event, actor=actor, note=(payload.get('resolution_summary') or note).strip())
        if self.action_name == 'reopen':
            return reopen_audit_finding(finding=finding_event, actor=actor, note=note)
        raise ValidationError({'detail': f'Unsupported finding action {self.action_name!r}.'})

    def post(self, request, pk):
        finding_event = AuditEvent.objects.filter(pk=pk).first()
        if finding_event is None or finding_event.event_type != FINDING_EVENT_TYPE:
            return Response({'detail': f'Workflow finding {pk!r} was not found.'}, status=status.HTTP_404_NOT_FOUND)

        payload = _validated(api_serializers.WorkflowFindingActionRequestSerializer, request.data)
        try:
            finding = self._apply(finding_event=finding_event, actor=request.user, payload=payload)
        except InvalidFindingTransition as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = api_serializers.WorkflowFindingActionResponseSerializer(
            {
                'finding': _finding_to_record(finding),
            }
        )
        return Response(serializer.data)


class WorkflowFindingAcknowledgeAPIView(WorkflowFindingActionAPIView):
    action_name = 'acknowledge'


class WorkflowFindingStartRemediationAPIView(WorkflowFindingActionAPIView):
    action_name = 'start_remediation'


class WorkflowFindingSuppressAPIView(WorkflowFindingActionAPIView):
    action_name = 'suppress'


class WorkflowFindingUnsuppressAPIView(WorkflowFindingActionAPIView):
    action_name = 'unsuppress'


class WorkflowFindingResolveAPIView(WorkflowFindingActionAPIView):
    action_name = 'resolve'


class WorkflowFindingReopenAPIView(WorkflowFindingActionAPIView):
    action_name = 'reopen'


class DisjointnessExceptionRequestAPIView(APIView):
    queryset = SuppressionRule.objects.all()

    def post(self, request):
        payload = _validated(api_serializers.DisjointnessExceptionRequestSerializer, request.data)
        try:
            rule = request_disjointness_exception(
                fabric=payload['fabric'],
                actor=request.user,
                plane=payload.get('plane'),
                optical_lane=payload.get('optical_lane'),
                path_hop_object_type=(payload.get('path_hop_object_type') or '').strip(),
                path_hop_object_id=payload.get('path_hop_object_id'),
                policy_key=(payload.get('policy_key') or '').strip(),
                reason=(payload.get('reason') or '').strip(),
                expires_at=payload.get('expires_at'),
                metadata=payload.get('metadata') or {},
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = api_serializers.DisjointnessExceptionResponseSerializer(_suppression_rule_to_record(rule))
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class DisjointnessExceptionActionAPIView(APIView):
    queryset = SuppressionRule.objects.all()
    action_name = ''

    def _apply(self, *, rule, actor, payload):
        comment = (payload.get('comment') or '').strip()
        if self.action_name == 'approve':
            return approve_disjointness_exception(exception=rule, actor=actor, comment=comment)
        if self.action_name == 'expire':
            return expire_disjointness_exception(exception=rule, actor=actor, comment=comment)
        if self.action_name == 'reactivate':
            return reactivate_disjointness_exception(
                exception=rule,
                actor=actor,
                comment=comment,
                expires_at=payload.get('expires_at'),
            )
        raise ValidationError({'detail': f'Unsupported disjointness action {self.action_name!r}.'})

    def post(self, request, pk):
        rule = SuppressionRule.objects.filter(pk=pk).first()
        if rule is None:
            return Response({'detail': f'Disjointness exception {pk!r} was not found.'}, status=status.HTTP_404_NOT_FOUND)

        payload = _validated(api_serializers.DisjointnessExceptionActionRequestSerializer, request.data)
        try:
            result = self._apply(rule=rule, actor=request.user, payload=payload)
        except InvalidDisjointnessExceptionTransition as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = api_serializers.DisjointnessExceptionResponseSerializer(_suppression_rule_to_record(result))
        return Response(serializer.data)


class DisjointnessExceptionApproveAPIView(DisjointnessExceptionActionAPIView):
    action_name = 'approve'


class DisjointnessExceptionExpireAPIView(DisjointnessExceptionActionAPIView):
    action_name = 'expire'


class DisjointnessExceptionReactivateAPIView(DisjointnessExceptionActionAPIView):
    action_name = 'reactivate'


class WorkflowRunsAPIView(APIView):
    queryset = OperationRun.objects.all()

    def get(self, request):
        validated = _validated(api_serializers.WorkflowRunsQuerySerializer, request.query_params)
        fabric_id = validated.get('fabric')
        limit = validated.get('limit', 500)

        queryset = OperationRun.objects.order_by('-created', '-pk')
        if fabric_id is not None:
            queryset = queryset.filter(fabric_id=fabric_id)

        return Response(_serialize_operation_runs(queryset[:limit]))


class OperationRunSummaryAPIView(WorkflowRunsAPIView):
    pass


class StampTemplateExecuteAPIView(APIView):
    queryset = StampTemplate.objects.all()

    def post(self, request, pk):
        template = StampTemplate.objects.filter(pk=pk).first()
        if template is None:
            return Response({'detail': f'StampTemplate {pk!r} was not found.'}, status=status.HTTP_404_NOT_FOUND)

        payload = _validated(api_serializers.StampTemplateExecuteRequestSerializer, request.data)
        try:
            result = execute_template_plan(
                template=template,
                fabric_name=payload['fabric_name'],
                fabric_slug=payload['fabric_slug'],
                source_bindings=payload.get('source_bindings') or {},
                creation_options=payload.get('creation_options') or {},
                actor=request.user,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = api_serializers.StampTemplateExecuteResponseSerializer(
            {
                'template_id': result.template.pk,
                'fabric_id': result.fabric.pk,
                'stamp_run_id': result.stamp_run.pk,
                'rollback_eligible': result.rollback_eligible,
                'status': result.stamp_run.status,
            }
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class StampRunRollbackAPIView(APIView):
    queryset = StampRun.objects.all()

    def post(self, request, pk):
        stamp_run = StampRun.objects.filter(pk=pk).first()
        if stamp_run is None:
            return Response({'detail': f'StampRun {pk!r} was not found.'}, status=status.HTTP_404_NOT_FOUND)

        payload = _validated(api_serializers.StampRunRollbackRequestSerializer, request.data)
        result = rollback_stamp_run(
            stamp_run=stamp_run,
            actor=request.user,
            delete_fabric_as_primitive=payload.get('delete_fabric_as_primitive', True),
        )
        serializer = api_serializers.StampRunRollbackResponseSerializer(
            {
                'stamp_run_id': result.stamp_run.pk,
                'already_rolled_back': result.already_rolled_back,
                'rollback_mode': result.rollback_mode,
                'deleted_counts': result.deleted_counts,
                'deleted_total': result.deleted_total,
            }
        )
        return Response(serializer.data)


class StampPreviewAPIView(APIView):
    queryset = StampTemplate.objects.all()

    def post(self, request):
        validated = _validated(api_serializers.StampPreviewRequestSerializer, request.data)
        template = StampTemplate.objects.filter(pk=validated['template_id']).first()
        if template is None:
            return Response(
                {'detail': f"StampTemplate {validated['template_id']!r} was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        parameters = validated.get('parameters') or {}
        try:
            preview = build_v2_stamp_template_preview(template, parameters)
        except (TypeError, ValueError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = api_serializers.StampPreviewResponseSerializer(preview)
        return Response(serializer.data)


__all__ = (
    'RootView',
    'PathQueryAPIView',
    'SuppressionSummaryAPIView',
    'AuditTimelineAPIView',
    'WorkflowSummaryAPIView',
    'WorkflowFindingsAPIView',
    'WorkflowFindingDetailAPIView',
    'WorkflowFindingAcknowledgeAPIView',
    'WorkflowFindingStartRemediationAPIView',
    'WorkflowFindingSuppressAPIView',
    'WorkflowFindingUnsuppressAPIView',
    'WorkflowFindingResolveAPIView',
    'WorkflowFindingReopenAPIView',
    'DisjointnessExceptionRequestAPIView',
    'DisjointnessExceptionApproveAPIView',
    'DisjointnessExceptionExpireAPIView',
    'DisjointnessExceptionReactivateAPIView',
    'WorkflowRunsAPIView',
    'OperationRunSummaryAPIView',
    'StampTemplateExecuteAPIView',
    'StampRunRollbackAPIView',
    'StampPreviewAPIView',
) + tuple(spec.viewset_name for spec in V2_OBJECT_SPECS)
