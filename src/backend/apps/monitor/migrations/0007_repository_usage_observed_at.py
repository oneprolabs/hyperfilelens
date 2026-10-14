from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitor", "0006_operational_event")]

    operations = [
        migrations.AddField(
            model_name="repositoryusagemetric",
            name="observed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
