import re
from urllib.parse import urlparse

from dcim.models import FrontPort, Interface, RearPort

from netbox_plant_graph.object_registry import get_object_spec


CORE_REGISTRY_MODELS = {
    'interface': (Interface, 'Interface'),
    'frontport': (FrontPort, 'Front Port'),
    'rearport': (RearPort, 'Rear Port'),
}


def get_registry_model(registry_key: str):
    core_entry = CORE_REGISTRY_MODELS.get(registry_key)
    if core_entry is not None:
        return core_entry[0]
    return get_object_spec(registry_key).model


def get_registry_label(registry_key: str) -> str:
    core_entry = CORE_REGISTRY_MODELS.get(registry_key)
    if core_entry is not None:
        return core_entry[1]
    return get_object_spec(registry_key).labels.singular


def resolve_registry_object(registry_key: str, object_id):
    if not registry_key or object_id in (None, ''):
        return None
    try:
        model = get_registry_model(registry_key)
        object_pk = int(_normalize_object_id(object_id))
    except (KeyError, TypeError, ValueError):
        return None
    return model.objects.filter(pk=object_pk).first()


def _normalize_object_id(object_id):
    if isinstance(object_id, int):
        return object_id
    if not isinstance(object_id, str):
        return object_id

    stripped = object_id.strip()
    if stripped.isdigit():
        return stripped

    parsed = urlparse(stripped)
    candidate = parsed.path.rstrip('/') if parsed.scheme or parsed.netloc else stripped.rstrip('/')
    match = re.search(r'(\d+)$', candidate)
    if match is None:
        return stripped
    return match.group(1)


def get_registry_key_for_object(obj) -> str | None:
    registry_key = getattr(obj, 'registry_key', None)
    if registry_key:
        return registry_key
    if isinstance(obj, Interface):
        return 'interface'
    if isinstance(obj, FrontPort):
        return 'frontport'
    if isinstance(obj, RearPort):
        return 'rearport'
    return None
