"""Shared primitives for node domain models."""

from __future__ import annotations

from django.db import models
from django.db.models import QuerySet
from django.utils import timezone


class NodeRole(models.TextChoices):
    """Agent topology role: data-plane agent, relay proxy, or control-plane gateway."""

    AGENT = "agent", "Agent"
    PROXY = "proxy", "Proxy"
    GATEWAY = "gateway", "Gateway"


class NodeInstallationMode(models.TextChoices):
    """Privilege and lifecycle boundary selected for one Agent installation."""

    SYSTEM = "system", "System Service"
    USER = "user", "Current User"


class TimeStampedModel(models.Model):
    """``created_at`` / ``updated_at`` for auditable rows."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(QuerySet):
    """QuerySet helpers for soft-deleted rows."""

    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(is_deleted=False)

    def deleted(self) -> SoftDeleteQuerySet:
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager):
    """Default manager: excludes soft-deleted rows."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return SoftDeleteQuerySet(self.model, using=self._db).filter(
            is_deleted=False,
        )


class AllObjectsManager(models.Manager):
    """Includes soft-deleted rows (admin, migrations, reconciliation)."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteModel(models.Model):
    """Logical delete via ``is_deleted`` + ``deleted_at``; use ``soft_delete()``."""

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(blank=True, null=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = timezone.now()
        update_fields = ["is_deleted", "deleted_at"]
        if isinstance(self, TimeStampedModel):
            update_fields.append("updated_at")
        self.save(update_fields=update_fields)


class OrganizationScopedModel(TimeStampedModel, SoftDeleteModel):
    """Tenant-scoped models with timestamps and soft delete.

    Concrete subclasses should redeclare ``organization`` with a domain
    ``related_name`` (e.g. ``nodes``, ``node_tasks``).
    """

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
    )

    class Meta:
        abstract = True
