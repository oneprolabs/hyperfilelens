"""Idempotent projection repair for restore NodeTasks after worker handoff."""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import CharField, Exists, OuterRef, Subquery
from django.db.models.functions import Cast

from apps.node.models import NodeTask
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.restore.services.task_classification import normalize_restore_task_type
from apps.restore.signals import sync_restore_record_from_node_task
from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.models import Task
from apps.task.services.interface import TERMINAL_STATUSES


logger = logging.getLogger(__name__)


_RESTORE_CORRELATIONS = ("restore.record", "restore.repository_server")
_TERMINAL_NODE_STATUSES = (
    NodeTask.Status.SUCCESS,
    NodeTask.Status.FAILED,
    NodeTask.Status.TIMEOUT,
    NodeTask.Status.CANCELED,
)
_ACTIVE_ITEM_STATUSES = (
    RestoreRecordItem.Status.PENDING,
    RestoreRecordItem.Status.RUNNING,
)


def reconcile_restore_node_task_projections(*, limit: int = 200) -> dict[str, int]:
    """Converge legacy task types and repair incomplete restore projections."""
    batch_size = max(1, int(limit))
    candidates = _candidate_terminal_node_tasks(limit=batch_size)
    replayed = 0
    replay_failed = 0
    for node_task in candidates:
        try:
            if not _projection_needs_replay(node_task=node_task):
                continue
            sync_restore_record_from_node_task(NodeTask, node_task)
        except Exception:
            replay_failed += 1
            logger.exception(
                "restore projection reconciliation failed node_task_id=%s",
                node_task.id,
            )
            continue
        replayed += 1
    classified, classification_failed = _classify_terminal_legacy_insight_tasks(
        limit=batch_size
    )
    return {
        "candidates": len(candidates),
        "replayed": replayed,
        "replay_failed": replay_failed,
        "classified": classified,
        "classification_failed": classification_failed,
    }


def _classify_terminal_legacy_insight_tasks(*, limit: int) -> tuple[int, int]:
    """Converge tasks completed by an adjacent pre-classification release."""
    from apps.source.services.internal.source_pipeline import (
        sync_task_pipeline_projection,
    )

    task_ids = (
        RestoreRecord.objects.filter(purpose=RestoreRecord.Purpose.LENS_WORKSPACE)
        .order_by()
        .values("task_id")
    )
    candidate_ids = list(
        Task.objects.filter(
            id__in=Subquery(task_ids),
            task_type=Task.Type.RESTORE,
            status__in=TERMINAL_STATUSES,
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    classified = 0
    failed = 0
    for task_id in candidate_ids:
        # Keep classification and its Protection read-model repair atomic. If
        # projection repair fails, the legacy type remains eligible for retry.
        try:
            with transaction.atomic():
                task = (
                    Task.objects.select_for_update()
                    .filter(
                        id=task_id,
                        task_type=Task.Type.RESTORE,
                        status__in=TERMINAL_STATUSES,
                    )
                    .first()
                )
                if task is None:
                    continue
                record = RestoreRecord.objects.filter(
                    task_id=task.id,
                    purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
                ).first()
                if record is None:
                    continue
                normalize_restore_task_type(record=record, task=task)
                sync_task_pipeline_projection(task_id=task.id)
        except Exception:
            failed += 1
            logger.exception(
                "legacy insight restore classification failed task_id=%s",
                task_id,
            )
            continue
        classified += 1
    return classified, failed


def _candidate_terminal_node_tasks(*, limit: int) -> list[NodeTask]:
    """Select work from incomplete projections instead of a moving task window."""
    node_task_ids = (
        RestoreRecordItem.objects.filter(
            node_task_id__isnull=False,
            terminal_projection_at__isnull=True,
        )
        .order_by()
        .values("node_task_id")
    )
    candidates = list(
        NodeTask.objects.filter(
            id__in=Subquery(node_task_ids),
            correlation_type="restore.record",
            status__in=_TERMINAL_NODE_STATUSES,
        ).order_by("updated_at", "id")[:limit]
    )

    remaining = max(0, limit - len(candidates))
    if remaining == 0:
        return candidates

    active_task_uuids = (
        Task.objects.filter(
            task_type__in=RESTORE_TASK_TYPES,
            status__in=(Task.Status.PENDING, Task.Status.RUNNING),
        )
        .order_by()
        .values("task_uuid")
    )
    active_items = RestoreRecordItem.objects.filter(
        restore_record_id=OuterRef("pk"),
        status__in=_ACTIVE_ITEM_STATUSES,
    )
    terminal_node_task = (
        NodeTask.objects.filter(
            correlation_type__in=_RESTORE_CORRELATIONS,
            correlation_id=Cast(OuterRef("task_uuid"), output_field=CharField()),
            status__in=_TERMINAL_NODE_STATUSES,
        )
        .order_by("-updated_at", "-id")
        .values("id")[:1]
    )
    terminal_node_task_ids = (
        RestoreRecord.objects.filter(task_uuid__in=Subquery(active_task_uuids))
        .annotate(has_active_items=Exists(active_items))
        .filter(has_active_items=False)
        .annotate(terminal_node_task_id=Subquery(terminal_node_task))
        .filter(terminal_node_task_id__isnull=False)
        .order_by("updated_at", "id")
        .values("terminal_node_task_id")[:remaining]
    )
    seen = {node_task.id for node_task in candidates}
    additional = NodeTask.objects.filter(
        id__in=Subquery(terminal_node_task_ids),
    ).order_by("updated_at", "id")
    for node_task in additional:
        if node_task.id in seen:
            continue
        candidates.append(node_task)
        seen.add(node_task.id)
    return candidates


def _projection_needs_replay(*, node_task: NodeTask) -> bool:
    if node_task.correlation_type == "restore.record":
        payload = node_task.payload if isinstance(node_task.payload, dict) else {}
        try:
            item_id = int(payload.get("restore_record_item_id"))
        except (TypeError, ValueError):
            return False
        item = (
            RestoreRecordItem.objects.select_related("restore_record")
            .filter(
                id=item_id,
                node_task_id=node_task.id,
            )
            .first()
        )
        if item is None:
            return False
        task = Task.objects.filter(
            organization_id=item.restore_record.organization_id,
            task_uuid=item.restore_record.task_uuid,
        ).first()
        if task is None:
            return False
        if item.terminal_projection_at is None:
            return True
        if task.status in TERMINAL_STATUSES:
            return False
        return not RestoreRecordItem.objects.filter(
            restore_record=item.restore_record,
            status__in=_ACTIVE_ITEM_STATUSES,
        ).exists()

    record = RestoreRecord.objects.filter(
        task_uuid=node_task.correlation_id,
    ).first()
    if record is None:
        return False
    task = Task.objects.filter(
        organization_id=record.organization_id,
        task_uuid=record.task_uuid,
    ).first()
    if task is None or task.status in TERMINAL_STATUSES:
        return False
    return not RestoreRecordItem.objects.filter(
        restore_record=record,
        status__in=_ACTIVE_ITEM_STATUSES,
    ).exists()
