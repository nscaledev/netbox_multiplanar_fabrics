from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from netbox_plant_graph.models import AttachmentUnit, Fabric, FabricPlane, PlaneMembership
from netbox_plant_graph.object_registry import get_object_spec
from netbox_plant_graph.object_registry import VIEW_OBJECT_SPECS
from netbox_plant_graph.services.sync import rebuild_graph
from netbox_plant_graph import views as view_module

from .topology import PlantGraphTopologyMixin


class ViewRegistrySmokeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='view-admin',
            email='view-admin@example.com',
            password='password',
        )

    def test_view_registry_is_populated(self):
        self.assertTrue(VIEW_OBJECT_SPECS)

    def test_generated_list_views_render_successfully(self):
        self.client.force_login(self.user)

        for spec in VIEW_OBJECT_SPECS:
            url = reverse(spec.list_url_name)
            with self.subTest(spec=spec.registry_key, url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_generated_list_views_render_with_rows(self):
        self.client.force_login(self.user)

        fabric = Fabric.objects.create(name='Fabric Rows')
        plane = FabricPlane.objects.create(fabric=fabric, plane_number=1)
        PlaneMembership.objects.create(
            plane=plane,
            member_type=ContentType.objects.get_for_model(Fabric),
            member_id=fabric.pk,
            membership_role='native',
        )

        urls = [
            reverse('plugins:netbox_plant_graph:fabric_list'),
            reverse('plugins:netbox_plant_graph:plane-membership_list'),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_read_only_list_views_do_not_render_broken_add_links(self):
        self.client.force_login(self.user)

        for registry_key in ('signallane', 'fineedge', 'lanemap', 'auditfinding'):
            spec = get_object_spec(registry_key)
            view_class = getattr(view_module, spec.view.list_class_name)
            url = reverse(spec.list_url_name)

            with self.subTest(spec=registry_key, url=url):
                self.assertFalse(spec.view.supports_create)
                self.assertNotIn('AddObject', {action.__name__ for action in view_class.actions})
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b'href="None', response.content)


class OperationalViewIntegrationTestCase(PlantGraphTopologyMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_superuser(
            username='operational-view-admin',
            email='operational-view-admin@example.com',
            password='password',
        )

    def test_graph_overview_renders_fabric_counts(self):
        self.build_multiplane_shuffle_topology()
        fabric = Fabric.objects.create(name='Fabric Overview')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:graph_overview'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Graph Overview')
        self.assertContains(response, 'Fabric Overview')
        self.assertContains(response, 'Attachment Units')
        self.assertContains(response, 'Signal Lanes')

    def test_path_resolver_view_renders_resolved_path(self):
        topology = self.build_passthrough_topology()
        fabric = Fabric.objects.create(name='Fabric Path View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:path_resolver'), {
            'source_registry_key': 'interface',
            'source_id': topology['interface_a'].pk,
            'destination_registry_key': 'interface',
            'destination_id': topology['interface_b'].pk,
            'resolution': 'attachment_unit',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Path Found')
        self.assertContains(response, 'Interface A')
        self.assertContains(response, 'Interface B')
        self.assertContains(response, 'Transfer Maps Crossed')
        attachment_unit = AttachmentUnit.objects.get(
            source_type=ContentType.objects.get_for_model(topology['interface_a'], for_concrete_model=False),
            source_id=topology['interface_a'].pk,
        )
        self.assertContains(response, f'href="{attachment_unit.get_absolute_url()}"', html=False)

    def test_plane_audit_view_renders_findings(self):
        self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Audit View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Plane Audit')
        self.assertContains(response, 'missing_plane_membership')
        self.assertContains(response, 'plane_underpopulated')

    def test_blast_radius_view_renders_impacted_objects(self):
        topology = self.build_direct_interface_topology()
        fabric = Fabric.objects.create(name='Fabric Blast Radius View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:blast_radius'), {
            'target_registry_key': 'interface',
            'target_id': topology['interface_a'].pk,
            'resolution': 'attachment_unit',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Blast Radius')
        self.assertContains(response, 'Interface A')
        self.assertContains(response, 'Interface B')

    def test_plane_audit_view_links_missing_port_mapping_objects(self):
        topology = self.build_passthrough_without_port_mapping_topology()
        fabric = Fabric.objects.create(name='Fabric Audit Missing Port Mapping View')
        rebuild_graph(scope={'fabric': fabric})
        self.client.force_login(self.user)

        response = self.client.get(reverse('plugins:netbox_plant_graph:plane_audit'), {'fabric_id': fabric.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'missing_port_mapping')
        self.assertContains(response, f'href="{topology["front_port"].get_absolute_url()}"', html=False)
        self.assertContains(response, f'href="{topology["rear_port"].get_absolute_url()}"', html=False)
