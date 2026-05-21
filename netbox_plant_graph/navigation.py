from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


menu = PluginMenu(
    label='Multi-planar v2',
    groups=(
        (
            'Fabric Visibility',
            (
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:graph_overview',
                    link_text='Graph Overview',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:health',
                    link_text='Health',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:fabric_onboard',
                    link_text='Onboard Fabric',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:coordinate_layout',
                    link_text='Coordinate Layout',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:operations_center',
                    link_text='Operations Center',
                ),
            ),
        ),
        (
            'Lane Analysis',
            (
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:path_resolver',
                    link_text='Path Resolver',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:interface_fanout_trace',
                    link_text='Interface Fanout Trace',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:lane_workspace',
                    link_text='Lane Workspace',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:lane_drilldown',
                    link_text='Lane Drilldown',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:lane_compare',
                    link_text='Lane Compare',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:blast_radius',
                    link_text='Physical Cable Blast Radius',
                ),
            ),
        ),
        (
            'Policy & Audit',
            (
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:policy_review',
                    link_text='Policy Review',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:plane_audit',
                    link_text='Plane Audit',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:audit_dashboard',
                    link_text='Audit Dashboard',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:audit_triage',
                    link_text='Audit Triage',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:disjointness_exception_request',
                    link_text='Exception Request',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:policy_dashboard',
                    link_text='Policy Dashboard',
                ),
            ),
        ),
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
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:cableassembly_list',
                    link_text='Cable Assemblies',
                    buttons=(
                        PluginMenuButton(
                            link='plugins:netbox_plant_graph:cableassembly_add',
                            title='Add Cable Assembly',
                            icon_class='mdi mdi-plus-thick',
                        ),
                    ),
                ),
                PluginMenuItem(link='plugins:netbox_plant_graph:path_query', link_text='Path Query'),
                PluginMenuItem(link='plugins:netbox_plant_graph:interface_fanout_trace', link_text='Interface Fanout Trace'),
                PluginMenuItem(link='plugins:netbox_plant_graph:lane_workspace', link_text='Lane Workspace'),
                PluginMenuItem(link='plugins:netbox_plant_graph:policy_dashboard', link_text='Policy Dashboard'),
                PluginMenuItem(link='plugins:netbox_plant_graph:coordinate_layout', link_text='Coordinate Layout'),
                PluginMenuItem(link='plugins:netbox_plant_graph:operations_center', link_text='Operations Center'),
            ),
        ),
    ),
    icon_class='mdi mdi-graph-outline',
)
