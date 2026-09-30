from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("protection", "0026_snapshot_usage_reconcile_cursor")]

    operations = [
        migrations.AlterField(
            model_name="snapshotusagelease",
            name="consumer_type",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("restore", "Restore"),
                    ("chat", "Chat preparation"),
                    ("chat_update", "Chat data update"),
                ],
            ),
        ),
    ]
