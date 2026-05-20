from decimal import Decimal

from django.test import TestCase

from netbox_plant_graph.models import (
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
    TransferMap,
    TransportChannel,
)
from netbox_plant_graph.services.resolver import resolve_optical_lane_path


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
        strand = FiberStrand.objects.create(segment=segment, strand_index=1)
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

    def test_resolver_walks_fiber_and_shuffle_transfer_edges(self):
        gpu_node = self._node('GPU-1', 'active_device')
        leaf_node = self._node('LEAF-1', 'active_device')
        shuffle_node = self._node('SHUFFLE-1', 'passive_assembly')

        gpu_osfp = self._endpoint(gpu_node, 'OSFP-1', kind='plugin_port', connector='osfp')
        gpu_mpo = self._endpoint(gpu_node, 'OSFP-1.MPO-1', kind='subconnector', parent=gpu_osfp)
        gpu_position = self._position(gpu_mpo, 1)
        gpu_channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=gpu_osfp,
            plane=self.plane,
            name='GPU-CH-1',
            channel_index=1,
        )

        leaf_osfp = self._endpoint(leaf_node, 'OSFP-1', kind='plugin_port', connector='osfp')
        leaf_mpo = self._endpoint(leaf_node, 'OSFP-1.MPO-1', kind='subconnector', parent=leaf_osfp)
        leaf_position = self._position(leaf_mpo, 1)
        leaf_channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=leaf_osfp,
            plane=self.plane,
            name='LEAF-CH-1',
            channel_index=1,
        )

        shuffle_front = self._endpoint(shuffle_node, 'FRONT.MPO-1')
        shuffle_rear = self._endpoint(shuffle_node, 'REAR.MPO-1')
        shuffle_front_position = self._position(shuffle_front, 1)
        shuffle_rear_position = self._position(shuffle_rear, 9)

        self._strand('GPU-to-shuffle', gpu_mpo, gpu_position, shuffle_front, shuffle_front_position)
        TransferMap.objects.create(
            fabric=self.fabric,
            owner_node=shuffle_node,
            map_kind='shuffle_2x2',
            src_position=shuffle_front_position,
            dst_position=shuffle_rear_position,
        )
        self._strand('Shuffle-to-leaf', shuffle_rear, shuffle_rear_position, leaf_mpo, leaf_position)

        source = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=gpu_osfp,
            channel=gpu_channel,
            plane=self.plane,
            local_mpo_endpoint=gpu_mpo,
            local_mpo_position=gpu_position,
            lane_index=1,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=Decimal('1311.000'),
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
            wavelength_nm=Decimal('1311.000'),
        )

        path = resolve_optical_lane_path(source=source, destination=destination)

        self.assertTrue(path.path_found, path.error)
        self.assertEqual(path.destination_lane_id, destination.pk)
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
        node = self._node('NODE-1', 'active_device')
        source_osfp = self._endpoint(node, 'OSFP-1', kind='plugin_port', connector='osfp')
        source_mpo = self._endpoint(node, 'OSFP-1.MPO-1', kind='subconnector', parent=source_osfp)
        source_position = self._position(source_mpo, 1)

        peer_osfp = self._endpoint(node, 'OSFP-2', kind='plugin_port', connector='osfp')
        peer_mpo = self._endpoint(node, 'OSFP-2.MPO-1', kind='subconnector', parent=peer_osfp)
        peer_position = self._position(peer_mpo, 1)
        self._strand('Direct', source_mpo, source_position, peer_mpo, peer_position)

        source = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=source_osfp,
            plane=self.plane,
            local_mpo_endpoint=source_mpo,
            local_mpo_position=source_position,
            lane_index=1,
            direction='send',
            wavelength_nm=Decimal('1311.000'),
        )
        destination = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=peer_osfp,
            plane=self.plane,
            local_mpo_endpoint=peer_mpo,
            local_mpo_position=peer_position,
            lane_index=1,
            direction='receive',
            wavelength_nm=Decimal('1313.000'),
        )

        path = resolve_optical_lane_path(source=source, destination=destination)

        self.assertFalse(path.path_found)
