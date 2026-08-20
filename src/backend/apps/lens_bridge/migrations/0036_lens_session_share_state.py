from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0035_lens_run_submission_retry"),
    ]

    operations = [
        migrations.AddField(
            model_name="lenssessionlink",
            name="share_state_json",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
