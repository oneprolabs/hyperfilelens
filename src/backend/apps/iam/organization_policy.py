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
# Permission families that represent product capabilities rather than merely
# organization RBAC.  Keep this mapping next to the organization policy so
# the backend can enforce Personal Edition boundaries even when a stored Role
# was created before the capability was introduced.
PERMISSION_CAPABILITIES = {
    "configuration.managed_storage": "managed_storage",
    "protection.object_storage_repositories": "object_storage",
    "protection.target_nas_repositories": "target_nas",
    "protection.local_disk_repositories": "local_disks",
    **SOURCE_PERMISSION_CAPABILITIES,
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
        return self.allows_permission(permission)

    def allows_permission(self, permission: str) -> bool:
        """Return whether a Role permission is supported by this organization.

        Managed repositories intentionally remain available in Personal
        Edition even though direct, user-configured Object Storage does not:
        the managed-storage API applies the separate administration boundary.
        """
        prefix = str(permission).rsplit(".", 1)[0]
        capability = PERMISSION_CAPABILITIES.get(prefix)
        if prefix == "protection.object_storage_repositories" and self.managed_storage:
            return True
        return capability is None or self.capabilities()[capability]

    def filter_permissions(self, permissions) -> frozenset[str]:
        """Remove unsupported product capabilities from a stored Role bundle."""
        return frozenset(
            permission for permission in permissions if self.allows_permission(permission)
        )


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
