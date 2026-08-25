from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("protection", "0024_backup_directory_static_capture")]

    operations = [
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="capture_entry_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="backupconfigdirectory",
            name="capture_directory_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="backupconfigdirectory",
            name="scope_mode",
            field=models.CharField(
                choices=[
                    ("dynamic", "Continuous path"),
                    ("static_direct_files", "Captured direct entries"),
                    ("static_recursive_files", "Captured recursive tree"),
                ],
                default="dynamic",
                max_length=32,
            ),
        ),
    ]
