from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from extras.events import serialize_for_event
from utilities.api import get_serializer_for_model

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
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric
from netbox_plant_graph.v2_registry import V2_OBJECT_SPECS


V2_MODELS = (
    FabricArchitecture,
    ArchitectureRole,
    TransferPattern,
    AllocationRuleSet,
    StampTemplate,
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
    StampRun,
    PathIntent,
)


class V2APISerializerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='api-admin',
            password='admin',
            email='api-admin@example.local',
        )

    def _stamp_with_path_intent(self):
        result = stamp_roce_4plane_mini_fabric()
        PathIntent.objects.create(
            fabric=result.fabric,
            name='Proof path intent',
            plane=result.source_lanes[0].plane,
            source_channel=result.source_lanes[0].channel,
            destination_channel=result.destination_lanes[0].channel,
            source_endpoint=result.source_lanes[0].endpoint,
            destination_endpoint=result.destination_lanes[0].endpoint,
            selector={'pair_key': result.source_lanes[0].pair_key},
        )
        return result

    def test_every_v2_model_has_event_safe_serializer(self):
        self._stamp_with_path_intent()

        for model in V2_MODELS:
            with self.subTest(model=model.__name__):
                instance = model.objects.first()
                self.assertIsNotNone(instance)
                serializer_class = get_serializer_for_model(model)
                self.assertEqual(serializer_class.Meta.model, model)

                payload = serialize_for_event(instance)

                self.assertEqual(payload['id'], instance.pk)
                self.assertIn('display', payload)

    def test_every_v2_registered_api_endpoint_renders_list_and_detail(self):
        self.client.force_login(self.user)
        self._stamp_with_path_intent()

        for spec in V2_OBJECT_SPECS:
            with self.subTest(spec=spec.registry_key):
                instance = spec.model.objects.first()
                self.assertIsNotNone(instance)

                list_response = self.client.get(reverse(spec.api_list_url_name))
                self.assertEqual(list_response.status_code, 200)

                detail_response = self.client.get(
                    reverse(spec.api_detail_url_name, kwargs={'pk': instance.pk})
                )
                self.assertEqual(detail_response.status_code, 200)
                self.assertEqual(detail_response.json()['id'], instance.pk)

    def test_path_query_api_resolves_selected_lanes(self):
        self.client.force_login(self.user)
        result = self._stamp_with_path_intent()
        source = result.source_lanes[0]
        destination = result.destination_lanes[0]

        response = self.client.get(
            reverse('plugins-api:netbox_plant_graph-api:path-query'),
            {
                'source_lane': source.pk,
                'destination_lane': destination.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['path_found'], payload['error'])
        self.assertEqual(payload['source_lane_id'], source.pk)
        self.assertEqual(payload['destination_lane_id'], destination.pk)
        self.assertIn('transfer_map', [step['step_type'] for step in payload['steps']])

    def test_path_query_api_requires_source_lane(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins-api:netbox_plant_graph-api:path-query'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['detail'], 'source_lane query parameter is required.')
