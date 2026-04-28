from django.test import SimpleTestCase

from netbox_plant_graph.object_registry import OBJECT_SPECS, get_object_spec


class ObjectRegistryContractTestCase(SimpleTestCase):
    def test_registry_contains_expected_phase_one_objects(self):
        registry_keys = {spec.registry_key for spec in OBJECT_SPECS}

        self.assertTrue({'fabric', 'fabricplane', 'plantnode', 'terminationpoint', 'attachmentunit', 'coarseedge', 'fineedge', 'transfermap', 'planemembership'}.issubset(registry_keys))

    def test_fabric_spec_exposes_route_and_surface_metadata(self):
        spec = get_object_spec('fabric')

        self.assertEqual(spec.routes.slug, 'fabric')
        self.assertEqual(spec.api.basename, 'fabrics')
        self.assertEqual(spec.view.list_class_name, 'FabricListView')
        self.assertEqual(spec.table.linkify_field, 'name')

    def test_spatialplacement_spec_is_read_only(self):
        spec = get_object_spec('spatialplacement')

        self.assertFalse(spec.view.supports_create)
        self.assertTrue(spec.api.read_only)
        self.assertFalse(spec.navigation.show_add_button)
