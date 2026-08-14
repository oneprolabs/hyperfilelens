from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0031_session_create_and_capacity_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensrunsubmission",
            name="attachment_uuids",
            field=models.JSONField(blank=True, default=list, null=True),
        ),
    ]
