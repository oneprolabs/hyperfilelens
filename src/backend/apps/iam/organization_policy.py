"""Organization product boundaries, independent of roles and resource scope."""

from dataclasses import dataclass
from typing import Any, Literal

from rest_framework.exceptions import PermissionDenied

from common.extension_spi import get_authz_provider

DIRECT_REPOSITORY_CAPABILITIES = {
    "s3": "object_storage", "nas": "target_nas", "proxy_fs": "local_disks",
}
SOURCE_PERMISSION_CAPABILITIES = {
    "protection.source_nas": "source_nas",
    "protection.proxy_hosts": "proxy_hosts",
    "protection.tags": "source_tags",
}


@dataclass(frozen=True)
class OrganizationPolicy:
    """A derived product policy, not another persisted organization identity."""

    kind: Literal["system_default", "personal", "tenant"] = "tenant"
    mode: Literal["standard", "personal"] = "standard"
    managed_storage: bool = False
    object_storage: bool = True
    target_nas: bool = True
    local_disks: bool = True
    source_nas: bool = True
    proxy_hosts: bool = True
    source_tags: bool = True

    def capabilities(self) -> dict[str, bool]:
        return {
            key: getattr(self, key)
            for key in (
                "managed_storage", "object_storage", "target_nas", "local_disks",
                "source_nas", "proxy_hosts", "source_tags",
            )
        }

    def allows_source_permission(self, permission: str) -> bool:
        """Product availability for source operations, independent of RBAC."""
        capability = SOURCE_PERMISSION_CAPABILITIES.get(permission.rsplit(".", 1)[0])
        return capability is None or self.capabilities()[capability]


def resolve_organization_policy(
    organization_id: int, *, request: Any = None,
) -> OrganizationPolicy:
    """Resolve once per request; provider failures never become broad grants."""
    provider = get_authz_provider()
    key = (id(provider), organization_id)
    cached = getattr(request, "_hfl_organization_policy", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    resolver = getattr(provider, "organization_policy", None)
    if provider is None:
        policy = OrganizationPolicy()
    elif not callable(resolver):
        raise PermissionDenied("Organization capabilities are unavailable.")
    else:
        policy = resolver(organization_id=organization_id)
    if not isinstance(policy, OrganizationPolicy):
        raise PermissionDenied("Organization capabilities are unavailable.")
    if request is not None:
        request._hfl_organization_policy = (key, policy)
    return policy


def require_organization_capability(
    organization_id: int, capability: str, *, request: Any = None,
) -> None:
    """Check the product boundary; callers must still perform user authorization."""
    policy = resolve_organization_policy(organization_id, request=request)
    if policy.capabilities().get(capability) is not True:
        raise PermissionDenied("This organization does not provide that capability.")
