"""
Access-profile helpers for feature gate / landing path.
"""

from __future__ import annotations


from apps.iam.features import FEATURE_DEFAULT_PATHS, FEATURE_KEYS, FEATURE_KEY_SET
from apps.iam.models import Membership


def normalize_feature_keys(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    seen = set()
    for v in values:
        key = str(v or "").strip()
        if not key or key not in FEATURE_KEY_SET or key in seen:
            continue
        out.append(key)
        seen.add(key)
    return out


ROLE_FEATURES: dict[str, list[str]] = {
    Membership.Role.OWNER: list(FEATURE_KEYS),
    Membership.Role.ADMIN: [
        "dashboard",
        "node",
        "task",
        "storage",
        "backup",
        "alerts",
        "audit",
        "settings",
    ],
    Membership.Role.MANAGER: [
        "dashboard",
        "task",
        "storage",
        "backup",
        "alerts",
        "audit",
        "settings",
    ],
    Membership.Role.OPERATOR: [
        "dashboard",
        "node",
        "task",
        "storage",
        "backup",
        "alerts",
        "settings",
        "audit",
    ],
    Membership.Role.AUDITOR: [
        "dashboard",
        "node",
        "storage",
        "task",
        "backup",
        "alerts",
        "audit",
        "settings",
    ],
}


def get_effective_feature_keys(
    *, membership: Membership | None, role: str | None = None, authorization=None,
) -> list[str]:
    if membership is None:
        return ["dashboard"]
    from common.extension_spi import get_authz_provider

    # Community (no EE authz): full feature surface for any active member.
    if get_authz_provider() is None:
        return normalize_feature_keys(list(FEATURE_KEYS))
    provider = get_authz_provider()
    resolver = getattr(provider, "effective_permissions", None)
    if callable(resolver) or authorization is not None:
        keys = (
            set(authorization.organization_permissions)
            if authorization is not None else set(resolver(membership.user, membership.organization.key))
        )
        group_resolver = getattr(provider, "effective_group_permissions", None)
        if authorization is not None:
            keys.update(set().union(*authorization.group_permissions.values()))
        elif callable(group_resolver):
            keys.update(group_resolver(membership.user, membership.organization.key))
        features = ["dashboard"]
        if any(key.startswith("configuration.") for key in keys):
            features.extend(["node", "settings"])
        if any(key.startswith("protection.") for key in keys):
            features.extend(["node", "storage", "backup"])
        if any(key.startswith("insights.") for key in keys):
            features.append("node")
        if "operations.tasks.view" in keys:
            features.append("task")
        if any(
            key.startswith("operations.alert")
            or key.startswith("operations.notification")
            for key in keys
        ):
            features.append("alerts")
        if "operations.audit_logs.view" in keys:
            features.append("audit")
        return normalize_feature_keys(features)
    effective = role
    if not effective:
        from apps.iam.services.membership_service import authoritative_role

        effective = authoritative_role(membership)
    return normalize_feature_keys(ROLE_FEATURES.get(effective, ["dashboard"]))


def get_landing_path(*, feature_keys: list[str], preferred_feature: str = "") -> str:
    preferred_feature = str(preferred_feature or "").strip()
    if preferred_feature and preferred_feature in feature_keys:
        return FEATURE_DEFAULT_PATHS.get(preferred_feature, "/")
    if "dashboard" in feature_keys:
        return FEATURE_DEFAULT_PATHS["dashboard"]
    if feature_keys:
        return FEATURE_DEFAULT_PATHS.get(feature_keys[0], "/")
    return "/"


def serialize_available_platforms(feature_keys: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for key in feature_keys:
        out.append(
            {
                "key": key,
                "default_path": FEATURE_DEFAULT_PATHS.get(key, "/"),
            }
        )
    return out


def resolve_membership_for_org(user, org_key: str | None) -> Membership | None:
    if not getattr(user, "is_authenticated", False):
        return None
    qs = Membership.objects.select_related("organization").filter(
        user=user, is_active=True
    )
    if org_key:
        membership = qs.filter(organization__key=org_key).first()
        if membership is not None:
            return membership
    return qs.order_by("organization__key", "id").first()


def get_access_profile(user, *, org_key: str | None = None) -> dict[str, object]:
    membership = resolve_membership_for_org(user, org_key)
    role = ""
    authorization = None
    if membership is not None:
        from common.extension_spi import get_authz_provider

        provider = get_authz_provider()
        if provider is not None:
            factory = getattr(provider, "authorization_context", None)
            if callable(factory):
                authorization = factory(user, membership.organization.key)
            if authorization is not None:
                role = authorization.role or ""
            else:
                role = provider.get_org_role(user, membership.organization.key) or ""
        else:
            # Community: affiliation ⇒ owner-equivalent access profile.
            role = Membership.Role.OWNER
    feature_keys = get_effective_feature_keys(
        membership=membership, role=role or None, authorization=authorization,
    )
    preferred_feature = ""
    if membership is not None:
        preferred_feature = str(membership.preferred_feature or "").strip()
    grants = []
    group_grants = []
    if authorization is not None:
        grants = sorted(authorization.organization_permissions)
        group_grants = sorted(set().union(*authorization.group_permissions.values()))
    elif membership and provider is not None:
        resolver = getattr(provider, "effective_permissions", None)
        group_resolver = getattr(provider, "effective_group_permissions", None)
        grants = (
            list(resolver(user, membership.organization.key))
            if callable(resolver)
            else []
        )
        group_grants = (
            list(group_resolver(user, membership.organization.key))
            if callable(group_resolver)
            else []
        )
    return {
        "permissions": grants,
        "group_permissions": group_grants,
        "org_key": getattr(membership.organization, "key", "") if membership else "",
        "role": role,
        "visible_features": feature_keys,
        "available_platforms": serialize_available_platforms(feature_keys),
        "landing_path": get_landing_path(
            feature_keys=feature_keys,
            preferred_feature=preferred_feature,
        ),
    }
