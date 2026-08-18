from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("task", "0014_insight_workspace_restore_task_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("backup", "Backup"),
                    ("restore", "Restore"),
                    ("insight_workspace_restore", "Insight workspace restore"),
                    ("snapshot_download", "Snapshot download"),
                    ("snapshot_delete", "Snapshot delete"),
                    ("backup_config_reset", "Backup config reset"),
                    ("backup_config_provision", "Backup config provision"),
                    ("source_unregister", "Source unregister"),
                    ("node_lifecycle", "Node lifecycle"),
                    ("repository_operation", "Repository operation"),
                    ("storage_provider_validation", "Storage provider validation"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
