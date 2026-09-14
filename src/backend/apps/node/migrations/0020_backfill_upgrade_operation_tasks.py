from decimal import Decimal

from django.db import migrations


UPGRADE_STEPS = (
    "dispatch_agent_upgrade",
    "install_agent_upgrade",
    "restart_agent",
    "verify_agent_upgrade",
    "finalize_agent_upgrade",
)


def backfill_upgrade_operation_tasks(apps, schema_editor):
    NodeTask = apps.get_model("node", "NodeTask")
    Task = apps.get_model("task", "Task")
    TaskEvent = apps.get_model("task", "TaskEvent")
    TaskResource = apps.get_model("task", "TaskResource")
    TaskStep = apps.get_model("task", "TaskStep")
    database = schema_editor.connection.alias

    status_map = {
        "pending": "pending",
        "running": "running",
        "success": "success",
        "failed": "failed",
        "timeout": "timeout",
        "canceled": "cancelled",
    }
    terminal = {"success", "failed", "timeout", "canceled"}
    queryset = (
        NodeTask.objects.using(database)
        .filter(
            correlation_type="node.lifecycle",
            kind="agent.upgrade",
            parent_task__isnull=True,
        )
        .select_related("node")
        .order_by("created_at", "id")
    )
    for node_task in queryset.iterator(chunk_size=500):
        node = node_task.node
        status = status_map[node_task.status]
        result = dict(node_task.result or {})
        payload = dict(node_task.payload or {})
        error_code = None
        error_message = None
        if node_task.status in {"failed", "timeout", "canceled"}:
            error_code = str(
                result.get("failure_code")
                or result.get("diagnostic_error_code")
                or (
                    "NODE_UPGRADE_TIMEOUT"
                    if node_task.status == "timeout"
                    else "NODE_UPGRADE_CANCELLED"
                    if node_task.status == "canceled"
                    else "NODE_UPGRADE_FAILED"
                )
            )
            error_message = str(
                node_task.last_error or "Agent upgrade did not complete."
            )
        started_at = (
            node_task.accepted_at
            or node_task.dispatched_at
            or (node_task.created_at if node_task.status != "pending" else None)
        )
        finished_at = node_task.updated_at if node_task.status in terminal else None
        role_label = {
            "agent": "Agent",
            "proxy": "Proxy",
            "gateway": "Gateway",
        }.get(str(node.role or ""), "Agent")
        task = Task.objects.using(database).create(
            organization_id=node_task.organization_id,
            task_type="node_lifecycle",
            display_name=f'Upgrade {role_label} "{node.name or node.id}"',
            status=status,
            progress=Decimal("100.00") if status == "success" else Decimal("0.00"),
            current_step=(
                "finalize_agent_upgrade"
                if node_task.status in terminal
                else "install_agent_upgrade"
                if node_task.status == "running"
                else "dispatch_agent_upgrade"
            ),
            trigger_type="manual",
            request_payload={
                "operation": "upgrade",
                "node_task_id": str(node_task.id),
                "target_version": str(payload.get("target_version") or ""),
                "target_commit": payload.get("target_commit"),
                "node": {
                    "id": int(node.id),
                    "name": str(node.name or node.id),
                    "role": str(node.role or ""),
                    "endpoint": str(node.ip_address or ""),
                    "registered_at": (
                        node.created_at.isoformat() if node.created_at else None
                    ),
                },
            },
            result_payload={
                "node_task_id": str(node_task.id),
                "node_id": int(node.id),
                "target_version": str(payload.get("target_version") or ""),
                "target_commit": payload.get("target_commit"),
                "source_version": str(payload.get("source_version") or ""),
                "failure_code": result.get("failure_code")
                or result.get("diagnostic_error_code"),
                "observed_agent_version": result.get("observed_agent_version"),
                "observed_agent_commit": result.get("observed_agent_commit"),
            },
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
        )
        Task.objects.using(database).filter(pk=task.pk).update(
            created_at=node_task.created_at,
            updated_at=node_task.updated_at,
        )
        steps = []
        for index, step_name in enumerate(UPGRADE_STEPS, start=1):
            step_status = "pending"
            step_progress = Decimal("0.00")
            if status == "success":
                step_status = "success"
                step_progress = Decimal("100.00")
            elif step_name == task.current_step:
                step_status = (
                    "running"
                    if status == "running"
                    else "failed"
                    if status in {"failed", "timeout"}
                    else "skipped"
                    if status == "cancelled"
                    else "pending"
                )
            steps.append(
                TaskStep(
                    task_id=task.id,
                    step_index=index,
                    step_name=step_name,
                    status=step_status,
                    progress=step_progress,
                )
            )
        TaskStep.objects.using(database).bulk_create(steps)
        TaskEvent.objects.using(database).create(
            task_id=task.id,
            seq=1,
            level=(
                "ERROR"
                if status in {"failed", "timeout"}
                else "WARN"
                if status == "cancelled"
                else "INFO"
            ),
            message="Historical Agent upgrade imported",
            metadata={"node_task_id": str(node_task.id)},
        )
        TaskResource.objects.using(database).create(
            task_id=task.id,
            resource_type="host",
            resource_subtype=str(node.role or ""),
            resource_id=int(node.id),
            is_primary=True,
        )
        NodeTask.objects.using(database).filter(
            pk=node_task.pk,
            parent_task__isnull=True,
        ).update(parent_task_id=task.id)


class Migration(migrations.Migration):
    dependencies = [
        ("node", "0019_nodetoken_automatic_installation_mode"),
        ("task", "0015_backup_config_provision_task_type"),
    ]

    operations = [
        migrations.RunPython(
            backfill_upgrade_operation_tasks, migrations.RunPython.noop
        ),
    ]
