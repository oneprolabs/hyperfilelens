from django.db import migrations


RESTORE_TASK_TYPES = (
    "restore",
    "insight_workspace_restore",
)


def backfill_task_repository_resources(apps, schema_editor):
    Task = apps.get_model("task", "Task")
    TaskResource = apps.get_model("task", "TaskResource")
    RestoreRecord = apps.get_model("restore", "RestoreRecord")
    database = schema_editor.connection.alias

    task_ids = Task.objects.using(database).filter(
        task_type__in=RESTORE_TASK_TYPES,
    ).values("id")
    task_repository_pairs = (
        RestoreRecord.objects.using(database)
        .filter(task_id__in=task_ids)
        .values_list("task_id", "items__repository_id")
        .distinct()
    )
    pairs = {
        (int(task_id), int(repository_id))
        for task_id, repository_id in task_repository_pairs
        if task_id is not None and repository_id is not None
    }
    if not pairs:
        return

    task_ids = {task_id for task_id, _repository_id in pairs}
    existing = set(
        TaskResource.objects.using(database)
        .filter(
            task_id__in=task_ids,
            resource_type="repository",
        )
        .values_list("task_id", "resource_id")
    )
    missing = [
        TaskResource(
            task_id=task_id,
            resource_type="repository",
            resource_subtype="",
            resource_id=repository_id,
            is_primary=False,
        )
        for task_id, repository_id in sorted(pairs - existing)
    ]
    TaskResource.objects.using(database).bulk_create(
        missing,
        ignore_conflicts=True,
        batch_size=500,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("restore", "0009_platform_restore_execution"),
        ("task", "0016_normalize_retry_trigger_sources"),
    ]

    operations = [
        migrations.RunPython(
            backfill_task_repository_resources,
            migrations.RunPython.noop,
        ),
    ]
