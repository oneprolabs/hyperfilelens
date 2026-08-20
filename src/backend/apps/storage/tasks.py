from __future__ import annotations

import logging
import time
from uuid import UUID, uuid4

from celery import shared_task
from celery.signals import worker_ready
from django.core.cache import cache
from django.utils import timezone

from common.observability.celery_context import logged_celery_task

from apps.storage.repositories.models import (
    Repository,
    RepositoryExecutionTarget,
    RepositoryTask,
)
from apps.storage.services.internal.repository_location import (
    invalidate_repository_location_ownership,
)
from apps.storage.services.internal.repository_health import (
    is_repository_ownership_failure,
    probe_repository_health,
)
from apps.storage.services.internal.repository_errors import (
    is_repository_health_transport_unconfirmed,
)
from apps.storage.services.internal.repository_usage import (
    sync_all_repositories,
    sync_organization_repositories,
)
from apps.storage.services.internal.kopia_cli import (
    KopiaCliCancelled,
    KopiaCliError,
    KopiaControlDecision,
    KopiaExecutionLeaseLost,
    run_maintenance,
)
from apps.storage.services.internal.repository_access import repository_payload_for_node
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationResult,
    RepositoryAgentOperationStateUnknown,
    RepositoryAgentOperationTimeout,
    resolve_or_dispatch_repository_agent_operation,
)
from apps.storage.services.internal.repository_operations import (
    fence_controller_repository_operation,
    finalize_repository_operation,
    maintenance_settings,
    repository_execution_target_has_owned_location,
    schedule_due_maintenance,
    set_controller_repository_operation_step,
    set_task_step,
    start_controller_repository_operation,
)
from apps.storage.services.internal.repository_secrets import scrub_secrets
from apps.task.models import Task, TaskStep
from apps.task.services.interface import start_task
from apps.task.services.recovery import (
    CONTROL_PLANE_RESTART_INTERRUPTED,
    RecoveryDecision,
    RecoveryPlan,
    record_recovery_decision,
)


logger = logging.getLogger(__name__)

_REPOSITORY_HEALTH_LOCK_TIMEOUT_SECONDS = 300
_REPOSITORY_HEALTH_STARTUP_DISPATCH_LOCK_TIMEOUT_SECONDS = 600
_REPOSITORY_HEALTH_RETRY_DELAY_SECONDS = 30
_REPOSITORY_OPERATION_LOCK_TIMEOUT_SECONDS = 30
_REPOSITORY_OPERATION_CONFLICT_RETRY_SECONDS = 3


def _repository_health_lock(repository_id: int) -> str:
    return f"storage:repository-health:repository:{int(repository_id)}"


def _repository_health_startup_dispatch_lock() -> str:
    return "storage:repository-health:startup-dispatch"


@worker_ready.connect
def enqueue_startup_repository_health_checks(sender=None, **_kwargs) -> None:
    """Schedule one health sweep when a Celery worker becomes ready."""
    dispatch_repository_health_checks.apply_async(kwargs={"startup": True})


@shared_task(name="apps.storage.tasks.reconcile_storage_repositories")
@logged_celery_task(
    name="apps.storage.tasks.reconcile_storage_repositories",
    trace_keys=("organization_id", "repo_type", "limit", "force"),
)
def reconcile_storage_repositories(
    *,
    organization_id: int | None = None,
    repository_ids: list[int] | None = None,
    repo_type: str | None = None,
    limit: int = 200,
    force: bool = False,
    stale_after_seconds: int | None = 900,
):
    """Refresh repository capacity and usage metrics for dashboards and alerts."""
    if organization_id is not None:
        result = sync_organization_repositories(
            organization_id=int(organization_id),
            repository_ids=repository_ids or None,
            repo_type=repo_type,
            limit=limit,
            force=force,
            stale_after_seconds=stale_after_seconds,
        )
    else:
        result = sync_all_repositories(
            repo_type=repo_type,
            limit=limit,
            force=force,
            stale_after_seconds=stale_after_seconds,
        )
    return {
        "repositories_scanned": result.get("repositories_synced", 0),
        "snapshots_upserted": 0,
        "snapshots_marked_deleted": 0,
    }


@shared_task(name="apps.storage.tasks.dispatch_repository_health_checks")
@logged_celery_task(name="apps.storage.tasks.dispatch_repository_health_checks")
def dispatch_repository_health_checks(*, startup: bool = False):
    """Fan out one lightweight health task per eligible repository."""
    if startup and not cache.add(
        _repository_health_startup_dispatch_lock(),
        "1",
        timeout=_REPOSITORY_HEALTH_STARTUP_DISPATCH_LOCK_TIMEOUT_SECONDS,
    ):
        return {"dispatched": 0, "startup": True, "skipped": "duplicate_startup"}

    repository_ids = list(
        Repository.objects.filter(
            status=Repository.Status.CREATED,
            repo_type__in=[
                Repository.Type.S3,
                Repository.Type.NAS,
                Repository.Type.PROXY_FS,
            ],
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    dispatched = 0
    for repository_id in repository_ids:
        try:
            check_storage_repository_health.apply_async(
                kwargs={"repository_id": repository_id}
            )
            dispatched += 1
        except Exception:
            logger.exception(
                "failed to enqueue repository health check repository_id=%s",
                repository_id,
            )
    return {"dispatched": dispatched, "startup": startup}


@shared_task(name="apps.storage.tasks.check_storage_repository_health")
@logged_celery_task(
    name="apps.storage.tasks.check_storage_repository_health",
    trace_keys=("repository_id",),
)
def check_storage_repository_health(*, repository_id: int, retry_attempt: int = 0):
    """Probe one repository; two consecutive failures confirm it as offline."""
    lock_key = _repository_health_lock(repository_id)
    if not cache.add(lock_key, "1", timeout=_REPOSITORY_HEALTH_LOCK_TIMEOUT_SECONDS):
        return {"repository_id": repository_id, "status": "skipped", "locked": True}

    try:
        repository = Repository.objects.filter(
            id=repository_id,
            status=Repository.Status.CREATED,
            repo_type__in=[
                Repository.Type.S3,
                Repository.Type.NAS,
                Repository.Type.PROXY_FS,
            ],
        ).first()
        if repository is None:
            return {
                "repository_id": repository_id,
                "status": "skipped",
                "eligible": False,
            }
        try:
            health = probe_repository_health(repository)
        except Exception as exc:
            logger.warning(
                "repository health check failed repository_id=%s retry_attempt=%s error_type=%s",
                repository_id,
                retry_attempt,
                type(exc).__name__,
            )
            if is_repository_ownership_failure(exc):
                invalidate_repository_location_ownership(repository)
                return _record_repository_health_failure(
                    repository=repository,
                    retry_attempt=retry_attempt,
                    fail_immediately=True,
                )
            if is_repository_health_transport_unconfirmed(exc):
                return {
                    "repository_id": repository_id,
                    "status": repository.health,
                    "probe_status": "transport_unknown",
                    "health_failures": repository.health_failures,
                }
            return _record_repository_health_failure(
                repository=repository,
                retry_attempt=retry_attempt,
            )
        current_scope = Repository.objects.filter(
            pk=repository_id,
            status=Repository.Status.CREATED,
            repo_type=repository.repo_type,
            bind_node_type=repository.bind_node_type,
            bind_node_id=repository.bind_node_id,
            updated_at=repository.updated_at,
        )
        if repository.health != health or repository.health_failures:
            if not current_scope.update(health=health, health_failures=0):
                return {
                    "repository_id": repository_id,
                    "status": "skipped",
                    "stale": True,
                }
        elif not current_scope.exists():
            return {
                "repository_id": repository_id,
                "status": "skipped",
                "stale": True,
            }
        return {"repository_id": repository_id, "status": health}
    finally:
        cache.delete(lock_key)


def _record_repository_health_failure(
    *,
    repository: Repository,
    retry_attempt: int,
    fail_immediately: bool = False,
) -> dict:
    """Persist a failed probe and retry it once before declaring the target offline."""
    failure_count = (
        2 if fail_immediately else min(int(repository.health_failures or 0) + 1, 2)
    )
    health = Repository.Health.OFFLINE if failure_count >= 2 else repository.health
    current_scope = Repository.objects.filter(
        pk=repository.id,
        status=Repository.Status.CREATED,
        repo_type=repository.repo_type,
        bind_node_type=repository.bind_node_type,
        bind_node_id=repository.bind_node_id,
        updated_at=repository.updated_at,
    )
    if not current_scope.update(health=health, health_failures=failure_count):
        return {"repository_id": repository.id, "status": "skipped", "stale": True}

    if failure_count == 1 and retry_attempt == 0:
        check_storage_repository_health.apply_async(
            kwargs={"repository_id": repository.id, "retry_attempt": 1},
            countdown=_REPOSITORY_HEALTH_RETRY_DELAY_SECONDS,
        )
        return {
            "repository_id": repository.id,
            "status": repository.health,
            "health_failures": failure_count,
            "retry_scheduled": True,
        }
    return {
        "repository_id": repository.id,
        "status": health,
        "health_failures": failure_count,
    }


@shared_task(name="apps.storage.tasks.schedule_repository_maintenance")
@logged_celery_task(name="apps.storage.tasks.schedule_repository_maintenance")
def schedule_repository_maintenance():
    repository_task_ids = schedule_due_maintenance()
    for repository_task_id in repository_task_ids:
        execute_repository_operation.apply_async(
            kwargs={"repository_task_id": repository_task_id}
        )
    return {
        "scheduled": len(repository_task_ids),
        "repository_task_ids": repository_task_ids,
    }


@shared_task(name="apps.storage.tasks.reconcile_repository_operations")
@logged_celery_task(
    name="apps.storage.tasks.reconcile_repository_operations",
    trace_keys=("limit",),
)
def reconcile_repository_operations(*, limit: int = 100):
    """Requeue active repository operations for one idempotent advance."""
    repository_task_ids = list(
        RepositoryTask.objects.filter(
            task__status__in=[Task.Status.PENDING, Task.Status.RUNNING],
        )
        .order_by("task__updated_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    for repository_task_id in repository_task_ids:
        execute_repository_operation.apply_async(
            kwargs={"repository_task_id": repository_task_id}
        )
    return {
        "scanned": len(repository_task_ids),
        "redispatched": len(repository_task_ids),
        "repository_task_ids": repository_task_ids,
    }


@shared_task(name="apps.storage.tasks.execute_repository_operation")
@logged_celery_task(
    name="apps.storage.tasks.execute_repository_operation",
    trace_keys=("repository_task_id",),
)
def execute_repository_operation(*, repository_task_id: int):
    task_identity = (
        RepositoryTask.objects.filter(pk=repository_task_id)
        .values_list("owner_type", "operation_type")
        .first()
    )
    if task_identity is None:
        raise RepositoryTask.DoesNotExist(repository_task_id)
    owner_type, operation_type = task_identity
    is_cleanup = operation_type in {
        RepositoryTask.OperationType.CLEANUP_TARGET,
        RepositoryTask.OperationType.CLEANUP_REPOSITORY,
    }
    uses_controller_workflow_lock = operation_type in {
        RepositoryTask.OperationType.CREATE_REPOSITORY,
        RepositoryTask.OperationType.REPAIR_BIND,
        RepositoryTask.OperationType.REPAIR_REMOUNT,
        RepositoryTask.OperationType.CHECK,
        RepositoryTask.OperationType.CREDENTIAL_ROTATE,
    }
    if (
        owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER
        and not is_cleanup
        and not uses_controller_workflow_lock
    ):
        return _execute_repository_operation(repository_task_id=repository_task_id)

    lock_key = f"storage:repository-operation:advance:{int(repository_task_id)}"
    owner_token = str(uuid4())
    if not cache.add(
        lock_key,
        owner_token,
        timeout=_REPOSITORY_OPERATION_LOCK_TIMEOUT_SECONDS,
    ):
        execute_repository_operation.apply_async(
            kwargs={"repository_task_id": int(repository_task_id)},
            countdown=_REPOSITORY_OPERATION_CONFLICT_RETRY_SECONDS,
        )
        return {
            "status": "rescheduled",
            "repository_task_id": repository_task_id,
            "retry_in_seconds": _REPOSITORY_OPERATION_CONFLICT_RETRY_SECONDS,
        }
    try:
        return _execute_repository_operation(repository_task_id=repository_task_id)
    finally:
        if cache.get(lock_key) == owner_token:
            cache.delete(lock_key)


def _execute_repository_operation(*, repository_task_id: int):
    repository_task = RepositoryTask.objects.select_related(
        "task", "repository", "execution_target"
    ).get(pk=repository_task_id)
    if repository_task.operation_type == RepositoryTask.OperationType.CHECK:
        from apps.storage.services.internal.repository_check import (
            run_repository_check_task,
        )

        return run_repository_check_task(repository_task_id=repository_task.id)
    if repository_task.operation_type == RepositoryTask.OperationType.CREDENTIAL_ROTATE:
        from apps.storage.services.internal.repository_credential_rotation import (
            run_repository_credential_rotation_task,
        )

        return run_repository_credential_rotation_task(
            repository_task_id=repository_task.id
        )
    if repository_task.operation_type in {
        RepositoryTask.OperationType.CREATE_REPOSITORY,
        RepositoryTask.OperationType.REPAIR_BIND,
        RepositoryTask.OperationType.REPAIR_REMOUNT,
    }:
        from apps.storage.services.internal.repository_create import (
            run_repository_create_task,
        )

        return run_repository_create_task(repository_task_id=repository_task.id)
    if repository_task.operation_type in {
        RepositoryTask.OperationType.CLEANUP_TARGET,
        RepositoryTask.OperationType.CLEANUP_REPOSITORY,
    }:
        from apps.storage.services.internal.repository_cleanup import (
            run_repository_cleanup_task,
        )

        return run_repository_cleanup_task(repository_task_id=repository_task.id)
    task = repository_task.task
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return {"status": task.status, "idempotent": True}
    started_now = task.status == Task.Status.PENDING
    execution_token: UUID | None = repository_task.execution_token
    if started_now:
        if repository_task.owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER:
            execution_token = start_controller_repository_operation(
                repository_task_id=repository_task.id
            )
            repository_task.refresh_from_db()
            task.refresh_from_db()
            if execution_token is None:
                return {"status": task.status, "idempotent": True}
        else:
            start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
    elif task.status == Task.Status.RUNNING:
        if repository_task.owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER:
            return _recover_controller_repository_operation(repository_task)
    else:
        return {"status": task.status, "idempotent": True}
    try:
        if started_now:
            _raise_for_control_decision(
                _set_repository_operation_step(
                    repository_task,
                    execution_token,
                    "prepare_repository_operation",
                    status=TaskStep.Status.SUCCESS,
                    progress=10,
                )
            )
            if (
                repository_task.execution_target is None
                or not repository_execution_target_has_owned_location(
                    repository_task.execution_target
                )
            ):
                raise ValueError("Repository location ownership requires verification.")
            _raise_for_control_decision(
                _set_repository_operation_step(
                    repository_task,
                    execution_token,
                    "verify_repository_owner",
                    status=TaskStep.Status.SUCCESS,
                    progress=20,
                )
            )
            _raise_for_control_decision(
                _set_repository_operation_step(
                    repository_task,
                    execution_token,
                    "run_repository_operation",
                    status=TaskStep.Status.RUNNING,
                    progress=25,
                )
            )
        operation = _execute_maintenance(
            repository_task,
            allow_dispatch=started_now,
            execution_token=execution_token,
        )
        if operation.waiting:
            return {
                "status": "waiting",
                "repository_task_id": repository_task.id,
                "remote_task_id": str(operation.node_task_id),
            }
        result = operation.result
        _raise_for_control_decision(
            _set_repository_operation_step(
                repository_task,
                execution_token,
                "run_repository_operation",
                status=TaskStep.Status.SUCCESS,
                progress=80,
            )
        )
        _raise_for_control_decision(
            _set_repository_operation_step(
                repository_task,
                execution_token,
                "refresh_repository_usage",
                status=TaskStep.Status.RUNNING,
                progress=85,
            )
        )
        sync_organization_repositories(
            organization_id=repository_task.repository.organization_id,
            repository_ids=[repository_task.repository_id],
            limit=1,
            force=True,
            stale_after_seconds=None,
        )
        _raise_for_control_decision(
            _set_repository_operation_step(
                repository_task,
                execution_token,
                "refresh_repository_usage",
                status=TaskStep.Status.SUCCESS,
                progress=95,
            )
        )
        _raise_for_control_decision(
            _set_repository_operation_step(
                repository_task,
                execution_token,
                "finalize_repository_operation",
                status=TaskStep.Status.SUCCESS,
                progress=100,
            )
        )
        completed_task = finalize_repository_operation(
            repository_task_id=repository_task.id,
            succeeded=True,
            result_payload=scrub_secrets(result),
            expected_execution_token=execution_token,
        )
        return {
            "status": completed_task.status,
            "repository_task_id": repository_task.id,
        }
    except KopiaCliCancelled as exc:
        repository_task.refresh_from_db(fields=["cancel_reason"])
        cancelled_task = finalize_repository_operation(
            repository_task_id=repository_task.id,
            succeeded=False,
            cancelled=True,
            error_message=repository_task.cancel_reason or str(exc),
            expected_execution_token=execution_token,
        )
        return {
            "status": cancelled_task.status,
            "repository_task_id": repository_task.id,
        }
    except KopiaExecutionLeaseLost:
        logger.warning(
            "repository operation execution lease lost repository_task_id=%s task_uuid=%s",
            repository_task.id,
            task.task_uuid,
        )
        return {"status": "lease_lost", "repository_task_id": repository_task.id}
    except RepositoryAgentOperationStateUnknown as exc:
        record_recovery_decision(
            task=task,
            plan=RecoveryPlan(
                decision=RecoveryDecision.FAIL,
                reason=str(exc),
                evidence={
                    "current_step": task.current_step,
                    "operation_type": repository_task.operation_type,
                },
            ),
        )
        decision = _set_repository_operation_step(
            repository_task,
            execution_token,
            task.current_step or "run_repository_operation",
            status=TaskStep.Status.FAILED,
            progress=int(task.progress),
        )
        interrupted = _finalize_control_interruption(
            decision=decision,
            repository_task=repository_task,
            execution_token=execution_token,
        )
        if interrupted is not None:
            return interrupted
        finalize_repository_operation(
            repository_task_id=repository_task.id,
            succeeded=False,
            error_code=CONTROL_PLANE_RESTART_INTERRUPTED,
            error_message=str(exc),
            expected_execution_token=execution_token,
        )
        return {"status": "failed", "repository_task_id": repository_task.id}
    except Exception as exc:
        decision = _set_repository_operation_step(
            repository_task,
            execution_token,
            task.current_step or "run_repository_operation",
            status=TaskStep.Status.FAILED,
            progress=int(task.progress),
        )
        interrupted = _finalize_control_interruption(
            decision=decision,
            repository_task=repository_task,
            execution_token=execution_token,
        )
        if interrupted is not None:
            return interrupted
        finalize_repository_operation(
            repository_task_id=repository_task.id,
            succeeded=False,
            error_code=_repository_operation_error_code(exc),
            error_message=str(scrub_secrets(str(exc))),
            expected_execution_token=execution_token,
        )
        return {"status": "failed", "repository_task_id": repository_task.id}


def _recover_controller_repository_operation(repository_task: RepositoryTask) -> dict:
    task = repository_task.task
    settings = maintenance_settings()
    now = timezone.now()
    token = repository_task.execution_token
    heartbeat = repository_task.execution_heartbeat_at
    heartbeat_fresh = bool(
        token
        and heartbeat
        and (now - heartbeat).total_seconds() <= settings.heartbeat_stale_seconds
    )
    if repository_task.cancel_requested_at is not None:
        if heartbeat_fresh:
            return {"status": "cancelling", "repository_task_id": repository_task.id}
        cancelled_task = finalize_repository_operation(
            repository_task_id=repository_task.id,
            succeeded=False,
            cancelled=True,
            error_message=repository_task.cancel_reason,
            expected_execution_token=token,
        )
        return {
            "status": cancelled_task.status,
            "repository_task_id": repository_task.id,
        }
    if heartbeat_fresh:
        return {"status": task.status, "idempotent": True}

    if token is None:
        legacy_started_at = task.started_at or task.updated_at or task.created_at
        if (
            now - legacy_started_at
        ).total_seconds() <= settings.execution_timeout_seconds:
            return {"status": task.status, "idempotent": True}

    recovery_token = fence_controller_repository_operation(
        repository_task_id=repository_task.id,
        expected_execution_token=token,
    )
    if recovery_token is None:
        repository_task.refresh_from_db()
        return {"status": repository_task.task.status, "idempotent": True}

    reason = (
        "Controller repository maintenance lost its execution heartbeat after a "
        "control-plane interruption."
    )
    record_recovery_decision(
        task=task,
        plan=RecoveryPlan(
            decision=RecoveryDecision.FAIL,
            reason=reason,
            evidence={
                "current_step": task.current_step,
                "operation_type": repository_task.operation_type,
                "execution_token": str(token) if token else None,
                "execution_heartbeat_at": heartbeat.isoformat() if heartbeat else None,
            },
        ),
    )
    decision = _set_repository_operation_step(
        repository_task,
        recovery_token,
        task.current_step or "run_repository_operation",
        status=TaskStep.Status.FAILED,
        progress=int(task.progress),
    )
    interrupted = _finalize_control_interruption(
        decision=decision,
        repository_task=repository_task,
        execution_token=recovery_token,
    )
    if interrupted is not None:
        return interrupted
    failed_task = finalize_repository_operation(
        repository_task_id=repository_task.id,
        succeeded=False,
        error_code=CONTROL_PLANE_RESTART_INTERRUPTED,
        error_message=reason,
        expected_execution_token=recovery_token,
    )
    return {"status": failed_task.status, "repository_task_id": repository_task.id}


def _set_repository_operation_step(
    repository_task: RepositoryTask,
    execution_token: UUID | None,
    step_name: str,
    *,
    status: str,
    progress: int,
) -> KopiaControlDecision:
    if repository_task.owner_type != RepositoryExecutionTarget.OwnerType.CONTROLLER:
        set_task_step(
            repository_task.task,
            step_name,
            status=status,
            progress=progress,
        )
        return KopiaControlDecision.CONTINUE
    if execution_token is None:
        return KopiaControlDecision.LOST_LEASE
    result = set_controller_repository_operation_step(
        repository_task_id=repository_task.id,
        expected_execution_token=execution_token,
        step_name=step_name,
        status=status,
        progress=progress,
    )
    return KopiaControlDecision(result)


def _raise_for_control_decision(decision: KopiaControlDecision) -> None:
    if decision == KopiaControlDecision.CANCEL:
        raise KopiaCliCancelled("Kopia repository maintenance was cancelled")
    if decision == KopiaControlDecision.LOST_LEASE:
        raise KopiaExecutionLeaseLost(
            "Kopia repository maintenance execution lease was lost"
        )


def _finalize_control_interruption(
    *,
    decision: KopiaControlDecision,
    repository_task: RepositoryTask,
    execution_token: UUID | None,
) -> dict | None:
    if decision == KopiaControlDecision.CONTINUE:
        return None
    if decision == KopiaControlDecision.LOST_LEASE:
        return {"status": "lease_lost", "repository_task_id": repository_task.id}
    repository_task.refresh_from_db(fields=["cancel_reason"])
    cancelled_task = finalize_repository_operation(
        repository_task_id=repository_task.id,
        succeeded=False,
        cancelled=True,
        error_message=repository_task.cancel_reason,
        expected_execution_token=execution_token,
    )
    return {
        "status": cancelled_task.status,
        "repository_task_id": repository_task.id,
    }


def _execute_maintenance(
    repository_task: RepositoryTask,
    *,
    allow_dispatch: bool = True,
    execution_token: UUID | None = None,
) -> RepositoryAgentOperationResult:
    if repository_task.operation_type not in {
        RepositoryTask.OperationType.MAINTENANCE_QUICK,
        RepositoryTask.OperationType.MAINTENANCE_FULL,
    }:
        raise ValueError(
            f"Operation {repository_task.operation_type} is not implemented"
        )
    full = (
        repository_task.operation_type == RepositoryTask.OperationType.MAINTENANCE_FULL
    )
    settings = maintenance_settings()
    if repository_task.owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER:
        if execution_token is None:
            raise KopiaExecutionLeaseLost(
                "Controller repository maintenance has no execution lease"
            )
        result = run_maintenance(
            repository_task.repository,
            full=full,
            owner_identity=repository_task.owner_identity,
            timeout_seconds=settings.execution_timeout_seconds,
            control=_controller_execution_control(
                repository_task_id=repository_task.id,
                execution_token=execution_token,
                heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
            ),
        )
        return RepositoryAgentOperationResult(
            waiting=False,
            node_task_id=repository_task.remote_task_id,
            result={
                "operation_type": repository_task.operation_type,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )

    from apps.node.models import Node

    node = Node.objects.filter(
        id=repository_task.owner_node_id,
        organization_id=repository_task.repository.organization_id,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValueError("Repository owner node was not found")
    inventory = (
        (node.metadata or {}).get("inventory")
        if isinstance(node.metadata, dict)
        else {}
    )
    capabilities = inventory.get("capabilities") if isinstance(inventory, dict) else []
    if "repository_operation_v1" not in (
        capabilities if isinstance(capabilities, list) else []
    ):
        raise ValueError("Repository owner does not advertise repository_operation_v1")
    repository_payload = repository_payload_for_node(
        repository=repository_task.repository,
        node=node,
        source_type="proxy" if node.role == "proxy" else "agent",
        source_ref_id=node.id,
    )
    outcome = resolve_or_dispatch_repository_agent_operation(
        repository_task=repository_task,
        node=node,
        payload={
            "operation_type": repository_task.operation_type,
            "owner_identity": repository_task.owner_identity,
            "repository": repository_payload,
        },
        persisted_payload={
            "repository_id": repository_task.repository_id,
            "operation_type": repository_task.operation_type,
            "owner_node_id": node.id,
        },
        correlation_type="repository_operation",
        timeout_seconds=settings.execution_timeout_seconds,
        allow_dispatch=allow_dispatch,
    )
    return RepositoryAgentOperationResult(
        waiting=outcome.waiting,
        node_task_id=outcome.node_task_id,
        result=scrub_secrets(outcome.result),
    )


def _controller_execution_control(
    *,
    repository_task_id: int,
    execution_token: UUID,
    heartbeat_interval_seconds: int,
):
    next_heartbeat = time.monotonic() + heartbeat_interval_seconds

    def control() -> KopiaControlDecision:
        nonlocal next_heartbeat
        state = (
            RepositoryTask.objects.filter(
                pk=repository_task_id,
                execution_token=execution_token,
            )
            .values("cancel_requested_at")
            .first()
        )
        if state is None:
            return KopiaControlDecision.LOST_LEASE
        if state["cancel_requested_at"] is not None:
            return KopiaControlDecision.CANCEL
        monotonic_now = time.monotonic()
        if monotonic_now >= next_heartbeat:
            updated = RepositoryTask.objects.filter(
                pk=repository_task_id,
                execution_token=execution_token,
            ).update(execution_heartbeat_at=timezone.now())
            if updated != 1:
                return KopiaControlDecision.LOST_LEASE
            next_heartbeat = monotonic_now + heartbeat_interval_seconds
        return KopiaControlDecision.CONTINUE

    return control


def _repository_operation_error_code(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, RepositoryAgentOperationTimeout)):
        return "REPOSITORY_OPERATION_TIMEOUT"
    if isinstance(exc, KopiaCliError):
        return "KOPIA_MAINTENANCE_FAILED"
    return "REPOSITORY_OPERATION_FAILED"


@shared_task(name="apps.storage.tasks.run_storage_provider_validation")
@logged_celery_task(name="apps.storage.tasks.run_storage_provider_validation")
def run_storage_provider_validation(run_id: str):
    from apps.storage.provider_catalog.validation import execute_validation_run

    execute_validation_run(run_id)
    return {"run_id": run_id}


@shared_task(name="apps.storage.tasks.cleanup_storage_provider_validation")
@logged_celery_task(name="apps.storage.tasks.cleanup_storage_provider_validation")
def cleanup_storage_provider_validation(run_id: str):
    from apps.storage.provider_catalog.validation import cleanup_validation_run

    cleanup_validation_run(run_id)
    return {"run_id": run_id}


@shared_task(name="apps.storage.tasks.cleanup_expired_storage_provider_validations")
@logged_celery_task(
    name="apps.storage.tasks.cleanup_expired_storage_provider_validations"
)
def cleanup_expired_storage_provider_validations():
    from apps.storage.provider_catalog.validation import cleanup_expired_validation_runs

    return cleanup_expired_validation_runs()
