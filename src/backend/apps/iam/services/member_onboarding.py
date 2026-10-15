"""Transactional account creation and organization membership, not invitations."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext as _

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.audit.services import interface as audit_services
from apps.iam.models import IdentityProvisioningLock, Membership, Organization
from apps.iam.profile_models import Profile
from apps.iam.services.member_management import (
    assert_eligible_member_account,
    assert_membership_management,
)
from apps.iam.services.membership_service import create_org_membership


def lock_identity_provisioning() -> None:
    """Call inside an atomic block before creating a local email identity."""
    # The durable row also recreates safely after a test database flush.
    IdentityProvisioningLock.objects.select_for_update().get_or_create(
        key="local_identity"
    )


@dataclass(frozen=True)
class OnboardingResult:
    membership: Membership
    account_created: bool
    temporary_password: str | None = None


@transaction.atomic
def onboard_member(
    request: Any,
    *,
    email: str | None,
    user_id: int | None,
    role: str,
    is_active: bool = True,
) -> OnboardingResult:
    """Only brand-new ordinary accounts receive one-time credentials."""
    from apps.iam.permissions_org import get_membership

    actor = get_membership(request)
    if actor is None:
        assert_membership_management(request, operation="can_add")
        raise ValidationError("Organization membership is required.")
    lock_identity_provisioning()
    org = Organization.objects.select_for_update(no_key=True).get(
        pk=actor.organization_id
    )
    assert_membership_management(request, operation="can_add", role=role)
    users = get_user_model()
    matches = list(
        users.objects.select_for_update()
        .filter(
            **({"email__iexact": email.strip().lower()} if email else {"pk": user_id})
        )
        .order_by("id")[:2]
    )
    if len(matches) > 1:
        raise ValidationError(
            {
                "email": _(
                    "Multiple accounts use this email. Contact your platform administrator."
                )
            }
        )
    created = not matches
    temporary_password = None
    if created:
        if not email:
            raise ValidationError({"user": _("Account not found.")})
        if not is_active:
            raise ValidationError(
                {"is_active": _("New accounts must reserve an active membership seat.")}
            )
        temporary_password = "Aa9!" + secrets.token_urlsafe(12)
        account = users.objects.create_user(
            username=f"member_{secrets.token_hex(16)}",
            email=email.strip().lower(),
            password=temporary_password,
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        profile, _profile_created = Profile.objects.get_or_create(user=account)
        profile.registration_completed = True
        profile.requires_password_change = True
        profile.save(
            update_fields=["registration_completed", "requires_password_change"]
        )
    else:
        account = matches[0]
    assert_eligible_member_account(account)
    existing = Membership.objects.filter(organization=org, user=account).first()
    if existing:
        raise ValidationError(
            {
                "email": (
                    _(
                        "This member is disabled in this organization. Enable the existing membership instead."
                    )
                    if not existing.is_active
                    else _("This account is already a member of this organization.")
                )
            }
        )
    membership = create_org_membership(
        organization=org,
        user=account,
        role=role,
        is_active=is_active,
    )
    # No password, request body or credential material is ever passed to audit.
    audit_services.write_audit_log_from_request(
        request,
        organization=org,
        action="membership.add",
        resource_type="membership",
        resource_id=str(membership.pk),
        resource_name=account.email or account.username,
        changes={
            "account_created": created,
            "after": {
                "user_id": account.pk,
                "role": role,
                "is_active": membership.is_active,
            },
        },
    )
    if created:
        audit_services.write_audit_log_from_request(
            request,
            organization=org,
            action="account.provision",
            resource_type="user",
            resource_id=str(account.pk),
            resource_name=account.email,
            changes={"requires_password_change": True},
        )
    return OnboardingResult(membership, created, temporary_password)
