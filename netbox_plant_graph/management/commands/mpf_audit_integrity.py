from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from netbox_plant_graph.models import Fabric
from netbox_plant_graph.services.topology_integrity import (
    audit_topology_integrity,
    format_topology_integrity_report_text,
    persist_topology_integrity_report,
)


class Command(BaseCommand):
    help = 'Audit V2 plugin-native topology integrity before path and blast-radius analysis.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fabric',
            default=None,
            help='Optional fabric ID or slug. When omitted, all fabrics are audited.',
        )
        parser.add_argument(
            '--format',
            choices=('text', 'json'),
            default='text',
            help='Output format.',
        )
        parser.add_argument(
            '--fail-on',
            choices=('none', 'info', 'warning', 'error', 'critical'),
            default='none',
            help='Raise CommandError when findings at or above this severity are present.',
        )
        parser.add_argument(
            '--indent',
            type=int,
            default=2,
            help='JSON indentation level.',
        )
        parser.add_argument(
            '--persist-operation-run',
            action='store_true',
            help='Persist the audit report snapshot to an OperationRun row.',
        )
        parser.add_argument(
            '--operation-run-id',
            type=int,
            default=None,
            help='Update this OperationRun row with the audit report instead of creating a new row.',
        )

    def handle(self, *args, **options):
        fabric = self._fabric_from_option(options.get('fabric'))
        report = audit_topology_integrity(fabric=fabric)
        persisted_run = None

        if options['persist_operation_run'] or options['operation_run_id'] is not None:
            try:
                persisted_run = persist_topology_integrity_report(
                    report,
                    operation_run=options['operation_run_id'],
                    parameters={'trigger': 'mpf_audit_integrity'},
                )
            except Exception as exc:
                raise CommandError(f'Unable to persist topology integrity audit report: {exc}') from exc

        if options['format'] == 'json':
            payload = report.as_dict()
            if persisted_run is not None:
                payload['operation_run'] = {
                    'id': persisted_run.pk,
                    'profile': persisted_run.profile,
                    'status': persisted_run.status,
                }
            self.stdout.write(json.dumps(payload, indent=options['indent'], sort_keys=True))
        else:
            self.stdout.write(format_topology_integrity_report_text(report))
            if persisted_run is not None:
                self.stdout.write(f'Persisted OperationRun #{persisted_run.pk}.')

        fail_on = options['fail_on']
        if fail_on != 'none' and report.has_findings_at_or_above(fail_on):
            raise CommandError(
                f'Topology integrity audit found findings at or above {fail_on} severity.'
            )

    def _fabric_from_option(self, raw_value: str | None) -> Fabric | None:
        if not raw_value:
            return None
        normalized_value = str(raw_value)
        lookup = {'pk': int(normalized_value)} if normalized_value.isdigit() else {'slug': normalized_value}
        fabric = Fabric.objects.filter(**lookup).first()
        if fabric is None:
            raise CommandError(f'Fabric {normalized_value!r} does not exist.')
        return fabric
