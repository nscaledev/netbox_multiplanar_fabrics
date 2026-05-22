from django.test import SimpleTestCase
from django.urls import reverse

from netbox_plant_graph import filtersets, forms, tables, views
from netbox_plant_graph.api import serializers, views as api_views
from netbox_plant_graph.choices import FabricClassChoices, TransferMapKindChoices
from netbox_plant_graph.models import (
    AuditEvent,
    OperationRun,
    AllocationRuleSet,
    ArchitectureDesignComponent,
    ArchitecturePublishPlan,
    ArchitectureRole,
    ArchitectureSourceArtifact,
    ArchitectureValidationRun,
    ArchitectureWorkspace,
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    OnboardingDesignItem,
    OnboardingExecutionStage,
    OnboardingObjectLink,
    OnboardingPlan,
    OnboardingPrerequisite,
    OnboardingSourceArtifact,
    OnboardingWorkspace,
    PathIntent,
    Plane,
    StampRun,
    StampTemplate,
    StrandTermination,
    SuppressionRule,
    TransferMap,
    TransferPattern,
    TransportChannel,
    TransportChannelPositionMap,
)
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS, get_v2_object_spec, get_v2_object_spec_for_model


EXPECTED_V2_MODELS = {
    FabricArchitecture,
    ArchitectureRole,
    TransferPattern,
    AllocationRuleSet,
    ArchitectureWorkspace,
    ArchitectureSourceArtifact,
    ArchitectureDesignComponent,
    ArchitectureValidationRun,
    ArchitecturePublishPlan,
    Fabric,
    Plane,
    FabricNode,
    Endpoint,
    ConnectorPosition,
    TransportChannel,
    TransportChannelPositionMap,
    FiberSegment,
    CableAssembly,
    FiberStrand,
    StrandTermination,
    OpticalLane,
    TransferMap,
    PathIntent,
    StampTemplate,
    StampRun,
    SuppressionRule,
    AuditEvent,
    OperationRun,
    OnboardingWorkspace,
    OnboardingSourceArtifact,
    OnboardingDesignItem,
    OnboardingPrerequisite,
    OnboardingPlan,
    OnboardingExecutionStage,
    OnboardingObjectLink,
}


class V2RegistryContractTestCase(SimpleTestCase):
    def test_registry_covers_current_v2_model_surface_once(self):
        self.assertEqual({spec.model for spec in V2_OBJECT_SPECS}, EXPECTED_V2_MODELS)
        self.assertEqual(len({spec.registry_key for spec in V2_OBJECT_SPECS}), len(V2_OBJECT_SPECS))
        self.assertEqual(len({spec.route_slug for spec in V2_OBJECT_SPECS}), len(V2_OBJECT_SPECS))
        self.assertEqual(len({spec.api_basename for spec in V2_OBJECT_SPECS}), len(V2_OBJECT_SPECS))

    def test_registry_lookup_helpers_round_trip(self):
        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                self.assertEqual(get_v2_object_spec(spec.registry_key), spec)
                self.assertEqual(get_v2_object_spec_for_model(spec.model), spec)

    def test_generated_serializers_and_viewsets_match_registry(self):
        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                serializer_class = getattr(serializers, spec.serializer_name)
                viewset_class = getattr(api_views, spec.viewset_name)

                self.assertEqual(serializer_class.Meta.model, spec.model)
                self.assertEqual(serializer_class.Meta.fields, spec.api_fields)
                self.assertEqual(serializer_class.Meta.brief_fields, spec.brief_fields)
                self.assertEqual(viewset_class.serializer_class, serializer_class)
                self.assertEqual(viewset_class.queryset.model, spec.model)

    def test_generated_standard_ui_classes_match_registry(self):
        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                table_class = getattr(tables, spec.table_name)
                form_class = getattr(forms, spec.form_name)
                filter_form_class = getattr(forms, spec.filter_form_name)
                filterset_class = getattr(filtersets, spec.filterset_name)
                list_view_class = getattr(views, spec.list_view_name)
                detail_view_class = getattr(views, spec.detail_view_name)
                edit_view_class = getattr(views, spec.edit_view_name)
                delete_view_class = getattr(views, spec.delete_view_name)
                changelog_view_class = getattr(views, spec.changelog_view_name)
                journal_view_class = getattr(views, spec.journal_view_name)

                self.assertEqual(table_class.Meta.model, spec.model)
                self.assertEqual(table_class.Meta.fields, spec.resolved_table_fields)
                self.assertEqual(table_class.Meta.default_columns, spec.resolved_default_columns)
                self.assertEqual(form_class.Meta.model, spec.model)
                self.assertEqual(form_class.Meta.fields, spec.resolved_form_fields)
                self.assertEqual(filter_form_class.model, spec.model)
                self.assertEqual(filterset_class.Meta.model, spec.model)
                self.assertEqual(filterset_class.Meta.fields, spec.resolved_filter_fields)
                self.assertEqual(list_view_class.queryset.model, spec.model)
                self.assertEqual(detail_view_class.queryset.model, spec.model)
                self.assertEqual(edit_view_class.queryset.model, spec.model)
                self.assertEqual(delete_view_class.queryset.model, spec.model)
                self.assertEqual(changelog_view_class.queryset.model, spec.model)
                self.assertEqual(journal_view_class.queryset.model, spec.model)

    def test_generated_api_routes_reverse_for_every_registered_v2_object(self):
        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                list_url = reverse(spec.api_list_url_name)
                detail_url = reverse(spec.api_detail_url_name, kwargs={'pk': 1})

                self.assertEqual(list_url, f'/api/plugins/plant-graph/{spec.api_basename}/')
                self.assertEqual(detail_url, f'/api/plugins/plant-graph/{spec.api_basename}/1/')

    def test_fabric_architecture_registry_exposes_fabric_class(self):
        spec = get_v2_object_spec('fabricarchitecture')

        self.assertIn('fabric_class', spec.fields)
        self.assertIn('fabric_class', spec.brief_fields)
        self.assertIn('fabric_class', spec.api_fields)
        self.assertIn('fabric_class', spec.resolved_form_fields)
        self.assertIn('fabric_class', spec.resolved_filter_fields)
        self.assertIn('fabric_class', spec.resolved_table_fields)
        self.assertIn('fabric_class', spec.resolved_default_columns)

    def test_blueprint_expansion_choices_are_registered(self):
        self.assertEqual(
            dict(FabricClassChoices.CHOICES),
            {
                'roce_backend': 'RoCE Backend',
                'ethernet_frontend': 'Ethernet Frontend',
                'management': 'Management',
                'storage': 'Storage',
            },
        )
        transfer_kind_values = {value for value, label in TransferMapKindChoices.CHOICES}

        self.assertGreaterEqual(
            transfer_kind_values,
            {
                'identity',
                'polarity_swap',
                'shuffle_2x2',
                'stagger',
                'breakout',
                'custom',
                'shuffle_1x4',
                'shuffle_2x2_mpo24',
                'shuffle_4x4',
                'direct_attach',
                'polarity_type_b',
                'polarity_type_c',
                'shuffle_nxm',
            },
        )
