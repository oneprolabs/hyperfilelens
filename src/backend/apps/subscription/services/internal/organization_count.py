"""Instance-level customer organization count (not org-split)."""

from __future__ import annotations

from apps.subscription.constants import DEFAULT_LIMITS, UNLIMITED
from common.errors import AppError

_ORG_COUNT_FULL = (
    "Organization limit reached for this deployment. Contact your platform "
    "administrator to raise the instance grant."
)


def _platform_org_key() -> str:
    try:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY

        return PLATFORM_ORG_KEY
    except Exception:  # pragma: no cover
        return "__platform_lens__"


def count_customer_organizations() -> int:
    from apps.iam.models import Organization

    return int(
        Organization.objects.filter(is_active=True)
        .exclude(key=_platform_org_key())
        .count()
    )


def resolve_max_organizations() -> int:
    """Resolve the active instance entitlement for customer organizations."""
    from common.extension_spi import get_quota_provider

    provider = get_quota_provider()
    resolver = getattr(provider, "get_instance_limit", None)
    if callable(resolver):
        return int(resolver("max_organizations"))
    from apps.subscription.services.internal.license_ops import get_instance_active_license

    lic = get_instance_active_license()
    if lic is not None:
        return int(getattr(lic, "max_organizations", DEFAULT_LIMITS["max_organizations"]))
    return int(DEFAULT_LIMITS["max_organizations"])


def assert_organization_count_available(*, additional: int = 1) -> None:
    """Reject when adding ``additional`` customer orgs would exceed the instance cap."""
    from apps.subscription.services.quota import hard_quota_enforcement_active
    from common.extension_spi import get_quota_provider

    if not hard_quota_enforcement_active():
        return
    requested = int(additional)
    if requested < 0:
        raise ValueError("Organization quota consumption cannot be negative")
    provider = get_quota_provider()
    if provider is not None:
        provider.check_quota(None, "max_organizations", requested)
        return
    cap = resolve_max_organizations()
    if cap == UNLIMITED or cap < 0:
        return
    used = count_customer_organizations()
    if used + requested > cap:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_ORG_COUNT_FULL,
            diagnostic=_ORG_COUNT_FULL,
            meta={
                "quota_type": "max_organizations",
                "limit": cap,
                "used": used,
                "requested": requested,
                "scope": "instance",
            },
        )
