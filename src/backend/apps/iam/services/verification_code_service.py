"""Issue and verify purpose-bound email verification codes."""

from __future__ import annotations

import hashlib
import secrets

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from apps.iam.email_verification_models import EmailVerificationCode


@transaction.atomic
def verify_email_verification_code(
    user: User,
    code: str,
    *,
    purpose: str,
    max_attempts: int | None = None,
    allow_legacy: bool = True,
    recipient_email: str | None = None,
) -> tuple[bool, str | None]:
    """
    Verify a 6-digit email verification code.

    Returns:
        (is_valid, error_reason)
    """
    code = str(code or "").strip()
    if not code or len(code) != 6 or not code.isdigit():
        return False, "INVALID_FORMAT"

    accepted_purposes = [purpose]
    if allow_legacy and purpose != EmailVerificationCode.Purpose.LOGIN:
        accepted_purposes.append(EmailVerificationCode.Purpose.LEGACY)

    email_code = (
        EmailVerificationCode.objects.select_for_update()
        .filter(
            user=user,
            is_used=False,
            invalidated_at__isnull=True,
            purpose__in=accepted_purposes,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if email_code is None:
        return False, "INVALID_CODE"

    if email_code.expires_at < timezone.now():
        return False, "EXPIRED"

    if max_attempts is not None and email_code.failed_attempts >= max_attempts:
        return False, "TOO_MANY_ATTEMPTS"

    if email_code.purpose == EmailVerificationCode.Purpose.LEGACY:
        expected_hash = hashlib.sha256(code.encode()).hexdigest()
    else:
        expected_hash = EmailVerificationCode.hash_code(
            code,
            user_id=user.id,
            purpose=email_code.purpose,
            recipient_email=recipient_email,
        )

    if not secrets.compare_digest(email_code.code_hash, expected_hash):
        if max_attempts is None:
            return False, "INVALID_CODE"
        email_code.failed_attempts += 1
        update_fields = ["failed_attempts"]
        if max_attempts is not None and email_code.failed_attempts >= max_attempts:
            email_code.invalidated_at = timezone.now()
            update_fields.append("invalidated_at")
        email_code.save(update_fields=update_fields)
        if max_attempts is not None and email_code.failed_attempts >= max_attempts:
            return False, "TOO_MANY_ATTEMPTS"
        return False, "INVALID_CODE"

    email_code.is_used = True
    email_code.used_at = timezone.now()
    email_code.save(update_fields=["is_used", "used_at"])
    return True, None
