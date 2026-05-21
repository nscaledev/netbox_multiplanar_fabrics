"""
Spatial template stamp service.

Walks a SpatialTemplate's node tree and creates Location / Rack records
plus SpatialPlacement coordinates.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.utils.text import slugify

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from ..models import DeploymentPlan, SpatialTemplate

logger = logging.getLogger(__name__)


@dataclass
class SpatialStampResult:
    locations: list = field(default_factory=list)
    racks: list = field(default_factory=list)
    placements: list = field(default_factory=list)
    stamp_records: list = field(default_factory=list)
    rack_placements: list = field(default_factory=list)
    # (node_pk, instance_index, plane) → [Device, ...]  — populated during rack-position stamping
    # Used by _stamp_connection_template to look up devices for a given node instance.
    devices_by_node_instance: dict = field(default_factory=dict)


def stamp_spatial_template(
    template: SpatialTemplate,
    scope: object,
    *,
    variables: dict | None = None,
    plan: DeploymentPlan | None = None,
    user: AbstractUser | None = None,
    dry_run: bool = False,
) -> SpatialStampResult:
    """
    Stamp a SpatialTemplate under an existing Site or Location scope.

    Walks the template node tree depth-first, creating Location and Rack
    records with SpatialPlacement coordinates.  After all spatial objects
    are stamped, enumerates ConnectionTemplate rows and stamps cables between
    the newly-created devices.

    Parameters
    ----------
    template : SpatialTemplate
        The spatial template to stamp.
    scope : Site | Location
        The parent object under which to create the hierarchy.
    variables : dict | None
        Override values for template.parameters variables.  Any variable
        declared in template.parameters that is not present in *variables*
        falls back to its declared default.
    plan : DeploymentPlan | None
        Optional deployment plan for provenance.
    user : AbstractUser | None
        The user performing the stamp.
    dry_run : bool
        If True, validate but do not commit.
    """
    from dcim.models import Location, Rack, Site

    from ..models import SpatialPlacement, StampRecord

    # Build resolved variable dict: defaults from schema, overridden by caller.
    resolved_vars: dict = {}
    param_schema = getattr(template, 'parameters', None) or {}
    for var_name, var_def in param_schema.items():
        resolved_vars[var_name] = var_def.get('default', '')
    if variables:
        resolved_vars.update(variables)

    root_nodes = list(
        template.nodes.filter(parent__isnull=True).order_by('sort_order', 'pk')
    )

    if dry_run:
        return SpatialStampResult()

    result = SpatialStampResult()

    # Determine site from scope
    if isinstance(scope, Site):
        site = scope
        parent_location = None
    elif isinstance(scope, Location):
        site = scope.site
        parent_location = scope
    else:
        raise ValueError(f'scope must be a Site or Location, got {type(scope).__name__}')

    # node_device_registry: maps (node_pk, instance_index, plane) → [Device, ...]
    # populated by rack_population_stamp cascades, used by connection enumeration.
    node_device_registry: dict[tuple[int, int, int], list] = {}

    plan_stamp_record = None

    with transaction.atomic():
        for node in root_nodes:
            _stamp_node(
                node=node,
                site=site,
                parent_location=parent_location,
                parent_index=0,
                parent_name=str(scope),
                result=result,
                plan=plan,
                user=user,
                variables=resolved_vars,
                node_device_registry=node_device_registry,
            )

        # Enumerate connection templates and stamp cables between stamped devices.
        conn_templates = list(
            template.connections.select_related(
                'assembly_template', 'source_node', 'dest_node',
            ).order_by('sort_order', 'pk')
        )
        for ct in conn_templates:
            _stamp_connection_template(
                conn_template=ct,
                node_device_registry=node_device_registry,
                result=result,
                plan=plan,
                user=user,
            )

        # Record top-level provenance
        if plan:
            template_ct = ContentType.objects.get_for_model(template)
            scope_ct = ContentType.objects.get_for_model(scope)
            plan_stamp_record = StampRecord.objects.create(
                plan=plan,
                template_type=template_ct,
                template_id=template.pk,
                result_type=scope_ct,
                result_id=scope.pk,
                stamped_at=timezone.now(),
                stamped_by=user,
                status='stamped',
                parameters={'scope_type': scope_ct.model, 'scope_id': scope.pk},
            )
            result.stamp_records.append(plan_stamp_record)

    logger.info(
        'Stamped spatial template %s → %d locations, %d racks, %d placements',
        template, len(result.locations), len(result.racks), len(result.placements),
    )
    return result


def _stamp_node(*, node, site, parent_location, parent_index, parent_name, result, plan, user,
                variables=None, node_device_registry=None):
    """Recursively stamp a SpatialTemplateNode and its children."""
    from dcim.models import Location, Rack

    from ..models import SpatialPlacement

    children = list(node.children.order_by('sort_order', 'pk'))

    # Resolve quantity: prefer quantity_expr if set and resolvable.
    quantity = node.quantity
    qty_expr = getattr(node, 'quantity_expr', '') or ''
    if qty_expr and variables:
        # quantity_expr is expected to be a single var reference like {hall_count}
        stripped = qty_expr.strip().strip('{}')
        if stripped in variables:
            try:
                quantity = int(variables[stripped])
            except (ValueError, TypeError):
                logger.warning(
                    'quantity_expr %r resolved to non-integer value %r for node pk=%s; using static quantity',
                    qty_expr, variables.get(stripped), node.pk,
                )

    for i in range(quantity):
        x = float(node.position_x or 0) + i * float(node.position_x_stride or 0)
        y = float(node.position_y or 0) + i * float(node.position_y_stride or 0)
        z = float(node.position_z or 0)

        if node.node_type == 'rack_position':
            rack_population_template = node.rack_population_template
            plane_multiplier = 1
            if rack_population_template is not None:
                plane_multiplier = getattr(rack_population_template, 'plane_multiplier', None) or 1
                if plane_multiplier <= 1 and getattr(rack_population_template, 'fabric_id', None):
                    plane_multiplier = max(1, int(rack_population_template.fabric.expected_plane_count or 1))

            for plane in range(1, plane_multiplier + 1):
                node_vars = dict(variables or {})
                if plane_multiplier > 1:
                    node_vars['plane'] = plane

                name = render_name_pattern(
                    node.name_pattern,
                    index=i + 1,
                    parent_name=parent_name,
                    variables=node_vars if node_vars else None,
                )
                if plane_multiplier > 1 and '{plane}' not in node.name_pattern:
                    # Avoid duplicate names when multiplying rack positions per plane.
                    name = f'{name}-P{plane}'

                rack = Rack.objects.create(
                    name=name,
                    site=site,
                    location=parent_location,
                    rack_type=node.rack_type,
                )
                result.racks.append(rack)
                placement = _create_placement(rack, parent_location or site, x, y, z, result)
                result.rack_placements.append((rack, placement))

                # Cascade into rack-population stamp if configured
                if rack_population_template:
                    from .rack_population_stamp import stamp_rack_population

                    rp_result = stamp_rack_population(
                        template=rack_population_template,
                        rack=rack,
                        site=site,
                        plan=plan,
                        user=user,
                        variables=node_vars if node_vars else None,
                    )
                    result.stamp_records.extend(rp_result.stamp_records)

                    # Register devices in the node registry for connection enumeration.
                    if node_device_registry is not None:
                        key = (node.pk, i, plane)
                        node_device_registry[key] = rp_result.devices
        else:
            name = render_name_pattern(
                node.name_pattern, index=i + 1, parent_name=parent_name, variables=variables,
            )
            loc = Location.objects.create(
                name=name,
                slug=slugify(name),
                site=site,
                parent=parent_location,
            )
            result.locations.append(loc)
            _create_placement(loc, parent_location or site, x, y, z, result)

            for child_node in children:
                _stamp_node(
                    node=child_node,
                    site=site,
                    parent_location=loc,
                    parent_index=i,
                    parent_name=name,
                    result=result,
                    plan=plan,
                    user=user,
                    variables=variables,
                    node_device_registry=node_device_registry,
                )


def _create_placement(target, reference_frame, x, y, z, result):
    """Create a SpatialPlacement for the given target."""
    from django.contrib.contenttypes.models import ContentType

    from ..models import SpatialPlacement

    target_ct = ContentType.objects.get_for_model(target)
    ref_ct = ContentType.objects.get_for_model(reference_frame) if reference_frame else None

    placement = SpatialPlacement.objects.create(
        target_type=target_ct,
        target_id=target.pk,
        reference_frame_type=ref_ct,
        reference_frame_id=reference_frame.pk if reference_frame else None,
        position_x=x,
        position_y=y,
        position_z=z,
    )
    result.placements.append(placement)
    return placement


def render_name_pattern(pattern: str, *, index: int = 1, parent_name: str = '', variables: dict | None = None) -> str:
    """
    Render a name pattern with token substitution.

    Supported tokens:
        {index}       — 1-based numeric index
        {letter}      — Uppercase letter (A, B, C, ...)
        {row}         — Two-digit zero-padded row number
        {parent_name} — Name of the parent object
        {plane}       — Fabric plane number (injected by plane-multiplied stamp loop)
        {<var>}       — Any variable declared in template.parameters and passed via variables dict
    """
    result = pattern
    result = result.replace('{index}', str(index))
    result = result.replace('{letter}', chr(64 + min(index, 26)))
    result = result.replace('{row}', f'{index:02d}')
    result = result.replace('{parent_name}', parent_name)
    if variables:
        for key, value in variables.items():
            result = result.replace(f'{{{key}}}', str(value))
    return result


def _stamp_connection_template(*, conn_template, node_device_registry, result, plan, user):
    """
    Enumerate device pairs for a ConnectionTemplate and stamp cables between them.

    The ConnectionTemplate describes how devices under two SpatialTemplateNodes
    (source_node and dest_node) should be connected.  The enumeration_mode
    determines the pairing strategy:

        one_to_one  — pair source[i] with dest[i]; counts must match.
        fan_out     — pair source[0] with every dest device.
        fan_in      — pair every source device with dest[0].

    For each pair the source device's interface at ``source_connector_number``
    (within ``source_slot_index``) is connected to the dest device's interface
    at ``dest_connector_number`` (within ``dest_slot_index``) via
    ``stamp_cable_assembly``.
    """
    from .assembly_stamp import stamp_cable_assembly

    source_node_pk = conn_template.source_node_id
    dest_node_pk = conn_template.dest_node_id

    # Collect all devices for source and dest nodes across all instance indices.
    source_devices: list = []
    dest_devices: list = []
    for key, devices in sorted(node_device_registry.items(), key=lambda kv: (kv[0][1], kv[0][2])):
        node_pk = key[0]
        if node_pk == source_node_pk:
            source_devices.extend(devices)
        elif node_pk == dest_node_pk:
            dest_devices.extend(devices)

    if not source_devices or not dest_devices:
        logger.debug(
            'ConnectionTemplate pk=%s skipped — no devices found for source_node=%s or dest_node=%s',
            conn_template.pk, source_node_pk, dest_node_pk,
        )
        return

    enumeration_mode = conn_template.enumeration_mode  # one_to_one | fan_out | fan_in

    pairs: list[tuple] = []
    if enumeration_mode == 'one_to_one':
        if len(source_devices) != len(dest_devices):
            logger.warning(
                'ConnectionTemplate pk=%s enumeration_mode=one_to_one but source has %d devices '
                'and dest has %d devices; skipping',
                conn_template.pk, len(source_devices), len(dest_devices),
            )
            return
        pairs = list(zip(source_devices, dest_devices))
    elif enumeration_mode == 'fan_out':
        anchor = source_devices[0]
        pairs = [(anchor, d) for d in dest_devices]
    elif enumeration_mode == 'fan_in':
        anchor = dest_devices[0]
        pairs = [(s, anchor) for s in source_devices]
    else:
        logger.warning('ConnectionTemplate pk=%s unknown enumeration_mode=%r; skipping', conn_template.pk, enumeration_mode)
        return

    if conn_template.assembly_template is None:
        logger.debug('ConnectionTemplate pk=%s has no assembly_template; skipping cable creation', conn_template.pk)
        return

    for pair_idx, (src_device, dst_device) in enumerate(pairs):
        # Resolve cableable termination targets from slot/connector indices.
        # Slot index selects which interface group; connector number selects within that group.
        a_target = _resolve_device_termination(src_device, conn_template.source_slot_index, conn_template.source_connector_number)
        b_target = _resolve_device_termination(dst_device, conn_template.dest_slot_index, conn_template.dest_connector_number)

        if a_target is None or b_target is None:
            logger.warning(
                'ConnectionTemplate pk=%s pair %d: could not resolve termination targets; skipping',
                conn_template.pk, pair_idx,
            )
            continue

        label = ''
        if conn_template.label_pattern:
            label = render_name_pattern(conn_template.label_pattern, index=pair_idx + 1)

        try:
            cable_result = stamp_cable_assembly(
                template=conn_template.assembly_template,
                a_termination_targets=[a_target],
                b_termination_targets=[b_target],
                label=label,
                plan=plan,
                user=user,
            )
            if cable_result.stamp_record:
                result.stamp_records.append(cable_result.stamp_record)
        except Exception:
            logger.exception(
                'ConnectionTemplate pk=%s pair %d: stamp_cable_assembly failed',
                conn_template.pk, pair_idx,
            )


def _resolve_device_termination(device, slot_index: int | None, connector_number: int | None):
    """
    Resolve a cableable interface/port on a device by slot and connector indices.

    Interfaces are ordered by name.  slot_index selects the (slot_index)-th interface
    (0-based); connector_number is currently unused but reserved for future port
    sub-selection within an interface.

    Returns the interface object or None if not found.
    """
    if device is None:
        return None
    try:
        interfaces = list(device.interfaces.order_by('name'))
        idx = int(slot_index or 0)
        if idx < len(interfaces):
            return interfaces[idx]
        logger.debug('Device %s has only %d interfaces; requested slot_index=%d', device, len(interfaces), idx)
    except Exception:
        logger.exception('_resolve_device_termination failed for device %s', device)
    return None
