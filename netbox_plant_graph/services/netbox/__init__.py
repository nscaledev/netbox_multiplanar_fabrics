from importlib import import_module

_EXPORTS = {
    'build_object_reference': ('netbox_plant_graph.services.netbox.adapters', 'build_object_reference'),
    'select_scope_inputs': ('netbox_plant_graph.services.netbox.selectors', 'select_scope_inputs'),
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
