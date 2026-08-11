from __future__ import annotations

from django.db import models


class TaskDependency(models.Model):
    """A durable condition that must resolve before a task can run."""

    class ReferenceType(models.TextChoices):
        TASK = "task", "Task"
        NODE_TASK = "node_task", "Node task"
        EXTERNAL = "external", "External condition"

    task = models.ForeignKey(
        "task.Task",
        on_delete=models.CASCADE,
        related_name="dependencies",
    )
    blocking_task = models.ForeignKey(
        "task.Task",
        on_delete=models.SET_NULL,
        related_name="blocked_tasks",
        blank=True,
        null=True,
    )
    reference_type = models.CharField(
        max_length=24,
        choices=ReferenceType.choices,
        default=ReferenceType.EXTERNAL,
    )
    reference_id = models.CharField(max_length=128, blank=True, default="")
    reference_task_type = models.CharField(max_length=64, blank=True, default="")
    code = models.CharField(max_length=64, db_index=True)
    detail = models.TextField()
    auto_resumable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_checked_at = models.DateTimeField(blank=True, null=True)
    next_check_at = models.DateTimeField(blank=True, null=True, db_index=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "task_dependency"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["task", "is_active"],
                name="task_dep_task_active_idx",
            ),
            models.Index(
                fields=["reference_type", "reference_id", "is_active"],
                name="task_dep_ref_active_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_id} waits for {self.reference_type}:{self.reference_id or self.code}"
