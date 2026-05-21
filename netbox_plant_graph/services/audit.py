from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone
from netbox.context import current_request

from netbox_plant_graph.models import AuditEvent, Fabric, OpticalLane, Plane, SuppressionRule


FINDING_EVENT_TYPE = 'policy_eval'
FINDING_TRANSITION_EVENT_TYPE = 'suppression_change'
FINDING_LIFECYCLE_KEY = 'finding_lifecycle'
DISJOINTNESS_EXCEPTION_KEY = 'disjointness_exception'
DEFAULT_DISJOINTNESS_POLICY_KEY = 'disjointness'

FINDING_STATUS_OPEN = 'open'
FINDING_STATUS_ACKNOWLEDGED = 'acknowledged'
FINDING_STATUS_IN_PROGRESS = 'in_progress'
FINDING_STATUS_SUPPRESSED = 'suppressed'
FINDING_STATUS_RESOLVED = 'resolved'

FINDING_STATUSES = frozenset(
    {
        FINDING_STATUS_OPEN,
        FINDING_STATUS_ACKNOWLEDGED,
        FINDING_STATUS_IN_PROGRESS,
        FINDING_STATUS_SUPPRESSED,
        FINDING_STATUS_RESOLVED,
    }
)

FINDING_ACTION_TRANSITIONS = {
    'acknowledge': {
        'from': frozenset({FINDING_STATUS_OPEN}),
        'to': FINDING_STATUS_ACKNOWLEDGED,
    },
    'start_remediation': {
        'from': frozenset({FINDING_STATUS_OPEN, FINDING_STATUS_ACKNOWLEDGED}),
        'to': FINDING_STATUS_IN_PROGRESS,
    },
    'suppress': {
        'from': frozenset({FINDING_STATUS_OPEN, FINDING_STATUS_ACKNOWLEDGED, FINDING_STATUS_IN_PROGRESS}),
        'to': FINDING_STATUS_SUPPRESSED,
    },
    'unsuppress': {
        'from': frozenset({FINDING_STATUS_SUPPRESSED}),
        'to': FINDING_STATUS_OPEN,
    },
    'resolve': {
        'from': frozenset({FINDING_STATUS_OPEN, FINDING_STATUS_ACKNOWLEDGED, FINDING_STATUS_IN_PROGRESS}),
        'to': FINDING_STATUS_RESOLVED,
    },
    'reopen': {
        'from': frozenset({FINDING_STATUS_SUPPRESSED, FINDING_STATUS_RESOLVED}),
        'to': FINDING_STATUS_OPEN,
    },
}


class InvalidFindingTransition(ValueError):
    pass


class InvalidDisjointnessExceptionTransition(ValueError):
    pass


@dataclass(frozen=True)
class SuppressionDecision:
    suppressed: bool
    rule: SuppressionRule | None = None
    reason: str = ''


@dataclass(frozen=True)
class AuditFinding:
    finding_id: int
    event: AuditEvent
    fabric_id: int | None
    plane_id: int | None
    finding_type: str
    severity: str
    status: str
    suppressed: bool
    message: str
    payload: dict[str, Any]
    metadata: dict[str, Any]
    lifecycle: dict[str, Any]
    first_seen_at: Any
    last_seen_at: Any


def _normalize_actor(actor):
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        return None
    return actor


def _fallback_changelog_user():
    User = get_user_model()
    user = (
        User.objects.filter(is_active=True, is_superuser=True).order_by('pk').first()
        or User.objects.filter(is_active=True, is_staff=True).order_by('pk').first()
        or User.objects.filter(is_active=True).order_by('pk').first()
    )
    if user is not None:
        return user

    username_field = getattr(User, 'USERNAME_FIELD', 'username')
    system_identifier = 'netbox-plant-graph-system'
    lookup = {username_field: system_identifier}
    system_user, created = User.objects.get_or_create(**lookup)
    if created and hasattr(system_user, 'set_unusable_password'):
        system_user.set_unusable_password()
        update_fields = []
        if hasattr(system_user, 'password'):
            update_fields.append('password')
        if hasattr(system_user, 'is_active'):
            system_user.is_active = True
            update_fields.append('is_active')
        if update_fields:
            system_user.save(update_fields=update_fields)
    return system_user


def _normalize_fabric(fabric=None):
    if isinstance(fabric, Fabric):
        return fabric
    if fabric is None:
        return None
    return Fabric.objects.filter(pk=getattr(fabric, 'pk', fabric)).first()


def _normalize_plane(plane=None):
    if isinstance(plane, Plane):
        return plane
    if plane is None:
        return None
    return Plane.objects.filter(pk=getattr(plane, 'pk', plane)).first()


def _normalize_lane(lane=None):
    if isinstance(lane, OpticalLane):
        return lane
    if lane is None:
        return None
    return OpticalLane.objects.filter(pk=getattr(lane, 'pk', lane)).first()


def _normalize_status(status: str | None) -> str:
    if status in FINDING_STATUSES:
        return status
    return FINDING_STATUS_OPEN


def _coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'1', 'true', 'yes', 'y'}:
            return True
        if lowered in {'0', 'false', 'no', 'n'}:
            return False
    return None


def _finding_lifecycle_for_event(event: AuditEvent) -> dict[str, Any]:
    metadata = dict(event.metadata or {})
    payload = dict(event.payload or {})
    lifecycle = dict(metadata.get(FINDING_LIFECYCLE_KEY) or {})
    status = _normalize_status(
        lifecycle.get('status')
        or payload.get('finding_status')
        or payload.get('status')
    )
    lifecycle['status'] = status
    lifecycle['suppressed'] = bool(lifecycle.get('suppressed', status == FINDING_STATUS_SUPPRESSED))
    lifecycle.setdefault('first_seen_at', event.created.isoformat() if event.created else '')
    lifecycle.setdefault('last_seen_at', event.last_updated.isoformat() if event.last_updated else '')
    return lifecycle


def _plane_id_from_finding(event: AuditEvent, lifecycle: dict[str, Any]) -> int | None:
    payload = dict(event.payload or {})
    payload_metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}

    raw_plane_id = (
        lifecycle.get('plane_id')
        or payload.get('plane_id')
        or payload_metadata.get('plane_id')
    )
    if raw_plane_id is not None:
        try:
            return int(raw_plane_id)
        except (TypeError, ValueError):
            return None

    plane_ids = payload_metadata.get('plane_ids')
    if isinstance(plane_ids, (list, tuple)) and plane_ids:
        first = plane_ids[0]
        try:
            return int(first)
        except (TypeError, ValueError):
            return None
    return None


def _normalize_finding(event: AuditEvent) -> AuditFinding:
    payload = dict(event.payload or {})
    metadata = dict(event.metadata or {})
    lifecycle = _finding_lifecycle_for_event(event)

    finding_type = str(
        payload.get('finding_type')
        or lifecycle.get('finding_type')
        or 'unknown'
    )
    severity = str(
        payload.get('severity')
        or lifecycle.get('severity')
        or 'warning'
    )
    status = _normalize_status(lifecycle.get('status'))
    suppressed = bool(lifecycle.get('suppressed', status == FINDING_STATUS_SUPPRESSED))
    message = event.message or str(payload.get('message') or '')
    plane_id = _plane_id_from_finding(event, lifecycle)

    first_seen_at = lifecycle.get('first_seen_at') or (event.created.isoformat() if event.created else '')
    last_seen_at = lifecycle.get('last_seen_at') or (event.last_updated.isoformat() if event.last_updated else '')

    return AuditFinding(
        finding_id=event.pk,
        event=event,
        fabric_id=event.fabric_id,
        plane_id=plane_id,
        finding_type=finding_type,
        severity=severity,
        status=status,
        suppressed=suppressed,
        message=message,
        payload=payload,
        metadata=metadata,
        lifecycle=lifecycle,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
    )


def _finding_event(finding) -> AuditEvent:
    if isinstance(finding, AuditEvent):
        event = finding
    else:
        event = AuditEvent.objects.filter(pk=getattr(finding, 'pk', finding)).first()
    if event is None or event.event_type != FINDING_EVENT_TYPE:
        raise AuditEvent.DoesNotExist('Audit finding was not found.')
    return event


def record_audit_event(
    *,
    event_type: str,
    fabric=None,
    actor=None,
    subject=None,
    outcome: str = 'ok',
    message: str = '',
    payload: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    subject_type = None
    subject_id = None
    if subject is not None:
        subject_type = ContentType.objects.get_for_model(subject, for_concrete_model=False)
        subject_id = subject.pk
    actor = _normalize_actor(actor)

    # NetBox change logging stores request.user on ObjectChange.user. In deployments
    # where plugin routes are intentionally unauthenticated, request.user may be
    # AnonymousUser, which cannot be assigned to that FK. When that happens, shadow
    # the request context with user=None just for this write.
    request_token = None
    request = current_request.get()
    if request is not None and not getattr(getattr(request, 'user', None), 'is_authenticated', False):
        changelog_user = actor if actor is not None else _fallback_changelog_user()
        request_token = current_request.set(
            SimpleNamespace(id=getattr(request, 'id', None), user=changelog_user)
        )

    try:
        return AuditEvent.objects.create(
            fabric=fabric,
            event_type=event_type,
            actor=actor,
            subject_type=subject_type,
            subject_id=subject_id,
            outcome=outcome,
            message=message,
            payload=payload or {},
            metadata=metadata or {},
        )
    finally:
        if request_token is not None:
            current_request.reset(request_token)


def _effective_rules_for_lane(lane: OpticalLane):
    now = timezone.now()
    queryset = SuppressionRule.objects.filter(
        fabric_id=lane.fabric_id,
        status='active',
        revoked_at__isnull=True,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )
    return queryset.select_related('plane', 'optical_lane').order_by('-created', '-pk')


def suppression_for_lane(lane: OpticalLane) -> SuppressionDecision:
    rules = _effective_rules_for_lane(lane)
    lane_rule = rules.filter(optical_lane_id=lane.pk).first()
    if lane_rule is not None:
        return SuppressionDecision(True, lane_rule, 'Suppressed by optical-lane rule.')

    if lane.plane_id is not None:
        plane_rule = rules.filter(plane_id=lane.plane_id, optical_lane__isnull=True).first()
        if plane_rule is not None:
            return SuppressionDecision(True, plane_rule, 'Suppressed by plane rule.')

    policy_rule = rules.filter(policy_key='path_resolution').first()
    if policy_rule is not None:
        return SuppressionDecision(True, policy_rule, 'Suppressed by path-resolution policy rule.')

    fabric_rule = rules.filter(
        plane__isnull=True,
        optical_lane__isnull=True,
        path_hop_object_type='',
        path_hop_object_id__isnull=True,
        policy_key='',
    ).first()
    if fabric_rule is not None:
        return SuppressionDecision(True, fabric_rule, 'Suppressed by fabric-wide rule.')

    return SuppressionDecision(False, None, '')


def suppression_for_path_hop(*, lane: OpticalLane, object_type: str, object_id: int) -> SuppressionDecision:
    now = timezone.now()
    rule = (
        SuppressionRule.objects.filter(
            fabric_id=lane.fabric_id,
            status='active',
            revoked_at__isnull=True,
            path_hop_object_type=object_type,
            path_hop_object_id=object_id,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .order_by('-created', '-pk')
        .first()
    )
    if rule is None:
        return SuppressionDecision(False, None, '')
    return SuppressionDecision(True, rule, f'Suppressed by path-hop rule for {object_type}:{object_id}.')


def list_audit_findings(
    *,
    fabric=None,
    plane=None,
    status: str | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    suppressed: bool | str | None = None,
) -> tuple[AuditFinding, ...]:
    queryset = AuditEvent.objects.filter(event_type=FINDING_EVENT_TYPE).select_related('fabric').order_by('-created', '-pk')
    selected_fabric = _normalize_fabric(fabric)
    if selected_fabric is not None:
        queryset = queryset.filter(fabric_id=selected_fabric.pk)

    plane_id = None
    selected_plane = _normalize_plane(plane)
    if selected_plane is not None:
        plane_id = selected_plane.pk
    elif plane is not None:
        try:
            plane_id = int(getattr(plane, 'pk', plane))
        except (TypeError, ValueError):
            plane_id = None

    status_filter = status.strip().lower() if isinstance(status, str) and status.strip() else None
    severity_filter = severity.strip().lower() if isinstance(severity, str) and severity.strip() else None
    finding_type_filter = finding_type.strip().lower() if isinstance(finding_type, str) and finding_type.strip() else None
    suppressed_filter = _coerce_bool(suppressed)

    rows: list[AuditFinding] = []
    for event in queryset:
        row = _normalize_finding(event)
        if plane_id is not None and row.plane_id != plane_id:
            continue
        if status_filter is not None and row.status.lower() != status_filter:
            continue
        if severity_filter is not None and row.severity.lower() != severity_filter:
            continue
        if finding_type_filter is not None and row.finding_type.lower() != finding_type_filter:
            continue
        if suppressed_filter is not None and row.suppressed != suppressed_filter:
            continue
        rows.append(row)
    return tuple(rows)


def get_audit_finding(*, finding) -> AuditFinding:
    return _normalize_finding(_finding_event(finding))


def transition_audit_finding(
    *,
    finding,
    action: str,
    actor=None,
    comment: str = '',
    reason: str = '',
    resolution_summary: str = '',
    expires_at=None,
    suppression_rule: SuppressionRule | None = None,
) -> AuditFinding:
    event = _finding_event(finding)
    rule = FINDING_ACTION_TRANSITIONS.get(action)
    if rule is None:
        raise InvalidFindingTransition(f'Unsupported finding action: {action!r}.')

    lifecycle = _finding_lifecycle_for_event(event)
    old_status = _normalize_status(lifecycle.get('status'))
    if old_status not in rule['from']:
        raise InvalidFindingTransition(
            f'Cannot apply "{action}" to finding #{event.pk} while status is "{old_status}".'
        )

    new_status = rule['to']
    now = timezone.now()
    actor = _normalize_actor(actor)
    actor_id = getattr(actor, 'pk', None)

    lifecycle['status'] = new_status
    lifecycle['suppressed'] = new_status == FINDING_STATUS_SUPPRESSED
    lifecycle['last_transition'] = action
    lifecycle['last_transition_at'] = now.isoformat()
    lifecycle['last_transition_by_id'] = actor_id
    lifecycle['last_seen_at'] = now.isoformat()

    if action == 'acknowledge':
        lifecycle.setdefault('acknowledged_at', now.isoformat())
        if actor_id is not None:
            lifecycle['acknowledged_by_id'] = actor_id
    elif action == 'start_remediation':
        lifecycle.setdefault('remediation_started_at', now.isoformat())
        if actor_id is not None:
            lifecycle['assigned_to_id'] = actor_id
    elif action == 'suppress':
        lifecycle['suppressed'] = True
        lifecycle['suppressed_at'] = now.isoformat()
        lifecycle['suppression_reason'] = reason or comment
        if expires_at is not None:
            lifecycle['suppressed_until'] = expires_at.isoformat()
        if suppression_rule is not None:
            lifecycle['suppression_rule_id'] = suppression_rule.pk
    elif action == 'unsuppress':
        lifecycle['suppressed'] = False
        lifecycle['unsuppressed_at'] = now.isoformat()
        lifecycle['unsuppression_reason'] = reason or comment
        lifecycle.pop('suppressed_until', None)
    elif action == 'resolve':
        lifecycle['resolved_at'] = now.isoformat()
        summary = resolution_summary or comment or reason
        if summary:
            lifecycle['resolution_summary'] = summary
    elif action == 'reopen':
        lifecycle['suppressed'] = False
        lifecycle['reopened_at'] = now.isoformat()
        lifecycle.pop('resolved_at', None)

    metadata = dict(event.metadata or {})
    payload = dict(event.payload or {})
    metadata[FINDING_LIFECYCLE_KEY] = lifecycle
    payload['finding_status'] = lifecycle['status']
    payload['suppressed'] = lifecycle['suppressed']
    event.metadata = metadata
    event.payload = payload
    event.save(update_fields=['metadata', 'payload', 'last_updated'])

    note = resolution_summary or reason or comment
    message = f'Finding lifecycle action "{action}" applied.'
    if note:
        message = f'{message} {note}'

    record_audit_event(
        event_type=FINDING_TRANSITION_EVENT_TYPE,
        fabric=event.fabric,
        actor=actor,
        subject=event,
        outcome='ok',
        message=message,
        payload={
            'finding_id': event.pk,
            'exception_action': '',
            'finding_action': action,
            'old_status': old_status,
            'new_status': lifecycle['status'],
            'comment': comment,
            'reason': reason,
        },
        metadata={
            'finding_event_type': FINDING_EVENT_TYPE,
            'finding_status': lifecycle['status'],
        },
    )
    return _normalize_finding(event)


def acknowledge_audit_finding(*, finding, actor=None, note: str = '') -> AuditFinding:
    return transition_audit_finding(
        finding=finding,
        action='acknowledge',
        actor=actor,
        comment=note,
    )


def start_audit_finding_remediation(*, finding, actor=None, note: str = '') -> AuditFinding:
    return transition_audit_finding(
        finding=finding,
        action='start_remediation',
        actor=actor,
        comment=note,
    )


def suppress_audit_finding(
    *,
    finding,
    actor=None,
    reason: str = '',
    days: int | None = None,
    expires_at=None,
) -> AuditFinding:
    suppressed_until = expires_at
    if suppressed_until is None and days is not None:
        suppressed_until = timezone.now() + timedelta(days=max(int(days), 0))
    return transition_audit_finding(
        finding=finding,
        action='suppress',
        actor=actor,
        reason=reason,
        expires_at=suppressed_until,
    )


def unsuppress_audit_finding(*, finding, actor=None, reason: str = '') -> AuditFinding:
    return transition_audit_finding(
        finding=finding,
        action='unsuppress',
        actor=actor,
        reason=reason,
    )


def resolve_audit_finding(*, finding, actor=None, note: str = '') -> AuditFinding:
    return transition_audit_finding(
        finding=finding,
        action='resolve',
        actor=actor,
        resolution_summary=note,
    )


def reopen_audit_finding(*, finding, actor=None, note: str = '') -> AuditFinding:
    return transition_audit_finding(
        finding=finding,
        action='reopen',
        actor=actor,
        comment=note,
    )


def _as_exception_rule(exception) -> SuppressionRule:
    if isinstance(exception, SuppressionRule):
        return exception
    rule = SuppressionRule.objects.filter(pk=getattr(exception, 'pk', exception)).first()
    if rule is None:
        raise SuppressionRule.DoesNotExist('Disjointness exception not found.')
    return rule


def _validate_exception_scope(*, fabric: Fabric, plane: Plane | None, lane: OpticalLane | None):
    if plane is not None and plane.fabric_id != fabric.pk:
        raise ValueError('Plane must belong to the requested fabric.')
    if lane is not None and lane.fabric_id != fabric.pk:
        raise ValueError('Optical lane must belong to the requested fabric.')
    if plane is not None and lane is not None and lane.plane_id is not None and lane.plane_id != plane.pk:
        raise ValueError('Optical lane plane must match selected plane when both are set.')


def _exception_metadata(rule: SuppressionRule) -> dict[str, Any]:
    metadata = dict(rule.metadata or {})
    exception = dict(metadata.get(DISJOINTNESS_EXCEPTION_KEY) or {})
    exception.setdefault('status', rule.status)
    metadata[DISJOINTNESS_EXCEPTION_KEY] = exception
    return metadata


def _set_exception_lifecycle(
    *,
    metadata: dict[str, Any],
    action: str,
    status: str,
    actor_id: int | None,
    comment: str = '',
):
    now = timezone.now()
    exception = dict(metadata.get(DISJOINTNESS_EXCEPTION_KEY) or {})
    exception['status'] = status
    exception['last_action'] = action
    exception['last_action_at'] = now.isoformat()
    exception['last_action_by_id'] = actor_id
    if comment:
        exception['last_comment'] = comment
    metadata[DISJOINTNESS_EXCEPTION_KEY] = exception


def request_disjointness_exception(
    *,
    fabric,
    actor=None,
    plane=None,
    optical_lane=None,
    path_hop_object_type: str = '',
    path_hop_object_id: int | None = None,
    policy_key: str = DEFAULT_DISJOINTNESS_POLICY_KEY,
    reason: str = '',
    expires_at=None,
    metadata: dict[str, Any] | None = None,
) -> SuppressionRule:
    selected_fabric = _normalize_fabric(fabric)
    if selected_fabric is None:
        raise ValueError('A valid fabric is required.')
    selected_plane = _normalize_plane(plane)
    selected_lane = _normalize_lane(optical_lane)
    _validate_exception_scope(fabric=selected_fabric, plane=selected_plane, lane=selected_lane)

    actor = _normalize_actor(actor)
    actor_id = getattr(actor, 'pk', None)
    rule_metadata = dict(metadata or {})
    rule_metadata[DISJOINTNESS_EXCEPTION_KEY] = {
        'status': 'pending',
        'requested_at': timezone.now().isoformat(),
        'requested_by_id': actor_id,
        'policy_scope': policy_key or DEFAULT_DISJOINTNESS_POLICY_KEY,
        'reason': reason,
    }

    rule = SuppressionRule.objects.create(
        fabric=selected_fabric,
        plane=selected_plane,
        optical_lane=selected_lane,
        path_hop_object_type=path_hop_object_type or '',
        path_hop_object_id=path_hop_object_id,
        policy_key=policy_key or DEFAULT_DISJOINTNESS_POLICY_KEY,
        status='pending',
        reason=reason,
        created_by=actor,
        expires_at=expires_at,
        metadata=rule_metadata,
    )

    record_audit_event(
        event_type=FINDING_TRANSITION_EVENT_TYPE,
        fabric=rule.fabric,
        actor=actor,
        subject=rule,
        outcome='pending',
        message='Disjointness exception requested.',
        payload={
            'exception_action': 'request',
            'suppression_rule_id': rule.pk,
            'status': rule.status,
            'policy_key': rule.policy_key,
        },
        metadata={DISJOINTNESS_EXCEPTION_KEY: dict(rule_metadata.get(DISJOINTNESS_EXCEPTION_KEY) or {})},
    )
    return rule


def approve_disjointness_exception(*, exception, actor=None, comment: str = '') -> SuppressionRule:
    rule = _as_exception_rule(exception)
    if rule.status != 'pending':
        raise InvalidDisjointnessExceptionTransition(
            f'Cannot approve exception #{rule.pk} while status is "{rule.status}".'
        )

    now = timezone.now()
    actor = _normalize_actor(actor)
    rule.status = 'active'
    rule.approved_by = actor
    rule.approved_at = now
    rule.revoked_at = None
    if rule.expires_at is not None and rule.expires_at <= now:
        rule.expires_at = None
    metadata = _exception_metadata(rule)
    _set_exception_lifecycle(
        metadata=metadata,
        action='approve',
        status='active',
        actor_id=getattr(actor, 'pk', None),
        comment=comment,
    )
    rule.metadata = metadata
    rule.save(update_fields=['status', 'approved_by', 'approved_at', 'revoked_at', 'expires_at', 'metadata', 'last_updated'])

    record_audit_event(
        event_type=FINDING_TRANSITION_EVENT_TYPE,
        fabric=rule.fabric,
        actor=actor,
        subject=rule,
        outcome='ok',
        message='Disjointness exception approved.',
        payload={
            'exception_action': 'approve',
            'suppression_rule_id': rule.pk,
            'status': rule.status,
        },
        metadata={DISJOINTNESS_EXCEPTION_KEY: dict(metadata.get(DISJOINTNESS_EXCEPTION_KEY) or {})},
    )
    return rule


def expire_disjointness_exception(*, exception, actor=None, comment: str = '') -> SuppressionRule:
    rule = _as_exception_rule(exception)
    if rule.status != 'active':
        raise InvalidDisjointnessExceptionTransition(
            f'Cannot expire exception #{rule.pk} while status is "{rule.status}".'
        )

    now = timezone.now()
    actor = _normalize_actor(actor)
    rule.status = 'expired'
    rule.revoked_at = now
    if rule.expires_at is None or rule.expires_at > now:
        rule.expires_at = now
    metadata = _exception_metadata(rule)
    _set_exception_lifecycle(
        metadata=metadata,
        action='expire',
        status='expired',
        actor_id=getattr(actor, 'pk', None),
        comment=comment,
    )
    rule.metadata = metadata
    rule.save(update_fields=['status', 'revoked_at', 'expires_at', 'metadata', 'last_updated'])

    record_audit_event(
        event_type=FINDING_TRANSITION_EVENT_TYPE,
        fabric=rule.fabric,
        actor=actor,
        subject=rule,
        outcome='ok',
        message='Disjointness exception expired.',
        payload={
            'exception_action': 'expire',
            'suppression_rule_id': rule.pk,
            'status': rule.status,
        },
        metadata={DISJOINTNESS_EXCEPTION_KEY: dict(metadata.get(DISJOINTNESS_EXCEPTION_KEY) or {})},
    )
    return rule


def reactivate_disjointness_exception(*, exception, actor=None, comment: str = '', expires_at=None) -> SuppressionRule:
    rule = _as_exception_rule(exception)
    if rule.status not in {'expired', 'revoked'}:
        raise InvalidDisjointnessExceptionTransition(
            f'Cannot reactivate exception #{rule.pk} while status is "{rule.status}".'
        )

    now = timezone.now()
    actor = _normalize_actor(actor)
    rule.status = 'active'
    rule.revoked_at = None
    rule.approved_by = actor
    rule.approved_at = now
    if expires_at is not None:
        rule.expires_at = expires_at
    elif rule.expires_at is not None and rule.expires_at <= now:
        rule.expires_at = None

    metadata = _exception_metadata(rule)
    _set_exception_lifecycle(
        metadata=metadata,
        action='reactivate',
        status='active',
        actor_id=getattr(actor, 'pk', None),
        comment=comment,
    )
    rule.metadata = metadata
    rule.save(
        update_fields=[
            'status',
            'revoked_at',
            'approved_by',
            'approved_at',
            'expires_at',
            'metadata',
            'last_updated',
        ]
    )

    record_audit_event(
        event_type=FINDING_TRANSITION_EVENT_TYPE,
        fabric=rule.fabric,
        actor=actor,
        subject=rule,
        outcome='ok',
        message='Disjointness exception reactivated.',
        payload={
            'exception_action': 'reactivate',
            'suppression_rule_id': rule.pk,
            'status': rule.status,
        },
        metadata={DISJOINTNESS_EXCEPTION_KEY: dict(metadata.get(DISJOINTNESS_EXCEPTION_KEY) or {})},
    )
    return rule


def expire_suppressions(*, now=None) -> int:
    now = now or timezone.now()
    expired = SuppressionRule.objects.filter(
        status='active',
        revoked_at__isnull=True,
        expires_at__isnull=False,
        expires_at__lte=now,
    )
    count = expired.count()
    expired.update(status='expired', revoked_at=now, last_updated=now)
    return count
