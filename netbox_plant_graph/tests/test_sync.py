from django.contrib.contenttypes.models import ContentType

from dcim.models import Site

from netbox_plant_graph.models import AttachmentUnit, CoarseEdge, Fabric, FineEdge, LaneMap, PlaneMembership, PlantNode, SignalLane, TerminationPoint, TransferMap
from netbox_plant_graph.services.sync import rebuild_graph

from .topology import PlantGraphTopologyMixin


class GraphSyncIntegrationTestCase(PlantGraphTopologyMixin):
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
        self.assertEqual(result['fine_edges'], 12)

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
            32,
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
