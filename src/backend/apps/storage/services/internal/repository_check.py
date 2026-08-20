"""Durable, Worker-owned repository health checks."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.storage.repositories.models import (
    Repository,
    RepositoryExecutionTarget,
    RepositoryTask,
)
from apps.storage.services.internal.repository_health import (
    is_repository_ownership_failure,
    probe_repository_health,
)
from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
)
from apps.storage.services.internal.repository_location import (
    invalidate_repository_location_ownership,
)
from apps.storage.services.internal.repository_secrets import (
    resolve_repository_secrets,
    scrub_secrets,
    secret_values_for_scrub,
)
from apps.storage.services.internal.repository_usage import sync_repository_usage
from apps.storage.services.internal.s3_validation_errors import (
    classify_s3_validation_error,
)
from apps.task.models import Task, TaskResource, TaskStep
from apps.task.services.interface import complete_task, create_task, start_task


CHECK_STEPS = (
    "check_storage_access",
    "verify_repository",
    "refresh_repository_usage",
    "finalize_repository_check",
)
_ACTIVE_STATUSES = (Task.Status.PENDING, Task.Status.RUNNING)


def active_repository_check_task(repository: Repository) -> RepositoryTask | None:
    return (
        RepositoryTask.objects.filter(
            repository=repository,
            operation_type=RepositoryTask.OperationType.CHECK,
            task__status__in=_ACTIVE_STATUSES,
        )
        .select_related("task")
        .order_by("-created_at", "-id")
        .first()
    )


def enqueue_repository_check_task(
    *,
    repository: Repository,
    requested_by=None,
) -> RepositoryTask:
    """Accept one idempotent repository check and dispatch it after commit."""
    active = active_repository_check_task(repository)
    if active is not None:
        return active

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        active = active_repository_check_task(locked)
        if active is not None:
            return active
        conflicting = (
            RepositoryTask.objects.filter(
                repository=locked,
                task__status__in=_ACTIVE_STATUSES,
            )
            .select_related("task")
            .order_by("created_at", "id")
            .first()
        )
        if conflicting is not None:
            raise ValidationError(
                "Repository already has an active operation. Wait for it to finish."
            )
        if locked.status != Repository.Status.CREATED:
            raise ValidationError("Only a created repository can be checked.")

        task = create_task(
            organization_id=locked.organization_id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name=f"Check Repository · {locked.name}",
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "repository_id": locked.id,
                "operation_type": RepositoryTask.OperationType.CHECK,
            },
            resources=[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": locked.id,
                    "is_primary": True,
                }
            ],
            steps=list(CHECK_STEPS),
        )
        repository_task = RepositoryTask.objects.create(
            task=task,
            repository=locked,
            operation_type=RepositoryTask.OperationType.CHECK,
            owner_type=RepositoryExecutionTarget.OwnerType.CONTROLLER,
            owner_identity="hfl-check@worker",
            requested_by_id=getattr(requested_by, "id", None),
        )
        transaction.on_commit(
            lambda: _dispatch_repository_operation(repository_task.id)
        )
        return repository_task


def run_repository_check_task(*, repository_task_id: int) -> dict[str, Any]:
    """Run a repository check in the Worker and persist its visible result."""
    with repository_execution_lock(
        operation="repository-check",
        operation_id=repository_task_id,
    ) as acquired:
        if not acquired:
            return {
                "status": "locked",
                "repository_task_id": repository_task_id,
                "idempotent": True,
            }
        return _run_repository_check_task_locked(
            repository_task_id=repository_task_id
        )


def _run_repository_check_task_locked(*, repository_task_id: int) -> dict[str, Any]:
    repository_task = RepositoryTask.objects.select_related("task", "repository").get(
        pk=repository_task_id
    )
    task = repository_task.task
    if repository_task.operation_type != RepositoryTask.OperationType.CHECK:
        raise ValidationError("Repository task is not a check operation.")
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return task.result_payload or {"status": task.status}
    if task.status == Task.Status.PENDING:
        start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)

    repository = repository_task.repository
    health_persisted = False
    try:
        _set_step(task, "check_storage_access", TaskStep.Status.RUNNING, 10)
        health = probe_repository_health(repository)
        _set_step(task, "check_storage_access", TaskStep.Status.SUCCESS, 35)
        _set_step(task, "verify_repository", TaskStep.Status.SUCCESS, 60)

        repository.health = health
        repository.health_failures = 0
        repository.last_checked_at = timezone.now()
        repository.save(
            update_fields=[
                "health",
                "health_failures",
                "last_checked_at",
                "updated_at",
            ]
        )
        health_persisted = True
        _set_step(
            task,
            "refresh_repository_usage",
            TaskStep.Status.RUNNING,
            75,
        )
        sync_repository_usage(repository)
        _set_step(
            task,
            "refresh_repository_usage",
            TaskStep.Status.SUCCESS,
            95,
        )
        _set_step(
            task,
            "finalize_repository_check",
            TaskStep.Status.SUCCESS,
            100,
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.SUCCESS,
            result_payload={
                "status": "success",
                "repository_id": repository.id,
                "health": repository.health,
                "last_checked_at": repository.last_checked_at.isoformat(),
            },
        )
        return {"status": "success", "repository_task_id": repository_task.id}
    except Exception as exc:
        if not health_persisted:
            if is_repository_ownership_failure(exc):
                invalidate_repository_location_ownership(repository)
            Repository.objects.filter(pk=repository.id).update(
                health=Repository.Health.OFFLINE,
                last_checked_at=timezone.now(),
            )
        current_step = task.current_step or CHECK_STEPS[0]
        _set_step(task, current_step, TaskStep.Status.FAILED, int(task.progress or 0))
        error_code = "REPOSITORY_CHECK_FAILED"
        message = _safe_error_message(repository, exc)
        if isinstance(exc, RepositoryInitializationError):
            failure = classify_s3_validation_error(exc, operation="bucket_access")
            error_code = failure.code
            message = failure.message
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.FAILED,
            progress=int(task.progress or 0),
            error_code=error_code,
            error_message=message,
        )
        return {
            "status": "failed",
            "repository_task_id": repository_task.id,
            "error_code": error_code,
            "error": message,
        }


def _set_step(task: Task, step_name: str, status: str, progress: int) -> None:
    from apps.storage.services.internal.repository_operations import set_task_step

    set_task_step(task, step_name, status=status, progress=progress)


def _dispatch_repository_operation(repository_task_id: int) -> None:
    from apps.storage.tasks import execute_repository_operation

    execute_repository_operation.apply_async(
        kwargs={"repository_task_id": repository_task_id}
    )


def _safe_error_message(repository: Repository, exc: Exception) -> str:
    try:
        secrets_payload = resolve_repository_secrets(repository)
    except Exception:
        secrets_payload = {}
    return str(
        scrub_secrets(
            str(exc),
            extra_values=secret_values_for_scrub(repository, secrets_payload),
        )
    )


__all__ = [
    "active_repository_check_task",
    "enqueue_repository_check_task",
    "run_repository_check_task",
]
