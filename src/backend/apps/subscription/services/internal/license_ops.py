"""License activation and lookup."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone as django_timezone

from apps.iam.models import Organization
from apps.subscription.constants import DEFAULT_LIMITS, UNLIMITED
from apps.subscription.models import License, MachineCode
from apps.subscription.services.internal.crypto import verify_activation_code
from apps.subscription.services.internal.machine_code import generate_machine_code
from apps.subscription.services.internal.usage import collect_usage_stats


_MAX_LICENSE_LIMIT = 2**31 - 1


def _normalize_license_limit(*, field: str, value) -> int:
    """Accept only values representable by the persisted license fields."""
    if isinstance(value, bool):
        raise ValueError(f"License limit {field} must be an integer")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"License limit {field} must be an integer") from exc
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise ValueError(f"License limit {field} must be an integer")
    limit = int(numeric)
    if limit < UNLIMITED:
        raise ValueError(f"License limit {field} cannot be less than {UNLIMITED}")
    if limit > _MAX_LICENSE_LIMIT:
        raise ValueError(f"License limit {field} exceeds the supported range")
    return limit


def _map_limits(raw: dict) -> dict:
    """Map xxz limit keys to organization license fields."""
    if not isinstance(raw, dict):
        raise ValueError("License limits must be an object")
    mapped = {
        "max_organizations": raw.get(
            "max_organizations", raw.get("max_tenants", DEFAULT_LIMITS["max_organizations"])
        ),
        "max_users": raw.get("max_users", DEFAULT_LIMITS["max_users"]),
        "max_nodes": raw.get("max_nodes", raw.get("max_proxies", DEFAULT_LIMITS["max_nodes"])),
        "max_storage_gb": raw.get("max_storage_gb", DEFAULT_LIMITS["max_storage_gb"]),
        "max_gateways": raw.get("max_gateways", DEFAULT_LIMITS["max_gateways"]),
        "max_public_gateways": raw.get(
            "max_public_gateways", DEFAULT_LIMITS["max_public_gateways"]
        ),
        "max_public_gateway_capacity_gb": raw.get(
            "max_public_gateway_capacity_gb",
            DEFAULT_LIMITS["max_public_gateway_capacity_gb"],
        ),
        "max_source_nas": raw.get("max_source_nas", DEFAULT_LIMITS["max_source_nas"]),
        "max_object_storage": raw.get(
            "max_object_storage", DEFAULT_LIMITS["max_object_storage"]
        ),
        "max_target_nas": raw.get("max_target_nas", DEFAULT_LIMITS["max_target_nas"]),
        "max_standalone_disk": raw.get(
            "max_standalone_disk", DEFAULT_LIMITS["max_standalone_disk"]
        ),
        "max_protected_sources": raw.get(
            "max_protected_sources", DEFAULT_LIMITS["max_protected_sources"]
        ),
        "ai_insights_quota": raw.get(
            "ai_tokens",
            raw.get(
                "ai_requests",
                raw.get(
                    "ai_insights_quota",
                    DEFAULT_LIMITS["ai_insights_quota"],
                ),
            ),
        ),
        # Legacy License column only — Tasks are not enforced as a quota meter.
        "max_tasks": raw.get("max_tasks", raw.get("max_backup_tasks", 0)),
        "max_alert_policies": raw.get(
            "max_alert_policies",
            raw.get("max_policies", DEFAULT_LIMITS["max_alert_policies"]),
        ),
    }
    return {
        field: _normalize_license_limit(field=field, value=value)
        for field, value in mapped.items()
    }


def _dev_unlimited_limits() -> dict:
    limits = {k: UNLIMITED for k in DEFAULT_LIMITS}
    # Keep License.max_tasks writable for DEV codes; still not a quota meter.
    limits["max_tasks"] = UNLIMITED
    return limits


def _normalize_features(raw) -> list[str]:
    if raw is None:
        # Legacy activation payloads predate feature entitlements and granted
        # the complete Enterprise product. Preserve that meaning on renewal.
        return ["*"]
    if not isinstance(raw, (list, tuple, set)):
        raise ValueError("License features must be a list")
    return sorted({str(item).strip() for item in raw if str(item).strip()})


def get_or_create_machine_code(*, organization: Organization, user, force: bool = False) -> str:
    existing = MachineCode.objects.filter(organization=organization).first()
    if existing and not force:
        return existing.code
    code, components = generate_machine_code(
        organization_id=organization.id,
        user_id=user.id if user and user.is_authenticated else 0,
    )
    if existing:
        existing.code = code
        existing.hostname = components.get("hostname", "")
        existing.source = components.get("source", "")
        existing.user = user if user and user.is_authenticated else None
        existing.save(update_fields=["code", "hostname", "source", "user"])
        return code
    MachineCode.objects.create(
        code=code,
        organization=organization,
        user=user if user and user.is_authenticated else None,
        hostname=components.get("hostname", ""),
        source=components.get("source", ""),
    )
    return code


def resolve_instance_license_organization() -> Organization | None:
    """
    Org that holds the deployment instance grant.

    Prefer an organization that already has a License row, otherwise the oldest
    active customer org. Excludes Platform Lens when that key is available.
    """
    try:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
    except Exception:  # pragma: no cover
        PLATFORM_ORG_KEY = "__platform_lens__"

    licensed = (
        License.objects.select_related("organization")
        .exclude(organization__key=PLATFORM_ORG_KEY)
        .order_by("organization_id", "activated_at")
        .first()
    )
    if licensed is not None:
        return licensed.organization

    return (
        Organization.objects.filter(is_active=True)
        .exclude(key=PLATFORM_ORG_KEY)
        .order_by("id")
        .first()
    )


def get_active_license(*, organization: Organization) -> License | None:
    try:
        lic = organization.license
    except License.DoesNotExist:
        return None
    if lic.is_valid:
        return lic
    if (
        lic.expires_at
        and lic.expires_at < django_timezone.now()
        and lic.status == License.Status.ACTIVE
    ):
        lic.status = License.Status.EXPIRED
        lic.save(update_fields=["status", "updated_at"])
    return lic if lic.is_valid else None


def get_instance_active_license() -> License | None:
    """Active deployment grant, regardless of the caller's current organization."""
    record = get_instance_license_record()
    if record is None:
        return None
    return get_active_license(organization=record.organization)


def get_instance_license_record() -> License | None:
    """Deployment license row regardless of status or expiry."""
    host = resolve_instance_license_organization()
    if host is None:
        return None
    return License.objects.filter(organization=host).select_related("organization").first()


def build_current_payload(
    *,
    organization: Organization,
    user,
    include_machine_code: bool = True,
) -> dict:
    machine_code = (
        get_or_create_machine_code(organization=organization, user=user)
        if include_machine_code
        else None
    )
    license_obj = get_active_license(organization=organization)
    usage = collect_usage_stats(organization_id=organization.id)
    from common.extension_spi import get_quota_provider

    # EE QuotaProvider enforces the built-in or licensed instance entitlement
    # plus the organization plan/override ceiling. Community stays informational.
    instance_lic = get_instance_active_license()
    instance_record = get_instance_license_record()
    provider = get_quota_provider()
    enforcement_enabled = provider is not None

    def _limits_for(org: Organization, fallback_lic: License | None) -> dict:
        # When a provider is registered, UI must match the organization plan
        # with any per-meter override.
        if provider is not None:
            return dict(provider.get_limits(org) or {})
        if fallback_lic is not None:
            return fallback_lic.get_limits()
        return dict(DEFAULT_LIMITS)

    if license_obj is not None:
        return {
            "is_valid": license_obj.is_valid,
            "license": license_obj,
            "entitlement_source": "license",
            "limits": _limits_for(organization, license_obj),
            "days_until_expiry": license_obj.days_until_expiry,
            "usage": usage,
            "machine_code": machine_code,
            "enforcement_enabled": enforcement_enabled,
        }

    # Secondary tenant (no org-bound License row) under instance licensing:
    # hard checks still use the deployment grant — surface that honestly.
    # Include the instance License object so UIs that expect `license` still
    # show status/expiry; `instance_shared` marks it as not this org's row.
    if instance_lic is not None:
        return {
            "is_valid": instance_lic.is_valid,
            "message": "Using instance license",
            "license": instance_lic,
            "entitlement_source": "license",
            "instance_shared": True,
            "limits": _limits_for(organization, instance_lic),
            "days_until_expiry": instance_lic.days_until_expiry,
            "usage": usage,
            "machine_code": machine_code,
            "organization_name": organization.name,
            "enforcement_enabled": enforcement_enabled,
        }

    if provider is not None and instance_record is None:
        return {
            "is_valid": True,
            "message": "Using built-in Enterprise entitlement",
            "license": None,
            "entitlement_source": "builtin_unlimited",
            "machine_code": machine_code,
            "usage": usage,
            "limits": _limits_for(organization, None),
            "organization_name": organization.name,
            "enforcement_enabled": True,
        }

    if provider is not None and instance_record is not None:
        return {
            "is_valid": False,
            "message": "Instance license is inactive",
            "license": instance_record,
            "entitlement_source": "license_inactive",
            "machine_code": machine_code,
            "usage": usage,
            "limits": _limits_for(organization, None),
            "organization_name": organization.name,
            "enforcement_enabled": True,
            "instance_shared": instance_record.organization_id != organization.id,
        }

    return {
        "is_valid": False,
        "message": "No active license",
        "machine_code": machine_code,
        "usage": usage,
        "limits": _limits_for(organization, None),
        "organization_name": organization.name,
        "enforcement_enabled": enforcement_enabled,
    }


def _feature_change_direction(
    *,
    existing_features: list[str] | None,
    new_features: list[str] | None,
) -> tuple[int, int]:
    """Return upgrade/downgrade counts for an Enterprise feature change."""
    current = {str(item).strip() for item in (existing_features or []) if str(item).strip()}
    incoming = {str(item).strip() for item in (new_features or []) if str(item).strip()}
    if current == incoming:
        return 0, 0
    if "*" in current:
        return (0, 0) if "*" in incoming else (0, 1)
    if "*" in incoming:
        return 1, 0
    if current < incoming:
        return 1, 0
    if incoming < current:
        return 0, 1
    return 1, 1


def _expiry_change_direction(existing_expires_at, new_expires_at) -> tuple[int, int, bool]:
    """Return upgrade/downgrade counts and whether validity was extended."""
    if existing_expires_at == new_expires_at:
        return 0, 0, False
    if existing_expires_at is None:
        return 0, 1, False
    if new_expires_at is None:
        return 1, 0, True
    if new_expires_at < existing_expires_at:
        return 0, 1, False
    return 0, 0, True


def _determine_change_type(
    existing: License,
    new_limits: dict,
    new_expires_at,
    new_features: list[str] | None = None,
) -> tuple[str, str]:
    upgrades = downgrades = 0
    for field, new_val in new_limits.items():
        old_val = getattr(existing, field, 0)
        old_rank = float("inf") if int(old_val) < 0 else int(old_val)
        new_rank = float("inf") if int(new_val) < 0 else int(new_val)
        if new_rank > old_rank:
            upgrades += 1
        elif new_rank < old_rank:
            downgrades += 1
    feature_upgrades, feature_downgrades = _feature_change_direction(
        existing_features=list(existing.features or []),
        new_features=(
            list(existing.features or []) if new_features is None else new_features
        ),
    )
    upgrades += feature_upgrades
    downgrades += feature_downgrades
    expiry_upgrades, expiry_downgrades, validity_extended = (
        _expiry_change_direction(existing.expires_at, new_expires_at)
    )
    upgrades += expiry_upgrades
    downgrades += expiry_downgrades
    if upgrades and not downgrades:
        return License.ChangeType.UPGRADE, "Entitlements upgraded"
    if downgrades and not upgrades:
        return License.ChangeType.DOWNGRADE, "Entitlements downgraded"
    if upgrades or downgrades:
        return License.ChangeType.RENEWAL, "Entitlements updated"
    if validity_extended:
        return License.ChangeType.RENEWAL, "License renewed"
    return License.ChangeType.RENEWAL, "License updated"


def _platform_org_key() -> str:
    try:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY

        return PLATFORM_ORG_KEY
    except Exception:  # pragma: no cover
        return "__platform_lens__"


# Stable advisory lock id for serializing instance-license activation (PostgreSQL).
_INSTANCE_LICENSE_ADVISORY_LOCK = 874_522_301


def _acquire_instance_license_lock() -> None:
    """Serialize first-activation races so two orgs cannot each mint a grant."""
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [_INSTANCE_LICENSE_ADVISORY_LOCK],
            )
        return
    # Backends without advisory locks: lock the oldest customer org row.
    (
        Organization.objects.filter(is_active=True)
        .exclude(key=_platform_org_key())
        .order_by("id")
        .select_for_update()
        .first()
    )


@transaction.atomic
def activate_license(
    *,
    organization: Organization,
    user,
    activation_code: str,
) -> tuple[License, str]:
    code = (activation_code or "").strip()
    if not code:
        raise ValueError("Activation code is required")

    # One deployment grant per instance: once any customer License exists,
    # only that host org (or Platform Ops, which activates on the host) may
    # renew/replace it. Secondary tenants are covered via EffectiveQuota.
    _acquire_instance_license_lock()
    existing_grant = (
        License.objects.exclude(organization__key=_platform_org_key())
        .select_for_update()
        .order_by("organization_id", "activated_at")
        .first()
    )
    if existing_grant is not None and existing_grant.organization_id != organization.id:
        raise ValueError(
            "Instance license is already active on another organization. "
            "Renew from the host organization or Platform Ops."
        )

    machine_code = get_or_create_machine_code(organization=organization, user=user)

    if getattr(settings, "DEBUG", False) and code.upper() in ("DEV", "DEV-UNLIMITED", "DEVELOPMENT"):
        data = {
            "license_key": f"DEV-{secrets.token_hex(8).upper()}",
            "machine_code": machine_code,
            "limits": _dev_unlimited_limits(),
            "features": ["*"],
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": None,
        }
        change_type = License.ChangeType.INITIAL
    else:
        data = verify_activation_code(code)
        if data["machine_code"] != machine_code:
            raise ValueError("Activation code is not for this machine")
        change_type = License.ChangeType.INITIAL

    license_key = data["license_key"]
    other = License.objects.filter(license_key=license_key).exclude(organization=organization).first()
    if other:
        raise ValueError("Activation code already used by another organization")

    raw_limits = data.get("limits")
    limits = _map_limits({} if raw_limits is None else raw_limits)
    features = _normalize_features(data.get("features"))
    issued_at = datetime.fromisoformat(data["issued_at"].replace("Z", "+00:00"))
    expires_at = None
    if data.get("expires_at"):
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))

    existing = License.objects.filter(organization=organization).first()
    if existing:
        change_type, reason = _determine_change_type(
            existing,
            limits,
            expires_at,
            features,
        )
        existing.archive_to_history(change_type=change_type, reason=reason, changed_by=user)
        existing.license_key = license_key
        existing.machine_code = machine_code
        existing.issued_at = issued_at
        existing.expires_at = expires_at
        existing.version += 1
        existing.change_type = change_type
        existing.change_reason = reason
        existing.status = License.Status.ACTIVE
        existing.features = features
        existing.signature = data.get("signature", "")
        for field, val in limits.items():
            setattr(existing, field, val)
        existing.save()
        _notify_license_activated(organization=organization, license_obj=existing)
        return existing, change_type

    lic = License.objects.create(
        organization=organization,
        license_key=license_key,
        machine_code=machine_code,
        issued_at=issued_at,
        expires_at=expires_at,
        activated_by=user if user and user.is_authenticated else None,
        signature=data.get("signature", ""),
        features=features,
        **limits,
    )
    lic.archive_to_history(change_type=License.ChangeType.INITIAL, reason="Initial activation", changed_by=user)
    _notify_license_activated(organization=organization, license_obj=lic)
    return lic, License.ChangeType.INITIAL


def _notify_license_activated(*, organization: Organization, license_obj: License) -> None:
    """Optional plugin hook: seed EffectiveQuota and validate instance pool.

    Failures propagate so the surrounding ``atomic`` activation rolls back
    (license must not commit without a successful quota seed when a provider
    is registered).
    """
    from common.extension_spi import get_quota_provider

    provider = get_quota_provider()
    if provider is not None:
        provider.on_license_activated(organization, license_obj)
