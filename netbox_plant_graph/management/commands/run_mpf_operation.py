from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from netbox_plant_graph.models import Fabric
from netbox_plant_graph.services.operations import execute_operation_profile


class Command(BaseCommand):
    help = 'Run a multiplanar-fabrics operation profile.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--profile',
            required=True,
            choices=('generic_roce', 'madison_default'),
            help='Operation profile name.',
        )
        parser.add_argument(
            '--fabric',
            type=int,
            default=None,
            help='Optional fabric ID.',
        )
        parser.add_argument(
            '--parameters',
            default='{}',
            help='JSON object parameters.',
        )

    def handle(self, *args, **options):
        fabric = None
        fabric_id = options.get('fabric')
        if fabric_id is not None:
            fabric = Fabric.objects.filter(pk=fabric_id).first()
            if fabric is None:
                raise CommandError(f'Fabric {fabric_id} does not exist.')

        try:
            parameters = json.loads(options['parameters'])
        except json.JSONDecodeError as exc:
            raise CommandError(f'Invalid --parameters JSON: {exc}') from exc
        if not isinstance(parameters, dict):
            raise CommandError('--parameters must decode to a JSON object.')

        execution = execute_operation_profile(
            profile=options['profile'],
            fabric=fabric,
            parameters=parameters,
            actor=None,
        )
        run = execution.run
        if execution.reused_existing:
            self.stdout.write(self.style.WARNING(f'Reused run #{run.pk} ({run.profile}).'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Completed run #{run.pk} ({run.profile}).'))
