from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_plant_graph', '0004_transport_channel_subinterfaces'),
    ]

    operations = [
        migrations.AddField(
            model_name='fabricarchitecture',
            name='fabric_class',
            field=models.CharField(default='roce_backend', max_length=64),
        ),
    ]
