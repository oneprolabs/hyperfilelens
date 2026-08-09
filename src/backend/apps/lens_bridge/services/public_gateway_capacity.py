"""Public Data Gateway capacity: infra (per-gateway) + occupancy metering.

Layers:
  1) License.max_public_gateways — instance Public Gateway count
  2) LensGatewayLink.capacity_gb — per-gateway infrastructure capacity (this module)
  3) Org quota max_public_gateway_capacity_gb — org share (usage via org_* helpers)
"""

from __future__ import annotations

from common.errors import AppError

_GIB = 1024**3

# Legacy instance-wide runtime setting (migrated onto LensGatewayLink.capacity_gb).
KEY_PUBLIC_GATEWAY_CAPACITY_GB = "gateway.public_total_capacity_gb"

_CAPACITY_FULL_MESSAGE = (
    "Public Data Gateway capacity is full. Contact your platform administrator "
    "to raise the workspace limit on this gateway."
)


def _normalize_capacity_gb(capacity_gb: int) -> int:
    value = int(capacity_gb)
    if value < -1:
        raise ValueError("capacity_gb must be -1 (unlimited) or >= 0")
    return value


def get_public_gateway_capacity_gb(*, gateway_link) -> int:
    """Configured workspace capacity in GiB for one Public Gateway link (-1 = unlimited)."""
    raw = getattr(gateway_link, "capacity_gb", None)
    if raw is None:
        return -1
    return int(raw)


def set_public_gateway_capacity_gb(*, gateway_link, capacity_gb: int, user=None) -> int:
    del user  # reserved for audit callers
    value = _normalize_capacity_gb(capacity_gb)
    if get_public_gateway_capacity_gb(gateway_link=gateway_link) == value:
        return value
    gateway_link.capacity_gb = value
    gateway_link.save(update_fields=["capacity_gb", "updated_at"])
    return value


def public_gateway_capacity_limit_bytes(*, gateway_link) -> int | None:
    gb = get_public_gateway_capacity_gb(gateway_link=gateway_link)
    if gb < 0:
        return None
    return int(gb) * _GIB


def lock_public_gateway_capacity(*, gateway_link):
    """Serialize capacity check + session create. Call inside transaction.atomic()."""
    from apps.lens_bridge.models import LensGatewayLink

    return LensGatewayLink.objects.select_for_update().get(pk=gateway_link.pk)


def session_scope_occupancy(*, session) -> tuple[int, bool]:
    """Bytes + unknown flag for a session's stored source_scopes_json."""
    return _occupancy_from_scope_dicts(
        organization_id=int(getattr(session, "organization_id", 0) or 0),
        scopes=list(getattr(session, "source_scopes_json", None) or []),
        re_resolve=False,
    )


def _occupancy_from_scope_dicts(
    *,
    organization_id: int,
    scopes: list[dict],
    re_resolve: bool,
) -> tuple[int, bool]:
    """Return (bytes, has_unknown) for a list of scope dicts."""
    from apps.protection.models import BackupSourceSnapshotDirectory
    from apps.subscription.services.quota import (
        resolve_scope_entry,
        summarize_gateway_select_scopes,
    )

    scope_entries = [scope for scope in scopes if isinstance(scope, dict)]
    if not scope_entries:
        return 0, False

    paired_scopes: list[dict] = []
    paired_dirs: list = []
    any_unknown = False
    for scope in scope_entries:
        try:
            directory_id = int(scope.get("backup_snapshot_directory_id"))
        except (TypeError, ValueError):
            any_unknown = True
            continue
        directory = BackupSourceSnapshotDirectory.objects.filter(id=directory_id).first()
        if directory is None:
            any_unknown = True
            continue
        path = str(scope.get("source_path") or directory.source_path or "")
        if re_resolve:
            path_type, size_bytes = resolve_scope_entry(
                organization_id=organization_id or int(directory.organization_id),
                directory=directory,
                source_path=path,
                claimed_type=str(scope.get("path_type") or "unknown"),
            )
            resolved: dict = {"source_path": path, "path_type": path_type}
            if size_bytes is not None:
                resolved["size_bytes"] = int(size_bytes)
        else:
            resolved = {
                "source_path": path,
                "path_type": str(scope.get("path_type") or "unknown"),
            }
            if "size_bytes" in scope and scope.get("size_bytes") is not None:
                resolved["size_bytes"] = int(scope["size_bytes"])
        paired_scopes.append(resolved)
        paired_dirs.append(directory)
    if not paired_scopes:
        return 0, True
    _files, nbytes, unknown = summarize_gateway_select_scopes(paired_scopes, paired_dirs)
    return int(nbytes or 0), bool(any_unknown or unknown)


def bulk_public_gateway_used_bytes(
    link_ids: list[int] | None = None,
) -> dict[int, tuple[int, bool]]:
    """
    Logical occupancy keyed by gateway_link_id.

    Counts managed-restore workspace bindings + PROVISIONING sessions that have
    not yet linked a knowledge_source (reservation without double-count).
    """
    from apps.lens_bridge.models import LensSessionLink, LensWorkspaceBinding
    from apps.lens_bridge.services import platform_lens

    if link_ids is None:
        ids = list(platform_lens.platform_gateway_links().values_list("id", flat=True))
    else:
        ids = [int(x) for x in link_ids]
    if not ids:
        return {}

    totals: dict[int, int] = {link_id: 0 for link_id in ids}
    unknowns: dict[int, bool] = {link_id: False for link_id in ids}

    bindings = (
        LensWorkspaceBinding.objects.filter(
            gateway_link_id__in=ids,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
        )
        .exclude(state=LensWorkspaceBinding.State.DELETED)
        .select_related("knowledge_source")
    )
    for binding in bindings:
        link_id = int(binding.gateway_link_id)
        ks = binding.knowledge_source
        if ks is None or str(getattr(ks, "lifecycle_status", "")).lower() == "deleted":
            continue
        org_id = int(getattr(ks, "organization_id", 0) or 0)
        nbytes, unknown = _occupancy_from_scope_dicts(
            organization_id=org_id,
            scopes=list(ks.source_scopes_json or []),
            re_resolve=True,
        )
        totals[link_id] = totals.get(link_id, 0) + nbytes
        unknowns[link_id] = bool(unknowns.get(link_id) or unknown)

    provisioning = LensSessionLink.objects.filter(
        gateway_link_id__in=ids,
        lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        status=LensSessionLink.Status.ACTIVE,
        knowledge_source_id__isnull=True,
    ).only("id", "organization_id", "source_scopes_json", "gateway_link_id")
    for session in provisioning:
        link_id = int(session.gateway_link_id)
        nbytes, unknown = _occupancy_from_scope_dicts(
            organization_id=int(session.organization_id),
            scopes=list(session.source_scopes_json or []),
            re_resolve=False,
        )
        totals[link_id] = totals.get(link_id, 0) + nbytes
        unknowns[link_id] = bool(unknowns.get(link_id) or unknown)

    return {
        link_id: (int(totals.get(link_id, 0)), bool(unknowns.get(link_id, False)))
        for link_id in ids
    }


def public_gateway_used_bytes(*, gateway_link_id: int) -> tuple[int, bool]:
    """Best-effort logical occupancy for one Public Gateway link."""
    return bulk_public_gateway_used_bytes([int(gateway_link_id)]).get(
        int(gateway_link_id),
        (0, False),
    )


def org_public_gateway_used_bytes(*, organization_id: int) -> tuple[int, bool]:
    """
    Org occupancy across all Public Gateways (layer 3 usage meter).

    Sums managed-restore bindings + provisioning reservations for this org.
    """
    from apps.lens_bridge.models import LensSessionLink, LensWorkspaceBinding
    from apps.lens_bridge.services import platform_lens

    org_id = int(organization_id)
    if org_id <= 0:
        return 0, False

    platform_ids = list(platform_lens.platform_gateway_links().values_list("id", flat=True))
    if not platform_ids:
        return 0, False

    total = 0
    any_unknown = False

    bindings = (
        LensWorkspaceBinding.objects.filter(
            organization_id=org_id,
            gateway_link_id__in=platform_ids,
            workspace_kind=LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
        )
        .exclude(state=LensWorkspaceBinding.State.DELETED)
        .select_related("knowledge_source")
    )
    for binding in bindings:
        ks = binding.knowledge_source
        if ks is None or str(getattr(ks, "lifecycle_status", "")).lower() == "deleted":
            continue
        nbytes, unknown = _occupancy_from_scope_dicts(
            organization_id=org_id,
            scopes=list(ks.source_scopes_json or []),
            re_resolve=True,
        )
        total += nbytes
        any_unknown = bool(any_unknown or unknown)

    provisioning = LensSessionLink.objects.filter(
        organization_id=org_id,
        gateway_link_id__in=platform_ids,
        lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        status=LensSessionLink.Status.ACTIVE,
        knowledge_source_id__isnull=True,
    ).only("id", "organization_id", "source_scopes_json", "gateway_link_id")
    for session in provisioning:
        nbytes, unknown = _occupancy_from_scope_dicts(
            organization_id=org_id,
            scopes=list(session.source_scopes_json or []),
            re_resolve=False,
        )
        total += nbytes
        any_unknown = bool(any_unknown or unknown)

    return int(total), bool(any_unknown)


def org_public_gateway_capacity_used_gb(*, organization_id: int) -> float:
    """GiB used by one org across Public Gateways (for EffectiveQuota meters)."""
    used_bytes, _unknown = org_public_gateway_used_bytes(organization_id=organization_id)
    return round(float(used_bytes) / float(_GIB), 6)


def assert_public_gateway_capacity(
    *,
    gateway_link,
    additional_bytes: int = 0,
    unknown_size: bool = False,
) -> None:
    """
    Reject when this Public Gateway cannot accept additional_bytes.

    Unknown sizes under a finite cap are rejected (fail closed).
    """
    limit = public_gateway_capacity_limit_bytes(gateway_link=gateway_link)
    if limit is None:
        return
    used, used_unknown = public_gateway_used_bytes(gateway_link_id=int(gateway_link.pk))
    limit_gb = get_public_gateway_capacity_gb(gateway_link=gateway_link)
    if unknown_size or used_unknown:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_CAPACITY_FULL_MESSAGE,
            diagnostic=_CAPACITY_FULL_MESSAGE,
            meta={
                "quota_type": "gateway.public_capacity_gb",
                "gateway_link_id": int(gateway_link.pk),
                "limit": limit_gb,
                "used": round(used / _GIB, 3),
                "requested": 0,
                "unknown_size": True,
                "scope": "gateway",
            },
        )
    needed = max(0, int(additional_bytes or 0))
    # Match org-pool semantics: at/over capacity, even a zero-byte admit is denied
    # (capacity_gb=0 must stay hard-empty).
    if used + needed > limit or (needed == 0 and used >= limit):
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_CAPACITY_FULL_MESSAGE,
            diagnostic=_CAPACITY_FULL_MESSAGE,
            meta={
                "quota_type": "gateway.public_capacity_gb",
                "gateway_link_id": int(gateway_link.pk),
                "limit": limit_gb,
                "used": round(used / _GIB, 3),
                "requested": round(needed / _GIB, 3),
                "scope": "gateway",
            },
        )


def public_gateway_capacity_payload(*, gateway_link) -> dict:
    limit_gb = get_public_gateway_capacity_gb(gateway_link=gateway_link)
    used_bytes, used_unknown = public_gateway_used_bytes(gateway_link_id=int(gateway_link.pk))
    gateway = getattr(gateway_link, "gateway", None)
    return {
        "gateway_link_id": int(gateway_link.pk),
        "gateway_id": int(getattr(gateway_link, "gateway_id", 0) or 0),
        "gateway_name": str(getattr(gateway, "name", "") or ""),
        "capacity_gb": limit_gb,
        "unlimited": limit_gb < 0,
        "used_bytes": used_bytes,
        "used_gb": round(used_bytes / _GIB, 3),
        "used_incomplete": used_unknown,
        "limit_bytes": None if limit_gb < 0 else limit_gb * _GIB,
    }


def list_public_gateway_capacity_payloads() -> list[dict]:
    """Capacity payload for every platform Public Gateway link."""
    from apps.lens_bridge.services import platform_lens

    links = list(platform_lens.platform_gateway_links().select_related("gateway").order_by("id"))
    used_map = bulk_public_gateway_used_bytes([int(link.id) for link in links])
    payloads = []
    for link in links:
        used_bytes, used_unknown = used_map.get(int(link.id), (0, False))
        limit_gb = get_public_gateway_capacity_gb(gateway_link=link)
        payloads.append(
            {
                "gateway_link_id": int(link.id),
                "gateway_id": int(link.gateway_id),
                "gateway_name": str(getattr(link.gateway, "name", "") or ""),
                "capacity_gb": limit_gb,
                "unlimited": limit_gb < 0,
                "used_bytes": used_bytes,
                "used_gb": round(used_bytes / _GIB, 3),
                "used_incomplete": used_unknown,
                "limit_bytes": None if limit_gb < 0 else limit_gb * _GIB,
            }
        )
    return payloads


__all__ = [
    "KEY_PUBLIC_GATEWAY_CAPACITY_GB",
    "assert_public_gateway_capacity",
    "bulk_public_gateway_used_bytes",
    "get_public_gateway_capacity_gb",
    "list_public_gateway_capacity_payloads",
    "lock_public_gateway_capacity",
    "org_public_gateway_capacity_used_gb",
    "org_public_gateway_used_bytes",
    "public_gateway_capacity_limit_bytes",
    "public_gateway_capacity_payload",
    "public_gateway_used_bytes",
    "session_scope_occupancy",
    "set_public_gateway_capacity_gb",
]
