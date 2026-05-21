PLUGINS = [
    "netbox_dns",
    "netbox_prometheus_sd",
    "netbox_plant_graph",
    "netbox_power_plant",
]

PLUGINS_CONFIG = {
    "netbox_plant_graph": {
        "top_level_menu": True,
    },
    "netbox_power_plant": {
        "top_level_menu": True,
    },
}
