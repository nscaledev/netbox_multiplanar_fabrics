from importlib import import_module

_EXPORTS = {
    'build_graph': ('netbox_plant_graph.services.sync.graph_builder', 'build_graph'),
    'extract_source_bundle': ('netbox_plant_graph.services.sync.extractor', 'extract_source_bundle'),
    'rebuild_graph': ('netbox_plant_graph.services.sync.rebuilder', 'rebuild_graph'),
    'transform_source_bundle': ('netbox_plant_graph.services.sync.transformer', 'transform_source_bundle'),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_path, attr_name = _EXPORTS[name]
    module = import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
