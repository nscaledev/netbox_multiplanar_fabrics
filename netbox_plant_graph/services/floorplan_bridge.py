from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.urls import reverse

from ..floorplan_compat import assert_floorplan_available


MANAGED_BY = 'netbox_plant_graph'
MANAGED_FLAG = 'managed_by'
MANUAL_OVERRIDE_FLAG = 'layout_manual_override'


@dataclass(frozen=True)
class FloorplanEnsureResult:
    floorplan: Any
    created: bool


@dataclass(frozen=True)
class FloorplanScopeUrls:
    add_url: str
    edit_url: str | None
    object_url: str | None


@dataclass
class FloorplanSyncResult:
    floorplan: Any
    created_floorplan: bool = False
    created_objects: int = 0
    updated_objects: int = 0
    skipped_manual_override: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class FloorplanPlacementReconcileResult:
    floorplan: Any
    updated_placements: int = 0
    created_placements: int = 0
    skipped_unmanaged: int = 0
    skipped_missing_rack: int = 0
    skipped_missing_placement: int = 0
    errors: list[str] = field(default_factory=list)


def _get_floorplan_model_class():
    assert_floorplan_available()
    from netbox_floorplan.models import Floorplan

    return Floorplan


def _get_scope_binding(scope) -> tuple[str, dict[str, Any]]:
    from dcim.models import Location, Site

    if isinstance(scope, Site):
        return 'site', {'site': scope}
    if isinstance(scope, Location):
        return 'location', {'location': scope}
    raise TypeError(f'Unsupported floorplan scope: {type(scope).__name__}. Expected dcim.Site or dcim.Location.')


def get_floorplan_for_scope(scope):
    floorplan_model = _get_floorplan_model_class()
    _, filters = _get_scope_binding(scope)
    return floorplan_model.objects.filter(**filters).first()


def ensure_floorplan_for_scope(scope) -> FloorplanEnsureResult:
    floorplan_model = _get_floorplan_model_class()
    _, filters = _get_scope_binding(scope)
    floorplan, created = floorplan_model.objects.get_or_create(**filters)
    return FloorplanEnsureResult(floorplan=floorplan, created=created)


def get_floorplan_urls_for_scope(scope) -> FloorplanScopeUrls:
    floorplan = get_floorplan_for_scope(scope)
    scope_key, _ = _get_scope_binding(scope)
    add_url = f"{reverse('plugins:netbox_floorplan:floorplan_add')}?{urlencode({scope_key: scope.pk})}"
    edit_url = None
    if floorplan is not None:
        edit_url = reverse('plugins:netbox_floorplan:floorplan_edit', kwargs={'pk': floorplan.pk})
    return FloorplanScopeUrls(
        add_url=add_url,
        edit_url=edit_url,
        object_url=edit_url,
    )


def sync_rack_placements_to_floorplan(scope, rack_placements, *, replace_existing: bool = False, force: bool = False):
    ensured = ensure_floorplan_for_scope(scope)
    result = FloorplanSyncResult(
        floorplan=ensured.floorplan,
        created_floorplan=ensured.created,
    )

    canvas = _normalize_canvas(getattr(ensured.floorplan, 'canvas', None))
    objects = canvas['objects']
    canvas_changed = False

    for rack_placement in rack_placements:
        try:
            rack, placement = _coerce_rack_placement(rack_placement)
            serialized = _serialize_rack_group(rack, placement)
            existing_index = _find_rack_object_index(objects, rack.pk)

            if existing_index is None:
                objects.append(serialized)
                result.created_objects += 1
                canvas_changed = True
                continue

            existing = objects[existing_index]
            if not force and _is_manual_override(existing):
                result.skipped_manual_override += 1
                continue

            replacement = serialized if replace_existing else _merge_floorplan_object(existing, serialized)
            if replacement != existing:
                objects[existing_index] = replacement
                result.updated_objects += 1
                canvas_changed = True
        except Exception as exc:  # pragma: no cover - exercised through result assertions
            result.errors.append(str(exc))

    if canvas_changed:
        ensured.floorplan.canvas = canvas
        ensured.floorplan.save()

    return result


def reconcile_floorplan_to_placements(scope, *, create_missing: bool = False):
    from django.contrib.contenttypes.models import ContentType

    from dcim.models import Rack

    from ..models import SpatialPlacement

    floorplan = get_floorplan_for_scope(scope)
    if floorplan is None:
        raise ValueError(f'No floorplan exists for {type(scope).__name__} scope {scope!s}.')

    result = FloorplanPlacementReconcileResult(floorplan=floorplan)
    canvas = _normalize_canvas(getattr(floorplan, 'canvas', None))
    rack_ct = ContentType.objects.get_for_model(Rack)
    scope_ct = ContentType.objects.get_for_model(scope) if create_missing else None

    for obj in canvas['objects']:
        custom_meta = obj.get('custom_meta') or {}
        if custom_meta.get('object_type') != 'rack':
            continue
        if custom_meta.get(MANAGED_FLAG) != MANAGED_BY:
            result.skipped_unmanaged += 1
            continue

        try:
            rack_id = int(custom_meta.get('object_id'))
        except (TypeError, ValueError):
            result.skipped_missing_rack += 1
            continue

        rack = Rack.objects.filter(pk=rack_id).select_related('location', 'site').first()
        if rack is None or not _scope_contains_rack(scope, rack):
            result.skipped_missing_rack += 1
            continue

        placement = SpatialPlacement.objects.filter(target_type=rack_ct, target_id=rack.pk).first()
        if placement is None:
            if not create_missing:
                result.skipped_missing_placement += 1
                continue
            placement = SpatialPlacement(
                target_type=rack_ct,
                target_id=rack.pk,
                reference_frame_type=scope_ct,
                reference_frame_id=scope.pk,
                position_z=0,
                coordinate_unit='meters',
            )
            result.created_placements += 1
        else:
            result.updated_placements += 1

        try:
            coordinate_unit = getattr(placement, 'coordinate_unit', None) or 'meters'
            placement.position_x = _placement_coordinate(obj.get('left'), coordinate_unit)
            placement.position_y = _placement_coordinate(obj.get('top'), coordinate_unit)
            if obj.get('angle') is not None:
                placement.orientation = round(_coerce_float(obj.get('angle')), 2)
            placement.save()
        except Exception as exc:  # pragma: no cover - exercised via result assertions
            if placement.pk is None and result.created_placements > 0:
                result.created_placements -= 1
            elif placement.pk is not None and result.updated_placements > 0:
                result.updated_placements -= 1
            result.errors.append(str(exc))

    return result


def _coerce_rack_placement(rack_placement) -> tuple[Any, Any]:
    if isinstance(rack_placement, tuple) and len(rack_placement) == 2:
        return rack_placement
    if isinstance(rack_placement, dict):
        return rack_placement['rack'], rack_placement['placement']
    if hasattr(rack_placement, 'rack') and hasattr(rack_placement, 'placement'):
        return rack_placement.rack, rack_placement.placement
    raise TypeError('Rack placement entries must be a (rack, placement) tuple, dict, or object with rack/placement.')


def _scope_contains_rack(scope, rack) -> bool:
    from dcim.models import Location, Site

    if isinstance(scope, Site):
        return getattr(rack, 'site_id', None) == scope.pk
    if isinstance(scope, Location):
        location = getattr(rack, 'location', None)
        if location is None:
            return False
        if getattr(location, 'pk', None) == scope.pk:
            return True
        get_ancestors = getattr(location, 'get_ancestors', None)
        if callable(get_ancestors):
            return get_ancestors(include_self=True).filter(pk=scope.pk).exists()

        current = getattr(location, 'parent', None)
        while current is not None:
            if getattr(current, 'pk', None) == scope.pk:
                return True
            current = getattr(current, 'parent', None)
        return False
    raise TypeError(f'Unsupported floorplan scope: {type(scope).__name__}. Expected dcim.Site or dcim.Location.')


def _normalize_canvas(canvas) -> dict[str, Any]:
    normalized = deepcopy(canvas) if isinstance(canvas, dict) else {}
    objects = normalized.get('objects')
    if not isinstance(objects, list):
        normalized['objects'] = []
    return normalized


def _find_rack_object_index(objects: list[dict[str, Any]], rack_id: int) -> int | None:
    rack_id_str = str(rack_id)
    for index, obj in enumerate(objects):
        custom_meta = obj.get('custom_meta') or {}
        if custom_meta.get('object_type') == 'rack' and str(custom_meta.get('object_id')) == rack_id_str:
            return index
    return None


def _is_manual_override(obj: dict[str, Any]) -> bool:
    custom_meta = obj.get('custom_meta') or {}
    return bool(
        custom_meta.get(MANUAL_OVERRIDE_FLAG)
        or custom_meta.get('manual_override')
        or custom_meta.get(MANAGED_FLAG) not in (None, MANAGED_BY)
    )


def _merge_floorplan_object(existing: dict[str, Any], replacement: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(replacement)
    merged_meta = merged.setdefault('custom_meta', {})
    existing_meta = existing.get('custom_meta') or {}

    for key in ('manual_color', 'manual_text_color', MANUAL_OVERRIDE_FLAG, 'manual_override'):
        if key in existing_meta and key not in merged_meta:
            merged_meta[key] = existing_meta[key]

    return merged


def _serialize_rack_group(rack, placement) -> dict[str, Any]:
    width, height = _rack_canvas_dimensions(rack)
    left = _canvas_coordinate(getattr(placement, 'position_x', 0), getattr(placement, 'coordinate_unit', 'meters'))
    top = _canvas_coordinate(getattr(placement, 'position_y', 0), getattr(placement, 'coordinate_unit', 'meters'))
    rotation = _coerce_float(getattr(placement, 'orientation', 0))
    fill = _rack_fill_color(rack)
    status = _rack_status_label(rack)
    custom_meta = _rack_custom_meta(rack)

    rect = {
        'type': 'rect',
        'left': -width / 2,
        'top': -height / 2,
        'width': width,
        'height': height,
        'fill': fill,
        'opacity': 0.8,
        'originX': 'left',
        'originY': 'top',
        'custom_meta': {
            **custom_meta,
        },
    }
    name_text = {
        'type': 'textbox',
        'text': str(getattr(rack, 'name', rack)),
        'fontFamily': 'Courier New',
        'fontSize': 16,
        'fill': '#FFFFFF',
        'width': width,
        'textAlign': 'center',
        'originX': 'center',
        'originY': 'center',
        'left': 0,
        'top': 0,
        'stroke': '#000000',
        'strokeWidth': 2,
        'paintFirst': 'stroke',
        'custom_meta': {
            'text_type': 'name',
        },
    }
    status_text = {
        'type': 'i-text',
        'text': status,
        'fontFamily': 'Courier New',
        'fontSize': 13,
        'fill': '#6ea8fe',
        'textAlign': 'center',
        'originX': 'center',
        'originY': 'center',
        'left': 0,
        'top': 16,
        'custom_meta': {
            'text_type': 'status',
        },
    }

    return {
        'type': 'group',
        'id': getattr(rack, 'pk', None),
        'left': left,
        'top': top,
        'angle': rotation,
        'originX': 'center',
        'originY': 'center',
        'objects': [rect, name_text, status_text],
        'custom_meta': custom_meta,
    }


def _rack_custom_meta(rack) -> dict[str, Any]:
    object_url = None
    if hasattr(rack, 'get_absolute_url'):
        try:
            object_url = rack.get_absolute_url()
        except Exception:  # pragma: no cover - defensive only
            object_url = None
    if not object_url and getattr(rack, 'pk', None) is not None:
        object_url = f'/dcim/racks/{rack.pk}/'

    return {
        'object_type': 'rack',
        'object_id': getattr(rack, 'pk', None),
        'object_name': str(getattr(rack, 'name', rack)),
        'object_url': object_url,
        MANAGED_FLAG: MANAGED_BY,
        MANUAL_OVERRIDE_FLAG: False,
    }


def _rack_fill_color(rack) -> str:
    role = getattr(rack, 'role', None)
    color = getattr(role, 'color', None)
    if color:
        value = str(color)
        return value if value.startswith('#') else f'#{value}'
    return '#000000'


def _rack_status_label(rack) -> str:
    getter = getattr(rack, 'get_status_display', None)
    if callable(getter):
        return str(getter())
    status = getattr(rack, 'status', '')
    return str(status or '')


def _rack_canvas_dimensions(rack) -> tuple[float, float]:
    width = getattr(rack, 'outer_width', None)
    depth = getattr(rack, 'outer_depth', None)
    unit = getattr(rack, 'outer_unit', None)

    if width is None or depth is None or not unit:
        return 60.0, 91.0

    width_value = _coerce_float(width)
    depth_value = _coerce_float(depth)
    if unit == 'in':
        return round(width_value * 0.0254 * 100, 2), round(depth_value * 0.0254 * 100, 2)
    return round(width_value / 1000 * 100, 2), round(depth_value / 1000 * 100, 2)


def _canvas_coordinate(value, coordinate_unit: str | None) -> float:
    scales = {
        'meters': 100.0,
        'feet': 30.48,
        'rack_units': 10.0,
    }
    scale = scales.get(coordinate_unit or 'meters', 100.0)
    return round(_coerce_float(value) * scale, 2)


def _placement_coordinate(value, coordinate_unit: str | None) -> float:
    scales = {
        'meters': 100.0,
        'feet': 30.48,
        'rack_units': 10.0,
    }
    scale = scales.get(coordinate_unit or 'meters', 100.0)
    return round(_coerce_float(value) / scale, 3)


def _coerce_float(value) -> float:
    if value is None:
        return 0.0
    return float(value)
