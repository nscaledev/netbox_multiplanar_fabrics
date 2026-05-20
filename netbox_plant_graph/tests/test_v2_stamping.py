from django.core.management import call_command
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site

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
    TransportChannel,
)
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.resolver import resolve_optical_lane_path
from netbox_plant_graph.services.stamping import HYBRID_STAMP_EXECUTORS, execute_stamp_template, stamp_roce_4plane_mini_fabric


class V2MiniFabricStampTestCase(TestCase):
    def test_hybrid_stamp_executor_registry_exposes_roce_mini_proof(self):
        self.assertIn('roce_4plane_mini_proof', HYBRID_STAMP_EXECUTORS)

    def test_execute_stamp_template_dispatches_hybrid_executor(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Template-dispatched proof',
            fabric_slug='template-dispatched-proof',
        )

        self.assertEqual(result.fabric.slug, 'template-dispatched-proof')
        self.assertEqual(result.fabric.metadata['stamp_executor'], 'roce_4plane_mini_proof')
        self.assertEqual(result.stamp_run.parameters['executor'], 'roce_4plane_mini_proof')
        self.assertEqual(len(result.resolved_paths), 4)

    def test_execute_stamp_template_anchors_nodes_and_ports_to_netbox_objects(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        device = Device.objects.create(
            name='GPU-REAL-1',
            device_type=device_type,
            role=role,
            site=site,
        )
        osfp = Interface.objects.create(
            device=device,
            name='OSFP-1',
            type='800gbase-x-osfp',
        )

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Anchored proof',
            fabric_slug='anchored-proof',
            source_bindings={
                'nodes': {
                    'GB300-TRAY-1': device,
                },
                'endpoints': {
                    'GB300-TRAY-1.OSFP-1': osfp,
                },
            },
        )

        node = FabricNode.objects.get(fabric=result.fabric, address='GB300-TRAY-1')
        endpoint = Endpoint.objects.get(fabric=result.fabric, address='GB300-TRAY-1.OSFP-1')
        device_content_type = ContentType.objects.get_for_model(Device)
        interface_content_type = ContentType.objects.get_for_model(Interface)
        self.assertEqual(node.source_type, device_content_type)
        self.assertEqual(node.source_id, device.pk)
        self.assertEqual(node.source, device)
        self.assertEqual(endpoint.source_type, interface_content_type)
        self.assertEqual(endpoint.source_id, osfp.pk)
        self.assertEqual(endpoint.source, osfp)
        self.assertEqual(result.stamp_run.parameters['source_binding_counts'], {'nodes': 1, 'endpoints': 1})

    def test_execute_stamp_template_rejects_unknown_hybrid_executor(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        fixture.stamp_template.template = {
            **fixture.stamp_template.template,
            'executor': {
                'mode': 'hybrid',
                'primitive': 'missing_executor',
            },
        }

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Bad proof',
                fabric_slug='bad-proof',
            )

        self.assertIn('Unknown hybrid stamp executor', str(raised.exception))

    def test_execute_stamp_template_rejects_template_missing_required_keys(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        invalid_template = dict(fixture.stamp_template.template)
        invalid_template.pop('source_bindings')
        fixture.stamp_template.template = invalid_template

        before_fabric_count = Fabric.objects.count()
        before_stamp_run_count = StampRun.objects.count()

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid proof',
                fabric_slug='invalid-proof',
            )

        self.assertIn('StampTemplate.template.source_bindings is required.', str(raised.exception))
        self.assertEqual(Fabric.objects.count(), before_fabric_count)
        self.assertEqual(StampRun.objects.count(), before_stamp_run_count)

    def test_execute_stamp_template_rejects_proof_path_outside_plane_set(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        proof_paths = [dict(item) for item in template['proof_paths']]
        proof_paths[0]['plane'] = 99
        template['proof_paths'] = proof_paths
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid plane proof',
                fabric_slug='invalid-plane-proof',
            )

        self.assertIn('must reference a plane in StampTemplate.template.planes', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-plane-proof').exists())
        self.assertEqual(StampRun.objects.count(), 0)

    def test_execute_stamp_template_rejects_unsupported_source_binding_model(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        source_bindings = [dict(item) for item in template['source_bindings']]
        source_bindings[0]['model'] = 'dcim.nonexistent'
        template['source_bindings'] = source_bindings
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid source binding proof',
                fabric_slug='invalid-source-binding-proof',
            )

        self.assertIn('must be one of: dcim.device, dcim.interface', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-source-binding-proof').exists())
        self.assertEqual(StampRun.objects.count(), 0)

    def test_execute_stamp_template_rejects_incomplete_leaf_plane_assignment(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        leaf_ports = dict(template['leaf_ports'])
        leaf_ports['plane_assignment'] = {'1': 1, '2': 2, '3': 3}
        template['leaf_ports'] = leaf_ports
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid assignment proof',
                fabric_slug='invalid-assignment-proof',
            )

        self.assertIn('plane_assignment must define exactly one entry for each leaf index', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-assignment-proof').exists())
        self.assertEqual(StampRun.objects.count(), 0)

    def test_execute_stamp_template_rejects_missing_device_binding_reference(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        source_bindings = [dict(item) for item in template['source_bindings']]
        source_bindings[1]['device_binding_address'] = 'MISSING-DEVICE-BINDING'
        template['source_bindings'] = source_bindings
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid binding reference proof',
                fabric_slug='invalid-binding-reference-proof',
            )

        self.assertIn('device_binding_address must reference an existing source binding address', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-binding-reference-proof').exists())
        self.assertEqual(StampRun.objects.count(), 0)

    def test_execute_stamp_template_rejects_interface_binding_without_interface_model(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        source_bindings = [dict(item) for item in template['source_bindings']]
        source_bindings[1]['model'] = 'dcim.device'
        template['source_bindings'] = source_bindings
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid interface model proof',
                fabric_slug='invalid-interface-model-proof',
            )

        self.assertIn('model must be "dcim.interface" when device_binding_address is set', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-interface-model-proof').exists())
        self.assertEqual(StampRun.objects.count(), 0)

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

    def test_mini_stamp_records_managed_v2_object_inventory(self):
        result = stamp_roce_4plane_mini_fabric()
        fabric = result.fabric
        managed_objects = result.stamp_run.result['managed_objects']
        object_counts = result.stamp_run.result['object_counts']

        self.assertEqual(managed_objects['fabrics'], [fabric.pk])
        self.assertEqual(object_counts['planes'], Plane.objects.filter(fabric=fabric).count())
        self.assertEqual(object_counts['nodes'], FabricNode.objects.filter(fabric=fabric).count())
        self.assertEqual(object_counts['endpoints'], Endpoint.objects.filter(fabric=fabric).count())
        self.assertEqual(
            object_counts['connector_positions'],
            ConnectorPosition.objects.filter(endpoint__fabric=fabric).count(),
        )
        self.assertEqual(object_counts['transport_channels'], TransportChannel.objects.filter(fabric=fabric).count())
        self.assertEqual(object_counts['fiber_segments'], FiberSegment.objects.filter(fabric=fabric).count())
        self.assertEqual(object_counts['fiber_strands'], FiberStrand.objects.filter(segment__fabric=fabric).count())
        self.assertEqual(
            object_counts['strand_terminations'],
            StrandTermination.objects.filter(strand__segment__fabric=fabric).count(),
        )
        self.assertEqual(object_counts['transfer_maps'], TransferMap.objects.filter(fabric=fabric).count())
        self.assertEqual(object_counts['optical_lanes'], OpticalLane.objects.filter(fabric=fabric).count())
        self.assertEqual(
            set(managed_objects['optical_lanes']),
            {lane.pk for lane in result.source_lanes + result.destination_lanes},
        )

    def test_seed_command_can_seed_architecture_or_full_mini_fabric(self):
        call_command('mpf_seed_v2', '--architecture-only')
        self.assertFalse(Fabric.objects.exists())

        call_command('mpf_seed_v2')
        self.assertTrue(Fabric.objects.filter(slug='roce-4-plane-mini-proof').exists())
        self.assertEqual(StampRun.objects.count(), 1)
