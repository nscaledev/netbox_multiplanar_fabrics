from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import override_settings

from dcim.models import FrontPort, Interface, RearPort, Site

try:
    from dcim.choices import CableProfileChoices  # noqa: F401
    HAS_NATIVE_CABLE_PROFILES = True
except ImportError:
    HAS_NATIVE_CABLE_PROFILES = False

from netbox_plant_graph.jobs import BlastRadiusJob, FullGraphRebuildJob, IncrementalRefreshJob
from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, Fabric, FineEdge, GraphBuildRun, LaneMap, PlaneMembership, PlantNode, SignalLane, TerminationPoint, TransferMap, UnresolvedStateObservation, UnresolvedStateSummary
from netbox_plant_graph.port_mapping_compat import PortMapping
from netbox_plant_graph.services.graph.unresolved_candidates import collect_unresolved_candidates
from netbox_plant_graph.services.sync import rebuild_graph
from netbox_plant_graph.services.sync.graph_builder import build_graph
from netbox_plant_graph.services.sync.extractor import extract_source_bundle
from netbox_plant_graph.services.sync.transformer import GraphInputs

from .topology import PlantGraphTopologyMixin


class GraphSyncIntegrationTestCase(PlantGraphTopologyMixin):
    def test_extract_source_bundle_accepts_direct_fabric_scope_and_respects_site_scope(self):
        local_topology = self.build_multiplane_shuffle_topology(site=self.site)
        remote_site = Site.objects.create(name='Remote Extract Scope Site', slug='remote-extract-scope-site')
        remote_topology = self.build_multiplane_shuffle_topology(site=remote_site)
        fabric = Fabric.objects.create(name='Fabric Extract Direct Scope', scope_site=self.site)

        bundle = extract_source_bundle(scope=fabric)

        self.assertEqual(bundle.fabric, fabric)
        self.assertEqual(
            {device.pk for device in bundle.devices},
            {
                local_topology['host_device'].pk,
                local_topology['shuffle_device'].pk,
                local_topology['leaf_device'].pk,
            },
        )
        self.assertNotIn(remote_topology['host_device'].pk, {device.pk for device in bundle.devices})
        self.assertNotIn(remote_topology['shuffle_device'].pk, {device.pk for device in bundle.devices})
        self.assertNotIn(remote_topology['leaf_device'].pk, {device.pk for device in bundle.devices})

    def test_extract_source_bundle_filters_cables_without_full_table_scan(self):
        local_topology = self.build_multiplane_shuffle_topology(site=self.site)
        remote_site = Site.objects.create(name='Remote Cable Extract Site', slug='remote-cable-extract-site')
        remote_topology = self.build_multiplane_shuffle_topology(site=remote_site)
        fabric = Fabric.objects.create(name='Fabric Extract Cable Scope', scope_site=self.site)

        with patch(
            'netbox_plant_graph.services.sync.extractor.Cable.objects.all',
            side_effect=AssertionError('extractor must not scan Cable.objects.all()'),
        ):
            bundle = extract_source_bundle(scope=fabric)

        self.assertEqual(
            {cable.pk for cable in bundle.cables},
            {
                local_topology['host_cable'].pk,
                local_topology['leaf_cable'].pk,
            },
        )
        self.assertNotIn(remote_topology['host_cable'].pk, {cable.pk for cable in bundle.cables})
        self.assertNotIn(remote_topology['leaf_cable'].pk, {cable.pk for cable in bundle.cables})

    def test_extract_source_bundle_filters_cable_paths_without_full_table_scan(self):
        local_topology = self.build_multiplane_shuffle_topology(site=self.site)
        remote_site = Site.objects.create(name='Remote Path Extract Site', slug='remote-path-extract-site')
        remote_topology = self.build_multiplane_shuffle_topology(site=remote_site)
        fabric = Fabric.objects.create(name='Fabric Extract Path Scope', scope_site=self.site)

        def path_ids_for_device_ids(device_ids):
            path_ids = set(
                Interface.objects.filter(device_id__in=device_ids, _path__isnull=False).values_list('_path_id', flat=True)
            )
            if hasattr(FrontPort, '_path'):
                path_ids |= set(
                    FrontPort.objects.filter(device_id__in=device_ids, _path__isnull=False).values_list('_path_id', flat=True)
                )
            if hasattr(RearPort, '_path'):
                path_ids |= set(
                    RearPort.objects.filter(device_id__in=device_ids, _path__isnull=False).values_list('_path_id', flat=True)
                )
            return path_ids

        local_device_ids = {
            local_topology['host_device'].pk,
            local_topology['shuffle_device'].pk,
            local_topology['leaf_device'].pk,
        }
        remote_device_ids = {
            remote_topology['host_device'].pk,
            remote_topology['shuffle_device'].pk,
            remote_topology['leaf_device'].pk,
        }

        with patch(
            'netbox_plant_graph.services.sync.extractor.CablePath.objects.all',
            side_effect=AssertionError('extractor must not scan CablePath.objects.all()'),
        ):
            bundle = extract_source_bundle(scope=fabric)

        self.assertEqual({path.pk for path in bundle.cable_paths}, path_ids_for_device_ids(local_device_ids))
        self.assertTrue(path_ids_for_device_ids(remote_device_ids).isdisjoint({path.pk for path in bundle.cable_paths}))

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_rebuild_graph_persists_unresolved_summaries_without_duplicates_across_rebuilds(self):
        self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Unresolved Stable')

        first_result = rebuild_graph(scope={'fabric': fabric})
        first_summaries = list(
            UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').order_by('fingerprint')
        )

        self.assertEqual(len(first_summaries), 2)
        self.assertEqual(UnresolvedStateObservation.objects.filter(summary__fabric=fabric).count(), 2)
        self.assertEqual(GraphBuildRun.objects.get(pk=first_result['build_run']).metadata['summary_sync']['created_count'], 2)

        first_ids = [summary.pk for summary in first_summaries]
        first_fingerprints = [summary.fingerprint for summary in first_summaries]
        first_seen_build_ids = [summary.first_seen_build_id for summary in first_summaries]

        second_result = rebuild_graph(scope={'fabric': fabric})
        second_summaries = list(
            UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').order_by('fingerprint')
        )

        self.assertEqual([summary.pk for summary in second_summaries], first_ids)
        self.assertEqual([summary.fingerprint for summary in second_summaries], first_fingerprints)
        self.assertEqual([summary.first_seen_build_id for summary in second_summaries], first_seen_build_ids)
        self.assertTrue(all(summary.last_seen_build_id == second_result['build_run'] for summary in second_summaries))
        self.assertEqual(UnresolvedStateObservation.objects.filter(summary__fabric=fabric).count(), 4)
        self.assertEqual(GraphBuildRun.objects.get(pk=second_result['build_run']).metadata['summary_sync']['updated_count'], 2)

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_rebuild_graph_resolves_and_reopens_unresolved_summaries(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Unresolved Lifecycle')

        first_result = rebuild_graph(scope={'fabric': fabric})
        first_summary_ids = list(
            UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping')
            .order_by('fingerprint')
            .values_list('pk', flat=True)
        )

        PortMapping.objects.create(
            device=self.device,
            front_port=topology['front_port'],
            front_port_position=1,
            rear_port=topology['rear_port'],
            rear_port_position=1,
        )

        second_result = rebuild_graph(scope={'fabric': fabric})
        resolved_summaries = list(
            UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').order_by('fingerprint')
        )

        self.assertEqual(first_summary_ids, [summary.pk for summary in resolved_summaries])
        self.assertTrue(all(not summary.active for summary in resolved_summaries))
        self.assertTrue(all(summary.resolved_at is not None for summary in resolved_summaries))
        self.assertEqual(GraphBuildRun.objects.get(pk=second_result['build_run']).metadata['summary_sync']['resolved_count'], 2)

        PortMapping.objects.filter(device=self.device, front_port=topology['front_port']).delete()

        third_result = rebuild_graph(scope={'fabric': fabric})
        reopened_summaries = list(
            UnresolvedStateSummary.objects.filter(fabric=fabric, cause_code='missing_port_mapping').order_by('fingerprint')
        )

        self.assertEqual(first_summary_ids, [summary.pk for summary in reopened_summaries])
        self.assertTrue(all(summary.active for summary in reopened_summaries))
        self.assertTrue(all(summary.resolved_at is None for summary in reopened_summaries))
        self.assertTrue(all(summary.last_seen_build_id == third_result['build_run'] for summary in reopened_summaries))
        self.assertEqual(UnresolvedStateObservation.objects.filter(summary__fabric=fabric).count(), 4)
        self.assertEqual(GraphBuildRun.objects.get(pk=third_result['build_run']).metadata['summary_sync']['reopened_count'], 2)

    @override_settings(PLUGINS_CONFIG={'netbox_plant_graph': {'persist_unresolved_summaries': True}})
    def test_incremental_site_scope_does_not_resolve_full_fabric_unresolved_summaries(self):
        self.build_profile_breakout_without_child_interfaces_topology()
        other_site = Site.objects.create(name='Scoped Incremental Site', slug='scoped-incremental-site')
        other_topology = self.build_multiplane_shuffle_topology(site=other_site)
        other_topology['host_device'].name = 'GPU Host Incremental Scope'
        other_topology['host_device'].save()
        other_topology['shuffle_device'].name = 'Shuffle Module Incremental Scope'
        other_topology['shuffle_device'].save()
        other_topology['leaf_device'].name = 'Leaf Switch Incremental Scope'
        other_topology['leaf_device'].save()
        fabric = Fabric.objects.create(name='Fabric Scoped Incremental Guard')

        full_result = rebuild_graph(scope={'fabric': fabric})
        active_summaries = list(UnresolvedStateSummary.objects.filter(fabric=fabric, active=True).order_by('pk'))

        self.assertTrue(active_summaries)
        self.assertTrue(all(summary.last_seen_build_id == full_result['build_run'] for summary in active_summaries))

        partial_result = rebuild_graph(
            scope={'fabric': fabric, 'site': other_site},
            trigger_mode='incremental',
        )
        partial_run = GraphBuildRun.objects.get(pk=partial_result['build_run'])

        refreshed_summaries = list(
            UnresolvedStateSummary.objects.filter(fabric=fabric, pk__in=[summary.pk for summary in active_summaries]).order_by('pk')
        )

        self.assertEqual(partial_run.metadata['comparable_scope'], 'partial_scope')
        self.assertEqual(partial_run.metadata['summary_sync']['status'], 'skipped_partial_scope')
        self.assertTrue(all(summary.active for summary in refreshed_summaries))
        self.assertTrue(all(summary.last_seen_build_id == full_result['build_run'] for summary in refreshed_summaries))

    def test_collect_unresolved_candidates_normalizes_profile_reasons(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Profile Reasons')
        rebuild_graph(scope={'fabric': fabric})

        with patch(
            'netbox_plant_graph.services.graph.unresolved_candidates._profile_mapping_diagnostics',
            return_value={
                'matched_positions': 0,
                'unresolved_positions': (
                    {
                        'cable_end': 'A',
                        'termination': topology['interface_a'],
                        'position': 1,
                        'reason': 'profile_error',
                        'detail': 'broken map',
                    },
                    {
                        'cable_end': 'A',
                        'termination': topology['interface_a'],
                        'position': 2,
                        'reason': 'profile_returned_none',
                        'detail': '',
                    },
                    {
                        'cable_end': 'A',
                        'termination': topology['interface_a'],
                        'position': 3,
                        'reason': 'missing_peer_position',
                        'detail': 'front:3',
                    },
                ),
            },
        ), patch(
            'netbox_plant_graph.services.graph.unresolved_candidates._cable_requires_explicit_profile',
            return_value=False,
        ):
            candidates = collect_unresolved_candidates(fabric=fabric)

        cause_codes = {
            candidate.cause_code
            for candidate in candidates
            if candidate.scope_object == topology['cable']
        }
        self.assertEqual(cause_codes, {'profile_error', 'profile_returned_none', 'missing_peer_position'})

    def test_rebuild_graph_expands_plugin_breakout_profile_from_cable_custom_field(self):
        topology = self.build_profile_breakout_with_partial_child_interfaces_topology(child_count=2)
        fabric = Fabric.objects.create(name='Fabric Sync Plugin Breakout CF')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['coarse_edges'], 2)
        self.assertEqual(result['fine_edges'], 2)
        self.assertEqual(
            (topology['host_cable'].custom_field_data or {}).get('mpf_breakout_profile'),
            topology['breakout_profile'].pk,
        )

    def test_rebuild_graph_persists_graph_build_run_with_stats(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Build Runs')

        result = rebuild_graph(scope={'fabric': fabric})

        build_run = GraphBuildRun.objects.get(pk=result['build_run'])
        self.assertEqual(build_run.fabric, fabric)
        self.assertEqual(build_run.scope_label, fabric.name)
        self.assertEqual(build_run.trigger_mode, 'manual')
        self.assertEqual(build_run.status, 'completed')
        self.assertEqual(build_run.stats['nodes'], result['nodes'])
        self.assertEqual(build_run.stats['coarse_edges'], result['coarse_edges'])
        self.assertNotIn('graph_revision', build_run.stats)
        self.assertEqual(build_run.metadata['graph_revision'], result['graph_revision'])

    def test_rebuild_graph_marks_graph_build_run_failed_on_build_error(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Build Failure')

        with patch('netbox_plant_graph.services.sync.rebuilder.build_graph', side_effect=RuntimeError('sync exploded')):
            with self.assertRaisesRegex(RuntimeError, 'sync exploded'):
                rebuild_graph(scope={'fabric': fabric})

        build_run = GraphBuildRun.objects.get(fabric=fabric)
        self.assertEqual(build_run.status, 'failed')
        self.assertEqual(build_run.metadata['failed_stage'], 'build')
        self.assertEqual(build_run.metadata['error_class'], 'RuntimeError')
        self.assertEqual(build_run.metadata['error'], 'sync exploded')

    def test_jobs_pass_build_run_trigger_modes_to_rebuilder(self):
        with patch('netbox_plant_graph.jobs.rebuild_graph', return_value={'ok': True}) as rebuild:
            result = FullGraphRebuildJob.run(None)

        self.assertEqual(result, {'ok': True})
        rebuild.assert_called_once_with(trigger_mode='job')

        with patch('netbox_plant_graph.jobs.rebuild_graph', return_value={'ok': True}) as rebuild:
            result = IncrementalRefreshJob.run(None, scope={'fabric': 'fabric-scope'})

        self.assertEqual(result, {'ok': True})
        rebuild.assert_called_once_with(scope={'fabric': 'fabric-scope'}, trigger_mode='incremental')

    def test_blast_radius_job_returns_service_result(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Blast Radius Job')
        rebuild_graph(scope={'fabric': fabric})
        attachment = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['interface_a'], for_concrete_model=False),
            source_id=topology['interface_a'].pk,
        )

        result = BlastRadiusJob.run(
            None,
            target_type='attachmentunit',
            target_id=attachment.pk,
            resolution='attachment_unit',
        )

        self.assertEqual(result['resolution'], 'attachment_unit')
        self.assertEqual(result['target'], attachment.pk)
        self.assertTrue(any(item['pk'] == attachment.pk for item in result['impacted_objects']))

    def test_build_graph_warns_when_plane_memberships_reference_missing_fabric_planes(self):
        fabric = Fabric.objects.create(name='Fabric Missing Plane Warning')
        result = build_graph(
            GraphInputs(
                fabrics=(
                    {
                        'instance': fabric,
                        'name': fabric.name,
                        'description': '',
                        'expected_plane_count': 4,
                        'tier_depth': 2,
                        'disjointness_policy': 'full',
                        'metadata': {},
                        'scope_site': None,
                        'scope_location': None,
                    },
                ),
                plane_numbers=(),
                plane_memberships=(
                    {
                        'plane_number': 7,
                        'member_key': 'missing-member',
                        'membership_role': 'native',
                        'metadata': {},
                    },
                ),
            )
        )

        self.assertIn(
            {'code': 'missing_fabric_plane_records', 'plane_numbers': [7]},
            result.get('warnings', []),
        )

    def test_build_graph_warns_when_attachment_units_have_no_plane_memberships(self):
        fabric = Fabric.objects.create(name='Fabric Zero Membership Warning')
        interface = Interface.objects.create(device=self.device, name='warn-plane-membership')

        result = build_graph(
            GraphInputs(
                fabrics=(
                    {
                        'instance': fabric,
                        'name': fabric.name,
                        'description': '',
                        'expected_plane_count': 4,
                        'tier_depth': 2,
                        'disjointness_policy': 'full',
                        'metadata': {},
                        'scope_site': None,
                        'scope_location': None,
                    },
                ),
                nodes=(
                    {
                        'key': 'node:device',
                        'name': self.device.name,
                        'node_type': 'device',
                        'role': self.device.role.slug,
                        'status': getattr(self.device.status, 'value', self.device.status),
                        'source': self.device,
                        'tenant': getattr(self.device, 'tenant', None),
                        'location': self.device.site,
                        'metadata': {'device_type': self.device.device_type.model, 'tier_level': None},
                    },
                ),
                terminations=(
                    {
                        'key': 'termination:interface',
                        'node_key': 'node:device',
                        'name': interface.name,
                        'tp_type': 'interface',
                        'connector_type': '',
                        'channel_capacity': 1,
                        'speed_gbps': 800,
                        'source': interface,
                        'metadata': {},
                    },
                ),
                attachment_units=(
                    {
                        'key': 'attachment:interface',
                        'termination_key': 'termination:interface',
                        'name': interface.name,
                        'ordinal': 0,
                        'unit_type': 'child_interface',
                        'speed_gbps': 800,
                        'topology_role': '',
                        'active': True,
                        'source': interface,
                        'metadata': {},
                    },
                ),
            )
        )

        self.assertIn({'code': 'zero_plane_memberships'}, result.get('warnings', []))

    def test_rebuild_graph_materializes_direct_interface_paths(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Direct')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['nodes'], 2)
        self.assertEqual(result['terminations'], 2)
        self.assertEqual(result['attachment_units'], 2)
        self.assertEqual(result['coarse_edges'], 1)
        self.assertEqual(result['fine_edges'], 1)
        self.assertEqual(result['transfer_maps'], 0)

        self.assertEqual(PlantNode.objects.filter(fabric=fabric).count(), 2)
        self.assertEqual(TerminationPoint.objects.filter(plant_node__fabric=fabric).count(), 2)
        self.assertEqual(AttachmentUnit.objects.filter(termination_point__plant_node__fabric=fabric).count(), 2)
        self.assertEqual(CoarseEdge.objects.filter(a_tp__plant_node__fabric=fabric).count(), 1)
        self.assertEqual(FineEdge.objects.filter(a_au__termination_point__plant_node__fabric=fabric).count(), 1)

        interface_type = ContentType.objects.get_for_model(topology['interface_a'], for_concrete_model=False)
        self.assertTrue(
            AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric,
                source_type=interface_type,
                source_id=topology['interface_a'].pk,
            ).exists()
        )

    def test_rebuild_graph_materializes_passthrough_maps(self):
        self.build_passthrough_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Passthrough')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['nodes'], 2)
        self.assertEqual(result['coarse_edges'], 3)
        self.assertEqual(result['fine_edges'], 5)
        self.assertEqual(result['transfer_maps'], 2)

        self.assertEqual(TransferMap.objects.filter(owner_node__fabric=fabric).count(), 2)
        self.assertEqual(
            FineEdge.objects.filter(
                a_au__termination_point__plant_node__fabric=fabric,
                edge_type='passthrough_map',
            ).count(),
            2,
        )

    def test_rebuild_graph_maps_channelized_child_interfaces_through_shuffle_module(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Multiplane')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['nodes'], 3)
        self.assertEqual(result['terminations'], 10)
        self.assertEqual(result['coarse_edges'], 8)
        self.assertEqual(result['transfer_maps'], 4)
        self.assertEqual(result['fine_edges'], 12 if HAS_NATIVE_CABLE_PROFILES else 18)

        self.assertEqual(PlantNode.objects.filter(fabric=fabric).count(), 3)
        self.assertEqual(TerminationPoint.objects.filter(plant_node__fabric=fabric).count(), 10)
        self.assertEqual(CoarseEdge.objects.filter(a_tp__plant_node__fabric=fabric).count(), 8)
        self.assertEqual(TransferMap.objects.filter(owner_node__fabric=fabric).count(), 4)
        self.assertEqual(
            FineEdge.objects.filter(
                a_au__termination_point__plant_node__fabric=fabric,
                edge_type='derived_cable_segment',
            ).count(),
            8,
        )
        self.assertEqual(
            FineEdge.objects.filter(
                a_au__termination_point__plant_node__fabric=fabric,
                edge_type='passthrough_map',
            ).count(),
            4,
        )

        child_interface_type = ContentType.objects.get_for_model(topology['host_children'][2], for_concrete_model=False)
        host_plane_two_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            source_type=child_interface_type,
            source_id=topology['host_children'][2].pk,
        )
        shuffle_front_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][1], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][1].pk,
        )
        shuffle_rear_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_rear_ports'][2], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_rear_ports'][2].pk,
        )

        self.assertTrue(
            FineEdge.objects.filter(
                granularity='attachment_unit',
                a_au=host_plane_two_attachment,
                b_au=shuffle_front_attachment,
                derived_from_profile=True,
            ).exists()
            or FineEdge.objects.filter(
                granularity='attachment_unit',
                a_au=shuffle_front_attachment,
                b_au=host_plane_two_attachment,
                derived_from_profile=True,
            ).exists()
        )
        self.assertTrue(
            TransferMap.objects.filter(
                owner_node__fabric=fabric,
                src_attachment_unit=shuffle_front_attachment,
                dst_attachment_unit=shuffle_rear_attachment,
            ).exists()
            or TransferMap.objects.filter(
                owner_node__fabric=fabric,
                src_attachment_unit=shuffle_rear_attachment,
                dst_attachment_unit=shuffle_front_attachment,
            ).exists()
        )

    def test_rebuild_graph_materializes_signal_lanes_for_multiplane_topology(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Signal Lanes')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['signal_lanes'], 64)
        self.assertEqual(result['lane_maps'], 16)
        self.assertEqual(
            SignalLane.objects.filter(attachment_unit__termination_point__plant_node__fabric=fabric).count(),
            64,
        )
        self.assertEqual(
            FineEdge.objects.filter(
                granularity='signal_lane',
                a_lane__attachment_unit__termination_point__plant_node__fabric=fabric,
            ).count(),
            32 if HAS_NATIVE_CABLE_PROFILES else 56,
        )
        self.assertEqual(
            LaneMap.objects.filter(owner_node__fabric=fabric).count(),
            16,
        )

        host_plane_two_attachment = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_children'][2], for_concrete_model=False),
            source_id=topology['host_children'][2].pk,
        )
        shuffle_front_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][1], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][1].pk,
        )
        self.assertEqual(host_plane_two_attachment.signal_lanes.count(), 4)
        self.assertEqual(shuffle_front_attachment.signal_lanes.count(), 4)

    def test_rebuild_graph_skips_ambiguous_blank_profile_fanout_mapping(self):
        topology = self.build_blank_profile_breakout_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Missing Profile')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['coarse_edges'], 0)
        self.assertEqual(result['fine_edges'], 0)

        host_plane_two_attachment = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_children'][2], for_concrete_model=False),
            source_id=topology['host_children'][2].pk,
        )
        shuffle_front_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][1], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][1].pk,
        )

        self.assertFalse(
            FineEdge.objects.filter(
                granularity='attachment_unit',
                a_au=host_plane_two_attachment,
                b_au=shuffle_front_attachment,
            ).exists()
            or FineEdge.objects.filter(
                granularity='attachment_unit',
                a_au=shuffle_front_attachment,
                b_au=host_plane_two_attachment,
            ).exists()
        )

    def test_rebuild_graph_skips_profile_breakout_without_child_interfaces(self):
        topology = self.build_profile_breakout_without_child_interfaces_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Missing Children')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['coarse_edges'], 0)
        self.assertEqual(result['fine_edges'], 0)
        self.assertTrue(
            AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric,
                source_id=topology['host_parent'].pk,
            ).exists()
        )
        shuffle_front_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][0], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][0].pk,
        )
        self.assertFalse(
            FineEdge.objects.filter(
                granularity='attachment_unit',
                a_au__termination_point__source_id=topology['host_parent'].pk,
                b_au=shuffle_front_attachment,
            ).exists()
            or FineEdge.objects.filter(
                granularity='attachment_unit',
                a_au=shuffle_front_attachment,
                b_au__termination_point__source_id=topology['host_parent'].pk,
            ).exists()
        )

    def test_rebuild_graph_materializes_only_existing_partial_child_interface_positions(self):
        topology = self.build_profile_breakout_with_partial_child_interfaces_topology(child_count=2)
        fabric = Fabric.objects.create(name='Fabric Sync Partial Children')

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['coarse_edges'], 2)
        self.assertEqual(result['fine_edges'], 2)

        host_plane_one_attachment = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_children'][1], for_concrete_model=False),
            source_id=topology['host_children'][1].pk,
        )
        host_plane_two_attachment = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_children'][2], for_concrete_model=False),
            source_id=topology['host_children'][2].pk,
        )
        shuffle_front_one_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][0], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][0].pk,
        )
        shuffle_front_three_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][2], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][2].pk,
        )

        self.assertTrue(
            FineEdge.objects.filter(granularity='attachment_unit', a_au=host_plane_one_attachment, b_au=shuffle_front_one_attachment).exists()
            or FineEdge.objects.filter(granularity='attachment_unit', a_au=shuffle_front_one_attachment, b_au=host_plane_one_attachment).exists()
        )
        self.assertFalse(
            FineEdge.objects.filter(granularity='attachment_unit', a_au=host_plane_two_attachment, b_au=shuffle_front_three_attachment).exists()
            or FineEdge.objects.filter(granularity='attachment_unit', a_au=shuffle_front_three_attachment, b_au=host_plane_two_attachment).exists()
        )

    def test_rebuild_graph_propagates_plane_membership_across_passive_attachment_units(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Sync Plane Propagation')

        rebuild_graph(scope={'fabric': fabric})

        attachment_type = ContentType.objects.get_for_model(AttachmentUnit)
        passive_attachment_ids = list(
            AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric,
                termination_point__plant_node__name='Shuffle Module',
            ).values_list('pk', flat=True)
        )
        passive_attachment_memberships = PlaneMembership.objects.filter(
            plane__fabric=fabric,
            member_type=attachment_type,
            member_id__in=passive_attachment_ids,
        )
        self.assertGreaterEqual(passive_attachment_memberships.count(), 4)

        host_plane_two_attachment = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['host_children'][2], for_concrete_model=False),
            source_id=topology['host_children'][2].pk,
        )
        propagated_attachment = AttachmentUnit.objects.get(
            termination_point__plant_node__fabric=fabric,
            termination_point__source_type=ContentType.objects.get_for_model(topology['shuffle_front_ports'][1], for_concrete_model=False),
            termination_point__source_id=topology['shuffle_front_ports'][1].pk,
        )
        plane_numbers = set(
            PlaneMembership.objects.filter(member_id__in=[host_plane_two_attachment.pk, propagated_attachment.pk])
            .values_list('plane__plane_number', flat=True)
        )
        self.assertIn(2, plane_numbers)

    def test_rebuild_graph_respects_fabric_site_scope(self):
        local_topology = self.build_multiplane_shuffle_topology(site=self.site)
        remote_site = Site.objects.create(name='Remote Site', slug='remote-site')
        self.build_multiplane_shuffle_topology(site=remote_site)
        fabric = Fabric.objects.create(name='Fabric Site Scoped', scope_site=self.site)

        result = rebuild_graph(scope={'fabric': fabric})

        self.assertEqual(result['nodes'], 3)
        self.assertEqual(PlantNode.objects.filter(fabric=fabric).count(), 3)
        self.assertEqual(
            set(PlantNode.objects.filter(fabric=fabric).values_list('source_id', flat=True)),
            {
                local_topology['host_device'].pk,
                local_topology['shuffle_device'].pk,
                local_topology['leaf_device'].pk,
            },
        )

    def test_rebuild_graph_accepts_direct_fabric_scope_and_respects_site_scope(self):
        local_topology = self.build_multiplane_shuffle_topology(site=self.site)
        remote_site = Site.objects.create(name='Remote Direct Scope Site', slug='remote-direct-scope-site')
        self.build_multiplane_shuffle_topology(site=remote_site)
        fabric = Fabric.objects.create(name='Fabric Direct Scope Rebuild', scope_site=self.site)

        result = rebuild_graph(scope=fabric)

        self.assertEqual(result['nodes'], 3)
        self.assertEqual(PlantNode.objects.filter(fabric=fabric).count(), 3)
        self.assertEqual(
            set(PlantNode.objects.filter(fabric=fabric).values_list('source_id', flat=True)),
            {
                local_topology['host_device'].pk,
                local_topology['shuffle_device'].pk,
                local_topology['leaf_device'].pk,
            },
        )
