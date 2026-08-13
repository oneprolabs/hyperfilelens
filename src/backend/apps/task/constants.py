from __future__ import annotations

from apps.task.models import Task


RESTORE_TASK_TYPES = frozenset(
    {
        Task.Type.RESTORE,
        Task.Type.INSIGHT_WORKSPACE_RESTORE,
    }
)


def is_restore_task_type(task_type: str | None) -> bool:
    """Return whether a task type uses the shared restore execution lifecycle."""

    return str(task_type or "") in RESTORE_TASK_TYPES


__all__ = ["RESTORE_TASK_TYPES", "is_restore_task_type"]
