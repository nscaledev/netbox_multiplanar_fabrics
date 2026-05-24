from __future__ import annotations

import os
from collections import Counter

from django.db import transaction

from netbox_power_plant.choices import DesignStateChoices, TopologyStateChoices
from netbox_power_plant.models import (
    ElectricalNode,
    ElectricalSegment,
    InternalPowerBus,
    InternalPowerBusAttachment,
    PowerHandoffPoint,
    PowerSystem,
)


MAD_SITE_SLUG = os.environ.get('MADISON_SITE_SLUG', 'gs001')
POWER_SYSTEM_NAME = os.environ.get('MADISON_POWER_SYSTEM_NAME', 'GS001 Electrical Plant')
APPLY = os.environ.get('MADISON_POWER_PLANT_ACTIVATE') == '1'


def main():
    power_system = PowerSystem.objects.get(name=POWER_SYSTEM_NAME, site__slug=MAD_SITE_SLUG)
    counters = Counter()

    nodes = ElectricalNode.objects.filter(power_system=power_system)
    segments = ElectricalSegment.objects.filter(power_system=power_system)
    handoffs = PowerHandoffPoint.objects.filter(power_system=power_system)
    buses = InternalPowerBus.objects.filter(power_system=power_system)
    attachments = InternalPowerBusAttachment.objects.filter(internal_power_bus__power_system=power_system)

    counters['nodes_total'] = nodes.count()
    counters['nodes_not_active'] = nodes.exclude(topology_state=TopologyStateChoices.STATE_ACTIVE).count()
    counters['segments_total'] = segments.count()
    counters['segments_not_active'] = segments.exclude(path_state=TopologyStateChoices.STATE_ACTIVE).count()
    counters['handoffs_total'] = handoffs.count()
    counters['handoffs_not_active'] = handoffs.exclude(design_state=DesignStateChoices.STATE_ACTIVE).count()
    counters['buses_total'] = buses.count()
    counters['buses_not_active'] = buses.exclude(design_state=DesignStateChoices.STATE_ACTIVE).count()
    counters['attachments_total'] = attachments.count()
    counters['attachments_not_active'] = attachments.exclude(design_state=DesignStateChoices.STATE_ACTIVE).count()

    if APPLY:
        with transaction.atomic():
            counters['nodes_activated'] = nodes.exclude(topology_state=TopologyStateChoices.STATE_ACTIVE).update(
                topology_state=TopologyStateChoices.STATE_ACTIVE,
                install_state=TopologyStateChoices.STATE_ACTIVE,
            )
            counters['segments_activated'] = segments.exclude(path_state=TopologyStateChoices.STATE_ACTIVE).update(
                path_state=TopologyStateChoices.STATE_ACTIVE,
            )
            counters['handoffs_activated'] = handoffs.exclude(design_state=DesignStateChoices.STATE_ACTIVE).update(
                design_state=DesignStateChoices.STATE_ACTIVE,
            )
            counters['buses_activated'] = buses.exclude(design_state=DesignStateChoices.STATE_ACTIVE).update(
                design_state=DesignStateChoices.STATE_ACTIVE,
            )
            counters['attachments_activated'] = attachments.exclude(design_state=DesignStateChoices.STATE_ACTIVE).update(
                design_state=DesignStateChoices.STATE_ACTIVE,
            )

    print('Madison power plant topology activation complete.')
    print(f'apply={APPLY}')
    print(f'site={MAD_SITE_SLUG}')
    print(f'power_system={POWER_SYSTEM_NAME}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')


main()
