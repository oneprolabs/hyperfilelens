from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("protection", "0025_backfill_snapshot_usage_leases"),
    ]

    operations = [
        migrations.AddField(
            model_name="snapshotusagelease",
            name="last_reconciled_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
