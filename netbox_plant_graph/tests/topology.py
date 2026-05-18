from dcim.choices import CableProfileChoices
from dcim.models import Cable, Device, FrontPort, Interface, RearPort
from dcim.tests.utils import CablePathTestCase

from netbox_plant_graph.breakout_profiles import set_plugin_breakout_profile_for_cable
from netbox_plant_graph.models import BreakoutProfile
from netbox_plant_graph.port_mapping_compat import PortMapping


class PlantGraphTopologyMixin(CablePathTestCase):
    def make_plugin_breakout_profile(self, slug='breakout-800g-4x200g'):
        return BreakoutProfile.objects.get_or_create(
            slug=slug,
            defaults={
                'name': '800G-4x200G',
                'description': 'Plugin-managed cable breakout profile',
                'parent_speed_gbps': 800,
                'child_count': 4,
                'child_speed_gbps': 200,
                'mapping_mode': 'sequential',
            },
        )[0]

    def create_peer_device(self, name='Peer Device', site=None):
        return Device.objects.create(
            site=site or self.site,
            device_type=self.device.device_type,
            role=self.device.role,
            name=name,
        )

    def build_direct_interface_topology(self):
        peer_device = self.create_peer_device()
        interface_a = Interface.objects.create(device=self.device, name='Interface A')
        interface_b = Interface.objects.create(device=peer_device, name='Interface B')

        cable = Cable(a_terminations=[interface_a], b_terminations=[interface_b])
        cable.clean()
        cable.save()

        return {
            'device_a': self.device,
            'device_b': peer_device,
            'interface_a': interface_a,
            'interface_b': interface_b,
            'cable': cable,
        }

    def build_passthrough_topology(self):
        peer_device = self.create_peer_device(name='Patch Peer')
        interface_a = Interface.objects.create(device=self.device, name='Interface A')
        interface_b = Interface.objects.create(device=peer_device, name='Interface B')
        front_port_a = FrontPort.objects.create(device=self.device, name='Front Port A')
        rear_port_a = RearPort.objects.create(device=self.device, name='Rear Port A', positions=1)
        front_port_b = FrontPort.objects.create(device=peer_device, name='Front Port B')
        rear_port_b = RearPort.objects.create(device=peer_device, name='Rear Port B', positions=1)

        PortMapping.objects.create(
            device=self.device,
            front_port=front_port_a,
            front_port_position=1,
            rear_port=rear_port_a,
            rear_port_position=1,
        )
        PortMapping.objects.create(
            device=peer_device,
            front_port=front_port_b,
            front_port_position=1,
            rear_port=rear_port_b,
            rear_port_position=1,
        )

        access_cable = Cable(a_terminations=[interface_a], b_terminations=[front_port_a])
        access_cable.clean()
        access_cable.save()

        trunk_cable = Cable(a_terminations=[rear_port_a], b_terminations=[rear_port_b])
        trunk_cable.clean()
        trunk_cable.save()

        peer_cable = Cable(a_terminations=[front_port_b], b_terminations=[interface_b])
        peer_cable.clean()
        peer_cable.save()

        return {
            'device_a': self.device,
            'device_b': peer_device,
            'interface_a': interface_a,
            'interface_b': interface_b,
            'front_port_a': front_port_a,
            'rear_port_a': rear_port_a,
            'front_port_b': front_port_b,
            'rear_port_b': rear_port_b,
        }

    def build_multiplane_shuffle_topology(
        self,
        *,
        site=None,
        host_profile=CableProfileChoices.BREAKOUT_1C4P_4C1P,
        leaf_profile=CableProfileChoices.BREAKOUT_1C4P_4C1P,
    ):
        site = site or self.site
        if site == self.site:
            host_device = self.device
            host_device.name = 'GPU Host'
            host_device.save()
        else:
            host_device = self.create_peer_device(name='GPU Host', site=site)
        shuffle_device = self.create_peer_device(name='Shuffle Module', site=site)
        leaf_device = self.create_peer_device(name='Leaf Switch', site=site)

        host_parent = Interface.objects.create(device=host_device, name='nic0', speed=800_000_000)
        host_children = {}
        for plane_number in range(1, 5):
            host_children[plane_number] = Interface.objects.create(
                device=host_device,
                parent=host_parent,
                name=f'nic0/plane{plane_number}',
                speed=200_000_000,
                custom_field_data={'fabric_plane': plane_number},
            )

        leaf_parent = Interface.objects.create(device=leaf_device, name='Ethernet1/1', speed=800_000_000)
        leaf_children = {}
        for plane_number in range(1, 5):
            leaf_children[plane_number] = Interface.objects.create(
                device=leaf_device,
                parent=leaf_parent,
                name=f'Ethernet1/1/plane{plane_number}',
                speed=200_000_000,
                custom_field_data={'fabric_plane': plane_number},
            )

        shuffle_front_ports = []
        shuffle_rear_ports = []
        for port_number in range(1, 5):
            shuffle_front_ports.append(
                FrontPort.objects.create(device=shuffle_device, name=f'front{port_number}')
            )
            shuffle_rear_ports.append(
                RearPort.objects.create(device=shuffle_device, name=f'rear{port_number}', positions=1)
            )

        shuffle_mapping = {1: 1, 2: 3, 3: 2, 4: 4}
        for front_number, rear_number in shuffle_mapping.items():
            PortMapping.objects.create(
                device=shuffle_device,
                front_port=shuffle_front_ports[front_number - 1],
                front_port_position=1,
                rear_port=shuffle_rear_ports[rear_number - 1],
                rear_port_position=1,
            )

        host_cable_kwargs = {
            'a_terminations': [host_parent],
            'b_terminations': shuffle_front_ports,
        }
        if host_profile is not None:
            host_cable_kwargs['profile'] = host_profile
        host_cable = Cable(**host_cable_kwargs)
        host_cable.clean()
        host_cable.save()

        leaf_cable_kwargs = {
            'a_terminations': [leaf_parent],
            'b_terminations': shuffle_rear_ports,
        }
        if leaf_profile is not None:
            leaf_cable_kwargs['profile'] = leaf_profile
        leaf_cable = Cable(**leaf_cable_kwargs)
        leaf_cable.clean()
        leaf_cable.save()

        return {
            'host_device': host_device,
            'shuffle_device': shuffle_device,
            'leaf_device': leaf_device,
            'host_parent': host_parent,
            'leaf_parent': leaf_parent,
            'host_children': host_children,
            'leaf_children': leaf_children,
            'shuffle_front_ports': shuffle_front_ports,
            'shuffle_rear_ports': shuffle_rear_ports,
            'shuffle_mapping': shuffle_mapping,
            'host_cable': host_cable,
            'leaf_cable': leaf_cable,
        }

    def build_blank_profile_breakout_topology(self, *, site=None):
        site = site or self.site
        if site == self.site:
            host_device = self.device
            host_device.name = 'GPU Host'
            host_device.save()
        else:
            host_device = self.create_peer_device(name='GPU Host', site=site)
        shuffle_device = self.create_peer_device(name='Shuffle Module', site=site)

        host_parent = Interface.objects.create(device=host_device, name='nic0', speed=800_000_000)
        host_children = {}
        for plane_number in range(1, 5):
            host_children[plane_number] = Interface.objects.create(
                device=host_device,
                parent=host_parent,
                name=f'nic0/plane{plane_number}',
                speed=200_000_000,
                custom_field_data={'fabric_plane': plane_number},
            )

        shuffle_front_ports = []
        for port_number in range(1, 5):
            shuffle_front_ports.append(
                FrontPort.objects.create(device=shuffle_device, name=f'front{port_number}')
            )

        host_cable = Cable(
            a_terminations=[host_parent],
            b_terminations=shuffle_front_ports,
        )
        host_cable.clean()
        host_cable.save()

        return {
            'host_device': host_device,
            'shuffle_device': shuffle_device,
            'host_parent': host_parent,
            'host_children': host_children,
            'shuffle_front_ports': shuffle_front_ports,
            'host_cable': host_cable,
        }

    def build_profile_breakout_without_child_interfaces_topology(self, *, site=None):
        site = site or self.site
        if site == self.site:
            host_device = self.device
            host_device.name = 'GPU Host'
            host_device.save()
        else:
            host_device = self.create_peer_device(name='GPU Host', site=site)
        shuffle_device = self.create_peer_device(name='Shuffle Module', site=site)

        host_parent = Interface.objects.create(device=host_device, name='nic0', speed=800_000_000)
        shuffle_front_ports = []
        for port_number in range(1, 5):
            shuffle_front_ports.append(
                FrontPort.objects.create(device=shuffle_device, name=f'front{port_number}')
            )

        host_cable = Cable(
            a_terminations=[host_parent],
            b_terminations=shuffle_front_ports,
        )
        host_cable.clean()
        host_cable.save()
        breakout_profile = self.make_plugin_breakout_profile()
        set_plugin_breakout_profile_for_cable(host_cable, breakout_profile)

        return {
            'host_device': host_device,
            'shuffle_device': shuffle_device,
            'host_parent': host_parent,
            'shuffle_front_ports': shuffle_front_ports,
            'host_cable': host_cable,
            'breakout_profile': breakout_profile,
        }

    def build_profile_breakout_with_partial_child_interfaces_topology(self, *, site=None, child_count=2):
        site = site or self.site
        if site == self.site:
            host_device = self.device
            host_device.name = 'GPU Host'
            host_device.save()
        else:
            host_device = self.create_peer_device(name='GPU Host', site=site)
        shuffle_device = self.create_peer_device(name='Shuffle Module', site=site)

        host_parent = Interface.objects.create(device=host_device, name='nic0', speed=800_000_000)
        host_children = {}
        for plane_number in range(1, child_count + 1):
            host_children[plane_number] = Interface.objects.create(
                device=host_device,
                parent=host_parent,
                name=f'nic0/plane{plane_number}',
                speed=200_000_000,
                custom_field_data={'fabric_plane': plane_number},
            )

        shuffle_front_ports = []
        for port_number in range(1, 5):
            shuffle_front_ports.append(
                FrontPort.objects.create(device=shuffle_device, name=f'front{port_number}')
            )

        host_cable = Cable(
            a_terminations=[host_parent],
            b_terminations=shuffle_front_ports,
        )
        host_cable.clean()
        host_cable.save()
        breakout_profile = self.make_plugin_breakout_profile()
        set_plugin_breakout_profile_for_cable(host_cable, breakout_profile)

        return {
            'host_device': host_device,
            'shuffle_device': shuffle_device,
            'host_parent': host_parent,
            'host_children': host_children,
            'shuffle_front_ports': shuffle_front_ports,
            'host_cable': host_cable,
            'breakout_profile': breakout_profile,
        }

    def build_orphaned_child_interface_topology(self, *, site=None):
        site = site or self.site
        if site == self.site:
            host_device = self.device
            host_device.name = 'GPU Host'
            host_device.save()
        else:
            host_device = self.create_peer_device(name='GPU Host', site=site)

        host_parent = Interface.objects.create(device=host_device, name='nic0', speed=800_000_000)
        orphan_child = Interface.objects.create(
            device=host_device,
            parent=host_parent,
            name='nic0/plane1',
            speed=200_000_000,
            custom_field_data={'fabric_plane': 1},
        )

        return {
            'host_device': host_device,
            'host_parent': host_parent,
            'orphan_child': orphan_child,
        }

    def build_passthrough_without_port_mapping_topology(self):
        peer_device = self.create_peer_device(name='Unmapped Passive Peer')
        interface_a = Interface.objects.create(device=self.device, name='Interface A')
        interface_b = Interface.objects.create(device=peer_device, name='Interface B')
        front_port = FrontPort.objects.create(device=self.device, name='Front Port A')
        rear_port = RearPort.objects.create(device=self.device, name='Rear Port A', positions=1)

        access_cable = Cable(a_terminations=[interface_a], b_terminations=[front_port])
        access_cable.clean()
        access_cable.save()

        peer_cable = Cable(a_terminations=[rear_port], b_terminations=[interface_b])
        peer_cable.clean()
        peer_cable.save()

        return {
            'device_a': self.device,
            'device_b': peer_device,
            'interface_a': interface_a,
            'interface_b': interface_b,
            'front_port': front_port,
            'rear_port': rear_port,
            'access_cable': access_cable,
            'peer_cable': peer_cable,
        }

    def build_profile_breakout_with_missing_peer_positions_topology(self, *, site=None):
        site = site or self.site
        if site == self.site:
            host_device = self.device
            host_device.name = 'GPU Host'
            host_device.save()
        else:
            host_device = self.create_peer_device(name='GPU Host', site=site)
        shuffle_device = self.create_peer_device(name='Shuffle Module', site=site)

        host_parent = Interface.objects.create(device=host_device, name='nic0', speed=800_000_000)
        host_children = {}
        for plane_number in range(1, 5):
            host_children[plane_number] = Interface.objects.create(
                device=host_device,
                parent=host_parent,
                name=f'nic0/plane{plane_number}',
                speed=200_000_000,
                custom_field_data={'fabric_plane': plane_number},
            )

        shuffle_front_ports = []
        for port_number in range(1, 4):
            shuffle_front_ports.append(
                FrontPort.objects.create(device=shuffle_device, name=f'front{port_number}')
            )

        host_cable = Cable(
            a_terminations=[host_parent],
            b_terminations=shuffle_front_ports,
        )
        host_cable.clean()
        host_cable.save()
        breakout_profile = self.make_plugin_breakout_profile()
        set_plugin_breakout_profile_for_cable(host_cable, breakout_profile)

        return {
            'host_device': host_device,
            'shuffle_device': shuffle_device,
            'host_parent': host_parent,
            'host_children': host_children,
            'shuffle_front_ports': shuffle_front_ports,
            'host_cable': host_cable,
            'breakout_profile': breakout_profile,
        }
