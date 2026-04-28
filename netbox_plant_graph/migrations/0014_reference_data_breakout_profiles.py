"""
Data migration: create standard BreakoutProfile and DeviceBreakoutTemplate
reference records for the 800G 4:1 GPU server topology.

Workstreams C4 + D7 from docs/gap-resolution-plan.md.
"""

from django.db import migrations


def create_breakout_reference_data(apps, schema_editor):
    BreakoutProfile = apps.get_model('netbox_plant_graph', 'BreakoutProfile')
    DeviceBreakoutTemplate = apps.get_model('netbox_plant_graph', 'DeviceBreakoutTemplate')
    DeviceChildInterfaceSpec = apps.get_model('netbox_plant_graph', 'DeviceChildInterfaceSpec')

    # C4 — Standard 800G 4x200G sequential breakout profile
    profile, _ = BreakoutProfile.objects.get_or_create(
        slug='breakout-800g-4x200g',
        defaults={
            'name': '800G 4x200G Sequential Breakout',
            'description': 'Standard 800G DAC/AOC 4:1 breakout — each parent position maps to one 200G child ordinal.',
            'parent_speed_gbps': 800,
            'child_count': 4,
            'child_speed_gbps': 200,
            'mapping_mode': 'sequential',
            'position_map': {},
            'metadata': {},
        },
    )

    # D7 — GPU Server 800G 4-Plane Breakout template
    template, created = DeviceBreakoutTemplate.objects.get_or_create(
        slug='gpu-server-800g-4plane',
        defaults={
            'name': 'GPU Server 800G 4-Plane Breakout',
            'description': (
                'Breakout template for GPU servers with two 800G parent NICs (NIC0, NIC1). '
                'Creates four 200G virtual child interfaces per NIC, assigned fabric_plane 1–4.'
            ),
            'metadata': {},
        },
    )

    if created:
        # Spec 1: NIC0 → four virtual 200G children, planes 1–4
        DeviceChildInterfaceSpec.objects.get_or_create(
            breakout_template=template,
            parent_interface_name='NIC0',
            defaults={
                'child_name_pattern': '{parent}.plane{plane}',
                'child_count': 4,
                'child_interface_type': 'virtual',
                'child_speed_kbps': 200_000,
                'fabric_plane_start': 1,
                'breakout_profile': profile,
                'sort_order': 0,
                'metadata': {},
            },
        )

        # Spec 2: NIC1 → four virtual 200G children, planes 1–4
        DeviceChildInterfaceSpec.objects.get_or_create(
            breakout_template=template,
            parent_interface_name='NIC1',
            defaults={
                'child_name_pattern': '{parent}.plane{plane}',
                'child_count': 4,
                'child_interface_type': 'virtual',
                'child_speed_kbps': 200_000,
                'fabric_plane_start': 1,
                'breakout_profile': profile,
                'sort_order': 1,
                'metadata': {},
            },
        )


def remove_breakout_reference_data(apps, schema_editor):
    """Reverse: delete only if still in pristine reference state (no FK consumers)."""
    DeviceBreakoutTemplate = apps.get_model('netbox_plant_graph', 'DeviceBreakoutTemplate')
    BreakoutProfile = apps.get_model('netbox_plant_graph', 'BreakoutProfile')

    DeviceBreakoutTemplate.objects.filter(slug='gpu-server-800g-4plane').delete()
    BreakoutProfile.objects.filter(slug='breakout-800g-4x200g').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_plant_graph', '0013_rackpopulationtemplate_fabric_and_more'),
    ]

    operations = [
        migrations.RunPython(
            create_breakout_reference_data,
            reverse_code=remove_breakout_reference_data,
        ),
    ]
