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
    """License.max_organizations when active; else DEFAULT_LIMITS."""
    try:
        from apps.subscription.services.internal.license_ops import get_instance_active_license

        lic = get_instance_active_license()
        if lic is not None:
            return int(getattr(lic, "max_organizations", DEFAULT_LIMITS["max_organizations"]))
    except Exception:
        pass
    return int(DEFAULT_LIMITS["max_organizations"])


def assert_organization_count_available(*, additional: int = 1) -> None:
    """Reject when adding ``additional`` customer orgs would exceed the instance cap."""
    from apps.subscription.services.quota import hard_quota_enforcement_active

    if not hard_quota_enforcement_active():
        return
    cap = resolve_max_organizations()
    if cap == UNLIMITED or cap < 0:
        return
    used = count_customer_organizations()
    if used + int(additional) > cap:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_ORG_COUNT_FULL,
            diagnostic=_ORG_COUNT_FULL,
            meta={
                "quota_type": "max_organizations",
                "limit": cap,
                "used": used,
                "requested": int(additional),
                "scope": "instance",
            },
        )
