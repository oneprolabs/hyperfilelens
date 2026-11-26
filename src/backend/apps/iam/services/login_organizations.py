"""Stable login defaults, restricted to the account's active memberships."""

from django.contrib.auth.models import User
from django.db.models import Case, IntegerField, QuerySet, Value, When

from apps.iam.models import Membership
from common.extension_spi import get_authz_provider


def login_memberships(user: User) -> QuerySet[Membership]:
    """Share one fallback order across password, code, OAuth, and profiles."""
    rows = Membership.objects.filter(
        user=user, user__is_active=True, is_active=True, organization__is_active=True,
    ).select_related("organization")
    preferred = getattr(get_authz_provider(), "preferred_installation_organization", None)
    key = preferred(user) if callable(preferred) else None
    if key:
        rows = rows.annotate(
            login_priority=Case(
                When(organization__key=key, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        ).order_by("login_priority", "organization__key", "organization_id")
    else:
        rows = rows.order_by("organization__key", "organization_id")
    return rows
