from django.contrib.auth import get_user_model
from django.test import TestCase

from netbox_plant_graph.models import AuditEvent, Fabric, StampRun
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.plan_execution import (
    build_deployment_workflow_summary,
    execute_template_plan,
    rollback_stamp_run,
)


class V2PlanExecutionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='plan-admin',
            password='admin',
            email='plan-admin@example.local',
        )
        cls.fixture = ensure_roce_4plane_shuffle_architecture()

    def _execute(self, slug='plan-exec-fabric', name='Plan Exec Fabric'):
        return execute_template_plan(
            template=self.fixture.stamp_template,
            fabric_name=name,
            fabric_slug=slug,
            actor=self.user,
        )

    def test_execute_template_plan_returns_stamp_run_and_fabric_context(self):
        result = self._execute(slug='plan-exec-context')

        self.assertEqual(result.fabric.slug, 'plan-exec-context')
        self.assertEqual(result.stamp_run.fabric_id, result.fabric.pk)
        self.assertEqual(result.stamp_run.status, 'completed')
        self.assertTrue(result.rollback_eligible)
        self.assertEqual(result.stamp_run.metadata['deployment_workflow']['helper'], 'execute_template_plan')
        self.assertEqual(result.stamp_run.metadata['deployment_workflow']['executed_by_id'], self.user.pk)

    def test_rollback_stamp_run_removes_stamped_fabric_and_writes_metadata(self):
        execution = self._execute(slug='rollback-target')
        stamp_run_pk = execution.stamp_run.pk
        fabric_pk = execution.fabric.pk

        rollback = rollback_stamp_run(stamp_run=execution.stamp_run, actor=self.user)
        rolled_back_run = StampRun.objects.get(pk=stamp_run_pk)

        self.assertFalse(Fabric.objects.filter(pk=fabric_pk).exists())
        self.assertFalse(rolled_back_run.fabric_id)
        self.assertFalse(rollback.already_rolled_back)
        self.assertEqual(rollback.rollback_mode, 'fabric_delete')
        self.assertGreaterEqual(rollback.deleted_total, 1)
        self.assertEqual(rolled_back_run.metadata['rollback']['state'], 'completed')
        self.assertEqual(rolled_back_run.metadata['rollback']['applied_by_id'], self.user.pk)

        rollback_events = [
            event
            for event in AuditEvent.objects.filter(event_type='stamp').order_by('pk')
            if (event.payload or {}).get('action') == 'rollback'
            and (event.payload or {}).get('stamp_run_id') == stamp_run_pk
        ]
        self.assertEqual(len(rollback_events), 1)

    def test_rollback_stamp_run_is_idempotent(self):
        execution = self._execute(slug='rollback-idempotent-target')
        stamp_run_pk = execution.stamp_run.pk

        first = rollback_stamp_run(stamp_run=execution.stamp_run, actor=self.user)
        second = rollback_stamp_run(stamp_run=execution.stamp_run, actor=self.user)

        self.assertFalse(first.already_rolled_back)
        self.assertTrue(second.already_rolled_back)
        self.assertEqual(first.deleted_counts, second.deleted_counts)
        self.assertEqual(first.deleted_total, second.deleted_total)

        rollback_events = [
            event
            for event in AuditEvent.objects.filter(event_type='stamp').order_by('pk')
            if (event.payload or {}).get('action') == 'rollback'
            and (event.payload or {}).get('stamp_run_id') == stamp_run_pk
        ]
        self.assertEqual(len(rollback_events), 1)

    def test_deployment_workflow_summary_tracks_rollback_eligibility(self):
        execution = self._execute(slug='summary-target')
        stamp_run_pk = execution.stamp_run.pk

        summary_before = build_deployment_workflow_summary(template=self.fixture.stamp_template, limit=10)
        row_before = next(row for row in summary_before.recent_runs if row.stamp_run.pk == stamp_run_pk)
        self.assertTrue(row_before.rollback_eligible)
        self.assertFalse(row_before.rollback_applied)
        self.assertIn(stamp_run_pk, summary_before.rollback_eligible_run_ids)
        self.assertNotIn(stamp_run_pk, summary_before.rollback_applied_run_ids)

        rollback_stamp_run(stamp_run=execution.stamp_run, actor=self.user)

        summary_after = build_deployment_workflow_summary(template=self.fixture.stamp_template, limit=10)
        row_after = next(row for row in summary_after.recent_runs if row.stamp_run.pk == stamp_run_pk)
        self.assertFalse(row_after.rollback_eligible)
        self.assertTrue(row_after.rollback_applied)
        self.assertNotIn(stamp_run_pk, summary_after.rollback_eligible_run_ids)
        self.assertIn(stamp_run_pk, summary_after.rollback_applied_run_ids)
