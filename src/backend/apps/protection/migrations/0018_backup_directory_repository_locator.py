from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("protection", "0017_backup_directory_size_estimated_at")]

    operations = [
        migrations.AddField(
            model_name="backupsourcesnapshotdirectory",
            name="repository_locator",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
