from types import SimpleNamespace
from unittest import TestCase

import strawberry
from django.contrib.auth import get_user_model
from django.test.client import RequestFactory

from netbox_plant_graph.graphql import schema as package_schema
from netbox_plant_graph.graphql.filters import GRAPHQL_FILTER_CLASS_MAP
from netbox_plant_graph.graphql.schema import NetBoxPlantGraphQuery, schema
from netbox_plant_graph.graphql.types import GRAPHQL_TYPE_CLASS_MAP
from netbox_plant_graph.models import Fabric
from netbox_plant_graph.services.sync import rebuild_graph

from .topology import PlantGraphTopologyMixin


class GraphQLSchemaRegistrationTestCase(TestCase):
    def test_schema_exists(self):
        self.assertIsNotNone(schema)
        self.assertEqual(schema, package_schema)

    def test_generated_type_registry_exists(self):
        self.assertTrue(GRAPHQL_TYPE_CLASS_MAP)

    def test_generated_filter_registry_exists(self):
        self.assertTrue(GRAPHQL_FILTER_CLASS_MAP)


class GraphQLExecutionTestCase(PlantGraphTopologyMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.request_factory = RequestFactory()
        cls.graphql_user = get_user_model().objects.create_superuser(
            username='graphql-admin',
            email='graphql@example.com',
            password='password',
        )

    def test_graphql_exposes_generated_and_operational_queries(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL')
        rebuild_graph(scope={'fabric': fabric})

        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecutePlantGraph($fabricId: ID!, $sourceId: ID!, $destinationId: ID!) {
          netboxPlantGraphFabricList {
            name
          }
          resolvePath(
            sourceRegistryKey: "interface"
            sourceId: $sourceId
            destinationRegistryKey: "interface"
            destinationId: $destinationId
          )
          planeAudit(fabricId: $fabricId)
          blastRadius(targetRegistryKey: "interface", targetId: $sourceId)
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={
                'fabricId': fabric.pk,
                'sourceId': topology['interface_a'].pk,
                'destinationId': topology['interface_b'].pk,
            },
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['netboxPlantGraphFabricList'][0]['name'], 'Fabric GraphQL')
        self.assertTrue(result.data['resolvePath']['path_found'])
        self.assertTrue(result.data['planeAudit']['findings'])
        self.assertGreaterEqual(len(result.data['blastRadius']['impacted_objects']), 2)

    def test_graphql_operational_queries_support_signal_lane_resolution_from_core_interfaces(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Signal Lane')
        rebuild_graph(scope={'fabric': fabric})
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteLaneQueries($sourceId: ID!, $destinationId: ID!) {
          resolvePath(
            sourceRegistryKey: "interface"
            sourceId: $sourceId
            destinationRegistryKey: "interface"
            destinationId: $destinationId
            resolution: "signal_lane"
          )
          blastRadius(
            targetRegistryKey: "interface"
            targetId: $sourceId
            resolution: "signal_lane"
          )
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={
                'sourceId': topology['host_children'][2].pk,
                'destinationId': topology['leaf_children'][3].pk,
            },
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertTrue(result.data['resolvePath']['path_found'])
        self.assertEqual(result.data['resolvePath']['resolution'], 'signal_lane')
        self.assertTrue(any(step.get('kind') == 'lane_map' for step in result.data['resolvePath']['path']))
        self.assertGreaterEqual(len(result.data['blastRadius']['impacted_objects']), 1)

    def test_graphql_exposes_lane_drilldown_for_core_interface_targets(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Lane Drilldown')
        rebuild_graph(scope={'fabric': fabric})
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteLaneDrilldown($targetId: ID!) {
          laneDrilldown(
            targetRegistryKey: "interface"
            targetId: $targetId
            laneIndex: 2
          )
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'targetId': topology['host_children'][2].pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['laneDrilldown']['lane_index'], 2)
        self.assertEqual(result.data['laneDrilldown']['total_attachment_units'], 1)
        self.assertEqual(result.data['laneDrilldown']['total_signal_lanes'], 1)
        self.assertEqual(result.data['laneDrilldown']['attachment_units'][0]['lanes'][0]['display'], 'nic0/plane2:l2')

    def test_graphql_exposes_fabric_health_summary(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Health')
        rebuild_graph(scope={'fabric': fabric})
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteHealth($fabricId: ID!) {
          fabricHealth(fabricId: $fabricId)
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'fabricId': fabric.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['fabricHealth']['fabric']['pk'], fabric.pk)
        self.assertEqual(result.data['fabricHealth']['summary']['planes_total'], 4)
