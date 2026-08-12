from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.constants import AuditAction, AuditResult, AuditResourceType
from apps.audit.services.interface import write_audit_log
from apps.iam.models import Organization
from apps.node.models import Node
from apps.storage.repositories.models import (
    Credential,
    Repository,
    RepositoryExecutionTarget,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.services.internal.repository_access import repository_payload_for_node
from apps.storage.services.internal.proxy_fs_repository import (
    proxy_fs_uses_managed_subdir,
)
from apps.storage.services.internal.repository_agent_operation import (
    RepositoryAgentOperationResult,
    RepositoryAgentOperationStateUnknown,
    resolve_or_dispatch_repository_agent_operation,
)
from apps.storage.services.internal.repository_secrets import (
    resolve_repository_secrets,
    sanitize_repository_config,
    scrub_secrets,
    secret_values_for_scrub,
)
from apps.storage.services.internal.repository_endpoints import repository_control_endpoint
from apps.storage.services.internal.repository_task_naming import (
    repository_operation_display_name,
)
from apps.storage.services.internal.s3_client import (
    S3ClientError,
    delete_s3_bucket_if_empty,
    delete_s3_prefix,
)
from apps.storage.services.internal.s3_url_style import normalize_s3_url_style
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import append_task_event, complete_task, create_task, start_task
from apps.task.services.recovery import (
    CONTROL_PLANE_RESTART_INTERRUPTED,
    MAX_AUTOMATIC_REPLACEMENTS,
    RecoveryDecision,
    RecoveryPlan,
    record_recovery_decision,
)


ACTIVE_TASK_STATUSES = (Task.Status.PENDING, Task.Status.RUNNING)
CLEANUP_STEPS = (
    "check_cleanup_dependencies",
    "verify_cleanup_owner",
    "prepare_cleanup_target",
    "delete_physical_repository",
    "cleanup_owner_local_state",
    "verify_cleanup_result",
    "finalize_cleanup_metadata",
)


class RepositoryCleanupBlocked(ValidationError):
    def __init__(self, preflight: dict[str, Any]):
        super().__init__("Repository cleanup is blocked.")
        self.preflight = preflight


def repository_active_task_blockers(
    *,
    repository: Repository,
    ignored_task_ids: Iterable[int] = (),
) -> list[dict[str, Any]]:
    """Return read-only blockers from active tasks using a repository."""
    ignored = {int(task_id) for task_id in ignored_task_ids if task_id}
    blockers: list[dict[str, Any]] = []
    active_tasks = _active_repository_tasks(repository=repository).exclude(
        id__in=ignored
    )
    for task in active_tasks.order_by("-created_at", "-id")[:50]:
        blockers.append(
            {
                "code": "active_task",
                "detail": f'{task.task_type} task "{task.display_name}" is still active.',
                "task_id": task.id,
                "task_uuid": str(task.task_uuid),
                "task_type": task.task_type,
            }
        )
    return blockers


def repository_cleanup_preflight(
    *,
    repository: Repository,
    force: bool = False,
    allow_associations: bool = False,
    ignored_task_ids: Iterable[int] = (),
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    ignored = {int(task_id) for task_id in ignored_task_ids if task_id}
    backup_config_model = apps.get_model("protection", "BackupConfig")
    snapshot_model = apps.get_model("protection", "BackupSourceSnapshot")
    restore_plan_model = apps.get_model("restore", "RestorePlan")
    restore_record_model = apps.get_model("restore", "RestoreRecord")

    associated_configs = backup_config_model.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    )
    associated_config_ids = list(associated_configs.values_list("id", flat=True))
    associated_source_count = associated_configs.count()
    snapshot_count = snapshot_model.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    ).count()
    restore_plan_count = restore_plan_model.objects.filter(
        organization_id=repository.organization_id,
        backup_config_id__in=associated_config_ids,
    ).count()
    restore_record_count = restore_record_model.objects.filter(
        Q(backup_config_id__in=associated_config_ids)
        | Q(items__repository_id=repository.id),
        organization_id=repository.organization_id,
    ).distinct().count()
    if not allow_associations and associated_source_count:
        blockers.append(
            {
                "code": "associated_sources",
                "detail": f"Repository has {associated_source_count} associated backup source(s).",
                "count": associated_source_count,
            }
        )
    if not allow_associations and snapshot_count:
        blockers.append(
            {
                "code": "associated_snapshots",
                "detail": f"Repository has {snapshot_count} snapshot record(s).",
                "count": snapshot_count,
            }
        )
    if not allow_associations and restore_plan_count:
        blockers.append(
            {
                "code": "associated_restore_plans",
                "detail": f"Repository has {restore_plan_count} restore plan(s).",
                "count": restore_plan_count,
            }
        )
    if not allow_associations and restore_record_count:
        warnings.append(
            {
                "code": "associated_restore_records",
                "detail": (
                    f"Repository has {restore_record_count} historical restore record(s). "
                    "The history is retained for audit and does not block deletion."
                ),
                "count": restore_record_count,
            }
        )

    targets = _ensure_cleanup_targets(repository)
    if (
        not allow_associations
        and repository.repo_type == Repository.Type.NAS
        and repository.bind_node_id is None
    ):
        active_targets = [target for target in targets if target.is_active]
        if active_targets:
            warnings.append(
                {
                "code": "physical_targets_to_cleanup",
                "detail": (
                    f"Direct NAS repository still has {len(active_targets)} physical target(s) "
                    "that will be cleaned before repository removal."
                ),
                "count": len(active_targets),
                }
            )

    if (
        repository.repo_type == Repository.Type.PROXY_FS
        and not proxy_fs_uses_managed_subdir(repository)
    ):
        config = repository.config if isinstance(repository.config, dict) else {}
        warnings.append(
            {
                "code": "legacy_local_disk_preserved",
                "detail": (
                    "This Local Disk predates managed repository directories. "
                    f'Its physical data at "{str(config.get("proxy_node_dir") or "").strip()}" '
                    "will be preserved when the repository registration is removed."
                ),
            }
        )

    blockers.extend(
        repository_active_task_blockers(
            repository=repository,
            ignored_task_ids=ignored,
        )
    )

    active_cleanup = _logical_cleanup_tasks(repository).filter(
        task__status__in=ACTIVE_TASK_STATUSES,
    ).select_related("task", "execution_target", "triggered_by_task").first()
    return {
        "allowed": not blockers,
        "force": force,
        "repository_id": repository.id,
        "repository_status": repository.status,
        "associated_source_count": associated_source_count,
        "active_snapshot_count": snapshot_count,
        "snapshot_count": snapshot_count,
        "restore_plan_count": restore_plan_count,
        "restore_record_count": restore_record_count,
        "blockers": blockers,
        "warnings": warnings,
        "targets": _cleanup_target_payloads(repository=repository, targets=targets),
        "active_cleanup_task": (
            repository_cleanup_task_payload(active_cleanup) if active_cleanup else None
        ),
    }


def create_repository_cleanup_task(
    *,
    repository: Repository,
    requested_by=None,
    force: bool = False,
    dispatch: bool = True,
) -> RepositoryTask:
    existing = _logical_cleanup_tasks(repository).filter(
        task__status__in=ACTIVE_TASK_STATUSES,
    ).select_related("task", "execution_target", "triggered_by_task").first()
    if existing is not None:
        if existing.force == force:
            return existing
        raise ValidationError(
            {"detail": "Repository already has an active cleanup task in a different mode."}
        )
    if repository.status == Repository.Status.REMOVED:
        latest = _logical_cleanup_tasks(repository).filter(
            task__status=Task.Status.SUCCESS,
        ).select_related("task", "execution_target", "triggered_by_task").first()
        if latest is not None:
            return latest

    preflight = repository_cleanup_preflight(repository=repository, force=force)
    if not preflight["allowed"]:
        raise RepositoryCleanupBlocked(preflight)

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        active = _logical_cleanup_tasks(locked).filter(
            task__status__in=ACTIVE_TASK_STATUSES,
        ).select_related("task", "execution_target", "triggered_by_task").first()
        if active is not None:
            if active.force == force:
                return active
            raise ValidationError(
                {"detail": "Repository already has an active cleanup task in a different mode."}
            )
        if locked.status == Repository.Status.REMOVED:
            latest = _logical_cleanup_tasks(locked).filter(
                task__status=Task.Status.SUCCESS,
            ).select_related("task", "execution_target", "triggered_by_task").first()
            if latest is not None:
                return latest
        if locked.status not in {
            Repository.Status.CREATED,
            Repository.Status.REMOVE_FAILED,
            Repository.Status.CREATE_FAILED,
            Repository.Status.CREATING,
        }:
            raise ValidationError(
                {"detail": f"Repository in status {locked.status} cannot be cleaned up."}
            )
        second_preflight = repository_cleanup_preflight(repository=locked, force=force)
        if not second_preflight["allowed"]:
            raise RepositoryCleanupBlocked(second_preflight)

        targets = _ensure_cleanup_targets(locked)
        target: RepositoryExecutionTarget | None
        if locked.repo_type == Repository.Type.NAS and locked.bind_node_id is None:
            target = None
        else:
            target = targets[0] if targets else None
            if target is None:
                raise ValidationError({"detail": "Repository physical cleanup target was not found."})

        locked.status = Repository.Status.REMOVING
        locked.save(update_fields=["status", "updated_at"])
        repository_task = _create_cleanup_task(
            repository=locked,
            operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
            target=target,
            requested_by=requested_by,
            force=force,
            triggered_by_task=None,
        )
        _write_cleanup_audit(
            repository_task=repository_task,
            user=requested_by,
            result=AuditResult.SUCCESS,
            details="Repository lifecycle cleanup accepted",
        )
        if dispatch:
            transaction.on_commit(lambda: _dispatch_cleanup_task(repository_task.id))
        return repository_task


def create_direct_nas_target_cleanup_task(
    *,
    repository: Repository,
    target_id: int,
    triggered_by_task: Task,
    requested_by=None,
    force: bool = False,
    dispatch: bool = False,
) -> RepositoryTask:
    if triggered_by_task.task_type not in {
        Task.Type.SOURCE_UNREGISTER,
        Task.Type.REPOSITORY_OPERATION,
    }:
        raise ValidationError(
            {
                "triggered_by_task": (
                    "Direct NAS physical cleanup must be triggered by source deregistration "
                    "or repository cleanup."
                )
            }
        )
    ignored_task_ids = [
        triggered_by_task.id,
        *_active_target_cleanup_task_ids(
            repository,
            triggered_by_task=triggered_by_task,
        ),
    ]
    preflight = repository_cleanup_preflight(
        repository=repository,
        allow_associations=True,
        ignored_task_ids=ignored_task_ids,
    )
    if not preflight["allowed"]:
        raise RepositoryCleanupBlocked(preflight)

    with transaction.atomic():
        locked = Repository.objects.select_for_update().get(
            pk=repository.id,
            organization_id=repository.organization_id,
        )
        if locked.repo_type != Repository.Type.NAS or locked.bind_node_id is not None:
            raise ValidationError(
                {"detail": "Physical target cleanup is only supported for Direct NAS repositories."}
            )
        allowed_statuses = {Repository.Status.CREATED}
        if triggered_by_task.task_type == Task.Type.REPOSITORY_OPERATION:
            allowed_statuses.add(Repository.Status.REMOVING)
        if locked.status not in allowed_statuses:
            raise ValidationError(
                {"detail": "Direct NAS physical cleanup requires an active logical repository."}
            )
        target = RepositoryExecutionTarget.objects.select_for_update().filter(
            pk=target_id,
            repository=locked,
            organization_id=locked.organization_id,
        ).first()
        if target is None:
            raise ValidationError({"detail": "Direct NAS physical cleanup target was not found."})

        source_unregister_attempt = int(triggered_by_task.retry_count or 0)
        existing = RepositoryTask.objects.filter(
            repository=locked,
            execution_target=target,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
            triggered_by_task=triggered_by_task,
            task__request_payload__source_unregister_attempt=source_unregister_attempt,
        ).select_related("task", "execution_target", "triggered_by_task").order_by(
            "-created_at", "-id"
        ).first()
        if existing is not None:
            return existing

        second_ignored_task_ids = [
            triggered_by_task.id,
            *_active_target_cleanup_task_ids(
                locked,
                triggered_by_task=triggered_by_task,
            ),
        ]
        second_preflight = repository_cleanup_preflight(
            repository=locked,
            allow_associations=True,
            ignored_task_ids=second_ignored_task_ids,
        )
        if not second_preflight["allowed"]:
            raise RepositoryCleanupBlocked(second_preflight)

        repository_task = _create_cleanup_task(
            repository=locked,
            operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
            target=target,
            requested_by=requested_by,
            force=force,
            triggered_by_task=triggered_by_task,
        )
        _write_cleanup_audit(
            repository_task=repository_task,
            user=requested_by,
            result=AuditResult.SUCCESS,
            details="Direct NAS physical target cleanup accepted",
        )
        if dispatch:
            transaction.on_commit(lambda: _dispatch_cleanup_task(repository_task.id))
        return repository_task


def run_repository_cleanup_task(*, repository_task_id: int) -> dict[str, Any]:
    repository_task = RepositoryTask.objects.select_related(
        "task",
        "repository",
        "execution_target",
        "triggered_by_task",
    ).get(pk=repository_task_id)
    if repository_task.operation_type not in {
        RepositoryTask.OperationType.CLEANUP_TARGET,
        RepositoryTask.OperationType.CLEANUP_REPOSITORY,
    }:
        raise ValidationError("Repository task is not a cleanup operation.")
    task = repository_task.task
    if task.status in {Task.Status.SUCCESS, Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.TIMEOUT}:
        return task.result_payload if isinstance(task.result_payload, dict) else {"status": task.status}

    started_now = task.status == Task.Status.PENDING
    if started_now:
        start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
    elif task.status == Task.Status.RUNNING:
        if (
            repository_task.owner_type
            == RepositoryExecutionTarget.OwnerType.CONTROLLER
            and repository_task.operation_type
            not in {
                RepositoryTask.OperationType.CLEANUP_TARGET,
                RepositoryTask.OperationType.CLEANUP_REPOSITORY,
            }
        ):
            return {"status": task.status, "repository_task_id": repository_task.id, "idempotent": True}
    else:
        return {"status": task.status, "repository_task_id": repository_task.id, "idempotent": True}

    try:
        resuming_physical_delete = (
            not started_now
            and task.current_step == "delete_physical_repository"
            and int(task.progress or 0) >= 40
        )
        if not resuming_physical_delete:
            _set_cleanup_step(task, "check_cleanup_dependencies", TaskStep.Status.RUNNING, 5)
            is_target_cleanup = (
                repository_task.operation_type == RepositoryTask.OperationType.CLEANUP_TARGET
            )
            ignored_task_ids = [task.id]
            if is_target_cleanup:
                ignored_task_ids.extend(_active_target_cleanup_task_ids(repository_task.repository))
                if repository_task.triggered_by_task_id:
                    ignored_task_ids.append(repository_task.triggered_by_task_id)
            elif (
                repository_task.repository.repo_type == Repository.Type.NAS
                and repository_task.repository.bind_node_id is None
            ):
                ignored_task_ids.extend(
                    _active_target_cleanup_task_ids(repository_task.repository)
                )
            preflight = repository_cleanup_preflight(
                repository=repository_task.repository,
                force=repository_task.force,
                allow_associations=is_target_cleanup,
                ignored_task_ids=ignored_task_ids,
            )
            if not preflight["allowed"]:
                raise RepositoryCleanupBlocked(preflight)
            _set_cleanup_step(task, "check_cleanup_dependencies", TaskStep.Status.SUCCESS, 10)
            _set_cleanup_step(task, "verify_cleanup_owner", TaskStep.Status.SUCCESS, 20)
            _set_cleanup_step(task, "prepare_cleanup_target", TaskStep.Status.SUCCESS, 30)
            _set_cleanup_step(task, "delete_physical_repository", TaskStep.Status.RUNNING, 40)
        physical_cleanup_complete = True
        try:
            physical_operation = _execute_physical_cleanup(
                repository_task,
                allow_dispatch=not resuming_physical_delete,
            )
            if isinstance(physical_operation, RepositoryAgentOperationResult):
                if physical_operation.waiting:
                    return {
                        "status": "waiting",
                        "repository_task_id": repository_task.id,
                        "remote_task_id": str(physical_operation.node_task_id),
                    }
                result = {"physical_cleanup": "deleted", **physical_operation.result}
            else:
                result = physical_operation
                if result.get("waiting"):
                    return {
                        "status": "waiting",
                        "repository_task_id": repository_task.id,
                        **result,
                    }
                physical_cleanup_complete = bool(
                    result.get("cleanup_complete", True)
                )
        except Exception as exc:
            if not repository_task.force:
                raise
            physical_cleanup_complete = False
            safe_failure = str(
                scrub_secrets(
                    _cleanup_exception_message(exc),
                    extra_values=_repository_secret_values(
                        repository_task.repository,
                    ),
                )
            )[:2000]
            result = {
                "physical_cleanup": "failed",
                "force": True,
                "outcome": "force_cleanup_success",
                "cleanup_complete": False,
                "cleanup_failures": [
                    {
                        "code": _cleanup_error_code(exc),
                        "detail": safe_failure,
                    }
                ],
                "retained_resources": _retained_repository_resources(
                    repository_task,
                ),
            }
        retained_unmounted_nas = _is_unmounted_nas_cleanup(result)
        if not physical_cleanup_complete and not repository_task.force and not retained_unmounted_nas:
            cleanup_failures = [
                item
                for item in result.get("cleanup_failures") or []
                if isinstance(item, dict)
            ]
            first_failure = cleanup_failures[0] if cleanup_failures else {}
            raise ValidationError(
                str(
                    first_failure.get("detail")
                    or "Physical repository cleanup reported an incomplete result."
                )
            )
        if physical_cleanup_complete:
            physical_data_preserved = (
                result.get("physical_cleanup") == "preserved_legacy_directory"
            )
            result = {
                **result,
                "force": bool(repository_task.force),
                "outcome": (
                    "cleanup_success_data_preserved"
                    if physical_data_preserved
                    else "cleanup_success"
                ),
                "cleanup_complete": True,
                "cleanup_failures": [],
                "retained_resources": (
                    result.get("retained_resources") or []
                    if physical_data_preserved
                    else []
                ),
            }
        elif retained_unmounted_nas:
            result = {
                **result,
                "force": bool(repository_task.force),
                "outcome": "cleanup_success_with_retained_resources",
                "cleanup_complete": False,
                "cleanup_failures": result.get("cleanup_failures") or [],
                "retained_resources": result.get("retained_resources") or [],
            }
            append_task_event(
                task=task,
                step=task.steps.filter(step_name="delete_physical_repository").first(),
                level=TaskEvent.Level.WARN,
                message="Remote NAS repository cleanup skipped because the target was not mounted",
                metadata={
                    "owner_node_id": repository_task.owner_node_id,
                    "mount_status": "not_mounted",
                    "physical_cleanup": "skipped_unmounted",
                    "retained_resources": result["retained_resources"],
                },
            )
        else:
            result = {
                **result,
                "force": True,
                "outcome": "force_cleanup_success",
                "cleanup_complete": False,
                "cleanup_failures": result.get("cleanup_failures") or [],
                "retained_resources": result.get("retained_resources") or [],
            }
        _set_cleanup_step(
            task,
            "delete_physical_repository",
            (
                TaskStep.Status.SUCCESS
                if physical_cleanup_complete
                else TaskStep.Status.WARNING
            ),
            75,
        )
        _set_cleanup_step(task, "cleanup_owner_local_state", TaskStep.Status.SUCCESS, 85)
        _set_cleanup_step(task, "verify_cleanup_result", TaskStep.Status.SUCCESS, 95)
        # Commit the lifecycle metadata and terminal task state together. This
        # prevents a successful Task from being visible before the repository
        # tombstone or Direct NAS target state has been persisted.
        with transaction.atomic():
            _apply_cleanup_success(
                repository_task_id=repository_task.id,
                cleanup_complete=physical_cleanup_complete,
                cleanup_result=(
                    Repository.CleanupResult.PRESERVED
                    if result.get("physical_cleanup") == "preserved_legacy_directory"
                    else Repository.CleanupResult.FORCE_SKIPPED
                    if retained_unmounted_nas
                    else None
                ),
            )
            _set_cleanup_step(task, "finalize_cleanup_metadata", TaskStep.Status.SUCCESS, 100)
            complete_task(
                task_uuid=task.task_uuid,
                organization_id=task.organization_id,
                status=Task.Status.SUCCESS,
                progress=100,
                result_payload=scrub_secrets(result),
            )
        _finalize_cleanup_task(repository_task_id=repository_task.id)
        return {"status": "success", "repository_task_id": repository_task.id, **scrub_secrets(result)}
    except RepositoryAgentOperationStateUnknown as exc:
        safe_message = str(exc)[:2000]
        _set_cleanup_step(
            task,
            str(task.current_step or "delete_physical_repository"),
            TaskStep.Status.FAILED,
            int(task.progress or 0),
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.FAILED,
            progress=max(1, int(task.progress or 0)),
            error_code=CONTROL_PLANE_RESTART_INTERRUPTED,
            error_message=safe_message,
        )
        _finalize_cleanup_task(repository_task_id=repository_task.id)
        replacement = _create_replacement_cleanup_task(repository_task_id=repository_task.id)
        record_recovery_decision(
            task=task,
            plan=RecoveryPlan(
                decision=(
                    RecoveryDecision.FAIL_AND_REPLACE
                    if replacement is not None
                    else RecoveryDecision.FAIL
                ),
                reason=safe_message,
                evidence={
                    "current_step": task.current_step,
                    "operation_type": repository_task.operation_type,
                    "remote_task_id": None,
                },
            ),
            replacement_task=replacement.task if replacement is not None else None,
        )
        return {
            "status": "failed",
            "repository_task_id": repository_task.id,
            "replacement_task_uuid": (
                str(replacement.task.task_uuid) if replacement is not None else None
            ),
        }
    except Exception as exc:
        message = _cleanup_exception_message(exc)
        safe_message = str(
            scrub_secrets(
                message,
                extra_values=_repository_secret_values(repository_task.repository),
            )
        )[:2000]
        _set_cleanup_step(
            task,
            str(task.current_step or "delete_physical_repository"),
            TaskStep.Status.FAILED,
            int(task.progress or 0),
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=task.organization_id,
            status=Task.Status.FAILED,
            progress=max(1, int(task.progress or 0)),
            error_code=_cleanup_error_code(exc),
            error_message=safe_message,
        )
        _finalize_cleanup_task(repository_task_id=repository_task.id)
        return {"status": "failed", "repository_task_id": repository_task.id, "error": safe_message}


def repository_cleanup_task_payload(repository_task: RepositoryTask) -> dict[str, Any]:
    repository_task = RepositoryTask.objects.select_related(
        "task",
        "execution_target",
        "triggered_by_task",
    ).get(pk=repository_task.pk)
    task = repository_task.task
    return {
        "id": task.id,
        "task_uuid": str(task.task_uuid),
        "repository_id": repository_task.repository_id,
        "status": task.status,
        "operation_type": repository_task.operation_type,
        "force": repository_task.force,
        "target_id": repository_task.execution_target_id,
        "target_key": (
            repository_task.execution_target.target_key
            if repository_task.execution_target_id
            else ""
        ),
        "owner_node_id": repository_task.owner_node_id,
        "repository_subdir": (
            repository_task.execution_target.repository_subdir
            if repository_task.execution_target_id
            else ""
        ),
        "triggered_by_task_uuid": (
            str(repository_task.triggered_by_task.task_uuid)
            if repository_task.triggered_by_task_id
            else None
        ),
        "triggered_by_task_type": (
            repository_task.triggered_by_task.task_type
            if repository_task.triggered_by_task_id
            else None
        ),
        "error_code": task.error_code,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def direct_nas_cleanup_target_ids(
    *,
    repository: Repository,
    backup_config_ids: Iterable[int],
    owner_node_id: int | None = None,
) -> list[int]:
    if repository.repo_type != Repository.Type.NAS or repository.bind_node_id:
        return []
    config_ids = {int(value) for value in backup_config_ids if value}
    targets = _ensure_cleanup_targets(repository)
    matching_keys: set[tuple[int, str]] = set()
    for shard in RepositoryUsageShard.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
        usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
    ):
        shard_config_ids = {
            int(value)
            for value in (shard.source_config_ids if isinstance(shard.source_config_ids, list) else [])
            if value
        }
        if config_ids & shard_config_ids or (
            owner_node_id is not None and int(shard.node_id) == int(owner_node_id)
        ):
            matching_keys.add((int(shard.node_id), str(shard.repository_subdir)))
    return [
        target.id
        for target in targets
        if target.owner_node_id is not None
        and (int(target.owner_node_id), str(target.repository_subdir)) in matching_keys
    ]


def _logical_cleanup_tasks(repository: Repository):
    return RepositoryTask.objects.filter(
        repository=repository,
        operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
        triggered_by_task=None,
    )


def _active_repository_tasks(*, repository: Repository):
    task_ids: set[int] = set(
        TaskResource.objects.filter(
            resource_type=TaskResource.Type.REPOSITORY,
            resource_id=repository.id,
        ).values_list("task_id", flat=True)
    )
    task_ids.update(
        RepositoryTask.objects.filter(repository=repository).values_list("task_id", flat=True)
    )
    snapshot_model = apps.get_model("protection", "BackupSourceSnapshot")
    task_ids.update(
        snapshot_model.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
        ).values_list("task_id", flat=True)
    )
    restore_item_model = apps.get_model("restore", "RestoreRecordItem")
    task_ids.update(
        restore_item_model.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
        ).values_list("restore_record__task_id", flat=True)
    )
    return Task.objects.filter(
        organization_id=repository.organization_id,
        status__in=ACTIVE_TASK_STATUSES,
    ).filter(Q(id__in=task_ids) | Q(request_payload__repository_id=repository.id)).distinct()


def _active_target_cleanup_task_ids(
    repository: Repository,
    *,
    triggered_by_task: Task | None = None,
) -> list[int]:
    queryset = RepositoryTask.objects.filter(
        repository=repository,
        operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
        task__status__in=ACTIVE_TASK_STATUSES,
    )
    if triggered_by_task is not None:
        queryset = queryset.filter(
            triggered_by_task=triggered_by_task,
            task__request_payload__source_unregister_attempt=int(
                triggered_by_task.retry_count or 0
            ),
        )
    return list(queryset.values_list("task_id", flat=True))


def _ensure_cleanup_targets(repository: Repository) -> list[RepositoryExecutionTarget]:
    definitions: list[tuple[str, str, int | None, str]] = []
    if repository.repo_type == Repository.Type.S3:
        definitions.append(
            (f"repository:{repository.id}", RepositoryExecutionTarget.OwnerType.CONTROLLER, None, "")
        )
    elif repository.bind_node_id:
        definitions.append(
            (
                f"repository:{repository.id}",
                RepositoryExecutionTarget.OwnerType.NODE,
                int(repository.bind_node_id),
                "",
            )
        )
    elif repository.repo_type == Repository.Type.NAS:
        shards = RepositoryUsageShard.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
            usage_scope=RepositoryUsageShard.Scope.DIRECT_NAS_AGENT,
        ).order_by("node_id", "repository_subdir")
        for shard in shards:
            definitions.append(
                (
                    f"repository:{repository.id}:node:{shard.node_id}:subdir:{shard.repository_subdir}",
                    RepositoryExecutionTarget.OwnerType.NODE,
                    int(shard.node_id),
                    str(shard.repository_subdir),
                )
            )
    targets: list[RepositoryExecutionTarget] = []
    for key, owner_type, node_id, subdir in definitions:
        target, _created = RepositoryExecutionTarget.objects.update_or_create(
            target_key=key,
            defaults={
                "organization_id": repository.organization_id,
                "repository": repository,
                "owner_type": owner_type,
                "owner_node_id": node_id,
                "owner_identity": _owner_identity(node_id),
            },
        )
        if subdir and target.repository_subdir != subdir:
            target.repository_subdir = subdir
            target.save(update_fields=["repository_subdir", "updated_at"])
        targets.append(target)
    return targets


def _create_cleanup_task(
    *,
    repository: Repository,
    operation_type: str,
    target: RepositoryExecutionTarget | None,
    requested_by,
    force: bool,
    triggered_by_task: Task | None,
    replaces_task: Task | None = None,
) -> RepositoryTask:
    owner_type = target.owner_type if target else RepositoryExecutionTarget.OwnerType.CONTROLLER
    owner_node_id = target.owner_node_id if target else None
    owner_identity = target.owner_identity if target else "hfl-cleanup@controller"
    action_label = (
        "Delete Subrepository"
        if operation_type == RepositoryTask.OperationType.CLEANUP_TARGET
        else "Delete Repository"
    )
    task = create_task(
        organization_id=repository.organization_id,
        task_type=Task.Type.REPOSITORY_OPERATION,
        display_name=repository_operation_display_name(
            action_label=action_label,
            repository=repository,
            target=(
                target
                if operation_type == RepositoryTask.OperationType.CLEANUP_TARGET
                else None
            ),
        ),
        trigger_type=(
            Task.TriggerType.RETRY
            if replaces_task is not None
            else Task.TriggerType.SYSTEM
            if triggered_by_task is not None
            else Task.TriggerType.MANUAL
        ),
        request_payload={
            "repository_id": repository.id,
            "operation_type": operation_type,
            "target_key": target.target_key if target else "",
            "force": force,
            "triggered_by_task_uuid": (
                str(triggered_by_task.task_uuid) if triggered_by_task is not None else None
            ),
            "source_unregister_attempt": (
                int(triggered_by_task.retry_count or 0)
                if triggered_by_task is not None
                else None
            ),
            "cleanup_plan": _repository_cleanup_plan(
                repository=repository,
                target=target,
                operation_type=operation_type,
            ),
        },
        resources=[
            {
                "resource_type": TaskResource.Type.REPOSITORY,
                "resource_id": repository.id,
                "is_primary": True,
            }
        ],
        steps=list(CLEANUP_STEPS),
        normalize_trigger_type=False,
        replaces_task=replaces_task,
    )
    repository_task = RepositoryTask.objects.create(
        task=task,
        repository=repository,
        execution_target=target,
        triggered_by_task=triggered_by_task,
        force=force,
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
                    {"detail": f"Repository target {target.target_key} already has an active task."}
                )
        target.active_task = task
        target.is_active = True
        target.save(update_fields=["active_task", "is_active", "updated_at"])
    return repository_task


def _repository_cleanup_plan(
    *,
    repository: Repository,
    target: RepositoryExecutionTarget | None,
    operation_type: str,
) -> dict[str, Any]:
    """Capture immutable, secret-free repository cleanup boundaries."""
    config = scrub_secrets(sanitize_repository_config(repository.config))
    identity_fields = {
        key: config.get(key)
        for key in (
            "prefix",
            "path",
            "proxy_node_dir",
            "server_address",
            "share_path",
            "mount_point",
        )
        if config.get(key) not in (None, "")
    }
    shards = list(
        RepositoryUsageShard.objects.filter(
            organization_id=repository.organization_id,
            repository_id=repository.id,
            is_active=True,
        )
        .order_by("id")
        .values(
            "id",
            "node_id",
            "repository_subdir",
            "mount_point",
        )
    )
    return {
        "version": 1,
        "operation_type": operation_type,
        "repository": {
            "id": repository.id,
            "name": repository.name,
            "type": repository.repo_type,
            "bucket": repository.s3_bucket,
            "bucket_mode": repository.s3_bucket_mode,
            "created_at": (
                repository.created_at.isoformat()
                if repository.created_at is not None
                else None
            ),
            **identity_fields,
        },
        "target": (
            {
                "id": target.id,
                "target_key": target.target_key,
                "owner_type": target.owner_type,
                "owner_node_id": target.owner_node_id,
                "repository_subdir": target.repository_subdir,
            }
            if target is not None
            else None
        ),
        "usage_shards": shards,
    }


@transaction.atomic
def _create_replacement_cleanup_task(
    *,
    repository_task_id: int,
) -> RepositoryTask | None:
    original = (
        RepositoryTask.objects.select_for_update()
        .select_related("task", "repository")
        .get(pk=repository_task_id)
    )
    original_task = original.task
    if original_task.status != Task.Status.FAILED:
        return None
    if int(original_task.recovery_attempt or 0) >= MAX_AUTOMATIC_REPLACEMENTS:
        return None
    try:
        return original_task.replacement_task.repository_operation
    except (ObjectDoesNotExist, RepositoryTask.DoesNotExist):
        pass

    repository = Repository.objects.select_for_update().get(pk=original.repository_id)
    target = (
        RepositoryExecutionTarget.objects.select_for_update().get(
            pk=original.execution_target_id
        )
        if original.execution_target_id
        else None
    )
    is_target_cleanup = original.operation_type == RepositoryTask.OperationType.CLEANUP_TARGET
    ignored_task_ids = [original_task.id]
    if original.triggered_by_task_id:
        ignored_task_ids.append(original.triggered_by_task_id)
    preflight = repository_cleanup_preflight(
        repository=repository,
        force=original.force,
        allow_associations=is_target_cleanup,
        ignored_task_ids=ignored_task_ids,
    )
    if not preflight["allowed"]:
        return None
    if not is_target_cleanup:
        repository.status = Repository.Status.REMOVING
        repository.save(update_fields=["status", "updated_at"])

    replacement = _create_cleanup_task(
        repository=repository,
        operation_type=original.operation_type,
        target=target,
        requested_by=_repository_task_user(original),
        force=original.force,
        triggered_by_task=original.triggered_by_task,
        replaces_task=original_task,
    )
    _write_cleanup_audit(
        repository_task=replacement,
        user=_repository_task_user(original),
        result=AuditResult.SUCCESS,
        details=f"Recovery replacement for interrupted task {original_task.task_uuid}",
    )
    transaction.on_commit(lambda: _dispatch_cleanup_task(replacement.id))
    return replacement


def _execute_physical_cleanup(
    repository_task: RepositoryTask,
    *,
    allow_dispatch: bool = True,
) -> dict[str, Any] | RepositoryAgentOperationResult:
    repository = repository_task.repository
    target = repository_task.execution_target
    if target is None:
        if (
            repository.repo_type == Repository.Type.NAS
            and repository.bind_node_id is None
        ):
            return _execute_direct_nas_repository_cleanup(repository_task)
        return {"physical_cleanup": "not_required", "scope": "metadata"}
    if target.owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER:
        if repository.repo_type != Repository.Type.S3:
            return {"physical_cleanup": "not_required", "scope": "metadata"}
        config = sanitize_repository_config(repository.config)
        secrets_payload = resolve_repository_secrets(repository)
        s3_args = dict(
            endpoint=repository_control_endpoint(config),
            region=str(config.get("region") or ""),
            bucket=str(repository.s3_bucket or ""),
            access_key_id=str(config.get("access_key_id") or ""),
            secret_access_key=str(secrets_payload.get("secret_access_key") or ""),
            s3_url_style=normalize_s3_url_style(
                config.get("s3_url_style"), platform=repository.s3_platform
            ),
            use_tls=config.get("use_tls") is not False,
        )
        result = delete_s3_prefix(
            **s3_args,
            prefix=str(config.get("prefix") or ""),
        )
        if repository.s3_bucket_mode == Repository.S3BucketMode.NEW:
            bucket_cleanup = delete_s3_bucket_if_empty(**s3_args)
        else:
            bucket_cleanup = {
                "bucket": str(repository.s3_bucket or ""),
                "status": "skipped_existing_bucket",
                "reason": "bucket_not_created_for_repository",
            }
        _remove_controller_repository_state(repository.id)
        return {
            "physical_cleanup": "deleted",
            "scope": "s3_prefix",
            **result,
            "bucket_cleanup": bucket_cleanup,
        }

    node = Node.objects.filter(
        id=target.owner_node_id,
        organization_id=repository.organization_id,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError("Repository owner node was not found.")
    inventory = (node.metadata or {}).get("inventory") if isinstance(node.metadata, dict) else {}
    capabilities = inventory.get("capabilities") if isinstance(inventory, dict) else []
    capabilities = capabilities if isinstance(capabilities, list) else []
    cleanup_scope = "managed_repository"
    if repository.repo_type == Repository.Type.PROXY_FS:
        if not proxy_fs_uses_managed_subdir(repository):
            if "repository_cleanup_v2" not in capabilities:
                return {
                    "physical_cleanup": "preserved_legacy_directory",
                    "scope": "legacy_local_disk",
                    "local_state_cleanup": "agent_upgrade_required",
                    "cleanup_complete": True,
                    "retained_resources": ["legacy_local_disk_directory"],
                }
            cleanup_scope = "local_state_only"
        elif "repository_cleanup_v2" not in capabilities:
            raise ValidationError(
                "Repository owner does not advertise repository_cleanup_v2. Upgrade the Proxy before deleting this Local Disk."
            )
    elif "repository_cleanup_v1" not in capabilities:
        raise ValidationError("Repository owner does not advertise repository_cleanup_v1.")
    repository_payload = repository_payload_for_node(
        repository=repository,
        node=node,
        source_type="proxy" if node.role == "proxy" else "agent",
        source_ref_id=node.id,
    )
    if repository.repo_type == Repository.Type.NAS and target.repository_subdir:
        repository_payload["subdir"] = target.repository_subdir
    if repository.repo_type == Repository.Type.NAS:
        unmounted_policy = "retain_and_continue"
    else:
        unmounted_policy = ""
    return resolve_or_dispatch_repository_agent_operation(
        repository_task=repository_task,
        node=node,
        payload={
            "operation_type": repository_task.operation_type,
            "cleanup_scope": cleanup_scope,
            "unmounted_policy": unmounted_policy,
            "repository": repository_payload,
        },
        correlation_type="repository_cleanup",
        timeout_seconds=1800,
        allow_dispatch=allow_dispatch,
    )


def _execute_direct_nas_repository_cleanup(
    repository_task: RepositoryTask,
) -> dict[str, Any]:
    """Advance every historical Direct NAS Agent target under one parent."""
    repository = repository_task.repository
    targets = [
        target
        for target in _ensure_cleanup_targets(repository)
        if target.is_active
    ]
    if not targets:
        return {
            "physical_cleanup": "already_absent",
            "scope": "direct_nas_targets",
            "target_cleanup_tasks": [],
        }

    waiting = False
    cleanup_complete = True
    cleanup_failures: list[dict[str, Any]] = []
    retained_resources: list[str] = []
    task_payloads: list[dict[str, Any]] = []
    for target in targets:
        child = create_direct_nas_target_cleanup_task(
            repository=repository,
            target_id=target.id,
            triggered_by_task=repository_task.task,
            requested_by=_repository_task_user(repository_task),
            force=repository_task.force,
            dispatch=False,
        )
        run_repository_cleanup_task(repository_task_id=child.id)
        child.task.refresh_from_db()
        payload = repository_cleanup_task_payload(child)
        task_payloads.append(payload)
        if child.task.status in ACTIVE_TASK_STATUSES:
            waiting = True
            continue
        if child.task.status != Task.Status.SUCCESS:
            raise ValidationError(
                child.task.error_message
                or f"Direct NAS target {target.target_key} cleanup failed."
            )
        child_result = (
            dict(child.task.result_payload or {})
            if isinstance(child.task.result_payload, dict)
            else {}
        )
        if not bool(child_result.get("cleanup_complete", True)):
            cleanup_complete = False
            cleanup_failures.extend(child_result.get("cleanup_failures") or [])
            retained_resources.extend(child_result.get("retained_resources") or [])

    if waiting:
        return {
            "waiting": True,
            "scope": "direct_nas_targets",
            "target_cleanup_tasks": task_payloads,
        }
    return {
        "physical_cleanup": (
            "deleted" if cleanup_complete else "partially_deleted"
        ),
        "scope": "direct_nas_targets",
        "target_cleanup_tasks": task_payloads,
        "cleanup_complete": cleanup_complete,
        "cleanup_failures": cleanup_failures,
        "retained_resources": retained_resources,
    }


def _apply_cleanup_success(
    *,
    repository_task_id: int,
    cleanup_complete: bool,
    cleanup_result: str | None = None,
) -> None:
    with transaction.atomic():
        repository_task = RepositoryTask.objects.select_for_update().select_related(
            "task", "repository"
        ).get(pk=repository_task_id)
        target = (
            RepositoryExecutionTarget.objects.select_for_update().get(
                pk=repository_task.execution_target_id
            )
            if repository_task.execution_target_id
            else None
        )
        repository = repository_task.repository
        if repository_task.operation_type == RepositoryTask.OperationType.CLEANUP_TARGET:
            if target is not None:
                target.is_active = False
                target.save(update_fields=["is_active", "updated_at"])
                RepositoryUsageShard.objects.filter(
                    organization_id=repository.organization_id,
                    repository_id=repository.id,
                    node_id=target.owner_node_id,
                    repository_subdir=target.repository_subdir,
                ).update(is_active=False)
        else:
            _tombstone_repository(
                repository=repository,
                cleanup_complete=cleanup_complete,
                cleanup_result=cleanup_result,
            )


def _finalize_cleanup_task(*, repository_task_id: int) -> None:
    with transaction.atomic():
        repository_task = RepositoryTask.objects.select_for_update().select_related(
            "task", "repository"
        ).get(pk=repository_task_id)
        task = repository_task.task
        target = (
            RepositoryExecutionTarget.objects.select_for_update().get(
                pk=repository_task.execution_target_id
            )
            if repository_task.execution_target_id
            else None
        )
        if target and target.active_task_id == task.id and task.status in {
            Task.Status.SUCCESS,
            Task.Status.FAILED,
            Task.Status.CANCELLED,
            Task.Status.TIMEOUT,
        }:
            target.active_task = None
            if task.status == Task.Status.SUCCESS:
                target.is_active = False
            target.save(update_fields=["active_task", "is_active", "updated_at"])

        repository = repository_task.repository
        task_result = task.result_payload if isinstance(task.result_payload, dict) else {}
        if task.status == Task.Status.SUCCESS:
            audit_result = (
                AuditResult.PARTIAL
                if repository_task.force or task_result.get("cleanup_complete") is False
                else AuditResult.SUCCESS
            )
        else:
            if repository_task.operation_type == RepositoryTask.OperationType.CLEANUP_REPOSITORY:
                repository.status = Repository.Status.REMOVE_FAILED
                repository.save(update_fields=["status", "updated_at"])
            audit_result = AuditResult.FAILURE
        _write_cleanup_audit(
            repository_task=repository_task,
            user=_repository_task_user(repository_task),
            result=audit_result,
            details=(
                f"{repository_task.operation_type} finished with task status {task.status}; "
                f"physical_cleanup={task_result.get('physical_cleanup') or 'unknown'}"
            ),
        )
        triggered_task = (
            Task.objects.filter(pk=repository_task.triggered_by_task_id).first()
            if repository_task.triggered_by_task_id
            else None
        )
        if (
            triggered_task is not None
            and triggered_task.task_type == Task.Type.SOURCE_UNREGISTER
            and triggered_task.status in ACTIVE_TASK_STATUSES
            and task.status
            in {
                Task.Status.SUCCESS,
                Task.Status.FAILED,
                Task.Status.CANCELLED,
                Task.Status.TIMEOUT,
            }
        ):
            triggered_task_id = int(triggered_task.id)

            def queue_source_unregister_advance() -> None:
                from apps.source.tasks.source_unregister import (
                    queue_source_unregister_task,
                )

                queue_source_unregister_task(
                    task_id=triggered_task_id,
                    countdown_seconds=1,
                )

            transaction.on_commit(queue_source_unregister_advance)
        elif (
            triggered_task is not None
            and triggered_task.task_type == Task.Type.REPOSITORY_OPERATION
            and triggered_task.status in ACTIVE_TASK_STATUSES
            and task.status
            in {
                Task.Status.SUCCESS,
                Task.Status.FAILED,
                Task.Status.CANCELLED,
                Task.Status.TIMEOUT,
            }
        ):
            parent = RepositoryTask.objects.filter(task=triggered_task).first()
            if parent is not None:
                parent_id = int(parent.id)

                def queue_repository_parent_advance() -> None:
                    from apps.storage.tasks import execute_repository_operation

                    execute_repository_operation.apply_async(
                        kwargs={"repository_task_id": parent_id},
                        countdown=1,
                    )

                transaction.on_commit(queue_repository_parent_advance)


def _tombstone_repository(
    *,
    repository: Repository,
    cleanup_complete: bool,
    cleanup_result: str | None = None,
) -> None:
    credential_id = repository.credential_id
    repository.status = Repository.Status.REMOVED
    repository.health = Repository.Health.OFFLINE
    repository.removed_at = timezone.now()
    repository.cleanup_result = cleanup_result or (
        Repository.CleanupResult.DELETED
        if cleanup_complete
        else Repository.CleanupResult.FORCE_SKIPPED
    )
    repository.config = sanitize_repository_config(repository.config)
    repository.credential_id = None
    repository.save(
        update_fields=[
            "status",
            "health",
            "removed_at",
            "cleanup_result",
            "config",
            "credential_id",
            "updated_at",
        ]
    )
    RepositoryExecutionTarget.objects.filter(repository=repository).update(
        is_active=False, active_task=None
    )
    RepositoryUsageShard.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    ).update(is_active=False)
    if credential_id and not Repository.objects.filter(
        organization_id=repository.organization_id,
        credential_id=credential_id,
    ).exists():
        Credential.objects.filter(
            organization_id=repository.organization_id,
            id=credential_id,
        ).delete()
    purge_model = apps.get_model("source", "BackupSourceRepositoryPurgePending")
    purge_model.objects.filter(
        organization_id=repository.organization_id,
        repository_id=repository.id,
    ).delete()


def _set_cleanup_step(task: Task, step_name: str, status: str, progress: int) -> None:
    from apps.storage.services.internal.repository_operations import set_task_step

    task.refresh_from_db(fields=["current_step", "progress"])
    set_task_step(task, step_name, status=status, progress=progress)


def _cleanup_target_payloads(
    *,
    repository: Repository,
    targets: list[RepositoryExecutionTarget],
) -> list[dict[str, Any]]:
    nodes = {
        node.id: node
        for node in Node.objects.filter(
            organization_id=repository.organization_id,
            id__in=[target.owner_node_id for target in targets if target.owner_node_id],
            is_deleted=False,
        )
    }
    return [
        {
            "id": target.id,
            "target_key": target.target_key,
            "repository_subdir": target.repository_subdir,
            "owner_type": target.owner_type,
            "owner_node_id": target.owner_node_id,
            "owner_node_name": nodes[target.owner_node_id].name if target.owner_node_id in nodes else "",
            "owner_online": (
                nodes[target.owner_node_id].availability == Node.Availability.ONLINE
                if target.owner_node_id in nodes
                else target.owner_type == RepositoryExecutionTarget.OwnerType.CONTROLLER
            ),
            "is_active": target.is_active,
        }
        for target in targets
    ]


def _dispatch_cleanup_task(repository_task_id: int) -> None:
    from apps.storage.tasks import execute_repository_operation

    execute_repository_operation.apply_async(kwargs={"repository_task_id": repository_task_id})


def _remove_controller_repository_state(repository_id: int) -> None:
    base_dir = Path(os.environ.get("HFL_KOPIA_CONFIG_DIR", "/tmp/hfl-kopia")) / str(repository_id)
    shutil.rmtree(base_dir, ignore_errors=True)


def _cleanup_error_code(exc: Exception) -> str:
    if isinstance(exc, RepositoryCleanupBlocked):
        return "REPOSITORY_CLEANUP_BLOCKED"
    if isinstance(exc, TimeoutError):
        return "REPOSITORY_CLEANUP_TIMEOUT"
    if isinstance(exc, S3ClientError):
        return "REPOSITORY_S3_CLEANUP_FAILED"
    if isinstance(exc, ValidationError):
        return "REPOSITORY_CLEANUP_INVALID"
    return "REPOSITORY_CLEANUP_FAILED"


def _cleanup_exception_message(exc: Exception) -> str:
    if isinstance(exc, RepositoryCleanupBlocked):
        details = [
            str(item.get("detail") or item.get("code") or "")
            for item in exc.preflight.get("blockers", [])
            if isinstance(item, dict)
        ]
        return "; ".join(item for item in details if item) or "Repository cleanup is blocked."
    if isinstance(exc, ValidationError):
        messages = list(getattr(exc, "messages", []) or [])
        if messages:
            return "; ".join(str(item) for item in messages)
    return str(exc)


def _is_unmounted_nas_cleanup(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("physical_cleanup") != "skipped_unmounted":
        return False
    failures = result.get("cleanup_failures")
    if not isinstance(failures, list) or not failures:
        return False
    return all(
        isinstance(item, dict) and item.get("code") == "NAS_NOT_MOUNTED"
        for item in failures
    )


def _retained_repository_resources(
    repository_task: RepositoryTask,
) -> list[str]:
    """Return stable, non-secret residue identifiers for Force Cleanup."""
    repository = repository_task.repository
    target = repository_task.execution_target
    if target is not None:
        return [f"repository_target:{target.target_key}"]
    if repository.repo_type == Repository.Type.S3:
        return [f"s3_prefix:repository-{repository.id}"]
    return [f"repository:{repository.id}"]


def _repository_secret_values(repository: Repository) -> list[str]:
    try:
        secrets_payload = resolve_repository_secrets(repository)
    except Exception:
        secrets_payload = {}
    return secret_values_for_scrub(repository, secrets_payload)


def _owner_identity(node_id: int | None) -> str:
    return f"hfl-maintenance@node-{node_id}" if node_id else "hfl-maintenance@controller"


def _repository_task_user(repository_task: RepositoryTask):
    if not repository_task.requested_by_id:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=repository_task.requested_by_id).first()


def _write_cleanup_audit(
    *,
    repository_task: RepositoryTask,
    user,
    result: str,
    details: str,
) -> None:
    organization = Organization.objects.filter(pk=repository_task.repository.organization_id).first()
    if organization is None:
        return
    task = repository_task.task
    write_audit_log(
        organization=organization,
        user=user,
        action=AuditAction.DELETE,
        resource_type=AuditResourceType.REPOSITORY,
        resource_id=str(repository_task.repository_id),
        resource_name=repository_task.repository.name,
        correlation_id=str(task.task_uuid),
        details=details,
        result=result,
        error_code=str(task.error_code or ""),
        error_message=str(task.error_message or ""),
        metadata={
            "task_uuid": str(task.task_uuid),
            "operation_type": repository_task.operation_type,
            "force": repository_task.force,
            "target_id": repository_task.execution_target_id,
            "triggered_by_task_uuid": (
                str(repository_task.triggered_by_task.task_uuid)
                if repository_task.triggered_by_task_id
                else None
            ),
        },
    )


__all__ = [
    "RepositoryCleanupBlocked",
    "create_direct_nas_target_cleanup_task",
    "create_repository_cleanup_task",
    "direct_nas_cleanup_target_ids",
    "repository_cleanup_preflight",
    "repository_active_task_blockers",
    "repository_cleanup_task_payload",
    "run_repository_cleanup_task",
]
