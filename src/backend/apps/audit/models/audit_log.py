"""
Audit log models.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.iam.models import Organization

from apps.audit.constants import AuditResult


class AuditLog(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    user_email = models.CharField(max_length=255, blank=True, default="")
    user_name = models.CharField(max_length=255, blank=True, default="")

    action = models.CharField(max_length=120, db_index=True)
    target_type = models.CharField(max_length=120, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=120, blank=True, default="", db_index=True)

    resource_type = models.CharField(max_length=120, blank=True, default="", db_index=True)
    resource_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    resource_name = models.CharField(max_length=255, blank=True, default="")

    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")
    details = models.TextField(blank=True, default="")
    changes = models.JSONField(default=dict, blank=True)
    result = models.CharField(
        max_length=20,
        choices=[
            (AuditResult.SUCCESS, "Success"),
            (AuditResult.FAILURE, "Failure"),
            (AuditResult.PARTIAL, "Partial"),
        ],
        default=AuditResult.SUCCESS,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=50, blank=True, default="")

    request_method = models.CharField(max_length=10, blank=True, default="")
    request_path = models.CharField(max_length=1000, blank=True, default="")
    request_query = models.JSONField(default=dict, blank=True)
    request_body = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    correlation_id = models.CharField(max_length=36, blank=True, default="", db_index=True)
    session_id = models.CharField(max_length=100, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="audit_log_orga_9b4d4b_idx",
            ),
            models.Index(
                fields=["organization", "action", "created_at"],
                name="audit_log_orga_5c1b2b_idx",
            ),
            models.Index(fields=["correlation_id"], name="audit_corr_id_idx"),
            models.Index(fields=["resource_type", "resource_id"], name="audit_resource_idx"),
            models.Index(
                fields=["organization", "result", "created_at"],
                name="audit_org_result_created_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.user_id and not self.user_email:
            email = getattr(self.user, "email", "") or ""
            name = (
                f"{getattr(self.user, 'first_name', '')} {getattr(self.user, 'last_name', '')}".strip()
            )
            self.user_email = email
            self.user_name = name or email
        if self.request_id and not self.correlation_id:
            self.correlation_id = self.request_id[:36]
        elif self.correlation_id and not self.request_id:
            self.request_id = self.correlation_id[:100]
        super().save(*args, **kwargs)
