from __future__ import annotations

from collections import Counter

from django.db import transaction

from dcim.models import Device, DeviceBay, DeviceRole, DeviceType, FrontPort, RearPort
from django.contrib.contenttypes.models import ContentType

from extras.models import TaggedItem
from tenancy.models import Tenant


MAD_SITE_SLUG = 'mad-1'
NSCALE_TENANT_SLUG = 'nscale'
PLANNED_STATUS = 'planned'
BOX_TYPE_SLUG = 'shuffle-box-3tray-18cassette'
TRAY_TYPE_SLUG = 'shuffle-tray-6cassette'
CASSETTE_TYPE_SLUG = 'shuffle-cassette-2x2-mpo'
CASSETTE_ROLE_SLUG = 'shuffle-cassette'
SOURCE_MARKER = 'madison_shuffle_full_cassette_capacity_v1'
BATCH_SIZE = 5000


def chunks(items, size=BATCH_SIZE):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def row_tags(device):
    return [tag for tag in device.tags.all() if tag.slug.startswith('nscale-row-id-')]


def cassette_name(tray_name: str, bay_name: str) -> str:
    return f'{tray_name}-{bay_name}'


def main() -> None:
    counters = Counter()
    tenant = Tenant.objects.get(slug=NSCALE_TENANT_SLUG)
    cassette_type = DeviceType.objects.get(slug=CASSETTE_TYPE_SLUG)
    cassette_role = DeviceRole.objects.get(slug=CASSETTE_ROLE_SLUG)

    trays = list(
        Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=TRAY_TYPE_SLUG)
        .select_related('site')
        .prefetch_related('tags')
        .order_by('name')
    )
    tray_by_id = {tray.pk: tray for tray in trays}
    tray_bays = list(
        DeviceBay.objects.filter(device_id__in=tray_by_id.keys(), name__startswith='cassette-')
        .select_related('installed_device')
        .order_by('device_id', 'name')
    )

    missing_specs = []
    for bay in tray_bays:
        if bay.installed_device_id is not None:
            continue
        tray = tray_by_id[bay.device_id]
        missing_specs.append((tray, bay, cassette_name(tray.name, bay.name)))

    with transaction.atomic():
        to_create = [
            Device(
                site=tray.site,
                name=name,
                device_type=cassette_type,
                role=cassette_role,
                tenant=tenant,
                status=PLANNED_STATUS,
                description=f'Capacity cassette in {tray.name} {bay.name}.',
                comments=(
                    '<!-- madison-shuffle-full-cassette-capacity:start -->\n'
                    'Created to expose all 18 physical cassette positions per shuffle box for the corrected Madison fiber worksheet.\n'
                    f'- tray: {tray.name}\n'
                    f'- bay: {bay.name}\n'
                    '<!-- madison-shuffle-full-cassette-capacity:end -->'
                ),
                local_context_data={
                    SOURCE_MARKER: {
                        'tray': tray.name,
                        'bay': bay.name,
                        'modeled_status': 'planned',
                    }
                },
            )
            for tray, bay, name in missing_specs
        ]
        for batch in chunks(to_create):
            Device.objects.bulk_create(batch, batch_size=BATCH_SIZE, ignore_conflicts=True)
            counters['cassettes_created_attempted'] += len(batch)

        created_names = [name for _, _, name in missing_specs]
        cassettes = {
            device.name: device
            for device in Device.objects.filter(site__slug=MAD_SITE_SLUG, name__in=created_names)
        }

        bay_updates = []
        for tray, bay, name in missing_specs:
            cassette = cassettes[name]
            if bay.installed_device_id != cassette.pk:
                bay.installed_device_id = cassette.pk
                bay_updates.append(bay)
        DeviceBay.objects.bulk_update(bay_updates, ['installed_device'], batch_size=BATCH_SIZE)
        counters['cassettes_installed'] += len(bay_updates)

        device_ct = ContentType.objects.get_for_model(Device)
        tag_links = []
        for tray, _, name in missing_specs:
            cassette = cassettes[name]
            for tag in row_tags(tray):
                tag_links.append(TaggedItem(content_type=device_ct, object_id=cassette.pk, tag=tag))
        for batch in chunks(tag_links):
            TaggedItem.objects.bulk_create(batch, batch_size=BATCH_SIZE, ignore_conflicts=True)
            counters['row_tag_links_attempted'] += len(batch)

        rear_to_create = []
        for cassette in cassettes.values():
            for index in range(1, 5):
                rear_to_create.append(
                    RearPort(
                        device=cassette,
                        name=f'rear-mpo-{index:02d}',
                        type='mpo',
                        positions=1,
                        description='MPO rear-side termination staged from Madison passive plant sources.',
                        color='2196f3',
                    )
                )
        for batch in chunks(rear_to_create):
            RearPort.objects.bulk_create(batch, batch_size=BATCH_SIZE, ignore_conflicts=True)
            counters['rear_ports_created_attempted'] += len(batch)

        rear_ports = {
            (port.device_id, port.name): port
            for port in RearPort.objects.filter(device_id__in=[device.pk for device in cassettes.values()])
        }
        front_to_create = []
        for cassette in cassettes.values():
            for index in range(1, 5):
                front_to_create.append(
                    FrontPort(
                        device=cassette,
                        name=f'front-mpo-{index:02d}',
                        type='mpo',
                        rear_port=rear_ports[(cassette.pk, f'rear-mpo-{index:02d}')],
                        rear_port_position=1,
                        description='MPO front-side termination staged from Madison passive plant sources.',
                        color='4caf50',
                    )
                )
        for batch in chunks(front_to_create):
            FrontPort.objects.bulk_create(batch, batch_size=BATCH_SIZE, ignore_conflicts=True)
            counters['front_ports_created_attempted'] += len(batch)

    print('Madison shuffle full cassette capacity seed complete.')
    print(f'shuffle_boxes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=BOX_TYPE_SLUG).count()}')
    print(f'shuffle_trays={len(trays)}')
    print(f'missing_cassette_positions_filled={len(missing_specs)}')
    print(f'shuffle_cassettes={Device.objects.filter(site__slug=MAD_SITE_SLUG, device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassette_front_ports={FrontPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    print(f'shuffle_cassette_rear_ports={RearPort.objects.filter(device__site__slug=MAD_SITE_SLUG, device__device_type__slug=CASSETTE_TYPE_SLUG).count()}')
    for key in sorted(counters):
        print(f'{key}={counters[key]}')


main()
