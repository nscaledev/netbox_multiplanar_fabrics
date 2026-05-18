from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0017_natural_ordering'),
        ('netbox_plant_graph', '0009_planning_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='assemblytemplate',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to='tenancy.tenant'),
        ),
        migrations.AddField(
            model_name='deploymentplan',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to='tenancy.tenant'),
        ),
        migrations.AddField(
            model_name='fabric',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to='tenancy.tenant'),
        ),
        migrations.AddField(
            model_name='rackpopulationtemplate',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to='tenancy.tenant'),
        ),
        migrations.AddField(
            model_name='spatialtemplate',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to='tenancy.tenant'),
        ),
    ]
