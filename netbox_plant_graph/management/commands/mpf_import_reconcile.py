import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from netbox_plant_graph.services.imports import reconcile_import_payload


class Command(BaseCommand):
    help = 'Dry-run or apply a plugin-native V2 import reconciliation JSON payload.'

    def add_arguments(self, parser):
        parser.add_argument('json_file', help='Path to the import reconciliation JSON file.')
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Apply create/update operations. Omit this flag for dry-run mode.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Write the reconciliation result as JSON instead of line-oriented text.',
        )
        parser.add_argument(
            '--fail-on-conflict',
            action='store_true',
            default=False,
            help='Exit with an error if any item reports a conflict.',
        )

    def handle(self, *args, **options):
        json_path = Path(options['json_file'])
        try:
            with json_path.open() as handle:
                payload = json.load(handle)
        except OSError as exc:
            raise CommandError(f'Unable to read {json_path}: {exc}') from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f'Invalid JSON in {json_path}: {exc}') from exc

        try:
            plan = reconcile_import_payload(payload, apply=options['apply'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if options['json']:
            self.stdout.write(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
        else:
            mode = 'apply' if plan.applied else 'dry-run'
            summary = plan.summary
            self.stdout.write(
                f'MPF import reconciliation {mode}: '
                f'total={summary.total} create={summary.create} update={summary.update} '
                f'skip={summary.skip} conflict={summary.conflict}'
            )
            for diff in plan.diffs:
                line = self.style.ERROR(diff.message) if diff.is_conflict else diff.message
                self.stdout.write(line)

        if options['fail_on_conflict'] and plan.has_conflicts:
            raise CommandError(f'Import reconciliation found {plan.summary.conflict} conflict(s).')
