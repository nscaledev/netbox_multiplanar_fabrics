from django.core.exceptions import ValidationError
from django.test import TestCase
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleBay, ModuleType, Site

from netbox_plant_graph.models import (
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricNode,
    TransceiverConnector,
    TransceiverConnectorProfile,
    TransceiverLaneProfile,
    TransceiverProfile,
)
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.transceivers import ensure_builtin_transceiver_profiles


class V2TransceiverModelingTestCase(TestCase):
    def setUp(self):
        self.architecture = ensure_roce_4plane_shuffle_architecture().architecture
        self.manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-transceiver-test')
        self.module_type = ModuleType.objects.create(
            manufacturer=self.manufacturer,
            model='MMS4X00-NM-T Test',
            part_number='MMS4X00-NM-T',
        )

    def test_builtin_profiles_create_dual_mpo_4x200_lane_map_and_module_mapping(self):
        counters = ensure_builtin_transceiver_profiles(architecture=self.architecture)

        self.assertGreater(counters['transceiver_profiles_created'], 0)
        profile = TransceiverProfile.objects.get(slug='osfp-dual-mpo12-apc-800g-4x200g-dr4')
        self.assertEqual(profile.aggregate_rate_gbps, 800)
        self.assertEqual(profile.channel_count, 4)
        self.assertEqual(profile.channel_rate_gbps, 200)
        self.assertTrue(profile.module_type_mappings.filter(module_type=self.module_type).exists())

        connectors = tuple(profile.connector_profiles.order_by('connector_index'))
        self.assertEqual([connector.name for connector in connectors], ['MPO-1', 'MPO-2'])
        self.assertEqual(connectors[0].polish, 'apc')
        self.assertEqual(connectors[0].position_count, 12)
        self.assertEqual(
            list(
                TransceiverLaneProfile.objects.filter(
                    connector_profile=connectors[0],
                    direction='send',
                ).order_by('lane_index').values_list('channel_index', 'lane_index', 'mpo_position')
            ),
            [
                (1, 1, 1),
                (1, 2, 12),
                (1, 3, 2),
                (1, 4, 11),
                (2, 5, 3),
                (2, 6, 10),
                (2, 7, 4),
                (2, 8, 9),
            ],
        )

    def test_installed_transceiver_connector_snapshots_profile_semantics(self):
        ensure_builtin_transceiver_profiles(architecture=self.architecture)
        connector_profile = TransceiverConnectorProfile.objects.get(
            profile__slug='osfp-dual-mpo12-apc-800g-4x200g-dr4',
            name='MPO-1',
        )
        site = Site.objects.create(name='Transceiver Site', slug='transceiver-site', status='active')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray-transceiver', color='ff0000')
        device_type = DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model='GB300 Tray Test',
            slug='gb300-tray-transceiver',
        )
        device = Device.objects.create(name='gb300-1', device_type=device_type, role=role, site=site)
        module_bay = ModuleBay.objects.create(device=device, name='osfp1', position='1')
        module = Module.objects.create(device=device, module_bay=module_bay, module_type=self.module_type)
        fabric = Fabric.objects.create(
            architecture=self.architecture,
            name='Transceiver Fabric',
            slug='transceiver-fabric',
            scope_site=site,
        )
        node = FabricNode.objects.create(fabric=fabric, name='gb300-1', address='gb300-1')
        endpoint = Endpoint.objects.create(
            fabric=fabric,
            node=node,
            name='osfp1.MPO-1',
            address='gb300-1.osfp1.MPO-1',
            endpoint_kind='subconnector',
            connector_kind='mpo-12',
            position_count=12,
        )
        ConnectorPosition.objects.create(endpoint=endpoint, position_number=1)

        connector = TransceiverConnector.objects.create(
            module=module,
            connector_profile=connector_profile,
            endpoint=endpoint,
        )

        self.assertEqual(connector.connector_family, 'mpo-12')
        self.assertEqual(connector.position_count, 12)
        self.assertEqual(connector.polish, 'apc')

    def test_transceiver_connector_rejects_endpoint_geometry_mismatch(self):
        profile = TransceiverProfile.objects.create(
            architecture=self.architecture,
            name='Mismatch Profile',
            slug='mismatch-profile',
            status='active',
        )
        connector_profile = TransceiverConnectorProfile.objects.create(
            profile=profile,
            name='MPO-1',
            connector_index=1,
            connector_family='mpo-12',
            position_count=12,
        )
        site = Site.objects.create(name='Mismatch Site', slug='mismatch-site', status='active')
        role = DeviceRole.objects.create(name='Mismatch Role', slug='mismatch-role', color='00ff00')
        device_type = DeviceType.objects.create(
            manufacturer=self.manufacturer,
            model='Mismatch Device Type',
            slug='mismatch-device-type',
        )
        device = Device.objects.create(name='mismatch-device', device_type=device_type, role=role, site=site)
        module_bay = ModuleBay.objects.create(device=device, name='osfp1', position='1')
        module = Module.objects.create(device=device, module_bay=module_bay, module_type=self.module_type)
        fabric = Fabric.objects.create(
            architecture=self.architecture,
            name='Mismatch Fabric',
            slug='mismatch-fabric',
            scope_site=site,
        )
        node = FabricNode.objects.create(fabric=fabric, name='mismatch-device', address='mismatch-device')
        endpoint = Endpoint.objects.create(
            fabric=fabric,
            node=node,
            name='bad-connector',
            address='mismatch-device.bad-connector',
            endpoint_kind='subconnector',
            connector_kind='mpo-8',
            position_count=8,
        )

        connector = TransceiverConnector(
            module=module,
            connector_profile=connector_profile,
            endpoint=endpoint,
        )
        connector.save()

        with self.assertRaises(ValidationError):
            connector.full_clean()
