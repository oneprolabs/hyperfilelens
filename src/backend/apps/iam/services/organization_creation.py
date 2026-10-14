"""Account-level self-service organizations, independent of the current role."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext as _

from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import interface as audit_services
from apps.configuration.selectors.interface import get_config
from apps.iam.models import Membership, Organization
from apps.iam.services.account_setup import assert_account_ready
from apps.iam.services.member_onboarding import lock_identity_provisioning
from apps.iam.services.registration_service import unique_org_key_for_name
from common.extension_spi import get_authz_provider


def organization_creation_options(user: Any) -> dict:
    provider = get_authz_provider()
    owned = (
        sum(
            provider.get_org_role(user, key) == "owner"
            for key in Membership.objects.filter(
                user=user,
                organization__is_active=True,
            ).values_list("organization__key", flat=True)
        )
        if provider
        else 0
    )
    limit = max(
        1, int(get_config("iam.organizations.max_owned_per_account", default=10))
    )
    allowed = bool(
        user.is_active
        and not user.is_staff
        and not user.is_superuser
        and provider
        and get_config("iam.organizations.self_service_enabled", default=True)
    )
    return {
        "can_create": allowed and owned < limit,
        "owned_count": owned,
        "owned_limit": limit,
    }


@transaction.atomic
def create_user_organization(request: Any, *, name: str) -> Organization:
    assert_account_ready(request.user)
    # Serialize per-account limits, instance quotas and unique key generation.
    lock_identity_provisioning()
    options = organization_creation_options(request.user)
    if not options["can_create"]:
        raise PermissionDenied(
            _(
                "Organization creation is unavailable or the account limit has been reached."
            )
        )
    name = name.strip()
    if not name or len(name) > 200:
        raise ValidationError(
            {"name": _("Enter an organization name of at most 200 characters.")}
        )
    from apps.subscription.services.internal.organization_count import (
        assert_organization_count_available,
    )
    from apps.subscription.services.interface import (
        enforce_license_quota,
        initialize_organization_quota,
    )

    assert_organization_count_available(additional=1)
    org = Organization.objects.create(key=unique_org_key_for_name(name), name=name)
    initialize_organization_quota(org)
    enforce_license_quota(org, "max_users", additional=1)
    Membership.objects.create(
        user=request.user, organization=org, role="owner", is_active=True
    )
    from apps.iam.services.role_seeding import seed_organization_roles

    seed_organization_roles(org)
    audit_services.write_audit_log_from_request(
        request,
        organization=org,
        action="organization.create",
        resource_type="organization",
        resource_id=str(org.pk),
        resource_name=name,
        changes={"owner_id": request.user.pk},
    )
    return org
