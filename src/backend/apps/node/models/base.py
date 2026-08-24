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
    """Permission boundary, backup continuity, and lifecycle for one Agent install."""

    # Host-level continuous protection:
    # an administrator installs the Agent as a system service and the Agent
    # does not depend on an interactive login, SSH session, or desktop session.
    # The service can protect host-level paths allowed by policy and provides
    # the normal online upgrade, rollback, and uninstall lifecycle. This mode
    # applies to Linux, Windows, and macOS.
    SYSTEM = "system", "System Service"

    # Current-user protection:
    # an ordinary user installs and runs the Agent, which can access only files
    # available to that user. On Windows and macOS, locking the screen or
    # disconnecting a remote desktop normally does not end the user session;
    # signing out stops the Agent. On Linux, ending the SSH session normally
    # stops the user service. Installation, upgrade, and uninstall stay within
    # the user's own files and service scope and do not require a privileged
    # Agent process.
    USER = "user", "Current User"

    # Linux user-continuous protection:
    # an ordinary user installs and runs the Agent from the user's own Agent
    # root, configuration, data, and systemd --user service. User lingering
    # keeps that user service manager alive after SSH logout or user sign-out,
    # so file backup continues without an elevated Agent process. Enabling
    # linger may require one administrator authorization, depending on host
    # policy. User-scoped upgrade and uninstall remain available. Uninstalling
    # the Agent deliberately leaves linger unchanged because it is a shared
    # account setting that may also keep unrelated user services running.
    USER_CONTINUOUS = "user_continuous", "User Continuous (Linux)"

    # Specified-user continuous protection:
    # an administrator installs the system service, but the Agent worker runs
    # as the selected ordinary account and is limited to that account's file
    # permissions. The worker continues after SSH disconnect, sign-out, or
    # desktop logout. This mode deliberately does not keep a product-owned
    # privileged runtime or lifecycle helper. From the customer's perspective,
    # retaining such a process would make this mode indistinguishable from the
    # system mode, so online upgrade, rollback, and uninstall are not supported
    # and must be performed manually on the host with administrator authorization.
    # The mode is intended for advanced or compatibility deployments that need
    # continuous backup without running the backup worker as an administrator.
    ACCOUNT = "account", "Specified User Continuous"


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
