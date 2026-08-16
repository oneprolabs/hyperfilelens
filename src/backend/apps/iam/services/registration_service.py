"""
Registration and password reset services.
"""

import hashlib
import logging
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.iam.config import (
    get_password_reset_verification_code_minutes,
    get_registration_verification_code_minutes,
)
from apps.iam.models import Membership, Organization

User = get_user_model()
logger = logging.getLogger(__name__)


def unique_org_key_for_email(email: str) -> str:
    """Derive a unique organization key from the user's email."""
    local = email.split("@", 1)[0]
    base = slugify(local) or "org"
    base = base[:40]
    suffix = hashlib.sha256(email.encode()).hexdigest()[:8]
    key = f"{base}-{suffix}"[:64]
    candidate = key
    counter = 1
    while Organization.objects.filter(key=candidate).exists():
        candidate = f"{key[:56]}-{counter}"[:64]
        counter += 1
    return candidate


@transaction.atomic
def provision_registered_user_tenant(user: User) -> tuple[Organization, Membership]:
    """
    Create a dedicated organization and owner membership for a newly registered user.
    Idempotent when the user already has an active membership.
    """
    existing = (
        Membership.objects.filter(user=user, is_active=True)
        .select_related("organization")
        .order_by("id")
        .first()
    )
    if existing is not None:
        return existing.organization, existing

    email = (user.email or "").strip().lower()
    local = email.split("@", 1)[0] if email else user.username
    from apps.subscription.services.internal.organization_count import (
        assert_organization_count_available,
    )

    assert_organization_count_available(additional=1)
    # The instance organization lock may have waited for another registration
    # of this same user. Re-read after acquiring it so an idempotent retry does
    # not create a second tenant from the stale pre-lock membership snapshot.
    existing = (
        Membership.objects.filter(user=user, is_active=True)
        .select_related("organization")
        .order_by("id")
        .first()
    )
    if existing is not None:
        return existing.organization, existing
    org = Organization.objects.create(
        key=unique_org_key_for_email(email or user.username),
        name=(email or local or user.username or "org")[:200],
        is_active=True,
    )
    from apps.subscription.services.interface import (
        enforce_license_quota,
        initialize_organization_quota,
    )

    initialize_organization_quota(org)

    # The automatically created Owner consumes the shared instance user pool.
    # Keep this check inside the organization transaction so a denial rolls
    # back both the new organization and its membership.
    enforce_license_quota(org, "max_users", additional=1)
    # role= is popped by MembershipQuerySet and synced to EE MemberRole when present.
    membership = Membership.objects.create(
        user=user,
        organization=org,
        role=Membership.Role.OWNER,
        is_active=True,
    )
    return org, membership


@transaction.atomic
def complete_user_registration(user: User, password: str, code: str) -> tuple[bool, str | None, Organization | None]:
    """
    Verify email code, activate user, set password, and provision tenant resources.

    Returns:
        tuple: (success, error_reason, organization or None)
    """
    from apps.iam.profile_models import Profile
    from apps.iam.services.verification_code_service import verify_email_verification_code

    if user.is_active:
        return False, "ALREADY_ACTIVE", None

    from apps.iam.email_verification_models import EmailVerificationCode

    is_valid, error_reason = verify_email_verification_code(
        user,
        code,
        purpose=EmailVerificationCode.Purpose.REGISTRATION,
    )
    if not is_valid:
        return False, error_reason, None

    user.set_password(password)
    user.is_active = True
    user.save(update_fields=["password", "is_active"])

    try:
        profile = Profile.objects.get(user=user)
        profile.registration_completed = True
        profile.registered_at = timezone.now()
        profile.save(update_fields=["registration_completed", "registered_at"])
    except Profile.DoesNotExist:
        Profile.objects.create(
            user=user,
            registration_completed=True,
            registered_at=timezone.now(),
        )

    org, _membership = provision_registered_user_tenant(user)
    from apps.lens_bridge.services.chat_user_provisioning import enqueue_sl_chat_user_provision

    enqueue_sl_chat_user_provision(user_id=user.id)
    return True, None, org


@transaction.atomic
def complete_social_user_registration(user: User) -> Organization:
    """
    Activate a Google/OAuth user and provision a dedicated tenant (same as email registration).

    Idempotent for users who already completed registration.
    """
    from apps.iam.profile_models import Profile

    was_inactive = not user.is_active
    user.is_active = True
    if was_inactive:
        user.set_unusable_password()
    user.save(update_fields=["is_active", "password"])

    try:
        profile = Profile.objects.get(user=user)
        if not profile.registration_completed:
            profile.registration_completed = True
            profile.registered_at = timezone.now()
            profile.save(update_fields=["registration_completed", "registered_at"])
    except Profile.DoesNotExist:
        Profile.objects.create(
            user=user,
            registration_completed=True,
            registered_at=timezone.now(),
        )

    org, _membership = provision_registered_user_tenant(user)
    from apps.lens_bridge.services.chat_user_provisioning import enqueue_sl_chat_user_provision

    enqueue_sl_chat_user_provision(user_id=user.id)
    return org


def generate_registration_verification_code(user: User) -> tuple[str, str | None]:
    """
    Generate a 6-digit registration verification code and send it via email.

    Returns:
        tuple: (code: str, error: str or None)
    """
    from apps.iam.email_verification_models import EmailVerificationCode

    plain_code = EmailVerificationCode.generate_code()
    purpose = EmailVerificationCode.Purpose.REGISTRATION
    code_hash = EmailVerificationCode.hash_code(
        plain_code,
        user_id=user.id,
        purpose=purpose,
    )

    EmailVerificationCode.objects.filter(
        user=user,
        purpose=purpose,
        is_used=False,
    ).update(is_used=True, used_at=timezone.now())

    email_code = EmailVerificationCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=code_hash,
        expires_at=timezone.now()
        + timedelta(minutes=get_registration_verification_code_minutes()),
    )

    try:
        _send_registration_verification_email(user.email, plain_code)
        return plain_code, None
    except Exception as exc:
        email_code.delete()
        logger.warning(
            "registration verification email delivery failed user_id=%s: %s",
            user.id,
            exc,
            exc_info=True,
        )
        return "", "EMAIL_SERVICE_UNAVAILABLE"


def _send_registration_verification_email(email: str, code: str) -> None:
    """Send registration verification code via email."""
    from apps.iam.services.verification_email import (
        VerificationEmailKind,
        send_verification_code_email,
    )

    send_verification_code_email(
        recipient=email,
        code=code,
        minutes=get_registration_verification_code_minutes(),
        kind=VerificationEmailKind.REGISTRATION,
    )


def generate_password_reset_code(user: User) -> tuple[str, str | None]:
    """
    Generate a 6-digit password reset code and send it via email.

    Returns:
        tuple: (code: str, error: str or None)
    """
    from apps.iam.email_verification_models import EmailVerificationCode

    plain_code = EmailVerificationCode.generate_code()
    purpose = EmailVerificationCode.Purpose.PASSWORD_RESET
    code_hash = EmailVerificationCode.hash_code(
        plain_code,
        user_id=user.id,
        purpose=purpose,
    )

    EmailVerificationCode.objects.filter(
        user=user,
        purpose=purpose,
        is_used=False,
    ).update(is_used=True, used_at=timezone.now())

    email_code = EmailVerificationCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=code_hash,
        expires_at=timezone.now()
        + timedelta(minutes=get_password_reset_verification_code_minutes()),
    )

    try:
        _send_password_reset_email(user.email, plain_code)
        return plain_code, None
    except Exception as exc:
        email_code.delete()
        logger.warning(
            "password reset email delivery failed user_id=%s: %s",
            user.id,
            exc,
            exc_info=True,
        )
        return "", "EMAIL_SERVICE_UNAVAILABLE"


def _send_password_reset_email(email: str, code: str) -> None:
    """Send password reset code via email."""
    from apps.iam.services.verification_email import (
        VerificationEmailKind,
        send_verification_code_email,
    )

    send_verification_code_email(
        recipient=email,
        code=code,
        minutes=get_password_reset_verification_code_minutes(),
        kind=VerificationEmailKind.PASSWORD_RESET,
    )


def validate_password_format(password: str) -> tuple[bool, str | None]:
    """
    Validate password format: 8-20 chars, must contain lowercase, uppercase and digits.

    Returns:
        tuple: (is_valid: bool, error_message: str or None)
    """
    if len(password) < 8 or len(password) > 20:
        return False, str(_("Password must be 8-20 characters"))
    if not re.fullmatch(r'[A-Za-z\d.!"@#$%&\'*():;\\+/=?^_`{|}~><-]+', password):
        return False, str(_("Password contains unsupported characters"))
    if not re.search(r'[a-z]', password):
        return False, str(_("Password must contain at least one lowercase letter"))
    if not re.search(r'[A-Z]', password):
        return False, str(_("Password must contain at least one uppercase letter"))
    if not re.search(r'\d', password):
        return False, str(_("Password must contain at least one digit"))
    return True, None


def mask_email(email: str) -> str:
    """Mask email address for display, e.g., u***@example.com"""
    if not email or '@' not in email:
        return email
    local, domain = email.rsplit('@', 1)
    if len(local) <= 2:
        masked_local = local[0] + '*'
    else:
        masked_local = local[0] + '*' * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"
