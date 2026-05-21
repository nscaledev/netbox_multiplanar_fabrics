from importlib.metadata import PackageNotFoundError, version

from netbox.plugins import PluginConfig


try:
    __version__ = version('netbox_plant_graph')
except PackageNotFoundError:
    __version__ = '0+unknown'


class PlantGraphConfig(PluginConfig):
    name = 'netbox_plant_graph'
    verbose_name = 'NetBox Multiplanar Fabrics'
    description = 'Plugin-native topology kernel for multi-planar optical fabrics'
    version = __version__
    author = 'Mencken Davidson'
    author_email = 'mencken@gmail.com'
    base_url = 'plant-graph'
    min_version = '4.2.3'
    max_version = '4.5.99'
    required_settings = []
    default_settings = {
        'forbid_native_cables': True,
        'path_resolution_mode': 'on_demand',
        'architecture_definition_source': 'database',
    }
    template_extensions = 'template_extensions.template_extensions'


config = PlantGraphConfig
