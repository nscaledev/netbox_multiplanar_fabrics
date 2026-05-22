from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from dcim.models import Site

from netbox_plant_graph.models import (
    FabricArchitecture,
    OnboardingDesignItem,
    OnboardingPlan,
    OnboardingPrerequisite,
    OnboardingSourceArtifact,
    OnboardingWorkspace,
)
from netbox_plant_graph.services.onboarding import (
    apply_onboarding_plan,
    approve_onboarding_plan,
    attach_source_artifact,
    discover_prerequisites,
    generate_onboarding_plan,
    normalize_source_artifact,
)


class OnboardingWorkspaceServiceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='onboarding-admin',
            password='admin',
            email='onboarding-admin@example.local',
        )
        cls.site = Site.objects.create(name='Onboarding Site', slug='onboarding-site', status='active')
        cls.architecture = FabricArchitecture.objects.create(
            name='Onboarding Architecture',
            slug='onboarding-architecture',
            version='v1',
            status='active',
        )

    def _workspace(self):
        return OnboardingWorkspace.objects.create(
            name='Onboarding Workspace',
            slug='onboarding-workspace',
            target_fabric_name='Onboarding Fabric',
            target_fabric_slug='onboarding-fabric',
            site=self.site,
            architecture=self.architecture,
        )

    def test_source_artifact_normalizes_to_design_items_with_provenance(self):
        workspace = self._workspace()
        artifact = attach_source_artifact(
            workspace=workspace,
            name='Cable schedule',
            artifact_type='api_payload',
            raw_payload={
                'items': [
                    {
                        'kind': 'cable_assembly',
                        'identity': 'jumper-001',
                        'data': {'cable_id': 'JUMPER-001'},
                    }
                ]
            },
            actor=self.user,
        )

        result = normalize_source_artifact(artifact, actor=self.user)

        self.assertEqual(result.created, 1)
        item = OnboardingDesignItem.objects.get(workspace=workspace)
        self.assertEqual(item.kind, 'cable_assembly')
        self.assertEqual(item.natural_key, 'cable_assembly:jumper-001')
        self.assertEqual(item.provenance['source_artifact_id'], artifact.pk)
        artifact.refresh_from_db()
        self.assertEqual(artifact.status, 'normalized')

    def test_prerequisite_discovery_blocks_missing_site_and_architecture(self):
        workspace = OnboardingWorkspace.objects.create(
            name='Blocked Workspace',
            slug='blocked-workspace',
            target_fabric_name='Blocked Fabric',
            target_fabric_slug='blocked-fabric',
        )

        result = discover_prerequisites(workspace, actor=self.user)

        self.assertEqual(result.open_count, 2)
        self.assertEqual(
            set(OnboardingPrerequisite.objects.filter(workspace=workspace).values_list('requirement_key', flat=True)),
            {'site:target', 'architecture:target'},
        )

    def test_plan_generation_is_hash_stable_and_approvable_without_blockers(self):
        workspace = self._workspace()

        first_plan = generate_onboarding_plan(workspace, actor=self.user)
        second_plan = generate_onboarding_plan(workspace, actor=self.user)

        self.assertEqual(first_plan.pk, second_plan.pk)
        self.assertEqual(first_plan.plan_hash, second_plan.plan_hash)
        self.assertEqual(first_plan.status, 'generated')

        approved = approve_onboarding_plan(first_plan, actor=self.user)

        self.assertEqual(approved.status, 'approved')
        workspace.refresh_from_db()
        self.assertEqual(workspace.current_plan, approved)
        self.assertEqual(workspace.status, 'approved')

    def test_partial_stage_apply_does_not_mark_plan_fully_applied(self):
        workspace = self._workspace()
        plan = approve_onboarding_plan(generate_onboarding_plan(workspace, actor=self.user), actor=self.user)

        result = apply_onboarding_plan(plan, actor=self.user, stages=['prerequisites'])

        plan.refresh_from_db()
        workspace.refresh_from_db()
        self.assertEqual(result.status, 'applying')
        self.assertEqual(plan.status, 'applying')
        self.assertEqual(workspace.status, 'applying')
        self.assertEqual(plan.stages.get(stage_key='prerequisites').status, 'completed')
        self.assertTrue(plan.stages.exclude(stage_key='prerequisites').exclude(status='completed').exists())


class OnboardingWorkspaceAPITestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='onboarding-api-admin',
            password='admin',
            email='onboarding-api-admin@example.local',
        )
        cls.workspace = OnboardingWorkspace.objects.create(
            name='API Workspace',
            slug='api-workspace',
            target_fabric_name='API Fabric',
            target_fabric_slug='api-fabric',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_api_attaches_and_normalizes_source_artifact(self):
        attach_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:onboarding-workspace-source-attach',
                kwargs={'pk': self.workspace.pk},
            ),
            data={
                'name': 'API source',
                'artifact_type': 'api_payload',
                'raw_payload': {
                    'items': [
                        {
                            'kind': 'manual_note',
                            'identity': 'api-note',
                            'message': 'hello',
                        }
                    ]
                },
            },
            content_type='application/json',
        )

        self.assertEqual(attach_response.status_code, 201)
        artifact_id = attach_response.json()['artifact']['id']

        normalize_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:onboarding-source-artifact-normalize',
                kwargs={'pk': artifact_id},
            ),
            content_type='application/json',
        )

        self.assertEqual(normalize_response.status_code, 200)
        self.assertEqual(normalize_response.json()['result']['created'], 1)
        self.assertTrue(OnboardingSourceArtifact.objects.filter(pk=artifact_id, status='normalized').exists())

    def test_api_generates_plan(self):
        plan_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:onboarding-workspace-plan-generate',
                kwargs={'pk': self.workspace.pk},
            ),
            content_type='application/json',
        )

        self.assertEqual(plan_response.status_code, 200)
        self.assertEqual(OnboardingPlan.objects.filter(workspace=self.workspace).count(), 1)
        self.assertIn('plan_hash', plan_response.json()['plan'])

    def test_workspace_detail_renders_control_surface(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:onboardingworkspace', kwargs={'pk': self.workspace.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Onboarding Workspace')
        self.assertContains(response, 'Attach Source Artifact')
        self.assertContains(response, 'Generate Plan')
