from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("protection", "0018_backup_directory_repository_locator")]

    operations = [
        migrations.AddField(
            model_name="snapshotdownloadartifact",
            name="sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="snapshotdownloadartifact",
            name="upload_token_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="snapshotdownloadartifact",
            name="status",
            field=models.CharField(
                choices=[
                    ("uploading", "Uploading"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                    ("expired", "Expired"),
                    ("deleted", "Deleted"),
                ],
                db_index=True,
                default="ready",
                max_length=20,
            ),
        ),
    ]
