"""Email-code login issuance, verification, and endpoint-specific rate limits."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.signing import salted_hmac
from django.db import transaction
from django.utils import timezone

from apps.iam.config import get_login_verification_code_minutes
from apps.iam.email_verification_models import EmailVerificationCode
from apps.iam.services.verification_code_service import verify_email_verification_code
from apps.iam.services.verification_email import (
    VerificationEmailKind,
    send_verification_code_email,
)

SEND_COOLDOWN_SECONDS = 60
SEND_EMAIL_HOURLY_LIMIT = 5
SEND_IP_HOURLY_LIMIT = 20
VERIFY_EMAIL_WINDOW_LIMIT = 20
VERIFY_IP_WINDOW_LIMIT = 60
VERIFY_WINDOW_SECONDS = 15 * 60
CODE_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


def _identity_digest(value: str) -> str:
    return salted_hmac(
        "iam.email_code_login.rate",
        value,
        algorithm="sha256",
    ).hexdigest()


def _increment_fixed_window(key: str, *, limit: int, seconds: int) -> RateLimitResult:
    now = int(time.time())
    bucket = now // seconds
    cache_key = f"email_code_login:window:{key}:{bucket}"
    timeout = max(1, (bucket + 1) * seconds - now)
    if cache.add(cache_key, 1, timeout=timeout):
        count = 1
    else:
        try:
            count = cache.incr(cache_key)
        except ValueError:
            cache.set(cache_key, 1, timeout=timeout)
            count = 1
    return RateLimitResult(count <= limit, 0 if count <= limit else timeout)


def check_send_rate_limit(*, email: str, client_ip: str, purpose: str = "login") -> RateLimitResult:
    email_key = _identity_digest(email if purpose == "login" else f"{purpose}:{email}")
    ip_key = _identity_digest((client_ip or "unknown") if purpose == "login" else f"{purpose}:{client_ip or 'unknown'}")
    now = int(time.time())
    cooldown_key = f"email_code_login:cooldown:{email_key}"
    cooldown_until = int(cache.get(cooldown_key, 0) or 0)
    if cooldown_until > now:
        return RateLimitResult(False, cooldown_until - now)

    for result in (
        _increment_fixed_window(
            f"send-email:{email_key}",
            limit=SEND_EMAIL_HOURLY_LIMIT,
            seconds=3600,
        ),
        _increment_fixed_window(
            f"send-ip:{ip_key}",
            limit=SEND_IP_HOURLY_LIMIT,
            seconds=3600,
        ),
    ):
        if not result.allowed:
            return result

    cooldown_until = now + SEND_COOLDOWN_SECONDS
    cache.set(cooldown_key, cooldown_until, timeout=SEND_COOLDOWN_SECONDS)
    return RateLimitResult(True)


def check_verify_rate_limit(*, email: str, client_ip: str, purpose: str = "login") -> RateLimitResult:
    email_key = _identity_digest(email if purpose == "login" else f"{purpose}:{email}")
    ip_key = _identity_digest((client_ip or "unknown") if purpose == "login" else f"{purpose}:{client_ip or 'unknown'}")
    results = (
        _increment_fixed_window(
            f"verify-email:{email_key}",
            limit=VERIFY_EMAIL_WINDOW_LIMIT,
            seconds=VERIFY_WINDOW_SECONDS,
        ),
        _increment_fixed_window(
            f"verify-ip:{ip_key}",
            limit=VERIFY_IP_WINDOW_LIMIT,
            seconds=VERIFY_WINDOW_SECONDS,
        ),
    )
    blocked = [result.retry_after for result in results if not result.allowed]
    return RateLimitResult(not blocked, max(blocked, default=0))


def issue_login_code(user: User) -> int:
    purpose = EmailVerificationCode.Purpose.LOGIN
    plain_code = EmailVerificationCode.generate_code()
    code_hash = EmailVerificationCode.hash_code(
        plain_code,
        user_id=user.id,
        purpose=purpose,
    )
    minutes = get_login_verification_code_minutes()
    email_code = EmailVerificationCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=code_hash,
        expires_at=timezone.now() + timedelta(minutes=minutes),
    )
    try:
        send_verification_code_email(
            recipient=user.email,
            code=plain_code,
            minutes=minutes,
            kind=VerificationEmailKind.LOGIN,
        )
    except Exception:
        email_code.delete()
        raise

    now = timezone.now()
    EmailVerificationCode.objects.filter(
        user=user,
        purpose=purpose,
        is_used=False,
    ).exclude(pk=email_code.pk).update(
        is_used=True,
        used_at=now,
        invalidated_at=now,
    )
    return minutes * 60


@transaction.atomic
def verify_login_code(user: User, code: str) -> tuple[bool, str | None]:
    return verify_email_verification_code(
        user,
        code,
        purpose=EmailVerificationCode.Purpose.LOGIN,
        max_attempts=CODE_MAX_ATTEMPTS,
    )
