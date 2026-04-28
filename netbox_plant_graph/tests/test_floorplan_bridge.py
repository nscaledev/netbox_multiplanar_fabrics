from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from dcim.models import Location, Rack, Site

from netbox_plant_graph.services import floorplan_bridge


class _FakeFloorplanQuerySet(list):
    def first(self):
        return self[0] if self else None


class _FakeFloorplanManager:
    def __init__(self):
        self.instances = []

    def filter(self, **kwargs):
        return _FakeFloorplanQuerySet(
            instance
            for instance in self.instances
            if all(getattr(instance, key) == value for key, value in kwargs.items())
        )

    def get_or_create(self, **kwargs):
        existing = self.filter(**kwargs).first()
        if existing is not None:
            return existing, False
        instance = _FakeFloorplan(**kwargs)
        self.instances.append(instance)
        return instance, True


class _FakeFloorplan:
    objects = _FakeFloorplanManager()
    _next_pk = 1

    def __init__(self, site=None, location=None):
        self.pk = _FakeFloorplan._next_pk
        _FakeFloorplan._next_pk += 1
        self.site = site
        self.location = location
        self.canvas = {}
        self.save_calls = 0

    def save(self):
        self.save_calls += 1


class FloorplanBridgeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Bridge Site', slug='bridge-site')
        cls.location = Location.objects.create(name='Bridge Hall', slug='bridge-hall', site=cls.site)
        cls.rack = Rack.objects.create(name='Bridge Rack', site=cls.site, location=cls.location)

    def setUp(self):
        _FakeFloorplan.objects = _FakeFloorplanManager()
        _FakeFloorplan._next_pk = 1
        self.floorplan_class_patch = patch.object(
            floorplan_bridge,
            '_get_floorplan_model_class',
            return_value=_FakeFloorplan,
        )
        self.floorplan_class_patch.start()
        self.addCleanup(self.floorplan_class_patch.stop)

    def test_get_floorplan_for_scope_returns_existing_site_floorplan(self):
        created, _ = _FakeFloorplan.objects.get_or_create(site=self.site)

        floorplan = floorplan_bridge.get_floorplan_for_scope(self.site)

        self.assertEqual(floorplan.pk, created.pk)

    def test_ensure_floorplan_for_scope_creates_location_floorplan(self):
        result = floorplan_bridge.ensure_floorplan_for_scope(self.location)

        self.assertTrue(result.created)
        self.assertEqual(result.floorplan.location, self.location)

    def test_get_floorplan_urls_for_scope_builds_add_and_edit_urls(self):
        ensured = floorplan_bridge.ensure_floorplan_for_scope(self.site)

        with patch.object(floorplan_bridge, 'reverse') as mock_reverse:
            mock_reverse.side_effect = lambda name, kwargs=None: (
                f"/{name}/{kwargs['pk']}/" if kwargs else f'/{name}/'
            )
            urls = floorplan_bridge.get_floorplan_urls_for_scope(self.site)

        self.assertEqual(urls.add_url, f"/plugins:netbox_floorplan:floorplan_add/?site={self.site.pk}")
        self.assertEqual(urls.edit_url, f'/plugins:netbox_floorplan:floorplan_edit/{ensured.floorplan.pk}/')
        self.assertEqual(urls.object_url, urls.edit_url)

    def test_sync_rack_placements_adds_new_managed_rack_object(self):
        placement = SimpleNamespace(position_x=1.5, position_y=2.0, coordinate_unit='meters', orientation=None)

        result = floorplan_bridge.sync_rack_placements_to_floorplan(
            self.site,
            [(self.rack, placement)],
        )

        self.assertTrue(result.created_floorplan)
        self.assertEqual(result.created_objects, 1)
        self.assertEqual(result.updated_objects, 0)
        self.assertEqual(result.skipped_manual_override, 0)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.floorplan.canvas['objects']), 1)
        canvas_object = result.floorplan.canvas['objects'][0]
        self.assertEqual(canvas_object['custom_meta']['object_id'], self.rack.pk)
        self.assertEqual(canvas_object['custom_meta']['managed_by'], floorplan_bridge.MANAGED_BY)

    def test_sync_rack_placements_updates_existing_managed_object_by_rack_id(self):
        ensured = floorplan_bridge.ensure_floorplan_for_scope(self.site)
        ensured.floorplan.canvas = {
            'objects': [
                {
                    'type': 'group',
                    'left': 10,
                    'top': 20,
                    'custom_meta': {
                        'object_type': 'rack',
                        'object_id': self.rack.pk,
                        'managed_by': floorplan_bridge.MANAGED_BY,
                        'layout_manual_override': False,
                    },
                    'objects': [],
                }
            ]
        }
        placement = SimpleNamespace(position_x=3.0, position_y=4.0, coordinate_unit='meters', orientation=15)

        result = floorplan_bridge.sync_rack_placements_to_floorplan(
            self.site,
            [(self.rack, placement)],
        )

        self.assertFalse(result.created_floorplan)
        self.assertEqual(result.created_objects, 0)
        self.assertEqual(result.updated_objects, 1)
        self.assertEqual(result.floorplan.canvas['objects'][0]['left'], 300.0)
        self.assertEqual(result.floorplan.canvas['objects'][0]['angle'], 15.0)

    def test_sync_rack_placements_skips_manual_override_object(self):
        ensured = floorplan_bridge.ensure_floorplan_for_scope(self.site)
        ensured.floorplan.canvas = {
            'objects': [
                {
                    'type': 'group',
                    'left': 10,
                    'top': 20,
                    'custom_meta': {
                        'object_type': 'rack',
                        'object_id': self.rack.pk,
                        'managed_by': floorplan_bridge.MANAGED_BY,
                        'layout_manual_override': True,
                    },
                    'objects': [],
                }
            ]
        }
        placement = SimpleNamespace(position_x=8.0, position_y=9.0, coordinate_unit='meters', orientation=None)

        result = floorplan_bridge.sync_rack_placements_to_floorplan(
            self.site,
            [(self.rack, placement)],
        )

        self.assertEqual(result.updated_objects, 0)
        self.assertEqual(result.skipped_manual_override, 1)
        self.assertEqual(result.floorplan.canvas['objects'][0]['left'], 10)

    def test_sync_rack_placements_rejects_unsupported_scope(self):
        placement = SimpleNamespace(position_x=1, position_y=1, coordinate_unit='meters', orientation=None)

        with self.assertRaises(TypeError):
            floorplan_bridge.sync_rack_placements_to_floorplan(
                object(),
                [(self.rack, placement)],
            )

    def test_reconcile_floorplan_updates_existing_spatial_placement_and_preserves_z(self):
        from netbox_plant_graph.models import SpatialPlacement

        rack_ct = ContentType.objects.get_for_model(Rack)
        site_ct = ContentType.objects.get_for_model(Site)
        placement = SpatialPlacement.objects.create(
            target_type=rack_ct,
            target_id=self.rack.pk,
            reference_frame_type=site_ct,
            reference_frame_id=self.site.pk,
            position_x=Decimal('1.000'),
            position_y=Decimal('2.000'),
            position_z=Decimal('7.500'),
            orientation=Decimal('5.00'),
            coordinate_unit='meters',
        )
        ensured = floorplan_bridge.ensure_floorplan_for_scope(self.site)
        ensured.floorplan.canvas = {
            'objects': [
                {
                    'type': 'group',
                    'left': 300,
                    'top': 400,
                    'angle': 15,
                    'custom_meta': {
                        'object_type': 'rack',
                        'object_id': self.rack.pk,
                        'managed_by': floorplan_bridge.MANAGED_BY,
                        'layout_manual_override': True,
                    },
                    'objects': [],
                }
            ]
        }

        result = floorplan_bridge.reconcile_floorplan_to_placements(self.site)

        placement.refresh_from_db()
        self.assertEqual(result.updated_placements, 1)
        self.assertEqual(result.created_placements, 0)
        self.assertEqual(placement.position_x, Decimal('3.000'))
        self.assertEqual(placement.position_y, Decimal('4.000'))
        self.assertEqual(placement.position_z, Decimal('7.500'))
        self.assertEqual(placement.orientation, Decimal('15.00'))

    def test_reconcile_floorplan_can_create_missing_spatial_placement_when_requested(self):
        from netbox_plant_graph.models import SpatialPlacement

        rack_ct = ContentType.objects.get_for_model(Rack)
        ensured = floorplan_bridge.ensure_floorplan_for_scope(self.site)
        ensured.floorplan.canvas = {
            'objects': [
                {
                    'type': 'group',
                    'left': 150,
                    'top': 250,
                    'angle': 45,
                    'custom_meta': {
                        'object_type': 'rack',
                        'object_id': self.rack.pk,
                        'managed_by': floorplan_bridge.MANAGED_BY,
                    },
                    'objects': [],
                }
            ]
        }

        result = floorplan_bridge.reconcile_floorplan_to_placements(self.site, create_missing=True)

        placement = SpatialPlacement.objects.get(target_type=rack_ct, target_id=self.rack.pk)
        self.assertEqual(result.updated_placements, 0)
        self.assertEqual(result.created_placements, 1)
        self.assertEqual(placement.position_x, Decimal('1.500'))
        self.assertEqual(placement.position_y, Decimal('2.500'))
        self.assertEqual(placement.position_z, Decimal('0.000'))
        self.assertEqual(placement.orientation, Decimal('45.00'))

    def test_reconcile_floorplan_skips_non_managed_rack_objects(self):
        from netbox_plant_graph.models import SpatialPlacement

        rack_ct = ContentType.objects.get_for_model(Rack)
        site_ct = ContentType.objects.get_for_model(Site)
        placement = SpatialPlacement.objects.create(
            target_type=rack_ct,
            target_id=self.rack.pk,
            reference_frame_type=site_ct,
            reference_frame_id=self.site.pk,
            position_x=Decimal('1.000'),
            position_y=Decimal('2.000'),
            position_z=Decimal('3.000'),
            orientation=Decimal('10.00'),
            coordinate_unit='meters',
        )
        ensured = floorplan_bridge.ensure_floorplan_for_scope(self.site)
        ensured.floorplan.canvas = {
            'objects': [
                {
                    'type': 'group',
                    'left': 900,
                    'top': 800,
                    'angle': 90,
                    'custom_meta': {
                        'object_type': 'rack',
                        'object_id': self.rack.pk,
                        'managed_by': 'other-plugin',
                    },
                    'objects': [],
                }
            ]
        }

        result = floorplan_bridge.reconcile_floorplan_to_placements(self.site)

        placement.refresh_from_db()
        self.assertEqual(result.updated_placements, 0)
        self.assertEqual(result.skipped_unmanaged, 1)
        self.assertEqual(placement.position_x, Decimal('1.000'))
        self.assertEqual(placement.orientation, Decimal('10.00'))
