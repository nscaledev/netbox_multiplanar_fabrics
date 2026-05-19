from dcim.models import Device, DeviceBay, DeviceRole, DeviceType, FrontPort, Manufacturer, RearPort

from netbox_plant_graph.models import Fabric, SignalLane
from netbox_plant_graph.services.sync import rebuild_graph
from netbox_plant_graph.template_extensions import MadisonShuffleContainmentTree, PlantGraphObjectBadges

from .topology import PlantGraphTopologyMixin


class TemplateExtensionTestCase(PlantGraphTopologyMixin):
    def test_object_badges_render_operational_links_for_core_interface(self):
        topology = self.build_direct_interface_topology()

        html = PlantGraphObjectBadges({'object': topology['interface_a']}).right_page()

        self.assertIn('Resolve Path', html)
        self.assertIn('Lane Drilldown', html)
        self.assertIn('Physical Cable Blast Radius', html)
        self.assertIn('source_registry_key=interface', html)
        self.assertIn(f'source_id={topology["interface_a"].pk}', html)

    def test_object_badges_render_signal_lane_links_for_signal_lane_objects(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Template Extension')
        rebuild_graph(scope={'fabric': fabric})
        signal_lane = SignalLane.objects.filter(
            attachment_unit__termination_point__plant_node__fabric=fabric
        ).first()
        self.assertIsNotNone(signal_lane)

        html = PlantGraphObjectBadges({'object': signal_lane}).right_page()

        self.assertIn('Signal-Lane Path', html)
        self.assertIn('Signal-Lane Radius', html)
        self.assertIn('source_registry_key=signallane', html)
        self.assertIn('resolution=signal_lane', html)

    def test_madison_shuffle_containment_tree_renders_box_tray_group_cassette_hierarchy(self):
        manufacturer = Manufacturer.objects.create(name='Shuffle Mfr', slug='shuffle-mfr')
        role = DeviceRole.objects.create(name='Shuffle Role', slug='shuffle-role')
        box_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='3-Tray 18-Cassette Shuffle Box',
            slug='shuffle-box-3tray-18cassette',
            subdevice_role='parent',
        )
        cassette_type = DeviceType.objects.create(
            manufacturer=manufacturer,
            model='2x2 MPO Shuffle Cassette',
            slug='shuffle-cassette-2x2-mpo',
            subdevice_role='child',
        )
        box = Device.objects.create(site=self.site, device_type=box_type, role=role, name='mad1-a01-shuffle-box-01')
        cassette = Device.objects.create(
            site=self.site,
            device_type=cassette_type,
            role=role,
            name='mad1-a01-shuffle-cassette-1.1',
        )

        DeviceBay.objects.create(device=box, name='cassette-1.1', installed_device=cassette)
        DeviceBay.objects.create(device=box, name='cassette-1.2')
        DeviceBay.objects.create(device=box, name='cassette-2.1')
        DeviceBay.objects.create(device=box, name='cassette-3.1')
        for index in range(1, 3):
            rear_port = RearPort.objects.create(device=cassette, name=f'rear-{index}', type='mpo', positions=1)
            FrontPort.objects.create(
                device=cassette,
                name=f'front-{index}',
                type='mpo',
                rear_port=rear_port,
                rear_port_position=1,
            )

        html = MadisonShuffleContainmentTree({'object': box}).left_page()

        self.assertIn('Shuffle Containment', html)
        self.assertIn('Viewing box:', html)
        self.assertIn('mad1-a01-shuffle-box-01', html)
        self.assertIn('Tray 1', html)
        self.assertIn('cassette-1.1', html)
        self.assertIn('mad1-a01-shuffle-cassette-1.1', html)
        self.assertIn('1/3 tray groups populated', html)
        self.assertIn('1/4 cassettes', html)
        self.assertIn('front MPO 2 / linked 0', html)
        self.assertIn('rear MPO 2 / linked 0', html)

        cassette_html = MadisonShuffleContainmentTree({'object': cassette}).left_page()
        self.assertIn('Viewing cassette:', cassette_html)
        self.assertIn('mad1-a01-shuffle-box-01', cassette_html)
        self.assertIn('mad1-a01-shuffle-cassette-1.1', cassette_html)

    def test_madison_shuffle_containment_tree_skips_non_shuffle_box_devices(self):
        html = MadisonShuffleContainmentTree({'object': self.device}).left_page()

        self.assertEqual(html, '')
