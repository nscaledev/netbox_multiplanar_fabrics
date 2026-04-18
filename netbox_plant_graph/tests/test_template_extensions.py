from netbox_plant_graph.models import Fabric, SignalLane
from netbox_plant_graph.services.sync import rebuild_graph
from netbox_plant_graph.template_extensions import PlantGraphObjectBadges

from .topology import PlantGraphTopologyMixin


class TemplateExtensionTestCase(PlantGraphTopologyMixin):
    def test_object_badges_render_operational_links_for_core_interface(self):
        topology = self.build_direct_interface_topology()

        html = PlantGraphObjectBadges({'object': topology['interface_a']}).right_page()

        self.assertIn('Resolve Path', html)
        self.assertIn('Lane Drilldown', html)
        self.assertIn('Blast Radius', html)
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
