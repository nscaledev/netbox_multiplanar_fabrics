from datetime import timedelta
from types import SimpleNamespace
from unittest import TestCase

import strawberry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.test.client import RequestFactory
from django.utils import timezone
from dcim.models import Site

from netbox_plant_graph.graphql import schema as package_schema
from netbox_plant_graph.graphql.filters import GRAPHQL_FILTER_CLASS_MAP
from netbox_plant_graph.graphql.schema import NetBoxPlantGraphQuery, schema
from netbox_plant_graph.graphql.types import GRAPHQL_TYPE_CLASS_MAP
from netbox_plant_graph.models import AuditFinding, DisjointnessException, Fabric, PlantNode, UnresolvedStateSummary
from netbox_plant_graph.services import (
    reopen_audit_finding,
    resolve_audit_finding,
    run_persistent_plane_audit,
    suppress_audit_finding,
)
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

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_graphql_exposes_unresolved_summary_list_detail_and_observations(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Unresolved State')
        rebuild_graph(scope={'fabric': fabric})
        summary = UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').order_by('pk').first()
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteUnresolvedState($summaryId: ID!) {
          netboxPlantGraphUnresolvedStateSummaryList {
            id
            scopeLabel
            summaryKind
            causeCode
            active
            ownerObjectId
            representativeObjectId
            firstSeenAt
            lastSeenAt
          }
          netboxPlantGraphUnresolvedStateSummary(id: $summaryId) {
            id
            causeCode
            summaryKind
            active
            fingerprint
            planeIds
          }
          netboxPlantGraphUnresolvedStateObservationList {
            id
            observedAt
            summary {
              pk
            }
            build {
              pk
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'summaryId': summary.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertTrue(any(
            item['causeCode'] == 'missing_port_mapping'
            and item['summaryKind'] == 'segment'
            and item['active']
            for item in result.data['netboxPlantGraphUnresolvedStateSummaryList']
        ))
        self.assertEqual(result.data['netboxPlantGraphUnresolvedStateSummary']['id'], str(summary.pk))
        self.assertEqual(result.data['netboxPlantGraphUnresolvedStateSummary']['causeCode'], 'missing_port_mapping')
        self.assertEqual(result.data['netboxPlantGraphUnresolvedStateSummary']['summaryKind'], 'segment')
        self.assertTrue(result.data['netboxPlantGraphUnresolvedStateSummary']['active'])
        self.assertTrue(any(
            item['summary']['pk'] == str(summary.pk)
            and item['build']['pk'] is not None
            for item in result.data['netboxPlantGraphUnresolvedStateObservationList']
        ))

    def test_graphql_exposes_typed_lane_first_queries(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Typed Lane Ops')
        rebuild_graph(scope={'fabric': fabric})
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteTypedLaneOps($fabricId: ID!, $sourceId: ID!, $destinationId: ID!) {
          laneDrilldownTyped(
            targetRegistryKey: "interface"
            targetId: $sourceId
            laneIndex: 1
          ) {
            target { display }
            laneIndex
            totalSignalLanes
            attachmentUnits {
              attachmentUnit { display }
              lanes { display }
            }
          }
          fabricHealthTyped(fabricId: $fabricId) {
            fabric { display }
            status
            summary { planesTotal healthyPlanes }
            findings { name value }
          }
          lanePath(
            sourceRegistryKey: "interface"
            sourceId: $sourceId
            sourceLaneIndex: 2
            destinationRegistryKey: "interface"
            destinationId: $destinationId
            destinationLaneIndex: 2
          ) {
            pathFound
            sourceLaneIndex
            destinationLaneIndex
            steps {
              stepKind
              display
              signalLane { display }
              attachmentUnit { display }
              plantNode { display }
            }
            summary {
              transferMapsCrossed
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={
                'fabricId': fabric.pk,
                'sourceId': topology['host_children'][2].pk,
                'destinationId': topology['leaf_children'][3].pk,
            },
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['laneDrilldownTyped']['target']['display'], 'nic0/plane2')
        self.assertEqual(result.data['laneDrilldownTyped']['laneIndex'], 1)
        self.assertEqual(result.data['laneDrilldownTyped']['totalSignalLanes'], 1)
        self.assertEqual(result.data['fabricHealthTyped']['fabric']['display'], 'Fabric GraphQL Typed Lane Ops')
        self.assertEqual(result.data['fabricHealthTyped']['summary']['planesTotal'], 4)
        self.assertTrue(result.data['lanePath']['pathFound'])
        self.assertEqual(result.data['lanePath']['sourceLaneIndex'], 2)
        self.assertEqual(result.data['lanePath']['destinationLaneIndex'], 2)
        self.assertTrue(any(
            step.get('signalLane') and step['signalLane']['display'] == 'nic0/plane2:l2'
            for step in result.data['lanePath']['steps']
        ))

    def test_graphql_exposes_lane_set_and_allocation_summary_queries(self):
        topology = self.build_profile_breakout_with_missing_peer_positions_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Lane Set')
        rebuild_graph(scope={'fabric': fabric})
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user
        plane = fabric.planes.get(plane_number=1)

        query = '''
        query ExecuteLaneSet($parentId: ID!, $planeId: ID!) {
          laneSet(targetRegistryKey: "interface", targetId: $parentId) {
            scopeKind
            totalAttachmentUnits
            expectedLaneTotal
            presentLaneTotal
            mappedLaneTotal
            unmatchedPeerPositions
            laneMapConsistency
            attachmentUnits {
              attachmentUnit { display }
              status
              position
            }
          }
          laneAllocationSummary(targetRegistryKey: "interface", targetId: $parentId) {
            scopeKind
            expectedLaneTotal
            presentLaneTotal
            mappedLaneTotal
            incompleteAttachmentUnits
            unmatchedPeerPositions
            laneMapConsistency
          }
          planeLaneSet: laneSet(targetRegistryKey: "fabricplane", targetId: $planeId) {
            scopeKind
            plane { display }
            totalAttachmentUnits
            planeConsistency
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={
                'parentId': topology['host_parent'].pk,
                'planeId': plane.pk,
            },
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['laneSet']['scopeKind'], 'parent_interface')
        self.assertEqual(result.data['laneSet']['expectedLaneTotal'], 16)
        self.assertEqual(result.data['laneSet']['mappedLaneTotal'], 12)
        self.assertEqual(result.data['laneSet']['unmatchedPeerPositions'], [4])
        self.assertEqual(result.data['laneAllocationSummary']['incompleteAttachmentUnits'], 1)
        self.assertEqual(result.data['laneAllocationSummary']['laneMapConsistency'], 'partial')
        self.assertEqual(result.data['planeLaneSet']['scopeKind'], 'plane')
        self.assertEqual(result.data['planeLaneSet']['plane']['display'], f'{fabric}:1')

    def test_graphql_exposes_lane_compare_query(self):
        baseline = self.build_multiplane_shuffle_topology()
        candidate_site = Site.objects.create(name='Compare GraphQL Candidate Site', slug='compare-graphql-candidate-site')
        candidate = self.build_profile_breakout_with_missing_peer_positions_topology(site=candidate_site)
        candidate['host_device'].name = 'GPU Host Candidate GraphQL'
        candidate['host_device'].save()
        candidate['shuffle_device'].name = 'Shuffle Module Candidate GraphQL'
        candidate['shuffle_device'].save()
        fabric = Fabric.objects.create(name='Fabric GraphQL Lane Compare')
        rebuild_graph(scope={'fabric': fabric})
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteLaneCompare($baselineId: ID!, $candidateId: ID!) {
          laneCompare(
            baselineRegistryKey: "interface"
            baselineId: $baselineId
            candidateRegistryKey: "interface"
            candidateId: $candidateId
          ) {
            metrics {
              name
              baselineValue
              candidateValue
              status
            }
            regressions
            planeIdsAdded
            planeIdsRemoved
            policyRegressions
            policyDomainDeltas {
              status
              planeIds
              artifacts { display }
            }
            attachmentDiffs {
              compareKey
              changedFields
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={
                'baselineId': baseline['host_parent'].pk,
                'candidateId': candidate['host_parent'].pk,
            },
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        mapped_metric = next(metric for metric in result.data['laneCompare']['metrics'] if metric['name'] == 'mapped_lane_total')
        self.assertEqual(mapped_metric['baselineValue'], 16)
        self.assertEqual(mapped_metric['candidateValue'], 12)
        self.assertEqual(mapped_metric['status'], 'regressed')
        self.assertTrue(any('Mapping symmetry regressed' in item for item in result.data['laneCompare']['regressions']))
        self.assertIn('policyRegressions', result.data['laneCompare'])
        self.assertIn('policyDomainDeltas', result.data['laneCompare'])
        self.assertTrue(any(item['compareKey'] == 'position:4' for item in result.data['laneCompare']['attachmentDiffs']))

    def test_graphql_exposes_audit_workflow_summary_query(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Audit Workflow')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=9)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        resolve_audit_finding(finding=finding, actor=self.graphql_user, note='Closed for churn')
        reopen_audit_finding(finding=finding, actor=self.graphql_user, note='Recurrence observed')
        suppress_audit_finding(finding=finding, actor=self.graphql_user, reason='Windowed change', days=3)

        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteAuditWorkflowSummary($fabricId: ID!) {
          auditWorkflowSummary(fabricId: $fabricId) {
            fabric { display }
            totalFindings
            activeFindings
            suppressedFindings
            staleFindings7d
            statusCounts { name value }
            churnWindows {
              days
              openedCount
              reopenedCount
              resolvedCount
              suppressedCount
            }
            recentRuns {
              run { display }
              newCount
            }
            recentEvents {
              eventType
              message
            }
            expiringSuppressions {
              reason
              remainingDays
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'fabricId': fabric.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['auditWorkflowSummary']['fabric']['display'], 'Fabric GraphQL Audit Workflow')
        self.assertGreaterEqual(result.data['auditWorkflowSummary']['totalFindings'], 1)
        self.assertEqual(result.data['auditWorkflowSummary']['suppressedFindings'], 1)
        self.assertGreaterEqual(result.data['auditWorkflowSummary']['staleFindings7d'], 1)
        churn_7d = next(item for item in result.data['auditWorkflowSummary']['churnWindows'] if item['days'] == 7)
        self.assertGreaterEqual(churn_7d['openedCount'], 1)
        self.assertGreaterEqual(churn_7d['reopenedCount'], 1)
        self.assertGreaterEqual(churn_7d['resolvedCount'], 1)
        self.assertGreaterEqual(churn_7d['suppressedCount'], 1)
        self.assertTrue(result.data['auditWorkflowSummary']['recentRuns'])
        self.assertTrue(any(event['eventType'] == 'suppressed' for event in result.data['auditWorkflowSummary']['recentEvents']))
        self.assertEqual(result.data['auditWorkflowSummary']['expiringSuppressions'][0]['reason'], 'Windowed change')

    def test_graphql_exposes_durable_audit_finding_search_and_detail_queries(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Durable Finding Search')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        finding = AuditFinding.objects.filter(fabric=fabric, finding_type='missing_plane_membership').first()
        finding.first_seen_at = timezone.now() - timedelta(days=11)
        finding.save(update_fields=('first_seen_at', 'last_updated'))
        suppress_audit_finding(finding=finding, actor=self.graphql_user, reason='Windowed change', days=3)

        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteDurableFindingQueries($fabricId: ID!, $findingId: ID!) {
          auditFindingSearch(
            fabricId: $fabricId
            suppressed: true
            minAgeDays: 7
            limit: 10
          ) {
            finding { pk display }
            findingType
            status
            ageDays
            activeSuppression {
              reason
            }
          }
          auditFindingDetail(findingId: $findingId) {
            finding {
              finding { pk }
              message
              acknowledgedByDisplay
              activeSuppression {
                reason
              }
            }
            recentEvents {
              eventType
              newStatus
              run {
                display
              }
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'fabricId': fabric.pk, 'findingId': finding.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['auditFindingSearch'][0]['finding']['pk'], finding.pk)
        self.assertEqual(result.data['auditFindingSearch'][0]['status'], 'suppressed')
        self.assertGreaterEqual(result.data['auditFindingSearch'][0]['ageDays'], 7)
        self.assertEqual(result.data['auditFindingSearch'][0]['activeSuppression']['reason'], 'Windowed change')
        self.assertEqual(result.data['auditFindingDetail']['finding']['finding']['pk'], finding.pk)
        self.assertEqual(result.data['auditFindingDetail']['finding']['activeSuppression']['reason'], 'Windowed change')
        self.assertTrue(any(event['eventType'] == 'suppressed' for event in result.data['auditFindingDetail']['recentEvents']))
        self.assertTrue(any(event['run'] for event in result.data['auditFindingDetail']['recentEvents']))

    def test_graphql_exposes_audit_run_timeline_query(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Run Timeline')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)

        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecuteAuditRunTimeline($fabricId: ID!) {
          auditRunTimeline(fabricId: $fabricId, limit: 5) {
            run { display }
            scopeLabel
            status
            findingCount
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'fabricId': fabric.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertTrue(result.data['auditRunTimeline'])
        self.assertTrue(result.data['auditRunTimeline'][0]['scopeLabel'])
        self.assertGreaterEqual(result.data['auditRunTimeline'][0]['findingCount'], 1)

    def test_graphql_exposes_policy_summary_and_contamination_domains(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Policy Review')
        rebuild_graph(scope={'fabric': fabric})
        plane = fabric.planes.get(plane_number=2)
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecutePolicyQueries($fabricId: ID!, $planeId: ID!) {
          policySummary(fabricId: $fabricId, planeId: $planeId) {
            fabric { display }
            policyMode
            plane { display }
            artifactShareCount
            edgeBridgeCount
            contaminationDomainCount
            planePairCount
            ruleCounts { name value }
          }
          contaminationDomains(fabricId: $fabricId, planeId: $planeId) {
            domainKey
            planeIds
            signalLaneCount
            artifacts { display }
            edgeBridges {
              ruleId
              leftPlaneIds
              rightPlaneIds
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'fabricId': fabric.pk, 'planeId': plane.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['policySummary']['fabric']['display'], 'Fabric GraphQL Policy Review')
        self.assertEqual(result.data['policySummary']['plane']['display'], f'{fabric}:2')
        self.assertGreaterEqual(result.data['policySummary']['artifactShareCount'], 1)
        self.assertGreaterEqual(result.data['policySummary']['contaminationDomainCount'], 1)
        self.assertTrue(any(item['name'] == 'shared_passive_artifact' for item in result.data['policySummary']['ruleCounts']))
        self.assertTrue(result.data['contaminationDomains'])
        self.assertTrue(any(artifact['display'] == 'Shuffle Module' for artifact in result.data['contaminationDomains'][0]['artifacts']))

    def test_graphql_exposes_policy_dashboard(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric GraphQL Policy Dashboard')
        rebuild_graph(scope={'fabric': fabric})
        run_persistent_plane_audit(fabric=fabric)
        shuffle_node = PlantNode.objects.get(fabric=fabric, name='Shuffle Module')
        DisjointnessException.objects.create(
            fabric=fabric,
            policy_mode=fabric.disjointness_policy,
            exception_type='shared_passive_artifact',
            target_type=ContentType.objects.get_for_model(PlantNode, for_concrete_model=False),
            target_id=shuffle_node.pk,
            plane_a=fabric.planes.get(plane_number=1),
            plane_b=fabric.planes.get(plane_number=2),
            scope_kind='artifact',
            status='approved',
            active=True,
        )
        strawberry_schema = strawberry.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user

        query = '''
        query ExecutePolicyDashboard($fabricId: ID!) {
          policyDashboard(fabricId: $fabricId) {
            fabric { display }
            policyMode
            activeExceptionCount
            contaminationDomainCount
            uncoveredEdgeBridgeCount
            planePairs {
              planePairIds
              uncoveredDomainCount
            }
            topDomains {
              domainKey
              coverageStatus
              highestRiskSeverity
              artifacts { display }
            }
            oldestActiveFindings {
              finding { display }
              findingType
              severity
            }
          }
        }
        '''

        result = strawberry_schema.execute_sync(
            query,
            variable_values={'fabricId': fabric.pk},
            context_value=SimpleNamespace(request=request),
        )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data['policyDashboard']['fabric']['display'], 'Fabric GraphQL Policy Dashboard')
        self.assertEqual(result.data['policyDashboard']['activeExceptionCount'], 1)
        self.assertGreaterEqual(result.data['policyDashboard']['contaminationDomainCount'], 1)
        self.assertTrue(result.data['policyDashboard']['planePairs'])
        self.assertTrue(result.data['policyDashboard']['topDomains'])
        self.assertTrue(result.data['policyDashboard']['oldestActiveFindings'])
        self.assertTrue(any(
            artifact['display'] == 'Shuffle Module'
            for artifact in result.data['policyDashboard']['topDomains'][0]['artifacts']
        ))


# ---------------------------------------------------------------------------
# Planning GraphQL query tests — Phase 7
# ---------------------------------------------------------------------------


class PlanningGraphQLQueryTestCase(PlantGraphTopologyMixin):
    """Tests for the 5 new planning GraphQL queries."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.request_factory = RequestFactory()
        cls.graphql_user = get_user_model().objects.create_superuser(
            username='planning-gql-admin',
            email='planning-gql@example.com',
            password='password',
        )
        from netbox_plant_graph.models import (
            AssemblyConnectorTemplate,
            AssemblyMappingTemplate,
            AssemblyTemplate,
            DeploymentPlan,
            RackPopulationTemplate,
            SpatialPlacement,
            SpatialTemplate,
            SpatialTemplateNode,
        )
        cls.assembly_template = AssemblyTemplate.objects.create(
            name='GQL Assembly Template',
            slug='gql-assembly-template',
            assembly_type='shuffle_trunk',
        )
        cls.a_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.assembly_template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=4,
        )
        cls.b_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.assembly_template, side='B', connector_number=1,
            connector_type='mpo-12', position_count=4,
        )
        AssemblyMappingTemplate.objects.create(
            template=cls.assembly_template,
            a_connector=cls.a_conn, a_position=1,
            b_connector=cls.b_conn, b_position=4,
        )
        cls.spatial_template = SpatialTemplate.objects.create(
            name='GQL Spatial Template',
            slug='gql-spatial-template',
            root_node_type='location',
        )
        SpatialTemplateNode.objects.create(
            template=cls.spatial_template,
            name_pattern='Row {n}',
            node_type='location',
            quantity=2,
        )
        cls.rack_population_template = RackPopulationTemplate.objects.create(
            name='GQL RPT',
            slug='gql-rpt',
        )
        cls.deployment_plan = DeploymentPlan.objects.create(name='GQL Test Plan')
        cls.site = Site.objects.create(name='GQL Planning Site', slug='gql-planning-site')
        from django.contrib.contenttypes.models import ContentType
        cls.site_ct = ContentType.objects.get_for_model(cls.site, for_concrete_model=False)
        SpatialPlacement.objects.create(
            target_type=cls.site_ct,
            target_id=cls.site.pk,
            reference_frame_type=cls.site_ct,
            reference_frame_id=cls.site.pk,
            position_x='10.0',
            position_y='20.0',
            coordinate_unit='meters',
        )

    def _execute(self, query, variable_values=None):
        import strawberry as sb
        strawberry_schema = sb.Schema(query=NetBoxPlantGraphQuery)
        request = self.request_factory.get('/graphql')
        request.user = self.graphql_user
        return strawberry_schema.execute_sync(
            query,
            variable_values=variable_values or {},
            context_value=SimpleNamespace(request=request),
        )

    def test_assembly_template_detail_query_returns_connectors(self):
        result = self._execute(
            'query T($id: ID!) { assemblyTemplateDetail(id: $id) }',
            variable_values={'id': self.assembly_template.pk},
        )
        self.assertIsNone(result.errors)
        data = result.data['assemblyTemplateDetail']
        self.assertEqual(data['id'], self.assembly_template.pk)
        self.assertEqual(len(data['connectors']), 2)
        self.assertEqual(len(data['mappings']), 1)

    def test_assembly_template_detail_query_returns_none_for_missing_id(self):
        result = self._execute(
            'query T($id: ID!) { assemblyTemplateDetail(id: $id) }',
            variable_values={'id': 99999999},
        )
        self.assertIsNone(result.errors)
        self.assertIsNone(result.data['assemblyTemplateDetail'])

    def test_spatial_template_detail_query_returns_nodes(self):
        result = self._execute(
            'query T($id: ID!) { spatialTemplateDetail(id: $id) }',
            variable_values={'id': self.spatial_template.pk},
        )
        self.assertIsNone(result.errors)
        data = result.data['spatialTemplateDetail']
        self.assertEqual(data['id'], self.spatial_template.pk)
        self.assertEqual(len(data['nodes']), 1)

    def test_deployment_plan_detail_query_returns_plan(self):
        result = self._execute(
            'query T($id: ID!) { deploymentPlanDetail(id: $id) }',
            variable_values={'id': self.deployment_plan.pk},
        )
        self.assertIsNone(result.errors)
        data = result.data['deploymentPlanDetail']
        self.assertEqual(data['id'], self.deployment_plan.pk)
        self.assertIn('status', data)
        self.assertIn('stamp_records', data)

    def test_spatial_placements_by_scope_returns_placements(self):
        result = self._execute(
            'query T($scopeType: String!, $scopeId: ID!) { spatialPlacementsByScope(scopeType: $scopeType, scopeId: $scopeId) }',
            variable_values={'scopeType': 'site', 'scopeId': self.site.pk},
        )
        self.assertIsNone(result.errors)
        data = result.data['spatialPlacementsByScope']
        self.assertIn('placements', data)
        self.assertEqual(len(data['placements']), 1)
        self.assertEqual(float(data['placements'][0]['position_x']), 10.0)

    def test_stamp_preview_query_returns_error_for_unknown_template_type(self):
        result = self._execute(
            'query T($tt: String!, $tid: ID!) { stampPreview(templateType: $tt, templateId: $tid) }',
            variable_values={'tt': 'nonexistent_type', 'tid': 1},
        )
        self.assertIsNone(result.errors)
        data = result.data['stampPreview']
        self.assertIn('error', data)
