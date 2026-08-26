"""Async repository create / NAS repair bind-remount via RepositoryTask.

HTTP create/repair acceptance returns quickly with ``status=creating``; a Celery
worker runs the previously synchronous initialize/remount work and finalizes the
repository row to ``created`` or ``create_failed`` (or ``created``+offline for
remount failures on an already-bound repository).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable
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
    RepositoryLocationClaim,
    RepositoryTask,
)
from apps.storage.services.internal.nas_repository import (
    NASRepositoryError,
    check_proxy_nas_repository,
    initialize_proxy_nas_repository,
    nas_mount_point,
    nas_proxy_repository_subdir,
    validate_proxy_for_repository,
)
from apps.storage.services.internal.proxy_fs_repository import (
    ProxyFSRepositoryError,
    check_proxy_fs_repository,
    initialize_proxy_fs_repository,
    validate_proxy_for_proxy_fs,
)
from apps.storage.services.internal.repository_errors import (
    REPOSITORY_ALREADY_EXISTS_CODE,
    RepositoryAlreadyExistsError,
)
from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryCreateAgentTaskResult,
    RepositoryAgentOperationStateUnknown,
    repository_create_has_durable_agent_task,
)
from apps.storage.services.internal.repository_initializer import (
    RepositoryInitializationError,
    check_s3_repository,
    initialize_s3_repository,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_initializing,
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    mark_repository_location_residual,
    release_repository_location,
    reserve_repository_location,
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
)
from apps.storage.services.internal.s3_validation_errors import (
    classify_s3_validation_error,
)
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import (
    append_task_event,
    complete_task,
    create_task,
    resume_waiting_task,
    start_task,
)

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = (
    Task.Status.PENDING,
    Task.Status.WAITING,
    Task.Status.BLOCKED,
    Task.Status.RUNNING,
)

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


def _repository_claim_locator(repository: Repository) -> dict[str, Any]:
    """Return the exact Claim coordinates for a bound NAS repository."""
    if repository.repo_type != Repository.Type.NAS or not repository.bind_node_id:
        return {}
    return {
        "owner_node_id": int(repository.bind_node_id),
        "repository_subdir": nas_proxy_repository_subdir(repository),
    }


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
    remount_new_claim_id: int | None = None,
    residual_recovery_claim_ids: Iterable[int] = (),
    intended_bind_node_id: int | None = None,
) -> RepositoryTask:
    if operation_type not in CREATE_OPERATION_TYPES:
        raise ValidationError(
            {"operation_type": "Unsupported repository create operation."}
        )
    if (
        operation_type == RepositoryTask.OperationType.REPAIR_REMOUNT
        and remount_previous_node_id is None
    ):
        raise ValidationError(
            {"detail": "Remount requires the previous proxy node id for rollback."}
        )
    recovery_claim_ids = sorted(
        {int(claim_id) for claim_id in residual_recovery_claim_ids if claim_id}
    )
    residual_bind_recovery = bool(recovery_claim_ids)
    if residual_bind_recovery and (
        operation_type != RepositoryTask.OperationType.REPAIR_BIND
        or not intended_bind_node_id
    ):
        raise ValidationError(
            {"detail": "Residual cleanup is only supported for an explicit Proxy bind."}
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
            accepts_created = operation_type == RepositoryTask.OperationType.REPAIR_REMOUNT
            accepts_created = accepts_created or (
                residual_bind_recovery
                and operation_type == RepositoryTask.OperationType.REPAIR_BIND
                and locked.bind_node_id is None
            )
            if locked.status != Repository.Status.CREATED or not accepts_created:
                raise ValidationError(
                    {
                        "detail": (
                            f"Repository in status {locked.status} cannot accept "
                            f"operation {operation_type}."
                        )
                    }
                )

        if residual_bind_recovery:
            owner_type = RepositoryExecutionTarget.OwnerType.CONTROLLER
            owner_node_id = None
            owner_identity = "hfl-repair@controller"
            target = None
        else:
            owner_type, owner_node_id, owner_identity, target = _resolve_create_owner(
                locked
            )
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
            "bind_node_id": intended_bind_node_id or locked.bind_node_id,
        }
        if residual_bind_recovery:
            request_payload["residual_recovery_claim_ids"] = recovery_claim_ids
        if remount_previous_node_id is not None:
            request_payload["previous_bind_node_id"] = int(remount_previous_node_id)
        if remount_previous_mount_path is not None:
            request_payload["previous_proxy_mount_path"] = str(
                remount_previous_mount_path or ""
            ).strip()
        if remount_new_claim_id is not None:
            request_payload["remount_new_claim_id"] = int(remount_new_claim_id)

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
            steps=(
                ["cleanup_failed_provisioning_targets", *CREATE_STEPS]
                if residual_bind_recovery
                else list(CREATE_STEPS)
            ),
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
                active_status = (
                    Task.objects.filter(pk=target.active_task_id)
                    .values_list("status", flat=True)
                    .first()
                )
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


def retry_repository_create(
    *,
    repository: Repository,
    requested_by=None,
) -> RepositoryTask:
    """Retry initialization on the existing failed Repository identity."""
    if repository.status != Repository.Status.CREATE_FAILED:
        active = active_repository_create_task(repository)
        if active is not None:
            return active
        raise ValidationError(
            {"detail": "Only a failed repository initialization can be retried."}
        )
    if repository.repo_type == Repository.Type.NAS and not repository.bind_node_id:
        raise ValidationError(
            {
                "detail": (
                    "Direct NAS repositories initialize when a backup source first "
                    "uses them."
                )
            }
        )
    if not repository.credential_id:
        raise ValidationError(
            {"detail": "Repository credentials are unavailable for retry."}
        )
    if repository.repo_type in {Repository.Type.NAS, Repository.Type.PROXY_FS}:
        preflight_bound_proxy(repository=repository)

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        if locked.status != Repository.Status.CREATE_FAILED:
            active = active_repository_create_task(locked)
            if active is not None:
                return active
            raise ValidationError(
                {"detail": "Repository initialization is no longer retryable."}
            )
        from apps.storage.services.internal.repository_location import (
            reserve_repository_location,
        )

        reserve_repository_location(locked)
        return enqueue_repository_create_task(
            repository=locked,
            operation_type=RepositoryTask.OperationType.CREATE_REPOSITORY,
            requested_by=requested_by,
        )


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
        with repository_execution_lock(
            operation="repository-create",
            operation_id=repository_task_id,
        ) as acquired:
            if not acquired:
                return {
                    "status": "locked",
                    "repository_task_id": repository_task_id,
                    "idempotent": True,
                }
            return _run_repository_create_task_locked(
                repository_task_id=repository_task_id
            )
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
    elif task.status in {Task.Status.WAITING, Task.Status.BLOCKED}:
        resume_waiting_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
        )
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
    if task.status == Task.Status.RUNNING and _remount_failure_rollback_applied(
        repository_task, repository
    ):
        # Failure rollback persisted, but the task never reached a terminal status.
        return _complete_create_failure_already_applied(
            repository_task,
            error_code="REPOSITORY_CREATE_FAILED",
            message="Repository remount failed; previous proxy binding was restored.",
        )

    try:
        recovery_wait = _advance_failed_provisioning_cleanup_and_bind(
            repository_task=repository_task,
        )
        if recovery_wait is not None:
            return recovery_wait
    except Exception as exc:
        message = _safe_error_message(repository, _exception_message(exc))
        _fail_create_keep_row(
            repository_task,
            error_code="DIRECT_NAS_BIND_RECOVERY_FAILED",
            message=message,
            physical_initialize_done=False,
        )
        return {
            "status": "failed",
            "repository_task_id": repository_task.id,
            "error_code": "DIRECT_NAS_BIND_RECOVERY_FAILED",
            "error": message,
        }
    repository = Repository.objects.get(pk=repository_task.repository_id)
    repository_task.refresh_from_db()
    task.refresh_from_db()
    initialize_done = _initialize_step_complete(task)
    # In-process latch: step rows may fail to persist after physical work returns.
    # Fail paths must not unwind bind/remount once this flips true.
    physical_initialize_done = initialize_done
    try:
        if not initialize_done:
            _set_create_step(
                task, "prepare_repository_create", TaskStep.Status.SUCCESS, 10
            )
            _set_create_step(
                task, "verify_repository_owner", TaskStep.Status.RUNNING, 20
            )
            if (
                repository_task.operation_type
                != RepositoryTask.OperationType.REPAIR_REMOUNT
            ):
                if repository.repo_type in {
                    Repository.Type.NAS,
                    Repository.Type.PROXY_FS,
                } and not repository_create_has_durable_agent_task(
                    repository_task=repository_task
                ):
                    preflight_bound_proxy(repository=repository)
            _set_create_step(
                task, "verify_repository_owner", TaskStep.Status.SUCCESS, 35
            )
            _set_create_step(task, "initialize_repository", TaskStep.Status.RUNNING, 45)
            if repository_task.operation_type in {
                RepositoryTask.OperationType.CREATE_REPOSITORY,
                RepositoryTask.OperationType.REPAIR_BIND,
            }:
                mark_repository_location_initializing(
                    repository,
                    **_repository_claim_locator(repository),
                )
            elif (
                repository_task.operation_type
                == RepositoryTask.OperationType.REPAIR_REMOUNT
            ):
                _prepare_remount_location(repository_task, repository)

            initialization_outcome = None
            if (
                repository_task.operation_type
                == RepositoryTask.OperationType.REPAIR_REMOUNT
            ):
                _run_repair_remount(repository_task)
            else:
                initialization_outcome = _run_initialize(
                    repository,
                    recovery=(
                        not started_now
                        or repository.location_claims.filter(
                            state=RepositoryLocationClaim.State.RESIDUAL
                        ).exists()
                    ),
                    repository_task=repository_task,
                )
            if isinstance(initialization_outcome, RepositoryCreateAgentTaskResult):
                if initialization_outcome.waiting or initialization_outcome.state_unknown:
                    return _mark_create_waiting(
                        repository_task,
                        initialization_outcome,
                    )
            physical_initialize_done = True
            if (
                repository_task.operation_type
                == RepositoryTask.OperationType.REPAIR_REMOUNT
            ):
                mark_repository_location_owned(
                    repository,
                    owner_node_id=int(repository.bind_node_id or 0) or None,
                    repository_subdir=nas_proxy_repository_subdir(repository),
                )
            else:
                mark_repository_location_owned(
                    repository,
                    **_repository_claim_locator(repository),
                )
            if _outcome_ownership_verified(initialization_outcome):
                mark_repository_location_ownership_verified(
                    repository,
                    **_repository_claim_locator(repository),
                )

            _set_create_step(
                task,
                "initialize_repository",
                TaskStep.Status.SUCCESS,
                _INITIALIZE_COMPLETE_PROGRESS,
            )

        _set_create_step(
            task, "finalize_repository_create", TaskStep.Status.RUNNING, 90
        )
        return _complete_create_success(repository_task)
    except RepositoryAgentOperationStateUnknown as exc:
        return _mark_create_waiting(
            repository_task,
            RepositoryCreateAgentTaskResult(
                waiting=False,
                state_unknown=True,
                node_task_id=repository_task.remote_task_id,
                result={"error_code": "REMOTE_RESULT_UNKNOWN", "detail": str(exc)},
            ),
        )
    except RepositoryAlreadyExistsError as exc:
        message = _safe_error_message(repository, str(exc))
        # Existing physical state may belong to an interrupted attempt or an
        # unrelated repository. Only a resumed task or an explicit retry with
        # a prior Claim may verify access and continue; a first attempt must
        # never adopt pre-existing storage.
        if (
            repository.status == Repository.Status.CREATING
            and repository_task.operation_type
            in {
                RepositoryTask.OperationType.CREATE_REPOSITORY,
                RepositoryTask.OperationType.REPAIR_BIND,
            }
            and _may_recover_existing_location(
                repository,
                resumed=not started_now,
                **_repository_claim_locator(repository),
            )
        ):
            try:
                verification_outcome = _verify_existing_repository_access(repository)
            except Exception as verify_exc:
                logger.warning(
                    "repository create rejected unverified existing location "
                    "repository_id=%s repository_task_id=%s error=%s",
                    repository.id,
                    repository_task.id,
                    _safe_error_message(repository, _exception_message(verify_exc)),
                )
                mark_repository_location_residual(
                    repository,
                    **_repository_claim_locator(repository),
                )
                _fail_create_keep_row(
                    repository_task,
                    error_code=REPOSITORY_ALREADY_EXISTS_CODE,
                    message=message,
                    physical_initialize_done=False,
                )
                return {
                    "status": "failed",
                    "repository_task_id": repository_task.id,
                    "error_code": REPOSITORY_ALREADY_EXISTS_CODE,
                    "error": message,
                }
            else:
                logger.warning(
                    "repository create verified existing repository access "
                    "repository_id=%s repository_task_id=%s",
                    repository.id,
                    repository_task.id,
                )
                mark_repository_location_owned(
                    repository,
                    **_repository_claim_locator(repository),
                )
                if _outcome_ownership_verified(verification_outcome):
                    mark_repository_location_ownership_verified(
                        repository,
                        **_repository_claim_locator(repository),
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
        if repository_task.operation_type in {
            RepositoryTask.OperationType.CREATE_REPOSITORY,
            RepositoryTask.OperationType.REPAIR_BIND,
        }:
            claim_locator = _repository_claim_locator(repository)
            if physical_initialize_done:
                mark_repository_location_owned(
                    repository,
                    **claim_locator,
                )
            else:
                mark_repository_location_residual(
                    repository,
                    **claim_locator,
                )
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


def _advance_failed_provisioning_cleanup_and_bind(
    *, repository_task: RepositoryTask
) -> dict[str, Any] | None:
    payload = (
        repository_task.task.request_payload
        if isinstance(repository_task.task.request_payload, dict)
        else {}
    )
    recovery_claim_ids = sorted(
        {
            int(value)
            for value in payload.get("residual_recovery_claim_ids", [])
            if str(value).isdigit()
        }
    )
    if not recovery_claim_ids:
        return None
    if repository_task.operation_type != RepositoryTask.OperationType.REPAIR_BIND:
        raise ValidationError("Residual cleanup continuation is not a Proxy bind.")

    from apps.storage.services.internal.repository_cleanup import (
        _repository_task_user,
        create_direct_nas_target_cleanup_task,
        ensure_direct_nas_cleanup_target_for_claim,
        repository_cleanup_preflight,
        run_repository_cleanup_task,
    )

    task = repository_task.task
    _set_create_step(
        task,
        "cleanup_failed_provisioning_targets",
        TaskStep.Status.RUNNING,
        5,
    )
    children = list(
        RepositoryTask.objects.filter(
            repository_id=repository_task.repository_id,
            triggered_by_task=task,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
        )
        .select_related("task", "execution_target")
        .order_by("id")
    )

    def child_claim_ids(child: RepositoryTask) -> set[int]:
        child_payload = (
            child.task.request_payload
            if isinstance(child.task.request_payload, dict)
            else {}
        )
        cleanup_plan = child_payload.get("cleanup_plan")
        cleanup_plan = cleanup_plan if isinstance(cleanup_plan, dict) else {}
        return {
            int(value)
            for value in cleanup_plan.get("location_claim_ids", [])
            if str(value).isdigit()
        }

    children_by_claim = {
        claim_id: child
        for child in children
        for claim_id in child_claim_ids(child)
    }
    repository = Repository.objects.get(pk=repository_task.repository_id)
    for claim_id in recovery_claim_ids:
        child = children_by_claim.get(claim_id)
        if child is None:
            claim = RepositoryLocationClaim.objects.filter(
                id=claim_id,
                repository=repository,
                organization_id=repository.organization_id,
                scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
                state=RepositoryLocationClaim.State.RESIDUAL,
            ).first()
            if claim is None:
                raise ValidationError(
                    "A retained Direct NAS recovery claim changed before cleanup."
                )
            target = ensure_direct_nas_cleanup_target_for_claim(
                repository=repository,
                claim=claim,
            )
            child = create_direct_nas_target_cleanup_task(
                repository=repository,
                target_id=target.id,
                triggered_by_task=task,
                requested_by=_repository_task_user(repository_task),
                force=False,
                dispatch=False,
            )
            children.append(child)
            children_by_claim[claim_id] = child
        if child.task.status in ACTIVE_TASK_STATUSES:
            run_repository_cleanup_task(repository_task_id=child.id)
            child.task.refresh_from_db()
        if child.task.status in ACTIVE_TASK_STATUSES:
            return {
                "status": "waiting",
                "repository_task_id": repository_task.id,
                "cleanup_task_uuid": str(child.task.task_uuid),
            }
        if child.task.status != Task.Status.SUCCESS:
            raise ValidationError(
                child.task.error_message
                or "Ownership-verified Direct NAS target cleanup failed."
            )

    child_task_ids = [child.task_id for child in children]
    intended_bind_node_id = int(payload.get("bind_node_id") or 0)
    if not intended_bind_node_id:
        raise ValidationError("The selected Proxy is unavailable.")

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        if locked.bind_node_id is not None or locked.status != Repository.Status.CREATING:
            raise ValidationError(
                "Repository binding state changed during residual cleanup."
            )
        claims = list(
            RepositoryLocationClaim.objects.select_for_update()
            .filter(
                id__in=recovery_claim_ids,
                repository=locked,
                organization_id=locked.organization_id,
                scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
                state=RepositoryLocationClaim.State.RESIDUAL,
            )
            .order_by("id")
        )
        if len(claims) != len(recovery_claim_ids):
            raise ValidationError(
                "A retained Direct NAS recovery claim changed before binding."
            )
        dependency_check = repository_cleanup_preflight(
            repository=locked,
            ignored_task_ids=[task.id, *child_task_ids],
        )
        blockers = [
            item
            for item in dependency_check["blockers"]
            if item.get("code") != "repository_ownership_unverified"
        ]
        if blockers:
            raise ValidationError(str(blockers[0].get("detail") or "Binding is blocked."))
        node = Node.objects.filter(
            id=intended_bind_node_id,
            organization_id=locked.organization_id,
            role=NodeRole.PROXY,
            availability=Node.Availability.ONLINE,
            is_deleted=False,
        ).first()
        if node is None:
            raise ValidationError("The selected Proxy is no longer online.")

        for claim in claims:
            release_repository_location(
                locked,
                owner_node_id=claim.owner_node_id,
                repository_subdir=claim.root_path,
            )
        locked.bind_node_type = Repository.BindNodeType.PROXY
        locked.bind_node_id = node.id
        config = dict(locked.config or {})
        config["proxy_mount_path"] = nas_mount_point(locked, node_id=node.id)
        locked.config = config
        locked.save(
            update_fields=[
                "bind_node_type",
                "bind_node_id",
                "config",
                "updated_at",
            ]
        )
        reserve_repository_location(locked)
        owner_type, owner_node_id, owner_identity, target = _resolve_create_owner(locked)
        RepositoryTask.objects.filter(id=repository_task.id).update(
            owner_type=owner_type,
            owner_node_id=owner_node_id,
            owner_identity=owner_identity,
            execution_target=target,
        )
        if target is not None:
            if target.active_task_id not in {None, task.id}:
                raise ValidationError("The selected Proxy repository target is busy.")
            target.active_task = task
            target.is_active = True
            target.save(update_fields=["active_task", "is_active", "updated_at"])

    _set_create_step(
        task,
        "cleanup_failed_provisioning_targets",
        TaskStep.Status.SUCCESS,
        10,
    )
    return None


def _initialize_step_complete(task: Task) -> bool:
    current_step = str(task.current_step or "")
    progress = int(task.progress or 0)
    if current_step == "finalize_repository_create":
        return True
    if (
        current_step == "initialize_repository"
        and progress >= _INITIALIZE_COMPLETE_PROGRESS
    ):
        return True
    return False


@transaction.atomic
def _mark_create_waiting(
    repository_task: RepositoryTask,
    result: RepositoryCreateAgentTaskResult,
) -> dict[str, Any]:
    """Park the product task without releasing its physical target lease."""

    task = Task.objects.select_for_update().get(pk=repository_task.task_id)
    # A terminal Agent callback can win the race with the worker that observed
    # the pending/unknown result.  Never regress that authoritative terminal
    # state back to WAITING or BLOCKED.
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return {
            "status": task.status,
            "repository_task_id": repository_task.id,
            "remote_task_id": str(result.node_task_id or ""),
            "idempotent": True,
        }
    state_unknown = bool(result.state_unknown)
    status = Task.Status.BLOCKED if state_unknown else Task.Status.WAITING
    error_code = "REMOTE_RESULT_UNKNOWN" if state_unknown else "AGENT_TASK_PENDING"
    message = (
        "Agent repository initialization exceeded its execution watchdog; the "
        "remote state is unknown and duplicate initialization is blocked."
        if state_unknown
        else "Repository initialization is waiting for the Agent to return a result."
    )
    task.status = status
    task.error_code = error_code
    task.error_message = message
    task.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "updated_at",
        ]
    )
    TaskStep.objects.filter(
        task_id=task.id,
        step_name="initialize_repository",
        status=TaskStep.Status.RUNNING,
    ).update(status=TaskStep.Status.WARNING)
    append_task_event(
        task=task,
        level=TaskEvent.Level.WARN,
        message=(
            "Repository initialization is waiting for the Agent"
            if not state_unknown
            else "Repository initialization entered an unknown remote state"
        ),
        metadata={
            "error_code": error_code,
            "remote_task_id": str(result.node_task_id or ""),
        },
    )
    return {
        "status": status,
        "repository_task_id": repository_task.id,
        "remote_task_id": str(result.node_task_id or ""),
        "error_code": error_code,
    }


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
        _set_create_step(
            task, "finalize_repository_create", TaskStep.Status.SUCCESS, 100
        )
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
        raise ValidationError(
            {"detail": "Bound proxy node is required for repository create."}
        )

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


def _run_initialize(
    repository: Repository,
    *,
    recovery: bool = False,
    repository_task: RepositoryTask | None = None,
):
    if repository.repo_type == Repository.Type.NAS:
        return initialize_proxy_nas_repository(
            repository,
            repository_task=repository_task,
        )
    if repository.repo_type == Repository.Type.PROXY_FS:
        return initialize_proxy_fs_repository(
            repository,
            repository_task=repository_task,
        )
    if repository.repo_type == Repository.Type.S3:
        initialize_s3_repository(repository, recovery=recovery)
        return
    raise ValidationError(
        f"Unsupported repository type for create: {repository.repo_type}"
    )


def _outcome_ownership_verified(outcome: object) -> bool:
    result = getattr(outcome, "result", None)
    return isinstance(result, dict) and result.get("ownership_verified") is True


def _verify_existing_repository_access(repository: Repository):
    """Prove this repository row can open the existing Kopia repository."""
    if repository.repo_type == Repository.Type.S3:
        check_s3_repository(repository)
        return
    if repository.repo_type == Repository.Type.NAS:
        return check_proxy_nas_repository(repository, health_only=True)
    if repository.repo_type == Repository.Type.PROXY_FS:
        return check_proxy_fs_repository(repository, health_only=True)
    raise ValidationError(
        f"Unsupported repository type for verification: {repository.repo_type}"
    )


def _may_recover_existing_location(
    repository: Repository,
    *,
    resumed: bool,
    owner_node_id: int | None = None,
    repository_subdir: str | None = None,
) -> bool:
    """Allow access verification only for a recoverable prior attempt."""
    recoverable_states = [
        RepositoryLocationClaim.State.OWNED,
        RepositoryLocationClaim.State.RESIDUAL,
    ]
    if resumed:
        recoverable_states.append(RepositoryLocationClaim.State.INITIALIZING)
    claims = repository.location_claims.filter(
        scope=RepositoryLocationClaim.Scope.REPOSITORY,
        state__in=recoverable_states,
    )
    if owner_node_id is not None:
        claims = claims.filter(owner_node_id=owner_node_id)
    if repository_subdir is not None:
        claims = claims.filter(root_path=str(repository_subdir).strip("/"))
    return claims.exists()


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
    _finalize_remount_location(repository_task, repository)


def _prepare_remount_location(
    repository_task: RepositoryTask,
    repository: Repository,
) -> None:
    """Ensure only the intended new Proxy Claim enters initialization."""
    payload = repository_task.task.request_payload or {}
    intended_node_id = int(payload.get("bind_node_id") or 0)
    if not intended_node_id:
        raise ValidationError("Remount target proxy is unavailable.")
    claim_id = int(payload.get("remount_new_claim_id") or 0)
    claim = None
    if claim_id:
        claim = repository.location_claims.filter(
            id=claim_id,
            owner_node_id=intended_node_id,
            root_path=nas_proxy_repository_subdir(repository),
            state__in=[
                RepositoryLocationClaim.State.RESERVED,
                RepositoryLocationClaim.State.INITIALIZING,
                RepositoryLocationClaim.State.RESIDUAL,
                RepositoryLocationClaim.State.OWNED,
            ],
        ).first()
    if claim is None:
        # Compatibility for a remount accepted by an older Controller before
        # the Claim handoff fields were introduced.
        claim = reserve_repository_location(repository)
    if claim is None or int(claim.owner_node_id or 0) != intended_node_id:
        raise ValidationError("Remount target location could not be reserved.")
    mark_repository_location_initializing(
        repository,
        owner_node_id=intended_node_id,
        repository_subdir=nas_proxy_repository_subdir(repository),
        include_residual=True,
    )


def _finalize_remount_location(
    repository_task: RepositoryTask,
    repository: Repository,
) -> None:
    """Commit the new Proxy Claim and release only the previous Proxy Claim."""
    payload = repository_task.task.request_payload or {}
    intended_node_id = int(payload.get("bind_node_id") or 0)
    previous_node_id = int(payload.get("previous_bind_node_id") or 0)
    repository_subdir = nas_proxy_repository_subdir(repository)
    mark_repository_location_owned(
        repository,
        owner_node_id=intended_node_id or None,
        repository_subdir=repository_subdir,
    )
    if previous_node_id and previous_node_id != intended_node_id:
        release_repository_location(
            repository,
            owner_node_id=previous_node_id,
            repository_subdir=repository_subdir,
        )


def _fail_repair_bind_already_exists(
    repository_task: RepositoryTask, *, message: str
) -> None:
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
            mark_repository_location_residual(
                repository,
                **_repository_claim_locator(repository),
            )
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


def _fail_create_already_exists(
    repository_task: RepositoryTask, *, message: str
) -> None:
    task = repository_task.task
    with transaction.atomic():
        repository = Repository.objects.select_for_update().get(
            pk=repository_task.repository_id
        )
        credential_id = repository.credential_id
        organization_id = repository.organization_id
        _set_create_step(
            task,
            str(task.current_step or "initialize_repository"),
            TaskStep.Status.FAILED,
            max(1, int(task.progress or 0)),
        )
        # Finalize the platform task, then detach PROTECT'd execution-target
        # links before deleting the failed repository row.
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
        release_repository_location(repository)
        RepositoryTask.objects.filter(repository_id=repository_id).update(
            execution_target=None
        )
        RepositoryExecutionTarget.objects.filter(repository_id=repository_id).delete()
        Repository.objects.filter(pk=repository_id).delete()
        if (
            credential_id
            and not Repository.objects.filter(
                organization_id=organization_id,
                credential_id=credential_id,
            ).exists()
        ):
            Credential.objects.filter(
                organization_id=organization_id,
                id=credential_id,
            ).delete()


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
    return int(repository.bind_node_id or 0) == int(previous_node_id) and int(
        intended_new_node_id
    ) != int(previous_node_id)


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
    intended_new_node_id = int(payload.get("bind_node_id") or 0)
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
    if intended_new_node_id:
        mark_repository_location_residual(
            repository,
            owner_node_id=intended_new_node_id,
            repository_subdir=nas_proxy_repository_subdir(repository),
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

    execute_repository_operation.apply_async(
        kwargs={"repository_task_id": repository_task_id}
    )


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
        return exc.error_code
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
    "retry_repository_create",
    "run_repository_create_task",
]
