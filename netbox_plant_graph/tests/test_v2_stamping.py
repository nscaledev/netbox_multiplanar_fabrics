from django.core.management import call_command
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from tenancy.models import Tenant

from netbox_plant_graph.models import (
    CableAssembly,
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
    TransportChannelPositionMap,
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
        subinterface = Interface.objects.get(device=device, name='OSFP-1/1')
        self.assertTrue(Interface.objects.filter(device=device, name='OSFP-1/2').exists())
        self.assertTrue(Interface.objects.filter(device=device, name='OSFP-1/3').exists())
        self.assertTrue(Interface.objects.filter(device=device, name='OSFP-1/4').exists())
        channel = TransportChannel.objects.get(fabric=result.fabric, endpoint=endpoint, channel_index=1)
        self.assertEqual(channel.source_subinterface_id, subinterface.pk)
        self.assertEqual(subinterface.parent_id, osfp.pk)
        self.assertEqual(subinterface.speed, 200000000)
        self.assertTrue(
            TransportChannelPositionMap.objects.filter(
                channel=channel,
                mpo_endpoint__address='GB300-TRAY-1.OSFP-1.MPO-1',
            ).exists()
        )
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

    def test_execute_stamp_template_rejects_invalid_channel_map_matrix(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        channel_subinterfaces = dict(template['channel_subinterfaces'])
        matrix = [dict(item) for item in channel_subinterfaces['channel_map_matrix']]
        matrix[0] = {**matrix[0], 'positions': [1, 13]}
        channel_subinterfaces['channel_map_matrix'] = matrix
        template['channel_subinterfaces'] = channel_subinterfaces
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid channel map proof',
                fabric_slug='invalid-channel-map-proof',
            )

        self.assertIn('exceeds StampTemplate.template.gpu_tray.positions_per_mpo', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-channel-map-proof').exists())
        self.assertEqual(StampRun.objects.count(), 0)

    def test_execute_stamp_template_honors_topology_parameters_and_phase(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        template['planes'] = [1, 2]
        template['topology_parameters'] = {
            'plane_count': {'value': 2, 'min': 1, 'max': 4},
            'gpu_tray_count': 1,
            'leaf_count_per_plane': 1,
        }
        template['stamp_phases'] = [
            {'name': 'phase-a', 'planes': [1]},
            {'name': 'phase-b', 'planes': [2]},
        ]
        template['proof_paths'] = [dict(item) for item in template['proof_paths'] if item['plane'] in {1, 2}]
        fixture.stamp_template.template = template

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Phase proof',
            fabric_slug='phase-proof',
            phase='phase-a',
        )

        self.assertEqual(Plane.objects.filter(fabric=result.fabric).count(), 1)
        self.assertEqual(Plane.objects.get(fabric=result.fabric).plane_number, 1)
        self.assertEqual(len(result.resolved_paths), 1)
        self.assertEqual(result.stamp_run.parameters['phase'], 'phase-a')
        self.assertEqual(result.stamp_run.parameters['active_planes'], [1])
        manifest = result.stamp_run.result['stamp_manifest']
        self.assertEqual(manifest['phase'], {'name': 'phase-a', 'planes': [1]})
        self.assertEqual(manifest['topology_parameters']['plane_count'], 2)
        self.assertEqual(FabricNode.objects.filter(fabric=result.fabric, address__startswith='LEAF-').count(), 1)

    def test_execute_stamp_template_uses_custom_wavelength_plan_and_records_dark_overrides(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        template['wavelength_plan'] = {
            'band': 'c_band',
            'channel_count': 4,
            'channels': ['1550.000', '1551.000', '1552.000', '1553.000'],
        }
        template['dark_position_overrides'] = {
            '1': [5, 6, 7, 8],
            'Plane 2': [5, 6, 7, 8],
        }
        fixture.stamp_template.template = template

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Wavelength proof',
            fabric_slug='wavelength-proof',
        )

        wavelengths = sorted(
            str(value)
            for value in OpticalLane.objects.filter(fabric=result.fabric, direction='send')
            .order_by('plane__plane_number')
            .values_list('wavelength_nm', flat=True)
        )
        self.assertEqual(wavelengths, ['1550.000', '1551.000', '1552.000', '1553.000'])
        self.assertEqual(
            result.stamp_run.result['dark_position_overrides'],
            {'1': [5, 6, 7, 8], '2': [5, 6, 7, 8]},
        )

    def test_execute_stamp_template_injects_fabric_ownership(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        tenant = Tenant.objects.create(name='Research', slug='research')
        site = Site.objects.create(name='Ownership Site', slug='ownership-site', status='active')
        template = dict(fixture.stamp_template.template)
        template['fabric_ownership'] = {
            'tenant_slug': tenant.slug,
            'scope_site_slug': site.slug,
        }
        fixture.stamp_template.template = template

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Owned proof',
            fabric_slug='owned-proof',
        )

        result.fabric.refresh_from_db()
        self.assertEqual(result.fabric.tenant_id, tenant.pk)
        self.assertEqual(result.fabric.scope_site_id, site.pk)
        self.assertEqual(result.stamp_run.result['fabric_ownership']['resolved']['tenant_slug'], 'research')

    def test_execute_stamp_template_rejects_invalid_tier2_parameters(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = dict(fixture.stamp_template.template)
        template['name_patterns'] = {
            'channel_subinterface': '{unsupported_variable}-{channel_index}',
        }
        fixture.stamp_template.template = template

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Invalid pattern proof',
                fabric_slug='invalid-pattern-proof',
            )

        self.assertIn('uses unsupported variable', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='invalid-pattern-proof').exists())

    def test_execute_stamp_template_can_create_and_bind_netbox_active_devices(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        gpu_device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        leaf_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='Leaf Switch',
            slug='leaf-switch',
        )
        gpu_role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        leaf_role = DeviceRole.objects.create(name='Leaf Switch', slug='leaf-switch', color='00ff00')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')

        result = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Created proof',
            fabric_slug='created-proof',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': gpu_device_type,
                'gpu_role': gpu_role,
                'leaf_device_type': leaf_device_type,
                'leaf_role': leaf_role,
                'name_prefix': 'created-proof',
            },
        )

        self.assertEqual(Device.objects.count(), 5)
        self.assertEqual(Interface.objects.count(), 40)
        self.assertEqual(
            sorted(device.name for device in Device.objects.order_by('name')),
            [
                'created-proof-gb300-tray-1',
                'created-proof-leaf-1',
                'created-proof-leaf-2',
                'created-proof-leaf-3',
                'created-proof-leaf-4',
            ],
        )
        gpu_node = FabricNode.objects.get(fabric=result.fabric, address='GB300-TRAY-1')
        leaf_node = FabricNode.objects.get(fabric=result.fabric, address='LEAF-1')
        self.assertEqual(gpu_node.source.name, 'created-proof-gb300-tray-1')
        self.assertEqual(leaf_node.source.name, 'created-proof-leaf-1')
        self.assertEqual(
            Endpoint.objects.get(fabric=result.fabric, address='GB300-TRAY-1.OSFP-1').source.name,
            'OSFP-1',
        )
        self.assertEqual(
            Endpoint.objects.get(fabric=result.fabric, address='LEAF-1.OSFP-1').source.name,
            'OSFP-1',
        )
        self.assertEqual(len(result.stamp_run.result['netbox_created_objects']['devices']), 5)
        self.assertEqual(len(result.stamp_run.result['netbox_created_objects']['interfaces']), 8)

        second = execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Created proof',
            fabric_slug='created-proof',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': gpu_device_type,
                'gpu_role': gpu_role,
                'leaf_device_type': leaf_device_type,
                'leaf_role': leaf_role,
                'name_prefix': 'created-proof',
            },
        )
        self.assertEqual(second.fabric.pk, result.fabric.pk)
        self.assertEqual(second.stamp_run.result['netbox_created_objects'], {'devices': [], 'interfaces': []})

    def test_execute_stamp_template_rejects_invalid_creation_options(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        with self.assertRaises(ValueError) as raised:
            execute_stamp_template(
                template=fixture.stamp_template,
                fabric_name='Bad create proof',
                fabric_slug='bad-create-proof',
                creation_options={'enabled': True},
            )

        self.assertIn('creation_options.site must be a Site', str(raised.exception))
        self.assertFalse(Fabric.objects.filter(slug='bad-create-proof').exists())
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
        self.assertEqual(CableAssembly.objects.count(), 8)
        self.assertEqual(FiberStrand.objects.filter(segment__fabric=fabric).count(), 8)
        self.assertEqual(StrandTermination.objects.filter(strand__segment__fabric=fabric).count(), 16)
        self.assertEqual(TransferMap.objects.filter(fabric=fabric, map_kind='shuffle_2x2').count(), 4)
        self.assertEqual(TransportChannelPositionMap.objects.filter(channel__fabric=fabric).count(), 32)
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
            'cable_assemblies': CableAssembly.objects.count(),
            'strands': FiberStrand.objects.filter(segment__fabric=fabric).count(),
            'terminations': StrandTermination.objects.filter(strand__segment__fabric=fabric).count(),
            'transfer_maps': TransferMap.objects.filter(fabric=fabric).count(),
            'channel_position_maps': TransportChannelPositionMap.objects.filter(channel__fabric=fabric).count(),
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
        self.assertEqual(CableAssembly.objects.count(), counts['cable_assemblies'])
        self.assertEqual(FiberStrand.objects.filter(segment__fabric=fabric).count(), counts['strands'])
        self.assertEqual(StrandTermination.objects.filter(strand__segment__fabric=fabric).count(), counts['terminations'])
        self.assertEqual(TransferMap.objects.filter(fabric=fabric).count(), counts['transfer_maps'])
        self.assertEqual(
            TransportChannelPositionMap.objects.filter(channel__fabric=fabric).count(),
            counts['channel_position_maps'],
        )
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
        self.assertEqual(
            object_counts['transport_channel_position_maps'],
            TransportChannelPositionMap.objects.filter(channel__fabric=fabric).count(),
        )
        self.assertEqual(object_counts['fiber_segments'], FiberSegment.objects.filter(fabric=fabric).count())
        self.assertEqual(object_counts['cable_assemblies'], CableAssembly.objects.count())
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
