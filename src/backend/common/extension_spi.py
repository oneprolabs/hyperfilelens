"""Host SPI registry for extension (plugin) providers.

Plugins register implementations; Host business code only calls getters.
Dependency: plugin → Host only.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence


class AuthzProvider(Protocol):
    """Org + platform authorization (typically one commercial plugin)."""

    def is_org_member(self, user: Any, org_key: str) -> bool: ...

    def get_org_role(self, user: Any, org_key: str) -> str | None:
        """Return authoritative role for user in org, or None if not a member."""
        ...

    def has_org_role(self, user: Any, org_key: str, roles: Sequence[str]) -> bool:
        """True when user's org role is one of ``roles``."""
        ...

    def sync_member_role(
        self, *, user_id: int, organization_id: int, role: str | None
    ) -> None:
        """Upsert plugin role row, or delete when ``role`` is None."""
        ...

    def count_active_owners(
        self, organization: Any, *, exclude_user_id: int | None = None
    ) -> int:
        """Count owner-authority rows for an organization."""
        ...

    def resolve_owner_email(self, organization: Any) -> str | None:
        """Email of the primary org owner, if any."""
        ...

    def peek_stored_role(
        self, *, user_id: int, organization_id: int
    ) -> str | None:
        """Return stored plugin role even when affiliation is inactive (display)."""
        ...

    def get_platform_role(self, user: Any) -> str | None:
        """Platform Console role key, or None when user has no platform access."""
        ...

    def has_platform_permission(self, user: Any, action: str) -> bool:
        """True when ``user`` may perform platform action ``action``."""
        ...

    def list_platform_permissions(self, user: Any) -> Sequence[str]:
        """Actions granted to ``user`` for deploy-profile / UI gating."""
        ...


class QuotaProvider(Protocol):
    """Quota enforcement and EffectiveQuota surface (commercial plugin).

    Community (no provider): Host facade is a no-op / informational defaults.
    Enterprise: hard checks + limits/validate/activate hooks.
    """

    def check_quota(
        self, organization: Any, resource_type: str, additional: int = 1
    ) -> Any:
        """Deny path for new consumption when over quota (may raise AppError)."""
        ...

    def get_limits(self, organization: Any) -> dict[str, int]:
        """Authoritative org limits map for UI / gateway select caps."""
        ...

    def validate_quota(
        self, organization: Any, quota_type: str, amount: int = 1
    ) -> dict:
        """Soft preview: is_valid, limit/used, enforcement_enabled, message."""
        ...

    def on_license_activated(self, organization: Any, license_obj: Any) -> None:
        """After activate: ensure policy knobs; keep resource meters shared; validate pool."""
        ...


_authz_provider: AuthzProvider | None = None
_quota_provider: QuotaProvider | None = None


def register_authz_provider(provider: AuthzProvider | None) -> None:
    global _authz_provider
    if provider is not None and _authz_provider is not None:
        raise RuntimeError("AuthzProvider already registered (singleton slot)")
    _authz_provider = provider


def register_quota_provider(provider: QuotaProvider | None) -> None:
    global _quota_provider
    if provider is not None and _quota_provider is not None:
        raise RuntimeError("QuotaProvider already registered (singleton slot)")
    _quota_provider = provider


def get_authz_provider() -> AuthzProvider | None:
    return _authz_provider


def get_quota_provider() -> QuotaProvider | None:
    return _quota_provider


def clear_providers_for_tests() -> tuple[AuthzProvider | None, QuotaProvider | None]:
    """Test helper: clear singleton slots; return previous providers for restore."""
    global _authz_provider, _quota_provider
    previous = (_authz_provider, _quota_provider)
    _authz_provider = None
    _quota_provider = None
    return previous


def restore_providers_for_tests(
    previous: tuple[AuthzProvider | None, QuotaProvider | None],
) -> None:
    """Test helper: restore providers saved by ``clear_providers_for_tests``."""
    global _authz_provider, _quota_provider
    _authz_provider, _quota_provider = previous
