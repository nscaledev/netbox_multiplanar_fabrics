from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from tenancy.models import Tenant

from netbox_plant_graph.models import Fabric, FabricPlane, PlaneMembership


class ModelBehaviorTestCase(TestCase):
    def test_fabric_stringifies_and_exposes_absolute_url(self):
        fabric = Fabric.objects.create(name='Fabric A')

        self.assertEqual(str(fabric), 'Fabric A')
        self.assertEqual(fabric.get_absolute_url(), reverse('plugins:netbox_plant_graph:fabric', args=[fabric.pk]))

    def test_fabric_plane_stringifies_with_fabric_and_plane_number(self):
        fabric = Fabric.objects.create(name='Fabric B')
        plane = FabricPlane.objects.create(fabric=fabric, plane_number=2)

        self.assertEqual(str(plane), 'Fabric B:2')
        self.assertEqual(plane.get_absolute_url(), reverse('plugins:netbox_plant_graph:fabric-plane', args=[plane.pk]))

    def test_plane_membership_absolute_url_uses_public_route_slug(self):
        fabric = Fabric.objects.create(name='Fabric C')
        plane = FabricPlane.objects.create(fabric=fabric, plane_number=1)
        membership = PlaneMembership.objects.create(
            plane=plane,
            member_type=ContentType.objects.get_for_model(Fabric),
            member_id=fabric.pk,
            membership_role='native',
        )

        self.assertEqual(
            membership.get_absolute_url(),
            reverse('plugins:netbox_plant_graph:plane-membership', args=[membership.pk]),
        )

    def test_fabric_resolved_tenant_uses_direct_tenant(self):
        tenant = Tenant.objects.create(name='Core Tenant', slug='core-tenant')
        fabric = Fabric.objects.create(name='Fabric D', tenant=tenant)

        self.assertEqual(fabric.resolved_tenant, tenant)

    def test_fabric_plane_resolved_tenant_inherits_from_fabric(self):
        tenant = Tenant.objects.create(name='Inherited Tenant', slug='inherited-tenant')
        fabric = Fabric.objects.create(name='Fabric E', tenant=tenant)
        plane = FabricPlane.objects.create(fabric=fabric, plane_number=2)

        self.assertEqual(plane.resolved_tenant, tenant)
