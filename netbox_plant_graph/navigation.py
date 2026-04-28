from django.conf import settings
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

from .object_registry import get_navigation_groups


WORKFLOW_MENU_GROUPS = (
    (
        'Fabric Visibility',
        (
            PluginMenuItem(link='plugins:netbox_plant_graph:graph_overview', link_text='Graph Overview'),
            PluginMenuItem(link='plugins:netbox_plant_graph:health', link_text='Health'),
            PluginMenuItem(link='plugins:netbox_plant_graph:fabric_onboard', link_text='Onboard Fabric'),
        ),
    ),
    (
        'Lane Analysis',
        (
            PluginMenuItem(link='plugins:netbox_plant_graph:path_resolver', link_text='Path Resolver'),
            PluginMenuItem(link='plugins:netbox_plant_graph:lane_workspace', link_text='Lane Workspace'),
            PluginMenuItem(link='plugins:netbox_plant_graph:lane_drilldown', link_text='Lane Drilldown'),
            PluginMenuItem(link='plugins:netbox_plant_graph:lane_compare', link_text='Lane Compare'),
            PluginMenuItem(link='plugins:netbox_plant_graph:blast_radius', link_text='Physical Cable Blast Radius'),
        ),
    ),
    (
        'Policy & Audit',
        (
            PluginMenuItem(link='plugins:netbox_plant_graph:policy_review', link_text='Policy Review'),
            PluginMenuItem(link='plugins:netbox_plant_graph:plane_audit', link_text='Plane Audit'),
            PluginMenuItem(link='plugins:netbox_plant_graph:audit_dashboard', link_text='Audit Dashboard'),
            PluginMenuItem(link='plugins:netbox_plant_graph:audit_triage', link_text='Audit Triage'),
            PluginMenuItem(link='plugins:netbox_plant_graph:disjointness_exception_request', link_text='Exception Request'),
        ),
    ),
)


def build_menu_item(spec):
    buttons = ()
    if spec.navigation.show_add_button and spec.view.edit_class_name is not None:
        buttons = (
            PluginMenuButton(link=spec.routes.add_url_name, title=f'Add {spec.labels.singular}', icon_class='mdi mdi-plus-thick'),
        )
    return PluginMenuItem(link=spec.routes.list_url_name, link_text=spec.navigation.label, buttons=buttons)


def build_table_menu_groups():
    return tuple(
        (group_name, tuple(build_menu_item(spec) for spec in specs))
        for group_name, specs in get_navigation_groups()
    )


def prefix_menu_groups(prefix, groups):
    return tuple((f'{prefix} / {group_name}', items) for group_name, items in groups)


TABLE_MENU_GROUPS = build_table_menu_groups()


plugin_settings = settings.PLUGINS_CONFIG.get('netbox_plant_graph', {})
if plugin_settings.get('top_level_menu', True):
    menus = (
        PluginMenu(label='Plant Graph Workflows', groups=WORKFLOW_MENU_GROUPS, icon_class='mdi mdi-graph-outline'),
        PluginMenu(label='Plant Graph Tables', groups=TABLE_MENU_GROUPS, icon_class='mdi mdi-table-large'),
    )
else:
    menus = (
        PluginMenu(
            label='Plant Graph',
            groups=prefix_menu_groups('Workflows', WORKFLOW_MENU_GROUPS) + prefix_menu_groups('Tables', TABLE_MENU_GROUPS),
            icon_class='mdi mdi-graph-outline',
        ),
    )
