from decimal import Decimal

from django.test import TestCase
from dcim.models import Site

from netbox_plant_graph.models import (
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StrandTermination,
    SuppressionRule,
    TransferMap,
    TransportChannel,
)
from netbox_plant_graph.services.resolver import (
    build_lane_drilldown_report,
    build_path_resolver_matrix,
    compare_optical_lane_paths,
    compute_unavailability_blast_radius,
    resolve_optical_lane_path,
)


class V2ResolverTestCase(TestCase):
    def setUp(self):
        architecture = FabricArchitecture.objects.create(
            name='ROCE 4 Plane Shuffle',
            slug='roce-4-plane-shuffle',
            version='v2',
        )
        self.fabric = Fabric.objects.create(
            architecture=architecture,
            name='Resolver Fabric',
            slug='resolver-fabric',
            scope_site=Site.objects.create(name='Resolver Site', slug='resolver-site', status='active'),
        )
        self.plane = Plane.objects.create(fabric=self.fabric, plane_number=1)

    def _node(self, name, kind):
        return FabricNode.objects.create(
            fabric=self.fabric,
            name=name,
            address=name,
            node_kind=kind,
        )

    def _endpoint(self, node, name, kind='connector', connector='mpo-12', parent=None):
        return Endpoint.objects.create(
            fabric=self.fabric,
            node=node,
            parent=parent,
            name=name,
            address=f'{node.address}.{name}',
            endpoint_kind=kind,
            connector_kind=connector,
            position_count=12 if connector == 'mpo-12' else 0,
        )

    def _position(self, endpoint, number):
        return ConnectorPosition.objects.create(endpoint=endpoint, position_number=number)

    def _strand(self, name, a_endpoint, a_position, b_endpoint, b_position):
        segment = FiberSegment.objects.create(
            fabric=self.fabric,
            name=name,
            a_endpoint=a_endpoint,
            b_endpoint=b_endpoint,
        )
        cable_assembly = CableAssembly.objects.create(
            site=self.fabric.scope_site,
            cable_id=f'{self.fabric.slug}:{name}',
            manufacturer='Resolver Fixture',
            model_id='JUMPER',
            description=f'Fixture cable for {name}',
        )
        strand = FiberStrand.objects.create(
            segment=segment,
            strand_index=1,
            cable_site=cable_assembly.site,
            cable_id=cable_assembly.cable_id,
        )
        StrandTermination.objects.create(
            strand=strand,
            mpo_endpoint=a_endpoint,
            mpo_position=a_position,
            termination_index=1,
        )
        StrandTermination.objects.create(
            strand=strand,
            mpo_endpoint=b_endpoint,
            mpo_position=b_position,
            termination_index=2,
        )
        return strand

    def _build_shuffle_path(
        self,
        *,
        prefix: str,
        source_wavelength: str = '1311.000',
        destination_wavelength: str | None = None,
        source_lane_index: int = 1,
    ):
        destination_wavelength = destination_wavelength or source_wavelength
        gpu_node = self._node(f'{prefix}-GPU', 'active_device')
        leaf_node = self._node(f'{prefix}-LEAF', 'active_device')
        shuffle_node = self._node(f'{prefix}-SHUFFLE', 'passive_assembly')

        gpu_osfp = self._endpoint(gpu_node, 'OSFP-1', kind='plugin_port', connector='osfp')
        gpu_mpo = self._endpoint(gpu_node, 'OSFP-1.MPO-1', kind='subconnector', parent=gpu_osfp)
        gpu_position = self._position(gpu_mpo, 1)
        gpu_channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=gpu_osfp,
            plane=self.plane,
            name=f'{prefix}-GPU-CH-1',
            channel_index=1,
        )

        leaf_osfp = self._endpoint(leaf_node, 'OSFP-1', kind='plugin_port', connector='osfp')
        leaf_mpo = self._endpoint(leaf_node, 'OSFP-1.MPO-1', kind='subconnector', parent=leaf_osfp)
        leaf_position = self._position(leaf_mpo, 1)
        leaf_channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=leaf_osfp,
            plane=self.plane,
            name=f'{prefix}-LEAF-CH-1',
            channel_index=1,
        )

        shuffle_front = self._endpoint(shuffle_node, 'FRONT.MPO-1')
        shuffle_rear = self._endpoint(shuffle_node, 'REAR.MPO-1')
        shuffle_front_position = self._position(shuffle_front, 1)
        shuffle_rear_position = self._position(shuffle_rear, 9)

        self._strand(f'{prefix}-GPU-to-shuffle', gpu_mpo, gpu_position, shuffle_front, shuffle_front_position)
        transfer_map = TransferMap.objects.create(
            fabric=self.fabric,
            owner_node=shuffle_node,
            map_kind='shuffle_2x2',
            src_position=shuffle_front_position,
            dst_position=shuffle_rear_position,
        )
        self._strand(f'{prefix}-Shuffle-to-leaf', shuffle_rear, shuffle_rear_position, leaf_mpo, leaf_position)

        source = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=gpu_osfp,
            channel=gpu_channel,
            plane=self.plane,
            local_mpo_endpoint=gpu_mpo,
            local_mpo_position=gpu_position,
            lane_index=source_lane_index,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=Decimal(source_wavelength),
        )
        destination = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=leaf_osfp,
            channel=leaf_channel,
            plane=self.plane,
            local_mpo_endpoint=leaf_mpo,
            local_mpo_position=leaf_position,
            lane_index=1,
            local_mpo_index=1,
            direction='receive',
            wavelength_nm=Decimal(destination_wavelength),
        )
        return {
            'source': source,
            'destination': destination,
            'transfer_map': transfer_map,
            'gpu_position': gpu_position,
            'leaf_position': leaf_position,
        }

    def _build_direct_path(
        self,
        *,
        prefix: str,
        source_wavelength: str,
        destination_wavelength: str,
        source_lane_index: int = 1,
    ):
        source_node = self._node(f'{prefix}-SRC', 'active_device')
        destination_node = self._node(f'{prefix}-DST', 'active_device')

        source_osfp = self._endpoint(source_node, 'OSFP-1', kind='plugin_port', connector='osfp')
        source_mpo = self._endpoint(source_node, 'OSFP-1.MPO-1', kind='subconnector', parent=source_osfp)
        source_position = self._position(source_mpo, 1)
        source_channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=source_osfp,
            plane=self.plane,
            name=f'{prefix}-SRC-CH-1',
            channel_index=1,
        )

        destination_osfp = self._endpoint(destination_node, 'OSFP-1', kind='plugin_port', connector='osfp')
        destination_mpo = self._endpoint(destination_node, 'OSFP-1.MPO-1', kind='subconnector', parent=destination_osfp)
        destination_position = self._position(destination_mpo, 1)
        destination_channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=destination_osfp,
            plane=self.plane,
            name=f'{prefix}-DST-CH-1',
            channel_index=1,
        )

        strand = self._strand(
            f'{prefix}-direct',
            source_mpo,
            source_position,
            destination_mpo,
            destination_position,
        )
        source = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=source_osfp,
            channel=source_channel,
            plane=self.plane,
            local_mpo_endpoint=source_mpo,
            local_mpo_position=source_position,
            lane_index=source_lane_index,
            direction='send',
            wavelength_nm=Decimal(source_wavelength),
        )
        destination = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=destination_osfp,
            channel=destination_channel,
            plane=self.plane,
            local_mpo_endpoint=destination_mpo,
            local_mpo_position=destination_position,
            lane_index=1,
            direction='receive',
            wavelength_nm=Decimal(destination_wavelength),
        )
        return {
            'source': source,
            'destination': destination,
            'strand': strand,
            'source_position': source_position,
            'destination_position': destination_position,
        }

    def test_resolver_walks_fiber_and_shuffle_transfer_edges(self):
        graph = self._build_shuffle_path(prefix='A')
        path = resolve_optical_lane_path(source=graph['source'], destination=graph['destination'])

        self.assertTrue(path.path_found, path.error)
        self.assertEqual(path.destination_lane_id, graph['destination'].pk)
        self.assertEqual(
            [step.step_type for step in path.steps],
            [
                'source_lane',
                'source_position',
                'fiber_strand',
                'connector_position',
                'transfer_map',
                'connector_position',
                'fiber_strand',
                'connector_position',
                'destination_position',
                'destination_lane',
            ],
        )

    def test_resolver_rejects_wrong_wavelength_destination(self):
        graph = self._build_direct_path(
            prefix='B',
            source_wavelength='1311.000',
            destination_wavelength='1313.000',
        )
        path = resolve_optical_lane_path(source=graph['source'], destination=graph['destination'])
        self.assertFalse(path.path_found)

    def test_lane_drilldown_report_includes_hop_suppressions(self):
        graph = self._build_shuffle_path(prefix='C')
        SuppressionRule.objects.create(
            fabric=self.fabric,
            status='active',
            reason='Transfer map outage',
            path_hop_object_type='transfer_map',
            path_hop_object_id=graph['transfer_map'].pk,
        )

        report = build_lane_drilldown_report(source=graph['source'], destination=graph['destination'])

        self.assertFalse(report.lane_suppression.suppressed)
        self.assertFalse(report.resolved_path.path_found)
        self.assertIsNotNone(report.blocking_hop_suppression)
        self.assertEqual(report.blocking_hop_suppression.object_type, 'transfer_map')
        self.assertEqual(report.blocking_hop_suppression.object_id, graph['transfer_map'].pk)
        self.assertTrue(report.blocking_hop_suppression.suppressed)

    def test_compare_optical_lane_paths_reports_deltas(self):
        baseline = self._build_direct_path(
            prefix='D1',
            source_wavelength='1311.000',
            destination_wavelength='1311.000',
        )
        candidate = self._build_direct_path(
            prefix='D2',
            source_wavelength='1315.000',
            destination_wavelength='1311.000',
        )

        report = compare_optical_lane_paths(
            baseline=baseline['source'],
            candidate=candidate['source'],
        )

        self.assertTrue(report.baseline_path.path_found)
        self.assertFalse(report.candidate_path.path_found)
        self.assertFalse(report.checks['wavelength_match'])
        self.assertTrue(report.checks['plane_match'])
        self.assertFalse(report.checks['endpoint_match'])
        self.assertEqual(report.deltas['reachability_delta'], -1)

    def test_compute_unavailability_blast_radius_for_transfer_map(self):
        impacted_graph = self._build_shuffle_path(prefix='E1')
        unaffected_graph = self._build_direct_path(
            prefix='E2',
            source_wavelength='1311.000',
            destination_wavelength='1311.000',
        )

        report = compute_unavailability_blast_radius(unavailable=impacted_graph['transfer_map'])

        impacted_lane_ids = {lane['lane_id'] for lane in report.impacted_lanes}
        self.assertIn(impacted_graph['source'].pk, impacted_lane_ids)
        self.assertNotIn(unaffected_graph['source'].pk, impacted_lane_ids)
        self.assertIn(impacted_graph['transfer_map'].pk, report.impacted_entities['transfer_map_ids'])
        self.assertTrue(any(impact.source_lane['lane_id'] == impacted_graph['source'].pk for impact in report.impacts))

    def test_build_path_resolver_matrix_returns_batch_resolution_rows(self):
        resolved_graph = self._build_direct_path(
            prefix='F1',
            source_wavelength='1311.000',
            destination_wavelength='1311.000',
        )
        unresolved_graph = self._build_direct_path(
            prefix='F2',
            source_wavelength='1317.000',
            destination_wavelength='1319.000',
        )

        rows = build_path_resolver_matrix(fabric_id=self.fabric.pk)
        rows_by_source_lane = {row.source_lane['lane_id']: row for row in rows}

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows_by_source_lane[resolved_graph['source'].pk].path_found)
        self.assertIsNotNone(rows_by_source_lane[resolved_graph['source'].pk].destination_lane)
        self.assertFalse(rows_by_source_lane[unresolved_graph['source'].pk].path_found)
        self.assertIsNone(rows_by_source_lane[unresolved_graph['source'].pk].destination_lane)
