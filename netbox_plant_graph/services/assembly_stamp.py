"""
Assembly stamp services.

Two stamp operations:
  1. stamp_cable_assembly — creates a Cable + CableTerminations from an AssemblyTemplate
  2. stamp_passive_device — creates a Device + FrontPorts + RearPorts + PortMappings

Both optionally record a StampRecord for provenance tracking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from ..breakout_profiles import set_plugin_breakout_profile_for_cable

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from ..models import AssemblyTemplate, DeploymentPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CableStampResult:
    cable: object = None
    cable_terminations: list = field(default_factory=list)
    stamp_record: object = None


@dataclass
class DeviceStampResult:
    device: object = None
    rear_ports: list = field(default_factory=list)
    front_ports: list = field(default_factory=list)
    port_mappings: list = field(default_factory=list)
    stamp_record: object = None


# ---------------------------------------------------------------------------
# Cable-assembly stamp  (shuffle trunk, trunk bundle, etc.)
# ---------------------------------------------------------------------------

def stamp_cable_assembly(
    template: AssemblyTemplate,
    a_termination_targets: list,
    b_termination_targets: list,
    *,
    label: str = '',
    plan: DeploymentPlan | None = None,
    user: AbstractUser | None = None,
    dry_run: bool = False,
) -> CableStampResult:
    """
    Create a Cable with CableTerminations from an AssemblyTemplate.

    Parameters
    ----------
    template : AssemblyTemplate
        The assembly template defining connectors and mappings.
    a_termination_targets : list
        Ordered list of cableable objects for the A-side connectors.
        Length must equal template.a_connectors.count().
    b_termination_targets : list
        Ordered list of cableable objects for the B-side connectors.
    label : str
        Optional cable label.
    plan : DeploymentPlan | None
        If provided, a StampRecord is created under this plan.
    user : AbstractUser | None
        The user performing the stamp.
    dry_run : bool
        If True, validate but do not commit.

    Returns
    -------
    CableStampResult
    """
    from dcim.choices import LinkStatusChoices
    from dcim.models import Cable, CableTermination

    a_connectors = list(template.a_connectors)
    b_connectors = list(template.b_connectors)

    if len(a_termination_targets) != len(a_connectors):
        raise ValueError(
            f'Expected {len(a_connectors)} A-side targets, got {len(a_termination_targets)}'
        )
    if len(b_termination_targets) != len(b_connectors):
        raise ValueError(
            f'Expected {len(b_connectors)} B-side targets, got {len(b_termination_targets)}'
        )

    if dry_run:
        return CableStampResult()

    with transaction.atomic():
        native_cable_profile = ''
        if template.cable_profile_hint:
            from dcim.choices import CableProfileChoices

            valid_profiles = {choice[0] for choice in CableProfileChoices.CHOICES}
            if template.cable_profile_hint in valid_profiles:
                native_cable_profile = template.cable_profile_hint
        cable = Cable.objects.create(
            label=label or str(template),
            status=LinkStatusChoices.STATUS_CONNECTED,
            profile=native_cable_profile,
        )
        if template.breakout_profile_id:
            set_plugin_breakout_profile_for_cable(cable, template.breakout_profile)

        terminations = []
        for idx, (connector, target) in enumerate(zip(a_connectors, a_termination_targets)):
            ct = CableTermination(
                cable=cable,
                cable_end='A',
                termination=target,
                connector=connector.connector_number,
                positions=list(range(1, connector.position_count + 1)),
            )
            ct.save()
            terminations.append(ct)

        for idx, (connector, target) in enumerate(zip(b_connectors, b_termination_targets)):
            ct = CableTermination(
                cable=cable,
                cable_end='B',
                termination=target,
                connector=connector.connector_number,
                positions=list(range(1, connector.position_count + 1)),
            )
            ct.save()
            terminations.append(ct)

        stamp_record = _create_stamp_record(
            template=template,
            result=cable,
            plan=plan,
            user=user,
        )

        logger.info(
            'Stamped cable assembly %s → Cable pk=%s (%d terminations)',
            template, cable.pk, len(terminations),
        )

    return CableStampResult(
        cable=cable,
        cable_terminations=terminations,
        stamp_record=stamp_record,
    )


# ---------------------------------------------------------------------------
# Passive-device stamp  (shuffle board, patch panel, breakout cassette)
# ---------------------------------------------------------------------------

def stamp_passive_device(
    template: AssemblyTemplate,
    *,
    name: str,
    site: object | None = None,
    location: object | None = None,
    rack: object | None = None,
    position: float | None = None,
    face: str = '',
    device_role: object | None = None,
    plan: DeploymentPlan | None = None,
    user: AbstractUser | None = None,
    dry_run: bool = False,
) -> DeviceStampResult:
    """
    Create a Device with FrontPorts, RearPorts and PortMappings from an AssemblyTemplate.

    If the template has a device_type, the native DeviceType→Device mechanism
    creates component templates automatically. Otherwise ports are created
    manually from the connector templates.

    Parameters
    ----------
    template : AssemblyTemplate
        The assembly template.
    name : str
        Device name.
    site, location, rack, position, face
        Placement parameters.
    device_role : DeviceRole | None
        Required if template.device_type is not set.
    plan : DeploymentPlan | None
        Optional deployment plan for provenance.
    user : AbstractUser | None
        The user performing the stamp.
    dry_run : bool
        If True, validate but do not commit.

    Returns
    -------
    DeviceStampResult
    """
    from dcim.models import Device, FrontPort, RearPort
    from ..port_mapping_compat import PortMapping

    a_connectors = list(template.a_connectors)
    b_connectors = list(template.b_connectors)
    mappings = list(template.mappings.select_related('a_connector', 'b_connector'))

    if dry_run:
        return DeviceStampResult()

    with transaction.atomic():
        if template.device_type:
            device = Device.objects.create(
                name=name,
                device_type=template.device_type,
                role=device_role or template.device_type.default_role,
                site=site,
                location=location,
                rack=rack,
                position=position,
                face=face,
            )
            rear_ports = list(device.rearports.order_by('name'))
            front_ports = list(device.frontports.order_by('name'))
        else:
            if not device_role:
                raise ValueError('device_role is required when template has no device_type')
            raise ValueError(
                'AssemblyTemplate must have a device_type set. '
                'NetBox requires device_type for Device creation.'
            )

        port_mappings = _create_port_mappings(
            device=device,
            mappings=mappings,
            rear_ports_by_connector={rp.name: rp for rp in rear_ports},
            front_ports_by_connector={fp.name: fp for fp in front_ports},
            a_connectors=a_connectors,
            b_connectors=b_connectors,
        )

        stamp_record = _create_stamp_record(
            template=template,
            result=device,
            plan=plan,
            user=user,
        )

        logger.info(
            'Stamped passive device %s → Device pk=%s (%d rear, %d front, %d mappings)',
            template, device.pk, len(rear_ports), len(front_ports), len(port_mappings),
        )

    return DeviceStampResult(
        device=device,
        rear_ports=rear_ports,
        front_ports=front_ports,
        port_mappings=port_mappings,
        stamp_record=stamp_record,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _create_rear_ports(device, a_connectors):
    """Create RearPort objects from A-side connector templates."""
    from dcim.models import RearPort

    rear_ports = []
    for connector in a_connectors:
        label = connector.label or f'A{connector.connector_number}'
        rp = RearPort.objects.create(
            device=device,
            name=label,
            type=_connector_type_to_port_type(connector.connector_type),
            positions=connector.position_count,
        )
        rear_ports.append(rp)
    return rear_ports


def _create_front_ports(device, b_connectors):
    """Create FrontPort objects from B-side connector templates."""
    from dcim.models import FrontPort

    front_ports = []
    for connector in b_connectors:
        label = connector.label or f'B{connector.connector_number}'
        fp = FrontPort.objects.create(
            device=device,
            name=label,
            type=_connector_type_to_port_type(connector.connector_type),
            positions=connector.position_count,
        )
        front_ports.append(fp)
    return front_ports


def _create_port_mappings(device, mappings, rear_ports_by_connector, front_ports_by_connector,
                          a_connectors, b_connectors):
    """Create PortMapping records from AssemblyMappingTemplate rows."""
    from ..port_mapping_compat import PortMapping

    # Build lookup: connector pk → port name
    a_label_map = {c.pk: c.label or f'A{c.connector_number}' for c in a_connectors}
    b_label_map = {c.pk: c.label or f'B{c.connector_number}' for c in b_connectors}

    port_mappings = []
    for m in mappings:
        rear_port_name = a_label_map.get(m.a_connector_id)
        front_port_name = b_label_map.get(m.b_connector_id)
        if not rear_port_name or not front_port_name:
            continue
        rear_port = rear_ports_by_connector.get(rear_port_name)
        front_port = front_ports_by_connector.get(front_port_name)
        if not rear_port or not front_port:
            continue
        pm = PortMapping.objects.create(
            device=device,
            front_port=front_port,
            front_port_position=m.b_position,
            rear_port=rear_port,
            rear_port_position=m.a_position,
        )
        port_mappings.append(pm)
    return port_mappings


def _connector_type_to_port_type(connector_type: str) -> str:
    """Map plugin connector type choices to dcim PortTypeChoices values."""
    mapping = {
        'mpo-8': '8p8c',
        'mpo-12': 'mpo',
        'mpo-16': 'mpo',
        'mpo-24': 'mpo',
        'mtp-16': 'mpo',
        'mtp-24': 'mpo',
        'lc-duplex': 'lc',
        'lc-simplex': 'lc',
        'sc-duplex': 'sc',
        'custom': 'other',
    }
    return mapping.get(connector_type, 'other')


def _create_stamp_record(*, template, result, plan, user):
    """Create a StampRecord linking the template to the stamped result."""
    from ..models import StampRecord

    if plan is None and result is None:
        return None

    template_ct = ContentType.objects.get_for_model(template)
    result_ct = ContentType.objects.get_for_model(result) if result else None

    return StampRecord.objects.create(
        plan=plan,
        template_type=template_ct,
        template_id=template.pk,
        result_type=result_ct,
        result_id=result.pk if result else None,
        stamped_at=timezone.now(),
        stamped_by=user,
        status='stamped',
    )


# ---------------------------------------------------------------------------
# Child-interface stamp  (breakout auto-creation)
# ---------------------------------------------------------------------------

@dataclass
class ChildInterfaceStampResult:
    created: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def create_child_interfaces_from_breakout_spec(
    *,
    device: object,
    breakout_template: object,
    dry_run: bool = False,
) -> ChildInterfaceStampResult:
    """
    Create child Interface objects on *device* according to the
    ``DeviceBreakoutTemplate`` specs.

    For each ``DeviceChildInterfaceSpec`` row:
      - Locate the parent interface on the device by name.
      - Create ``child_count`` child Interface objects with
        ``parent_id`` pointing to the parent.
      - Set the ``fabric_plane`` custom field on each child using
        ``spec.fabric_plane_start + child_index``.

    Already-existing children with matching names are skipped, not
    duplicated (idempotent).

    Parameters
    ----------
    device : dcim.models.Device
        The device on which to create child interfaces.
    breakout_template : DeviceBreakoutTemplate
        The template that owns the ``DeviceChildInterfaceSpec`` rows.
    dry_run : bool
        If True, validate and log but do not commit.

    Returns
    -------
    ChildInterfaceStampResult
    """
    from django.conf import settings
    from dcim.models import Interface

    plugin_config = getattr(settings, 'PLUGINS_CONFIG', {}).get('netbox_plant_graph', {})
    plane_field_name = plugin_config.get('default_plane_field_name', 'fabric_plane')

    result = ChildInterfaceStampResult()

    specs = list(
        breakout_template.child_specs.order_by('sort_order', 'parent_interface_name')
    )
    if not specs:
        return result

    parent_iface_by_name = {
        iface.name: iface
        for iface in Interface.objects.filter(device=device)
    }
    existing_child_names = {
        iface.name
        for iface in Interface.objects.filter(device=device).exclude(parent_id=None)
    }

    for spec in specs:
        parent_iface = parent_iface_by_name.get(spec.parent_interface_name)
        if parent_iface is None:
            logger.warning(
                'create_child_interfaces_from_breakout_spec: '
                'parent interface %r not found on device %s (pk=%s), skipping spec pk=%s',
                spec.parent_interface_name, device.name, device.pk, spec.pk,
            )
            result.skipped.append({'spec_pk': spec.pk, 'reason': 'parent_interface_not_found'})
            continue

        for child_index in range(spec.child_count):
            child_name = spec.generate_child_name(child_index)
            if child_name in existing_child_names:
                result.skipped.append({'spec_pk': spec.pk, 'child_name': child_name, 'reason': 'already_exists'})
                continue

            if dry_run:
                result.created.append({'spec_pk': spec.pk, 'child_name': child_name, 'dry_run': True})
                continue

            plane_number = spec.fabric_plane_start + child_index
            child_iface = Interface(
                device=device,
                parent=parent_iface,
                name=child_name,
                type=spec.child_interface_type,
                speed=spec.child_speed_kbps,
            )
            child_iface.custom_field_data = {plane_field_name: plane_number}
            child_iface.save()

            existing_child_names.add(child_name)
            result.created.append({'spec_pk': spec.pk, 'child_name': child_name, 'interface_pk': child_iface.pk})
            logger.debug(
                'Created child interface %r (pk=%s) under parent %r on device %s',
                child_name, child_iface.pk, spec.parent_interface_name, device.name,
            )

    return result
