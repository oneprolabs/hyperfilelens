from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lens_bridge", "0038_session_lifecycle_error_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="lenssessionlink",
            name="analysis_mode",
            field=models.CharField(
                choices=[
                    ("fast", "Fast"),
                    ("standard", "Standard"),
                    ("deep", "Deep"),
                ],
                db_index=True,
                default="standard",
                max_length=16,
            ),
        ),
    ]
