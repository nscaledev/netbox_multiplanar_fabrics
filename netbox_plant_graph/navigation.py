from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


menu = PluginMenu(
    label='Multiplanar Fabrics',
    groups=(
        (
            'Multi-planar v2',
            (
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:home',
                    link_text='Overview',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:fabric_list',
                    link_text='Fabrics',
                    buttons=(
                        PluginMenuButton(
                            link='plugins:netbox_plant_graph:fabric_add',
                            title='Add Fabric',
                            icon_class='mdi mdi-plus-thick',
                        ),
                    ),
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:architecture_list',
                    link_text='Architectures',
                    buttons=(
                        PluginMenuButton(
                            link='plugins:netbox_plant_graph:fabricarchitecture_add',
                            title='Add Architecture',
                            icon_class='mdi mdi-plus-thick',
                        ),
                    ),
                ),
                PluginMenuItem(link='plugins:netbox_plant_graph:path_query', link_text='Path Query'),
            ),
        ),
    ),
    icon_class='mdi mdi-graph-outline',
)
