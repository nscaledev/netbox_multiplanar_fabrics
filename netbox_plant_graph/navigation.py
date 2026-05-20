from netbox.plugins import PluginMenu, PluginMenuItem


menus = (
    PluginMenu(
        label='Multiplanar Fabrics',
        groups=(
            (
                'V2 Kernel',
                (
                    PluginMenuItem(link='plugins:netbox_plant_graph:fabric_list', link_text='Fabrics'),
                    PluginMenuItem(link='plugins:netbox_plant_graph:architecture_list', link_text='Architectures'),
                    PluginMenuItem(link='plugins:netbox_plant_graph:path_query', link_text='Path Query'),
                ),
            ),
        ),
        icon_class='mdi mdi-graph-outline',
    ),
)
