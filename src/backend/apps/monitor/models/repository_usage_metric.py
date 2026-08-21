from django.db import models
from django.db.models import Q


class RepositoryUsageMetric(models.Model):
    """A logical 15-minute occupied-capacity sample for a storage repository."""

    class UsageSource(models.TextChoices):
        ESTIMATED = "estimated", "Estimated"
        PROVIDER = "provider", "Provider reported"

    repository = models.ForeignKey(
        "storage.Repository",
        on_delete=models.CASCADE,
        related_name="usage_metrics",
    )
    recorded_at = models.DateTimeField()
    usage_bytes = models.BigIntegerField(blank=True, null=True)
    usage_source = models.CharField(
        max_length=20,
        choices=UsageSource.choices,
        blank=True,
        null=True,
    )
    object_count = models.BigIntegerField(blank=True, null=True)

    class Meta:
        db_table = "monitor_repository_usage_metrics"
        ordering = ["repository_id", "-recorded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "recorded_at"],
                name="mon_repo_usage_repo_at_uniq",
            ),
            models.CheckConstraint(
                condition=Q(usage_bytes__gte=0) | Q(usage_bytes__isnull=True),
                name="mon_repo_usage_bytes_nonneg",
            ),
            models.CheckConstraint(
                condition=Q(object_count__gte=0) | Q(object_count__isnull=True),
                name="mon_repo_object_count_nonneg",
            ),
        ]
        indexes = [
            models.Index(
                fields=["repository", "-recorded_at"],
                name="mon_repo_usage_repo_at_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"repository:{self.repository_id}@{self.recorded_at}"
