from decimal import Decimal

from django.core.exceptions import ValidationError
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
    TransportChannel,
)


class V2ModelSemanticsTestCase(TestCase):
    def setUp(self):
        self.architecture = FabricArchitecture.objects.create(
            name='ROCE 4 Plane Shuffle',
            slug='roce-4-plane-shuffle',
            version='v2',
        )
        self.fabric = Fabric.objects.create(
            architecture=self.architecture,
            name='Fabric V2',
            slug='fabric-v2',
        )
        self.plane = Plane.objects.create(fabric=self.fabric, plane_number=1, label='Plane 1')
        self.node = FabricNode.objects.create(
            fabric=self.fabric,
            name='GPU-1',
            address='GPU-1',
            node_kind='active_device',
        )
        self.osfp = Endpoint.objects.create(
            fabric=self.fabric,
            node=self.node,
            name='OSFP-1',
            address='GPU-1.OSFP-1',
            endpoint_kind='plugin_port',
            connector_kind='osfp',
        )
        self.mpo = Endpoint.objects.create(
            fabric=self.fabric,
            node=self.node,
            parent=self.osfp,
            name='OSFP-1.MPO-1',
            address='GPU-1.OSFP-1.MPO-1',
            endpoint_kind='subconnector',
            connector_kind='mpo-12',
            position_count=12,
        )
        self.position_1 = ConnectorPosition.objects.create(endpoint=self.mpo, position_number=1)
        self.position_2 = ConnectorPosition.objects.create(endpoint=self.mpo, position_number=2)
        self.channel = TransportChannel.objects.create(
            fabric=self.fabric,
            endpoint=self.osfp,
            plane=self.plane,
            name='CH-1',
            channel_index=1,
            speed_gbps=200,
        )

    def test_optical_lane_is_transceiver_local(self):
        lane = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=self.osfp,
            channel=self.channel,
            plane=self.plane,
            local_mpo_endpoint=self.mpo,
            local_mpo_position=self.position_1,
            lane_index=1,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=Decimal('1311.000'),
            nominal_rate_gbps=100,
        )

        lane.full_clean()

        self.assertEqual(lane.endpoint, self.osfp)
        self.assertEqual(lane.local_mpo_endpoint, self.mpo)
        self.assertEqual(lane.local_mpo_position, self.position_1)
        self.assertEqual(lane.direction, 'send')

    def test_optical_lane_requires_position_on_declared_mpo_endpoint(self):
        other_mpo = Endpoint.objects.create(
            fabric=self.fabric,
            node=self.node,
            parent=self.osfp,
            name='OSFP-1.MPO-2',
            address='GPU-1.OSFP-1.MPO-2',
            endpoint_kind='subconnector',
            connector_kind='mpo-12',
            position_count=12,
        )
        other_position = ConnectorPosition.objects.create(endpoint=other_mpo, position_number=1)
        lane = OpticalLane(
            fabric=self.fabric,
            endpoint=self.osfp,
            channel=self.channel,
            plane=self.plane,
            local_mpo_endpoint=self.mpo,
            local_mpo_position=other_position,
            lane_index=1,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=Decimal('1311.000'),
        )

        with self.assertRaises(ValidationError) as raised:
            lane.full_clean()

        self.assertIn('local_mpo_position', raised.exception.error_dict)

    def test_strand_termination_normalizes_fiber_to_mpo_position(self):
        peer_node = FabricNode.objects.create(
            fabric=self.fabric,
            name='Panel-1',
            address='Panel-1',
            node_kind='passive_assembly',
        )
        peer_mpo = Endpoint.objects.create(
            fabric=self.fabric,
            node=peer_node,
            name='FRONT.MPO-1',
            address='Panel-1.FRONT.MPO-1',
            endpoint_kind='connector',
            connector_kind='mpo-12',
            position_count=12,
        )
        peer_position = ConnectorPosition.objects.create(endpoint=peer_mpo, position_number=1)
        segment = FiberSegment.objects.create(
            fabric=self.fabric,
            name='Jumper-1',
            a_endpoint=self.mpo,
            b_endpoint=peer_mpo,
        )
        strand = FiberStrand.objects.create(segment=segment, strand_index=1)

        StrandTermination.objects.create(
            strand=strand,
            mpo_endpoint=self.mpo,
            mpo_position=self.position_1,
            termination_index=1,
        ).full_clean()
        StrandTermination.objects.create(
            strand=strand,
            mpo_endpoint=peer_mpo,
            mpo_position=peer_position,
            termination_index=2,
        ).full_clean()

        self.assertEqual(strand.terminations.count(), 2)

    def test_distinct_wavelength_lanes_can_share_one_local_position(self):
        first = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=self.osfp,
            channel=self.channel,
            plane=self.plane,
            local_mpo_endpoint=self.mpo,
            local_mpo_position=self.position_1,
            lane_index=1,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=Decimal('1311.000'),
        )
        second = OpticalLane.objects.create(
            fabric=self.fabric,
            endpoint=self.osfp,
            channel=self.channel,
            plane=self.plane,
            local_mpo_endpoint=self.mpo,
            local_mpo_position=self.position_1,
            lane_index=2,
            local_mpo_index=1,
            direction='send',
            wavelength_nm=Decimal('1313.000'),
        )

        self.assertEqual(first.local_mpo_position, second.local_mpo_position)
        self.assertNotEqual(first.wavelength_nm, second.wavelength_nm)
