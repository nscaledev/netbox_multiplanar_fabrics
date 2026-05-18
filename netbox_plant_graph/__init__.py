import logging

from importlib.metadata import PackageNotFoundError, version

from django.conf import settings
from netbox.plugins import PluginConfig

from .breakout_profiles import ensure_breakout_profile_custom_field
from .compatibility import emit_runtime_compatibility_warning
from .floorplan_compat import emit_floorplan_availability_warning

logger = logging.getLogger('netbox_plant_graph')


def _plugin_config_setting(name: str, default):
    return (
        getattr(settings, 'PLUGINS_CONFIG', {})
        .get('netbox_plant_graph', {})
        .get(name, default)
    )


def _ensure_managed_custom_fields(sender, **kwargs):
    """
    Ensure the plugin-managed custom fields exist and are attached to the
    expected NetBox core objects. Runs after every migration and is safe to
    call repeatedly.
    """
    try:
        try:
            from core.models import ObjectType
        except Exception:  # pragma: no cover
            ObjectType = None
        from django.contrib.contenttypes.models import ContentType
        from extras.models import CustomField

        plane_field_name = _plugin_config_setting('default_plane_field_name', 'fabric_plane')

        cf, created = CustomField.objects.get_or_create(
            name=plane_field_name,
            defaults={
                'label': 'Fabric Plane',
                'type': 'integer',
                'required': False,
                'description': (
                    'RoCE fabric plane number (1–N) for this attachment unit. '
                    'Read by the Plant Graph plugin to assign PlaneMembership records.'
                ),
            },
        )
        from dcim.models import Interface
        if ObjectType is not None:
            iface_ct = ObjectType.objects.get_for_model(Interface)
        else:
            iface_ct = ContentType.objects.get_for_model(Interface)
        if not cf.object_types.filter(pk=iface_ct.pk).exists():
            cf.object_types.add(iface_ct)
        if created:
            logger.info('Created %r custom field on dcim.Interface', plane_field_name)
        breakout_field_name = _plugin_config_setting('default_breakout_profile_field_name', 'mpf_breakout_profile')
        breakout_field, breakout_created, breakout_changed = ensure_breakout_profile_custom_field()
        if breakout_created:
            logger.info('Created %r custom field on dcim.Cable', breakout_field_name)
        elif breakout_changed:
            logger.info('Updated %r custom field configuration on dcim.Cable', breakout_field.name)
    except Exception as exc:
        logger.warning('Could not ensure plugin-managed custom fields: %s', exc)

try:
    __version__ = version('netbox_plant_graph')
except PackageNotFoundError:
    __version__ = '0+unknown'


class PlantGraphConfig(PluginConfig):
    name = 'netbox_plant_graph'
    verbose_name = 'NetBox Plant Graph'
    description = 'Lane-aware, plane-aware topology graph for structured GPU fabric cabling'
    version = __version__
    author = 'Mencken Davidson'
    author_email = 'mencken@gmail.com'
    base_url = 'plant-graph'
    min_version = '4.2.3'
    max_version = '4.5.99'
    required_settings = []
    default_settings = {
        'top_level_menu': True,
        'graph_default_resolution': 'attachment_unit',
        'enable_incremental_refresh': True,
        'max_path_depth': 256,
        'materialize_signal_lanes': True,
        'default_plane_field_name': 'fabric_plane',
        'default_breakout_profile_field_name': 'mpf_breakout_profile',
        'policy_reporting_cache_enabled': False,
        'policy_reporting_cache_timeout': 300,
        'persist_unresolved_summaries': True,
        'unresolved_reporting_cache_enabled': False,
        'unresolved_reporting_cache_timeout': 300,
        'audit_suppression_default_days': 7,
        'audit_run_retention_days': 90,
        'audit_event_retention_days': 180,
        'graph_build_run_retention_days': 90,
    }
    template_extensions = 'template_extensions.template_extensions'

    def ready(self):
        super().ready()

        from . import jobs  # noqa: F401
        from . import checks  # noqa: F401

        from django.db.models.signals import post_migrate
        post_migrate.connect(_ensure_managed_custom_fields, sender=self)
        _ensure_managed_custom_fields(sender=self)

        from . import navigation as _nav
        try:
            from netbox.plugins import register_menu as _register_menu
            for _m in getattr(_nav, 'menus', ()):  # pragma: no branch
                _register_menu(_m)
        except (ImportError, AttributeError):  # pragma: no cover
            pass  # register_menu not available on this NetBox version
        emit_runtime_compatibility_warning(netbox_version=getattr(settings, 'VERSION', self.min_version))
        emit_floorplan_availability_warning()


config = PlantGraphConfig
