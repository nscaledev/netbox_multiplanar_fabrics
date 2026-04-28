from django.db import models
from django.test import TestCase
from django import forms
from utilities.forms.fields import DynamicModelChoiceField

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

    def test_generated_model_forms_use_dynamic_fields_for_foreign_keys(self):
        for spec in FORM_OBJECT_SPECS:
            form_class = getattr(forms_module, spec.form.class_name)
            for field_name in spec.form.fields:
                model_field = spec.model._meta.get_field(field_name)
                if isinstance(model_field, models.ForeignKey):
                    with self.subTest(spec=spec.registry_key, field=field_name):
                        if model_field.related_model._meta.label_lower == 'contenttypes.contenttype':
                            self.assertIsInstance(form_class.base_fields[field_name], forms.ModelChoiceField)
                        else:
                            self.assertIsInstance(form_class.base_fields[field_name], DynamicModelChoiceField)

    def test_generated_filter_forms_use_dynamic_fields_for_foreign_keys(self):
        for spec in FILTER_FORM_OBJECT_SPECS:
            filter_form_class = getattr(forms_module, spec.filter_form.class_name)
            for field_name in spec.filter_form.fields:
                model_field = spec.model._meta.get_field(field_name)
                if isinstance(model_field, models.ForeignKey):
                    with self.subTest(spec=spec.registry_key, field=field_name):
                        if model_field.related_model._meta.label_lower == 'contenttypes.contenttype':
                            self.assertIsInstance(filter_form_class.base_fields[field_name], forms.ModelChoiceField)
                        else:
                            self.assertIsInstance(filter_form_class.base_fields[field_name], DynamicModelChoiceField)

    def test_workflow_forms_use_dynamic_fields_for_cross_references(self):
        workflow_fields = {
            forms_module.AssemblyStampForm: ('site', 'location', 'rack', 'device_role', 'plan'),
            forms_module.SpatialStampForm: ('site', 'parent_location', 'plan'),
            forms_module.RackPopulationStampForm: ('rack', 'plan'),
            forms_module.FabricOnboardForm: ('tenant', 'site', 'location'),
            forms_module.PathResolverForm: ('plane_id',),
            forms_module.DisjointnessExceptionRequestForm: ('fabric', 'plane_a', 'plane_b'),
        }

        for form_class, field_names in workflow_fields.items():
            for field_name in field_names:
                with self.subTest(form=form_class.__name__, field=field_name):
                    self.assertIsInstance(form_class.base_fields[field_name], DynamicModelChoiceField)

    def test_path_resolver_form_uses_dynamic_selector_fields_for_registry_objects(self):
        form = forms_module.PathResolverForm()

        for registry_key in forms_module.PATH_RESOLVER_REGISTRY_KEYS:
            for role in ('source', 'destination'):
                field_name = forms_module.path_resolver_selector_field_name(role, registry_key)
                with self.subTest(field=field_name):
                    self.assertIsInstance(form.fields[field_name], DynamicModelChoiceField)
                    self.assertTrue(form.fields[field_name].selector)
