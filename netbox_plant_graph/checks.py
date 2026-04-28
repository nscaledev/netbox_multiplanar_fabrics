from django.conf import settings
from django.core.checks import Tags, Warning, register
from django.db.utils import OperationalError, ProgrammingError

from .floorplan_compat import get_floorplan_availability


def _plugin_setting(name: str, default):
    return (
        getattr(settings, 'PLUGINS_CONFIG', {})
        .get('netbox_plant_graph', {})
        .get(name, default)
    )


@register(Tags.models)
def check_fabric_plane_custom_field(app_configs, **kwargs):
    """
    Verify that the configured plane custom field exists and is attached to dcim.Interface.
    """
    try:
        from django.contrib.contenttypes.models import ContentType
        from dcim.models import Interface
        from extras.models import CustomField

        plane_field_name = _plugin_setting('default_plane_field_name', 'fabric_plane')
        custom_field = CustomField.objects.filter(name=plane_field_name).first()
        if custom_field is None:
            return [
                Warning(
                    f"Custom field '{plane_field_name}' is missing. Plane membership extraction will be incomplete "
                    "until the field exists on dcim.Interface.",
                    id='netbox_plant_graph.W001',
                )
            ]

        interface_type = ContentType.objects.get_for_model(Interface)
        if not custom_field.object_types.filter(pk=interface_type.pk).exists():
            return [
                Warning(
                    f"Custom field '{plane_field_name}' is not attached to dcim.Interface. "
                    "Plane membership extraction expects that association.",
                    id='netbox_plant_graph.W002',
                )
            ]
    except (OperationalError, ProgrammingError):
        return []

    return []


@register(Tags.models)
def check_breakout_profile_custom_field(app_configs, **kwargs):
    """
    Verify that the configured breakout-profile custom field exists on
    dcim.Cable and targets netbox_plant_graph.BreakoutProfile.
    """
    try:
        from django.contrib.contenttypes.models import ContentType
        from dcim.models import Cable
        from extras.models import CustomField

        from .models import BreakoutProfile

        field_name = _plugin_setting('default_breakout_profile_field_name', 'mpf_breakout_profile')
        custom_field = CustomField.objects.filter(name=field_name).first()
        if custom_field is None:
            return [
                Warning(
                    f"Custom field '{field_name}' is missing. Cable breakout expansion will ignore plugin "
                    'BreakoutProfile assignments until the field exists on dcim.Cable.',
                    id='netbox_plant_graph.W003',
                )
            ]

        if custom_field.type != 'object':
            return [
                Warning(
                    f"Custom field '{field_name}' must be type=object to reference BreakoutProfile rows.",
                    id='netbox_plant_graph.W004',
                )
            ]

        breakout_profile_type = ContentType.objects.get_for_model(BreakoutProfile)
        if custom_field.related_object_type_id != breakout_profile_type.pk:
            return [
                Warning(
                    f"Custom field '{field_name}' must point to netbox_plant_graph.BreakoutProfile.",
                    id='netbox_plant_graph.W005',
                )
            ]

        cable_type = ContentType.objects.get_for_model(Cable)
        if not custom_field.object_types.filter(pk=cable_type.pk).exists():
            return [
                Warning(
                    f"Custom field '{field_name}' is not attached to dcim.Cable. Breakout-profile cable mapping "
                    'expects that association.',
                    id='netbox_plant_graph.W006',
                )
            ]
    except (OperationalError, ProgrammingError):
        return []

    return []


@register(Tags.models)
def check_floorplan_plugin_prerequisite(app_configs, **kwargs):
    """
    Verify that the floorplan plugin required by the refactor work is both
    installed and enabled in NetBox settings.
    """
    availability = get_floorplan_availability()
    if availability.ready:
        return []

    if not availability.installed:
        return [
            Warning(
                availability.message,
                id='netbox_plant_graph.W007',
            )
        ]

    return [
        Warning(
            availability.message,
            id='netbox_plant_graph.W008',
        )
    ]
