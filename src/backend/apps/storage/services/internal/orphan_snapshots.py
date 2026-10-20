"""Durable, fail-closed discovery and delayed cleanup of unmanaged snapshots."""

from contextlib import nullcontext
from datetime import timedelta
import logging
import os
from uuid import UUID

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.node.models import Node, NodeTask
from apps.node.services.interface import run_agent_task_async
from apps.protection.models import BackupSourceSnapshotDirectory, SnapshotUsageLease
from apps.storage.repositories.models import (
    Repository, RepositoryOrphanSnapshot, RepositoryTask,
    RepositoryExecutionTarget,
)
from apps.storage.services.internal.kopia_cli import delete_s3_snapshots
from apps.storage.services.internal.orphan_snapshot_fence import backup_blocker
from apps.storage.services.internal.orphan_reconciliation_errors import (
    OrphanReconciliationError, classify_reconciliation_error,
)
from apps.storage.services.internal.orphan_delete_execution import (
    controller_delete_execution, controller_execution_pending,
    controller_result_registration, recover_controller_deletions,
)
from apps.storage.services.internal.repository_operations import (
    finalize_repository_operation, repository_execution_target_has_owned_location,
    set_task_step,
)
from apps.storage.services.internal.snapshot_inventory import (
    controller_snapshot_inventory, parse_snapshot_inventory,
)
from apps.task.models import Task, TaskEvent, TaskStep
from apps.task.signals import task_updated
from apps.task.services.interface import append_task_event, create_task, start_task


logger = logging.getLogger(__name__)


def managed_ids(repository):
    # Failed/partial results still own their physical snapshots.
    rows = BackupSourceSnapshotDirectory.objects.filter(
        organization_id=repository.organization_id, repository_id=repository.id,
    ).exclude(
        kopia_snapshot_id__isnull=True,
    )
    protected_snapshots = SnapshotUsageLease.objects.filter(
        organization_id=repository.organization_id,
    ).values("snapshot_id")
    from django.db.models import Q
    return set(rows.filter(
        ~Q(status=BackupSourceSnapshotDirectory.Status.DELETED)
        | Q(source_snapshot_id__in=protected_snapshots),
    ).values_list("kopia_snapshot_id", flat=True))


def operation_task(repository, operation_id):
    if not operation_id:
        return None
    try:
        task_uuid = UUID(operation_id[:36])
    except (ValueError, TypeError):
        return None
    return Task.objects.filter(
        organization_id=repository.organization_id, task_uuid=task_uuid,
        task_type=Task.Type.BACKUP, request_payload__repository_id=repository.id,
    ).first()


def pending_operation(repository, operation_id):
    task = operation_task(repository, operation_id)
    if task is None:
        return False
    if task.status in {Task.Status.PENDING, Task.Status.WAITING, Task.Status.BLOCKED, Task.Status.RUNNING}:
        return True
    # Existing directory operations may adopt a late result or reuse on retry.
    # Reconcile/restore these through the backup pipeline, never destructive GC.
    return BackupSourceSnapshotDirectory.objects.filter(
        source_snapshot__task_uuid=task.task_uuid,
    ).exclude(status=BackupSourceSnapshotDirectory.Status.DELETED).exists()


def _operation_index(repository, inventory):
    uuids = set()
    for item in inventory:
        try:
            uuids.add(UUID(item["operation_id"][:36]))
        except (ValueError, TypeError):
            pass
    tasks = {
        str(task.task_uuid): task for task in Task.objects.filter(
            organization_id=repository.organization_id, task_type=Task.Type.BACKUP,
            task_uuid__in=uuids, request_payload__repository_id=repository.id,
        )
    }
    pending = {
        str(value) for value in BackupSourceSnapshotDirectory.objects.filter(
            organization_id=repository.organization_id,
            source_snapshot__task_uuid__in=uuids,
        ).exclude(status=BackupSourceSnapshotDirectory.Status.DELETED).values_list(
            "source_snapshot__task_uuid", flat=True,
        )
    }
    pending.update(
        key for key, task in tasks.items()
        if task.status in {Task.Status.PENDING, Task.Status.WAITING, Task.Status.BLOCKED, Task.Status.RUNNING}
    )
    return tasks, pending


def _source_info(item, task):
    source = dict(item["source"])
    payload = (task.request_payload or {}) if task else {}
    source.update({
        "name": (payload.get("source_orphan_display_name") or task.display_name.removeprefix("Backup ")) if task else "Unlinked HFL backup source",
        "source_type": payload.get("source_type", ""),
        "source_ref_id": payload.get("source_ref_id"),
        "linked": task is not None,
    })
    return source


def _record(rt, candidate, outcome, reason=""):
    # Unique per-task/candidate event: retries cannot duplicate or change history.
    metadata = {
        "event_type": "orphan_snapshot_result", "outcome": outcome,
        "snapshot_id": candidate.snapshot_id, "source": candidate.source,
        "snapshot_created_at": (
            candidate.source.get("snapshot_created_at")
            if isinstance(candidate.source, dict) else None
        ),
        "first_seen_at": candidate.first_seen_at.isoformat(), "reason": reason,
    }
    if rt.task.events.filter(
        metadata__event_type="orphan_snapshot_result",
        metadata__snapshot_id=candidate.snapshot_id,
    ).exists():
        return
    append_task_event(
        task=rt.task,
        level=TaskEvent.Level.INFO, message=f"Orphan snapshot {outcome}",
        metadata=metadata,
    )


def _remote(rt, operation, snapshot_id=""):
    """Persist child dispatch under a row lock; never redispatch unknown execution."""
    from apps.storage.services.internal.repository_access import repository_payload_for_node

    identity = f"{rt.task.task_uuid}:{operation}:{snapshot_id}"
    if rt.task.retry_count:
        identity += f":retry-{rt.task.retry_count}"
    with transaction.atomic():
        RepositoryTask.objects.select_for_update().get(pk=rt.pk)
        child = NodeTask.objects.filter(
            parent_task=rt.task, correlation_id=identity,
        ).first()
        if child is not None and (
            child.organization_id != rt.repository.organization_id
            or child.node_id != rt.owner_node_id
            or child.kind != "repository.operation"
            or child.correlation_type != "repository_orphan"
            or (child.payload or {}).get("operation_type") != operation
        ):
            raise OrphanReconciliationError(
                "ORPHAN_AGENT_IDENTITY_MISMATCH",
                "The stored Agent operation does not match this repository task. Confirm the original execution before recovery; no replacement will be dispatched.",
                execution_unknown=True,
            )
        if child is None:
            node = _owner_node(rt)
            payload = repository_payload_for_node(
                repository=rt.repository, node=node,
                source_type="proxy" if node.role == "proxy" else "agent",
                source_ref_id=node.id,
            )
            payload["repository_subdir"] = rt.execution_target.repository_subdir
            handle = run_agent_task_async(
                organization_id=rt.repository.organization_id, node_id=node.id,
                kind="repository.operation",
                payload={
                    "operation_type": operation, "repository": payload,
                    "snapshot_ids": [snapshot_id] if snapshot_id else [],
                },
                persisted_payload={"repository_id": rt.repository_id, "operation_type": operation},
                parent_task=rt.task, correlation_type="repository_orphan", correlation_id=identity,
            )
            child = handle.task
    if child.status == NodeTask.Status.SUCCESS:
        return child.result or {}
    if child.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        return None
    if child.status in {NodeTask.Status.TIMEOUT, NodeTask.Status.CANCELED}:
        # Hold the persistent repository gate until a confirmed late result.
        return None
    if operation == "snapshot.orphan_delete" and (child.result or {}).get("execution_complete") is True:
        return {"deletion_failed": True}
    if child.accepted_at:
        return None
    raise OrphanReconciliationError(
        "ORPHAN_AGENT_OPERATION_FAILED",
        "The Agent rejected the snapshot operation before execution. Check its availability and retry.",
    )


def _owner_node(rt):
    node = Node.objects.filter(
        pk=rt.owner_node_id, organization_id=rt.repository.organization_id,
    ).first()
    if node is None:
        raise OrphanReconciliationError(
            "ORPHAN_OWNER_MISSING",
            "The repository owner is unavailable. Restore or reassign the owner before manually retrying.",
        )
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    capabilities = (
        inventory.get("capabilities", metadata.get("capabilities", []))
        if isinstance(inventory, dict) else metadata.get("capabilities", [])
    )
    if not isinstance(capabilities, list) or "orphan_snapshot_reconcile_v1" not in capabilities:
        raise OrphanReconciliationError(
            "ORPHAN_CAPABILITY_MISSING",
            "The owner must advertise orphan_snapshot_reconcile_v1. Upgrade or reconnect the Agent, then manually retry reconciliation.",
        )
    return node


def _step(rt, name, status, progress):
    step = rt.task.steps.filter(step_name=name).first()
    if step is None or step.status == status or step.status in {
        TaskStep.Status.SUCCESS, TaskStep.Status.SKIPPED,
    }:
        return
    set_task_step(rt.task, name, status=status, progress=progress)


def run_orphan_reconciliation(repository_task_id):
    rt = RepositoryTask.objects.select_related(
        "task", "repository", "execution_target",
    ).get(pk=repository_task_id)
    if rt.task.status in {Task.Status.SUCCESS, Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.TIMEOUT}:
        return {"status": rt.task.status}
    if (
        rt.task.status == Task.Status.PENDING
        and rt.due_at and rt.due_at > timezone.now()
    ):
        return {"status": "retry_scheduled"}
    if (
        rt.owner_type == "controller"
        and rt.task.error_code == "ORPHAN_EXECUTION_CONFIRMING"
        and rt.due_at and rt.due_at > timezone.now()
    ):
        return {"status": "recovering"}
    if rt.task.status == Task.Status.PENDING:
        start_task(task_uuid=rt.task.task_uuid, organization_id=rt.repository.organization_id)
    _step(rt, "prepare_repository_operation", TaskStep.Status.RUNNING, 5)
    scanned = None
    stage = "prepare_repository_operation"
    controller = rt.owner_type == "controller"
    try:
        _step(rt, stage, TaskStep.Status.SUCCESS, 10)
        stage = "verify_repository_owner"
        _step(rt, stage, TaskStep.Status.RUNNING, 15)
        if not repository_execution_target_has_owned_location(rt.execution_target):
            raise OrphanReconciliationError(
                "ORPHAN_OWNERSHIP_UNVERIFIED",
                "Verify the physical repository ownership before manually retrying reconciliation.",
            )
        blocker = backup_blocker(rt.repository)
        if blocker:
            raise OrphanReconciliationError("ORPHAN_BACKUP_ACTIVE", blocker)
        _step(rt, stage, TaskStep.Status.SUCCESS, 20)
        stage = "run_repository_operation"
        _step(rt, stage, TaskStep.Status.RUNNING, 25)
        if controller:
            if controller_execution_pending(rt):
                return _wait(rt, "Automatically confirming the previous deletion executor.")
            inventory = controller_snapshot_inventory(rt.repository)
            if recover_controller_deletions(rt, inventory):
                return _finish(
                    rt, False, "Controller execution stopped and snapshot states were recovered; this interrupted cycle ended without redispatching deletion.",
                    scanned=len(inventory), error_code="ORPHAN_DELETE_RECOVERED",
                )
        else:
            result = _remote(rt, "snapshot.inventory")
            if result is None:
                return _wait(rt)
            if result.get("inventory_complete") is not True:
                raise ValueError("Agent snapshot inventory is incomplete.")
            inventory = parse_snapshot_inventory(result.get("manifests"))
        scanned = len(inventory)
        now = timezone.now()
        protected = managed_ids(rt.repository)
        operation_tasks, pending_operations = _operation_index(rt.repository, inventory)
        candidates = {
            candidate.snapshot_id: candidate
            for candidate in RepositoryOrphanSnapshot.objects.filter(
                target=rt.execution_target, status__in=["candidate", "deleting"],
            )
        }
        present = {item["snapshot_id"] for item in inventory}
        for candidate in RepositoryOrphanSnapshot.objects.filter(
            target=rt.execution_target, status__in=["candidate", "deleting"],
        ).exclude(snapshot_id__in=present):
            with controller_result_registration(rt, None) if controller else nullcontext():
                if candidate.status == "deleting":
                    # Absence alone cannot prove the old deletion process has stopped.
                    return _wait(rt, "Deletion execution requires confirmation.")
                candidate.status = "absent"
                candidate.save(update_fields=["status"])
                _record(rt, candidate, "absent")
        for item in inventory:
            execution = None
            sid = item["snapshot_id"]
            candidate = candidates.get(sid)
            created_at = item["source"].get("snapshot_created_at")
            if candidate and created_at:
                source = candidate.source if isinstance(candidate.source, dict) else {}
                if source.get("snapshot_created_at") != created_at:
                    candidate.source = {**source, "snapshot_created_at": created_at}
                    candidate.save(update_fields=["source"])
            operation_uuid = item["operation_id"][:36]
            if sid in protected or operation_uuid in pending_operations:
                if candidate and candidate.status != "deleting":
                    candidate.status = "managed"
                    candidate.save(update_fields=["status"])
                    _record(rt, candidate, "managed")
                continue
            if candidate is None or candidate.status in {"managed", "absent", "deleted"}:
                candidate, _ = RepositoryOrphanSnapshot.objects.update_or_create(
                    target=rt.execution_target, snapshot_id=sid,
                    defaults={
                        "source": _source_info(item, operation_tasks.get(operation_uuid)),
                        "operation_id": item["operation_id"],
                        "first_seen_at": now, "last_seen_at": now,
                        "status": "candidate", "deleted_at": None, "last_error": "",
                    },
                )
                _record(rt, candidate, "discovered")
                continue
            candidate.last_seen_at = now
            candidate.save(update_fields=["last_seen_at"])
            if (
                now - candidate.first_seen_at < timedelta(hours=24)
                or candidate.delete_not_before and candidate.delete_not_before > now
            ):
                _record(rt, candidate, "grace")
                continue
            if os.getenv("STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED", "true").lower() not in {"true", "1", "yes", "on"}:
                _record(rt, candidate, "deferred", "Automatic orphan deletion is disabled.")
                continue
            # Never act on a candidate discovered by this same logical task.
            if TaskEvent.objects.filter(
                task_id__in=[rt.task_id, rt.task.replaces_task_id],
                metadata__snapshot_id=sid, metadata__outcome="discovered",
            ).exists():
                continue
            if controller and candidate.status == "deleting":
                return _wait(rt, "Automatically confirming the previous Controller deletion.")
            with transaction.atomic():
                Repository.objects.select_for_update().get(pk=rt.repository_id)
                candidate = RepositoryOrphanSnapshot.objects.select_for_update().get(pk=candidate.pk)
                if controller and candidate.status == "deleting":
                    return _wait(rt, "Concurrent deletion requires execution confirmation.")
                if backup_blocker(rt.repository) or sid in managed_ids(rt.repository):
                    _record(rt, candidate, "deferred", "Backup or management relationship changed.")
                    continue
                if not controller:
                    candidate.status = "deleting"
                    candidate.save(update_fields=["status"])
            if controller:
                # Fresh complete listing and physical ownership verification before delete.
                fresh = {entry["snapshot_id"] for entry in controller_snapshot_inventory(rt.repository)}
                if sid not in fresh:
                    outcome = "absent"
                else:
                    with controller_delete_execution(rt, candidate) as execution:
                        results = delete_s3_snapshots(rt.repository, snapshot_ids=[sid]).get("results", [])
                    from apps.protection.services.kopia_snapshot_delete import classify_kopia_snapshot_delete_results
                    deleted, absent, failures = classify_kopia_snapshot_delete_results(results)
                    if failures:
                        if execution.attempt is not None:
                            # Process termination is not the business outcome.
                            # Re-observe after a timeout/nonzero exit before
                            # releasing the repository's write fence.
                            recover_controller_deletions(
                                rt, controller_snapshot_inventory(rt.repository),
                                reason=(
                                    "Snapshot deletion timed out; its executor stopped automatically."
                                    if execution.outcome == "timeout"
                                    else "Physical snapshot deletion failed; its executor stopped."
                                ),
                            )
                            continue
                        with controller_result_registration(rt, execution):
                            candidate.status = "candidate"
                            candidate.delete_not_before = timezone.now() + timedelta(hours=24)
                            candidate.last_error = (
                                "Snapshot deletion timed out; its executor stopped automatically."
                                if execution.outcome == "timeout"
                                else "Physical snapshot deletion failed."
                            )
                            candidate.save(update_fields=["status", "last_error", "delete_not_before"])
                            _record(rt, candidate, "failed", candidate.last_error)
                        continue
                    if sid not in deleted | absent:
                        raise ValueError("Physical snapshot deletion completion is unknown.")
                    outcome = "deleted" if sid in deleted else "absent"
            else:
                result = _remote(rt, "snapshot.orphan_delete", sid)
                if result is None:
                    return _wait(rt)
                if result.get("deletion_failed"):
                    candidate.status = "candidate"
                    candidate.last_error = "Agent confirmed snapshot deletion failure."
                    candidate.save(update_fields=["status", "last_error"])
                    _record(rt, candidate, "failed", candidate.last_error)
                    continue
                if result.get("snapshot_id") != sid or not isinstance(result.get("deleted"), bool):
                    return _wait(rt, "Agent deletion identity or completion is unconfirmed.")
                outcome = "deleted" if result.get("deleted") is True else "absent"
            with controller_result_registration(rt, execution) if controller else transaction.atomic():
                candidate.status = outcome
                candidate.deleted_at = now if outcome == "deleted" else None
                candidate.save(update_fields=["status", "deleted_at"])
                _record(rt, candidate, outcome)
        return _finish(rt, True, scanned=scanned)
    except Exception as error:
        diagnostic = classify_reconciliation_error(error, stage)
        append_task_event(
            task=rt.task, level=TaskEvent.Level.ERROR,
            message=diagnostic["message"],
            step=rt.task.steps.filter(step_name=stage).first(),
            metadata={"event_type": "orphan_snapshot_error", **diagnostic},
        )
        # A Controller interruption may leave the child process running. Preserve the gate.
        if diagnostic["execution_unknown"]:
            return _wait(rt, diagnostic["message"])
        if RepositoryOrphanSnapshot.objects.filter(target=rt.execution_target, status="deleting").exists():
            return _wait(rt, "Deletion completion requires confirmation.")
        return _finish(
            rt, False, diagnostic["message"], scanned=scanned,
            error_code=diagnostic["error_code"],
            diagnostic=diagnostic,
        )


@transaction.atomic
def _wait(rt, reason="Waiting for confirmed Agent result."):
    if rt.owner_type == "controller":
        # Keep recovery automatic, bounded by the independent executor. A
        # missing/expired cache lock never proves that execution has stopped.
        RepositoryTask.objects.select_for_update().get(pk=rt.pk)
        rt.task = Task.objects.select_for_update().get(pk=rt.task_id)
        if rt.task.status in {
            Task.Status.SUCCESS, Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.TIMEOUT,
        }:
            return {"status": rt.task.status}
        now = timezone.now()
        payload = rt.task.result_payload if isinstance(rt.task.result_payload, dict) else {}
        recovery = payload.get("orphan_recovery") or {}
        checks = int(recovery.get("checks", 0)) + 1
        next_check = now + timedelta(seconds=min(900, 60 * 2 ** min(checks - 1, 4)))
        recovery = {
            "first_checked_at": recovery.get("first_checked_at", now.isoformat()),
            "last_checked_at": now.isoformat(), "next_check_at": next_check.isoformat(),
            "checks": checks, "phase": "confirming_execution",
        }
        updated = Task.objects.filter(
            pk=rt.task_id,
            status__in=[Task.Status.PENDING, Task.Status.RUNNING, Task.Status.WAITING],
        ).update(
            status=Task.Status.RUNNING, error_code="ORPHAN_EXECUTION_CONFIRMING",
            error_message="Automatically confirming snapshot cleanup execution before recovery.",
            result_payload={**payload, "orphan_recovery": recovery},
            updated_at=now,
        )
        if not updated:
            return {"status": Task.objects.get(pk=rt.task_id).status}
        RepositoryTask.objects.filter(pk=rt.pk).update(due_at=next_check)
        if checks == 4:
            append_task_event(
                task=rt.task, level=TaskEvent.Level.WARN,
                message="Snapshot cleanup recovery is retrying automatically.",
                metadata={"event_type": "orphan_snapshot_recovery", **recovery},
            )
        transaction.on_commit(lambda: _notify_recovery(rt.task_id))
        return {"status": "recovering"}
    Task.objects.filter(pk=rt.task_id).update(status=Task.Status.WAITING, error_message=reason)
    return {"status": "waiting", "reason": reason}


def _notify_recovery(task_id):
    # Reload after commit so a terminal result cannot be followed by a stale
    # "running" projection from a delayed recovery notification.
    task = Task.objects.filter(pk=task_id).first()
    if task is None:
        return
    task_updated.send_robust(
        sender=Task, task_uuid=str(task.task_uuid),
        organization_id=task.organization_id, status=task.status,
        progress=float(task.progress),
    )


@transaction.atomic
def _finish(rt, succeeded, reason="", scanned=None, *, error_code="", diagnostic=None):
    # Match admission's Repository-before-Target lock order. The failed attempt
    # and its replacement hand the gate over without exposing an unlocked gap.
    Repository.objects.select_for_update().get(pk=rt.repository_id)
    RepositoryTask.objects.select_for_update().get(pk=rt.pk)
    rt.task = Task.objects.select_for_update().get(pk=rt.task_id)
    if rt.task.status in {
        Task.Status.SUCCESS, Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.TIMEOUT,
    }:
        return {"status": rt.task.status}
    target = RepositoryExecutionTarget.objects.select_for_update().get(pk=rt.execution_target_id)
    # Another advance may have committed a newer delete after recovery. Intent
    # creation takes this same Repository lock, so check and finish atomically.
    if rt.owner_type == "controller" and RepositoryOrphanSnapshot.objects.filter(
        target_id=rt.execution_target_id, status="deleting",
    ).exists():
        return _wait(rt, "Automatically confirming the current Controller deletion before finishing.")
    counts = {}
    for row in rt.task.events.filter(
        metadata__event_type="orphan_snapshot_result",
    ).values("metadata__outcome").annotate(total=Count("id")):
        counts[row["metadata__outcome"]] = row["total"]
    if counts.get("failed"):
        succeeded = False
        reason = "Some orphan snapshot deletions failed. They will be checked again in the next scheduled cycle."
        error_code = "ORPHAN_DELETE_FAILED"
    if succeeded:
        _step(rt, "run_repository_operation", TaskStep.Status.SUCCESS, 80)
        _step(rt, "finalize_repository_operation", TaskStep.Status.SUCCESS, 100)
    else:
        # Fail the active step, never leave a completed task with queued successors.
        for step in rt.task.steps.order_by("step_index"):
            if step.status == TaskStep.Status.RUNNING:
                _step(rt, step.step_name, TaskStep.Status.FAILED, int(rt.task.progress))
            elif step.status == TaskStep.Status.PENDING:
                step.status = TaskStep.Status.SKIPPED
                step.save(update_fields=["status"])
                append_task_event(
                    task=rt.task, step=step, message=f"Step {step.step_name} skipped",
                )
    summary = {"scanned": scanned, "inventory_available": scanned is not None, **counts}
    result = {"orphan_snapshot_summary": summary}
    if isinstance(rt.task.result_payload, dict) and rt.task.result_payload.get("orphan_recovery"):
        result["orphan_recovery"] = {
            **rt.task.result_payload["orphan_recovery"],
            "phase": "finished", "finished_at": timezone.now().isoformat(),
        }
    if diagnostic:
        result["orphan_snapshot_error"] = diagnostic
    append_task_event(
        task=rt.task, message="Orphan snapshot reconciliation summary",
        metadata={"event_type": "orphan_snapshot_summary", "summary": summary},
    )
    # Retry failed inventory checks only. Never redispatch uncertain remote work
    # or repeat deletion after a missing/ambiguous completion.
    retry_scan = (
        not succeeded and rt.task.retry_count < 1
        and rt.task.recovery_attempt == 0 and rt.task.replaces_task_id is None
        and target.active_task_id == rt.task_id
        and not counts.get("failed") and not counts.get("deleted")
        and error_code != "ORPHAN_DELETE_RECOVERED"
    )
    if retry_scan:
        for child in rt.task.node_tasks.filter(correlation_type="repository_orphan"):
            if child.status in {"pending", "running", "timeout", "canceled"} or (
                child.status == "failed" and child.accepted_at
                and (child.result or {}).get("execution_complete") is not True
            ):
                return _wait(rt, "Agent execution requires confirmation before retry.")
    completed = finalize_repository_operation(
        repository_task_id=rt.id, succeeded=succeeded, error_message=reason,
        error_code=error_code, result_payload=result,
        advance_reconcile_schedule=not retry_scan,
    )
    if retry_scan and completed.status == Task.Status.FAILED:
        # Reuse the established replacement-task contract; never reset the
        # failed attempt's status, timestamps, steps or event history.
        retried = create_task(
            organization_id=completed.organization_id,
            task_type=completed.task_type,
            display_name=completed.display_name,
            trigger_type=Task.TriggerType.RETRY,
            normalize_trigger_type=False,
            request_payload=completed.request_payload,
            resources=list(completed.resources.values(
                "resource_type", "resource_subtype", "resource_id", "is_primary",
            )),
            steps=list(completed.steps.exclude(
                step_name="refresh_repository_usage",
            ).order_by("step_index").values_list("step_name", flat=True)),
            replaces_task=completed,
        )
        retry = RepositoryTask.objects.create(
            task=retried, repository_id=rt.repository_id,
            execution_target_id=rt.execution_target_id,
            operation_type=rt.operation_type,
            owner_type=rt.owner_type, owner_node_id=rt.owner_node_id,
            owner_identity=rt.owner_identity,
            requested_by_id=rt.requested_by_id,
            due_at=timezone.now() + timedelta(minutes=1),
        )
        target.active_task = retried
        target.save(update_fields=["active_task", "updated_at"])
        transaction.on_commit(lambda: _enqueue_retry(retry.id))
        return {"status": "retry_scheduled"}
    return {"status": "success" if succeeded else "failed"}


def _enqueue_retry(repository_task_id):
    from apps.storage.tasks import execute_repository_operation

    try:
        execute_repository_operation.apply_async(
            kwargs={"repository_task_id": repository_task_id}, countdown=60,
        )
    except Exception:
        # The periodic operation reconciler will redispatch the durable pending task.
        logger.warning("Snapshot reconciliation retry dispatch deferred task_id=%s", repository_task_id)
