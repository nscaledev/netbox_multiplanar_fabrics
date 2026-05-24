from dataclasses import replace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netbox_plant_graph.models import (
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureRole,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
    FabricArchitecture,
    StampTemplate,
    TransferPattern,
)
from netbox_plant_graph.services.architecture_workspace import (
    approve_architecture_publish_plan,
    attach_architecture_source_artifact,
    generate_architecture_publish_plan,
    normalize_architecture_source_artifact,
    publish_architecture_plan,
    validate_architecture_workspace,
)
from netbox_plant_graph.services.architecture_schema import ARCHITECTURE_SCHEMA_CONTRACT_VERSION
from netbox_plant_graph.services.blueprint_registry import (
    architecture_definition_to_payload,
    get_default_blueprint_registry,
)


def architecture_workspace_bundle(slug='workspace-blueprint', version='v1'):
    entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')
    definition = replace(entry.definition, slug=slug, version=version, required_device_types={})
    return {
        'definition': architecture_definition_to_payload(definition),
        'parameter_schema': {},
        'required_device_types': {},
        'stamp_templates': {
            f'{slug}-mini-proof': {
                'slug': f'{slug}-mini-proof',
                'name': f'{slug} Mini Proof',
                'template': {
                    **entry.stamp_templates['roce-4-plane-mini-proof']['template'],
                    'architecture_slug': slug,
                    'architecture_version': version,
                },
            }
        },
        'schema_contract_version': ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
        'bundle_version': version,
    }


class ArchitectureWorkspaceServiceTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='architecture-admin',
            password='admin',
            email='architecture-admin@example.local',
        )

    def _workspace(self, slug='architecture-workspace'):
        return ArchitectureWorkspace.objects.create(
            name='Architecture Workspace',
            slug=slug,
            target_slug=f'{slug}-target',
            target_version='v1',
        )

    def test_source_artifact_normalizes_to_architecture_components(self):
        workspace = self._workspace()
        artifact = attach_architecture_source_artifact(
            workspace=workspace,
            name='Architecture blueprint bundle',
            artifact_type='blueprint_bundle',
            raw_payload=architecture_workspace_bundle('workspace-normalize-blueprint'),
            actor=self.user,
        )

        result = normalize_architecture_source_artifact(artifact, actor=self.user)

        self.assertGreaterEqual(result.created, 4)
        self.assertTrue(
            ArchitectureDesignComponent.objects.filter(
                workspace=workspace,
                kind='fabric_architecture_blueprint',
                natural_key='fabric_architecture_blueprint:workspace-normalize-blueprint:v1',
            ).exists()
        )
        self.assertTrue(ArchitectureDesignComponent.objects.filter(workspace=workspace, kind='architecture_role').exists())
        self.assertTrue(ArchitectureDesignComponent.objects.filter(workspace=workspace, kind='transfer_pattern').exists())
        artifact.refresh_from_db()
        workspace.refresh_from_db()
        self.assertEqual(artifact.status, 'normalized')
        self.assertEqual(workspace.status, 'normalizing')

    def test_validate_generate_approve_and_publish_architecture_plan(self):
        workspace = self._workspace('architecture-publish-workspace')
        artifact = attach_architecture_source_artifact(
            workspace=workspace,
            name='Publishable bundle',
            artifact_type='blueprint_bundle',
            raw_payload=architecture_workspace_bundle('workspace-publish-blueprint'),
            actor=self.user,
        )
        normalize_architecture_source_artifact(artifact, actor=self.user)

        run = validate_architecture_workspace(workspace, actor=self.user)
        plan = generate_architecture_publish_plan(workspace, actor=self.user)
        approved = approve_architecture_publish_plan(plan, actor=self.user)
        published = publish_architecture_plan(approved, actor=self.user)

        self.assertEqual(run.status, 'passed')
        self.assertEqual(plan.pk, published.pk)
        self.assertEqual(published.status, 'published')
        architecture = FabricArchitecture.objects.get(slug='workspace-publish-blueprint', version='v1')
        workspace.refresh_from_db()
        self.assertEqual(workspace.status, 'published')
        self.assertEqual(workspace.published_architecture, architecture)
        self.assertGreater(ArchitectureRole.objects.filter(architecture=architecture).count(), 0)
        self.assertGreater(TransferPattern.objects.filter(architecture=architecture).count(), 0)
        self.assertTrue(StampTemplate.objects.filter(slug='workspace-publish-blueprint-mini-proof').exists())


class ArchitectureWorkspaceAPITestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='architecture-api-admin',
            password='admin',
            email='architecture-api-admin@example.local',
        )
        cls.workspace = ArchitectureWorkspace.objects.create(
            name='API Architecture Workspace',
            slug='api-architecture-workspace',
            target_slug='api-architecture-target',
            target_version='v1',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_api_attaches_normalizes_validates_and_generates_publish_plan(self):
        attach_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:architecture-workspace-source-attach',
                kwargs={'pk': self.workspace.pk},
            ),
            data={
                'name': 'API architecture source',
                'artifact_type': 'blueprint_bundle',
                'raw_payload': architecture_workspace_bundle('api-architecture-blueprint'),
            },
            content_type='application/json',
        )

        self.assertEqual(attach_response.status_code, 201)
        artifact_id = attach_response.json()['artifact']['id']

        normalize_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:architecture-source-artifact-normalize',
                kwargs={'pk': artifact_id},
            ),
            content_type='application/json',
        )
        self.assertEqual(normalize_response.status_code, 200)
        self.assertGreater(normalize_response.json()['result']['created'], 0)

        validate_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:architecture-workspace-validate',
                kwargs={'pk': self.workspace.pk},
            ),
            content_type='application/json',
        )
        self.assertEqual(validate_response.status_code, 200)
        self.assertEqual(validate_response.json()['validation_run']['status'], 'passed')

        plan_response = self.client.post(
            reverse(
                'plugins-api:netbox_plant_graph-api:architecture-workspace-plan-generate',
                kwargs={'pk': self.workspace.pk},
            ),
            content_type='application/json',
        )
        self.assertEqual(plan_response.status_code, 200)
        self.assertEqual(plan_response.json()['plan']['status'], 'generated')
        self.assertEqual(ArchitecturePublishPlan.objects.filter(workspace=self.workspace).count(), 1)

    def test_workspace_detail_renders_control_surface(self):
        response = self.client.get(
            reverse('plugins:netbox_plant_graph:architectureworkspace', kwargs={'pk': self.workspace.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Architecture Workspace')
        self.assertContains(response, 'Sources: active')
        self.assertContains(response, 'Next action')
        self.assertContains(response, 'Attach a source artifact')
        self.assertContains(response, 'Attach Architecture Source Artifact')
        self.assertContains(response, 'Blueprint Bundle')
        self.assertContains(response, 'Parser Key can be blank')
        self.assertContains(response, 'Generate Publish Plan')

    def test_workspace_detail_renders_normalized_inventory_and_provenance(self):
        workspace = ArchitectureWorkspace.objects.create(
            name='Rendered Architecture Inventory',
            slug='rendered-architecture-inventory',
            target_slug='rendered-architecture-target',
            target_version='v1',
        )
        artifact = attach_architecture_source_artifact(
            workspace=workspace,
            name='Rendered bundle',
            artifact_type='blueprint_bundle',
            raw_payload=architecture_workspace_bundle('rendered-architecture-blueprint'),
            actor=self.user,
        )
        normalize_architecture_source_artifact(artifact, actor=self.user)

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:architectureworkspace', kwargs={'pk': workspace.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Normalize: complete')
        self.assertContains(response, 'Component Inventory')
        self.assertContains(response, 'Normalized Architecture Semantics')
        self.assertContains(response, 'rendered-architecture-blueprint')
        self.assertContains(response, 'OSFP channels')
        self.assertContains(response, 'stamp templates 1')
        self.assertContains(response, 'mpf_blueprint_bundle')
        self.assertContains(response, 'Rendered bundle')

    def test_workspace_detail_renders_validation_publish_triage(self):
        workspace = ArchitectureWorkspace.objects.create(
            name='Rendered Architecture Triage',
            slug='rendered-architecture-triage',
            target_slug='rendered-architecture-triage-target',
            target_version='v1',
        )
        artifact = attach_architecture_source_artifact(
            workspace=workspace,
            name='Triage bundle',
            artifact_type='blueprint_bundle',
            raw_payload=architecture_workspace_bundle('rendered-triage-blueprint'),
            actor=self.user,
        )
        normalize_architecture_source_artifact(artifact, actor=self.user)
        validate_architecture_workspace(workspace, actor=self.user)
        generate_architecture_publish_plan(workspace, actor=self.user)

        response = self.client.get(
            reverse('plugins:netbox_plant_graph:architectureworkspace', kwargs={'pk': workspace.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Validation And Import Triage')
        self.assertContains(response, 'No validation or component issues surfaced.')
        self.assertContains(response, 'Approve publish plan')
        self.assertContains(response, 'Generated')
        self.assertContains(response, '0 conflict')

        workspace.target_version = 'v2'
        workspace.save(update_fields=('target_version', 'last_updated'))
        stale_response = self.client.get(
            reverse('plugins:netbox_plant_graph:architectureworkspace', kwargs={'pk': workspace.pk})
        )

        self.assertEqual(stale_response.status_code, 200)
        self.assertContains(stale_response, 'Regenerate stale plan')
        self.assertContains(stale_response, 'stale plan')

    def test_handoff_api_returns_workspace_artifacts_and_plans(self):
        ArchitectureValidationRun.objects.create(
            workspace=self.workspace,
            status='passed',
            validation_kind='publish_preflight',
            workspace_revision='handoff-revision',
        )

        response = self.client.get(
            reverse(
                'plugins-api:netbox_plant_graph-api:architecture-workspace-handoff',
                kwargs={'pk': self.workspace.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['workspace']['slug'], self.workspace.slug)
        self.assertIn('validation_runs', response.json())
