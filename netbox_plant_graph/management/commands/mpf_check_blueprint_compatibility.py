from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from netbox_plant_graph.services.blueprint_registry import (
    check_blueprint_device_type_compatibility,
    get_default_blueprint_registry,
)


class Command(BaseCommand):
    help = 'Check registered MPF blueprints against the current NetBox DeviceType catalog.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--slug',
            default='',
            help='Optional blueprint slug. When omitted, all registered blueprints are checked.',
        )
        parser.add_argument(
            '--blueprint-version',
            dest='blueprint_version',
            default='',
            help='Optional blueprint version. Requires --slug; defaults to the latest active/deprecated version.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Write the compatibility result as JSON instead of line-oriented text.',
        )

    def handle(self, *args, **options):
        registry = get_default_blueprint_registry()
        slug = options.get('slug') or ''
        version = options.get('blueprint_version') or None
        if version and not slug:
            raise CommandError('--blueprint-version requires --slug.')

        if slug:
            try:
                entries = (registry.get_blueprint(slug, version),)
            except KeyError as exc:
                raise CommandError(str(exc)) from exc
        else:
            entries = registry.list_blueprints(include_retired=True)

        results = tuple(check_blueprint_device_type_compatibility(entry) for entry in entries)
        payload = {
            'checked': len(results),
            'issue_count': sum(len(result.issues) for result in results),
            'error_count': sum(len(result.errors) for result in results),
            'warning_count': sum(len(result.warnings) for result in results),
            'results': [result.to_dict() for result in results],
        }

        if options['json']:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                'MPF blueprint compatibility: '
                f'checked={payload["checked"]} errors={payload["error_count"]} '
                f'warnings={payload["warning_count"]}'
            )
            for result in results:
                if not result.issues:
                    self.stdout.write(f'ok {result.slug} {result.version}')
                    continue
                for issue in result.issues:
                    style = self.style.ERROR if issue.severity == 'error' else self.style.WARNING
                    self.stdout.write(
                        style(
                            f'{issue.severity} {result.slug} {result.version} '
                            f'{issue.code} {issue.path}: {issue.message}'
                        )
                    )

        if payload['error_count']:
            raise CommandError(f'Blueprint compatibility found {payload["error_count"]} error(s).')
