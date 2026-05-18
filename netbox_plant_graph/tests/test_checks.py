from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from dcim.models import Cable, Interface
from extras.models import CustomField

from netbox_plant_graph.checks import check_breakout_profile_custom_field, check_fabric_plane_custom_field
from netbox_plant_graph.models import BreakoutProfile


def _model_type_for_model(model):
    try:
        from core.models import ObjectType
        return ObjectType.objects.get_for_model(model)
    except Exception:
        return ContentType.objects.get_for_model(model)


class FabricPlaneCustomFieldCheckTestCase(TestCase):
    def setUp(self):
        self.interface_type = _model_type_for_model(Interface)

    def test_check_warns_when_custom_field_is_missing(self):
        CustomField.objects.filter(name='fabric_plane').delete()

        messages = check_fabric_plane_custom_field(None)

        self.assertEqual([message.id for message in messages], ['netbox_plant_graph.W001'])

    def test_check_warns_when_custom_field_is_not_attached_to_interface(self):
        custom_field, _ = CustomField.objects.get_or_create(
            name='fabric_plane',
            defaults={'label': 'Fabric Plane', 'type': 'integer'},
        )
        custom_field.object_types.clear()

        messages = check_fabric_plane_custom_field(None)

        self.assertEqual([message.id for message in messages], ['netbox_plant_graph.W002'])

    def test_check_passes_when_custom_field_exists_on_interface(self):
        custom_field, _ = CustomField.objects.get_or_create(
            name='fabric_plane',
            defaults={'label': 'Fabric Plane', 'type': 'integer'},
        )
        custom_field.object_types.set([self.interface_type])

        messages = check_fabric_plane_custom_field(None)

        self.assertEqual(messages, [])


class BreakoutProfileCustomFieldCheckTestCase(TestCase):
    def setUp(self):
        self.cable_type = _model_type_for_model(Cable)
        self.breakout_profile_type = _model_type_for_model(BreakoutProfile)

    def test_check_warns_when_custom_field_is_missing(self):
        CustomField.objects.filter(name='mpf_breakout_profile').delete()

        messages = check_breakout_profile_custom_field(None)

        self.assertEqual([message.id for message in messages], ['netbox_plant_graph.W003'])

    def test_check_warns_when_custom_field_has_wrong_type(self):
        custom_field, _ = CustomField.objects.get_or_create(
            name='mpf_breakout_profile',
            defaults={'label': 'Breakout Profile', 'type': 'object'},
        )
        custom_field.type = 'text'
        custom_field.related_object_type = None
        custom_field.save()
        custom_field.object_types.set([self.cable_type])

        messages = check_breakout_profile_custom_field(None)

        self.assertEqual([message.id for message in messages], ['netbox_plant_graph.W004'])

    def test_check_warns_when_custom_field_targets_wrong_model(self):
        interface_type = _model_type_for_model(Interface)
        custom_field, _ = CustomField.objects.get_or_create(
            name='mpf_breakout_profile',
            defaults={'label': 'Breakout Profile', 'type': 'object'},
        )
        custom_field.related_object_type = interface_type
        custom_field.save()
        custom_field.object_types.set([self.cable_type])

        messages = check_breakout_profile_custom_field(None)

        self.assertEqual([message.id for message in messages], ['netbox_plant_graph.W005'])

    def test_check_warns_when_custom_field_is_not_attached_to_cable(self):
        custom_field, _ = CustomField.objects.get_or_create(
            name='mpf_breakout_profile',
            defaults={'label': 'Breakout Profile', 'type': 'object'},
        )
        custom_field.related_object_type = self.breakout_profile_type
        custom_field.save()
        custom_field.object_types.clear()

        messages = check_breakout_profile_custom_field(None)

        self.assertEqual([message.id for message in messages], ['netbox_plant_graph.W006'])

    def test_check_passes_when_custom_field_exists_on_cable(self):
        custom_field, _ = CustomField.objects.get_or_create(
            name='mpf_breakout_profile',
            defaults={'label': 'Breakout Profile', 'type': 'object'},
        )
        custom_field.related_object_type = self.breakout_profile_type
        custom_field.save()
        custom_field.object_types.set([self.cable_type])

        messages = check_breakout_profile_custom_field(None)

        self.assertEqual(messages, [])
