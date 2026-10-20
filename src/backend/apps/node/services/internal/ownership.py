"""Explicit ownership selectors for infrastructure, never optional tenant filters."""

from typing import Any

from apps.iam.models import Organization
from apps.node.models import Node


def node_owner_filter(organization: Organization | None) -> dict[str, Any]:
    """None explicitly selects platform Nodes at trusted infrastructure boundaries."""
    return {
        "organization": organization,
        "scope": Node.Scope.PLATFORM if organization is None else Node.Scope.ORGANIZATION,
    }


def owner_id(organization: Organization | None) -> int | None:
    """Return a real tenant ID or no tenant for platform infrastructure."""
    return organization.pk if organization is not None else None


def lock_platform_infrastructure() -> None:
    """Serialize platform default/enrollment changes without a synthetic tenant."""
    from django.db import connection

    if connection.vendor == "postgresql":
        if not connection.in_atomic_block:
            raise RuntimeError("Platform infrastructure lock requires a transaction")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [874_522_309])
