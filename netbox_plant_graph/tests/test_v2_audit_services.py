from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from netbox.context import current_request

from netbox_plant_graph.models import AuditEvent, Fabric, Plane, SuppressionRule
from netbox_plant_graph.services.audit import (
    InvalidDisjointnessExceptionTransition,
    InvalidFindingTransition,
    acknowledge_audit_finding,
    approve_disjointness_exception,
    expire_disjointness_exception,
    get_audit_finding,
    list_audit_findings,
    reactivate_disjointness_exception,
    record_audit_event,
    request_disjointness_exception,
    resolve_audit_finding,
    start_audit_finding_remediation,
    suppress_audit_finding,
    unsuppress_audit_finding,
)


class V2AuditServiceLifecycleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='audit-admin',
            password='admin',
            email='audit-admin@example.local',
        )
        cls.fabric = Fabric.objects.create(
            name='Audit Lifecycle Fabric',
            slug='audit-lifecycle-fabric',
            status='active',
        )
        cls.plane = Plane.objects.create(
            fabric=cls.fabric,
            plane_number=1,
            label='Plane 1',
        )

    def _create_policy_finding_event(self):
        return AuditEvent.objects.create(
            fabric=self.fabric,
            event_type='policy_eval',
            outcome='warning',
            message='Cross-plane overlap detected.',
            payload={
                'finding_type': 'cross_plane_overlap',
                'severity': 'error',
                'plane_id': self.plane.pk,
            },
            metadata={},
        )

    def test_finding_transition_happy_path(self):
        finding_event = self._create_policy_finding_event()

        acknowledged = acknowledge_audit_finding(
            finding=finding_event,
            actor=self.user,
            note='Triaged by operator.',
        )
        self.assertEqual(acknowledged.status, 'acknowledged')

        in_progress = start_audit_finding_remediation(
            finding=finding_event,
            actor=self.user,
            note='Remediation started.',
        )
        self.assertEqual(in_progress.status, 'in_progress')

        suppressed = suppress_audit_finding(
            finding=finding_event,
            actor=self.user,
            reason='Planned maintenance window.',
            days=1,
        )
        self.assertEqual(suppressed.status, 'suppressed')
        self.assertTrue(suppressed.suppressed)

        active_suppressed = list_audit_findings(
            fabric=self.fabric,
            status='suppressed',
            suppressed=True,
        )
        self.assertEqual(len(active_suppressed), 1)
        self.assertEqual(active_suppressed[0].finding_id, finding_event.pk)

        reopened = unsuppress_audit_finding(
            finding=finding_event,
            actor=self.user,
            reason='Window complete.',
        )
        self.assertEqual(reopened.status, 'open')
        self.assertFalse(reopened.suppressed)

        resolved = resolve_audit_finding(
            finding=finding_event,
            actor=self.user,
            note='Resolved by cable reroute.',
        )
        self.assertEqual(resolved.status, 'resolved')

        latest = get_audit_finding(finding=finding_event.pk)
        self.assertEqual(latest.status, 'resolved')
        self.assertEqual(latest.plane_id, self.plane.pk)
        self.assertEqual(latest.finding_type, 'cross_plane_overlap')

        transition_events = AuditEvent.objects.filter(
            event_type='suppression_change',
            payload__finding_id=finding_event.pk,
        )
        self.assertEqual(transition_events.count(), 5)

    def test_invalid_finding_transition_rejected(self):
        finding_event = self._create_policy_finding_event()

        with self.assertRaises(InvalidFindingTransition):
            unsuppress_audit_finding(
                finding=finding_event,
                actor=self.user,
                reason='Invalid transition attempt.',
            )

        current = get_audit_finding(finding=finding_event.pk)
        self.assertEqual(current.status, 'open')
        self.assertFalse(current.suppressed)

    def test_disjointness_exception_request_approve_expire_reactivate(self):
        requested = request_disjointness_exception(
            fabric=self.fabric,
            plane=self.plane,
            actor=self.user,
            reason='Temporary sanctioned overlap.',
            expires_at=timezone.now() + timedelta(days=2),
        )
        self.assertEqual(requested.status, 'pending')
        self.assertEqual(requested.policy_key, 'disjointness')
        self.assertIn('disjointness_exception', requested.metadata)

        approved = approve_disjointness_exception(exception=requested, actor=self.user, comment='Approved for rollout.')
        self.assertEqual(approved.status, 'active')
        self.assertIsNotNone(approved.approved_at)
        self.assertEqual(approved.approved_by_id, self.user.pk)

        expired = expire_disjointness_exception(exception=requested, actor=self.user, comment='Rollout window ended.')
        self.assertEqual(expired.status, 'expired')
        self.assertIsNotNone(expired.revoked_at)

        reactivated = reactivate_disjointness_exception(
            exception=requested,
            actor=self.user,
            comment='Issue recurred; allow temporary bypass.',
        )
        self.assertEqual(reactivated.status, 'active')
        self.assertIsNone(reactivated.revoked_at)

        ct = ContentType.objects.get_for_model(SuppressionRule, for_concrete_model=False)
        transition_events = AuditEvent.objects.filter(
            event_type='suppression_change',
            subject_type=ct,
            subject_id=requested.pk,
        ).order_by('created', 'pk')
        self.assertEqual(transition_events.count(), 4)
        self.assertEqual(
            [event.payload.get('exception_action') for event in transition_events],
            ['request', 'approve', 'expire', 'reactivate'],
        )

    def test_disjointness_exception_invalid_transition_rejected(self):
        requested = request_disjointness_exception(
            fabric=self.fabric,
            plane=self.plane,
            actor=self.user,
            reason='Transition validation check.',
        )

        with self.assertRaises(InvalidDisjointnessExceptionTransition):
            reactivate_disjointness_exception(exception=requested, actor=self.user)

    def test_record_audit_event_handles_anonymous_request_context(self):
        token = current_request.set(
            SimpleNamespace(
                id=uuid4(),
                user=AnonymousUser(),
            )
        )
        try:
            event = record_audit_event(
                event_type='path_resolve',
                fabric=self.fabric,
                actor=AnonymousUser(),
                outcome='ok',
                message='Anonymous trace event.',
                payload={'scope': 'test'},
            )
        finally:
            current_request.reset(token)

        self.assertIsNotNone(event.pk)
        self.assertIsNone(event.actor_id)
