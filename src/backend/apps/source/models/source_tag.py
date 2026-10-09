"""Organization-owned labels for backup-selectable sources."""

from django.db import models
from django.db.models.functions import Lower


class SourceTag(models.Model):
    COLORS = ("neutral", "blue", "green", "orange", "red", "purple", "teal", "pink")

    organization = models.ForeignKey("iam.Organization", on_delete=models.CASCADE)
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True, default="")
    color = models.CharField(max_length=16, default="neutral")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "source_tag"
        constraints = [
            models.UniqueConstraint(Lower("name"), "organization", name="uniq_source_tag_org_name")
        ]


class SourceTagAssignment(models.Model):
    organization = models.ForeignKey("iam.Organization", on_delete=models.CASCADE)
    tag = models.ForeignKey(SourceTag, on_delete=models.CASCADE, related_name="assignments")
    source_kind = models.CharField(max_length=16)
    ref_id = models.BigIntegerField()

    class Meta:
        db_table = "source_tag_assignment"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "tag", "source_kind", "ref_id"],
                name="uniq_source_tag_assignment",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "source_kind", "ref_id"], name="src_tag_org_source_idx"),
        ]
