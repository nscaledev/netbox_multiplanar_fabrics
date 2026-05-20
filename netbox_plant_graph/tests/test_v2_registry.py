from django.test import SimpleTestCase
from django.urls import reverse

from netbox_plant_graph.api import serializers, views
from netbox_plant_graph.models import (
    AllocationRuleSet,
    ArchitectureRole,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    PathIntent,
    Plane,
    StampRun,
    StampTemplate,
    StrandTermination,
    TransferMap,
    TransferPattern,
    TransportChannel,
)
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS, get_v2_object_spec, get_v2_object_spec_for_model


EXPECTED_V2_MODELS = {
    FabricArchitecture,
    ArchitectureRole,
    TransferPattern,
    AllocationRuleSet,
    Fabric,
    Plane,
    FabricNode,
    Endpoint,
    ConnectorPosition,
    TransportChannel,
    FiberSegment,
    FiberStrand,
    StrandTermination,
    OpticalLane,
    TransferMap,
    PathIntent,
    StampTemplate,
    StampRun,
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
                viewset_class = getattr(views, spec.viewset_name)

                self.assertEqual(serializer_class.Meta.model, spec.model)
                self.assertEqual(serializer_class.Meta.fields, spec.api_fields)
                self.assertEqual(serializer_class.Meta.brief_fields, spec.brief_fields)
                self.assertEqual(viewset_class.serializer_class, serializer_class)
                self.assertEqual(viewset_class.queryset.model, spec.model)

    def test_generated_api_routes_reverse_for_every_registered_v2_object(self):
        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                list_url = reverse(spec.api_list_url_name)
                detail_url = reverse(spec.api_detail_url_name, kwargs={'pk': 1})

                self.assertEqual(list_url, f'/api/plugins/plant-graph/{spec.api_basename}/')
                self.assertEqual(detail_url, f'/api/plugins/plant-graph/{spec.api_basename}/1/')
