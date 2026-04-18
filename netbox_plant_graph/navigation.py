from django.conf import settings
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

from .object_registry import get_navigation_groups


def build_menu_item(spec):
    buttons = ()
    if spec.navigation.show_add_button and spec.view.edit_class_name is not None:
        buttons = (
            PluginMenuButton(link=spec.routes.add_url_name, title=f'Add {spec.labels.singular}', icon_class='mdi mdi-plus-thick'),
        )
    return PluginMenuItem(link=spec.routes.list_url_name, link_text=spec.navigation.label, buttons=buttons)


menus = []
for group_name, specs in get_navigation_groups():
    menu_items = tuple(build_menu_item(spec) for spec in specs)
    menus.append(PluginMenu(label=group_name, groups=((group_name, menu_items),), icon_class='mdi mdi-graph-outline'))


plugin_settings = settings.PLUGINS_CONFIG.get('netbox_plant_graph', {})
if not plugin_settings.get('top_level_menu', True):
    menus = (
        PluginMenu(
            label='Plant Graph',
            groups=tuple((group_name, tuple(build_menu_item(spec) for spec in specs)) for group_name, specs in get_navigation_groups()),
            icon_class='mdi mdi-graph-outline',
        ),
    )
