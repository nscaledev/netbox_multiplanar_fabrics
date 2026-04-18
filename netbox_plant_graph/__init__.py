from importlib.metadata import PackageNotFoundError, version

from django.conf import settings
from netbox.plugins import PluginConfig

from .compatibility import emit_runtime_compatibility_warning

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
    min_version = '4.5.0'
    max_version = '4.5.99'
    required_settings = []
    default_settings = {
        'top_level_menu': True,
        'graph_default_resolution': 'attachment_unit',
        'enable_incremental_refresh': True,
        'max_path_depth': 256,
        'materialize_signal_lanes': True,
        'default_plane_field_name': 'fabric_plane',
    }
    template_extensions = 'template_extensions.template_extensions'

    def ready(self):
        super().ready()

        from . import jobs  # noqa: F401

        from . import navigation as _nav
        from netbox.plugins import register_menu as _register_menu
        for _m in getattr(_nav, 'menus', ()):  # pragma: no branch
            _register_menu(_m)
        emit_runtime_compatibility_warning(netbox_version=getattr(settings, 'VERSION', self.min_version))


config = PlantGraphConfig
