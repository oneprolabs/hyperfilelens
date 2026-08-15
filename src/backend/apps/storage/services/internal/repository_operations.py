from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common.errors import AppError
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.storage.repositories.models import (
    Repository,
    RepositoryExecutionTarget,
    RepositoryLocationClaim,
    RepositoryMaintenanceState,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.services.internal.repository_task_naming import (
    repository_operation_display_name,
)
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import (
    TERMINAL_STATUSES,
    append_task_event,
    cancel_task as cancel_platform_task,
    complete_task,
    create_task,
    start_task,
)


REPOSITORY_OPERATION_NOT_CANCELLABLE = "STORAGE.REPOSITORY_OPERATION_NOT_CANCELLABLE"
REPOSITORY_OPERATION_NOT_ACTIVE = "STORAGE.REPOSITORY_OPERATION_NOT_ACTIVE"


@dataclass(frozen=True)
class MaintenanceSettings:
    enabled: bool
    quick_interval: timedelta
    full_interval: timedelta
    scan_interval: timedelta
    window_start: time
    window_end: time
    timezone: ZoneInfo
    global_concurrency: int
    per_node_concurrency: int
    execution_timeout_seconds: int
    heartbeat_interval_seconds: int
    heartbeat_stale_seconds: int


def maintenance_settings() -> MaintenanceSettings:
    def positive_int(name: str, default: int) -> int:
        raw = os.getenv(name, str(default)).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ImproperlyConfigured(f"{name} must be an integer") from exc
        if value < 1:
            raise ImproperlyConfigured(f"{name} must be at least 1")
        return value

    def clock(name: str, default: str) -> time:
        raw = os.getenv(name, default).strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
            raise ImproperlyConfigured(f"{name} must use HH:MM format")
        try:
            return time.fromisoformat(raw)
        except ValueError as exc:
            raise ImproperlyConfigured(f"{name} must use HH:MM format") from exc

    timezone_name = os.getenv("STORAGE_MAINTENANCE_TIMEZONE", "UTC").strip() or "UTC"
    try:
        configured_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ImproperlyConfigured("STORAGE_MAINTENANCE_TIMEZONE must be an IANA timezone") from exc
    enabled_raw = os.getenv("STORAGE_MAINTENANCE_ENABLED", "true").strip().lower()
    if enabled_raw not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ImproperlyConfigured("STORAGE_MAINTENANCE_ENABLED must be a boolean")
    enabled = enabled_raw in {"1", "true", "yes", "on"}
    window_start = clock("STORAGE_MAINTENANCE_FULL_WINDOW_START", "00:00")
    window_end = clock("STORAGE_MAINTENANCE_FULL_WINDOW_END", "06:00")
    if window_start == window_end:
        raise ImproperlyConfigured("Maintenance full window start and end must differ")
    heartbeat_interval_seconds = positive_int(
        "STORAGE_MAINTENANCE_HEARTBEAT_INTERVAL_SECONDS", 10
    )
    heartbeat_stale_seconds = positive_int(
        "STORAGE_MAINTENANCE_HEARTBEAT_STALE_SECONDS", 60
    )
    if heartbeat_stale_seconds <= heartbeat_interval_seconds:
        raise ImproperlyConfigured(
            "STORAGE_MAINTENANCE_HEARTBEAT_STALE_SECONDS must be greater than "
            "STORAGE_MAINTENANCE_HEARTBEAT_INTERVAL_SECONDS"
        )
    return MaintenanceSettings(
        enabled=enabled,
        quick_interval=timedelta(seconds=positive_int("STORAGE_MAINTENANCE_QUICK_INTERVAL_SECONDS", 3600)),
        full_interval=timedelta(seconds=positive_int("STORAGE_MAINTENANCE_FULL_INTERVAL_SECONDS", 86400)),
        scan_interval=timedelta(seconds=positive_int("STORAGE_MAINTENANCE_SCAN_INTERVAL_SECONDS", 60)),
        window_start=window_start,
        window_end=window_end,
        timezone=configured_timezone,
        global_concurrency=positive_int("STORAGE_MAINTENANCE_GLOBAL_CONCURRENCY", 4),
        per_node_concurrency=positive_int("STORAGE_MAINTENANCE_PER_NODE_CONCURRENCY", 1),
        execution_timeout_seconds=positive_int("STORAGE_MAINTENANCE_EXECUTION_TIMEOUT_SECONDS", 21600),
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        heartbeat_stale_seconds=heartbeat_stale_seconds,
    )


def _owner_identity(node_id: int | None) -> str:
    return f"hfl-maintenance@node-{node_id}" if node_id else "hfl-maintenance@controller"


def repository_execution_target_has_owned_location(
    target: RepositoryExecutionTarget,
) -> bool:
    """Return whether a maintenance target has sufficient ownership proof."""
    repository = target.repository
    query = RepositoryLocationClaim.objects.filter(
        repository=repository,
        state=RepositoryLocationClaim.State.OWNED,
        ownership_verified_at__isnull=False,
    )
    if repository.repo_type == Repository.Type.NAS and repository.bind_node_id is None:
        if (
            target.owner_type != RepositoryExecutionTarget.OwnerType.NODE
            or target.owner_node_id is None
            or not target.repository_subdir
        ):
            return False
        query = query.filter(
            scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
            owner_node_id=target.owner_node_id,
            root_path=target.repository_subdir,
        )
    else:
        query = query.filter(scope=RepositoryLocationClaim.Scope.REPOSITORY)
    return query.exists()


def discover_repository_execution_targets(*, now: datetime | None = None) -> int:
    current = now or timezone.now()
    seen: set[str] = set()
    count = 0
    repositories = Repository.objects.filter(status=Repository.Status.CREATED).order_by("id")
    for repository in repositories:
        definitions: list[tuple[str, str, int | None, str]] = []
        if repository.repo_type == Repository.Type.S3:
            definitions.append((f"repository:{repository.id}", RepositoryExecutionTarget.OwnerType.CONTROLLER, None, ""))
        elif repository.repo_type in {Repository.Type.NAS, Repository.Type.PROXY_FS} and repository.bind_node_id:
            definitions.append((f"repository:{repository.id}", RepositoryExecutionTarget.OwnerType.NODE, int(repository.bind_node_id), ""))
        elif repository.repo_type == Repository.Type.NAS:
            owned_keys = set(
                RepositoryLocationClaim.objects.filter(
                    repository=repository,
                    scope=RepositoryLocationClaim.Scope.DIRECT_NAS_AGENT,
                    state=RepositoryLocationClaim.State.OWNED,
                    owner_node_id__isnull=False,
                ).values_list("owner_node_id", "root_path")
            )
            shards = RepositoryUsageShard.objects.filter(
                organization_id=repository.organization_id,
                repository_id=repository.id,
                usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
                is_active=True,
            )
            for shard in shards:
                if (shard.node_id, shard.repository_subdir) not in owned_keys:
                    continue
                key = f"repository:{repository.id}:node:{shard.node_id}:subdir:{shard.repository_subdir}"
                definitions.append((key, RepositoryExecutionTarget.OwnerType.NODE, int(shard.node_id), shard.repository_subdir))
        for key, owner_type, node_id, subdir in definitions:
            seen.add(key)
            target, _created = RepositoryExecutionTarget.objects.update_or_create(
                target_key=key,
                defaults={
                    "organization_id": repository.organization_id,
                    "repository": repository,
                    "owner_type": owner_type,
                    "owner_node_id": node_id,
                    "owner_identity": _owner_identity(node_id),
                    "repository_subdir": subdir,
                    "is_active": True,
                },
            )
            RepositoryMaintenanceState.objects.get_or_create(
                execution_target=target,
                defaults={"next_quick_due_at": current, "next_full_due_at": current},
            )
            count += 1
    RepositoryExecutionTarget.objects.filter(is_active=True).exclude(target_key__in=seen).update(is_active=False)
    return count


def _inside_full_window(now: datetime, settings: MaintenanceSettings) -> bool:
    local_time = now.astimezone(settings.timezone).time().replace(tzinfo=None)
    if settings.window_start < settings.window_end:
        return settings.window_start <= local_time < settings.window_end
    return local_time >= settings.window_start or local_time < settings.window_end


def _stable_full_delay(target_key: str, settings: MaintenanceSettings) -> timedelta:
    start_seconds = settings.window_start.hour * 3600 + settings.window_start.minute * 60
    end_seconds = settings.window_end.hour * 3600 + settings.window_end.minute * 60
    width = (end_seconds - start_seconds) % 86400
    if width <= 1:
        return timedelta(0)
    digest = hashlib.sha256(target_key.encode("utf-8")).digest()
    return timedelta(seconds=int.from_bytes(digest[:8], "big") % width)


def _concurrency_available(target: RepositoryExecutionTarget, settings: MaintenanceSettings) -> bool:
    active = RepositoryExecutionTarget.objects.filter(active_task__status__in=[Task.Status.PENDING, Task.Status.RUNNING])
    if active.count() >= settings.global_concurrency:
        return False
    if target.owner_node_id and active.filter(owner_node_id=target.owner_node_id).count() >= settings.per_node_concurrency:
        return False
    return True


@transaction.atomic
def create_repository_operation_task(
    *,
    target_id: int,
    operation_type: str,
    trigger_type: str = Task.TriggerType.SYSTEM,
    due_at: datetime | None = None,
) -> RepositoryTask | None:
    if operation_type not in {value for value, _ in RepositoryTask.OperationType.choices}:
        raise ValidationError({"operation_type": "Unsupported repository operation."})
    target_ref = RepositoryExecutionTarget.objects.only("repository_id").get(pk=target_id)
    # Cleanup acceptance locks the Repository before inspecting/creating target
    # work. Use the same order here so maintenance cannot slip in between the
    # cleanup preflight and REMOVING transition, and so both paths avoid a
    # Repository/Target lock-order deadlock.
    repository = Repository.objects.select_for_update().get(
        pk=target_ref.repository_id
    )
    target = (
        RepositoryExecutionTarget.objects.select_for_update()
        .select_related("repository")
        .get(pk=target_id)
    )
    if not target.is_active or target.active_task_id:
        return None
    if repository.status != Repository.Status.CREATED:
        target.is_active = False
        target.save(update_fields=["is_active", "updated_at"])
        return None
    if not repository_execution_target_has_owned_location(target):
        target.is_active = False
        target.save(update_fields=["is_active", "updated_at"])
        return None
    operation_label = dict(RepositoryTask.OperationType.choices)[operation_type]
    task = create_task(
        organization_id=target.organization_id,
        task_type=Task.Type.REPOSITORY_OPERATION,
        display_name=repository_operation_display_name(
            action_label=operation_label,
            repository=target.repository,
            target=target,
        ),
        trigger_type=trigger_type,
        request_payload={
            "repository_id": target.repository_id,
            "target_key": target.target_key,
            "operation_type": operation_type,
            "owner_identity": target.owner_identity,
        },
        resources=[
            {
                "resource_type": TaskResource.Type.REPOSITORY,
                "resource_id": target.repository_id,
                "is_primary": True,
            }
        ],
        normalize_trigger_type=False,
    )
    repository_task = RepositoryTask.objects.create(
        task=task,
        repository=target.repository,
        execution_target=target,
        operation_type=operation_type,
        owner_type=target.owner_type,
        owner_node_id=target.owner_node_id,
        owner_identity=target.owner_identity,
        due_at=due_at,
    )
    target.active_task = task
    target.save(update_fields=["active_task", "updated_at"])
    return repository_task


@transaction.atomic
def schedule_due_maintenance(*, now: datetime | None = None) -> list[int]:
    settings = maintenance_settings()
    if not settings.enabled:
        return []
    current = now or timezone.now()
    discover_repository_execution_targets(now=current)
    scheduled: list[int] = []
    # Each accepted operation takes its Repository and Target locks in
    # create_repository_operation_task(). Do not pre-lock every Target here;
    # doing so would reverse the cleanup path's lock order.
    targets = RepositoryExecutionTarget.objects.filter(is_active=True)
    for target in targets.order_by("target_key"):
        state = target.maintenance_state
        if target.active_task_id or (state.next_retry_at and state.next_retry_at > current):
            continue
        full_due = not state.next_full_due_at or state.next_full_due_at <= current
        quick_due = not state.next_quick_due_at or state.next_quick_due_at <= current
        operation = None
        full_window_due = full_due and _inside_full_window(current, settings)
        if full_window_due:
            window_open = current.astimezone(settings.timezone).replace(
                hour=settings.window_start.hour,
                minute=settings.window_start.minute,
                second=0,
                microsecond=0,
            )
            if settings.window_start > settings.window_end and current.astimezone(settings.timezone).time().replace(tzinfo=None) < settings.window_end:
                window_open -= timedelta(days=1)
            if current >= (window_open + _stable_full_delay(target.target_key, settings)).astimezone(
                datetime_timezone.utc
            ):
                operation = RepositoryTask.OperationType.MAINTENANCE_FULL
        if operation is None and quick_due and not full_window_due:
            operation = RepositoryTask.OperationType.MAINTENANCE_QUICK
        if operation is None or not _concurrency_available(target, settings):
            continue
        repository_task = create_repository_operation_task(
            target_id=target.id,
            operation_type=operation,
            trigger_type=(Task.TriggerType.RETRY if state.consecutive_failures else Task.TriggerType.SYSTEM),
            due_at=current,
        )
        if repository_task:
            scheduled.append(repository_task.id)
    return scheduled


def set_task_step(task: Task, step_name: str, *, status: str, progress: int) -> None:
    TaskStep.objects.filter(task=task, step_name=step_name).update(status=status, progress=progress)
    task.current_step = step_name
    task.progress = progress
    task.save(update_fields=["current_step", "progress", "updated_at"])
    append_task_event(task=task, step=task.steps.filter(step_name=step_name).first(), message=f"Step {step_name} {status}")


def repository_operation_cancellation_supported(repository_task: RepositoryTask) -> bool:
    return (
        repository_task.owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER
        and repository_task.operation_type
        in {
            RepositoryTask.OperationType.MAINTENANCE_QUICK,
            RepositoryTask.OperationType.MAINTENANCE_FULL,
        }
    )


@transaction.atomic
def start_controller_repository_operation(*, repository_task_id: int) -> UUID | None:
    repository_task = RepositoryTask.objects.select_for_update().get(
        pk=repository_task_id
    )
    task = Task.objects.select_for_update().get(pk=repository_task.task_id)
    if (
        not repository_operation_cancellation_supported(repository_task)
        or repository_task.cancel_requested_at is not None
        or task.status != Task.Status.PENDING
    ):
        return None
    start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
    token = uuid4()
    now = timezone.now()
    repository_task.execution_token = token
    repository_task.execution_heartbeat_at = now
    repository_task.save(
        update_fields=["execution_token", "execution_heartbeat_at", "updated_at"]
    )
    return token


@transaction.atomic
def set_controller_repository_operation_step(
    *,
    repository_task_id: int,
    expected_execution_token: UUID,
    step_name: str,
    status: str,
    progress: int,
) -> str:
    repository_task = (
        RepositoryTask.objects.select_for_update()
        .select_related("task")
        .get(pk=repository_task_id)
    )
    if (
        repository_task.execution_token != expected_execution_token
        or repository_task.task.status not in {Task.Status.PENDING, Task.Status.RUNNING}
    ):
        return "lost_lease"
    if repository_task.cancel_requested_at is not None:
        return "cancel"
    set_task_step(
        repository_task.task,
        step_name,
        status=status,
        progress=progress,
    )
    repository_task.execution_heartbeat_at = timezone.now()
    repository_task.save(
        update_fields=["execution_heartbeat_at", "updated_at"]
    )
    return "continue"


def fence_controller_repository_operation(
    *, repository_task_id: int, expected_execution_token: UUID | None
) -> UUID | None:
    recovery_token = uuid4()
    queryset = RepositoryTask.objects.filter(
        pk=repository_task_id,
        task__status=Task.Status.RUNNING,
    )
    if expected_execution_token is None:
        queryset = queryset.filter(execution_token__isnull=True)
    else:
        queryset = queryset.filter(execution_token=expected_execution_token)
    updated = queryset.update(
        execution_token=recovery_token,
        execution_heartbeat_at=timezone.now(),
    )
    return recovery_token if updated == 1 else None


@transaction.atomic
def request_repository_operation_cancel(
    *,
    task_uuid: UUID | str,
    organization_id: int,
    reason: str = "",
    requested_by_id: int | None = None,
) -> tuple[Task, bool]:
    repository_task = (
        RepositoryTask.objects.select_for_update()
        .select_related("task")
        .filter(task__task_uuid=task_uuid, task__organization_id=organization_id)
        .first()
    )
    if repository_task is None:
        raise AppError(
            code="RESOURCE.NOT_FOUND",
            status=404,
            title="Repository task not found",
            diagnostic="Repository maintenance task was not found.",
        )
    task = repository_task.task
    if not repository_operation_cancellation_supported(repository_task):
        raise AppError(
            code=REPOSITORY_OPERATION_NOT_CANCELLABLE,
            status=409,
            title="Repository task cannot be cancelled",
            diagnostic=(
                "Only controller-owned Quick or Full maintenance tasks can be cancelled."
            ),
        )
    if task.status in TERMINAL_STATUSES:
        if task.status == Task.Status.CANCELLED:
            return task, False
        raise AppError(
            code=REPOSITORY_OPERATION_NOT_ACTIVE,
            status=409,
            title="Repository task already finished",
            diagnostic="This repository maintenance task has already finished.",
        )
    if repository_task.cancel_requested_at is not None:
        return task, task.status == Task.Status.RUNNING

    now = timezone.now()
    repository_task.cancel_requested_at = now
    repository_task.cancel_reason = (reason.strip() or "Task cancelled by user")[:500]
    repository_task.save(
        update_fields=["cancel_requested_at", "cancel_reason", "updated_at"]
    )
    append_task_event(
        task=task,
        level=TaskEvent.Level.WARN,
        message="Repository maintenance cancellation requested",
        metadata={
            "reason": repository_task.cancel_reason,
            "requested_by_id": requested_by_id,
        },
    )
    if task.status == Task.Status.PENDING:
        return (
            finalize_repository_operation(
                repository_task_id=repository_task.id,
                succeeded=False,
                cancelled=True,
                error_message=repository_task.cancel_reason,
            ),
            False,
        )
    return task, True


@transaction.atomic
def finalize_repository_operation(
    *,
    repository_task_id: int,
    succeeded: bool,
    result_payload: dict | None = None,
    error_code: str = "",
    error_message: str = "",
    cancelled: bool = False,
    expected_execution_token: UUID | None = None,
) -> Task:
    # ``execution_target`` is nullable for cleanup operations. Joining it in a
    # ``select_for_update()`` query therefore produces a LEFT OUTER JOIN, which
    # PostgreSQL refuses to lock ("FOR UPDATE cannot be applied to the nullable
    # side of an outer join"). Lock the repository task first, then lock the
    # required maintenance target explicitly.
    repository_task = (
        RepositoryTask.objects.select_for_update()
        .select_related("task")
        .get(pk=repository_task_id)
    )
    if repository_task.execution_target_id is None:
        raise ValidationError({"execution_target": "Repository maintenance requires an execution target."})
    task = repository_task.task
    if expected_execution_token is not None and repository_task.execution_token != expected_execution_token:
        return task
    if task.status in TERMINAL_STATUSES:
        return task
    target = RepositoryExecutionTarget.objects.select_for_update().get(
        pk=repository_task.execution_target_id
    )
    state = RepositoryMaintenanceState.objects.select_for_update().get(execution_target=target)
    settings = maintenance_settings()
    now = timezone.now()
    cancelled = cancelled or bool(
        expected_execution_token is not None
        and repository_task.cancel_requested_at is not None
    )
    if cancelled:
        if repository_task.operation_type == RepositoryTask.OperationType.MAINTENANCE_QUICK:
            state.next_quick_due_at = now + settings.quick_interval
        elif repository_task.operation_type == RepositoryTask.OperationType.MAINTENANCE_FULL:
            state.next_full_due_at = now + settings.full_interval
            state.next_quick_due_at = now + settings.quick_interval
        state.consecutive_failures = 0
        state.next_retry_at = None
        status = Task.Status.CANCELLED
    elif succeeded:
        if repository_task.operation_type == RepositoryTask.OperationType.MAINTENANCE_QUICK:
            state.last_quick_success_at = now
            state.next_quick_due_at = now + settings.quick_interval
        elif repository_task.operation_type == RepositoryTask.OperationType.MAINTENANCE_FULL:
            state.last_full_success_at = now
            state.next_full_due_at = now + settings.full_interval
            state.next_quick_due_at = now + settings.quick_interval
        state.consecutive_failures = 0
        state.next_retry_at = None
        status = Task.Status.SUCCESS
    else:
        state.last_failure_at = now
        state.consecutive_failures += 1
        retry_seconds = min(3600, 60 * (2 ** min(state.consecutive_failures - 1, 6)))
        state.next_retry_at = now + timedelta(seconds=retry_seconds)
        status = Task.Status.FAILED
    state.save()
    repository_task.execution_token = None
    repository_task.execution_heartbeat_at = None
    repository_task.save(
        update_fields=["execution_token", "execution_heartbeat_at", "updated_at"]
    )
    if target.active_task_id == task.id:
        target.active_task = None
        target.save(update_fields=["active_task", "updated_at"])
    if status == Task.Status.CANCELLED:
        return cancel_platform_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            reason=(error_message or repository_task.cancel_reason or "Task cancelled by user")[:2000],
        )
    return complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=status,
        progress=100 if succeeded else task.progress,
        result_payload=result_payload,
        error_code=error_code,
        error_message=error_message[:2000],
    )


__all__ = [
    "create_repository_operation_task",
    "discover_repository_execution_targets",
    "fence_controller_repository_operation",
    "finalize_repository_operation",
    "maintenance_settings",
    "repository_operation_cancellation_supported",
    "repository_execution_target_has_owned_location",
    "request_repository_operation_cancel",
    "schedule_due_maintenance",
    "set_controller_repository_operation_step",
    "set_task_step",
    "start_controller_repository_operation",
    "start_task",
]
