"""Initialize optional Enterprise Role presets only for newly created tenants."""

from typing import Any

from common.extension_spi import get_authz_provider


def seed_organization_roles(organization: Any) -> None:
    """Delegate initial Role definitions without granting implicit capabilities."""
    seed = getattr(get_authz_provider(), "seed_organization_roles", None)
    if callable(seed):
        seed(organization)
