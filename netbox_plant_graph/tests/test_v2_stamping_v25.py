from copy import deepcopy

from django.test import TestCase
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleType, Site

from netbox_plant_graph.models import (
    AllocationRuleSet,
    ArchitectureRole,
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
    StampTemplate,
    StrandTermination,
    SuppressionRule,
    TransferMap,
    TransportChannel,
    TransportChannelPositionMap,
    TransceiverConnector,
)
from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.blueprint_registry import get_default_blueprint_registry
from netbox_plant_graph.services.architecture_schema import (
    ARCHITECTURE_COMPATIBILITY_COMPATIBLE,
    ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE,
    ARCHITECTURE_COMPATIBILITY_WARNING,
)
from netbox_plant_graph.services.stamping_v25 import (
    StampValidationError,
    apply_stamp_template_v25,
    classify_stamp_retry_v25,
    preview_stamp_template_v25,
    rollback_stamp_run_v25,
)


class V2StampingV25TestCase(TestCase):
    def test_preview_describes_creates_without_mutating_database(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        before_counts = self._managed_counts()

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Dry run proof',
            fabric_slug='dry-run-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues])
        self.assertEqual(self._managed_counts(), before_counts)
        self.assertFalse(Fabric.objects.filter(slug='dry-run-proof').exists())
        self.assertGreater(preview.action_counts['create'], 0)
        self.assertTrue(preview.retry.supported)
        self.assertEqual(preview.retry.strategy, 'idempotent_reapply')
        self.assertFalse(preview.rollback.supported)
        self.assertIn(
            ('Fabric', 'dry-run-proof', 'create'),
            {(change.object_type, change.identity, change.action) for change in preview.changes},
        )

    def test_preview_catches_missing_architecture_role(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        ArchitectureRole.objects.filter(architecture=fixture.architecture, slug='gpu_tray').delete()

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Missing role proof',
            fabric_slug='missing-role-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertValidationIssue(preview, 'missing_architecture_role', 'architecture.roles.gpu_tray')

    def test_preview_architecture_gate_passes_for_builtin_fixture(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Architecture gate proof',
            fabric_slug='architecture-gate-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues])
        self.assertIsNotNone(preview.architecture_gate)
        self.assertTrue(preview.architecture_gate.fixture_valid)
        self.assertEqual(preview.architecture_gate.fixture_error_count, 0)
        self.assertEqual(preview.architecture_gate.target_source, 'template_fk')
        self.assertEqual(preview.architecture_gate.target_architecture_id, fixture.architecture.pk)
        self.assertTrue(preview.architecture_gate.persisted_schema_valid)
        self.assertEqual(preview.architecture_gate.persisted_schema_error_count, 0)
        self.assertEqual(preview.architecture_gate.compatibility_status, ARCHITECTURE_COMPATIBILITY_COMPATIBLE)
        self.assertEqual(preview.architecture_gate.compatibility_issue_count, 0)

    def test_preview_selects_non_gb300_registry_blueprint_without_legacy_rejection(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        entry = get_default_blueprint_registry().get_blueprint('roce-4-plane-h100-direct-attach', 'v2')
        template_spec = deepcopy(fixture.stamp_template.template)
        template_spec['architecture_slug'] = entry.slug
        template_spec['architecture_version'] = entry.version
        template_spec['channel_subinterfaces'] = {
            **template_spec['channel_subinterfaces'],
            'channel_map_matrix': [dict(item) for item in entry.definition.channel_map_matrix],
        }
        template = StampTemplate.objects.create(
            name='H100 registry preview template',
            slug='h100-registry-preview-template',
            template=template_spec,
        )

        preview = preview_stamp_template_v25(
            template=template,
            fabric_name='H100 registry proof',
            fabric_slug='h100-registry-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues if issue.severity == 'error'])
        self.assertEqual(preview.architecture_gate.blueprint_source, 'registry')
        self.assertEqual(preview.architecture_gate.blueprint_slug, 'roce-4-plane-h100-direct-attach')
        self.assertFalse(preview.issues_for_code('unsupported_architecture'))
        self.assertFalse(preview.issues_for_code('unsupported_architecture_version'))

    def test_preview_surfaces_blueprint_parameter_schema_violations(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template_spec = deepcopy(fixture.stamp_template.template)
        template_spec['planes'] = [1, 2, 3, 4, 5]
        template_spec['topology_parameters'] = {'plane_count': 5}
        fixture.stamp_template.template = template_spec

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Blueprint parameter proof',
            fabric_slug='blueprint-parameter-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertValidationIssue(
            preview,
            'blueprint_parameter.maximum',
            'template.template.blueprint_parameters.topology_parameters.plane_count',
        )

    def test_preview_surfaces_missing_required_blueprint_device_types(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Blueprint device type proof',
            fabric_slug='blueprint-device-type-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues if issue.severity == 'error'])
        self.assertValidationIssue(
            preview,
            'architecture_gate.missing_device_type',
            'required_device_types.gpu_tray',
            severity='warning',
        )

    def test_preview_blocks_persisted_architecture_schema_errors_before_apply(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        fixture.allocation_rule_sets['channel_subinterface_mapping'].delete()

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Malformed architecture proof',
            fabric_slug='malformed-architecture-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertFalse(preview.architecture_gate.persisted_schema_valid)
        self.assertEqual(preview.architecture_gate.compatibility_status, 'not_checked')
        self.assertValidationIssue(
            preview,
            'architecture_schema.persisted_architecture.channel_map_rule_missing',
            'architecture.allocation_rule_sets.channel_subinterface_mapping',
        )
        with self.assertRaises(StampValidationError):
            apply_stamp_template_v25(
                template=fixture.stamp_template,
                fabric_name='Malformed architecture proof',
                fabric_slug='malformed-architecture-proof',
            )
        self.assertFalse(Fabric.objects.filter(slug='malformed-architecture-proof').exists())

    def test_preview_blocks_incompatible_persisted_architecture_before_apply(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        fixture.architecture.version = 'v2-future'
        fixture.architecture.save(update_fields=['version'])

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Incompatible architecture proof',
            fabric_slug='incompatible-architecture-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertEqual(preview.architecture_gate.compatibility_status, ARCHITECTURE_COMPATIBILITY_INCOMPATIBLE)
        self.assertValidationIssue(
            preview,
            'architecture_compatibility.architecture_version_mismatch',
            'architecture.version',
        )
        with self.assertRaises(StampValidationError):
            apply_stamp_template_v25(
                template=fixture.stamp_template,
                fabric_name='Incompatible architecture proof',
                fabric_slug='incompatible-architecture-proof',
            )
        self.assertFalse(Fabric.objects.filter(slug='incompatible-architecture-proof').exists())

    def test_preview_blocks_unsupported_template_architecture_hint_before_apply(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template_spec = deepcopy(fixture.stamp_template.template)
        template_spec['architecture_version'] = 'v2-future'
        fixture.stamp_template.template = template_spec

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Template hint proof',
            fabric_slug='template-hint-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertEqual(preview.architecture_gate.compatibility_status, 'not_checked')
        self.assertValidationIssue(
            preview,
            'architecture_gate.blueprint_not_registered',
            'template.template.architecture_version',
        )
        with self.assertRaises(StampValidationError):
            apply_stamp_template_v25(
                template=fixture.stamp_template,
                fabric_name='Template hint proof',
                fabric_slug='template-hint-proof',
            )
        self.assertFalse(Fabric.objects.filter(slug='template-hint-proof').exists())

    def test_preview_keeps_warning_only_architecture_drift_non_blocking(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        fixture.architecture.metadata = {}
        fixture.architecture.save(update_fields=['metadata'])

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Architecture warning proof',
            fabric_slug='architecture-warning-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues if issue.severity == 'error'])
        self.assertEqual(preview.architecture_gate.compatibility_status, ARCHITECTURE_COMPATIBILITY_WARNING)
        self.assertValidationIssue(
            preview,
            'architecture_compatibility.schema_contract_version_missing',
            'architecture.metadata.schema_contract_version',
            severity='warning',
        )

    def test_preview_catches_missing_device_type_selection_before_apply(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Missing device type proof',
            fabric_slug='missing-device-type-proof',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_role': role,
            },
        )

        self.assertFalse(preview.is_valid)
        self.assertValidationIssue(
            preview,
            'missing_device_type_selection',
            'creation_options.gpu_device_type',
        )
        with self.assertRaises(StampValidationError):
            apply_stamp_template_v25(
                template=fixture.stamp_template,
                fabric_name='Missing device type proof',
                fabric_slug='missing-device-type-proof',
                creation_options={
                    'enabled': True,
                    'site': site,
                    'gpu_role': role,
                },
            )
        self.assertFalse(Fabric.objects.filter(slug='missing-device-type-proof').exists())

    def test_preview_includes_netbox_creation_diff_and_name_pattern_samples(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        role = DeviceRole.objects.create(name='Fabric Endpoint', slug='fabric-endpoint', color='00ff00')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')

        before_device_count = Device.objects.count()
        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Creation preview proof',
            fabric_slug='creation-preview-proof',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': device_type,
                'gpu_role': role,
                'leaf_device_type': device_type,
                'leaf_role': role,
                'name_prefix': 'preview',
            },
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues])
        self.assertEqual(Device.objects.count(), before_device_count)
        change_types = [change.object_type for change in preview.changes]
        self.assertEqual(change_types.count('Device'), 5)
        self.assertEqual(change_types.count('Interface'), 8)
        self.assertEqual(change_types.count('ChannelSubinterface'), 32)
        self.assertIn('StampRun', change_types)
        self.assertIn('AuditEvent', change_types)
        self.assertIn(
            ('Device', 'GB300-TRAY-1', 'preview-gb300-tray-1', False),
            {
                (sample.object_type, sample.address, sample.name, sample.collision)
                for sample in preview.name_pattern_samples
            },
        )
        self.assertIn(
            ('ChannelSubinterface', 'GB300-TRAY-1.OSFP-1.channel-1', 'preview-gb300-tray-1:OSFP-1/1', False),
            {
                (sample.object_type, sample.address, sample.name, sample.collision)
                for sample in preview.name_pattern_samples
            },
        )

    def test_preview_catches_channel_map_mismatch_and_incomplete_mapping(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = deepcopy(fixture.stamp_template.template)
        channel_subinterfaces = deepcopy(template['channel_subinterfaces'])
        matrix = deepcopy(channel_subinterfaces['channel_map_matrix'])
        matrix[0]['positions'] = [1, 12, 2]
        channel_subinterfaces['channel_map_matrix'] = matrix
        template['channel_subinterfaces'] = channel_subinterfaces
        fixture.stamp_template.template = template

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Bad matrix proof',
            fabric_slug='bad-matrix-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertValidationIssue(
            preview,
            'channel_map_mismatch',
            'template.template.channel_subinterfaces.channel_map_matrix',
        )
        self.assertValidationIssue(
            preview,
            'architecture_schema.channel_map.channel_width',
            'template.template.channel_subinterfaces.channel_map_matrix[1].positions',
        )
        self.assertValidationIssue(
            preview,
            'architecture_schema.channel_map.missing_active_positions',
            'template.template.channel_subinterfaces.channel_map_matrix.mpo[1]',
        )

    def test_preview_exposes_tier2_parameter_metadata_and_name_pattern_samples(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = deepcopy(fixture.stamp_template.template)
        template['planes'] = [1, 2]
        template['topology_parameters'] = {
            'plane_count': {'value': 2, 'min': 1, 'max': 4},
            'gpu_tray_count': 1,
            'leaf_count_per_plane': 1,
        }
        template['proof_paths'] = [dict(item) for item in template['proof_paths'] if item['plane'] in {1, 2}]
        template['stamp_phases'] = [
            {'name': 'phase-a', 'planes': [1]},
            {'name': 'phase-b', 'planes': [2]},
        ]
        template['name_patterns'] = {
            'channel_subinterface': 'e{rack_id}-{tray_index}.{port_index}.{channel_index}',
        }
        fixture.stamp_template.template = template

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Tier2 preview proof',
            fabric_slug='tier2-preview-proof',
            phase='phase-a',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues])
        self.assertEqual(preview.parameter_metadata['topology_parameters']['plane_count'], 2)
        self.assertEqual(preview.parameter_metadata['phase'], {'name': 'phase-a', 'planes': [1]})
        self.assertEqual(preview.parameter_metadata['active_planes'], [1])
        self.assertIn(
            ('NamePattern:channel_subinterface', 'channel_subinterface:sample-1', 'e1-1.1.1'),
            {
                (sample.object_type, sample.address, sample.name)
                for sample in preview.name_pattern_samples
            },
        )

    def test_preview_warns_for_missing_fabric_ownership_reference(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = deepcopy(fixture.stamp_template.template)
        template['fabric_ownership'] = {
            'tenant_slug': 'missing-tenant',
        }
        fixture.stamp_template.template = template

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Missing ownership proof',
            fabric_slug='missing-ownership-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues if issue.severity == 'error'])
        self.assertValidationIssue(
            preview,
            'fabric_ownership_missing',
            'template.template.fabric_ownership.tenant_slug',
            severity='warning',
        )
        self.assertEqual(preview.parameter_metadata['fabric_ownership']['missing']['tenant_slug'], 'missing-tenant')

    def test_preview_blocks_missing_allocation_rule_override(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = deepcopy(fixture.stamp_template.template)
        template['allocation_rule_override'] = 'missing-rule-set'
        fixture.stamp_template.template = template

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Missing allocation override proof',
            fabric_slug='missing-allocation-override-proof',
        )

        self.assertFalse(preview.is_valid)
        self.assertValidationIssue(
            preview,
            'allocation_rule_override_missing',
            'template.template.allocation_rule_override',
        )
        self.assertEqual(preview.parameter_metadata['allocation_rule_override'], 'missing-rule-set')

    def test_preview_warns_when_allocation_rule_override_changes_channel_map(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        channel_spec = deepcopy(fixture.stamp_template.template['channel_subinterfaces'])
        shifted_matrix = deepcopy(channel_spec['channel_map_matrix'])
        shifted_matrix[0]['subinterface_index'] = 2
        shifted_matrix[1]['subinterface_index'] = 1
        AllocationRuleSet.objects.create(
            architecture=fixture.architecture,
            name='Shifted channel map',
            slug='shifted-channel-map',
            rule={
                'channel_map_matrix': shifted_matrix,
            },
        )
        template = deepcopy(fixture.stamp_template.template)
        template['allocation_rule_override'] = 'shifted-channel-map'
        fixture.stamp_template.template = template

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Shifted allocation proof',
            fabric_slug='shifted-allocation-proof',
        )

        self.assertTrue(preview.is_valid, [str(issue) for issue in preview.issues if issue.severity == 'error'])
        self.assertValidationIssue(
            preview,
            'allocation_rule_override_delta',
            'template.template.allocation_rule_override',
            severity='warning',
        )

    def test_preview_catches_netbox_device_name_collision_risk(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia')
        desired_type = DeviceType.objects.create(manufacturer=manufacturer, model='GB300 Tray', slug='gb300-tray')
        wrong_type = DeviceType.objects.create(manufacturer=manufacturer, model='Other Tray', slug='other-tray')
        role = DeviceRole.objects.create(name='GPU Tray', slug='gpu-tray', color='ff0000')
        site = Site.objects.create(name='Site 1', slug='site-1', status='active')
        Device.objects.create(
            name='collision-gb300-tray-1',
            device_type=wrong_type,
            role=role,
            site=site,
        )

        preview = preview_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Collision proof',
            fabric_slug='collision-proof',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': desired_type,
                'gpu_role': role,
                'leaf_device_type': desired_type,
                'leaf_role': role,
                'name_prefix': 'collision',
            },
        )

        self.assertFalse(preview.is_valid)
        self.assertValidationIssue(
            preview,
            'name_collision_risk',
            'creation_options.name_prefix.collision-gb300-tray-1',
        )
        self.assertIn(
            ('Device', 'GB300-TRAY-1', 'collision-gb300-tray-1', True),
            {
                (sample.object_type, sample.address, sample.name, sample.collision)
                for sample in preview.name_pattern_samples
            },
        )

    def test_apply_is_idempotent_for_safe_subset_and_blocks_ambiguous_second_rollback(self):
        fixture = ensure_roce_4plane_shuffle_architecture()

        first = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='V25 apply proof',
            fabric_slug='v25-apply-proof',
        )
        fabric = first.execution.fabric
        counts = self._fabric_counts(fabric)
        stamp_run_count = StampRun.objects.filter(fabric=fabric).count()

        second = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='V25 apply proof',
            fabric_slug='v25-apply-proof',
        )

        self.assertEqual(second.execution.fabric.pk, fabric.pk)
        self.assertEqual(self._fabric_counts(fabric), counts)
        self.assertEqual(StampRun.objects.filter(fabric=fabric).count(), stamp_run_count + 1)
        self.assertEqual(second.preview.action_counts['create'], 2)
        self.assertEqual(second.preview.action_counts['update'], len(second.preview.changes) - 2)

        rollback = rollback_stamp_run_v25(stamp_run=second.execution.stamp_run)
        self.assertFalse(rollback.supported)
        self.assertEqual(rollback.strategy, 'managed_object_compensation')
        self.assertEqual(rollback.stamp_run_id, second.execution.stamp_run.pk)
        self.assertValidationIssue(rollback, 'rollback_shared_fabric_stamp_runs', 'stamp_run.fabric')

    def test_v25_apply_binds_transceiver_modules_and_mpo_connectors_for_created_osfps(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        manufacturer = Manufacturer.objects.create(name='NVIDIA', slug='nvidia-xcvr-stamp')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='GB300 Tray XCVR Stamp',
            slug='gb300-tray-xcvr-stamp',
        )
        module_type = ModuleType.objects.create(
            manufacturer=manufacturer,
            model='MMS4X00-NM-T',
            part_number='MMS4X00-NM-T',
        )
        role = DeviceRole.objects.create(name='Fabric Endpoint XCVR', slug='fabric-endpoint-xcvr', color='00ff00')
        site = Site.objects.create(name='XCVR Stamp Site', slug='xcvr-stamp-site', status='active')

        result = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='XCVR stamp proof',
            fabric_slug='xcvr-stamp-proof',
            creation_options={
                'enabled': True,
                'site': site,
                'gpu_device_type': device_type,
                'gpu_role': role,
                'leaf_device_type': device_type,
                'leaf_role': role,
                'name_prefix': 'xcvr-proof',
                'transceiver_module_type': module_type,
            },
        )
        fabric = result.execution.fabric
        connectors = TransceiverConnector.objects.filter(endpoint__fabric=fabric)

        self.assertEqual(Module.objects.filter(module_type=module_type).count(), 8)
        self.assertEqual(connectors.count(), 16)
        self.assertEqual(
            set(connectors.values_list('connector_profile__profile__slug', flat=True)),
            {'osfp-dual-mpo12-apc-800g-4x200g-dr4'},
        )
        self.assertEqual(len(result.execution.stamp_run.result['transceiver_bindings']), 8)
        self.assertTrue(
            all(binding['status'] == 'bound' and len(binding['connector_ids']) == 2
                for binding in result.execution.stamp_run.result['transceiver_bindings'])
        )
        self.assertEqual(len(result.execution.stamp_run.result['managed_objects']['transceiver_connectors']), 16)

        rollback = rollback_stamp_run_v25(stamp_run=result.execution.stamp_run, apply=True)
        self.assertTrue(rollback.supported, [str(issue) for issue in rollback.issues])
        self.assertEqual(TransceiverConnector.objects.filter(endpoint__fabric=fabric).count(), 0)
        self.assertEqual(Endpoint.objects.filter(fabric=fabric).count(), 0)

    def test_phase_scoped_rollback_allows_phase_two_while_phase_one_run_remains(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        template = deepcopy(fixture.stamp_template.template)
        template['planes'] = [1, 2]
        template['topology_parameters'] = {
            'plane_count': {'value': 2, 'min': 1, 'max': 4},
            'gpu_tray_count': 1,
            'leaf_count_per_plane': 1,
        }
        template['proof_paths'] = [dict(item) for item in template['proof_paths'] if item['plane'] in {1, 2}]
        template['stamp_phases'] = [
            {'name': 'phase-a', 'planes': [1]},
            {'name': 'phase-b', 'planes': [2]},
        ]
        fixture.stamp_template.template = template

        phase_a = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Phase rollback proof',
            fabric_slug='phase-rollback-proof',
            phase='phase-a',
        )
        fabric = phase_a.execution.fabric
        after_phase_a_ids = self._managed_ids()
        phase_b = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Phase rollback proof',
            fabric_slug='phase-rollback-proof',
            phase='phase-b',
        )

        broad_preview = rollback_stamp_run_v25(stamp_run=phase_b.execution.stamp_run)
        self.assertFalse(broad_preview.supported)
        self.assertValidationIssue(broad_preview, 'rollback_shared_fabric_stamp_runs', 'stamp_run.fabric')
        shared_issue = next(issue for issue in broad_preview.issues if issue.code == 'rollback_shared_fabric_stamp_runs')
        self.assertEqual(shared_issue.context['reason'], 'manifest_not_phase_scoped')

        self._scope_stamp_run_to_new_managed_objects(phase_b.execution.stamp_run, after_phase_a_ids)
        scoped_preview = rollback_stamp_run_v25(stamp_run=phase_b.execution.stamp_run)

        self.assertTrue(scoped_preview.supported, [str(issue) for issue in scoped_preview.issues])
        self.assertFalse(scoped_preview.issues_for_code('rollback_shared_fabric_stamp_runs'))
        self.assertFalse(
            any(item.model_label == 'netbox_plant_graph.fabric' for item in scoped_preview.manifest),
            [item.natural_key for item in scoped_preview.manifest],
        )

        applied = rollback_stamp_run_v25(stamp_run=phase_b.execution.stamp_run, apply=True)

        self.assertTrue(applied.applied)
        self.assertTrue(Fabric.objects.filter(pk=fabric.pk).exists())
        self.assertTrue(StampRun.objects.filter(pk=phase_a.execution.stamp_run.pk, fabric=fabric).exists())
        self.assertTrue(StampRun.objects.filter(pk=phase_b.execution.stamp_run.pk, fabric=fabric).exists())
        self.assertTrue(Plane.objects.filter(fabric=fabric, plane_number=1).exists())
        self.assertFalse(Plane.objects.filter(fabric=fabric, plane_number=2).exists())
        self.assertTrue(OpticalLane.objects.filter(fabric=fabric, plane__plane_number=1).exists())
        self.assertFalse(OpticalLane.objects.filter(fabric=fabric, plane__plane_number=2).exists())

    def test_rollback_preview_manifest_and_apply_delete_plugin_owned_stamp(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        result = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Rollback proof',
            fabric_slug='rollback-proof',
        )
        fabric_id = result.execution.fabric.pk

        preview = rollback_stamp_run_v25(stamp_run=result.execution.stamp_run)

        self.assertTrue(preview.supported, [str(issue) for issue in preview.issues])
        self.assertEqual(preview.strategy, 'managed_object_compensation')
        self.assertGreater(len(preview.manifest), 0)
        fabric_item = next(item for item in preview.manifest if item.model_label == 'netbox_plant_graph.fabric')
        self.assertEqual(fabric_item.primary_key, fabric_id)
        self.assertEqual(fabric_item.natural_key, 'rollback-proof')
        self.assertEqual(fabric_item.action, 'delete')
        self.assertEqual(fabric_item.ownership_marker, 'fabric.metadata.fixture_template_executor')

        applied = rollback_stamp_run_v25(stamp_run=result.execution.stamp_run, apply=True)

        self.assertTrue(applied.applied)
        self.assertEqual(applied.deleted_counts['fabrics'], 1)
        self.assertFalse(Fabric.objects.filter(pk=fabric_id).exists())
        result.execution.stamp_run.refresh_from_db()
        self.assertEqual(result.execution.stamp_run.result['rollback']['state'], 'completed')
        self.assertEqual(result.execution.stamp_run.result['rollback']['manifest'][0]['action'], 'delete')

        second_preview = rollback_stamp_run_v25(stamp_run=result.execution.stamp_run)
        self.assertFalse(second_preview.supported)
        self.assertTrue(second_preview.applied)

    def test_rollback_blocks_ambiguous_ownership_marker(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        result = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Ambiguous rollback proof',
            fabric_slug='ambiguous-rollback-proof',
        )
        lane = OpticalLane.objects.filter(fabric=result.execution.fabric).first()
        lane.metadata = {}
        lane.save(update_fields=['metadata'])

        preview = rollback_stamp_run_v25(stamp_run=result.execution.stamp_run)

        self.assertFalse(preview.supported)
        self.assertValidationIssue(preview, 'rollback_ownership_ambiguous', f'stamp_run.result.managed_objects.optical_lanes.{lane.pk}')

    def test_rollback_blocks_downstream_dependencies_outside_manifest(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        result = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Dependency rollback proof',
            fabric_slug='dependency-rollback-proof',
        )
        lane = OpticalLane.objects.filter(fabric=result.execution.fabric).first()
        SuppressionRule.objects.create(
            fabric=result.execution.fabric,
            optical_lane=lane,
            status='active',
            reason='Operator-owned dependency',
        )

        preview = rollback_stamp_run_v25(stamp_run=result.execution.stamp_run)

        self.assertFalse(preview.supported)
        self.assertValidationIssue(preview, 'rollback_downstream_dependency', f'netbox_plant_graph.suppressionrule.{SuppressionRule.objects.first().pk}')

    def test_retry_classification_distinguishes_completed_retryable_and_blocked(self):
        fixture = ensure_roce_4plane_shuffle_architecture()
        completed = apply_stamp_template_v25(
            template=fixture.stamp_template,
            fabric_name='Retry completed proof',
            fabric_slug='retry-completed-proof',
        ).execution.stamp_run
        failed = StampRun.objects.create(
            template=fixture.stamp_template,
            status='failed',
            parameters={
                'fabric_name': 'Retry failed proof',
                'fabric_slug': 'retry-failed-proof',
                'template_slug': fixture.stamp_template.slug,
                'executor': 'roce_4plane_mini_proof',
            },
            result={},
        )
        blocked = StampRun.objects.create(
            template=None,
            status='failed',
            parameters={},
            result={},
        )

        self.assertEqual(classify_stamp_retry_v25(stamp_run=completed).classification, 'already-converged')
        retryable = classify_stamp_retry_v25(stamp_run=failed)
        self.assertTrue(retryable.supported)
        self.assertEqual(retryable.classification, 'retryable')
        self.assertEqual(classify_stamp_retry_v25(stamp_run=blocked).classification, 'blocked')

    def assertValidationIssue(self, preview, code, path, severity=None):
        issues = (
            preview.issues_for_code(code)
            if hasattr(preview, 'issues_for_code')
            else tuple(issue for issue in preview.issues if issue.code == code)
        )
        self.assertTrue(issues, [str(issue) for issue in preview.issues])
        self.assertTrue(any(issue.path == path for issue in issues), [str(issue) for issue in issues])
        if severity is not None:
            self.assertTrue(
                any(issue.path == path and issue.severity == severity for issue in issues),
                [str(issue) for issue in issues],
            )

    def _managed_counts(self):
        return {
            'fabrics': Fabric.objects.count(),
            'planes': Plane.objects.count(),
            'nodes': FabricNode.objects.count(),
            'endpoints': Endpoint.objects.count(),
            'positions': ConnectorPosition.objects.count(),
            'segments': FiberSegment.objects.count(),
            'strands': FiberStrand.objects.count(),
            'terminations': StrandTermination.objects.count(),
            'transfer_maps': TransferMap.objects.count(),
            'channel_position_maps': TransportChannelPositionMap.objects.count(),
            'lanes': OpticalLane.objects.count(),
        }

    def _fabric_counts(self, fabric):
        return {
            'fabrics': Fabric.objects.count(),
            'planes': Plane.objects.filter(fabric=fabric).count(),
            'nodes': FabricNode.objects.filter(fabric=fabric).count(),
            'endpoints': Endpoint.objects.filter(fabric=fabric).count(),
            'positions': ConnectorPosition.objects.filter(endpoint__fabric=fabric).count(),
            'segments': FiberSegment.objects.filter(fabric=fabric).count(),
            'strands': FiberStrand.objects.filter(segment__fabric=fabric).count(),
            'terminations': StrandTermination.objects.filter(strand__segment__fabric=fabric).count(),
            'transfer_maps': TransferMap.objects.filter(fabric=fabric).count(),
            'channel_position_maps': TransportChannelPositionMap.objects.filter(channel__fabric=fabric).count(),
            'lanes': OpticalLane.objects.filter(fabric=fabric).count(),
        }

    def _managed_ids(self):
        return {
            'fabrics': set(Fabric.objects.values_list('pk', flat=True)),
            'planes': set(Plane.objects.values_list('pk', flat=True)),
            'nodes': set(FabricNode.objects.values_list('pk', flat=True)),
            'endpoints': set(Endpoint.objects.values_list('pk', flat=True)),
            'connector_positions': set(ConnectorPosition.objects.values_list('pk', flat=True)),
            'transport_channels': set(TransportChannel.objects.values_list('pk', flat=True)),
            'transport_channel_position_maps': set(TransportChannelPositionMap.objects.values_list('pk', flat=True)),
            'fiber_segments': set(FiberSegment.objects.values_list('pk', flat=True)),
            'cable_assemblies': set(CableAssembly.objects.values_list('pk', flat=True)),
            'fiber_strands': set(FiberStrand.objects.values_list('pk', flat=True)),
            'strand_terminations': set(StrandTermination.objects.values_list('pk', flat=True)),
            'transfer_maps': set(TransferMap.objects.values_list('pk', flat=True)),
            'optical_lanes': set(OpticalLane.objects.values_list('pk', flat=True)),
        }

    def _scope_stamp_run_to_new_managed_objects(self, stamp_run, before_ids):
        result = deepcopy(stamp_run.result)
        scoped_managed = {}
        for key, raw_ids in (result.get('managed_objects') or {}).items():
            previous_ids = before_ids.get(key, set())
            scoped_managed[key] = [
                int(primary_key)
                for primary_key in raw_ids
                if int(primary_key) not in previous_ids
            ]
        result['managed_objects'] = scoped_managed
        result['object_counts'] = {key: len(value) for key, value in scoped_managed.items()}
        stamp_run.result = result
        stamp_run.save(update_fields=['result'])
        stamp_run.refresh_from_db()
