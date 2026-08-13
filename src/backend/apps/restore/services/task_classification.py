from __future__ import annotations

from apps.restore.models import RestoreRecord
from apps.task.models import Task


def normalize_restore_task_type(*, record: RestoreRecord, task: Task) -> None:
    """Classify an active legacy insight restore before terminal completion."""

    if (
        record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE
        and task.task_type == Task.Type.RESTORE
    ):
        task.task_type = Task.Type.INSIGHT_WORKSPACE_RESTORE
        task.save(update_fields=["task_type", "updated_at"])
