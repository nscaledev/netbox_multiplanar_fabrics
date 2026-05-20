from django.core.management import call_command
from django.test import TestCase

from netbox_plant_graph.models import (
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricNode,
    FiberSegment,
    FiberStrand,
    OpticalLane,
    Plane,
    StampRun,
    StrandTermination,
    TransferMap,
)
from netbox_plant_graph.services.resolver import resolve_optical_lane_path
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric


class V2MiniFabricStampTestCase(TestCase):
    def test_mini_stamp_creates_resolvable_four_plane_shuffle(self):
        result = stamp_roce_4plane_mini_fabric()
        fabric = result.fabric

        self.assertEqual(fabric.slug, 'roce-4-plane-mini-proof')
        self.assertEqual(Plane.objects.filter(fabric=fabric).count(), 4)
        self.assertEqual(FabricNode.objects.filter(fabric=fabric, node_kind='active_device').count(), 5)
        self.assertEqual(FabricNode.objects.filter(fabric=fabric, node_kind='passive_assembly').count(), 2)
        self.assertEqual(Endpoint.objects.filter(fabric=fabric, connector_kind='osfp').count(), 8)
        self.assertEqual(Endpoint.objects.filter(fabric=fabric, connector_kind='mpo-12').count(), 32)
        self.assertEqual(ConnectorPosition.objects.filter(endpoint__fabric=fabric).count(), 384)
        self.assertEqual(FiberSegment.objects.filter(fabric=fabric).count(), 8)
        self.assertEqual(FiberStrand.objects.filter(segment__fabric=fabric).count(), 8)
        self.assertEqual(StrandTermination.objects.filter(strand__segment__fabric=fabric).count(), 16)
        self.assertEqual(TransferMap.objects.filter(fabric=fabric, map_kind='shuffle_2x2').count(), 4)
        self.assertEqual(OpticalLane.objects.filter(fabric=fabric, direction='send').count(), 4)
        self.assertEqual(OpticalLane.objects.filter(fabric=fabric, direction='receive').count(), 4)

        self.assertEqual(len(result.resolved_paths), 4)
        for path in result.resolved_paths:
            with self.subTest(path=path):
                self.assertTrue(path.path_found, path.error)
                self.assertIn('transfer_map', [step.step_type for step in path.steps])

        for source, destination in zip(result.source_lanes, result.destination_lanes, strict=True):
            with self.subTest(source=source.pk, destination=destination.pk):
                path = resolve_optical_lane_path(source=source, destination=destination)
                self.assertTrue(path.path_found, path.error)
                self.assertEqual(path.destination_lane_id, destination.pk)

    def test_mini_stamp_is_idempotent_except_for_audit_runs(self):
        first = stamp_roce_4plane_mini_fabric()
        fabric = first.fabric

        counts = {
            'fabrics': Fabric.objects.count(),
            'planes': Plane.objects.filter(fabric=fabric).count(),
            'nodes': FabricNode.objects.filter(fabric=fabric).count(),
            'endpoints': Endpoint.objects.filter(fabric=fabric).count(),
            'positions': ConnectorPosition.objects.filter(endpoint__fabric=fabric).count(),
            'segments': FiberSegment.objects.filter(fabric=fabric).count(),
            'strands': FiberStrand.objects.filter(segment__fabric=fabric).count(),
            'terminations': StrandTermination.objects.filter(strand__segment__fabric=fabric).count(),
            'transfer_maps': TransferMap.objects.filter(fabric=fabric).count(),
            'lanes': OpticalLane.objects.filter(fabric=fabric).count(),
        }
        stamp_run_count = StampRun.objects.filter(fabric=fabric).count()

        second = stamp_roce_4plane_mini_fabric()

        self.assertEqual(second.fabric.pk, fabric.pk)
        self.assertEqual(Fabric.objects.count(), counts['fabrics'])
        self.assertEqual(Plane.objects.filter(fabric=fabric).count(), counts['planes'])
        self.assertEqual(FabricNode.objects.filter(fabric=fabric).count(), counts['nodes'])
        self.assertEqual(Endpoint.objects.filter(fabric=fabric).count(), counts['endpoints'])
        self.assertEqual(ConnectorPosition.objects.filter(endpoint__fabric=fabric).count(), counts['positions'])
        self.assertEqual(FiberSegment.objects.filter(fabric=fabric).count(), counts['segments'])
        self.assertEqual(FiberStrand.objects.filter(segment__fabric=fabric).count(), counts['strands'])
        self.assertEqual(StrandTermination.objects.filter(strand__segment__fabric=fabric).count(), counts['terminations'])
        self.assertEqual(TransferMap.objects.filter(fabric=fabric).count(), counts['transfer_maps'])
        self.assertEqual(OpticalLane.objects.filter(fabric=fabric).count(), counts['lanes'])
        self.assertEqual(StampRun.objects.filter(fabric=fabric).count(), stamp_run_count + 1)

    def test_seed_command_can_seed_architecture_or_full_mini_fabric(self):
        call_command('mpf_seed_v2', '--architecture-only')
        self.assertFalse(Fabric.objects.exists())

        call_command('mpf_seed_v2')
        self.assertTrue(Fabric.objects.filter(slug='roce-4-plane-mini-proof').exists())
        self.assertEqual(StampRun.objects.count(), 1)
