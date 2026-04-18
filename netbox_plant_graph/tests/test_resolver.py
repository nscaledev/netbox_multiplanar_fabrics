from collections import defaultdict

from netbox_plant_graph.models import AttachmentUnit, Fabric, FabricPlane
from netbox_plant_graph.services import build_lane_drilldown, compute_blast_radius, resolve_path, run_plane_audit
from netbox_plant_graph.services.sync import rebuild_graph

from .topology import PlantGraphTopologyMixin


class GraphServiceIntegrationTestCase(PlantGraphTopologyMixin):
    def test_resolve_path_traces_passthrough_graph(self):
        topology = self.build_passthrough_topology()
        fabric = Fabric.objects.create(name='Fabric Resolve')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_path(source=topology['interface_a'], destination=topology['interface_b'])

        self.assertTrue(result['path_found'])
        self.assertEqual(result['summary']['coarse_edges_crossed'], 3)
        self.assertEqual(result['summary']['transfer_maps_crossed'], 2)
        self.assertTrue(any(step.get('kind') == 'transfer_map' for step in result['path'] if isinstance(step, dict)))

    def test_compute_blast_radius_returns_connected_units(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Blast Radius')
        rebuild_graph(scope={'fabric': fabric})

        result = compute_blast_radius(target=topology['interface_a'])

        self.assertGreaterEqual(len(result['impacted_objects']), 2)
        self.assertEqual(result['impacted_paths'][0]['distance'], 0)
        self.assertIn('Interface B', {item['object']['display'] for item in result['impacted_paths']})

    def test_plane_audit_reports_missing_memberships(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding_types = {finding['finding_type'] for finding in result['findings']}

        self.assertIn('missing_plane_membership', finding_types)
        self.assertIn('plane_underpopulated', finding_types)

    def test_resolve_path_traces_multiplane_shuffle_fixture_from_child_interface(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Multiplane Resolve')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_path(
            source=topology['host_children'][2],
            destination=topology['leaf_children'][3],
        )

        self.assertTrue(result['path_found'])
        self.assertEqual(result['summary']['coarse_edges_crossed'], 2)
        self.assertEqual(result['summary']['transfer_maps_crossed'], 1)
        self.assertEqual(result['summary']['shuffle_modules_crossed'], 1)
        self.assertEqual(result['summary']['planes_touched'], [2, 3])
        path_displays = {step['display'] for step in result['path'] if isinstance(step, dict) and 'display' in step}
        self.assertIn('nic0/plane2', path_displays)
        self.assertIn('Ethernet1/1/plane3', path_displays)

    def test_resolve_path_with_plane_filter_uses_propagated_passive_memberships(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Plane Filter Resolve')
        rebuild_graph(scope={'fabric': fabric})
        plane = FabricPlane.objects.get(fabric=fabric, plane_number=2)

        result = resolve_path(
            source=topology['host_children'][2],
            destination=topology['leaf_children'][3],
            plane=plane,
        )

        self.assertTrue(result['path_found'])

    def test_resolve_path_supports_signal_lane_resolution(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Signal Lane Resolve')
        rebuild_graph(scope={'fabric': fabric})

        result = resolve_path(
            source=topology['host_children'][2],
            destination=topology['leaf_children'][3],
            resolution='signal_lane',
        )

        self.assertTrue(result['path_found'])
        self.assertEqual(result['resolution'], 'signal_lane')
        self.assertTrue(any(step.get('kind') == 'lane_map' for step in result['path'] if isinstance(step, dict)))

    def test_plane_audit_reports_cross_plane_shuffle_contamination(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Multiplane Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        finding_types = {finding['finding_type'] for finding in result['findings']}

        self.assertIn('multi_plane_attachment', finding_types)
        self.assertIn('shared_passive_artifact', finding_types)

        shuffle_attachment_names = set(
            AttachmentUnit.objects.filter(
                termination_point__plant_node__fabric=fabric,
                termination_point__plant_node__name='Shuffle Module',
            ).values_list('name', flat=True)
        )
        finding_messages = ' '.join(finding['message'] for finding in result['findings'])
        self.assertTrue(shuffle_attachment_names)
        self.assertIn('multiple planes', finding_messages)

    def test_plane_audit_reports_blank_profile_fanout_cable(self):
        topology = self.build_blank_profile_breakout_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Profile Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('missing_cable_profile', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_cable'].pk
            for finding in findings_by_type['missing_cable_profile']
        ))
        self.assertTrue(any(
            'explicit profile' in finding['message']
            for finding in findings_by_type['missing_cable_profile']
        ))

    def test_plane_audit_reports_profile_breakout_without_child_interfaces(self):
        topology = self.build_profile_breakout_without_child_interfaces_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Children Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('missing_child_interface', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_parent'].pk
            for finding in findings_by_type['missing_child_interface']
        ))
        self.assertTrue(any(
            'explicit child interfaces' in finding['message']
            for finding in findings_by_type['missing_child_interface']
        ))

    def test_plane_audit_reports_incomplete_child_interface_set(self):
        topology = self.build_profile_breakout_with_partial_child_interfaces_topology(child_count=2)
        fabric = Fabric.objects.create(name='Fabric Partial Children Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('incomplete_child_interface_set', findings_by_type)
        self.assertTrue(any(
            finding['object']['pk'] == topology['host_parent'].pk
            for finding in findings_by_type['incomplete_child_interface_set']
        ))
        self.assertTrue(any(
            finding['metadata'].get('expected_child_count') == 4 and finding['metadata'].get('actual_child_count') == 2
            for finding in findings_by_type['incomplete_child_interface_set']
        ))

    def test_plane_audit_reports_orphaned_child_interface(self):
        topology = self.build_orphaned_child_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Orphaned Child Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('orphaned_attachment_unit', findings_by_type)
        self.assertTrue(any(
            finding['object']['display'] == topology['orphan_child'].name
            for finding in findings_by_type['orphaned_attachment_unit']
        ))

    def test_plane_audit_reports_missing_port_mapping_for_cabled_passive_ports(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Missing Port Mapping Audit')
        rebuild_graph(scope={'fabric': fabric})

        result = run_plane_audit(fabric=fabric)
        findings_by_type = defaultdict(list)
        for finding in result['findings']:
            findings_by_type[finding['finding_type']].append(finding)

        self.assertIn('missing_port_mapping', findings_by_type)
        missing_objects = {finding['object']['display'] for finding in findings_by_type['missing_port_mapping']}
        self.assertIn(topology['front_port'].name, missing_objects)
        self.assertIn(topology['rear_port'].name, missing_objects)
        self.assertTrue(any(
            finding['metadata'].get('mapping_side') == 'front'
            for finding in findings_by_type['missing_port_mapping']
        ))
        self.assertTrue(any(
            finding['metadata'].get('mapping_side') == 'rear'
            for finding in findings_by_type['missing_port_mapping']
        ))

    def test_lane_drilldown_returns_lane_groups_for_core_child_interface(self):
        topology = self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Lane Drilldown')
        rebuild_graph(scope={'fabric': fabric})

        result = build_lane_drilldown(target=topology['host_children'][2])

        self.assertEqual(result['total_attachment_units'], 1)
        self.assertEqual(result['total_signal_lanes'], 4)
        self.assertEqual(result['available_lane_indexes'], [0, 1, 2, 3])
        self.assertEqual(result['attachment_units'][0]['attachment_unit']['display'], topology['host_children'][2].name)
        self.assertEqual(len(result['attachment_units'][0]['lanes']), 4)
