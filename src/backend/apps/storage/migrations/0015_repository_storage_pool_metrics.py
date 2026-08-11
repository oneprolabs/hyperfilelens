from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0014_repository_metric_probe_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="repository",
            name="storage_total_bytes",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="repository",
            name="storage_used_bytes",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="repository",
            name="storage_available_bytes",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="repository",
            name="storage_pool_key",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="repository",
            name="storage_mount_point",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
    ]
