from collections import defaultdict

from django.db import migrations


def normalize_retry_sources(apps, schema_editor):
    """Resolve each legacy node once, then update sources in bounded batches."""
    Task = apps.get_model("task", "Task")
    RepositoryTask = apps.get_model("storage", "RepositoryTask")
    database = schema_editor.connection.alias
    tasks = Task.objects.using(database)
    operations = RepositoryTask.objects.using(database)
    batch_size = 500
    fields = ("id", "organization_id", "task_type", "trigger_type", "replaces_task_id")
    retry_nodes = {
        row["id"]: row
        for row in tasks.filter(trigger_type="retry").order_by().values(*fields)
    }
    if not retry_nodes:
        return

    nodes = dict(retry_nodes)
    parent_ids = list({
        row["replaces_task_id"] for row in retry_nodes.values()
        if row["replaces_task_id"] is not None
    } - retry_nodes.keys())
    for offset in range(0, len(parent_ids), batch_size):
        nodes.update({
            row["id"]: row
            for row in tasks.filter(
                pk__in=parent_ids[offset:offset + batch_size],
            ).order_by().values(*fields)
        })
    system_task_ids = {
        row["task_id"]
        for row in operations.filter(task__trigger_type="retry").order_by().values(
            "task_id", "operation_type", "triggered_by_task_id", "requested_by_id",
        ).iterator(chunk_size=batch_size)
        if (
            row["operation_type"] in {"maintenance.quick", "maintenance.full"}
            or row["triggered_by_task_id"]
            or (
                row["operation_type"] == "snapshot.reconcile"
                and row["requested_by_id"] is None
            )
        )
    }

    sources: dict[int, str] = {}
    updates: dict[str, list[int]] = defaultdict(list)
    # Ascending IDs make malformed-cycle fallback deterministic. Resolution is
    # iterative and memoized, including when ancestry is not in ID order.
    for task_id in sorted(retry_nodes):
        task = retry_nodes[task_id]
        current = task
        path: list[int] = []
        visited: set[int] = set()
        while True:
            current_id = current["id"]
            if current_id in sources:
                source = sources[current_id]
                break
            if current["trigger_type"] != "retry":
                source = current["trigger_type"]
                break
            if current_id in visited:
                source = "system" if current_id in system_task_ids else "manual"
                break
            path.append(current_id)
            visited.add(current_id)
            parent = nodes.get(current["replaces_task_id"])
            if (
                parent is None
                or parent["organization_id"] != task["organization_id"]
                or parent["task_type"] != task["task_type"]
            ):
                source = "system" if current_id in system_task_ids else "manual"
                break
            current = parent
        for resolved_id in path:
            sources[resolved_id] = source
        updates[source].append(task_id)

    for source, task_ids in updates.items():
        for offset in range(0, len(task_ids), batch_size):
            # Keep a concurrent writer's already-normalized value untouched.
            tasks.filter(
                pk__in=task_ids[offset:offset + batch_size], trigger_type="retry",
            ).update(trigger_type=source)


class Migration(migrations.Migration):
    dependencies = [
        ("task", "0015_backup_config_provision_task_type"),
        ("storage", "0022_orphan_delete_execution"),
    ]

    operations = [
        migrations.RunPython(normalize_retry_sources, migrations.RunPython.noop),
    ]
