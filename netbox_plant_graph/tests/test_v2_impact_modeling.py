from __future__ import annotations

from django.test import TestCase
from dcim.models import DeviceRole, DeviceType, Interface, Manufacturer, Site

from netbox_plant_graph.models import CableAssembly, OperationRun
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.impact_modeling import (
    IMPACT_DEGRADED,
    IMPACT_FAILED,
    IMPACT_LOW,
    OPERATIONAL_IMPACT_OPERATION_KIND,
    OPERATIONAL_IMPACT_OPERATION_PROFILE,
    OPERATIONAL_IMPACT_REPORT_SCHEMA,
    SCENARIO_CABLE_ASSEMBLY_CUT,
    SCENARIO_MPO_CONNECTOR_UNPLUG,
    SCENARIO_OSFP_TRANSCEIVER_UNSEAT,
    compare_operational_impact_reports,
    model_cable_assembly_cut_impact,
    model_mpo_connector_unplug_impact,
    model_operational_impact,
    model_osfp_transceiver_unseat_impact,
    persist_operational_impact_report,
)
from netbox_plant_graph.services.stamping import execute_stamp_template, stamp_roce_4plane_mini_fabric


class V2OperationalImpactModelingTestCase(TestCase):
    def _cable_for_lane(self, lane):
        termination = lane.local_mpo_position.strand_terminations.select_related('strand').first()
        self.assertIsNotNone(termination)
        return CableAssembly.objects.get(
            site_id=termination.strand.cable_site_id,
            cable_id=termination.strand.cable_id,
        )

    def _flatten_hierarchy(self, nodes):
        flattened = []
        for node in nodes:
            flattened.append(node)
            flattened.extend(self._flatten_hierarchy(node.children))
        return flattened

    def _stamp_anchored_fabric(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA Impact', slug='nvidia-impact')
        gpu_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Impact Tray',
            slug='gb300-impact-tray',
        )
        leaf_device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='Impact Leaf Switch',
            slug='impact-leaf-switch',
        )
        gpu_role = DeviceRole.objects.create(name='Impact GPU Tray', slug='impact-gpu-tray', color='ff0000')
        leaf_role = DeviceRole.objects.create(name='Impact Leaf', slug='impact-leaf', color='00ff00')
        site = Site.objects.create(name='Impact Site', slug='impact-site', status='active')
        return execute_stamp_template(
            template=fixture.stamp_template,
            fabric_name='Impact Fabric',
            fabric_slug='impact-fabric',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': gpu_device_type,
                'gpu_role': gpu_role,
                'leaf_device_type': leaf_device_type,
                'leaf_role': leaf_role,
                'name_prefix': 'impact',
            },
        )

    def test_cable_assembly_cut_report_includes_paths_and_severity_tiers(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]
        cable = self._cable_for_lane(source_lane)

        report = model_cable_assembly_cut_impact(cable_assembly=cable)

        self.assertEqual(report.scenario.scenario_type, SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertEqual(report.scenario.target_objects[0].object_id, cable.pk)
        self.assertGreater(report.summary['impacted_path_count'], 0)
        self.assertGreater(report.summary['objects_by_severity'][IMPACT_FAILED], 0)
        self.assertGreater(report.summary['objects_by_severity'][IMPACT_DEGRADED], 0)
        self.assertTrue(any(node.severity == IMPACT_LOW for node in self._flatten_hierarchy(report.hierarchy)))

        source_impact = next(entry for entry in report.impacted_lanes if entry.object.object_id == source_lane.pk)
        self.assertEqual(source_impact.severity, IMPACT_FAILED)
        self.assertIn(source_lane.channel_id, source_impact.channel_ids)
        self.assertTrue(source_impact.path_ids)

        first_path = report.impacted_paths[0]
        self.assertEqual(first_path.source_lane_id, source_lane.pk)
        self.assertEqual(first_path.source_channel_id, source_lane.channel_id)
        self.assertTrue(
            any(component.model == 'netbox_plant_graph.fiberstrand' for component in first_path.matched_components)
        )

        payload = report.as_dict()
        self.assertEqual(payload['scenario']['scenario_type'], SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertIn('impacted_channels', payload)
        self.assertIn('source_channel_id', payload['impacted_paths'][0])

    def test_mpo_connector_unplug_groups_failed_channels_lanes_and_endpoints(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]

        report = model_mpo_connector_unplug_impact(connector_endpoint=source_lane.local_mpo_endpoint)

        self.assertEqual(report.scenario.scenario_type, SCENARIO_MPO_CONNECTOR_UNPLUG)
        failed_endpoint = next(
            entry for entry in report.impacted_endpoints if source_lane.local_mpo_endpoint_id in entry.endpoint_ids
        )
        self.assertEqual(failed_endpoint.severity, IMPACT_FAILED)

        failed_channel = next(
            entry for entry in report.impacted_channels if source_lane.channel_id in entry.channel_ids
        )
        self.assertEqual(failed_channel.severity, IMPACT_FAILED)
        self.assertIn(source_lane.pk, failed_channel.lane_ids)

        failed_lane = next(entry for entry in report.impacted_lanes if entry.object.object_id == source_lane.pk)
        self.assertEqual(failed_lane.severity, IMPACT_FAILED)
        self.assertIn(source_lane.local_mpo_endpoint_id, failed_lane.endpoint_ids)

    def test_osfp_transceiver_unseat_uses_interface_descendants_and_device_hierarchy(self):
        result = self._stamp_anchored_fabric()
        source_lane = result.source_lanes[0]
        source_interface = source_lane.endpoint.source
        self.assertIsInstance(source_interface, Interface)

        report = model_osfp_transceiver_unseat_impact(
            interface=source_interface,
            selected_fabric=result.fabric,
        )

        self.assertEqual(report.scenario.scenario_type, SCENARIO_OSFP_TRANSCEIVER_UNSEAT)
        self.assertEqual(report.scenario.target_objects[0].model, 'dcim.interface')
        self.assertEqual(report.scope['fabric_slugs'], [result.fabric.slug])

        failed_lane = next(entry for entry in report.impacted_lanes if entry.object.object_id == source_lane.pk)
        self.assertEqual(failed_lane.severity, IMPACT_FAILED)
        self.assertIn(source_lane.channel_id, failed_lane.channel_ids)

        failed_device = next(
            entry for entry in report.impacted_devices if entry.object.object_id == source_interface.device_id
        )
        self.assertEqual(failed_device.severity, IMPACT_FAILED)
        self.assertIn(source_lane.endpoint_id, failed_device.endpoint_ids)

        hierarchy_nodes = self._flatten_hierarchy(report.hierarchy)
        self.assertTrue(
            any(
                node.kind == 'device' and node.object.object_id == source_interface.device_id
                for node in hierarchy_nodes
            )
        )
        self.assertTrue(report.impacted_paths)
        self.assertEqual(report.impacted_paths[0].source_channel_id, source_lane.channel_id)

    def test_generic_dispatcher_returns_same_report_shape(self):
        result = stamp_roce_4plane_mini_fabric()
        cable = self._cable_for_lane(result.source_lanes[0])

        report = model_operational_impact(
            scenario_type=SCENARIO_CABLE_ASSEMBLY_CUT,
            cable_assembly=cable,
        )

        self.assertEqual(report.scenario.scenario_type, SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertTrue(report.impacted_paths)

    def test_persist_operational_impact_report_creates_operation_run_snapshot(self):
        result = stamp_roce_4plane_mini_fabric()
        cable = self._cable_for_lane(result.source_lanes[0])
        report = model_cable_assembly_cut_impact(cable_assembly=cable)

        run = persist_operational_impact_report(
            report,
            report_name='Maintenance cut preview',
            parameters={'trigger': 'test'},
        )

        self.assertEqual(run.profile, OPERATIONAL_IMPACT_OPERATION_PROFILE)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.fabric, result.fabric)
        self.assertEqual(run.metadata['operation_kind'], OPERATIONAL_IMPACT_OPERATION_KIND)
        self.assertEqual(run.metadata['report_schema'], OPERATIONAL_IMPACT_REPORT_SCHEMA)
        self.assertEqual(run.metadata['scenario_type'], SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertEqual(run.result['report_name'], 'Maintenance cut preview')
        self.assertEqual(run.result['scenario_type'], SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertEqual(run.result['summary'], report.summary)
        self.assertEqual(run.result['report']['scenario']['target_objects'][0]['id'], cable.pk)
        self.assertEqual(run.parameters['trigger'], 'test')
        self.assertTrue(run.result['report_hash'])
        self.assertEqual(OperationRun.objects.get(pk=run.pk).result['report_schema'], OPERATIONAL_IMPACT_REPORT_SCHEMA)

    def test_compare_operational_impact_reports_returns_common_specific_and_deltas(self):
        result = stamp_roce_4plane_mini_fabric()
        source_lane = result.source_lanes[0]
        cable = self._cable_for_lane(source_lane)
        cable_report = model_cable_assembly_cut_impact(cable_assembly=cable)
        connector_report = model_mpo_connector_unplug_impact(
            connector_endpoint=source_lane.local_mpo_endpoint,
        )

        comparison = compare_operational_impact_reports(cable_report, connector_report.as_dict())
        payload = comparison.as_dict()

        self.assertEqual(payload['report_count'], 2)
        self.assertEqual(payload['reports'][0]['scenario_type'], SCENARIO_CABLE_ASSEMBLY_CUT)
        self.assertEqual(payload['reports'][1]['scenario_type'], SCENARIO_MPO_CONNECTOR_UNPLUG)

        common_keys = {impact['key'] for impact in payload['common_impacts']}
        self.assertIn(
            f'impacted_lanes:netbox_plant_graph.opticallane:{source_lane.pk}',
            common_keys,
        )
        common_lane = next(
            impact
            for impact in payload['common_impacts']
            if impact['key'] == f'impacted_lanes:netbox_plant_graph.opticallane:{source_lane.pk}'
        )
        self.assertEqual(common_lane['severities'][0]['severity'], IMPACT_FAILED)
        self.assertEqual(common_lane['severities'][1]['severity'], IMPACT_FAILED)

        specifics_by_report = {
            group['report_index']: group['impacts']
            for group in payload['scenario_specific_impacts']
        }
        self.assertTrue(
            any(impact['kind'] == 'fiber_strand' for impact in specifics_by_report[0])
        )
        self.assertTrue(
            any(impact['kind'] == 'connector_position' for impact in specifics_by_report[1])
        )

        self.assertEqual(payload['severity_deltas'][0]['base_report_index'], 0)
        self.assertEqual(payload['severity_deltas'][0]['compare_report_index'], 1)
        self.assertIn('objects_by_severity', payload['severity_deltas'][0]['severity_count_delta'])
        self.assertEqual(payload['device_deltas'][0]['base_report_index'], 0)
        self.assertEqual(payload['device_deltas'][0]['compare_report_index'], 1)
        self.assertIn('added_device_count', payload['device_deltas'][0])
