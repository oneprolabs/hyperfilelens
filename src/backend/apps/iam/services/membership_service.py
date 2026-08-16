"""Membership write rules (owner uniqueness, role guards).

Affiliation rows stay on OSS ``Membership`` (no role column). When an
AuthzProvider is active, role authority is synced only through the SPI.

Deactivate clears affiliation only — plugin role rows are retained so
reactivate restores the prior role. Delete removes the plugin role row.
Inactive creates still seed the plugin role so later activate keeps authority.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from apps.iam.models import Membership, Organization
from common.extension_spi import get_authz_provider


class MembershipPolicyError(ValidationError):
    pass


def sync_member_role(*, user_id: int, organization_id: int, role: str | None) -> None:
    """Forward role writes to the plugin AuthzProvider when present."""
    provider = get_authz_provider()
    if provider is None:
        return
    try:
        provider.sync_member_role(
            user_id=user_id, organization_id=organization_id, role=role
        )
    except DjangoValidationError as exc:
        detail = getattr(exc, "message_dict", None) or {
            "role": list(getattr(exc, "messages", None) or [str(exc)])
        }
        raise MembershipPolicyError(detail) from exc


# Back-compat alias (call sites / seeds).
_sync_ee_role = sync_member_role


def authoritative_role(membership: Membership) -> str:
    """Resolve display/policy role: plugin AuthzProvider, else community OWNER."""
    provider = get_authz_provider()
    if provider is not None:
        role = provider.get_org_role(membership.user, membership.organization.key)
        if role:
            return role
        peek = getattr(provider, "peek_stored_role", None)
        if peek is not None:
            stored = peek(
                user_id=membership.user_id,
                organization_id=membership.organization_id,
            )
            if stored:
                return stored
        # Fail-closed: do not invent OPERATOR for active members missing EE rows.
        # Inactive rows without a seed show empty for display.
        return ""
    return Membership.Role.OWNER


# Back-compat alias for callers/tests.
_authoritative_role = authoritative_role


def active_owner_count(
    organization: Organization, *, exclude_pk: int | None = None
) -> int:
    exclude_user_id: int | None = None
    if exclude_pk is not None:
        m = Membership.objects.filter(pk=exclude_pk).first()
        if m is not None:
            exclude_user_id = m.user_id

    provider = get_authz_provider()
    if provider is not None:
        return provider.count_active_owners(
            organization, exclude_user_id=exclude_user_id
        )

    # Community: every active affiliation is owner-equivalent.
    qs = Membership.objects.filter(organization=organization, is_active=True)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.count()


def assert_role_assignable(role: str) -> None:
    if role == Membership.Role.OWNER:
        raise MembershipPolicyError(
            {"role": [_("Owner role cannot be assigned via membership API.")]}
        )


def assert_can_deactivate(membership: Membership) -> None:
    if not membership.is_active:
        return
    # Keep at least one owner-authority affiliation (community: last active member).
    if active_owner_count(membership.organization, exclude_pk=membership.pk) < 1:
        raise MembershipPolicyError(
            {"is_active": [_("Cannot deactivate the organization owner.")]}
        )


def assert_can_delete(membership: Membership) -> None:
    org = membership.organization
    others = Membership.objects.filter(organization=org).exclude(pk=membership.pk)
    if not others.exists():
        raise MembershipPolicyError(
            {"detail": [_("Cannot remove the last organization membership.")]}
        )
    if membership.is_active and active_owner_count(org, exclude_pk=membership.pk) < 1:
        raise MembershipPolicyError(
            {"detail": [_("Cannot remove the organization owner membership.")]}
        )


@transaction.atomic
def create_org_membership(
    *,
    organization: Organization,
    user,
    role: str,
    is_active: bool = True,
) -> Membership:
    assert_role_assignable(role)
    if Membership.objects.filter(user=user, organization=organization).exists():
        raise MembershipPolicyError(
            {"user": [_("User is already a member of this organization.")]}
        )
    if is_active:
        from apps.subscription.services.interface import enforce_license_quota

        enforce_license_quota(organization, "max_users", additional=1)
        # The quota lock may have waited for another request that added this
        # same user. Recheck the committed affiliation before attempting the
        # unique (user, organization) insert.
        if Membership.objects.filter(user=user, organization=organization).exists():
            raise MembershipPolicyError(
                {"user": [_("User is already a member of this organization.")]}
            )
    # Always forward role= so inactive rows still seed ee_member_role for later activate.
    try:
        # Keep the insert in a savepoint so an inactive duplicate race can be
        # translated without leaving the outer service transaction unusable.
        with transaction.atomic():
            membership = Membership.objects.create(
                user=user,
                organization=organization,
                is_active=is_active,
                role=role,
            )
    except IntegrityError as exc:
        if Membership.objects.filter(user=user, organization=organization).exists():
            raise MembershipPolicyError(
                {"user": [_("User is already a member of this organization.")]}
            ) from exc
        raise
    return membership


@transaction.atomic
def update_org_membership(membership: Membership, **fields) -> Membership:
    # Re-read under a row lock so concurrent retries evaluate the committed
    # affiliation state instead of both consuming quota from stale instances.
    membership = (
        Membership.objects.select_for_update()
        .select_related("organization")
        .get(pk=membership.pk)
    )
    new_role = fields.get("role")
    if new_role is not None:
        assert_role_assignable(new_role)

    was_active = membership.is_active
    if "is_active" in fields and fields["is_active"] is not None:
        new_active = bool(fields["is_active"])
        if membership.is_active and not new_active:
            assert_can_deactivate(membership)
        if (not membership.is_active) and new_active:
            from apps.subscription.services.interface import enforce_license_quota

            enforce_license_quota(membership.organization, "max_users", additional=1)
        membership.is_active = new_active
        membership.save(update_fields=["is_active"])

    # Keep plugin role rows on deactivate; always sync explicit role updates
    # (including inactive seeds) so PATCH role is never a silent no-op.
    if new_role is not None:
        sync_member_role(
            user_id=membership.user_id,
            organization_id=membership.organization_id,
            role=new_role,
        )
    elif not was_active and membership.is_active and get_authz_provider() is not None:
        # Reactivate without role: require a stored EE row (seeded at create/deactivate).
        provider = get_authz_provider()
        peek = getattr(provider, "peek_stored_role", None)
        stored = (
            peek(
                user_id=membership.user_id,
                organization_id=membership.organization_id,
            )
            if peek is not None
            else None
        )
        if not stored:
            raise MembershipPolicyError(
                {
                    "role": [
                        _(
                            "Cannot activate membership without a role. "
                            "Set role explicitly or recreate the member."
                        )
                    ]
                }
            )
    return membership
