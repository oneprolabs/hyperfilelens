"""Durable, fail-closed discovery and delayed cleanup of unmanaged snapshots."""

from datetime import timedelta
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
)
from apps.storage.services.internal.kopia_cli import delete_s3_snapshots
from apps.storage.services.internal.orphan_snapshot_fence import backup_blocker
from apps.storage.services.internal.repository_operations import (
    finalize_repository_operation, repository_execution_target_has_owned_location,
)
from apps.storage.services.internal.snapshot_inventory import (
    controller_snapshot_inventory, parse_snapshot_inventory,
)
from apps.task.models import Task, TaskEvent
from apps.task.services.interface import append_task_event, start_task


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

    node = Node.objects.get(pk=rt.owner_node_id)
    if "orphan_snapshot_reconcile_v1" not in (node.capabilities or []):
        raise ValueError("Repository owner requires orphan_snapshot_reconcile_v1.")
    identity = f"{rt.task.task_uuid}:{operation}:{snapshot_id}"
    with transaction.atomic():
        RepositoryTask.objects.select_for_update().get(pk=rt.pk)
        child = NodeTask.objects.filter(
            organization_id=rt.repository.organization_id, parent_task=rt.task,
            correlation_id=identity,
        ).first()
        if child is None:
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
    raise ValueError("Agent orphan snapshot operation failed.")


def run_orphan_reconciliation(repository_task_id):
    rt = RepositoryTask.objects.select_related(
        "task", "repository", "execution_target",
    ).get(pk=repository_task_id)
    if rt.task.status in {Task.Status.SUCCESS, Task.Status.FAILED, Task.Status.CANCELLED}:
        return {"status": rt.task.status}
    if rt.task.status == Task.Status.PENDING:
        start_task(task_uuid=rt.task.task_uuid, organization_id=rt.repository.organization_id)
    if not repository_execution_target_has_owned_location(rt.execution_target):
        return _finish(rt, False, "Physical repository ownership is not verified.")
    blocker = backup_blocker(rt.repository)
    if blocker:
        return _finish(rt, False, blocker)
    controller = rt.owner_type == "controller"
    try:
        if controller:
            inventory = controller_snapshot_inventory(rt.repository)
        else:
            result = _remote(rt, "snapshot.inventory")
            if result is None:
                return _wait(rt)
            if result.get("inventory_complete") is not True:
                raise ValueError("Agent snapshot inventory is incomplete.")
            inventory = parse_snapshot_inventory(result.get("manifests"))
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
            if candidate.status == "deleting":
                # Absence alone cannot prove the old deletion process has stopped.
                return _wait(rt, "Deletion execution requires confirmation.")
            else:
                candidate.status = "absent"
            candidate.save(update_fields=["status"])
            _record(rt, candidate, "absent")
        for item in inventory:
            sid = item["snapshot_id"]
            candidate = candidates.get(sid)
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
            if now - candidate.first_seen_at < timedelta(hours=24):
                _record(rt, candidate, "grace")
                continue
            if os.getenv("STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED", "true").lower() not in {"true", "1", "yes", "on"}:
                _record(rt, candidate, "deferred", "Automatic orphan deletion is disabled.")
                continue
            # Never act on a candidate discovered by this same logical task.
            if rt.task.events.filter(metadata__snapshot_id=sid, metadata__outcome="discovered").exists():
                continue
            if controller and candidate.status == "deleting":
                return _wait(rt, "Controller delete result unknown; manual execution confirmation required.")
            with transaction.atomic():
                Repository.objects.select_for_update().get(pk=rt.repository_id)
                candidate = RepositoryOrphanSnapshot.objects.select_for_update().get(pk=candidate.pk)
                if controller and candidate.status == "deleting":
                    return _wait(rt, "Concurrent deletion requires execution confirmation.")
                if backup_blocker(rt.repository) or sid in managed_ids(rt.repository):
                    _record(rt, candidate, "deferred", "Backup or management relationship changed.")
                    continue
                candidate.status = "deleting"
                candidate.save(update_fields=["status"])
            if controller:
                # Fresh complete listing and physical ownership verification before delete.
                fresh = {entry["snapshot_id"] for entry in controller_snapshot_inventory(rt.repository)}
                if sid not in fresh:
                    outcome = "absent"
                else:
                    results = delete_s3_snapshots(rt.repository, snapshot_ids=[sid]).get("results", [])
                    from apps.protection.services.kopia_snapshot_delete import classify_kopia_snapshot_delete_results
                    deleted, absent, failures = classify_kopia_snapshot_delete_results(results)
                    if failures:
                        candidate.status = "candidate"
                        candidate.last_error = "Physical snapshot deletion failed."
                        candidate.save(update_fields=["status", "last_error"])
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
            with transaction.atomic():
                candidate.status = outcome
                candidate.deleted_at = now if outcome == "deleted" else None
                candidate.save(update_fields=["status", "deleted_at"])
                _record(rt, candidate, outcome)
        return _finish(rt, True, scanned=len(inventory))
    except Exception:
        # A Controller interruption may leave the child process running. Preserve the gate.
        if RepositoryOrphanSnapshot.objects.filter(target=rt.execution_target, status="deleting").exists():
            return _wait(rt, "Deletion completion requires confirmation.")
        return _finish(rt, False, "Snapshot reconciliation failed; no unconfirmed deletion will be retried.")


def _wait(rt, reason="Waiting for confirmed Agent result."):
    Task.objects.filter(pk=rt.task_id).update(status=Task.Status.WAITING, error_message=reason)
    return {"status": "waiting", "reason": reason}


def _finish(rt, succeeded, reason="", scanned=0):
    counts = {}
    for row in rt.task.events.filter(
        metadata__event_type="orphan_snapshot_result",
    ).values("metadata__outcome").annotate(total=Count("id")):
        counts[row["metadata__outcome"]] = row["total"]
    if counts.get("failed"):
        succeeded = False
        reason = "Some orphan snapshot deletions failed and will be retried."
    summary = {"scanned": scanned, **counts}
    append_task_event(
        task=rt.task, message="Orphan snapshot reconciliation summary",
        metadata={"event_type": "orphan_snapshot_summary", "summary": summary},
    )
    finalize_repository_operation(
        repository_task_id=rt.id, succeeded=succeeded, error_message=reason,
        result_payload={"orphan_snapshot_summary": summary},
    )
    return {"status": "success" if succeeded else "failed"}
