from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0034_session_cleanup_and_poll_fencing"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensrunsubmission",
            name="retry_of_run_uuid",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
