# Generated manually for Madison optical-lane wavelength modeling.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_plant_graph', '0014_reference_data_breakout_profiles'),
    ]

    operations = [
        migrations.AddField(
            model_name='signallane',
            name='wavelength_nm',
            field=models.PositiveIntegerField(
                default=1310,
                help_text='Nominal optical wavelength in nanometers. Defaults to 1310nm for DR-family optics.',
            ),
        ),
    ]
