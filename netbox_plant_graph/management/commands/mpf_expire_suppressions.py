from __future__ import annotations

from django.core.management.base import BaseCommand

from netbox_plant_graph.services.audit import expire_suppressions


class Command(BaseCommand):
    help = 'Expire active suppression rules whose expires_at is in the past.'

    def handle(self, *args, **options):
        count = expire_suppressions()
        self.stdout.write(self.style.SUCCESS(f'Expired {count} suppression rule(s).'))
