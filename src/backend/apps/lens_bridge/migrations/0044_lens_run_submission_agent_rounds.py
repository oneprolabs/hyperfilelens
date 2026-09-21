from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0043_alter_lenssessionlink_active_run_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensrunsubmission",
            name="agent_rounds",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
