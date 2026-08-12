from django.db import migrations, transaction
from django.utils import timezone


def end_deferred_source_unregister_tasks(apps, schema_editor):
    SourceResource = apps.get_model("source", "SourceResource")
    Task = apps.get_model("task", "Task")
    TaskDependency = apps.get_model("task", "TaskDependency")
    TaskEvent = apps.get_model("task", "TaskEvent")
    TaskResource = apps.get_model("task", "TaskResource")
    TaskStep = apps.get_model("task", "TaskStep")

    database = schema_editor.connection.alias
    now = timezone.now()
    with transaction.atomic(using=database):
        tasks = Task.objects.using(database).select_for_update().filter(
            task_type="source_unregister",
            status__in=("waiting", "blocked", "running"),
        )
        for task in tasks.iterator():
            payload = task.result_payload if isinstance(task.result_payload, dict) else {}
            was_deferred = task.status in ("waiting", "blocked") or (
                task.status == "running"
                and task.current_step == "prepare_source_unregister"
                and any(key in payload for key in ("waiting_reasons", "blocked_reasons"))
            )
            if not was_deferred:
                continue
            reasons = payload.get("waiting_reasons") or payload.get("blocked_reasons") or []
            task.status = "failed"
            task.error_code = "SOURCE_UNREGISTER_DEFERRED_CANCELLED"
            task.error_message = (
                "The previous deferred deregistration was ended. "
                "Submit a new request after resolving the prerequisite."
            )
            task.result_payload = {
                "ok": False,
                "accepted": False,
                "result": "failed",
                "source_ids": list((task.request_payload or {}).get("source_ids") or []),
                "reasons": reasons,
            }
            task.finished_at = now
            task.started_at = task.started_at or now
            task.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "result_payload",
                    "started_at",
                    "finished_at",
                    "updated_at",
                ]
            )
            prepare_step = TaskStep.objects.using(database).filter(
                task_id=task.id,
                step_name="prepare_source_unregister",
            ).order_by("step_index", "id").first()
            if prepare_step is not None:
                prepare_step.status = "failed"
                prepare_step.save(update_fields=["status"])
            TaskStep.objects.using(database).filter(
                task_id=task.id,
                status__in=("pending", "running"),
            ).exclude(id=getattr(prepare_step, "id", None)).update(status="skipped")
            TaskDependency.objects.using(database).filter(
                task_id=task.id,
                is_active=True,
            ).update(
                is_active=False,
                resolved_at=now,
            )
            last_event = TaskEvent.objects.using(database).filter(
                task_id=task.id,
            ).order_by("-seq").first()
            TaskEvent.objects.using(database).create(
                task_id=task.id,
                step_id=getattr(prepare_step, "id", None),
                seq=(int(last_event.seq) + 1 if last_event is not None else 1),
                level="ERROR",
                message="Deferred source deregistration ended without execution",
                metadata={
                    "error_code": "SOURCE_UNREGISTER_DEFERRED_CANCELLED",
                    "reasons": reasons,
                },
            )

    # Restore NAS state only after task locks are committed. The old API remains
    # live during blue/green migration and locks source rows before task rows;
    # keeping both phases in one transaction would introduce the opposite lock
    # order and a deadlock window. Querying by error code also makes this phase
    # retry-safe if migration execution is interrupted between the two phases.
    ended_task_ids = Task.objects.using(database).filter(
        task_type="source_unregister",
        error_code="SOURCE_UNREGISTER_DEFERRED_CANCELLED",
    ).values_list("id", flat=True)

    ended_nas_ids = list(
        TaskResource.objects.using(database)
        .filter(
            task_id__in=ended_task_ids,
            resource_type="backup_source",
            resource_subtype="nas",
        )
        .order_by("resource_id")
        .values_list("resource_id", flat=True)
        .distinct()
    )
    for nas_id in ended_nas_ids:
        with transaction.atomic(using=database):
            resource = (
                SourceResource.objects.using(database)
                .select_for_update()
                .filter(
                    id=nas_id,
                    is_deleted=False,
                    status="removing",
                )
                .first()
            )
            if resource is None:
                continue
            # Recheck after acquiring the source lock. A still-live old API can
            # submit a new request during blue/green migration; checking before
            # this lock could miss its uncommitted task and overwrite the new
            # removing fence with active.
            has_active_unregister = (
                TaskResource.objects.using(database)
                .filter(
                    task__task_type="source_unregister",
                    task__status__in=("pending", "running"),
                    resource_type="backup_source",
                    resource_subtype="nas",
                    resource_id=nas_id,
                )
                .exclude(task_id__in=ended_task_ids)
                .exists()
            )
            if has_active_unregister:
                continue
            resource.status = "active"
            resource.status_message = (
                "The previous deferred deregistration ended during upgrade. "
                "Submit a new request after resolving the prerequisite."
            )
            resource.updated_at = now
            resource.save(
                update_fields=["status", "status_message", "updated_at"]
            )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("source", "0013_source_resource_probing_status"),
        ("task", "0012_task_blocked_idempotency_and_dependency_checks"),
    ]

    operations = [
        migrations.RunPython(
            end_deferred_source_unregister_tasks,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
