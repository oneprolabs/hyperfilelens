from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("protection", "0023_backup_config_create_request")]

    operations = [
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="scope_mode",
            field=models.CharField(
                choices=[
                    ("dynamic", "Continuous path"),
                    ("static_direct_files", "Captured direct files"),
                    ("static_recursive_files", "Captured recursive files"),
                ],
                default="dynamic",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="capture_group_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="capture_root",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="captured_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="capture_file_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="capture_manifest_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
