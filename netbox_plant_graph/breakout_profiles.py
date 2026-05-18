from django.conf import settings


def get_breakout_profile_field_name() -> str:
    plugin_config = getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})
    return plugin_config.get('default_breakout_profile_field_name', 'mpf_breakout_profile')


def ensure_breakout_profile_custom_field():
    """
    Ensure the configured breakout-profile custom field exists on dcim.Cable and
    points at netbox_plant_graph.BreakoutProfile.
    """
    from dcim.models import Cable
    try:
        from core.models import ObjectType
    except Exception:  # pragma: no cover
        ObjectType = None
    from django.contrib.contenttypes.models import ContentType
    from extras.models import CustomField

    from .models import BreakoutProfile

    field_name = get_breakout_profile_field_name()
    if ObjectType is not None:
        breakout_profile_type = ObjectType.objects.get_for_model(BreakoutProfile)
        cable_type = ObjectType.objects.get_for_model(Cable)
    else:
        breakout_profile_type = ContentType.objects.get_for_model(BreakoutProfile)
        cable_type = ContentType.objects.get_for_model(Cable)

    custom_field, created = CustomField.objects.get_or_create(
        name=field_name,
        defaults={
            'label': 'Breakout Profile',
            'type': 'object',
            'related_object_type': breakout_profile_type,
            'required': False,
            'description': (
                'Plant Graph breakout profile used to expand a cable into child '
                'attachment-unit segments during graph rebuild.'
            ),
        },
    )

    changed = False
    if custom_field.type != 'object':
        custom_field.type = 'object'
        changed = True
    if custom_field.related_object_type_id != breakout_profile_type.pk:
        custom_field.related_object_type = breakout_profile_type
        changed = True
    if changed:
        custom_field.save()

    if not custom_field.object_types.filter(pk=cable_type.pk).exists():
        custom_field.object_types.add(cable_type)

    return custom_field, created, changed


def get_plugin_breakout_profile_for_cable(cable):
    from .models import BreakoutProfile

    raw_value = (getattr(cable, 'custom_field_data', None) or {}).get(get_breakout_profile_field_name())
    if raw_value in (None, '', []):
        return None
    if isinstance(raw_value, BreakoutProfile):
        return raw_value
    return BreakoutProfile.objects.filter(pk=getattr(raw_value, 'pk', raw_value)).first()


def get_breakout_profile_for_cable(cable):
    profile = cable.profile_class() if getattr(cable, 'profile_class', None) else None
    if profile is not None:
        return profile
    return get_plugin_breakout_profile_for_cable(cable)


def get_breakout_profile_name_for_cable(cable) -> str:
    if getattr(cable, 'profile_class', None) and cable.profile_class() is not None:
        return getattr(cable, 'profile', '') or ''
    breakout_profile = get_plugin_breakout_profile_for_cable(cable)
    return breakout_profile.slug if breakout_profile is not None else ''


def set_plugin_breakout_profile_for_cable(cable, breakout_profile):
    custom_field_data = dict(getattr(cable, 'custom_field_data', None) or {})
    field_name = get_breakout_profile_field_name()

    if breakout_profile is None:
        custom_field_data.pop(field_name, None)
    else:
        custom_field_data[field_name] = getattr(breakout_profile, 'pk', breakout_profile)

    cable.custom_field_data = custom_field_data
    cable.save()
    return cable
