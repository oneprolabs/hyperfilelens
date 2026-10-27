from django.db import migrations


def normalize_retry_sources(apps, schema_editor):
    """Recover sources from explicit ancestry or known legacy operation kinds."""
    Task = apps.get_model("task", "Task")
    RepositoryTask = apps.get_model("storage", "RepositoryTask")
    database = schema_editor.connection.alias
    tasks = Task.objects.using(database)
    operations = RepositoryTask.objects.using(database)
    for task in tasks.filter(trigger_type="retry").iterator(chunk_size=500):
        current = task
        visited = set()
        while current.trigger_type == "retry" and current.replaces_task_id:
            if current.pk in visited:
                break
            visited.add(current.pk)
            parent = tasks.filter(
                pk=current.replaces_task_id,
                organization_id=task.organization_id,
                task_type=task.task_type,
            ).first()
            if parent is None:
                break
            current = parent
        source = current.trigger_type
        if source == "retry":
            source = "manual"
            operation = operations.filter(task_id=current.pk).first()
            if operation is not None and (
                operation.operation_type in {"maintenance.quick", "maintenance.full"}
                or operation.triggered_by_task_id
                or (
                    operation.operation_type == "snapshot.reconcile"
                    and operation.requested_by_id is None
                )
            ):
                source = "system"
        tasks.filter(pk=task.pk, trigger_type="retry").update(trigger_type=source)


class Migration(migrations.Migration):
    dependencies = [
        ("task", "0015_backup_config_provision_task_type"),
        ("storage", "0022_orphan_delete_execution"),
    ]

    operations = [
        migrations.RunPython(normalize_retry_sources, migrations.RunPython.noop),
    ]
