from netbox_plant_graph.object_registry import API_OBJECT_SPECS, OBJECT_SPECS, VIEW_OBJECT_SPECS

REGISTRY_OBJECT_KEYS = tuple(spec.registry_key for spec in OBJECT_SPECS)
API_OBJECT_KEYS = tuple(spec.registry_key for spec in API_OBJECT_SPECS)
VIEW_OBJECT_KEYS = tuple(spec.registry_key for spec in VIEW_OBJECT_SPECS)
