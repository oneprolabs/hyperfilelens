"""Email verification codes for registration and password reset."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.signing import salted_hmac
from django.db import models
from django.utils import timezone


class EmailVerificationCode(models.Model):
    """Purpose-bound, single-use email verification code."""

    class Purpose(models.TextChoices):
        LEGACY = "legacy", "Legacy"
        REGISTRATION = "registration", "Registration"
        PASSWORD_RESET = "password_reset", "Password reset"
        LOGIN = "login", "Login"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_codes",
    )
    purpose = models.CharField(
        max_length=32,
        choices=Purpose.choices,
        default=Purpose.LEGACY,
        db_index=True,
    )
    code_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "iam_email_verification_code"
        indexes = [
            models.Index(fields=["user", "is_used", "expires_at"]),
            models.Index(
                fields=["user", "purpose", "is_used", "expires_at"],
                name="iam_email_code_purpose_idx",
            ),
        ]

    @staticmethod
    def generate_code() -> str:
        return str(secrets.randbelow(900000) + 100000)

    @staticmethod
    def hash_code(
        plain_code: str,
        *,
        user_id: int,
        purpose: str,
        recipient_email: str | None = None,
    ) -> str:
        value = f"{user_id}:{purpose}:{plain_code}"
        if recipient_email is not None:
            value += f":{recipient_email.strip().lower()}"
        return salted_hmac(
            "iam.email_verification_code",
            value,
            algorithm="sha256",
        ).hexdigest()

    @property
    def is_valid(self) -> bool:
        return (
            timezone.now() < self.expires_at
            and not self.is_used
            and self.invalidated_at is None
        )
