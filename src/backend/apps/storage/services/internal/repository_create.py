"""Async repository create / NAS repair bind-remount via RepositoryTask.

HTTP create/repair acceptance returns quickly with ``status=creating``; a Celery
worker runs the previously synchronous initialize/remount work and finalizes the
repository row to ``created`` or ``create_failed`` (or ``created``+offline for
remount failures on an already-bound repository).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryExecutionTarget,
    RepositoryTask,
)
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    initialize_proxy_nas_repository,
    nas_mount_point,
    validate_proxy_for_repository,
)
from apps.storage.services.internal.proxy_fs_repository import (
    ProxyFSRepositoryError,
    initialize_proxy_fs_repository,
    validate_proxy_for_proxy_fs,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    initialize_s3_repository,
)
from apps.storage.services.internal.repository_secrets import (
    scrub_secrets,
    secret_values_for_scrub,
)
from apps.storage.services.internal.repository_task_naming import (
    repository_operation_display_name,
)
from apps.storage.services.internal.repository_usage import (
    enqueue_repository_usage_refresh,
    sync_repository_usage,
)
from apps.storage.services.internal.s3_validation_errors import (
    classify_s3_validation_error,
)
from apps.task.models import Task, TaskResource, TaskStep
from apps.task.services.interface import complete_task, create_task, start_task

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = (Task.Status.PENDING, Task.Status.RUNNING)

CREATE_STEPS = (
    "prepare_repository_create",
    "verify_repository_owner",
    "initialize_repository",
    "finalize_repository_create",
)

CREATE_OPERATION_TYPES = frozenset(
    {
        RepositoryTask.OperationType.CREATE_REPOSITORY,
        RepositoryTask.OperationType.REPAIR_BIND,
        RepositoryTask.OperationType.REPAIR_REMOUNT,
    }
)

# Covers the longest create/remount path: NAS remount (180s) + old-proxy unmount
# (60s) plus finalize overhead. Prevents reconcile from overlapping a still-running
# create after the short repository-operation advance lock expires.
_CREATE_RUN_LOCK_TIMEOUT_SECONDS = 300
_INITIALIZE_COMPLETE_PROGRESS = 85


def repository_create_task_payload(repository_task: RepositoryTask) -> dict[str, Any]:
    task = repository_task.task
    return {
        "task_uuid": str(task.task_uuid),
        "status": task.status,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "operation_type": repository_task.operation_type,
    }


def active_repository_create_task(repository: Repository) -> RepositoryTask | None:
    return (
        RepositoryTask.objects.filter(
            repository=repository,
            operation_type__in=CREATE_OPERATION_TYPES,
            task__status__in=ACTIVE_TASK_STATUSES,
        )
        .select_related("task")
        .order_by("-created_at", "-id")
        .first()
    )


def preflight_bound_proxy(*, repository: Repository) -> Node:
    """Fail fast before enqueue when the bound Proxy cannot run init."""
    if repository.repo_type == Repository.Type.PROXY_FS:
        return validate_proxy_for_proxy_fs(repository)
    if repository.repo_type == Repository.Type.NAS:
        return validate_proxy_for_repository(repository)
    raise ValidationError("Repository type does not require a bound proxy.")


def enqueue_repository_create_task(
    *,
    repository: Repository,
    operation_type: str = RepositoryTask.OperationType.CREATE_REPOSITORY,
    requested_by=None,
    dispatch: bool = True,
    remount_previous_node_id: int | None = None,
    remount_previous_mount_path: str | None = None,
) -> RepositoryTask:
    if operation_type not in CREATE_OPERATION_TYPES:
        raise ValidationError({"operation_type": "Unsupported repository create operation."})
    if (
        operation_type == RepositoryTask.OperationType.REPAIR_REMOUNT
        and remount_previous_node_id is None
    ):
        raise ValidationError(
            {"detail": "Remount requires the previous proxy node id for rollback."}
        )

    existing = active_repository_create_task(repository)
    if existing is not None:
        return existing

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        active = active_repository_create_task(locked)
        if active is not None:
            return active
        if locked.status not in {
            Repository.Status.CREATING,
            Repository.Status.CREATE_FAILED,
        }:
            # Remount may briefly set CREATING from CREATED; create path starts CREATING.
            if locked.status != Repository.Status.CREATED or operation_type != (
                RepositoryTask.OperationType.REPAIR_REMOUNT
            ):
                raise ValidationError(
                    {
                        "detail": (
                            f"Repository in status {locked.status} cannot accept "
                            f"operation {operation_type}."
                        )
                    }
                )

        owner_type, owner_node_id, owner_identity, target = _resolve_create_owner(locked)
        if locked.status != Repository.Status.CREATING:
            locked.status = Repository.Status.CREATING
            locked.save(update_fields=["status", "updated_at"])

        action_label = {
            RepositoryTask.OperationType.CREATE_REPOSITORY: "Create Repository",
            RepositoryTask.OperationType.REPAIR_BIND: "Bind Proxy",
            RepositoryTask.OperationType.REPAIR_REMOUNT: "Remount Repository",
        }[operation_type]

        request_payload: dict[str, Any] = {
            "repository_id": locked.id,
            "operation_type": operation_type,
            "repo_type": locked.repo_type,
            "bind_node_id": locked.bind_node_id,
        }
        if remount_previous_node_id is not None:
            request_payload["previous_bind_node_id"] = int(remount_previous_node_id)
        if remount_previous_mount_path is not None:
            request_payload["previous_proxy_mount_path"] = str(
                remount_previous_mount_path or ""
            ).strip()

        task = create_task(
            organization_id=locked.organization_id,
            task_type=Task.Type.REPOSITORY_OPERATION,
            display_name=repository_operation_display_name(
                action_label=action_label,
                repository=locked,
                target=target,
            ),
            trigger_type=Task.TriggerType.MANUAL,
            request_payload=request_payload,
            resources=[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": locked.id,
                    "is_primary": True,
                }
            ],
            steps=list(CREATE_STEPS),
            normalize_trigger_type=False,
        )
        repository_task = RepositoryTask.objects.create(
            task=task,
            repository=locked,
            execution_target=target,
            requested_by_id=getattr(requested_by, "id", None),
            operation_type=operation_type,
            owner_type=owner_type,
            owner_node_id=owner_node_id,
            owner_identity=owner_identity,
            due_at=timezone.now(),
        )
        if target is not None:
            if target.active_task_id:
                active_status = Task.objects.filter(pk=target.active_task_id).values_list(
                    "status", flat=True
                ).first()
                if active_status in {
                    Task.Status.SUCCESS,
                    Task.Status.FAILED,
                    Task.Status.CANCELLED,
                    Task.Status.TIMEOUT,
                }:
                    target.active_task = None
                else:
                    raise ValidationError(
                        {
                            "detail": (
                                f"Repository target {target.target_key} already has an active task."
                            )
                        }
                    )
            target.active_task = task
            target.is_active = True
            target.save(update_fields=["active_task", "is_active", "updated_at"])

        if dispatch:
            transaction.on_commit(lambda: _dispatch_create_task(repository_task.id))
        return repository_task


def run_repository_create_task(*, repository_task_id: int) -> dict[str, Any]:
    lock_key = f"storage:repository-create:run:{int(repository_task_id)}"
    owner_token = str(uuid4())
    if not cache.add(lock_key, owner_token, timeout=_CREATE_RUN_LOCK_TIMEOUT_SECONDS):
        return {
            "status": "locked",
            "repository_task_id": repository_task_id,
            "idempotent": True,
        }
    try:
        return _run_repository_create_task_locked(repository_task_id=repository_task_id)
    finally:
        if cache.get(lock_key) == owner_token:
            cache.delete(lock_key)


def _run_repository_create_task_locked(*, repository_task_id: int) -> dict[str, Any]:
    repository_task = RepositoryTask.objects.select_related(
        "task", "repository", "execution_target"
    ).get(pk=repository_task_id)
    task = repository_task.task
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return {"status": task.status, "idempotent": True}

    started_now = task.status == Task.Status.PENDING
    if started_now:
        start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
        task.refresh_from_db()
    elif task.status != Task.Status.RUNNING:
        return {"status": task.status, "idempotent": True}

    repository = Repository.objects.get(pk=repository_task.repository_id)
    if (
        repository.status == Repository.Status.CREATED
        and repository.health == Repository.Health.ONLINE
        and task.status == Task.Status.RUNNING
    ):
        # Worker died after repository finalize but before task completion.
        return _complete_create_success(repository_task)
    if (
        task.status == Task.Status.RUNNING
        and _remount_failure_rollback_applied(repository_task, repository)
    ):
        # Failure rollback persisted, but the task never reached a terminal status.
        return _complete_create_failure_already_applied(
            repository_task,
            error_code="REPOSITORY_CREATE_FAILED",
            message="Repository remount failed; previous proxy binding was restored.",
        )

    initialize_done = _initialize_step_complete(task)
    # In-process latch: step rows may fail to persist after physical work returns.
    # Fail paths must not unwind bind/remount once this flips true.
    physical_initialize_done = initialize_done
    try:
        if not initialize_done:
            _set_create_step(task, "prepare_repository_create", TaskStep.Status.SUCCESS, 10)
            _set_create_step(task, "verify_repository_owner", TaskStep.Status.RUNNING, 20)
            if repository_task.operation_type != RepositoryTask.OperationType.REPAIR_REMOUNT:
                if repository.repo_type in {Repository.Type.NAS, Repository.Type.PROXY_FS}:
                    preflight_bound_proxy(repository=repository)
            _set_create_step(task, "verify_repository_owner", TaskStep.Status.SUCCESS, 35)
            _set_create_step(task, "initialize_repository", TaskStep.Status.RUNNING, 45)

            if repository_task.operation_type == RepositoryTask.OperationType.REPAIR_REMOUNT:
                _run_repair_remount(repository_task)
            else:
                _run_initialize(repository)
            physical_initialize_done = True

            _set_create_step(
                task,
                "initialize_repository",
                TaskStep.Status.SUCCESS,
                _INITIALIZE_COMPLETE_PROGRESS,
            )

        _set_create_step(task, "finalize_repository_create", TaskStep.Status.RUNNING, 90)
        return _complete_create_success(repository_task)
    except RepositoryAlreadyExistsError as exc:
        message = _safe_error_message(repository, str(exc))
        # CREATE re-entry after a successful physical init must finalize, not
        # delete. REPAIR_BIND conflicts still mean "target already occupied" and
        # must restore the unbound NAS row instead of marking it online.
        if (
            not started_now
            and repository.status == Repository.Status.CREATING
            and repository_task.operation_type
            == RepositoryTask.OperationType.CREATE_REPOSITORY
        ):
            logger.warning(
                "repository create resume treating already-exists as success "
                "repository_id=%s repository_task_id=%s",
                repository.id,
                repository_task.id,
            )
            return _complete_create_success(repository_task)
        if repository_task.operation_type == RepositoryTask.OperationType.REPAIR_BIND:
            _fail_repair_bind_already_exists(repository_task, message=message)
        else:
            _fail_create_already_exists(repository_task, message=message)
        return {
            "status": "failed",
            "repository_task_id": repository_task.id,
            "error_code": REPOSITORY_ALREADY_EXISTS_CODE,
            "error": message,
        }
    except Exception as exc:
        message = _safe_error_message(repository, _exception_message(exc))
        error_code = _create_error_code(exc)
        if isinstance(exc, RepositoryInitializationError):
            failure = classify_s3_validation_error(exc, operation="bucket_access")
            message = failure.message
            error_code = failure.code
        _fail_create_keep_row(
            repository_task,
            error_code=error_code,
            message=message,
            physical_initialize_done=physical_initialize_done,
        )
        return {
            "status": "failed",
            "repository_task_id": repository_task.id,
            "error_code": error_code,
            "error": message,
        }


def _initialize_step_complete(task: Task) -> bool:
    current_step = str(task.current_step or "")
    progress = int(task.progress or 0)
    if current_step == "finalize_repository_create":
        return True
    if current_step == "initialize_repository" and progress >= _INITIALIZE_COMPLETE_PROGRESS:
        return True
    return False


def _complete_create_success(repository_task: RepositoryTask) -> dict[str, Any]:
    task = repository_task.task
    with transaction.atomic():
        repository = Repository.objects.select_for_update().get(
            pk=repository_task.repository_id
        )
        repository.status = Repository.Status.CREATED
        repository.health = Repository.Health.ONLINE
        repository.last_checked_at = timezone.now()
        repository.save(
            update_fields=["status", "health", "last_checked_at", "updated_at"]
        )
        _set_create_step(task, "finalize_repository_create", TaskStep.Status.SUCCESS, 100)
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.SUCCESS,
            progress=100,
            result_payload={"repository_id": repository.id, "status": "created"},
        )
    _clear_target_active_task(repository_task)
    repository = Repository.objects.get(pk=repository_task.repository_id)
    try:
        if repository.repo_type == Repository.Type.PROXY_FS:
            sync_repository_usage(repository)
        else:
            enqueue_repository_usage_refresh(
                organization_id=repository.organization_id,
                repository_ids=[repository.id],
                force=True,
                trigger="storage.repository.create_async",
            )
    except Exception:
        logger.exception(
            "repository create usage refresh failed repository_id=%s",
            repository.id,
        )
    return {"status": "success", "repository_task_id": repository_task.id}


def _resolve_create_owner(
    repository: Repository,
) -> tuple[str, int | None, str, RepositoryExecutionTarget | None]:
    if repository.repo_type == Repository.Type.S3:
        return (
            RepositoryExecutionTarget.OwnerType.CONTROLLER,
            None,
            "hfl-create@controller",
            None,
        )

    node_id = int(repository.bind_node_id or 0) or None
    if not node_id:
        raise ValidationError({"detail": "Bound proxy node is required for repository create."})

    target_key = f"repository:{repository.id}"
    target, _created = RepositoryExecutionTarget.objects.update_or_create(
        target_key=target_key,
        defaults={
            "organization_id": repository.organization_id,
            "repository": repository,
            "owner_type": RepositoryExecutionTarget.OwnerType.NODE,
            "owner_node_id": node_id,
            "owner_identity": f"hfl-create@node-{node_id}",
            "repository_subdir": "",
            "is_active": True,
        },
    )
    return (
        RepositoryExecutionTarget.OwnerType.NODE,
        node_id,
        f"hfl-create@node-{node_id}",
        target,
    )


def _run_initialize(repository: Repository) -> None:
    if repository.repo_type == Repository.Type.NAS:
        initialize_proxy_nas_repository(repository)
        return
    if repository.repo_type == Repository.Type.PROXY_FS:
        initialize_proxy_fs_repository(repository)
        return
    if repository.repo_type == Repository.Type.S3:
        initialize_s3_repository(repository)
        return
    raise ValidationError(f"Unsupported repository type for create: {repository.repo_type}")


def _run_repair_remount(repository_task: RepositoryTask) -> None:
    from apps.storage.services.internal.nas_repair import (
        _remount_on_new_proxy,
        _unmount_on_old_proxy,
    )

    repository = Repository.objects.get(pk=repository_task.repository_id)
    payload = repository_task.task.request_payload or {}
    previous_node_id = payload.get("previous_bind_node_id")
    # Prefer the bind frozen at enqueue time. After a failure rollback the live
    # repository.bind_node_id points at the previous proxy and must not be used
    # as the remount target on resume.
    intended_new_node_id = payload.get("bind_node_id") or repository.bind_node_id
    if not intended_new_node_id:
        raise ValidationError("Bound proxy node not found.")
    new_node = Node.objects.filter(
        id=int(intended_new_node_id),
        organization_id=repository.organization_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if new_node is None:
        raise ValidationError("Bound proxy node not found.")
    if new_node.availability != Node.Availability.ONLINE:
        raise ValidationError(f'Bound proxy node "{new_node.name}" is not online.')

    _remount_on_new_proxy(
        organization_id=repository.organization_id,
        repository=repository,
        new_node=new_node,
    )
    if previous_node_id:
        _unmount_on_old_proxy(
            organization_id=repository.organization_id,
            repository=repository,
            old_node_id=int(previous_node_id),
        )


def _fail_repair_bind_already_exists(repository_task: RepositoryTask, *, message: str) -> None:
    """Keep the unbound NAS row when bind discovers an existing Kopia repository."""
    task = repository_task.task
    with transaction.atomic():
        repository = (
            Repository.objects.select_for_update()
            .filter(pk=repository_task.repository_id)
            .first()
        )
        _set_create_step(
            task,
            str(task.current_step or "initialize_repository"),
            TaskStep.Status.FAILED,
            max(1, int(task.progress or 0)),
        )
        if repository is not None:
            _restore_unbound_nas(repository)
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.FAILED,
            progress=max(1, int(task.progress or 0)),
            error_code=REPOSITORY_ALREADY_EXISTS_CODE,
            error_message=message[:2000],
        )
    _clear_target_active_task(repository_task)


def _fail_create_already_exists(repository_task: RepositoryTask, *, message: str) -> None:
    task = repository_task.task
    repository = repository_task.repository
    credential_id = repository.credential_id
    _set_create_step(
        task,
        str(task.current_step or "initialize_repository"),
        TaskStep.Status.FAILED,
        max(1, int(task.progress or 0)),
    )
    # Finalize the platform task, then detach PROTECT'd execution-target links
    # before deleting the repository row.
    _clear_target_active_task(repository_task)
    complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=Task.Status.FAILED,
        progress=max(1, int(task.progress or 0)),
        error_code=REPOSITORY_ALREADY_EXISTS_CODE,
        error_message=message[:2000],
    )
    repository_id = int(repository.id)
    RepositoryTask.objects.filter(repository_id=repository_id).update(
        execution_target=None
    )
    RepositoryExecutionTarget.objects.filter(repository_id=repository_id).delete()
    Repository.objects.filter(pk=repository_id).delete()
    if credential_id:
        Credential.objects.filter(id=credential_id).delete()


def _fail_create_keep_row(
    repository_task: RepositoryTask,
    *,
    error_code: str,
    message: str,
    physical_initialize_done: bool | None = None,
) -> None:
    task = repository_task.task
    # Prefer the in-process latch from the runner: step persistence can lag
    # behind a successful remount/initialize return. Persisted step progress is
    # only the fallback for callers that omit the latch.
    if physical_initialize_done is None:
        initialize_done = _initialize_step_complete(task)
    else:
        initialize_done = physical_initialize_done
    with transaction.atomic():
        repository = (
            Repository.objects.select_for_update()
            .filter(pk=repository_task.repository_id)
            .first()
        )
        _set_create_step(
            task,
            str(task.current_step or "initialize_repository"),
            TaskStep.Status.FAILED,
            max(1, int(task.progress or 0)),
        )
        if repository is not None:
            if (
                not initialize_done
                and repository_task.operation_type
                == RepositoryTask.OperationType.REPAIR_BIND
            ):
                _restore_unbound_nas(repository)
            elif (
                not initialize_done
                and repository_task.operation_type
                == RepositoryTask.OperationType.REPAIR_REMOUNT
            ):
                _restore_previous_proxy_binding(repository_task, repository)
            elif repository_task.operation_type in {
                RepositoryTask.OperationType.REPAIR_BIND,
                RepositoryTask.OperationType.REPAIR_REMOUNT,
            }:
                repository.status = Repository.Status.CREATED
                repository.health = Repository.Health.OFFLINE
                repository.last_checked_at = timezone.now()
                repository.save(
                    update_fields=["status", "health", "last_checked_at", "updated_at"]
                )
            else:
                repository.status = Repository.Status.CREATE_FAILED
                repository.health = Repository.Health.OFFLINE
                repository.last_checked_at = timezone.now()
                repository.save(
                    update_fields=["status", "health", "last_checked_at", "updated_at"]
                )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.FAILED,
            progress=max(1, int(task.progress or 0)),
            error_code=error_code,
            error_message=message[:2000],
        )
    _clear_target_active_task(repository_task)


def _remount_failure_rollback_applied(
    repository_task: RepositoryTask,
    repository: Repository,
) -> bool:
    if repository_task.operation_type != RepositoryTask.OperationType.REPAIR_REMOUNT:
        return False
    if repository.status != Repository.Status.CREATED:
        return False
    if repository.health != Repository.Health.OFFLINE:
        return False
    payload = repository_task.task.request_payload or {}
    previous_node_id = payload.get("previous_bind_node_id")
    intended_new_node_id = payload.get("bind_node_id")
    if not previous_node_id or not intended_new_node_id:
        return False
    return (
        int(repository.bind_node_id or 0) == int(previous_node_id)
        and int(intended_new_node_id) != int(previous_node_id)
    )


def _complete_create_failure_already_applied(
    repository_task: RepositoryTask,
    *,
    error_code: str,
    message: str,
) -> dict[str, Any]:
    task = repository_task.task
    with transaction.atomic():
        _set_create_step(
            task,
            str(task.current_step or "initialize_repository"),
            TaskStep.Status.FAILED,
            max(1, int(task.progress or 0)),
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.FAILED,
            progress=max(1, int(task.progress or 0)),
            error_code=error_code,
            error_message=message[:2000],
        )
    _clear_target_active_task(repository_task)
    return {
        "status": "failed",
        "repository_task_id": repository_task.id,
        "error_code": error_code,
        "error": message,
        "idempotent": True,
    }


def _restore_unbound_nas(repository: Repository) -> None:
    config = dict(repository.config or {})
    config.pop("proxy_mount_path", None)
    repository.config = config
    repository.bind_node_type = None
    repository.bind_node_id = None
    repository.status = Repository.Status.CREATED
    repository.health = Repository.Health.UNVERIFIED
    repository.save(
        update_fields=[
            "config",
            "bind_node_type",
            "bind_node_id",
            "status",
            "health",
            "updated_at",
        ]
    )


def _restore_previous_proxy_binding(
    repository_task: RepositoryTask,
    repository: Repository,
) -> None:
    payload = repository_task.task.request_payload or {}
    previous_node_id = payload.get("previous_bind_node_id")
    previous_mount = str(payload.get("previous_proxy_mount_path") or "").strip()
    config = dict(repository.config or {})
    if previous_node_id:
        repository.bind_node_type = Repository.BindNodeType.PROXY
        repository.bind_node_id = int(previous_node_id)
        if previous_mount:
            config["proxy_mount_path"] = previous_mount
        else:
            config["proxy_mount_path"] = nas_mount_point(
                repository, node_id=int(previous_node_id)
            )
    else:
        # Without the prior binding we cannot safely invent a rollback target.
        # Keep the failed remount binding visible as offline so operators can
        # repair explicitly instead of silently keeping a half-swapped proxy.
        logger.error(
            "repository remount failure missing previous_bind_node_id "
            "repository_id=%s repository_task_id=%s bind_node_id=%s",
            repository.id,
            repository_task.id,
            repository.bind_node_id,
        )
    repository.config = config
    repository.status = Repository.Status.CREATED
    repository.health = Repository.Health.OFFLINE
    repository.last_checked_at = timezone.now()
    repository.save(
        update_fields=[
            "config",
            "bind_node_type",
            "bind_node_id",
            "status",
            "health",
            "last_checked_at",
            "updated_at",
        ]
    )


def _clear_target_active_task(repository_task: RepositoryTask) -> None:
    target = repository_task.execution_target
    if target is None:
        return
    if target.active_task_id == repository_task.task_id:
        target.active_task = None
        target.save(update_fields=["active_task", "updated_at"])


def _set_create_step(task: Task, step_name: str, status: str, progress: int) -> None:
    from apps.storage.services.internal.repository_operations import set_task_step

    task.refresh_from_db(fields=["current_step", "progress"])
    set_task_step(task, step_name, status=status, progress=progress)


def _dispatch_create_task(repository_task_id: int) -> None:
    from apps.storage.tasks import execute_repository_operation

    execute_repository_operation.apply_async(kwargs={"repository_task_id": repository_task_id})


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, DRFValidationError):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            parts = []
            for key, value in detail.items():
                if isinstance(value, (list, tuple)):
                    parts.append(f"{key}: {'; '.join(str(item) for item in value)}")
                else:
                    parts.append(f"{key}: {value}")
            if parts:
                return "; ".join(parts)
        return str(detail or exc)
    if isinstance(exc, ValidationError):
        messages = list(getattr(exc, "messages", []) or [])
        if messages:
            return "; ".join(str(item) for item in messages)
        return str(exc)
    return str(exc)


def _create_error_code(exc: Exception) -> str:
    if isinstance(exc, RepositoryAlreadyExistsError):
        return REPOSITORY_ALREADY_EXISTS_CODE
    if isinstance(exc, RepositoryInitializationError):
        return classify_s3_validation_error(exc, operation="bucket_access").code
    if isinstance(exc, NASRepositoryError):
        return exc.error_code
    if isinstance(exc, ProxyFSRepositoryError):
        return "REPOSITORY_CREATE_FAILED"
    if isinstance(exc, (ValidationError, DRFValidationError)):
        return "REPOSITORY_CREATE_INVALID"
    if isinstance(exc, TimeoutError):
        return "REPOSITORY_CREATE_TIMEOUT"
    return "REPOSITORY_CREATE_FAILED"


def _safe_error_message(repository: Repository, message: str) -> str:
    try:
        from apps.storage.services.internal.repository_secrets import (
            resolve_repository_secrets,
        )

        secrets_payload = resolve_repository_secrets(repository)
    except Exception:
        secrets_payload = {}
    return str(
        scrub_secrets(
            message,
            extra_values=secret_values_for_scrub(repository, secrets_payload),
        )
    )


__all__ = [
    "CREATE_OPERATION_TYPES",
    "active_repository_create_task",
    "enqueue_repository_create_task",
    "preflight_bound_proxy",
    "repository_create_task_payload",
    "run_repository_create_task",
]
