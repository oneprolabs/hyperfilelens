from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0014_repository_metric_probe_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="repository",
            name="cleanup_result",
            field=models.CharField(
                blank=True,
                choices=[
                    ("deleted", "Physical repository deleted"),
                    ("force_skipped", "Physical repository retained"),
                    ("preserved", "Legacy physical repository preserved"),
                ],
                db_index=True,
                default="",
                max_length=24,
            ),
        ),
    ]
