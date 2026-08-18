"""Public Data Gateway capacity: infra (per-gateway) + occupancy metering.

Layers:
  1) License.max_public_gateways — instance Public Gateway count
  2) LensGatewayLink.capacity_bytes — per-gateway infrastructure capacity (this module)
  3) Org quota max_public_gateway_capacity_bytes — org share (usage via org_* helpers)
"""

from __future__ import annotations

import math
from typing import Any

from common.errors import AppError

_MAX_BIGINT = 2**63 - 1
_MIN_BIGINT = -(2**63)
_MIB = 1024**2

# Legacy instance-wide runtime setting (migrated onto LensGatewayLink.capacity_bytes).
KEY_PUBLIC_GATEWAY_CAPACITY_GB = "gateway.public_total_capacity_gb"

_CAPACITY_FULL_MESSAGE = (
    "Public Data Gateway capacity is full. Contact your platform administrator "
    "to raise the workspace limit on this gateway."
)


def _exact_stored_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer value")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("stored value must be an exact integer")
        parsed = int(value)
    elif isinstance(value, str):
        parsed = int(value.strip())
    else:
        raise TypeError("stored value must be an integer")
    if parsed < _MIN_BIGINT or parsed > _MAX_BIGINT:
        raise OverflowError("stored value is outside the database integer range")
    return parsed


def _normalize_capacity_bytes(capacity_bytes: Any) -> int:
    value = _exact_stored_int(capacity_bytes)
    if value < -1:
        raise ValueError("capacity_bytes must be -1 (unlimited) or >= 0")
    if value >= 0 and value % _MIB != 0:
        raise ValueError("capacity_bytes must use whole MiB increments")
    return value


def get_public_gateway_capacity_bytes(*, gateway_link) -> int:
    """Configured workspace capacity in bytes for one Public Gateway (-1 = unlimited)."""
    raw = getattr(gateway_link, "capacity_bytes", None)
    if raw is None:
        return -1
    return int(raw)


def set_public_gateway_capacity_bytes(
    *, gateway_link, capacity_bytes: Any, user=None
) -> int:
    del user  # reserved for audit callers
    value = _normalize_capacity_bytes(capacity_bytes)
    if get_public_gateway_capacity_bytes(gateway_link=gateway_link) == value:
        return value
    gateway_link.capacity_bytes = value
    gateway_link.save(update_fields=["capacity_bytes", "updated_at"])
    return value


def public_gateway_capacity_limit_bytes(*, gateway_link) -> int | None:
    capacity_bytes = get_public_gateway_capacity_bytes(gateway_link=gateway_link)
    if capacity_bytes < 0:
        return None
    return capacity_bytes


def lock_public_gateway_capacity(*, gateway_link):
    """Serialize capacity check + session create. Call inside transaction.atomic()."""
    from apps.lens_bridge.models import LensGatewayLink

    return LensGatewayLink.objects.select_for_update().get(pk=gateway_link.pk)


def session_scope_occupancy(*, session) -> tuple[int, bool]:
    """Bytes + unknown flag for a session's stored source_scopes_json."""
    if getattr(session, "capacity_reservation_status", "") == "reserved":
        try:
            reserved_bytes = _exact_stored_int(
                getattr(session, "capacity_reserved_bytes", 0) or 0
            )
        except (TypeError, ValueError, OverflowError):
            return 0, True
        if reserved_bytes < 0:
            return 0, True
        return reserved_bytes, False
    return _occupancy_from_scope_dicts(
        organization_id=int(getattr(session, "organization_id", 0) or 0),
        scopes=list(getattr(session, "source_scopes_json", None) or []),
        re_resolve=False,
    )


def _metered_managed_restore_bindings(
    *,
    gateway_link_ids: list[int],
    organization_id: int | None = None,
):
    """Return workspaces not currently represented by a Chat reservation."""

    from django.db.models import Exists, OuterRef

    from apps.lens_bridge.models import LensSessionLink, LensWorkspaceBinding

    active_reservation = LensSessionLink.objects.filter(
        organization_id=OuterRef("organization_id"),
        knowledge_source_id=OuterRef("knowledge_source_id"),
        gateway_link_id=OuterRef("gateway_link_id"),
        lifecycle_status__in=(
            LensSessionLink.LifecycleStatus.PROVISIONING,
            LensSessionLink.LifecycleStatus.DELETING,
        ),
        capacity_reservation_status=(
            LensSessionLink.CapacityReservationStatus.RESERVED
        ),
    )
    filters = {
        "gateway_link_id__in": gateway_link_ids,
        "workspace_kind": LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE,
    }
    if organization_id is not None:
        filters["organization_id"] = int(organization_id)
    return (
        LensWorkspaceBinding.objects.filter(**filters)
        .exclude(state=LensWorkspaceBinding.State.DELETED)
        .annotate(has_chat_reservation=Exists(active_reservation))
        .filter(has_chat_reservation=False)
        .select_related("knowledge_source")
    )


def _occupancy_from_scope_dicts(
    *,
    organization_id: int,
    scopes: list[dict],
    re_resolve: bool,
) -> tuple[int, bool]:
    """Return stored (bytes, has_unknown) without Agent or browser I/O."""
    del re_resolve  # retained for compatibility with current callers
    from apps.protection.models import BackupSourceSnapshotDirectory
    from apps.subscription.services.quota import summarize_gateway_select_scopes

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
        directory_filters = {"id": directory_id}
        if organization_id > 0:
            directory_filters["organization_id"] = organization_id
        directory = BackupSourceSnapshotDirectory.objects.filter(
            **directory_filters
        ).first()
        if directory is None:
            any_unknown = True
            continue
        path = str(scope.get("source_path") or directory.source_path or "")
        resolved = {
            "source_path": path,
            "path_type": str(scope.get("path_type") or "unknown"),
        }
        has_summary = (
            scope.get("size_bytes") is not None
            and scope.get("file_count") is not None
        )
        if has_summary:
            try:
                size_bytes = _exact_stored_int(scope["size_bytes"])
                file_count = _exact_stored_int(scope["file_count"])
            except (TypeError, ValueError, OverflowError):
                any_unknown = True
                continue
            summary_valid = (
                resolved["path_type"] in {"file", "dir"}
                and size_bytes >= 0
                and file_count >= 0
                and (
                    resolved["path_type"] != "file"
                    or file_count == 1
                )
            )
            if not summary_valid:
                any_unknown = True
                continue
            resolved["size_bytes"] = size_bytes
            resolved["file_count"] = file_count
        elif "size_bytes" in scope and scope.get("size_bytes") is not None:
            try:
                legacy_size_bytes = _exact_stored_int(scope["size_bytes"])
            except (TypeError, ValueError, OverflowError):
                any_unknown = True
            else:
                if legacy_size_bytes < 0:
                    any_unknown = True
                else:
                    resolved["size_bytes"] = legacy_size_bytes
        paired_scopes.append(resolved)
        paired_dirs.append(directory)
    if not paired_scopes:
        return 0, True
    trusted_bytes = sum(
        max(0, int(scope.get("size_bytes") or 0))
        for scope in paired_scopes
        if "file_count" in scope
    )
    legacy_scopes = [scope for scope in paired_scopes if "file_count" not in scope]
    legacy_dirs = [
        directory
        for scope, directory in zip(paired_scopes, paired_dirs, strict=True)
        if "file_count" not in scope
    ]
    _files, legacy_bytes, unknown = summarize_gateway_select_scopes(
        legacy_scopes,
        legacy_dirs,
    )
    nbytes = trusted_bytes + legacy_bytes
    return int(nbytes or 0), bool(any_unknown or unknown)


def bulk_public_gateway_used_bytes(
    link_ids: list[int] | None = None,
) -> dict[int, tuple[int, bool]]:
    """
    Logical occupancy keyed by gateway_link_id.

    Counts managed-restore workspaces, except while a PROVISIONING Chat's
    durable reservation is authoritative for that workspace.
    """
    from apps.lens_bridge.models import LensSessionLink
    from apps.lens_bridge.services import platform_lens

    if link_ids is None:
        ids = list(platform_lens.platform_gateway_links().values_list("id", flat=True))
    else:
        ids = [int(x) for x in link_ids]
    if not ids:
        return {}

    totals: dict[int, int] = {link_id: 0 for link_id in ids}
    unknowns: dict[int, bool] = {link_id: False for link_id in ids}

    bindings = _metered_managed_restore_bindings(
        gateway_link_ids=ids,
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
        lifecycle_status__in=(
            LensSessionLink.LifecycleStatus.PROVISIONING,
            LensSessionLink.LifecycleStatus.DELETING,
        ),
        capacity_reservation_status=(
            LensSessionLink.CapacityReservationStatus.RESERVED
        ),
    ).only("id", "capacity_reserved_bytes", "gateway_link_id")
    for session in provisioning:
        link_id = int(session.gateway_link_id)
        nbytes, unknown = session_scope_occupancy(session=session)
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
    org_id = int(organization_id)
    if org_id <= 0:
        return 0, False

    return bulk_org_public_gateway_used_bytes([org_id]).get(org_id, (0, False))


def bulk_org_public_gateway_used_bytes(
    organization_ids: list[int] | tuple[int, ...] | None = None,
) -> dict[int, tuple[int, bool]]:
    """Return Public Gateway occupancy for organizations in one scan."""
    from apps.lens_bridge.models import LensSessionLink
    from apps.lens_bridge.services import platform_lens

    requested_ids = (
        sorted({int(value) for value in organization_ids if int(value) > 0})
        if organization_ids is not None
        else None
    )
    if requested_ids == []:
        return {}

    platform_ids = list(
        platform_lens.platform_gateway_links().values_list("id", flat=True)
    )
    if not platform_ids:
        return {
            organization_id: (0, False)
            for organization_id in (requested_ids or [])
        }

    totals: dict[int, int] = {
        organization_id: 0 for organization_id in (requested_ids or [])
    }
    unknowns: dict[int, bool] = {
        organization_id: False for organization_id in (requested_ids or [])
    }

    bindings = _metered_managed_restore_bindings(
        gateway_link_ids=platform_ids,
    )
    if requested_ids is not None:
        bindings = bindings.filter(organization_id__in=requested_ids)
    for binding in bindings:
        ks = binding.knowledge_source
        if ks is None or str(getattr(ks, "lifecycle_status", "")).lower() == "deleted":
            continue
        organization_id = int(binding.organization_id)
        nbytes, unknown = _occupancy_from_scope_dicts(
            organization_id=organization_id,
            scopes=list(ks.source_scopes_json or []),
            re_resolve=True,
        )
        totals[organization_id] = totals.get(organization_id, 0) + nbytes
        unknowns[organization_id] = bool(
            unknowns.get(organization_id, False) or unknown
        )

    provisioning = LensSessionLink.objects.filter(
        gateway_link_id__in=platform_ids,
        lifecycle_status__in=(
            LensSessionLink.LifecycleStatus.PROVISIONING,
            LensSessionLink.LifecycleStatus.DELETING,
        ),
        capacity_reservation_status=(
            LensSessionLink.CapacityReservationStatus.RESERVED
        ),
    )
    if requested_ids is not None:
        provisioning = provisioning.filter(organization_id__in=requested_ids)
    provisioning = provisioning.only(
        "id",
        "organization_id",
        "capacity_reserved_bytes",
        "gateway_link_id",
    )
    for session in provisioning:
        organization_id = int(session.organization_id)
        nbytes, unknown = session_scope_occupancy(session=session)
        totals[organization_id] = totals.get(organization_id, 0) + nbytes
        unknowns[organization_id] = bool(
            unknowns.get(organization_id, False) or unknown
        )

    return {
        organization_id: (
            int(totals.get(organization_id, 0)),
            bool(unknowns.get(organization_id, False)),
        )
        for organization_id in totals
    }


def org_public_gateway_capacity_used_bytes(*, organization_id: int) -> int:
    """Bytes used by one organization across Public Gateways."""
    used_bytes, _unknown = org_public_gateway_used_bytes(
        organization_id=organization_id
    )
    return used_bytes


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
    limit_bytes = get_public_gateway_capacity_bytes(gateway_link=gateway_link)
    if unknown_size or used_unknown:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_CAPACITY_FULL_MESSAGE,
            diagnostic=_CAPACITY_FULL_MESSAGE,
            meta={
                "quota_type": "gateway.public_capacity_bytes",
                "gateway_link_id": int(gateway_link.pk),
                "limit": limit_bytes,
                "used": used,
                "requested": 0,
                "unknown_size": True,
                "scope": "gateway",
            },
        )
    needed = max(0, int(additional_bytes or 0))
    # Match org-pool semantics: at/over capacity, even a zero-byte admit is denied
    # (capacity_bytes=0 must stay hard-empty).
    if used + needed > limit or (needed == 0 and used >= limit):
        raise AppError(
            code="SUBSCRIPTION.QUOTA_EXCEEDED",
            status=403,
            title=_CAPACITY_FULL_MESSAGE,
            diagnostic=_CAPACITY_FULL_MESSAGE,
            meta={
                "quota_type": "gateway.public_capacity_bytes",
                "gateway_link_id": int(gateway_link.pk),
                "limit": limit_bytes,
                "used": used,
                "requested": needed,
                "scope": "gateway",
            },
        )


def public_gateway_capacity_payload(*, gateway_link) -> dict:
    limit_bytes = get_public_gateway_capacity_bytes(gateway_link=gateway_link)
    used_bytes, used_unknown = public_gateway_used_bytes(
        gateway_link_id=int(gateway_link.pk)
    )
    gateway = getattr(gateway_link, "gateway", None)
    return {
        "gateway_link_id": int(gateway_link.pk),
        "gateway_id": int(getattr(gateway_link, "gateway_id", 0) or 0),
        "gateway_name": str(getattr(gateway, "name", "") or ""),
        "capacity_bytes": limit_bytes,
        "unlimited": limit_bytes < 0,
        "used_bytes": used_bytes,
        "used_incomplete": used_unknown,
        "limit_bytes": None if limit_bytes < 0 else limit_bytes,
    }


def list_public_gateway_capacity_payloads() -> list[dict]:
    """Capacity payload for every platform Public Gateway link."""
    from apps.lens_bridge.services import platform_lens

    links = list(
        platform_lens.platform_gateway_links().select_related("gateway").order_by("id")
    )
    used_map = bulk_public_gateway_used_bytes([int(link.id) for link in links])
    payloads = []
    for link in links:
        used_bytes, used_unknown = used_map.get(int(link.id), (0, False))
        limit_bytes = get_public_gateway_capacity_bytes(gateway_link=link)
        payloads.append(
            {
                "gateway_link_id": int(link.id),
                "gateway_id": int(link.gateway_id),
                "gateway_name": str(getattr(link.gateway, "name", "") or ""),
                "capacity_bytes": limit_bytes,
                "unlimited": limit_bytes < 0,
                "used_bytes": used_bytes,
                "used_incomplete": used_unknown,
                "limit_bytes": None if limit_bytes < 0 else limit_bytes,
            }
        )
    return payloads


__all__ = [
    "KEY_PUBLIC_GATEWAY_CAPACITY_GB",
    "assert_public_gateway_capacity",
    "bulk_org_public_gateway_used_bytes",
    "bulk_public_gateway_used_bytes",
    "get_public_gateway_capacity_bytes",
    "list_public_gateway_capacity_payloads",
    "lock_public_gateway_capacity",
    "org_public_gateway_capacity_used_bytes",
    "org_public_gateway_used_bytes",
    "public_gateway_capacity_limit_bytes",
    "public_gateway_capacity_payload",
    "public_gateway_used_bytes",
    "session_scope_occupancy",
    "set_public_gateway_capacity_bytes",
]
