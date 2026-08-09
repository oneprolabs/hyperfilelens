"""Instance Public Gateway count cap (license layer 1)."""

from __future__ import annotations

from apps.subscription.constants import DEFAULT_LIMITS, UNLIMITED
from common.errors import AppError

_PUBLIC_GATEWAY_COUNT_FULL = (
    "Public Data Gateway count is full for this deployment. "
    "Activate a larger instance license or retire an unused Public Gateway."
)


def resolve_max_public_gateways() -> int:
    """License.max_public_gateways when active; else DEFAULT_LIMITS (unsigned default grant)."""
    try:
        from apps.subscription.services.internal.license_ops import get_instance_active_license

        lic = get_instance_active_license()
        if lic is not None:
            return int(getattr(lic, "max_public_gateways", DEFAULT_LIMITS["max_public_gateways"]))
    except Exception:  # pragma: no cover
        pass
    return int(DEFAULT_LIMITS["max_public_gateways"])


def count_public_gateways() -> int:
    from apps.lens_bridge.services.platform_lens import platform_gateway_links

    return int(platform_gateway_links().count())


def assert_public_gateway_count_available(*, additional: int = 1) -> None:
    """Reject when adding ``additional`` Public Gateways would exceed the instance cap."""
    from apps.subscription.services.quota import hard_quota_enforcement_active

    if not hard_quota_enforcement_active():
        return
    cap = resolve_max_public_gateways()
    if cap == UNLIMITED or cap < 0:
        return
    used = count_public_gateways()
    if used + int(additional) > cap:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_PUBLIC_GATEWAY_COUNT_FULL,
            diagnostic=_PUBLIC_GATEWAY_COUNT_FULL,
            meta={
                "quota_type": "max_public_gateways",
                "limit": cap,
                "used": used,
                "requested": int(additional),
                "scope": "instance",
            },
        )
