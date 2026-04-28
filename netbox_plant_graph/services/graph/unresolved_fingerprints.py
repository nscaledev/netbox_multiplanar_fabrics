import hashlib
import json

from django.contrib.contenttypes.models import ContentType


def _normalized_identity(obj) -> dict | None:
    if obj is None:
        return None

    source_type = getattr(obj, 'source_type', None)
    source_type_id = getattr(obj, 'source_type_id', None)
    source_id = getattr(obj, 'source_id', None)
    if source_type_id and source_id:
        return {
            'app_label': source_type.app_label,
            'model': source_type.model,
            'object_id': source_id,
        }

    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    return {
        'app_label': content_type.app_label,
        'model': content_type.model,
        'object_id': obj.pk,
    }


def _normalize_jsonish(value):
    if isinstance(value, dict):
        return {
            key: _normalize_jsonish(value[key])
            for key in sorted(value)
        }
    if isinstance(value, tuple):
        return [_normalize_jsonish(item) for item in value]
    if isinstance(value, list):
        return [_normalize_jsonish(item) for item in value]
    if isinstance(value, set):
        normalized_items = [_normalize_jsonish(item) for item in value]
        return sorted(normalized_items, key=lambda item: json.dumps(item, sort_keys=True, separators=(',', ':')))
    return value


def build_unresolved_summary_fingerprint(
    *,
    summary_kind: str,
    cause_code: str,
    fabric_id: int,
    plane_numbers: tuple[int, ...],
    owner_object=None,
    representative_object=None,
    selector: dict | None = None,
    anchors: dict | None = None,
) -> str:
    payload = {
        'summary_kind': summary_kind,
        'cause_code': cause_code,
        'fabric': fabric_id,
        'plane_numbers': tuple(sorted(set(plane_numbers or ()))),
        'owner_identity': _normalized_identity(owner_object),
        'representative_identity': _normalized_identity(representative_object),
        'selector': _normalize_jsonish(selector or {}),
        'anchors': _normalize_jsonish(anchors or {}),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
