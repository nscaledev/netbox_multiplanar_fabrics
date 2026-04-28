"""
bulk_apply_breakout — D8

Apply a DeviceBreakoutTemplate to one or more existing Devices, creating
child Interface objects that were not created at device-stamp time.

Usage examples::

    # Apply to specific devices by PK:
    python manage.py bulk_apply_breakout \\
        --template gpu-server-800g-4plane \\
        --device-pks 42,43,44

    # Apply to all devices with a given role slug:
    python manage.py bulk_apply_breakout \\
        --template gpu-server-800g-4plane \\
        --role gpu-server

    # Dry run (validate without committing):
    python manage.py bulk_apply_breakout \\
        --template gpu-server-800g-4plane \\
        --role gpu-server \\
        --dry-run

This is the escape hatch for existing environments where child interfaces were
not created during rack_population_stamp (GAP #6 in docs/gap-resolution-plan.md).
"""

import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger('netbox_plant_graph')


class Command(BaseCommand):
    help = (
        'Apply a DeviceBreakoutTemplate to existing devices, creating missing '
        'child interfaces. Idempotent — already-existing children are skipped.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--template',
            required=True,
            metavar='SLUG',
            help='Slug of the DeviceBreakoutTemplate to apply.',
        )
        device_group = parser.add_mutually_exclusive_group(required=True)
        device_group.add_argument(
            '--device-pks',
            metavar='PK[,PK,...]',
            help='Comma-separated list of Device PKs to target.',
        )
        device_group.add_argument(
            '--role',
            metavar='ROLE_SLUG',
            help='Apply to all devices whose role slug matches this value.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Validate and log what would be created without committing.',
        )

    def handle(self, *args, **options):
        from dcim.models import Device
        from netbox_plant_graph.models import DeviceBreakoutTemplate
        from netbox_plant_graph.services.assembly_stamp import create_child_interfaces_from_breakout_spec

        template_slug = options['template']
        dry_run = options['dry_run']

        try:
            template = DeviceBreakoutTemplate.objects.get(slug=template_slug)
        except DeviceBreakoutTemplate.DoesNotExist:
            raise CommandError(f'DeviceBreakoutTemplate with slug {template_slug!r} not found.')

        if options['device_pks']:
            raw_pks = [pk.strip() for pk in options['device_pks'].split(',') if pk.strip()]
            try:
                pk_list = [int(pk) for pk in raw_pks]
            except ValueError as exc:
                raise CommandError(f'Invalid device PK list: {exc}')
            devices = list(Device.objects.filter(pk__in=pk_list))
            missing = set(pk_list) - {d.pk for d in devices}
            if missing:
                raise CommandError(f'Devices not found for PKs: {sorted(missing)}')
        else:
            role_slug = options['role']
            devices = list(Device.objects.filter(role__slug=role_slug))
            if not devices:
                raise CommandError(f'No devices found with role slug {role_slug!r}.')

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'DRY RUN — no changes will be committed.')
            )

        total_created = 0
        total_skipped = 0
        total_devices = 0

        for device in devices:
            result = create_child_interfaces_from_breakout_spec(
                device=device,
                breakout_template=template,
                dry_run=dry_run,
            )
            created_count = len(result.created)
            skipped_count = len(result.skipped)
            total_created += created_count
            total_skipped += skipped_count
            total_devices += 1

            if created_count:
                action = 'would create' if dry_run else 'created'
                self.stdout.write(
                    f'  {device.name} (pk={device.pk}): {action} {created_count} child interface(s)'
                )
            else:
                self.stdout.write(
                    f'  {device.name} (pk={device.pk}): skipped {skipped_count} (already exist or parent missing)'
                )

        action_word = 'Would create' if dry_run else 'Created'
        self.stdout.write(
            self.style.SUCCESS(
                f'\n{action_word} {total_created} child interface(s) across {total_devices} device(s). '
                f'{total_skipped} skipped (already exist or parent interface not found).'
            )
        )
