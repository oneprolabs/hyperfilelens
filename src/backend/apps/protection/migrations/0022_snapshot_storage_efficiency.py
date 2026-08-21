from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("protection", "0021_snapshot_download_upload"),
    ]

    operations = [
        migrations.AddField(
            model_name="backupsourcesnapshotdirectory",
            name="new_original_content_bytes",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="backupsourcesnapshotdirectory",
            name="new_packed_content_bytes",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
