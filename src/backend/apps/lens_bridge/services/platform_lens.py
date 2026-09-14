"""Platform-scoped SourceLens resources (hidden system org)."""

from __future__ import annotations

from django.db import transaction

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services.gateway_readiness import (
    gateway_runtime_state,
    require_hfl_usable_gateway,
)
from apps.lens_bridge.services.gateway_ownership import organization_gateway_links

PLATFORM_ORG_KEY = "__platform_lens__"
PLATFORM_ORG_NAME = "Platform Lens"
NO_PUBLIC_DATA_GATEWAY_AVAILABLE = (
    "No public Data Gateway is available. Select a private Data Gateway "
    "or contact your administrator."
)


def get_or_create_platform_org() -> Organization:
    org, created = Organization.objects.get_or_create(
        key=PLATFORM_ORG_KEY,
        defaults={
            "name": PLATFORM_ORG_NAME,
            "is_active": True,
        },
    )
    if created:
        from apps.subscription.services.interface import initialize_organization_quota

        initialize_organization_quota(org)
    return org


def platform_gateway_links():
    org = get_or_create_platform_org()
    return LensGatewayLink.objects.filter(
        organization=org,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
    ).select_related("gateway")


def _first_eligible(links) -> LensGatewayLink | None:
    for link in links:
        if gateway_runtime_state(link)["copilot_eligible"]:
            return link
    return None


def resolve_platform_default_gateway_link() -> LensGatewayLink | None:
    """Resolve Auto to the first HFL-ready platform gateway in stable list order."""
    org = get_or_create_platform_org()
    return _first_eligible(
        LensGatewayLink.objects.filter(
            organization=org,
            scope=LensGatewayLink.GatewayScope.PLATFORM,
            sl_lensnode_uuid__isnull=False,
            is_deleted=False,
        )
        .select_related("gateway")
        .order_by("-is_platform_default", "created_at", "id")
    )


def resolve_organization_default_gateway_link(
    *, organization: Organization
) -> LensGatewayLink | None:
    """Resolve the first HFL-ready Private Gateway in an organization."""
    return _first_eligible(
        organization_gateway_links(organization=organization)
        .filter(sl_lensnode_uuid__isnull=False)
        .order_by("created_at", "id")
    )


def resolve_auto_gateway_link_for_copilot(*, user) -> LensGatewayLink | None:
    """Resolve Chat Auto to an HFL-ready platform gateway only."""
    return resolve_platform_default_gateway_link()


def resolve_gateway_link_for_copilot(
    org: Organization,
    *,
    user,
    gateway_link_id: int | None = None,
) -> LensGatewayLink:
    """Resolve DG for Copilot. Prefer platform pool; Auto → platform default.

    Never creates or deletes a DG — only selects an existing SL-admin gateway link.
    """
    from rest_framework.exceptions import ValidationError

    if gateway_link_id:
        link = (
            platform_gateway_links()
            .filter(pk=gateway_link_id, sl_lensnode_uuid__isnull=False)
            .first()
        )
        if link is None:
            link = (
                organization_gateway_links(organization=org)
                .filter(pk=gateway_link_id, sl_lensnode_uuid__isnull=False)
                .first()
            )
        if link is None:
            raise ValidationError({"gateway_link_id": "Data gateway is not available."})
        from apps.lens_bridge.services.gateway_execution import context_for_gateway_link

        context_for_gateway_link(
            tenant_organization=org,
            gateway_link=link,
        )
        return link

    platform_default = resolve_auto_gateway_link_for_copilot(user=user)
    if platform_default is not None:
        return platform_default

    raise ValidationError(
        {"gateway_link_id": NO_PUBLIC_DATA_GATEWAY_AVAILABLE}
    )


@transaction.atomic
def set_platform_default_gateway(*, gateway_link_id: int) -> LensGatewayLink:
    org = get_or_create_platform_org()
    Organization.objects.select_for_update().get(pk=org.pk)
    link = LensGatewayLink.objects.select_for_update().get(
        pk=gateway_link_id,
        organization=org,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
    )
    require_hfl_usable_gateway(link)
    LensGatewayLink.objects.filter(
        organization=org,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
        is_platform_default=True,
    ).exclude(pk=link.pk).update(is_platform_default=False)
    link.is_platform_default = True
    link.save(update_fields=["is_platform_default", "updated_at"])
    return link
