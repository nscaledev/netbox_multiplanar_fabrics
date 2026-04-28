"""
Rack population stamp service.

Creates Devices in a Rack from a RackPopulationTemplate, optionally cascading
into assembly stamps for passive devices.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from ..models import DeploymentPlan, RackPopulationTemplate

logger = logging.getLogger(__name__)


@dataclass
class RackPopulationStampResult:
    devices: list = field(default_factory=list)
    assembly_results: list = field(default_factory=list)
    stamp_records: list = field(default_factory=list)


def stamp_rack_population(
    template: RackPopulationTemplate,
    rack: object,
    *,
    site: object | None = None,
    plan: DeploymentPlan | None = None,
    user: AbstractUser | None = None,
    dry_run: bool = False,
    variables: dict | None = None,
) -> RackPopulationStampResult:
    """
    Populate a Rack from a RackPopulationTemplate.

    For each RackPopulationSlot, creates a Device at the specified U position
    and face. If the slot has an assembly_template, cascades into a passive-
    device stamp.

    Parameters
    ----------
    template : RackPopulationTemplate
        The rack population template.
    rack : Rack
        The target rack to populate.
    site : Site | None
        Site for the devices (defaults to rack.site).
    plan : DeploymentPlan | None
        Optional deployment plan for provenance.
    user : AbstractUser | None
        The user performing the stamp.
    dry_run : bool
        If True, validate but do not commit.
    """
    from dcim.models import Device

    from ..models import StampRecord
    from .spatial_stamp import render_name_pattern

    slots = list(template.slots.select_related(
        'device_type', 'device_role', 'assembly_template', 'breakout_template',
    ).order_by('sort_order', 'u_position'))

    if dry_run:
        return RackPopulationStampResult()

    result = RackPopulationStampResult()
    device_site = site or rack.site

    # W5: plane multiplier — stamp once per plane if template declares it.
    plane_multiplier = getattr(template, 'plane_multiplier', None) or 1

    with transaction.atomic():
        for plane in range(1, plane_multiplier + 1):
            plane_vars: dict = dict(variables or {})
            if plane_multiplier > 1:
                plane_vars['plane'] = plane

            for idx, slot in enumerate(slots):
                name = render_name_pattern(
                    slot.name_pattern,
                    index=idx + 1,
                    parent_name=rack.name,
                    variables=plane_vars if plane_vars else None,
                )

                # Cascade into assembly stamp for passive devices — the assembly
                # stamp creates the Device with ports and port mappings, so we skip
                # creating a bare Device here to avoid duplicate position conflicts.
                stamped_device = None
                if slot.assembly_template:
                    from .assembly_stamp import stamp_passive_device

                    assembly_result = stamp_passive_device(
                        template=slot.assembly_template,
                        name=name,
                        site=device_site,
                        location=rack.location,
                        rack=rack,
                        position=float(slot.u_position),
                        face=slot.face,
                        device_role=slot.device_role,
                        plan=plan,
                        user=user,
                    )
                    stamped_device = assembly_result.device
                    if assembly_result.device:
                        result.devices.append(assembly_result.device)
                    result.assembly_results.append(assembly_result)
                    if assembly_result.stamp_record:
                        result.stamp_records.append(assembly_result.stamp_record)
                else:
                    device = Device.objects.create(
                        name=name,
                        device_type=slot.device_type,
                        role=slot.device_role,
                        site=device_site,
                        location=rack.location,
                        rack=rack,
                        position=float(slot.u_position),
                        face=slot.face,
                    )
                    stamped_device = device
                    result.devices.append(device)

                # If the slot has a breakout_template, auto-create child interfaces
                # on the device that was just stamped.
                if slot.breakout_template and stamped_device is not None:
                    from .assembly_stamp import create_child_interfaces_from_breakout_spec
                    create_child_interfaces_from_breakout_spec(
                        device=stamped_device,
                        breakout_template=slot.breakout_template,
                        dry_run=dry_run,
                    )

        # Record rack-population provenance
        if plan:
            template_ct = ContentType.objects.get_for_model(template)
            rack_ct = ContentType.objects.get_for_model(rack)
            sr = StampRecord.objects.create(
                plan=plan,
                template_type=template_ct,
                template_id=template.pk,
                result_type=rack_ct,
                result_id=rack.pk,
                stamped_at=timezone.now(),
                stamped_by=user,
                status='stamped',
            )
            result.stamp_records.append(sr)

    logger.info(
        'Stamped rack population %s → Rack %s (%d devices, %d assembly cascades)',
        template, rack, len(result.devices), len(result.assembly_results),
    )
    return result
