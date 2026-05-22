from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from dcim.models import DeviceRole, DeviceType, Interface, Manufacturer, Site

from netbox_plant_graph.models import CableAssembly
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.impact_modeling import (
    SCENARIO_CABLE_ASSEMBLY_CUT,
    SCENARIO_MPO_CONNECTOR_UNPLUG,
    SCENARIO_OSFP_TRANSCEIVER_UNSEAT,
)
from netbox_plant_graph.services.stamping import (
    execute_stamp_template,
    stamp_roce_4plane_mini_fabric,
)


REPORT_TOP_LEVEL_KEYS = {
    'scenario',
    'scope',
    'summary',
    'simulated_components',
    'impacted_paths',
    'impacted_lanes',
    'impacted_channels',
    'impacted_endpoints',
    'impacted_devices',
    'hierarchy',
}


class V2OperationalImpactAPITestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='impact-api-admin',
            password='admin',
            email='impact-api-admin@example.local',
        )

    def _post_json(self, route_name, payload):
        self.client.force_login(self.user)
        return self.client.post(
            reverse(f'plugins-api:netbox_plant_graph-api:{route_name}'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _cable_for_lane(self, lane):
        termination = lane.local_mpo_position.strand_terminations.select_related('strand').first()
        self.assertIsNotNone(termination)
        return CableAssembly.objects.get(
            site_id=termination.strand.cable_site_id,
            cable_id=termination.strand.cable_id,
        )

    def _stamp_anchored_fabric(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA Impact API', slug='nvidia-impact-api')
        gpu_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Impact API Tray',
            slug='gb300-impact-api-tray',
        )
        leaf_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='Impact API Leaf Switch',
            slug='impact-api-leaf-switch',
        )
        gpu_role = DeviceRole.objects.create(
            name='Impact API GPU Tray',
            slug='impact-api-gpu-tray',
            color='ff0000',
        )
        leaf_role = DeviceRole.objects.create(
            name='Impact API Leaf',
            slug='impact-api-leaf',
            color='00ff00',
        )
        site = Site.objects.create(name='Impact API Site', slug='impact-api-site', status='active')
        return execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Impact API Fabric',
            fabric_slug='impact-api-fabric',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': gpu_device_type,
                'gpu_role': gpu_role,
                'leaf_device_type': leaf_device_type,
                'leaf_role': leaf_role,
                'name_prefix': 'impact-api',
            },
        )

    def _assert_report_payload(self, payload, scenario_type):
        self.assertEqual(set(payload), REPORT_TOP_LEVEL_KEYS)
        self.assertEqual(payload['scenario']['scenario_type'], scenario_type)
        self.assertIn('impacted_path_count', payload['summary'])
        self.assertIn('objects_by_severity', payload['summary'])

    def test_cable_assembly_cut_api_returns_operational_impact_report(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]
        cable = self._cable_for_lane(source_lane)

        response = self._post_json(
            'impact-cable-assembly-cut',
            {
                'cable_assembly_id': cable.pk,
                'fabric': result.fabric.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self._assert_report_payload(payload, SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertEqual(payload['scenario']['target_objects'][0]['id'], cable.pk)
        self.assertGreater(payload['summary']['impacted_path_count'], 0)

    def test_mpo_connector_unplug_api_returns_operational_impact_report(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]

        response = self._post_json(
            'impact-mpo-connector-unplug',
            {
                'connector_endpoint_id': source_lane.local_mpo_endpoint_id,
                'fabric': result.fabric.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self._assert_report_payload(payload, SCENARIO_MPO_CONNECTOR_UNPLUG)
        self.assertEqual(payload['scenario']['target_objects'][0]['id'], source_lane.local_mpo_endpoint_id)
        self.assertGreater(payload['summary']['impacted_channel_count'], 0)

    def test_osfp_transceiver_unseat_api_returns_operational_impact_report(self):
        result = self._stamp_anchored_fabric()
        source_lane = result.source_lanes[0]
        source_interface = source_lane.endpoint.source
        self.assertIsInstance(source_interface, Interface)

        response = self._post_json(
            'impact-osfp-transceiver-unseat',
            {
                'interface_id': source_interface.pk,
                'fabric': result.fabric.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self._assert_report_payload(payload, SCENARIO_OSFP_TRANSCEIVER_UNSEAT)
        self.assertEqual(payload['scenario']['target_objects'][0]['id'], source_interface.pk)
        self.assertGreater(payload['summary']['impacted_device_count'], 0)

    def test_impact_api_validates_missing_and_invalid_targets(self):
        missing = self._post_json('impact-cable-assembly-cut', {})
        self.assertEqual(missing.status_code, 400)
        self.assertIn('cable_assembly_ids', missing.json())

        invalid = self._post_json(
            'impact-cable-assembly-cut',
            {
                'cable_assembly_id': 999999,
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('cable_assembly_ids', invalid.json())
