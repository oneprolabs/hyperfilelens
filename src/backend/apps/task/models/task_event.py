from django.db import models

from apps.task.event_text import neutral_event_metadata, neutral_event_text

from .task import Task
from .task_step import TaskStep


class TaskEvent(models.Model):
    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        WARN = "WARN", "Warn"
        ERROR = "ERROR", "Error"
        DEBUG = "DEBUG", "Debug"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="events")
    step = models.ForeignKey(
        TaskStep,
        on_delete=models.SET_NULL,
        related_name="events",
        blank=True,
        null=True,
    )
    seq = models.BigIntegerField()
    level = models.CharField(max_length=16, choices=Level.choices, db_index=True)
    message = models.TextField()
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def save(self, *args, **kwargs):
        # Apply to both collection and later failure-detail refreshes. Keep the
        # caller's metadata untouched so service logs retain engine diagnostics.
        self.message = neutral_event_text(self.message)
        self.metadata = neutral_event_metadata(self.metadata)
        return super().save(*args, **kwargs)

    class Meta:
        db_table = "task_event"
        ordering = ["seq", "id"]
        constraints = [
            models.UniqueConstraint(fields=["task", "seq"], name="uniq_task_event_seq"),
        ]
        indexes = [
            models.Index(fields=["task", "created_at"], name="task_event_task_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.task_id}:{self.seq}:{self.level}"
