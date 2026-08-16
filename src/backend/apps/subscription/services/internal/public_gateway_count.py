"""Instance Public Gateway count cap (license layer 1)."""

from __future__ import annotations

from apps.subscription.constants import DEFAULT_LIMITS, UNLIMITED
from common.errors import AppError

_PUBLIC_GATEWAY_COUNT_FULL = (
    "Public Data Gateway count is full for this deployment. "
    "Activate a larger instance license or retire an unused Public Gateway."
)


def resolve_max_public_gateways() -> int:
    """Resolve the active instance entitlement for Public Gateways."""
    from common.extension_spi import get_quota_provider

    provider = get_quota_provider()
    resolver = getattr(provider, "get_instance_limit", None)
    if callable(resolver):
        return int(resolver("max_public_gateways"))
    from apps.subscription.services.internal.license_ops import get_instance_active_license

    lic = get_instance_active_license()
    if lic is not None:
        return int(getattr(lic, "max_public_gateways", DEFAULT_LIMITS["max_public_gateways"]))
    return int(DEFAULT_LIMITS["max_public_gateways"])


def count_public_gateways() -> int:
    from apps.lens_bridge.services.platform_lens import platform_gateway_links

    return int(platform_gateway_links().count())


def assert_public_gateway_count_available(*, additional: int = 1) -> None:
    """Reject when adding ``additional`` Public Gateways would exceed the instance cap."""
    from apps.subscription.services.quota import hard_quota_enforcement_active
    from common.extension_spi import get_quota_provider

    if not hard_quota_enforcement_active():
        return
    requested = int(additional)
    if requested < 0:
        raise ValueError("Public Gateway quota consumption cannot be negative")
    provider = get_quota_provider()
    if provider is not None:
        provider.check_quota(None, "max_public_gateways", requested)
        return
    cap = resolve_max_public_gateways()
    if cap == UNLIMITED or cap < 0:
        return
    used = count_public_gateways()
    if used + requested > cap:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_PUBLIC_GATEWAY_COUNT_FULL,
            diagnostic=_PUBLIC_GATEWAY_COUNT_FULL,
            meta={
                "quota_type": "max_public_gateways",
                "limit": cap,
                "used": used,
                "requested": requested,
                "scope": "instance",
            },
        )
