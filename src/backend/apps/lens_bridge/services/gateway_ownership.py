"""Organization ownership semantics for Private Data Gateways."""

from __future__ import annotations

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink


# ``user`` remains a supported persistence value while blue/green deployments
# may still run code from before Private Data Gateways became organization
# resources. Both values have identical authorization semantics in new code.
PRIVATE_GATEWAY_SCOPES = (
    LensGatewayLink.GatewayScope.ORGANIZATION,
    LensGatewayLink.GatewayScope.USER,
)


def is_private_gateway(link: LensGatewayLink) -> bool:
    return link.scope in PRIVATE_GATEWAY_SCOPES


def organization_gateway_links(*, organization: Organization):
    """Return Private Data Gateways owned by one organization."""

    return LensGatewayLink.objects.filter(
        organization=organization,
        scope__in=PRIVATE_GATEWAY_SCOPES,
        is_deleted=False,
    ).select_related("gateway", "created_by")


def external_gateway_scope(link: LensGatewayLink) -> str:
    """Return the stable API scope while legacy rows remain in storage."""

    if is_private_gateway(link):
        return LensGatewayLink.GatewayScope.ORGANIZATION
    return str(link.scope)


def persistence_scope_for_private_gateway() -> str:
    """Keep new rows readable by the previous blue/green application version."""

    return LensGatewayLink.GatewayScope.USER
