"""
Tests for planning models, stamp services, and plan execution.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    FrontPort,
    FrontPortTemplate,
    Location,
    Manufacturer,
    Rack,
    RackType,
    RearPort,
    RearPortTemplate,
    Site,
)

from netbox_plant_graph.models import (
    AssemblyConnectorTemplate,
    AssemblyMappingTemplate,
    AssemblyTemplate,
    DeploymentPlan,
    Fabric,
    RackPopulationSlot,
    RackPopulationTemplate,
    SpatialPlacement,
    SpatialTemplate,
    SpatialTemplateNode,
    StampRecord,
)
from netbox_plant_graph.services.spatial_stamp import render_name_pattern


def create_front_port_template(**kwargs):
    field_names = {field.name for field in FrontPortTemplate._meta.fields}
    front_positions = kwargs.get('positions', 1)
    if 'positions' in kwargs and 'positions' not in field_names:
        kwargs.pop('positions')
    if 'rear_port' in field_names and 'rear_port' not in kwargs:
        rear_port = RearPortTemplate.objects.filter(device_type=kwargs['device_type']).order_by('pk').first()
        if rear_port is None:
            rear_port = RearPortTemplate.objects.create(
                device_type=kwargs['device_type'],
                name=f"{kwargs['name']}-rear",
                type=kwargs.get('type', 'other'),
                positions=front_positions or 1,
            )
        kwargs['rear_port'] = rear_port
        kwargs.setdefault('rear_port_position', 1)
    return FrontPortTemplate.objects.create(**kwargs)


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------


class AssemblyConnectorTemplateValidationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.template = AssemblyTemplate.objects.create(
            name='Validation Template',
            slug='validation-template',
            assembly_type='shuffle_trunk',
        )

    def test_position_count_must_be_positive(self):
        connector = AssemblyConnectorTemplate(
            template=self.template,
            side='A',
            connector_number=1,
            connector_type='mpo-12',
            position_count=0,
        )
        with self.assertRaises(ValidationError):
            connector.full_clean()


class AssemblyMappingTemplateValidationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.template = AssemblyTemplate.objects.create(
            name='Mapping Validation',
            slug='mapping-validation',
            assembly_type='shuffle_trunk',
        )
        cls.a_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=8,
        )
        cls.b_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.template, side='B', connector_number=1,
            connector_type='mpo-12', position_count=8,
        )

    def test_a_connector_must_be_side_a(self):
        mapping = AssemblyMappingTemplate(
            template=self.template,
            a_connector=self.b_conn,
            a_position=1,
            b_connector=self.b_conn,
            b_position=1,
        )
        with self.assertRaises(ValidationError):
            mapping.full_clean()

    def test_b_connector_must_be_side_b(self):
        mapping = AssemblyMappingTemplate(
            template=self.template,
            a_connector=self.a_conn,
            a_position=1,
            b_connector=self.a_conn,
            b_position=1,
        )
        with self.assertRaises(ValidationError):
            mapping.full_clean()

    def test_position_exceeds_connector_capacity(self):
        mapping = AssemblyMappingTemplate(
            template=self.template,
            a_connector=self.a_conn,
            a_position=99,
            b_connector=self.b_conn,
            b_position=1,
        )
        with self.assertRaises(ValidationError):
            mapping.full_clean()


class DeploymentPlanModelTestCase(TestCase):
    def test_deployment_plan_stringifies_as_name(self):
        plan = DeploymentPlan.objects.create(name='Test Plan')
        self.assertEqual(str(plan), 'Test Plan')

    def test_stamp_record_stringifies_with_status(self):
        plan = DeploymentPlan.objects.create(name='SR Plan')
        template = AssemblyTemplate.objects.create(
            name='SR Template', slug='sr-template', assembly_type='shuffle_trunk',
        )
        sr = StampRecord.objects.create(
            plan=plan,
            template_type=ContentType.objects.get_for_model(template),
            template_id=template.pk,
            status='pending',
        )
        self.assertIn('Pending', str(sr))


class RackPopulationTemplateModelTestCase(TestCase):
    def test_rack_population_template_stringifies_as_name(self):
        rpt = RackPopulationTemplate.objects.create(
            name='Pop Template', slug='pop-template',
        )
        self.assertEqual(str(rpt), 'Pop Template')

    def test_slot_unique_constraint_on_position_and_face(self):
        manufacturer = Manufacturer.objects.create(name='RackPopMfr', slug='rackpopmfr')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='RPT Model')
        device_role = DeviceRole.objects.create(name='RPT Role', slug='rpt-role')
        rpt = RackPopulationTemplate.objects.create(
            name='Unique Slot Test', slug='unique-slot-test',
        )
        RackPopulationSlot.objects.create(
            template=rpt, u_position=1, face='front',
            device_type=device_type, device_role=device_role,
            name_pattern='Slot-{index}',
        )
        with self.assertRaises(Exception):
            RackPopulationSlot.objects.create(
                template=rpt, u_position=1, face='front',
                device_type=device_type, device_role=device_role,
                name_pattern='Slot-{index}-dup',
            )


class SpatialModelsTestCase(TestCase):
    def test_spatial_template_stringifies_as_name(self):
        t = SpatialTemplate.objects.create(
            name='Spatial T', slug='spatial-t', root_node_type='building',
        )
        self.assertEqual(str(t), 'Spatial T')

    def test_spatial_template_node_stringifies_as_name_pattern(self):
        t = SpatialTemplate.objects.create(
            name='Spatial T2', slug='spatial-t2', root_node_type='building',
        )
        node = SpatialTemplateNode.objects.create(
            template=t, name_pattern='Hall-{index}', node_type='hall', quantity=2,
        )
        self.assertEqual(str(node), 'Hall-{index}')


# ---------------------------------------------------------------------------
# render_name_pattern tests
# ---------------------------------------------------------------------------


class RenderNamePatternTestCase(TestCase):
    def test_index_substitution(self):
        self.assertEqual(render_name_pattern('Row-{index}', index=3), 'Row-3')

    def test_letter_substitution(self):
        self.assertEqual(render_name_pattern('Hall-{letter}', index=1), 'Hall-A')
        self.assertEqual(render_name_pattern('Hall-{letter}', index=3), 'Hall-C')

    def test_row_substitution(self):
        self.assertEqual(render_name_pattern('Row-{row}', index=5), 'Row-05')

    def test_parent_name_substitution(self):
        self.assertEqual(
            render_name_pattern('{parent_name}-Sub-{index}', index=1, parent_name='DC1'),
            'DC1-Sub-1',
        )

    def test_no_tokens_returns_literal(self):
        self.assertEqual(render_name_pattern('Literal', index=1), 'Literal')


# ---------------------------------------------------------------------------
# Stamp service tests
# ---------------------------------------------------------------------------


class SpatialStampTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Stamp Site', slug='stamp-site')

    def _make_template(self):
        template = SpatialTemplate.objects.create(
            name='DC Layout', slug='dc-layout', root_node_type='building',
        )
        hall_node = SpatialTemplateNode.objects.create(
            template=template, name_pattern='Hall-{letter}', node_type='hall',
            quantity=2, sort_order=0, position_x=0, position_y=0, position_z=0,
            position_x_stride=10,
        )
        SpatialTemplateNode.objects.create(
            template=template, parent=hall_node,
            name_pattern='{parent_name}-Row-{row}', node_type='row',
            quantity=2, sort_order=0, position_x=0, position_y=0, position_z=0,
            position_y_stride=3,
        )
        return template

    def test_stamp_creates_locations(self):
        from netbox_plant_graph.services import stamp_spatial_template

        template = self._make_template()
        result = stamp_spatial_template(template, self.site)

        # 2 halls + 2×2 rows = 6 locations
        self.assertEqual(len(result.locations), 6)
        self.assertEqual(len(result.racks), 0)
        self.assertEqual(len(result.placements), 6)

        hall_names = sorted(loc.name for loc in result.locations if 'Hall' in loc.name and 'Row' not in loc.name)
        self.assertEqual(hall_names, ['Hall-A', 'Hall-B'])

        row_names = sorted(loc.name for loc in result.locations if 'Row' in loc.name)
        self.assertEqual(row_names, [
            'Hall-A-Row-01', 'Hall-A-Row-02', 'Hall-B-Row-01', 'Hall-B-Row-02',
        ])

    def test_stamp_creates_placements_with_coordinates(self):
        from netbox_plant_graph.services import stamp_spatial_template

        template = self._make_template()
        result = stamp_spatial_template(template, self.site)

        hall_placements = [
            p for p in result.placements
            if p.target_type == ContentType.objects.get_for_model(Location)
            and Location.objects.get(pk=p.target_id).name.startswith('Hall-')
            and 'Row' not in Location.objects.get(pk=p.target_id).name
        ]
        xs = sorted(float(p.position_x) for p in hall_placements)
        self.assertEqual(xs, [0.0, 10.0])

    def test_dry_run_creates_nothing(self):
        from netbox_plant_graph.services import stamp_spatial_template

        template = self._make_template()
        result = stamp_spatial_template(template, self.site, dry_run=True)

        self.assertEqual(len(result.locations), 0)
        self.assertEqual(Location.objects.filter(site=self.site).count(), 0)

    def test_stamp_with_rack_position_nodes(self):
        from netbox_plant_graph.services import stamp_spatial_template

        manufacturer = Manufacturer.objects.create(name='SpatialMfr', slug='spatialmfr')
        rack_type = RackType.objects.create(manufacturer=manufacturer, model='ST RackType', slug='st-racktype')

        template = SpatialTemplate.objects.create(
            name='Rack Layout', slug='rack-layout', root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='Rack-{index}', node_type='rack_position',
            quantity=3, sort_order=0, rack_type=rack_type,
            position_x=0, position_x_stride=1,
        )
        result = stamp_spatial_template(template, self.site)

        self.assertEqual(len(result.racks), 3)
        self.assertEqual(len(result.locations), 0)
        rack_names = sorted(r.name for r in result.racks)
        self.assertEqual(rack_names, ['Rack-1', 'Rack-2', 'Rack-3'])

    def test_stamp_with_rack_position_nodes_syncs_floorplan_and_records_summary(self):
        from netbox_plant_graph.services import stamp_spatial_template

        manufacturer = Manufacturer.objects.create(name='SpatialSyncMfr', slug='spatialsyncmfr')
        rack_type = RackType.objects.create(manufacturer=manufacturer, model='Sync RackType', slug='sync-racktype')

        template = SpatialTemplate.objects.create(
            name='Rack Layout Sync', slug='rack-layout-sync', root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='Sync-Rack-{index}', node_type='rack_position',
            quantity=2, sort_order=0, rack_type=rack_type,
            position_x=1, position_x_stride=2, position_y=3,
        )

        floorplan_result = type(
            'FloorplanSyncResultStub',
            (),
            {
                'floorplan': object(),
                'created_floorplan': True,
                'created_objects': 2,
                'updated_objects': 0,
                'skipped_manual_override': 0,
                'errors': [],
            },
        )()

        with patch('netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan', return_value=floorplan_result) as sync_floorplan:
            result = stamp_spatial_template(template, self.site)

        self.assertEqual(len(result.racks), 2)
        self.assertEqual(result.floorplans_touched, 1)
        self.assertEqual(result.racks_synced_to_floorplan, 2)
        self.assertEqual(len(result.floorplan_results), 1)
        self.assertEqual(result.floorplan_errors, [])

        sync_floorplan.assert_called_once()
        scope_arg, rack_placements_arg = sync_floorplan.call_args.args
        self.assertEqual(scope_arg, self.site)
        self.assertEqual(len(rack_placements_arg), 2)
        synced_rack_names = [rack.name for rack, _placement in rack_placements_arg]
        self.assertEqual(synced_rack_names, ['Sync-Rack-1', 'Sync-Rack-2'])
        self.assertTrue(all(isinstance(placement, SpatialPlacement) for _rack, placement in rack_placements_arg))

    def test_stamp_under_location_scope_syncs_location_floorplan(self):
        from netbox_plant_graph.services import stamp_spatial_template

        manufacturer = Manufacturer.objects.create(name='SpatialScopeMfr', slug='spatialscopemfr')
        rack_type = RackType.objects.create(manufacturer=manufacturer, model='Scope RackType', slug='scope-racktype')
        location = Location.objects.create(name='Scope Room', slug='scope-room', site=self.site)

        template = SpatialTemplate.objects.create(
            name='Location Rack Layout', slug='location-rack-layout', root_node_type='room',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='Location-Rack-{index}', node_type='rack_position',
            quantity=1, sort_order=0, rack_type=rack_type,
        )

        floorplan_result = type(
            'FloorplanSyncResultStub',
            (),
            {
                'floorplan': object(),
                'created_floorplan': False,
                'created_objects': 1,
                'updated_objects': 0,
                'skipped_manual_override': 0,
                'errors': [],
            },
        )()

        with patch('netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan', return_value=floorplan_result) as sync_floorplan:
            result = stamp_spatial_template(template, location)

        self.assertEqual(len(result.racks), 1)
        self.assertEqual(result.floorplans_touched, 1)
        self.assertEqual(result.racks_synced_to_floorplan, 1)
        sync_floorplan.assert_called_once()
        self.assertEqual(sync_floorplan.call_args.args[0], location)

    def test_stamp_without_racks_skips_floorplan_sync(self):
        from netbox_plant_graph.services import stamp_spatial_template

        template = self._make_template()

        with patch('netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan') as sync_floorplan:
            result = stamp_spatial_template(template, self.site)

        self.assertEqual(len(result.racks), 0)
        self.assertEqual(result.floorplans_touched, 0)
        self.assertEqual(result.racks_synced_to_floorplan, 0)
        sync_floorplan.assert_not_called()

    def test_stamp_with_plan_persists_floorplan_sync_summary_on_stamp_record(self):
        from netbox_plant_graph.services import stamp_spatial_template

        manufacturer = Manufacturer.objects.create(name='SpatialPlanMfr', slug='spatialplanmfr')
        rack_type = RackType.objects.create(manufacturer=manufacturer, model='Plan RackType', slug='plan-racktype')
        template = SpatialTemplate.objects.create(
            name='Plan Rack Layout', slug='plan-rack-layout', root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template,
            name_pattern='Plan-Rack-{index}',
            node_type='rack_position',
            quantity=2,
            sort_order=0,
            rack_type=rack_type,
        )
        plan = DeploymentPlan.objects.create(name='Spatial Floorplan Summary Plan')

        floorplan_result = type(
            'FloorplanSyncResultStub',
            (),
            {
                'floorplan': object(),
                'created_floorplan': True,
                'created_objects': 2,
                'updated_objects': 0,
                'skipped_objects': 0,
                'errors': [],
            },
        )()

        with patch(
            'netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan',
            return_value=floorplan_result,
        ):
            result = stamp_spatial_template(template, self.site, plan=plan)

        stamp_record = result.stamp_records[-1]
        floorplan_sync = stamp_record.metadata.get('floorplan_sync')
        self.assertIsNotNone(floorplan_sync)
        self.assertTrue(floorplan_sync['sync_requested'])
        self.assertEqual(floorplan_sync['rack_placement_count'], 2)
        self.assertEqual(floorplan_sync['racks_synced_to_floorplan'], 2)
        self.assertEqual(floorplan_sync['floorplans_touched'], 1)
        self.assertEqual(floorplan_sync['results'][0]['created_objects'], 2)


class RackPopulationStampTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Pop Site', slug='pop-site')
        cls.manufacturer = Manufacturer.objects.create(name='PopMfr', slug='popmfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='Pop DT')
        cls.device_role = DeviceRole.objects.create(name='Pop Role', slug='pop-role')
        cls.rack = Rack.objects.create(name='Pop Rack', site=cls.site)

    def test_stamp_creates_devices_in_rack(self):
        from netbox_plant_graph.services import stamp_rack_population

        template = RackPopulationTemplate.objects.create(
            name='Pop Test', slug='pop-test',
        )
        RackPopulationSlot.objects.create(
            template=template, u_position=1, face='front',
            device_type=self.device_type, device_role=self.device_role,
            name_pattern='{parent_name}-U{index}',
        )
        RackPopulationSlot.objects.create(
            template=template, u_position=2, face='front',
            device_type=self.device_type, device_role=self.device_role,
            name_pattern='{parent_name}-U{index}',
        )

        result = stamp_rack_population(template, self.rack, site=self.site)

        self.assertEqual(len(result.devices), 2)
        device_names = sorted(d.name for d in result.devices)
        self.assertEqual(device_names, ['Pop Rack-U1', 'Pop Rack-U2'])
        self.assertTrue(all(d.rack_id == self.rack.pk for d in result.devices))

    def test_dry_run_creates_nothing(self):
        from netbox_plant_graph.services import stamp_rack_population

        template = RackPopulationTemplate.objects.create(
            name='Pop Dry', slug='pop-dry',
        )
        RackPopulationSlot.objects.create(
            template=template, u_position=5, face='front',
            device_type=self.device_type, device_role=self.device_role,
            name_pattern='Dry-{index}',
        )

        result = stamp_rack_population(template, self.rack, dry_run=True)
        self.assertEqual(len(result.devices), 0)


class AssemblyPassiveDeviceStampTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Asm Site', slug='asm-site')
        cls.manufacturer = Manufacturer.objects.create(name='AsmMfr', slug='asmmfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='Asm DT')
        cls.device_role = DeviceRole.objects.create(name='Asm Role', slug='asm-role')
        # Create component templates so Device auto-stamp creates ports
        cls.rpt = RearPortTemplate.objects.create(
            device_type=cls.device_type, name='A1', type='mpo', positions=8,
        )
        cls.fpt = create_front_port_template(
            device_type=cls.device_type, name='B1', type='lc', positions=8,
        )

    def _make_assembly_template(self):
        template = AssemblyTemplate.objects.create(
            name='Shuffle Board', slug='shuffle-board', assembly_type='shuffle_board',
            device_type=self.device_type,
        )
        a_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=8, label='A1',
        )
        b_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='B', connector_number=1,
            connector_type='lc-duplex', position_count=8, label='B1',
        )
        for pos in range(1, 9):
            AssemblyMappingTemplate.objects.create(
                template=template,
                a_connector=a_conn, a_position=pos,
                b_connector=b_conn, b_position=pos,
                mapping_type='identity',
            )
        return template

    def test_passive_device_stamp_creates_device_with_ports_and_mappings(self):
        from netbox_plant_graph.services import stamp_passive_device

        template = self._make_assembly_template()
        result = stamp_passive_device(
            template, name='Shuffle-1', site=self.site, device_role=self.device_role,
        )

        self.assertIsNotNone(result.device)
        self.assertEqual(result.device.name, 'Shuffle-1')
        self.assertEqual(len(result.rear_ports), 1)
        self.assertEqual(len(result.front_ports), 1)
        self.assertEqual(result.rear_ports[0].positions, 8)
        self.assertEqual(result.front_ports[0].positions, 8)
        self.assertEqual(len(result.port_mappings), 8)

    def test_passive_device_stamp_records_provenance(self):
        from netbox_plant_graph.services import stamp_passive_device

        plan = DeploymentPlan.objects.create(name='Provenance Plan')
        template = self._make_assembly_template()

        result = stamp_passive_device(
            template, name='Provenance-1', site=self.site, device_role=self.device_role,
            plan=plan,
        )

        self.assertIsNotNone(result.stamp_record)
        self.assertEqual(result.stamp_record.plan_id, plan.pk)
        self.assertEqual(result.stamp_record.result_id, result.device.pk)
        self.assertEqual(result.stamp_record.status, 'stamped')

    def test_passive_device_dry_run(self):
        from netbox_plant_graph.services import stamp_passive_device

        template = self._make_assembly_template()
        result = stamp_passive_device(
            template, name='Dry-1', site=self.site, device_role=self.device_role,
            dry_run=True,
        )
        self.assertIsNone(result.device)
        self.assertEqual(len(result.rear_ports), 0)


# ---------------------------------------------------------------------------
# Plan execution / rollback tests
# ---------------------------------------------------------------------------


class PlanExecutionTestCase(TestCase):
    def _make_plan_with_records(self):
        plan = DeploymentPlan.objects.create(name='Exec Plan', status='approved')
        template = AssemblyTemplate.objects.create(
            name='Exec Template', slug='exec-template', assembly_type='shuffle_trunk',
        )
        template_ct = ContentType.objects.get_for_model(template)
        for i in range(3):
            StampRecord.objects.create(
                plan=plan,
                template_type=template_ct,
                template_id=template.pk,
                status='pending',
            )
        return plan

    def test_execute_plan_transitions_to_active(self):
        from netbox_plant_graph.services import execute_plan

        plan = self._make_plan_with_records()
        result = execute_plan(plan)

        self.assertEqual(result.status, 'active')
        for record in result.stamp_records.all():
            self.assertEqual(record.status, 'stamped')
            self.assertIsNotNone(record.stamped_at)

    def test_execute_plan_rejects_non_approved(self):
        from netbox_plant_graph.services import execute_plan

        plan = DeploymentPlan.objects.create(name='Draft Plan', status='draft')
        with self.assertRaises(ValueError):
            execute_plan(plan)

    def test_rollback_plan_reverses_stamped_records(self):
        from netbox_plant_graph.services import execute_plan, rollback_plan

        plan = self._make_plan_with_records()
        execute_plan(plan)
        plan.refresh_from_db()

        rolled_back = rollback_plan(plan)
        self.assertEqual(rolled_back.status, 'rolled_back')
        for record in rolled_back.stamp_records.all():
            self.assertEqual(record.status, 'rolled_back')

    def test_rollback_rejects_draft_plan(self):
        from netbox_plant_graph.services import rollback_plan

        plan = DeploymentPlan.objects.create(name='Draft Rollback', status='draft')
        with self.assertRaises(ValueError):
            rollback_plan(plan)

    def test_execute_plan_persists_floorplan_sync_summary_for_spatial_records(self):
        from netbox_plant_graph.services import execute_plan

        site = Site.objects.create(name='Exec Spatial Site', slug='exec-spatial-site')
        manufacturer = Manufacturer.objects.create(name='ExecSpatialMfr', slug='execspatialmfr')
        rack_type = RackType.objects.create(
            manufacturer=manufacturer, model='Exec Spatial RackType', slug='exec-spatial-racktype',
        )
        template = SpatialTemplate.objects.create(
            name='Exec Spatial Template', slug='exec-spatial-template', root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template,
            name_pattern='Exec-Rack-{index}',
            node_type='rack_position',
            quantity=1,
            sort_order=0,
            rack_type=rack_type,
        )
        plan = DeploymentPlan.objects.create(name='Exec Spatial Plan', status='approved')
        template_ct = ContentType.objects.get_for_model(template)
        record = StampRecord.objects.create(
            plan=plan,
            template_type=template_ct,
            template_id=template.pk,
            status='pending',
            parameters={
                'stamp_type': 'spatial',
                'template_id': template.pk,
                'scope_type': 'site',
                'scope_id': site.pk,
            },
        )
        floorplan_result = type(
            'FloorplanSyncResultStub',
            (),
            {
                'floorplan': object(),
                'created_floorplan': False,
                'created_objects': 1,
                'updated_objects': 0,
                'skipped_objects': 0,
                'errors': [],
            },
        )()

        with patch(
            'netbox_plant_graph.services.floorplan_bridge.sync_rack_placements_to_floorplan',
            return_value=floorplan_result,
        ), patch('netbox_plant_graph.services.sync.rebuilder.rebuild_graph') as mock_rebuild, patch(
            'netbox_plant_graph.services.graph.persistent_audits.run_persistent_plane_audit',
        ) as mock_audit:
            mock_rebuild.return_value = {'build_run': 1}
            mock_audit.return_value = {}
            execute_plan(plan)

        record.refresh_from_db()
        floorplan_sync = record.metadata.get('floorplan_sync')
        self.assertIsNotNone(floorplan_sync)
        self.assertEqual(floorplan_sync['racks_synced_to_floorplan'], 1)
        self.assertEqual(floorplan_sync['floorplans_touched'], 1)
        self.assertEqual(floorplan_sync['results'][0]['created_objects'], 1)


# ---------------------------------------------------------------------------
# Assembly mapping fallback (transformer) tests
# ---------------------------------------------------------------------------


class AssemblyMappingLookupTestCase(TestCase):
    """Test _build_assembly_mapping_lookup used by the transformer for cables without native profiles."""

    @classmethod
    def setUpTestData(cls):
        from dcim.choices import LinkStatusChoices
        from dcim.models import Cable

        cls.template = AssemblyTemplate.objects.create(
            name='Shuffle Trunk Test', slug='shuffle-trunk-test', assembly_type='shuffle_trunk',
        )
        cls.a_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.template, side='A', connector_number=1, connector_type='mpo', position_count=4,
        )
        cls.b_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.template, side='B', connector_number=1, connector_type='mpo', position_count=4,
        )
        # Shuffle mapping: A1→B3, A2→B4, A3→B1, A4→B2
        for a_pos, b_pos in [(1, 3), (2, 4), (3, 1), (4, 2)]:
            AssemblyMappingTemplate.objects.create(
                template=cls.template,
                a_connector=cls.a_conn, a_position=a_pos,
                b_connector=cls.b_conn, b_position=b_pos,
            )

        cls.cable = Cable.objects.create(
            label='Assembly Lookup Test Cable',
            status=LinkStatusChoices.STATUS_CONNECTED,
        )
        cable_ct = ContentType.objects.get_for_model(Cable)
        template_ct = ContentType.objects.get_for_model(AssemblyTemplate)
        StampRecord.objects.create(
            template_type=template_ct, template_id=cls.template.pk,
            result_type=cable_ct, result_id=cls.cable.pk,
            status='completed',
        )

    def test_lookup_returns_bidirectional_mapping(self):
        from netbox_plant_graph.services.sync.transformer import _build_assembly_mapping_lookup

        lookup = _build_assembly_mapping_lookup({self.cable.pk})
        cable_map = lookup[self.cable.pk]

        # A-side → B-side
        self.assertEqual(cable_map[('A', 1, 1)], (1, 3))
        self.assertEqual(cable_map[('A', 1, 2)], (1, 4))
        self.assertEqual(cable_map[('A', 1, 3)], (1, 1))
        self.assertEqual(cable_map[('A', 1, 4)], (1, 2))

        # B-side → A-side (reverse)
        self.assertEqual(cable_map[('B', 1, 3)], (1, 1))
        self.assertEqual(cable_map[('B', 1, 4)], (1, 2))
        self.assertEqual(cable_map[('B', 1, 1)], (1, 3))
        self.assertEqual(cable_map[('B', 1, 2)], (1, 4))

    def test_lookup_returns_empty_for_unknown_cable(self):
        from netbox_plant_graph.services.sync.transformer import _build_assembly_mapping_lookup

        lookup = _build_assembly_mapping_lookup({999999})
        self.assertEqual(lookup, {})


# ---------------------------------------------------------------------------
# Stamp → graph rebuild integration tests
# ---------------------------------------------------------------------------


class AssemblyStampGraphRebuildTestCase(TestCase):
    """Verify that stamped passive devices produce valid graph edges after rebuild."""

    @classmethod
    def setUpTestData(cls):
        from dcim.choices import LinkStatusChoices
        from dcim.models import Cable, FrontPortTemplate, Interface, Manufacturer, RearPortTemplate

        cls.site = Site.objects.create(name='Graph Asm Site', slug='graph-asm-site')
        cls.manufacturer = Manufacturer.objects.create(name='GraphAsmMfr', slug='graphasmmfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='Graph Asm DT')
        cls.device_role = DeviceRole.objects.create(name='Graph Asm Role', slug='graph-asm-role')

        RearPortTemplate.objects.create(
            device_type=cls.device_type, name='A1', type='mpo', positions=4,
        )
        create_front_port_template(
            device_type=cls.device_type, name='B1', type='lc', positions=4,
        )

        # Create assembly template with 4 identity mappings
        cls.template = AssemblyTemplate.objects.create(
            name='Graph Shuffle', slug='graph-shuffle', assembly_type='shuffle_board',
            device_type=cls.device_type,
        )
        a_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=4, label='A1',
        )
        b_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.template, side='B', connector_number=1,
            connector_type='lc-duplex', position_count=4, label='B1',
        )
        for pos in range(1, 5):
            AssemblyMappingTemplate.objects.create(
                template=cls.template,
                a_connector=a_conn, a_position=pos,
                b_connector=b_conn, b_position=pos,
                mapping_type='identity',
            )

    def test_stamp_then_rebuild_creates_plant_nodes(self):
        from netbox_plant_graph.models import Fabric, PlantNode
        from netbox_plant_graph.services import stamp_passive_device
        from netbox_plant_graph.services.sync.rebuilder import rebuild_graph

        result = stamp_passive_device(
            self.template, name='Graph-Device-1',
            site=self.site, device_role=self.device_role,
        )
        self.assertIsNotNone(result.device)

        fabric = Fabric.objects.create(name='Asm Rebuild Fabric')
        rebuild_result = rebuild_graph(trigger_mode='test')

        device_ct = ContentType.objects.get_for_model(Device)
        device_nodes = PlantNode.objects.filter(
            source_type=device_ct, source_id=result.device.pk,
        )
        self.assertTrue(device_nodes.exists(), 'Stamped device should produce PlantNode after rebuild')

    def test_stamp_with_cables_rebuild_succeeds(self):
        """Stamp a device, cable it, and verify rebuild completes without error."""
        from dcim.models import Cable, Interface

        from netbox_plant_graph.models import Fabric
        from netbox_plant_graph.services import stamp_passive_device
        from netbox_plant_graph.services.sync.rebuilder import rebuild_graph

        # Stamp the passive device
        result = stamp_passive_device(
            self.template, name='FineEdge-Device',
            site=self.site, device_role=self.device_role,
        )

        # Create peer device with interface
        peer_a = Device.objects.create(
            name='Peer-A-FE', site=self.site,
            device_type=self.device_type, role=self.device_role,
        )
        iface_a = Interface.objects.create(device=peer_a, name='eth0')

        # Cable peer interface → stamped device's rear port
        rear_port = result.rear_ports[0]
        cable = Cable(a_terminations=[iface_a], b_terminations=[rear_port])
        cable.clean()
        cable.save()

        fabric = Fabric.objects.create(name='FE Rebuild Fabric')
        rebuild_result = rebuild_graph(trigger_mode='test')

        # Rebuild should succeed and return a build_run reference
        self.assertIn('build_run', rebuild_result)


class SpatialStampGraphRebuildTestCase(TestCase):
    """Verify that spatial stamp creates objects that are visible after rebuild."""

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Spatial Graph Site', slug='spatial-graph-site')
        cls.manufacturer = Manufacturer.objects.create(name='SpatialGraphMfr', slug='spatialgraphmfr')
        cls.rack_type = RackType.objects.create(
            manufacturer=cls.manufacturer, model='SG RackType', slug='sg-racktype',
        )

    def test_spatial_stamp_creates_rack_then_rebuild_succeeds(self):
        from netbox_plant_graph.models import Fabric
        from netbox_plant_graph.services import stamp_spatial_template
        from netbox_plant_graph.services.sync.rebuilder import rebuild_graph

        template = SpatialTemplate.objects.create(
            name='SG Layout', slug='sg-layout', root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='SG-Rack-{index}', node_type='rack_position',
            quantity=2, sort_order=0, rack_type=self.rack_type,
        )

        result = stamp_spatial_template(template, self.site)
        self.assertEqual(len(result.racks), 2)

        fabric = Fabric.objects.create(name='Spatial Rebuild Fabric')
        rebuild_result = rebuild_graph(trigger_mode='test')
        self.assertIn('build_run', rebuild_result)


# ---------------------------------------------------------------------------
# End-to-end plan execution integration tests
# ---------------------------------------------------------------------------


class PlanExecutionIntegrationTestCase(TestCase):
    """
    End-to-end: plan creation → stamp → graph rebuild → audit → verify.

    The execute_plan orchestrator now triggers rebuild_graph and
    run_persistent_plane_audit after stamping.
    """

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='E2E Site', slug='e2e-site')
        cls.manufacturer = Manufacturer.objects.create(name='E2EMfr', slug='e2emfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='E2E DT')
        cls.device_role = DeviceRole.objects.create(name='E2E Role', slug='e2e-role')
        RearPortTemplate.objects.create(
            device_type=cls.device_type, name='A1', type='mpo', positions=4,
        )
        create_front_port_template(
            device_type=cls.device_type, name='B1', type='lc', positions=4,
        )

    def test_execute_plan_triggers_rebuild_and_audit(self):
        from unittest.mock import patch

        from netbox_plant_graph.services import execute_plan

        template = AssemblyTemplate.objects.create(
            name='E2E Template', slug='e2e-template', assembly_type='shuffle_board',
            device_type=self.device_type,
        )
        template_ct = ContentType.objects.get_for_model(template)

        plan = DeploymentPlan.objects.create(name='E2E Plan', status='approved')
        StampRecord.objects.create(
            plan=plan, template_type=template_ct, template_id=template.pk,
            status='pending',
        )

        with patch('netbox_plant_graph.services.sync.rebuilder.rebuild_graph') as mock_rebuild, \
             patch('netbox_plant_graph.services.graph.persistent_audits.run_persistent_plane_audit') as mock_audit:
            mock_rebuild.return_value = {'build_run': 1}
            mock_audit.return_value = {}
            result = execute_plan(plan)

        self.assertEqual(result.status, 'active')
        mock_rebuild.assert_called_once_with(trigger_mode='stamp')
        mock_audit.assert_called_once_with(trigger_mode='stamp')

    def test_execute_plan_succeeds_if_rebuild_fails(self):
        """Plan execution should succeed even if post-stamp rebuild errors."""
        from unittest.mock import patch

        from netbox_plant_graph.services import execute_plan

        template = AssemblyTemplate.objects.create(
            name='E2E Rebuild Fail', slug='e2e-rebuild-fail', assembly_type='shuffle_board',
        )
        template_ct = ContentType.objects.get_for_model(template)

        plan = DeploymentPlan.objects.create(name='E2E Rebuild Fail Plan', status='approved')
        StampRecord.objects.create(
            plan=plan, template_type=template_ct, template_id=template.pk,
            status='pending',
        )

        with patch('netbox_plant_graph.services.sync.rebuilder.rebuild_graph', side_effect=RuntimeError('boom')), \
             patch('netbox_plant_graph.services.graph.persistent_audits.run_persistent_plane_audit') as mock_audit:
            result = execute_plan(plan)

        # Plan should still be active — rebuild failure is non-fatal
        self.assertEqual(result.status, 'active')
        for record in result.stamp_records.all():
            self.assertEqual(record.status, 'stamped')
        # Audit should still be attempted even after rebuild failure
        mock_audit.assert_called_once()

    def test_execute_plan_succeeds_if_audit_fails(self):
        """Plan execution should succeed even if post-stamp audit errors."""
        from unittest.mock import patch

        from netbox_plant_graph.services import execute_plan

        template = AssemblyTemplate.objects.create(
            name='E2E Audit Fail', slug='e2e-audit-fail', assembly_type='shuffle_board',
        )
        template_ct = ContentType.objects.get_for_model(template)

        plan = DeploymentPlan.objects.create(name='E2E Audit Fail Plan', status='approved')
        StampRecord.objects.create(
            plan=plan, template_type=template_ct, template_id=template.pk,
            status='pending',
        )

        with patch('netbox_plant_graph.services.sync.rebuilder.rebuild_graph') as mock_rebuild, \
             patch('netbox_plant_graph.services.graph.persistent_audits.run_persistent_plane_audit', side_effect=RuntimeError('audit boom')):
            mock_rebuild.return_value = {'build_run': 1}
            result = execute_plan(plan)

        self.assertEqual(result.status, 'active')

    def test_full_plan_lifecycle_with_real_stamp(self):
        """
        Full lifecycle: create plan → add stamp records → approve → execute → rollback.
        Uses real stamp_passive_device to create actual objects.
        """
        from unittest.mock import patch

        from netbox_plant_graph.services import execute_plan, rollback_plan, stamp_passive_device

        template = AssemblyTemplate.objects.create(
            name='Lifecycle Template', slug='lifecycle-template',
            assembly_type='shuffle_board', device_type=self.device_type,
        )
        a_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=4, label='A1',
        )
        b_conn = AssemblyConnectorTemplate.objects.create(
            template=template, side='B', connector_number=1,
            connector_type='lc-duplex', position_count=4, label='B1',
        )
        for pos in range(1, 5):
            AssemblyMappingTemplate.objects.create(
                template=template,
                a_connector=a_conn, a_position=pos,
                b_connector=b_conn, b_position=pos,
                mapping_type='identity',
            )

        # Create plan and stamp a device under it
        plan = DeploymentPlan.objects.create(name='Lifecycle Plan', status='draft')
        stamp_result = stamp_passive_device(
            template, name='Lifecycle-Device-1',
            site=self.site, device_role=self.device_role,
            plan=plan,
        )
        self.assertIsNotNone(stamp_result.device)
        self.assertIsNotNone(stamp_result.stamp_record)

        # Verify device exists
        self.assertTrue(Device.objects.filter(pk=stamp_result.device.pk).exists())

        # Approve and execute
        plan.status = 'approved'
        plan.save(update_fields=['status'])

        with patch('netbox_plant_graph.services.sync.rebuilder.rebuild_graph') as mock_rebuild, \
             patch('netbox_plant_graph.services.graph.persistent_audits.run_persistent_plane_audit') as mock_audit:
            mock_rebuild.return_value = {'build_run': 1}
            mock_audit.return_value = {}
            executed = execute_plan(plan)

        self.assertEqual(executed.status, 'active')

        # Rollback should delete the stamped device
        plan.refresh_from_db()
        rolled_back = rollback_plan(plan)
        self.assertEqual(rolled_back.status, 'rolled_back')
        self.assertFalse(
            Device.objects.filter(pk=stamp_result.device.pk).exists(),
            'Rollback should delete the stamped device',
        )


# ---------------------------------------------------------------------------
# Cascade integration tests (spatial → rack pop → assembly)
# ---------------------------------------------------------------------------


class CascadeStampTestCase(TestCase):
    """
    Test the full cascade: spatial template stamp → rack population → assembly.
    """

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name='Cascade Site', slug='cascade-site')
        cls.manufacturer = Manufacturer.objects.create(name='CascadeMfr', slug='cascademfr')
        cls.device_type = DeviceType.objects.create(manufacturer=cls.manufacturer, model='Cascade DT')
        cls.device_role = DeviceRole.objects.create(name='Cascade Role', slug='cascade-role')
        cls.rack_type = RackType.objects.create(
            manufacturer=cls.manufacturer, model='Cascade RT', slug='cascade-rt',
        )

        RearPortTemplate.objects.create(device_type=cls.device_type, name='A1', type='mpo', positions=4)
        create_front_port_template(device_type=cls.device_type, name='B1', type='lc', positions=4)

        # Assembly template for passive device in each rack slot
        cls.assembly_template = AssemblyTemplate.objects.create(
            name='Cascade Assembly', slug='cascade-assembly',
            assembly_type='shuffle_board', device_type=cls.device_type,
        )
        a_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.assembly_template, side='A', connector_number=1,
            connector_type='mpo-12', position_count=4, label='A1',
        )
        b_conn = AssemblyConnectorTemplate.objects.create(
            template=cls.assembly_template, side='B', connector_number=1,
            connector_type='lc-duplex', position_count=4, label='B1',
        )
        for pos in range(1, 5):
            AssemblyMappingTemplate.objects.create(
                template=cls.assembly_template,
                a_connector=a_conn, a_position=pos,
                b_connector=b_conn, b_position=pos,
                mapping_type='identity',
            )

        # Rack population template with 2 slots, one with assembly cascade
        from netbox_plant_graph.models import RackPopulationSlot

        cls.rack_pop_template = RackPopulationTemplate.objects.create(
            name='Cascade RackPop', slug='cascade-rackpop',
        )
        RackPopulationSlot.objects.create(
            template=cls.rack_pop_template, u_position=1, face='front',
            device_type=cls.device_type, device_role=cls.device_role,
            name_pattern='{parent_name}-U1',
        )
        RackPopulationSlot.objects.create(
            template=cls.rack_pop_template, u_position=2, face='front',
            device_type=cls.device_type, device_role=cls.device_role,
            name_pattern='{parent_name}-U2',
            assembly_template=cls.assembly_template,
        )

    def test_rack_population_cascades_to_assembly(self):
        from netbox_plant_graph.services import stamp_rack_population

        result = stamp_rack_population(
            self.rack_pop_template,
            Rack.objects.create(name='Cascade Rack', site=self.site),
            site=self.site,
        )

        self.assertEqual(len(result.devices), 2)
        # Slot 2 has assembly cascade → should produce an assembly result
        self.assertEqual(len(result.assembly_results), 1)
        assembly_result = result.assembly_results[0]
        self.assertIsNotNone(assembly_result.device)
        self.assertEqual(len(assembly_result.port_mappings), 4)

    def test_spatial_cascades_through_rack_population(self):
        from netbox_plant_graph.services import stamp_spatial_template

        template = SpatialTemplate.objects.create(
            name='Full Cascade Template', slug='full-cascade-template',
            root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='Cascade-Rack-{index}',
            node_type='rack_position', quantity=2, sort_order=0,
            rack_type=self.rack_type,
            rack_population_template=self.rack_pop_template,
        )

        result = stamp_spatial_template(template, self.site)

        # 2 racks created from spatial template
        self.assertEqual(len(result.racks), 2)
        # Each rack gets 2 devices from rack population → 4 total
        devices = Device.objects.filter(
            rack__in=[r.pk for r in result.racks],
        )
        self.assertEqual(devices.count(), 4)

    def test_full_cascade_with_plan_provenance(self):
        from netbox_plant_graph.services import stamp_spatial_template

        plan = DeploymentPlan.objects.create(name='Cascade Plan', status='draft')

        template = SpatialTemplate.objects.create(
            name='Provenance Cascade', slug='provenance-cascade',
            root_node_type='row',
        )
        SpatialTemplateNode.objects.create(
            template=template, name_pattern='Prov-Rack-{index}',
            node_type='rack_position', quantity=1, sort_order=0,
            rack_type=self.rack_type,
            rack_population_template=self.rack_pop_template,
        )

        result = stamp_spatial_template(template, self.site, plan=plan)

        # Should have stamp records from spatial + rack population + assembly cascades
        self.assertTrue(len(result.stamp_records) > 0)
        # All plan-associated stamp records should reference the plan
        plan_records = StampRecord.objects.filter(plan=plan)
        self.assertTrue(plan_records.exists())
