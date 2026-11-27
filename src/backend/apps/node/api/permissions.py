from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.iam.permissions_org import IsOrgOperator, IsOrgReader, IsOrgStaffReader, IsOrgWriter
from common.extension_spi import get_authz_provider


def require_gateway_enrollment_creation(request, organization, role: str) -> None:
    """Group grants on existing gateways do not authorize new enrollment."""
    if role != "gateway":
        return
    provider = get_authz_provider()
    if provider is not None and not provider.has_org_action(
        request.user, organization.key, "insights.private_data_gateways.manage",
    ):
        raise PermissionDenied()


__all__ = [
    "AllowAny",
    "IsAuthenticated",
    "IsOrgOperator",
    "IsOrgReader",
    "IsOrgStaffReader",
    "IsOrgWriter",
    "require_gateway_enrollment_creation",
]
