"""Permissions for instance settings APIs (OSS Admin Console essentials)."""

from __future__ import annotations

from typing import Sequence

from rest_framework.permissions import BasePermission

from common.deploy.site import platform_ops_api_allowed
from common.platform_authz import has_any_platform_permission, has_platform_permission


class IsInstanceSettingsStaff(BasePermission):
    """Ops-site entry gate (staff + platform role when AuthzProvider present)."""

    message = "Instance settings access denied."

    def has_permission(self, request, view):
        return platform_ops_api_allowed(request)


class HasPlatformPermission(BasePermission):
    """Require ops-site access plus one (or all) platform action(s).

    Community (no AuthzProvider): staff bootstrap grants the full catalog.
    Enterprise: EE AuthzProvider enforces role → actions.
    """

    message = "Platform permission denied."
    actions: tuple[str, ...] = ()
    mode: str = "any"  # any | all

    @classmethod
    def for_actions(cls, *actions: str, mode: str = "any") -> type[BasePermission]:
        label = "_".join(a.rsplit(".", 1)[-1] for a in actions) or "none"
        return type(
            f"HasPlatformPerm_{label}",
            (cls,),
            {"actions": tuple(actions), "mode": mode},
        )

    def _actions(self, view) -> Sequence[str]:
        view_actions = getattr(view, "platform_permissions", None)
        if view_actions:
            return tuple(str(a) for a in view_actions)
        return self.actions

    def has_permission(self, request, view):
        if not platform_ops_api_allowed(request):
            return False
        actions = self._actions(view)
        if not actions:
            return False
        if self.mode == "all":
            return all(
                has_platform_permission(request.user, action) for action in actions
            )
        return has_any_platform_permission(request.user, actions)
