from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


menu = PluginMenu(
    label='Multi-planar v2',
    groups=(
        (
            'Operate',
            (
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:graph_overview',
                    link_text='Fabric Overview',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:interface_fanout_trace',
                    link_text='Interface Fanout Trace',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:path_query',
                    link_text='Path Query',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:blast_radius',
                    link_text='Physical Cable Blast Radius',
                ),
            ),
        ),
        (
            'Build & Run',
            (
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:architectureworkspace_list',
                    link_text='Architecture Workspaces',
                    buttons=(
                        PluginMenuButton(
                            link='plugins:netbox_plant_graph:architectureworkspace_add',
                            title='Add Architecture Workspace',
                            icon_class='mdi mdi-plus-thick',
                        ),
                    ),
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:onboardingworkspace_list',
                    link_text='Onboarding Workspaces',
                    buttons=(
                        PluginMenuButton(
                            link='plugins:netbox_plant_graph:onboardingworkspace_add',
                            title='Add Onboarding Workspace',
                            icon_class='mdi mdi-plus-thick',
                        ),
                    ),
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:fabric_onboard',
                    link_text='Onboard Fabric',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:operations_center',
                    link_text='Operations Center',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:import_preview',
                    link_text='Import Preview',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:impact_reports',
                    link_text='Impact Reports',
                ),
            ),
        ),
        (
            'Audit',
            (
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
                    link_text='Exception Requests',
                ),
            ),
        ),
        (
            'Model Inventory',
            (
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
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:lane_workspace',
                    link_text='Lane Inventory',
                ),
                PluginMenuItem(
                    link='plugins:netbox_plant_graph:home',
                    link_text='Model Catalog',
                ),
            ),
        ),
    ),
    icon_class='mdi mdi-graph-outline',
)
