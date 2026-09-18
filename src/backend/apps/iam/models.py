"""
IAM domain models: organization/tenant and thin user↔org affiliation.

Role authority lives in the commercial AuthzProvider (plugin).
Community (no provider): any active affiliation is owner-equivalent.
"""

import secrets

from django.conf import settings
from django.db import models


class Organization(models.Model):
    """
    Tenant boundary.
    """

    key = models.SlugField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Stable tenant key used in API requests.",
    )
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "iam_organization"
        ordering = ["key", "id"]

    def __str__(self):
        return f"{self.name} ({self.key})"


class MembershipQuerySet(models.QuerySet):
    """Affiliation rows only. ``role=`` on create is forwarded to AuthzProvider sync."""

    def create(self, **kwargs):
        from django.db import transaction

        role = kwargs.pop("role", None)
        with transaction.atomic():
            obj = super().create(**kwargs)
            # Seed EE role for inactive rows too (reactivate must not invent OPERATOR).
            if role is not None:
                from apps.iam.services.membership_service import sync_member_role

                sync_member_role(
                    user_id=obj.user_id,
                    organization_id=obj.organization_id,
                    role=role,
                )
            return obj


class Membership(models.Model):
    """
    Thin user↔organization affiliation (no role column).

    ``Role`` enum values remain as permission vocabulary / EE sync constants.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        OPERATOR = "operator", "Operator"
        AUDITOR = "auditor", "Auditor"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    preferred_feature = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Optional preferred feature key for landing path within this org.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MembershipQuerySet.as_manager()

    class Meta:
        db_table = "iam_membership"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="uniq_iam_user_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "is_active"],
                name="iam_members_org_active_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user_id}@{self.organization_id}"


class PersonalApiKey(models.Model):
    """
    Simple personal API key for external integrations.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="personal_api_keys",
    )
    name = models.CharField(max_length=120)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "iam_personal_api_key"
        ordering = ["-created_at", "id"]

    @staticmethod
    def generate_token() -> str:
        return secrets.token_hex(32)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = self.generate_token()
        return super().save(*args, **kwargs)


class OAuthErrorEvent(models.Model):
    """Short-lived, single-use handoff for verified OAuth failures."""

    class Reason(models.TextChoices):
        OAUTH_FAILED = "oauth_failed", "OAuth failed"
        STATE_LOST = "state_lost", "OAuth state lost"
        INVALID_GRANT = "invalid_grant", "Invalid OAuth grant"
        NO_EMAIL = "no_email", "Email unavailable"
        DISABLED = "disabled", "OAuth disabled"
        NOT_AUTHENTICATED = "not_authenticated", "Authentication incomplete"
        ACCOUNT_DISABLED = "account_disabled", "Account disabled"
        PROVISION_FAILED = "provision_failed", "Provisioning failed"

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "iam_oauth_error_event"
        ordering = ["-created_at", "id"]
