"""
Host quota facade.

Create-path helpers live here; hard checks run through QuotaProvider (plugin).
Community (empty socket): no-op unless a future community hard-limit is enabled.
"""

from __future__ import annotations

from apps.subscription.constants import QUOTA_ENFORCEMENT_ENABLED, UNLIMITED
from common.errors import AppError
from common.extension_spi import get_quota_provider

_QUOTA_FULL_MESSAGE = (
    "Organization quota is full. Contact your platform administrator to raise limits."
)

_NODE_ROLE_TO_RESOURCE = {
    "agent": "max_source_hosts",
    "proxy": "max_proxies",
    "gateway": "max_gateways",
}

_REPO_TYPE_TO_RESOURCE = {
    "s3": "max_object_storage",
    "nas": "max_target_nas",
    "proxy_fs": "max_standalone_disk",
}


def _enforcement_flag_enabled() -> bool:
    try:
        from django.conf import settings

        return bool(getattr(settings, "HFL_QUOTA_ENFORCEMENT_ENABLED", QUOTA_ENFORCEMENT_ENABLED))
    except Exception:
        return bool(QUOTA_ENFORCEMENT_ENABLED)


def hard_quota_enforcement_active() -> bool:
    """
    True when create paths should hard-deny over quota.

    EE QuotaProvider present → always hard. Community → only if the
    explicit enforcement flag is enabled (default off / informational).
    """
    if get_quota_provider() is not None:
        return True
    return _enforcement_flag_enabled()


def _quota_exceeded_error(
    *,
    quota_type: str,
    limit: int | float,
    used: int | float,
    requested: int | float = 0,
    scope: str = "organization",
) -> AppError:
    return AppError(
        code="SUBSCRIPTION.QUOTA_EXCEEDED",
        status=403,
        title=_QUOTA_FULL_MESSAGE,
        diagnostic=_QUOTA_FULL_MESSAGE,
        meta={
            "quota_type": quota_type,
            "limit": limit,
            "used": used,
            "requested": requested,
            "scope": scope,
        },
    )


def enforce_license_quota(organization, resource_type: str, additional: int | float = 1):
    """Deny path for new consumption when over quota (hard limit)."""
    provider = get_quota_provider()
    if provider is not None:
        return provider.check_quota(organization, resource_type, additional)
    if not _enforcement_flag_enabled():
        return None
    return None


def record_usage_event(organization, **event):
    """Publish a business measurement to the optional enterprise usage ledger."""
    provider = get_quota_provider()
    recorder = getattr(provider, "record_usage_event", None)
    if not callable(recorder):
        return None
    return recorder(organization, **event)


def initialize_organization_quota(organization) -> None:
    """Let the active extension assign its default policy to a new organization."""
    provider = get_quota_provider()
    initializer = getattr(provider, "on_organization_created", None)
    if callable(initializer):
        initializer(organization)


def enterprise_feature_enabled(feature_key: str) -> bool:
    """Read an Enterprise feature grant without introducing a Host dependency."""
    provider = get_quota_provider()
    resolver = getattr(provider, "feature_enabled", None)
    return bool(resolver(feature_key)) if callable(resolver) else False


def enforce_node_role_quota(*, organization, role: str):
    resource = _NODE_ROLE_TO_RESOURCE.get(str(role or "").strip().lower())
    if not resource:
        return None
    return enforce_license_quota(organization, resource, additional=1)


def enforce_repository_type_quota(*, organization, repo_type: str):
    resource = _REPO_TYPE_TO_RESOURCE.get(str(repo_type or "").strip().lower())
    if not resource:
        return None
    return enforce_license_quota(organization, resource, additional=1)


def normalize_scope_path(path: str) -> str:
    """Normalize backup/gateway paths for equality checks (Windows + POSIX)."""
    normalized = str(path or "").replace("\\", "/").strip()
    return normalized.rstrip("/") or "/"


def relative_scope_path(*, root: str, selected: str) -> str:
    """Return selected path relative to root, or '' when equal / outside."""
    root_n = normalize_scope_path(root)
    selected_n = normalize_scope_path(selected)
    if selected_n == root_n:
        return ""
    prefix = f"{root_n}/"
    if selected_n.startswith(prefix):
        return selected_n[len(prefix) :]
    return ""


def resolve_scope_entry(
    *,
    organization_id: int,
    directory,
    source_path: str,
    claimed_type: str = "unknown",
) -> tuple[str, int | None]:
    """
    Resolve trusted path_type and size for quota accounting.

    Returns (path_type, size_bytes). size_bytes is:
      - directory metadata for root selections
      - browse entry size for nested files
      - None when size cannot be proven (nested dirs / unresolved)
    """
    _ = claimed_type  # not trusted for nested paths
    selected = normalize_scope_path(source_path)
    root = normalize_scope_path(str(getattr(directory, "source_path", "") or ""))
    root_size = int(getattr(directory, "size_bytes", 0) or 0)
    if selected == root:
        raw = str(getattr(directory, "path_type", "") or "").lower()
        if raw == "file":
            return "file", root_size
        return "dir", root_size

    rel = relative_scope_path(root=root, selected=selected)
    if not rel:
        return "unknown", None
    parent = "/".join(rel.split("/")[:-1])
    name = rel.split("/")[-1]
    if not name:
        return "unknown", None
    try:
        from apps.protection.services.snapshot_browser import browse_snapshot_directory

        result = browse_snapshot_directory(
            organization_id=organization_id,
            directory_id=int(directory.id),
            path=parent,
            limit=2000,
        )
    except Exception:
        return "unknown", None
    for entry in result.get("entries") or []:
        entry_name = str(entry.get("name") or "").strip()
        entry_rel = str(entry.get("path") or "").replace("\\", "/").strip().rstrip("/")
        if entry_name == name or entry_rel == rel or entry_rel.endswith(f"/{name}"):
            entry_type = str(entry.get("type") or "").lower()
            if entry_type == "file":
                if "size_bytes" not in entry or entry.get("size_bytes") is None:
                    return "file", None
                try:
                    return "file", int(entry.get("size_bytes"))
                except (TypeError, ValueError):
                    return "file", None
            if entry_type in {"dir", "directory"}:
                return "dir", None
            return "unknown", None
    return "unknown", None


def summarize_gateway_select_scopes(
    scopes: list[dict],
    directories: list,
) -> tuple[int, int, bool]:
    """
    Compute (file_count, size_bytes, unknown_directory) for a selection.

    Root path match uses directory metadata. Nested dirs / unknown → unknown.
    Nested files require explicit size_bytes (including 0).
    """
    total_files = 0
    total_bytes = 0
    unknown_directory = False
    for scope, directory in zip(scopes, directories, strict=True):
        selected = normalize_scope_path(str(scope.get("source_path") or ""))
        root = normalize_scope_path(str(getattr(directory, "source_path", "") or ""))
        path_type = str(scope.get("path_type") or "unknown").lower()
        if selected == root:
            total_files += int(getattr(directory, "file_count", 0) or 0)
            total_bytes += int(getattr(directory, "size_bytes", 0) or 0)
            continue
        if path_type == "file":
            total_files += 1
            if "size_bytes" not in scope or scope.get("size_bytes") is None:
                unknown_directory = True
            else:
                total_bytes += int(scope.get("size_bytes") or 0)
            continue
        unknown_directory = True
    return total_files, total_bytes, unknown_directory


def assert_gateway_select_within_limits(
    *,
    organization,
    file_count: int,
    size_bytes: int,
    unknown_directory: bool = False,
) -> None:
    """
    Gateway/copilot selection caps.

    When a plugin provides get_limits, finite caps always apply (not gated on
    create-path enforcement). Without a provider, this is a no-op.
    """
    provider = get_quota_provider()
    if provider is None:
        return None
    limits = provider.get_limits(organization) or {}
    max_files = int(limits.get("gateway_select_max_files", UNLIMITED))
    max_bytes = int(limits.get("gateway_select_max_bytes", UNLIMITED))
    if unknown_directory and (max_files >= 0 or max_bytes >= 0):
        raise _quota_exceeded_error(
            quota_type="gateway_select_max_files",
            limit=max_files if max_files >= 0 else max_bytes,
            used=0,
            requested=file_count,
        )
    if max_files >= 0 and int(file_count) > max_files:
        raise _quota_exceeded_error(
            quota_type="gateway_select_max_files",
            limit=max_files,
            used=int(file_count),
            requested=0,
        )
    if max_bytes >= 0 and int(size_bytes) > max_bytes:
        raise _quota_exceeded_error(
            quota_type="gateway_select_max_bytes",
            limit=max_bytes,
            used=int(size_bytes),
            requested=0,
        )
    return None


def validate_quota(organization, quota_type: str, amount: int = 1) -> dict:
    """Preview whether a quota check would pass."""
    provider = get_quota_provider()
    if provider is not None:
        return provider.validate_quota(organization, quota_type, amount)
    # Host alone never hard-enforces; do not claim enforcement_enabled=True.
    return {
        "is_valid": True,
        "quota_type": quota_type,
        "message": (
            "Quota enforcement disabled"
            if not _enforcement_flag_enabled()
            else "No QuotaProvider; Host create-path enforcement is a no-op"
        ),
        "enforcement_enabled": False,
    }
