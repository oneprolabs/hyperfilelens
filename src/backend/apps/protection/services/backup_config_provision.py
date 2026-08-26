from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.capabilities import (
    REPOSITORY_OWNERSHIP_CAPABILITY,
    node_supports_capability,
)
from apps.protection.models import BackupConfig
from apps.storage.repositories.models import (
    Repository,
    RepositoryUsageShard,
)
from apps.storage.services.internal.nas_repository import (
    mount_point_from_repo_status_result,
    nas_agent_repository_subdir,
)
from apps.storage.services.internal.repository_location import (
    mark_repository_location_initialization_failed,
    mark_repository_location_owned,
    mark_repository_location_ownership_verified,
    repository_has_owned_location,
)
from apps.task.models import Task, TaskEvent, TaskStep
from apps.task.services.interface import (
    append_task_event,
    complete_task,
    resume_waiting_task,
    retry_task,
    start_task,
)
from common.errors import AppError

logger = logging.getLogger(__name__)

_ACTIVE_NODE_TASK_STATUSES = {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}
_TRANSIENT_CODES = {
    "AGENT_TASK_PENDING",
    "AGENT_OFFLINE",
    "AGENT_RECONNECTING",
    "NODE_TASK_TIMEOUT",
    "TASK_DISPATCH_FAILED",
}
_MAX_TRANSIENT_RETRIES = 8
_DISPATCH_LEASE_SECONDS = 90
_WAITING_EVENT_MESSAGE = "Storage validation is waiting for the Agent to reconnect"
_EXPLICIT_RETRY_EVENT_MESSAGE = "Task queued for retry"


def queue_backup_config_provision_task(*, task_id: int) -> bool:
    from apps.protection.tasks.backup_config_provision import (
        execute_backup_config_provision_task,
    )

    try:
        execute_backup_config_provision_task.delay(task_id=int(task_id))
    except Exception:
        # The PostgreSQL Task is the durable queue intent. A broker outage must
        # not turn an already committed API request into a false failure; the
        # periodic reconciler will dispatch it after connectivity recovers.
        logger.exception(
            "backup config provision dispatch failed task_id=%s",
            int(task_id),
        )
        return False
    return True


def run_backup_config_provision_task(*, task_id: int) -> dict[str, object]:
    task = Task.objects.filter(
        id=int(task_id),
        task_type=Task.Type.BACKUP_CONFIG_PROVISION,
    ).first()
    if task is None:
        return {"status": "missing"}
    if task.status == Task.Status.SUCCESS:
        return {"status": "success", "task_uuid": str(task.task_uuid)}
    if task.status in {Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.TIMEOUT}:
        return {"status": task.status, "task_uuid": str(task.task_uuid)}

    config = _task_config(task)
    if config is None:
        _finish_failure(
            task=task,
            config=None,
            error_code="BACKUP_CONFIG_NOT_FOUND",
            message="Backup configuration no longer exists.",
        )
        return {"status": "failed", "error_code": "BACKUP_CONFIG_NOT_FOUND"}

    task, execution_state, latest_node_task = _claim_task_execution(task_id=task.id)
    if execution_state == "terminal":
        return {"status": task.status, "task_uuid": str(task.task_uuid)}
    if execution_state == "waiting_agent":
        return {
            "status": "waiting_agent",
            "node_task_id": str(latest_node_task.id) if latest_node_task else "",
        }
    if execution_state == "node_success" and latest_node_task is not None:
        try:
            _activate_from_node_result(task=task, config=config, node_task=latest_node_task)
        except Exception as exc:
            code, message = _error_details(exc, task=task)
            _finish_failure(task=task, config=config, error_code=code, message=message)
            return {"status": "failed", "error_code": code}
        return {"status": "success", "task_uuid": str(task.task_uuid)}
    if execution_state == "dispatch_in_progress":
        return {"status": "dispatch_in_progress"}
    _set_step(task, "validate_repository_target", progress=10)

    try:
        from apps.protection.services.backup_config import (
            _initialize_direct_nas_repository,
        )

        _set_step(task, "initialize_repository", progress=35)
        _initialize_direct_nas_repository(
            organization_id=int(config.organization_id),
            source_type=config.source_type,
            source_ref_id=int(config.source_ref_id),
            repository_id=int(config.repository_id),
            parent_task=task,
            require_ownership_capability=True,
        )
        _activate_config(task=task, config=config)
    except Exception as exc:
        code, message = _error_details(exc, task=task)
        if code == "REMOTE_RESULT_UNKNOWN":
            blocked = _mark_waiting(
                task=task,
                config=config,
                error_code=code,
                message=message,
                state_unknown=True,
            )
            if not blocked:
                return {
                    "status": "failed",
                    "error_code": "BACKUP_CONFIG_NOT_AVAILABLE",
                }
            task.refresh_from_db()
            if task.status == Task.Status.SUCCESS:
                return {"status": "success", "task_uuid": str(task.task_uuid)}
            if task.status in {
                Task.Status.FAILED,
                Task.Status.CANCELLED,
                Task.Status.TIMEOUT,
            }:
                return {"status": task.status, "task_uuid": str(task.task_uuid)}
            return {"status": "blocked", "error_code": code}
        if code in _TRANSIENT_CODES and _transient_retry_count(task) < _MAX_TRANSIENT_RETRIES:
            latest = _latest_node_task_for_attempt(task)
            if _mark_waiting(
                task=task,
                config=config,
                error_code=code,
                message=message,
                reset_attempt=(
                    latest is not None
                    and latest.status
                    in {NodeTask.Status.FAILED, NodeTask.Status.CANCELED}
                ),
            ):
                task.refresh_from_db()
                if task.status == Task.Status.SUCCESS:
                    return {"status": "success", "task_uuid": str(task.task_uuid)}
                if task.status in {
                    Task.Status.FAILED,
                    Task.Status.CANCELLED,
                    Task.Status.TIMEOUT,
                }:
                    return {"status": task.status, "task_uuid": str(task.task_uuid)}
                return {"status": "waiting", "error_code": code}
            return {"status": "failed", "error_code": "BACKUP_CONFIG_NOT_AVAILABLE"}
        _finish_failure(task=task, config=config, error_code=code, message=message)
        return {"status": "failed", "error_code": code}
    return {"status": "success", "task_uuid": str(task.task_uuid)}


def _latest_node_task_for_attempt(task: Task) -> NodeTask | None:
    """Return only Agent work belonging to the current product-task attempt."""
    if task.started_at is None:
        # retry_task clears started_at. Until this attempt is claimed, all
        # linked NodeTasks belong to an older attempt and must not be projected.
        return None
    return (
        task.node_tasks.filter(created_at__gte=task.started_at)
        .order_by("-created_at", "-id")
        .first()
    )


@transaction.atomic
def _claim_task_execution(*, task_id: int) -> tuple[Task, str, NodeTask | None]:
    """Serialize Celery deliveries and lease one durable provisioning attempt."""
    task = Task.objects.select_for_update().get(id=int(task_id))
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return task, "terminal", None

    latest = _latest_node_task_for_attempt(task)
    if latest is not None and latest.status in _ACTIVE_NODE_TASK_STATUSES:
        return task, "waiting_agent", latest

    now = timezone.now()
    if latest is not None and latest.status == NodeTask.Status.SUCCESS:
        if (
            task.status == Task.Status.RUNNING
            and task.updated_at >= latest.updated_at
            and task.updated_at
            > now - timedelta(seconds=_DISPATCH_LEASE_SECONDS)
        ):
            return task, "dispatch_in_progress", latest
        # Lease local result projection too; duplicate deliveries must not both
        # activate the configuration and enqueue the same follow-up work.
        Task.objects.filter(id=task.id).update(
            status=Task.Status.RUNNING,
            updated_at=now,
        )
        task.status = Task.Status.RUNNING
        task.updated_at = now
        return task, "node_success", latest

    if (
        task.status == Task.Status.RUNNING
        and task.updated_at
        > now - timedelta(seconds=_DISPATCH_LEASE_SECONDS)
    ):
        # Another worker claimed this attempt but may not have committed its
        # NodeTask yet. The periodic reconciler recovers it after the lease.
        return task, "dispatch_in_progress", latest

    if task.status == Task.Status.PENDING:
        task = start_task(
            task_uuid=task.task_uuid,
            organization_id=int(task.organization_id),
        )
    elif task.status in {Task.Status.WAITING, Task.Status.BLOCKED}:
        task = resume_waiting_task(
            task_uuid=task.task_uuid,
            organization_id=int(task.organization_id),
        )
    elif task.status == Task.Status.RUNNING:
        # Recover an expired lease whose worker died before creating NodeTask.
        Task.objects.filter(id=task.id).update(updated_at=now)
        task.updated_at = now
    else:
        return task, "terminal", latest
    return task, "claimed", latest


def _transient_retry_count(task: Task) -> int:
    """Count automatic waits since the most recent explicit user retry."""
    events = task.events.filter(message=_WAITING_EVENT_MESSAGE)
    explicit_retry = (
        task.events.filter(message=_EXPLICIT_RETRY_EVENT_MESSAGE)
        .order_by("-created_at", "-id")
        .first()
    )
    if explicit_retry is not None:
        events = events.filter(created_at__gte=explicit_retry.created_at)
    return int(events.count())


@transaction.atomic
def retry_backup_config_provision(*, config: BackupConfig) -> Task:
    config = BackupConfig.objects.select_for_update().filter(
        id=config.id,
        organization_id=config.organization_id,
    ).first()
    if config is None:
        raise ValidationError("Backup configuration no longer exists.")
    if config.status != BackupConfig.Status.PROVISION_FAILED:
        raise ValidationError("Only failed storage validation can be retried.")
    task = Task.objects.filter(
        task_uuid=config.provisioning_task_uuid,
        organization_id=config.organization_id,
        task_type=Task.Type.BACKUP_CONFIG_PROVISION,
    ).first()
    if task is None:
        raise ValidationError("Storage validation task was not found.")
    task = retry_task(
        task_uuid=task.task_uuid,
        organization_id=int(config.organization_id),
        reason="Storage validation retried after the target or Agent was corrected.",
    )
    BackupConfig.objects.filter(id=config.id).update(
        status=BackupConfig.Status.PROVISIONING,
        provisioning_error_code="",
        provisioning_error_message="",
    )
    transaction.on_commit(lambda: queue_backup_config_provision_task(task_id=task.id))
    return task


def reconcile_backup_config_provision_tasks(
    *, limit: int = 100, stale_seconds: int = 90
) -> dict[str, int]:
    cutoff = timezone.now() - timedelta(seconds=max(30, int(stale_seconds)))
    active_node_tasks = NodeTask.objects.filter(
        parent_task_id=OuterRef("pk"),
        status__in=_ACTIVE_NODE_TASK_STATUSES,
        created_at__gte=OuterRef("started_at"),
    )
    resolved_node_tasks = NodeTask.objects.filter(
        parent_task_id=OuterRef("pk"),
        correlation_type="protection.backup_config",
        kind="repo.initialize",
        status__in=[
            NodeTask.Status.SUCCESS,
            NodeTask.Status.FAILED,
            NodeTask.Status.CANCELED,
        ],
        created_at__gte=OuterRef("started_at"),
    )
    stale_tasks = (
        Task.objects.filter(task_type=Task.Type.BACKUP_CONFIG_PROVISION)
        .annotate(
            has_active_node_task=Exists(active_node_tasks),
            has_resolved_node_task=Exists(resolved_node_tasks),
        )
        .filter(
            updated_at__lt=cutoff,
        )
        .filter(
            Q(
                status__in=[
                    Task.Status.PENDING,
                    Task.Status.WAITING,
                    Task.Status.RUNNING,
                ]
            )
            | Q(
                status=Task.Status.BLOCKED,
                has_resolved_node_task=True,
            )
        )
    )
    active_agent_tasks = stale_tasks.filter(has_active_node_task=True).count()
    candidates = stale_tasks.filter(has_active_node_task=False).order_by(
        "updated_at", "id"
    )[: max(1, int(limit))]
    scanned = 0
    dispatch_attempted = 0
    redispatched = 0
    dispatch_failed = 0
    for task in candidates:
        scanned += 1
        dispatch_attempted += 1
        if queue_backup_config_provision_task(task_id=task.id):
            redispatched += 1
        else:
            dispatch_failed += 1
            # Broker availability is shared; stop this pass instead of logging
            # and retrying the same outage for every stale product task.
            break

    upgrade_recovered = 0
    for config in _upgrade_recovery_candidates(limit=limit):
        node = _execution_node_for_config(config)
        if (
            node is None
            or not node_supports_capability(node, REPOSITORY_OWNERSHIP_CAPABILITY)
        ):
            continue
        try:
            retry_backup_config_provision(config=config)
        except ValidationError:
            continue
        upgrade_recovered += 1
    return {
        "scanned": scanned,
        "active_agent_tasks": active_agent_tasks,
        "dispatch_attempted": dispatch_attempted,
        "redispatched": redispatched,
        "dispatch_failed": dispatch_failed,
        "upgrade_recovered": upgrade_recovered,
    }


def _upgrade_recovery_candidates(*, limit: int) -> list[BackupConfig]:
    """Return a bounded batch whose execution node advertises ownership support."""
    from apps.source.constants import ResourceType
    from apps.source.models import SourceResource

    capability = REPOSITORY_OWNERSHIP_CAPABILITY
    capability_query = Q(
        metadata__inventory__capabilities__contains=[capability]
    ) | (
        ~Q(metadata__inventory__has_key="capabilities")
        & Q(metadata__capabilities__contains=[capability])
    )
    capable_nodes = Node.objects.filter(is_deleted=False).filter(capability_query)
    capable_agents = capable_nodes.filter(role=NodeRole.AGENT).values("id")
    capable_proxies = capable_nodes.filter(role=NodeRole.PROXY).values("id")
    capable_nas_sources = SourceResource.objects.filter(
        resource_type=ResourceType.NAS,
        is_deleted=False,
        bound_node_id__in=capable_proxies,
    ).values("id")
    batch_limit = max(1, int(limit))
    return list(
        BackupConfig.objects.filter(
            status=BackupConfig.Status.PROVISION_FAILED,
            provisioning_error_code="AGENT_UPGRADE_REQUIRED",
        )
        .filter(
            Q(source_type="agent", source_ref_id__in=capable_agents)
            | Q(source_type="nas", source_ref_id__in=capable_nas_sources)
        )
        .order_by("updated_at", "id")[:batch_limit]
    )


def _task_config(task: Task) -> BackupConfig | None:
    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    config_id = int(payload.get("backup_config_id") or 0)
    if config_id <= 0:
        return None
    return BackupConfig.objects.filter(
        id=config_id,
        organization_id=task.organization_id,
    ).first()


def _execution_node_for_config(config: BackupConfig):
    from apps.protection.services.backup_config import _direct_nas_execution_node

    try:
        return _direct_nas_execution_node(
            organization_id=int(config.organization_id),
            source_type=config.source_type,
            source_ref_id=int(config.source_ref_id),
        )
    except ValidationError:
        return None


@transaction.atomic
def _activate_from_node_result(
    *, task: Task, config: BackupConfig, node_task: NodeTask
) -> None:
    result = node_task.result if isinstance(node_task.result, dict) else {}
    if result.get("ownership_verified") is not True:
        raise AppError(
            code="AGENT_PROTOCOL_INVALID",
            status=409,
            diagnostic=(
                "Agent completed repository validation without an ownership result. "
                "Upgrade the Agent and retry."
            ),
        )
    repository = Repository.objects.select_for_update().filter(
        id=config.repository_id,
        organization_id=config.organization_id,
        status=Repository.Status.CREATED,
    ).first()
    if repository is None:
        raise ValidationError("Repository is no longer available for backup.")
    node_id = int(node_task.node_id)
    repository_subdir = nas_agent_repository_subdir(node_id)
    mark_repository_location_owned(
        repository,
        owner_node_id=node_id,
        repository_subdir=repository_subdir,
    )
    mark_repository_location_ownership_verified(
        repository,
        owner_node_id=node_id,
        repository_subdir=repository_subdir,
    )
    if not repository_has_owned_location(
        repository,
        owner_node_id=node_id,
        repository_subdir=repository_subdir,
    ):
        raise ValidationError(
            "Repository location claim is missing; storage validation was retained for review."
        )
    checked_at = timezone.now()
    defaults = {
        "is_active": True,
        "status": RepositoryUsageShard.Status.SUCCESS,
        "last_error": "",
        "last_checked_at": checked_at,
        "last_success_checked_at": checked_at,
    }
    mount_point = mount_point_from_repo_status_result(result)
    if mount_point:
        defaults["mount_point"] = mount_point
    RepositoryUsageShard.objects.update_or_create(
        organization_id=config.organization_id,
        repository_id=repository.id,
        usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
        node_id=node_id,
        repository_subdir=repository_subdir,
        defaults=defaults,
    )
    if repository.health != Repository.Health.ONLINE:
        repository.health = Repository.Health.ONLINE
        repository.last_checked_at = checked_at
        repository.save(update_fields=["health", "last_checked_at", "updated_at"])
    _activate_config(task=task, config=config)


@transaction.atomic
def _activate_config(*, task: Task, config: BackupConfig) -> None:
    _set_step(task, "activate_backup_config", progress=90)
    activated = BackupConfig.objects.filter(
        id=config.id,
        status__in=[BackupConfig.Status.PROVISIONING, BackupConfig.Status.PROVISION_FAILED],
    ).update(
        status=BackupConfig.Status.ACTIVE,
        provisioning_error_code="",
        provisioning_error_message="",
    )
    if not activated and not BackupConfig.objects.filter(
        id=config.id,
        status=BackupConfig.Status.ACTIVE,
    ).exists():
        raise ValidationError("Backup configuration is no longer available.")
    TaskStep.objects.filter(
        task=task,
        status__in=[
            TaskStep.Status.PENDING,
            TaskStep.Status.RUNNING,
            TaskStep.Status.WARNING,
        ],
    ).update(status=TaskStep.Status.SUCCESS, progress=Decimal("100.00"))
    complete_task(
        task_uuid=task.task_uuid,
        organization_id=int(task.organization_id),
        status=Task.Status.SUCCESS,
        result_payload={"backup_config_id": int(config.id)},
    )
    if activated:
        transaction.on_commit(
            lambda: _run_post_activation_followups(
                config_id=int(config.id),
                organization_id=int(config.organization_id),
                repository_id=int(config.repository_id),
            )
        )


def _run_post_activation_followups(
    *, config_id: int, organization_id: int, repository_id: int
) -> None:
    """Best-effort projections after the authoritative activation commits."""
    try:
        from apps.protection.services.backup_config import (
            _enqueue_direct_nas_usage_refresh,
        )
        from apps.protection.tasks.directory_size_estimate import (
            refresh_backup_config_directory_estimates_task,
        )
        from apps.protection.tasks.repository_policy import (
            sync_backup_config_repository_policy_task,
        )
    except Exception:
        logger.exception(
            "backup config activation follow-up loading failed config_id=%s",
            int(config_id),
        )
        return

    followups = (
        (
            "repository policy sync",
            lambda: sync_backup_config_repository_policy_task.delay(
                config_id=int(config_id)
            ),
        ),
        (
            "directory size estimate",
            lambda: refresh_backup_config_directory_estimates_task.delay(
                config_id=int(config_id)
            ),
        ),
        (
            "repository usage refresh",
            lambda: _enqueue_direct_nas_usage_refresh(
                organization_id=int(organization_id),
                repository_ids=[int(repository_id)],
                trigger="protection.backup_config.provision",
            ),
        ),
    )
    for name, enqueue in followups:
        try:
            enqueue()
        except Exception:
            # Activation is authoritative and already committed. A temporary
            # broker failure must not turn it into a provisioning failure.
            logger.exception(
                "backup config activation follow-up failed config_id=%s followup=%s",
                int(config_id),
                name,
            )


def _set_step(task: Task, step_name: str, *, progress: int) -> None:
    task.refresh_from_db()
    if task.status not in {Task.Status.RUNNING, Task.Status.WAITING}:
        return
    TaskStep.objects.filter(
        task=task,
        status__in=[TaskStep.Status.RUNNING, TaskStep.Status.WARNING],
    ).exclude(step_name=step_name).update(
        status=TaskStep.Status.SUCCESS,
        progress=Decimal("100.00"),
    )
    step = TaskStep.objects.filter(task=task, step_name=step_name).first()
    if step is not None:
        step.status = TaskStep.Status.RUNNING
        step.progress = Decimal(str(progress))
        step.save(update_fields=["status", "progress"])
    Task.objects.filter(id=task.id).update(
        current_step=step_name,
        progress=Decimal(str(progress)),
    )
    # Keep the caller's instance aligned with the authoritative row. Failure
    # and waiting projections below use ``task.current_step`` to update the
    # step that actually performed the work.
    task.current_step = step_name
    task.progress = Decimal(str(progress))


@transaction.atomic
def _mark_waiting(
    *,
    task: Task,
    config: BackupConfig,
    error_code: str,
    message: str,
    state_unknown: bool = False,
    reset_attempt: bool = False,
) -> bool:
    now = timezone.now()
    # The Agent may finish between dispatch and this projection.  Serialize
    # against the result callback before changing the parent state; otherwise
    # a late worker delivery could move an already-successful task back to
    # WAITING/BLOCKED.
    task = Task.objects.select_for_update().get(id=task.id)
    if task.status in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return True
    config_updated = BackupConfig.objects.filter(
        id=config.id,
        status__in=[
            BackupConfig.Status.PROVISIONING,
            BackupConfig.Status.PROVISION_FAILED,
        ],
    ).update(
        status=BackupConfig.Status.PROVISIONING,
        provisioning_error_code=error_code,
        provisioning_error_message=message,
    )
    if not config_updated:
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=int(task.organization_id),
            status=Task.Status.FAILED,
            error_code="BACKUP_CONFIG_NOT_AVAILABLE",
            error_message="Backup configuration is no longer available for validation.",
        )
        return False
    task_updates = {
        "status": Task.Status.BLOCKED if state_unknown else Task.Status.WAITING,
        "retry_count": int(task.retry_count) + 1,
        "error_code": error_code,
        "error_message": message,
        "updated_at": now,
    }
    if reset_attempt:
        # A terminal Agent failure is known not to be executing anymore. Start
        # a fresh product attempt on the next reconcile, while keeping the old
        # NodeTask as an audit record. TIMEOUT/unknown never takes this path.
        task_updates["started_at"] = None
        task_updates["finished_at"] = None
    Task.objects.filter(id=task.id).update(**task_updates)
    TaskStep.objects.filter(
        task_id=task.id,
        step_name=task.current_step,
        status=TaskStep.Status.RUNNING,
    ).update(status=TaskStep.Status.WARNING)
    append_task_event(
        task=Task.objects.get(id=task.id),
        level=TaskEvent.Level.WARN,
        message=(
            "Storage validation entered an unknown remote state"
            if state_unknown
            else _WAITING_EVENT_MESSAGE
        ),
        metadata={
            "error_code": error_code,
            "retry_count": int(task.retry_count) + 1,
        },
    )
    return True


@transaction.atomic
def _finish_failure(
    *,
    task: Task,
    config: BackupConfig | None,
    error_code: str,
    message: str,
) -> None:
    if config is not None:
        failed = BackupConfig.objects.filter(
            id=config.id,
            status__in=[
                BackupConfig.Status.PROVISIONING,
                BackupConfig.Status.PROVISION_FAILED,
            ],
        ).update(
            status=BackupConfig.Status.PROVISION_FAILED,
            provisioning_error_code=error_code,
            provisioning_error_message=message,
        )
        latest = _latest_node_task_for_attempt(task) if failed else None
        if latest is not None:
            repository = Repository.objects.filter(
                id=config.repository_id,
                organization_id=config.organization_id,
            ).first()
            if repository is not None:
                mark_repository_location_initialization_failed(
                    repository,
                    owner_node_id=int(latest.node_id),
                    repository_subdir=nas_agent_repository_subdir(int(latest.node_id)),
                )
    TaskStep.objects.filter(
        task=task,
        step_name=task.current_step,
        status__in=[TaskStep.Status.PENDING, TaskStep.Status.RUNNING],
    ).update(status=TaskStep.Status.FAILED)
    TaskStep.objects.filter(
        task=task,
        status=TaskStep.Status.PENDING,
    ).update(status=TaskStep.Status.SKIPPED)
    complete_task(
        task_uuid=task.task_uuid,
        organization_id=int(task.organization_id),
        status=Task.Status.FAILED,
        error_code=error_code,
        error_message=message,
    )


def _error_details(exc: Exception, *, task: Task) -> tuple[str, str]:
    latest = _latest_node_task_for_attempt(task)
    result = latest.result if latest is not None and isinstance(latest.result, dict) else {}
    result_code = str(result.get("error_code") or "").strip()
    if isinstance(exc, AppError):
        return exc.code, str(exc)
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            values = [
                str(item)
                for messages in exc.message_dict.values()
                for item in (messages if isinstance(messages, list) else [messages])
            ]
            message = " ".join(values).strip()
        else:
            message = " ".join(str(item) for item in exc.messages).strip()
    else:
        message = str(exc).strip()
    lowered = message.lower()
    if result_code:
        code = result_code
    elif "read-only file system" in lowered or "read only" in lowered:
        code = "NAS_REPOSITORY_READ_ONLY"
    elif "permission denied" in lowered or "access denied" in lowered:
        code = "NAS_REPOSITORY_WRITE_DENIED"
    elif "unavailable or busy" in lowered or "offline" in lowered:
        code = "AGENT_OFFLINE"
    elif latest is not None and latest.status in {
        NodeTask.Status.PENDING,
        NodeTask.Status.RUNNING,
        NodeTask.Status.TIMEOUT,
    }:
        code = "NODE_TASK_TIMEOUT"
    else:
        code = "REPOSITORY_PROVISION_FAILED"
    return code, message or "Repository validation failed."


__all__ = [
    "queue_backup_config_provision_task",
    "reconcile_backup_config_provision_tasks",
    "retry_backup_config_provision",
    "run_backup_config_provision_task",
]
