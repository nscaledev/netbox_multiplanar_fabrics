import json
import tempfile
from dataclasses import replace
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from dcim.models import Device, DeviceRole, DeviceType, Interface, Manufacturer, Module, ModuleType, Site

from netbox_plant_graph.models import (
    CableAssembly,
    ConnectorPosition,
    Endpoint,
    Fabric,
    FabricArchitecture,
    FabricNode,
    FiberSegment,
    FiberStrand,
    Plane,
    StrandTermination,
    TransportChannel,
    TransportChannelPositionMap,
    ArchitectureRole,
    TransferPattern,
    AllocationRuleSet,
    StampTemplate,
    TransceiverConnector,
)
from netbox_plant_graph.services.imports import build_import_plan, reconcile_import_payload
from netbox_plant_graph.services.imports.reconciliation import PROVENANCE_METADATA_NAMESPACE
from netbox_plant_graph.services.architecture import (
    ARCHITECTURE_SLUG,
    ARCHITECTURE_VERSION,
    CHANNEL_MAP_MATRIX,
    MPO_DARK_POSITIONS,
    MPO_POSITION_COUNT,
)
from netbox_plant_graph.services.architecture_schema import (
    ARCHITECTURE_COMPATIBILITY_COMPATIBLE,
    ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE,
    ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
)
from netbox_plant_graph.services.blueprint_registry import (
    architecture_definition_to_payload,
    get_default_blueprint_registry,
)


class V2ImportReconciliationTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name='Import Site', slug='import-site', status='active')
        self.architecture = FabricArchitecture.objects.create(
            name='Import Architecture',
            slug='import-architecture',
            version='v2',
        )
        self.fabric = Fabric.objects.create(
            architecture=self.architecture,
            name='Import Fabric',
            slug='import-fabric',
            scope_site=self.site,
        )
        self.plane = Plane.objects.create(fabric=self.fabric, plane_number=1, label='Plane 1')
        self.node = FabricNode.objects.create(
            fabric=self.fabric,
            name='GPU-1',
            address='GPU-1',
            node_kind='active_device',
        )
        self.peer_node = FabricNode.objects.create(
            fabric=self.fabric,
            name='Panel-1',
            address='Panel-1',
            node_kind='passive_assembly',
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
        self.position_1 = ConnectorPosition.objects.create(endpoint=self.mpo, position_number=1, label='1')
        self.position_2 = ConnectorPosition.objects.create(endpoint=self.mpo, position_number=2, label='2')
        self.peer_mpo = Endpoint.objects.create(
            fabric=self.fabric,
            node=self.peer_node,
            name='FRONT.MPO-1',
            address='Panel-1.FRONT.MPO-1',
            endpoint_kind='connector',
            connector_kind='mpo-12',
            position_count=12,
        )
        self.segment = FiberSegment.objects.create(
            fabric=self.fabric,
            name='Jumper-1',
            a_endpoint=self.mpo,
            b_endpoint=self.peer_mpo,
        )
        self.strand_1 = FiberStrand.objects.create(segment=self.segment, strand_index=1)
        self.strand_2 = FiberStrand.objects.create(segment=self.segment, strand_index=2)

    def test_transceiver_assignment_import_binds_module_and_mpo_connector_rows(self):
        manufacturer = Manufacturer.objects.create(name='NVIDIA Import', slug='nvidia-import')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Import Tray',
            slug='gb300-import-tray',
        )
        module_type = ModuleType.objects.create(
            manufacturer=manufacturer,
            model='MMS4X00-NM-T Import',
            part_number='MMS4X00-NM-T',
        )
        role = DeviceRole.objects.create(name='Import GPU Tray', slug='import-gpu-tray', color='3366ff')
        device = Device.objects.create(name='gpu-import-1', device_type=device_type, role=role, site=self.site)
        interface = Interface.objects.create(device=device, name='OSFP-1', type='800gbase-x-osfp')
        self.osfp.source_type = ContentType.objects.get_for_model(interface, for_concrete_model=False)
        self.osfp.source_id = interface.pk
        self.osfp.save()
        Endpoint.objects.create(
            fabric=self.fabric,
            node=self.node,
            parent=self.osfp,
            name='OSFP-1.MPO-2',
            address='GPU-1.OSFP-1.MPO-2',
            endpoint_kind='subconnector',
            connector_kind='mpo-12',
            position_count=12,
            metadata={'mpo_index': 2},
        )
        payload = {
            'items': [
                {
                    'kind': 'transceiver_assignment',
                    'fabric': self.fabric.slug,
                    'endpoint': self.osfp.address,
                    'module_type_part_number': module_type.part_number,
                    'role_hint': 'gb300_compute_osfp',
                }
            ]
        }

        preview = reconcile_import_payload(payload)
        self.assertEqual(preview.summary.create, 1)
        self.assertEqual(TransceiverConnector.objects.count(), 0)

        applied = reconcile_import_payload(payload, apply=True)
        self.assertTrue(applied.committed, [diff.message for diff in applied.diffs])
        self.assertEqual(applied.summary.create, 1)
        self.assertEqual(Module.objects.filter(module_type=module_type).count(), 1)
        connectors = TransceiverConnector.objects.filter(endpoint__parent=self.osfp)
        self.assertEqual(connectors.count(), 2)
        self.assertEqual(
            set(connectors.values_list('connector_profile__profile__slug', flat=True)),
            {'osfp-dual-mpo12-apc-800g-4x200g-dr4'},
        )

        second_preview = reconcile_import_payload(payload)
        self.assertEqual(second_preview.summary.skip, 1)

    def test_dry_run_reports_create_update_skip_and_conflict_without_writes(self):
        CableAssembly.objects.create(site=self.site, cable_id='EXISTING-1', manufacturer='Existing Maker')
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'NEW-1',
                    'manufacturer': 'New Maker',
                },
                {
                    'kind': 'endpoint',
                    'fabric': self.fabric.slug,
                    'node': self.node.address,
                    'address': self.osfp.address,
                    'name': 'OSFP-1 updated',
                    'connector_kind': 'osfp',
                },
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'EXISTING-1',
                    'manufacturer': 'Existing Maker',
                },
                {
                    'kind': 'fiber_strand_cable',
                    'fabric': self.fabric.slug,
                    'segment': self.segment.name,
                    'strand_index': 1,
                    'cable_site': self.site.slug,
                    'cable_id': 'MISSING-CABLE',
                },
            ],
        }

        plan = build_import_plan(payload)

        self.assertEqual([diff.outcome for diff in plan.diffs], ['create', 'update', 'skip', 'conflict'])
        self.assertEqual(plan.summary.to_dict(), {'total': 4, 'create': 1, 'update': 1, 'skip': 1, 'conflict': 1})
        self.assertEqual(plan.diffs[0].message, 'create cable_assembly site=import-site cable_id=NEW-1')
        self.assertIn('fields=name', plan.diffs[1].message)
        self.assertIn('missing CableAssembly site=import-site cable_id=MISSING-CABLE', plan.diffs[3].message)
        self.assertFalse(CableAssembly.objects.filter(site=self.site, cable_id='NEW-1').exists())
        self.osfp.refresh_from_db()
        self.assertEqual(self.osfp.name, 'OSFP-1')

    def test_architecture_hint_gate_allows_compatible_payloads(self):
        payload = {
            'architecture': {
                'slug': ARCHITECTURE_SLUG,
                'version': ARCHITECTURE_VERSION,
                'schema_contract_version': ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
                'channel_map_matrix': [dict(row) for row in CHANNEL_MAP_MATRIX],
                'mpo_position_count': MPO_POSITION_COUNT,
                'dark_positions': list(MPO_DARK_POSITIONS),
            },
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'ARCH-GATE-OK',
                    'manufacturer': 'Gate Maker',
                },
            ],
        }

        plan = build_import_plan(payload)

        self.assertEqual(plan.architecture_gate.status, ARCHITECTURE_COMPATIBILITY_COMPATIBLE)
        self.assertTrue(plan.architecture_gate.hint_present)
        self.assertFalse(plan.has_conflicts)
        self.assertEqual(plan.summary.to_dict(), {'total': 1, 'create': 1, 'update': 0, 'skip': 0, 'conflict': 0})
        self.assertEqual(plan.diffs[0].kind, 'cable_assembly')

    def test_architecture_hint_gate_blocks_incompatible_payloads_before_rows(self):
        payload = {
            'architecture_slug': ARCHITECTURE_SLUG,
            'architecture_version': 'v9',
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'ARCH-GATE-BLOCKED',
                    'manufacturer': 'Gate Maker',
                },
            ],
        }

        plan = reconcile_import_payload(payload, apply=True)

        self.assertEqual(plan.architecture_gate.status, ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE)
        self.assertTrue(plan.has_conflicts)
        self.assertFalse(plan.committed)
        self.assertEqual(plan.summary.to_dict(), {'total': 1, 'create': 0, 'update': 0, 'skip': 0, 'conflict': 1})
        self.assertEqual(plan.diffs[0].index, -1)
        self.assertEqual(plan.diffs[0].kind, 'architecture_gate')
        self.assertEqual(
            plan.diffs[0].details['architecture_gate']['issues'][0]['code'],
            'architecture_version_mismatch',
        )
        self.assertFalse(CableAssembly.objects.filter(site=self.site, cable_id='ARCH-GATE-BLOCKED').exists())

    def test_architecture_hint_gate_blocks_channel_map_mismatch(self):
        bad_matrix = [dict(row) for row in CHANNEL_MAP_MATRIX]
        bad_matrix[0]['positions'] = [1, 12, 2]
        payload = {
            'architecture': {
                'slug': ARCHITECTURE_SLUG,
                'version': ARCHITECTURE_VERSION,
                'channel_map_matrix': bad_matrix,
            },
            'items': [],
        }

        plan = build_import_plan(payload)

        self.assertEqual(plan.architecture_gate.status, ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE)
        self.assertTrue(plan.has_conflicts)
        self.assertEqual(plan.diffs[0].details['architecture_gate']['issues'][0]['code'], 'channel_map_matrix_mismatch')

    def test_apply_is_idempotent_for_safe_subset(self):
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'APPLY-1',
                    'manufacturer': 'Fiber Maker',
                    'model_id': 'MPO-12',
                    'metadata': {'source': 'unit-test'},
                },
                {
                    'kind': 'endpoint',
                    'fabric': self.fabric.slug,
                    'node': self.node.address,
                    'address': 'GPU-1.OSFP-2',
                    'name': 'OSFP-2',
                    'endpoint_kind': 'plugin_port',
                    'connector_kind': 'osfp',
                    'metadata': {'source': 'unit-test'},
                },
                {
                    'kind': 'transport_channel',
                    'fabric': self.fabric.slug,
                    'endpoint': self.osfp.address,
                    'channel_index': 1,
                    'plane': 1,
                    'speed_gbps': 200,
                    'metadata': {'source': 'unit-test'},
                },
                {
                    'kind': 'transport_channel_position_map',
                    'fabric': self.fabric.slug,
                    'channel_endpoint': self.osfp.address,
                    'channel_index': 1,
                    'mpo_endpoint': self.mpo.address,
                    'mpo_position': 1,
                    'metadata': {'source': 'unit-test'},
                },
                {
                    'kind': 'fiber_strand_cable',
                    'fabric': self.fabric.slug,
                    'segment': self.segment.name,
                    'strand_index': 1,
                    'cable_site': self.site.slug,
                    'cable_id': 'APPLY-1',
                },
                {
                    'kind': 'strand_termination',
                    'fabric': self.fabric.slug,
                    'segment': self.segment.name,
                    'strand_index': 1,
                    'mpo_endpoint': self.mpo.address,
                    'mpo_position': 1,
                    'termination_index': 1,
                    'label': 'strand-1-local',
                    'metadata': {'source': 'unit-test'},
                },
            ],
        }

        first = reconcile_import_payload(payload, apply=True)
        second = reconcile_import_payload(payload, apply=True)

        self.assertEqual(first.summary.conflict, 0)
        self.assertEqual(second.summary.to_dict(), {'total': 6, 'create': 0, 'update': 0, 'skip': 6, 'conflict': 0})
        self.assertTrue(CableAssembly.objects.filter(site=self.site, cable_id='APPLY-1').exists())
        self.assertTrue(Endpoint.objects.filter(fabric=self.fabric, address='GPU-1.OSFP-2').exists())
        self.assertTrue(
            TransportChannel.objects.filter(
                fabric=self.fabric,
                endpoint=self.osfp,
                channel_index=1,
            ).exists()
        )
        self.assertTrue(
            TransportChannelPositionMap.objects.filter(
                channel__fabric=self.fabric,
                channel__endpoint=self.osfp,
                mpo_position=self.position_1,
            ).exists()
        )
        self.strand_1.refresh_from_db()
        self.assertEqual(self.strand_1.cable_id, 'APPLY-1')
        self.assertEqual(self.strand_1.cable_site, self.site)
        self.assertTrue(StrandTermination.objects.filter(strand=self.strand_1, mpo_position=self.position_1).exists())

    def test_provenance_propagates_to_metadata_and_diff_details(self):
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'PROVENANCE-1',
                    'manufacturer': 'Traceable Fiber',
                    'source_system': 'madison-workbook',
                    'source_document': 'fiber-bom.xlsx',
                    'source_row': 17,
                    'external_id': 'cable-row-17',
                    'idempotency_key': 'cable:import-site:PROVENANCE-1',
                },
                {
                    'kind': 'endpoint',
                    'fabric': self.fabric.slug,
                    'node': self.node.address,
                    'address': 'GPU-1.OSFP-PROV',
                    'name': 'OSFP-PROV',
                    'endpoint_kind': 'plugin_port',
                    'connector_kind': 'osfp',
                    'metadata': {'source': 'unit-test'},
                    'source_system': 'madison-workbook',
                    'source_document': 'endpoint-map.xlsx',
                    'source_row': 'Endpoints!42',
                    'external_id': 'endpoint-row-42',
                    'idempotency_key': 'endpoint:GPU-1.OSFP-PROV',
                },
                {
                    'kind': 'strand_termination',
                    'fabric': self.fabric.slug,
                    'segment': self.segment.name,
                    'strand_index': 2,
                    'mpo_endpoint': self.mpo.address,
                    'mpo_position': 2,
                    'termination_index': 2,
                    'source_system': 'madison-workbook',
                    'source_document': 'termination-map.xlsx',
                    'source_row': 55,
                    'external_id': 'termination-row-55',
                    'idempotency_key': 'termination:Jumper-1:2',
                },
            ],
        }

        plan = reconcile_import_payload(payload, apply=True)

        self.assertEqual(plan.summary.to_dict(), {'total': 3, 'create': 3, 'update': 0, 'skip': 0, 'conflict': 0})
        cable = CableAssembly.objects.get(site=self.site, cable_id='PROVENANCE-1')
        endpoint = Endpoint.objects.get(fabric=self.fabric, address='GPU-1.OSFP-PROV')
        termination = StrandTermination.objects.get(strand=self.strand_2, mpo_position=self.position_2)
        self.assertEqual(
            cable.metadata[PROVENANCE_METADATA_NAMESPACE],
            {
                'source_system': 'madison-workbook',
                'source_document': 'fiber-bom.xlsx',
                'source_row': 17,
                'external_id': 'cable-row-17',
                'idempotency_key': 'cable:import-site:PROVENANCE-1',
            },
        )
        self.assertEqual(endpoint.metadata['source'], 'unit-test')
        self.assertEqual(endpoint.metadata[PROVENANCE_METADATA_NAMESPACE]['source_row'], 'Endpoints!42')
        self.assertEqual(
            termination.metadata[PROVENANCE_METADATA_NAMESPACE]['idempotency_key'],
            'termination:Jumper-1:2',
        )
        self.assertEqual(plan.diffs[0].details['provenance']['source_document'], 'fiber-bom.xlsx')
        self.assertIn('source_row=17', plan.diffs[0].message)

    def test_idempotency_key_metadata_reapply_is_noop_and_preserves_metadata(self):
        CableAssembly.objects.create(
            site=self.site,
            cable_id='IDEMPOTENT-1',
            manufacturer='Stable Fiber',
            metadata={'preserved': True},
        )
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'IDEMPOTENT-1',
                    'manufacturer': 'Stable Fiber',
                    'source_system': 'madison-workbook',
                    'source_document': 'fiber-bom.xlsx',
                    'source_row': 31,
                    'external_id': 'cable-row-31',
                    'idempotency_key': 'cable:import-site:IDEMPOTENT-1',
                },
            ],
        }

        first = reconcile_import_payload(payload, apply=True)
        second = reconcile_import_payload(payload, apply=True)

        self.assertEqual(first.summary.to_dict(), {'total': 1, 'create': 0, 'update': 1, 'skip': 0, 'conflict': 0})
        self.assertEqual(second.summary.to_dict(), {'total': 1, 'create': 0, 'update': 0, 'skip': 1, 'conflict': 0})
        cable = CableAssembly.objects.get(site=self.site, cable_id='IDEMPOTENT-1')
        self.assertTrue(cable.metadata['preserved'])
        self.assertEqual(
            cable.metadata[PROVENANCE_METADATA_NAMESPACE]['idempotency_key'],
            'cable:import-site:IDEMPOTENT-1',
        )

    def test_dependency_planner_applies_prerequisites_before_dependents(self):
        payload = {
            'payload_version': 'v2.1',
            'source_label': 'unit-test-bundle',
            'items': [
                {
                    'kind': 'transport_channel',
                    'fabric': self.fabric.slug,
                    'endpoint': 'GPU-1.OSFP-9',
                    'channel_index': 9,
                    'plane': 1,
                    'speed_gbps': 400,
                },
                {
                    'kind': 'endpoint',
                    'fabric': self.fabric.slug,
                    'node': self.node.address,
                    'address': 'GPU-1.OSFP-9',
                    'name': 'OSFP-9',
                    'endpoint_kind': 'plugin_port',
                    'connector_kind': 'osfp',
                },
            ],
        }

        plan = reconcile_import_payload(payload, apply=True)

        self.assertEqual(plan.summary.to_dict(), {'total': 2, 'create': 2, 'update': 0, 'skip': 0, 'conflict': 0})
        self.assertEqual(plan.payload_version, 'v2.1')
        self.assertEqual(plan.source_label, 'unit-test-bundle')
        self.assertEqual(plan.apply_order, (1, 0))
        self.assertTrue(plan.committed, plan.to_dict())
        self.assertEqual([diff.index for diff in plan.diffs], [0, 1])
        self.assertEqual([diff.outcome for diff in plan.diffs], ['create', 'create'])
        edge = plan.dependency_edges[0]
        self.assertEqual(edge.status, 'planned')
        self.assertEqual(edge.dependent_index, 0)
        self.assertEqual(edge.prerequisite_index, 1)
        endpoint = Endpoint.objects.get(fabric=self.fabric, address='GPU-1.OSFP-9')
        self.assertTrue(
            TransportChannel.objects.filter(
                fabric=self.fabric,
                endpoint=endpoint,
                channel_index=9,
            ).exists()
        )

    def test_missing_prerequisite_conflict_includes_dependency_details(self):
        payload = {
            'items': [
                {
                    'kind': 'transport_channel',
                    'fabric': self.fabric.slug,
                    'endpoint': 'GPU-1.MISSING',
                    'channel_index': 10,
                    'source_system': 'madison-workbook',
                    'source_document': 'channels.xlsx',
                    'source_row': 88,
                    'external_id': 'channel-row-88',
                    'idempotency_key': 'channel:GPU-1.MISSING:10',
                },
            ],
        }

        plan = build_import_plan(payload)

        self.assertEqual(plan.summary.to_dict(), {'total': 1, 'create': 0, 'update': 0, 'skip': 0, 'conflict': 1})
        self.assertEqual(plan.diffs[0].error, 'missing Endpoint fabric=import-fabric address=GPU-1.MISSING')
        self.assertEqual(plan.diffs[0].details['code'], 'missing_prerequisite')
        self.assertEqual(plan.diffs[0].details['provenance']['source_document'], 'channels.xlsx')
        self.assertIn('source_row=88', plan.diffs[0].message)
        missing = plan.diffs[0].details['missing_prerequisites'][0]
        self.assertEqual(missing['status'], 'missing')
        self.assertEqual(missing['field'], 'endpoint')
        self.assertEqual(missing['reference']['kind'], 'endpoint')
        self.assertEqual(missing['reference']['lookup']['address'], 'GPU-1.MISSING')

    def test_dependency_cycle_reports_structured_conflicts(self):
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'CYCLE-A',
                    'parent_cable': 'CYCLE-B',
                },
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'CYCLE-B',
                    'parent_cable': 'CYCLE-A',
                },
            ],
        }

        plan = build_import_plan(payload)

        self.assertEqual(plan.summary.to_dict(), {'total': 2, 'create': 0, 'update': 0, 'skip': 0, 'conflict': 2})
        self.assertEqual([diff.details['code'] for diff in plan.diffs], ['dependency_cycle', 'dependency_cycle'])
        self.assertEqual(plan.diffs[0].details['item_indexes'], [0, 1])
        self.assertEqual(
            {(edge.dependent_index, edge.prerequisite_index) for edge in plan.dependency_edges},
            {(0, 1), (1, 0)},
        )

    def test_apply_rolls_back_when_later_row_conflicts(self):
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'ROLLBACK-1',
                    'manufacturer': 'Rollback Maker',
                },
                {
                    'kind': 'endpoint',
                    'fabric': self.fabric.slug,
                    'node': 'MISSING-NODE',
                    'address': 'GPU-1.BAD',
                    'endpoint_kind': 'plugin_port',
                    'connector_kind': 'osfp',
                },
            ],
        }

        plan = reconcile_import_payload(payload, apply=True)

        self.assertTrue(plan.applied)
        self.assertFalse(plan.committed)
        self.assertEqual([diff.outcome for diff in plan.diffs], ['create', 'conflict'])
        self.assertFalse(CableAssembly.objects.filter(site=self.site, cable_id='ROLLBACK-1').exists())
        self.assertFalse(Endpoint.objects.filter(fabric=self.fabric, address='GPU-1.BAD').exists())

    def test_conflict_when_mpo_position_is_already_terminated_by_another_strand(self):
        StrandTermination.objects.create(
            strand=self.strand_1,
            mpo_endpoint=self.mpo,
            mpo_position=self.position_1,
            termination_index=1,
        )
        payload = {
            'items': [
                {
                    'kind': 'strand_termination',
                    'fabric': self.fabric.slug,
                    'segment': self.segment.name,
                    'strand_index': 2,
                    'mpo_endpoint': self.mpo.address,
                    'mpo_position': 1,
                    'termination_index': 2,
                },
            ],
        }

        plan = reconcile_import_payload(payload)

        self.assertEqual(plan.summary.conflict, 1)
        self.assertIn('mpo_position already terminated by segment=Jumper-1 strand=1', plan.diffs[0].message)
        self.assertFalse(StrandTermination.objects.filter(strand=self.strand_2, mpo_position=self.position_1).exists())

    def test_partial_import_errors_do_not_hide_valid_rows(self):
        payload = {
            'items': [
                'not-an-object',
                {'kind': 'cable_assembly', 'cable_id': 'MISSING-SITE'},
                {'kind': 'unsupported_kind', 'name': 'ignored'},
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'VALID-1',
                    'manufacturer': 'Valid Maker',
                },
            ],
        }

        plan = build_import_plan(payload)

        self.assertEqual([diff.outcome for diff in plan.diffs], ['conflict', 'conflict', 'conflict', 'create'])
        self.assertEqual(plan.summary.to_dict(), {'total': 4, 'create': 1, 'update': 0, 'skip': 0, 'conflict': 3})
        self.assertIn('item must be a JSON object', plan.diffs[0].message)
        self.assertIn('missing required field site', plan.diffs[1].message)
        self.assertIn('unsupported kind unsupported_kind', plan.diffs[2].message)

    def test_management_command_dry_runs_by_default(self):
        payload = {
            'items': [
                {
                    'kind': 'cable_assembly',
                    'site': self.site.slug,
                    'cable_id': 'COMMAND-DRY-RUN',
                    'manufacturer': 'Command Maker',
                },
            ],
        }
        output = StringIO()

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as handle:
            json.dump(payload, handle)
            handle.flush()
            call_command('mpf_import_reconcile', handle.name, stdout=output)

        self.assertIn(
            'MPF import reconciliation dry-run: total=1 create=1 update=0 skip=0 conflict=0',
            output.getvalue(),
        )
        self.assertIn('create cable_assembly site=import-site cable_id=COMMAND-DRY-RUN', output.getvalue())
        self.assertFalse(CableAssembly.objects.filter(site=self.site, cable_id='COMMAND-DRY-RUN').exists())

    def test_blueprint_bundle_dry_run_normalizes_to_blueprint_item(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')
        definition = replace(entry.definition, slug='imported-blueprint-dry-run', version='v1')
        payload = {
            'bundle_version': '2026.05',
            'bundle_author': 'Unit Test',
            'schema_contract_version': ARCHITECTURE_SCHEMA_CONTRACT_VERSION,
            'architecture': architecture_definition_to_payload(definition),
            'parameter_schema': entry.parameter_schema,
            'required_device_types': entry.required_device_types,
        }

        plan = build_import_plan(payload)

        self.assertEqual(plan.payload_version, '2026.05')
        self.assertEqual(plan.source_label, 'Unit Test')
        self.assertFalse(plan.architecture_gate.hint_present)
        self.assertEqual(plan.summary.to_dict(), {'total': 1, 'create': 1, 'update': 0, 'skip': 0, 'conflict': 0})
        self.assertEqual(plan.diffs[0].kind, 'fabric_architecture_blueprint')
        self.assertIn('slug=imported-blueprint-dry-run version=v1', plan.diffs[0].message)
        self.assertFalse(FabricArchitecture.objects.filter(slug='imported-blueprint-dry-run').exists())

    def test_blueprint_import_apply_persists_architecture_contract_rows(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')
        definition = replace(entry.definition, slug='imported-blueprint-apply', version='v1')
        payload = {
            'items': [
                {
                    'kind': 'fabric_architecture_blueprint',
                    'definition': architecture_definition_to_payload(definition),
                    'parameter_schema': entry.parameter_schema,
                    'required_device_types': entry.required_device_types,
                    'stamp_templates': {
                        'imported-blueprint-mini-proof': {
                            'slug': 'imported-blueprint-mini-proof',
                            'name': 'Imported blueprint mini proof',
                            'template': {
                                **entry.stamp_templates['roce-4-plane-mini-proof']['template'],
                                'architecture_slug': 'imported-blueprint-apply',
                                'architecture_version': 'v1',
                            },
                        },
                    },
                },
            ],
        }

        plan = reconcile_import_payload(payload, apply=True)

        self.assertTrue(plan.committed, plan.to_dict())
        self.assertEqual(plan.summary.create, 1)
        architecture = FabricArchitecture.objects.get(slug='imported-blueprint-apply', version='v1')
        self.assertEqual(ArchitectureRole.objects.filter(architecture=architecture).count(), len(definition.roles))
        self.assertEqual(TransferPattern.objects.filter(architecture=architecture).count(), len(definition.transfer_patterns))
        self.assertEqual(AllocationRuleSet.objects.filter(architecture=architecture).count(), len(definition.allocation_rule_sets))
        self.assertEqual(StampTemplate.objects.filter(architecture=architecture).count(), 1)
        self.assertEqual(
            architecture.metadata['blueprint']['required_device_types']['gpu_tray'][0],
            'gb300-tray',
        )
        self.assertEqual(
            architecture.metadata['blueprint']['parameter_schema']['type'],
            'object',
        )

    def test_blueprint_import_reports_schema_validation_conflict(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')
        definition_payload = architecture_definition_to_payload(
            replace(entry.definition, slug='imported-blueprint-invalid', version='v1')
        )
        definition_payload['dark_positions'] = []
        payload = {
            'items': [
                {
                    'kind': 'fabric_architecture_blueprint',
                    'definition': definition_payload,
                    'parameter_schema': entry.parameter_schema,
                    'required_device_types': entry.required_device_types,
                },
            ],
        }

        plan = reconcile_import_payload(payload, apply=True)

        self.assertFalse(plan.committed)
        self.assertEqual(plan.summary.conflict, 1)
        self.assertEqual(plan.diffs[0].details['code'], 'architecture_schema_invalid')
        self.assertEqual(plan.diffs[0].details['issues'][0]['code'], 'mpo_positions.coverage')
        self.assertFalse(FabricArchitecture.objects.filter(slug='imported-blueprint-invalid').exists())

    def test_blueprint_bundle_rejects_contract_version_mismatch(self):
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-gb300-2x2-shuffle', 'v2')
        definition = replace(entry.definition, slug='imported-blueprint-contract-mismatch', version='v1')
        payload = {
            'bundle_version': '2026.05',
            'bundle_author': 'Unit Test',
            'schema_contract_version': 'v99',
            'architecture': architecture_definition_to_payload(definition),
            'parameter_schema': entry.parameter_schema,
            'required_device_types': entry.required_device_types,
        }

        plan = build_import_plan(payload)

        self.assertEqual(plan.summary.conflict, 1)
        self.assertEqual(plan.diffs[0].details['code'], 'schema_contract_version_mismatch')
