from django.db import migrations, models


INSIGHT_WORKSPACE_RESTORE = "insight_workspace_restore"
USER_RESTORE = "restore"
TERMINAL_TASK_STATUSES = ("success", "failed", "cancelled", "timeout")


def classify_insight_workspace_restore_tasks(apps, schema_editor):
    RestoreRecord = apps.get_model("restore", "RestoreRecord")
    Task = apps.get_model("task", "Task")
    database = schema_editor.connection.alias

    task_ids = (
        RestoreRecord.objects.using(database)
        .filter(purpose="lens_workspace")
        .order_by()
        .values_list("task_id", flat=True)
    )
    Task.objects.using(database).filter(
        id__in=task_ids,
        task_type=USER_RESTORE,
        # Migrations run while the previous blue/green API remains live. Leave
        # active tasks on the legacy type so adjacent versions coordinate them
        # through completion.
        status__in=TERMINAL_TASK_STATUSES,
    ).update(task_type=INSIGHT_WORKSPACE_RESTORE)


def restore_legacy_task_type(apps, schema_editor):
    RestoreRecord = apps.get_model("restore", "RestoreRecord")
    Task = apps.get_model("task", "Task")
    database = schema_editor.connection.alias
    task_ids = RestoreRecord.objects.using(database).filter(
        purpose="lens_workspace",
    ).values("task_id")
    Task.objects.using(database).filter(
        id__in=task_ids,
        task_type=INSIGHT_WORKSPACE_RESTORE,
    ).update(task_type=USER_RESTORE)


class Migration(migrations.Migration):
    dependencies = [
        ("restore", "0006_restore_item_terminal_projection"),
        ("task", "0013_end_deferred_source_unregister_tasks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("backup", "Backup"),
                    ("restore", "Restore"),
                    (INSIGHT_WORKSPACE_RESTORE, "Insight workspace restore"),
                    ("snapshot_download", "Snapshot download"),
                    ("snapshot_delete", "Snapshot delete"),
                    ("backup_config_reset", "Backup config reset"),
                    ("source_unregister", "Source unregister"),
                    ("node_lifecycle", "Node lifecycle"),
                    ("repository_operation", "Repository operation"),
                    ("storage_provider_validation", "Storage provider validation"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.RunPython(
            classify_insight_workspace_restore_tasks,
            restore_legacy_task_type,
        ),
    ]
