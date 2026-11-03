"""Resolve the organization Owner contact for product workflows."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractBaseUser

def resolve_organization_owner_user(organization: Any) -> AbstractBaseUser | None:
    """Return the primary organization Owner user, if any."""
    if organization is None:
        return None

    from common.extension_spi import get_authz_provider

    provider = get_authz_provider()
    if provider is not None:
        email = provider.resolve_owner_email(organization)
        if not email:
            return None
        membership = (
            organization.memberships.filter(
                is_active=True,
                user__email__iexact=str(email).strip(),
            )
            .select_related("user")
            .order_by("id")
            .first()
        )
        return membership.user if membership else None

    # Community has no persisted membership role column. Its historical
    # owner-equivalent behavior is the first active affiliation, so do not
    # query ``Membership.role`` here: the ``role=`` argument accepted by
    # MembershipQuerySet.create() is an EE synchronization input only.
    owner = (
        organization.memberships.filter(is_active=True)
        .select_related("user")
        .order_by("id")
        .first()
    )
    return owner.user if owner else None


def resolve_organization_owner_email(organization: Any) -> str | None:
    """Return the primary organization Owner email, if any."""
    owner = resolve_organization_owner_user(organization)
    email = getattr(owner, "email", None) if owner else None
    return str(email).strip() or None
