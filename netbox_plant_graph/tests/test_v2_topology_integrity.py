from __future__ import annotations

import json
import inspect
import re
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from dcim.models import Site

from netbox_plant_graph.models import (
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberStrand,
    OperationRun,
    OpticalLane,
    StrandTermination,
    TransferMap,
    TransportChannel,
    TransportChannelPositionMap,
)
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric
from netbox_plant_graph.services import topology_integrity
from netbox_plant_graph.services.topology_integrity import (
    TOPOLOGY_INTEGRITY_OPERATION_KIND,
    TOPOLOGY_INTEGRITY_OPERATION_PROFILE,
    audit_topology_integrity,
    integrity_gate_for_fabric,
    persist_topology_integrity_report,
    topology_integrity_finding_catalog,
)


class V2TopologyIntegrityAuditTestCase(TestCase):
    def _codes(self, report):
        return {finding.code for finding in report.findings}

    def _stamp_clean_fabric(self):
        return stamp_roce_4plane_mini_fabric()

    def test_clean_stamped_fabric_has_no_integrity_findings(self):
        result = self._stamp_clean_fabric()

        report = audit_topology_integrity(fabric=result.fabric)

        self.assertTrue(report.ok)
        self.assertEqual(report.findings, ())
        self.assertEqual(report.summary['total'], 0)
        self.assertGreater(report.checked['optical_lanes'], 0)

    def test_integrity_gate_passes_for_clean_fabric(self):
        result = self._stamp_clean_fabric()

        gate = integrity_gate_for_fabric(result.fabric)

        self.assertEqual(gate.status, 'pass')
        self.assertTrue(gate.ok)
        self.assertEqual(gate.blocking_count, 0)
        self.assertEqual(gate.summary['total'], 0)
        self.assertEqual(gate.source, 'fresh_audit')

    def test_dark_mpo_position_usage_is_reported_with_structured_remediation(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.filter(pk=lane.pk).update(local_mpo_position=dark_position)

        report = audit_topology_integrity(fabric=result.fabric)

        dark_findings = [
            finding
            for finding in report.findings
            if finding.code == 'dark_mpo_position_usage' and finding.object.object_id == lane.pk
        ]
        self.assertEqual(len(dark_findings), 1)
        finding_payload = dark_findings[0].as_dict()
        self.assertEqual(finding_payload['severity'], 'error')
        self.assertEqual(finding_payload['object']['model'], 'netbox_plant_graph.opticallane')
        self.assertIn('Move modeled lanes', finding_payload['remediation'])
        self.assertEqual(finding_payload['details']['position_number'], 5)
        self.assertEqual(finding_payload['object_family'], 'architecture')
        self.assertTrue(finding_payload['workflow_flags']['path_tracing'])
        self.assertTrue(finding_payload['path_blocking'])
        self.assertEqual(finding_payload['catalog']['severity'], 'error')

        payload = report.as_dict()
        grouped = payload['grouped_summary']
        self.assertGreaterEqual(grouped['path_blocking_count'], 1)
        architecture_group = next(
            group
            for group in grouped['groups']
            if group['object_family'] == 'architecture'
        )
        self.assertEqual(architecture_group['summary']['error'], 1)
        self.assertEqual(architecture_group['codes'][0]['code'], 'dark_mpo_position_usage')
        self.assertIn('dark_mpo_position_usage', payload['finding_catalog'])

    def test_integrity_gate_warns_for_non_blocking_findings_and_honors_fail_threshold(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        OpticalLane.objects.filter(pk=lane.pk).update(channel=None)

        warn_gate = integrity_gate_for_fabric(result.fabric)

        self.assertEqual(warn_gate.status, 'warn')
        self.assertTrue(warn_gate.ok)
        self.assertEqual(warn_gate.blocking_count, 0)
        self.assertEqual(warn_gate.summary['warning'], 1)
        self.assertEqual(warn_gate.non_blocking_warning_count, 1)

        fail_gate = integrity_gate_for_fabric(result.fabric, fail_on='warning')

        self.assertEqual(fail_gate.status, 'fail')
        self.assertFalse(fail_gate.ok)
        self.assertEqual(fail_gate.fail_threshold, 'warning')
        self.assertEqual(fail_gate.fail_threshold_count, 1)

    def test_integrity_gate_fails_for_path_blocking_findings(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.filter(pk=lane.pk).update(local_mpo_position=dark_position)

        gate = integrity_gate_for_fabric(result.fabric)

        self.assertEqual(gate.status, 'fail')
        self.assertFalse(gate.ok)
        self.assertGreaterEqual(gate.blocking_count, 1)
        self.assertIn('dark_mpo_position_usage', {finding['code'] for finding in gate.blocking_findings})

    def test_all_emitted_finding_codes_are_cataloged(self):
        source = inspect.getsource(topology_integrity)
        emitted_codes = set(re.findall(r"code='([^']+)'", source))

        self.assertEqual(emitted_codes - set(topology_integrity_finding_catalog()), set())

    def test_strand_termination_count_and_cable_reference_are_reported(self):
        result = self._stamp_clean_fabric()
        strand = FiberStrand.objects.filter(segment__fabric=result.fabric).first()
        StrandTermination.objects.filter(strand=strand).order_by('pk').first().delete()
        FiberStrand.objects.filter(pk=strand.pk).update(cable_id='missing-cable-id')

        report = audit_topology_integrity(fabric=result.fabric)

        codes = self._codes(report)
        self.assertIn('fiber_strand_termination_count', codes)
        self.assertIn('cable_reference_missing', codes)

    def test_channel_position_map_completeness_and_disjointness_are_reported(self):
        result = self._stamp_clean_fabric()
        channel = TransportChannel.objects.filter(fabric=result.fabric).order_by('pk').first()
        maps = list(channel.position_maps.select_related('mpo_endpoint', 'mpo_position').order_by('pk'))
        removed_map = maps[0]
        duplicate_source = maps[1]
        removed_map.delete()

        duplicate_channel = TransportChannel.objects.create(
            fabric=result.fabric,
            endpoint=channel.endpoint,
            plane=channel.plane,
            name='Duplicate channel',
            channel_index=99,
        )
        TransportChannelPositionMap.objects.create(
            channel=duplicate_channel,
            mpo_endpoint=duplicate_source.mpo_endpoint,
            mpo_position=duplicate_source.mpo_position,
        )

        report = audit_topology_integrity(fabric=result.fabric)

        codes = self._codes(report)
        self.assertIn('channel_position_map_incomplete', codes)
        self.assertIn('channel_position_map_not_disjoint', codes)

    def test_transfer_map_and_optical_lane_cross_references_are_reported(self):
        result = self._stamp_clean_fabric()
        other_architecture = FabricArchitecture.objects.create(
            name='Other Architecture',
            slug='other-architecture',
            version='v2',
        )
        other_site = Site.objects.create(name='Other Site', slug='other-site', status='active')
        other_fabric = Fabric.objects.create(
            architecture=other_architecture,
            name='Other Fabric',
            slug='other-fabric',
            scope_site=other_site,
        )
        other_node = FabricNode.objects.create(
            fabric=other_fabric,
            name='Other Node',
            address='OTHER',
            node_kind='passive_assembly',
        )
        other_endpoint = Endpoint.objects.create(
            fabric=other_fabric,
            node=other_node,
            name='MPO-1',
            address='OTHER.MPO-1',
            endpoint_kind='connector',
            connector_kind='mpo-12',
            position_count=12,
        )
        other_position = ConnectorPosition.objects.create(endpoint=other_endpoint, position_number=1)

        transfer_map = TransferMap.objects.filter(fabric=result.fabric).order_by('pk').first()
        TransferMap.objects.filter(pk=transfer_map.pk).update(src_position=other_position)

        lane = result.source_lanes[0]
        wrong_channel = TransportChannel.objects.filter(fabric=result.fabric).exclude(endpoint=lane.endpoint).first()
        OpticalLane.objects.filter(pk=lane.pk).update(channel=wrong_channel)

        report = audit_topology_integrity(fabric=result.fabric)

        codes = self._codes(report)
        self.assertIn('transfer_map_position_fabric_mismatch', codes)
        self.assertIn('transfer_map_position_owner_mismatch', codes)
        self.assertIn('optical_lane_channel_endpoint_mismatch', codes)

    def test_management_command_outputs_json_for_fabric_scope(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.filter(pk=lane.pk).update(local_mpo_position=dark_position)

        stdout = StringIO()
        call_command(
            'mpf_audit_integrity',
            '--fabric',
            result.fabric.slug,
            '--format',
            'json',
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['scope']['fabric_slug'], result.fabric.slug)
        self.assertGreater(payload['summary']['error'], 0)
        self.assertIn(
            'dark_mpo_position_usage',
            {finding['code'] for finding in payload['findings']},
        )
        self.assertIn('grouped_summary', payload)
        self.assertIn('finding_catalog', payload)

    def test_persist_topology_integrity_report_creates_operation_run_snapshot(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.filter(pk=lane.pk).update(local_mpo_position=dark_position)
        report = audit_topology_integrity(fabric=result.fabric)

        run = persist_topology_integrity_report(report, parameters={'trigger': 'test'})

        self.assertEqual(run.profile, TOPOLOGY_INTEGRITY_OPERATION_PROFILE)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.fabric, result.fabric)
        self.assertEqual(run.metadata['operation_kind'], TOPOLOGY_INTEGRITY_OPERATION_KIND)
        self.assertEqual(run.result['summary'], report.summary)
        self.assertEqual(run.result['finding_count'], len(report.findings))
        self.assertEqual(run.result['path_blocking_count'], report.grouped_summary['path_blocking_count'])
        self.assertEqual(
            run.result['report']['findings'][0]['catalog']['object_family'],
            'architecture',
        )

    def test_integrity_gate_serializes_supplied_and_latest_report_payloads(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.filter(pk=lane.pk).update(local_mpo_position=dark_position)
        report = audit_topology_integrity(fabric=result.fabric)

        supplied_gate = integrity_gate_for_fabric(result.fabric, report=report.as_dict())
        supplied_payload = supplied_gate.as_dict()

        self.assertEqual(supplied_gate.status, 'fail')
        self.assertEqual(supplied_payload['status'], 'fail')
        self.assertEqual(supplied_payload['path_blocking_count'], supplied_gate.blocking_count)
        json.dumps(supplied_payload)

        run = persist_topology_integrity_report(report, parameters={'trigger': 'gate-test'})
        latest_gate = integrity_gate_for_fabric(result.fabric, latest=True, run_audit=False)

        self.assertEqual(latest_gate.status, 'fail')
        self.assertEqual(latest_gate.source, 'latest_operation_run')
        self.assertEqual(latest_gate.operation_run_id, run.pk)
        self.assertEqual(
            latest_gate.as_dict()['blocking_findings'][0]['catalog']['object_family'],
            'architecture',
        )

    def test_management_command_can_persist_operation_run(self):
        result = self._stamp_clean_fabric()
        lane = result.source_lanes[0]
        dark_position = ConnectorPosition.objects.get(
            endpoint=lane.local_mpo_endpoint,
            position_number=5,
        )
        OpticalLane.objects.filter(pk=lane.pk).update(local_mpo_position=dark_position)

        stdout = StringIO()
        call_command(
            'mpf_audit_integrity',
            '--fabric',
            result.fabric.slug,
            '--format',
            'json',
            '--persist-operation-run',
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        run = OperationRun.objects.get(pk=payload['operation_run']['id'])
        self.assertEqual(run.profile, TOPOLOGY_INTEGRITY_OPERATION_PROFILE)
        self.assertGreaterEqual(run.result['summary']['error'], 1)
        self.assertEqual(run.result['report']['scope']['fabric_slug'], result.fabric.slug)
