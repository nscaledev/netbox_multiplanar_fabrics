from django.core.management.base import BaseCommand

from netbox_plant_graph.services.architecture import ensure_roce_4plane_shuffle_architecture
from netbox_plant_graph.services.stamping import stamp_roce_4plane_mini_fabric


class Command(BaseCommand):
    help = 'Seed the V2 multi-planar architecture fixture and optional mini proof fabric.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--architecture-only',
            action='store_true',
            default=False,
            help='Only seed the architecture, roles, rule sets, transfer patterns, and stamp template.',
        )
        parser.add_argument(
            '--fabric-name',
            default='RoCE 4-plane mini proof',
            help='Name for the mini proof fabric.',
        )
        parser.add_argument(
            '--fabric-slug',
            default='roce-4-plane-mini-proof',
            help='Slug for the mini proof fabric.',
        )

    def handle(self, *args, **options):
        fixture = ensure_roce_4plane_shuffle_architecture()
        self.stdout.write(
            self.style.SUCCESS(
                f'Seeded architecture {fixture.architecture.slug} {fixture.architecture.version} '
                f'with {len(fixture.roles)} roles, {len(fixture.transfer_patterns)} transfer patterns, '
                f'and {len(fixture.allocation_rule_sets)} allocation rule sets.'
            )
        )

        if options['architecture_only']:
            return

        result = stamp_roce_4plane_mini_fabric(
            fabric_name=options['fabric_name'],
            fabric_slug=options['fabric_slug'],
        )
        unresolved = [path for path in result.resolved_paths if not path.path_found]
        if unresolved:
            self.stdout.write(self.style.ERROR(f'Stamped fabric {result.fabric.slug}, but {len(unresolved)} paths failed.'))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Stamped fabric {result.fabric.slug} with {len(result.source_lanes)} source lanes, '
                    f'{len(result.destination_lanes)} destination lanes, and {len(result.resolved_paths)} resolved paths.'
                )
            )
