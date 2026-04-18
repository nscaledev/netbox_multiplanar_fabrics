from django.test import TestCase

from netbox_plant_graph import forms as forms_module
from netbox_plant_graph.object_registry import FILTER_FORM_OBJECT_SPECS, FORM_OBJECT_SPECS


class FormStructureSmokeTestCase(TestCase):
    def test_form_registry_is_populated(self):
        self.assertTrue(FORM_OBJECT_SPECS)

    def test_generated_filter_forms_expose_model_for_netbox_mixins(self):
        self.assertTrue(FILTER_FORM_OBJECT_SPECS)

        for spec in FILTER_FORM_OBJECT_SPECS:
            filter_form_class = getattr(forms_module, spec.filter_form.class_name)
            with self.subTest(spec=spec.registry_key):
                self.assertIs(filter_form_class.model, spec.model)
                filter_form_class()
