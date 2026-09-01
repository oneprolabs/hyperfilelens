from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.node.models import NodeTask
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.kopia_snapshot_delete import (
    classify_kopia_snapshot_delete_results,
    normalize_kopia_snapshot_id,
)
from apps.protection.services.snapshot_delete import (
    _latest_delete_task,
    create_and_queue_snapshot_delete_task,
    fail_snapshot_delete_task,
)
from apps.protection.services.snapshot_usage import snapshot_is_protected
from apps.task.models import Task
from apps.task.services.interface import complete_task


class Command(BaseCommand):
    help = "Reconcile stuck or failed snapshot deletes. Defaults to a read-only dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply changes; default is dry-run.")
        parser.add_argument(
            "--retry",
            action="store_true",
            help="Queue unresolved deletes after repair. Has effects only together with --apply.",
        )
        parser.add_argument("--source-type")
        parser.add_argument("--source-ref-id", type=int)

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        retry_unresolved = bool(options["retry"])
        snapshots = BackupSourceSnapshot.objects.filter(
            status__in=(
                BackupSourceSnapshot.Status.DELETING,
                BackupSourceSnapshot.Status.DELETE_FAILED,
            )
        )
        if options.get("source_type"):
            snapshots = snapshots.filter(source_type=options["source_type"])
        if options.get("source_ref_id") is not None:
            snapshots = snapshots.filter(source_ref_id=options["source_ref_id"])

        reconciled = 0
        queued = 0
        delete_failed = 0
        unchanged = 0
        for snapshot in snapshots.order_by("id").iterator():
            if snapshot_is_protected(snapshot_id=snapshot.id):
                unchanged += 1
                self.stdout.write(
                    f"snapshot={snapshot.id} task=unknown current={snapshot.status} "
                    "action=unchanged reason=snapshot_in_use"
                )
                continue
            task = _latest_delete_task(
                organization_id=snapshot.organization_id,
                source_snapshot_id=snapshot.id,
            )
            if task is None:
                unchanged += 1
                self.stdout.write(
                    f"snapshot={snapshot.id} task=missing current={snapshot.status} "
                    "action=unchanged reason=no_delete_task"
                )
                continue

            result = task.result_payload if isinstance(task.result_payload, dict) else {}
            if not result.get("results"):
                node_task = NodeTask.objects.filter(
                    organization_id=snapshot.organization_id,
                    correlation_type="protection.snapshot_delete",
                    correlation_id=str(task.task_uuid),
                ).order_by("-created_at").first()
                if node_task is not None and isinstance(node_task.result, dict):
                    result = node_task.result

            items = result.get("results") if isinstance(result.get("results"), list) else []
            deleted_ids, absent_ids, hard_failures = classify_kopia_snapshot_delete_results(
                [item for item in items if isinstance(item, dict)]
            )
            reconciled_ids = deleted_ids | absent_ids
            remaining_rows = list(
                BackupSourceSnapshotDirectory.objects.filter(source_snapshot=snapshot)
                .exclude(status=BackupSourceSnapshotDirectory.Status.DELETED)
                .order_by("id")
            )
            physical_ids = {
                snapshot_id
                for row in remaining_rows
                if (snapshot_id := normalize_kopia_snapshot_id(row.kopia_snapshot_id))
            }
            missing_physical_safe = bool(remaining_rows) and all(
                not normalize_kopia_snapshot_id(row.kopia_snapshot_id)
                and row.status
                in {
                    BackupSourceSnapshotDirectory.Status.FAILED,
                    BackupSourceSnapshotDirectory.Status.CANCELLED,
                }
                for row in remaining_rows
            )
            can_finalize = missing_physical_safe or (
                bool(items)
                and not hard_failures
                and physical_ids.issubset(reconciled_ids)
            )
            if can_finalize:
                action = "finalize_deleted"
                reason = "no_physical_snapshot_ids" if missing_physical_safe else "physical_delete_reconciled"
            else:
                action = "fail_and_retry" if retry_unresolved else "mark_delete_failed"
                reason = "physical_delete_requires_retry"

            self.stdout.write(
                f"snapshot={snapshot.id} task={task.task_uuid} current={snapshot.status} "
                f"action={action} reason={reason} physical_ids={len(physical_ids)} "
                f"reconciled_ids={len(reconciled_ids)} hard_failures={len(hard_failures)}"
            )
            if not apply_changes:
                reconciled += int(can_finalize)
                queued += int(not can_finalize and retry_unresolved)
                delete_failed += int(not can_finalize and not retry_unresolved)
                continue

            if can_finalize:
                if self._finalize_snapshot(
                    snapshot=snapshot,
                    task=task,
                    result=result,
                ):
                    reconciled += 1
                else:
                    unchanged += 1
                continue

            self._mark_failed(snapshot=snapshot, task=task, result=result)
            if retry_unresolved:
                snapshot.refresh_from_db()
                create_and_queue_snapshot_delete_task(source_snapshot=snapshot)
                queued += 1
            else:
                delete_failed += 1

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: reconciled={reconciled} queued={queued} "
                f"delete_failed={delete_failed} unchanged={unchanged}"
            )
        )

    @staticmethod
    def _finalize_snapshot(
        *, snapshot: BackupSourceSnapshot, task: Task, result: dict
    ) -> bool:
        now = timezone.now()
        with transaction.atomic():
            locked_snapshot = BackupSourceSnapshot.objects.select_for_update().get(
                pk=snapshot.pk
            )
            if snapshot_is_protected(snapshot_id=locked_snapshot.id):
                return False
            BackupSourceSnapshotDirectory.objects.filter(
                source_snapshot=locked_snapshot
            ).exclude(
                status=BackupSourceSnapshotDirectory.Status.DELETED
            ).update(status=BackupSourceSnapshotDirectory.Status.DELETED, updated_at=now)
            locked_snapshot.status = BackupSourceSnapshot.Status.DELETED
            locked_snapshot.deleted_at = now
            locked_snapshot.error_code = ""
            locked_snapshot.error_message = ""
            locked_snapshot.save(
                update_fields=["status", "deleted_at", "error_code", "error_message", "updated_at"]
            )
            if task.status not in {
                Task.Status.SUCCESS,
                Task.Status.FAILED,
                Task.Status.CANCELLED,
                Task.Status.TIMEOUT,
            }:
                complete_task(
                    task_uuid=task.task_uuid,
                    organization_id=task.organization_id,
                    status=Task.Status.SUCCESS,
                    progress=100,
                    result_payload=result,
                )
        return True

    @staticmethod
    def _mark_failed(*, snapshot: BackupSourceSnapshot, task: Task, result: dict) -> None:
        message = task.error_message or "Snapshot delete requires retry."
        if task.status not in {
            Task.Status.SUCCESS,
            Task.Status.FAILED,
            Task.Status.CANCELLED,
            Task.Status.TIMEOUT,
        }:
            fail_snapshot_delete_task(
                task=task,
                source_snapshot=snapshot,
                error_code="SNAPSHOT_DELETE_REPAIR_REQUIRED",
                error_message=message,
                result_payload=result,
                event_message="Snapshot delete state repaired",
            )
            return
        BackupSourceSnapshot.objects.filter(id=snapshot.id).update(
            status=BackupSourceSnapshot.Status.DELETE_FAILED,
            error_code="SNAPSHOT_DELETE_REPAIR_REQUIRED",
            error_message=message,
            updated_at=timezone.now(),
        )
