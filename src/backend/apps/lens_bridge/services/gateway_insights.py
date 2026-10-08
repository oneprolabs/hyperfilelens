"""Project SourceLens-admin LensNodes into HFL Data Gateway views."""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import threading
import time
from typing import Any

from django.core.cache import caches
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from apps.lens_bridge.models import LensGatewayLink, LensKnowledgeSource
from apps.lens_bridge.services import (
    gateway_ownership,
    gateway_readiness,
    provisioning,
    sl_client,
)
from apps.node.api.serializers.node import NodeSerializer
from apps.node.services.internal.agent_upgrade import node_agent_release_status
from apps.node.services.internal.node_lifecycle import enrich_node_row

_SYSTEM_LENSNODE_NAMES = {"local-dev-lensnode"}
GATEWAY_DIRECTORY_PAGE_SIZES = (10, 20, 30, 50, 100)
GATEWAY_DIRECTORY_DEFAULT_PAGE_SIZE = 30
_SL_SNAPSHOT_REFRESHED_AT_KEY = "sl_lensnode_snapshot_refreshed_at"
_SL_SNAPSHOT_TTL = timedelta(seconds=30)
_SL_STATUS_REFRESH_WORKERS = 8
_SL_STATUS_REFRESH_TIMEOUT_SECONDS = 5
_SL_STATUS_REFRESH_BUDGET_SECONDS = 6
_SL_STATUS_FAILURE_TTL_SECONDS = 10
# Both active work and queued work are bounded across requests in this process.
_SL_STATUS_EXECUTOR = ThreadPoolExecutor(
    max_workers=_SL_STATUS_REFRESH_WORKERS,
    thread_name_prefix="gateway-status",
)
_SL_STATUS_SLOTS = threading.BoundedSemaphore(100)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LensNodeSnapshot:
    """Successful observation, retaining its original time through caching."""

    data: dict[str, Any]
    observed_at: datetime


def _lensnode_rows() -> list[dict[str, Any]]:
    """Return the full SL-admin LensNode directory, accepting DRF pagination."""
    payload = sl_client.request_json("GET", "/api/lens/admin/lensnodes/")
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("items", []))
    if not isinstance(payload, Iterable) or isinstance(payload, (str, bytes, dict)):
        return []
    return [
        dict(item) for item in payload if isinstance(item, dict) and item.get("uuid")
    ]


def _link_index(*, organization=None) -> dict[str, LensGatewayLink]:
    links = LensGatewayLink.objects.filter(
        sl_lensnode_uuid__isnull=False
    ).select_related("gateway", "organization", "created_by", "owner_user")
    if organization is not None:
        links = links.filter(
            organization=organization,
            scope__in=gateway_ownership.PRIVATE_GATEWAY_SCOPES,
        )
    return {str(link.sl_lensnode_uuid): link for link in links}


def _sl_status(value: Any) -> str:
    return str(value or "offline").strip().lower() or "offline"


def _origin_for_unlinked(sl_node: dict[str, Any]) -> str:
    if str(sl_node.get("name") or "").strip() in _SYSTEM_LENSNODE_NAMES:
        return LensGatewayLink.Origin.SYSTEM
    return LensGatewayLink.Origin.EXTERNAL


def _serialize_row(
    *,
    position: int,
    sl_node: dict[str, Any],
    link: LensGatewayLink | None,
    user=None,
    release_targets: dict[tuple[str, str, str, str], str] | None = None,
    persist_snapshot: bool = True,
    knowledge_source_count: int | None = None,
    authoritative_node: bool = False,
) -> dict[str, Any]:
    sl_uuid = str(sl_node["uuid"])
    sl_status = _sl_status(sl_node.get("status"))
    if link is not None:
        if persist_snapshot:
            provisioning.apply_gateway_lensnode_snapshot(link, sl_node)
        node = link.gateway
        enrichment = enrich_node_row(org=node.organization, node=node, user=user)
        enrichment["agent_release"] = node_agent_release_status(
            node,
            target_cache=release_targets,
        )
        node_payload = NodeSerializer(
            node,
            context={"enrichments": {node.id: enrichment}},
        ).data
        if knowledge_source_count is None:
            knowledge_source_count = LensKnowledgeSource.objects.filter(
                gateway=node
            ).count()
        origin = link.origin
        created_by = link.created_by or link.owner_user
        workspace_root = str(
            sl_node.get("workspace_path") or link.resolved_workspace_root()
        )
        sidecar_status = link.sidecar_status
        is_platform_default = bool(link.is_platform_default)
        runtime_state = gateway_readiness.gateway_runtime_state(
            link,
            sl_runtime_status=sl_status,
        )
        gateway_link_id = link.id
    else:
        node_payload = {
            "id": -(position + 1),
            "organization": 0,
            "name": str(sl_node.get("name") or sl_uuid),
            "role": "gateway",
            "status": sl_status,
            "routable": sl_status == "online",
            "version": str(sl_node.get("agent_version") or ""),
            "os_name": "",
            "ip_address": None,
            "metadata": {},
            "created_at": sl_node.get("created_at") or "",
            "updated_at": sl_node.get("updated_at") or "",
            "is_deleted": False,
            "deleted_at": None,
            "lifecycle": None,
            "workload": None,
            "agent_release": None,
        }
        knowledge_source_count = 0
        origin = _origin_for_unlinked(sl_node)
        created_by = None
        workspace_root = str(sl_node.get("workspace_path") or "")
        sidecar_status = LensGatewayLink.SidecarStatus.NOT_DEPLOYED
        is_platform_default = False
        runtime_state = gateway_readiness.gateway_runtime_state(
            None,
            sl_runtime_status=sl_status,
        )
        gateway_link_id = None

    tasks = sl_node.get("tasks") or []
    return {
        **node_payload,
        "name": (
            node_payload["name"]
            if authoritative_node
            else str(sl_node.get("name") or node_payload["name"])
        ),
        "status": node_payload["status"] if authoritative_node else sl_status,
        "ai_enabled": runtime_state["hfl_usable"],
        "sl_lensnode_uuid": (sl_uuid or None) if authoritative_node else sl_uuid,
        "lensnode_status": sl_status,
        "knowledge_source_count": knowledge_source_count,
        "workspace_root": workspace_root,
        "sidecar_status": sidecar_status,
        "scope": gateway_ownership.external_gateway_scope(link) if link else "",
        "origin": origin,
        "gateway_link_id": gateway_link_id,
        "managed_by_hfl": runtime_state["hfl_managed"],
        **runtime_state,
        # Keep the old response keys during the compatibility window. They now
        # describe installation audit only and must not be used for access.
        "owner_user_id": created_by.id if created_by else None,
        "owner_username": getattr(created_by, "username", "") if created_by else "",
        "created_by_id": created_by.id if created_by else None,
        "created_by_username": getattr(created_by, "username", "")
        if created_by
        else "",
        "owner_organization_id": link.organization_id if link else None,
        "is_platform_default": is_platform_default,
        "sl_name": str(sl_node.get("name") or ""),
        "sl_status": sl_status,
        "sl_workspace_path": str(sl_node.get("workspace_path") or ""),
        "sl_agent_version": str(sl_node.get("agent_version") or ""),
        "sl_last_heartbeat_at": sl_node.get("last_heartbeat_at"),
        "sl_registered_at": sl_node.get("registered_at"),
        "sl_tasks": tasks if isinstance(tasks, list) else [],
    }


def _cached_lensnode_row(link: LensGatewayLink) -> dict[str, Any]:
    snapshot = provisioning.sl_lensnode_snapshot_from_link(link)
    status = str(snapshot.get("sl_status") or "").strip().lower()
    if not status:
        status = (
            "online"
            if link.sidecar_status == LensGatewayLink.SidecarStatus.ONLINE
            else "offline"
        )
    return {
        "uuid": str(link.sl_lensnode_uuid or ""),
        "name": str(snapshot.get("sl_name") or link.gateway.name),
        "status": status,
        "workspace_path": str(
            snapshot.get("sl_workspace_path") or link.resolved_workspace_root()
        ),
        "agent_version": str(snapshot.get("sl_agent_version") or ""),
        "last_heartbeat_at": snapshot.get("sl_last_heartbeat_at"),
        "registered_at": snapshot.get("sl_registered_at"),
        "tasks": list(snapshot.get("sl_tasks") or []),
    }


def _knowledge_source_counts(gateway_ids: list[int]) -> dict[int, int]:
    if not gateway_ids:
        return {}
    return {
        int(row["gateway_id"]): int(row["count"])
        for row in LensKnowledgeSource.objects.filter(
            gateway_id__in=gateway_ids,
        )
        .values("gateway_id")
        .annotate(count=Count("id"))
    }


def _serialize_organization_links(
    links: list[LensGatewayLink],
    *,
    user=None,
) -> list[dict[str, Any]]:
    release_targets: dict[tuple[str, str, str, str], str] = {}
    counts = _knowledge_source_counts([link.gateway_id for link in links])
    return [
        _serialize_row(
            position=index,
            sl_node=_cached_lensnode_row(link),
            link=link,
            user=user,
            release_targets=release_targets,
            persist_snapshot=False,
            knowledge_source_count=counts.get(link.gateway_id, 0),
            authoritative_node=True,
        )
        for index, link in enumerate(links)
    ]


def _organization_gateway_queryset(*, organization, search: str = ""):
    queryset = gateway_ownership.organization_gateway_links(
        organization=organization,
    ).filter(gateway__is_deleted=False)
    term = str(search or "").strip()
    if term:
        queryset = queryset.filter(
            Q(gateway__name__icontains=term)
            | Q(gateway__ip_address__icontains=term)
            | Q(gateway__connection_ip_address__icontains=term)
        )
    return queryset.select_related("owner_user", "gateway__organization").order_by(
        "gateway__name", "gateway_id", "id"
    )


def list_organization_gateway_directory_page(
    *,
    organization,
    user=None,
    page: int = 1,
    page_size: int = GATEWAY_DIRECTORY_DEFAULT_PAGE_SIZE,
    search: str = "",
) -> dict[str, Any]:
    """Return one HFL-owned Private Data Gateway page from authoritative links."""

    page = int(page)
    if page < 1:
        raise ValidationError(
            {"page": "Ensure this value is greater than or equal to 1."}
        )
    page_size = int(page_size)
    if page_size not in GATEWAY_DIRECTORY_PAGE_SIZES:
        raise ValidationError(
            {
                "page_size": (
                    "Select one of: "
                    + ", ".join(str(value) for value in GATEWAY_DIRECTORY_PAGE_SIZES)
                    + "."
                )
            }
        )
    queryset = _organization_gateway_queryset(
        organization=organization,
        search=search,
    )
    count = queryset.count()
    start = (page - 1) * page_size
    links = list(queryset[start : start + page_size]) if start < count else []
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": _serialize_organization_links(links, user=user),
    }


def _snapshot_timestamp(raw: Any) -> datetime | None:
    try:
        refreshed_at = parse_datetime(str(raw or ""))
    except ValueError:
        return None
    if refreshed_at is None:
        return None
    if timezone.is_naive(refreshed_at):
        refreshed_at = timezone.make_aware(refreshed_at)
    return refreshed_at


def _snapshot_is_fresh(link: LensGatewayLink, *, now) -> bool:
    refreshed_at = _snapshot_timestamp(
        (link.config_json or {}).get(_SL_SNAPSHOT_REFRESHED_AT_KEY)
    )
    return bool(refreshed_at and now - _SL_SNAPSHOT_TTL <= refreshed_at <= now)


def _unpack_snapshot(value: Any, *, sl_uuid: str) -> LensNodeSnapshot | None:
    if not isinstance(value, dict):
        return None
    data = value.get("data")
    observed_at = _snapshot_timestamp(value.get("observed_at"))
    now = timezone.now()
    if (
        not isinstance(data, dict)
        or str(data.get("uuid") or "") != sl_uuid
        or observed_at is None
        or not now - _SL_SNAPSHOT_TTL <= observed_at <= now
    ):
        return None
    return LensNodeSnapshot(data=data, observed_at=observed_at)


def _fetch_lensnode_snapshot(
    sl_uuid: str, *, deadline: float, force: bool = False
) -> LensNodeSnapshot | None:
    """Best-effort status read with shared cache, single flight and backoff."""
    if time.monotonic() >= deadline:
        return None
    # Separate alias has short socket timeouts and no automatic Redis retries.
    cache = None
    key = f"gateway-directory:v2:snapshot:{sl_uuid}"
    claim_key = f"{key}:in-flight"
    claimed = False
    try:
        cache = caches["gateway_directory"]
        cached = cache.get(key)
        if not force:
            if cached == {}:
                return None
            snapshot = _unpack_snapshot(cached, sl_uuid=sl_uuid)
            if snapshot:
                return snapshot
        if time.monotonic() >= deadline:
            return None
        claimed = cache.add(claim_key, True, timeout=60)
        if not claimed:
            return None
        # Another process may have completed between our first read and claim.
        cached = cache.get(key)
        if not force:
            if cached == {}:
                return None
            snapshot = _unpack_snapshot(cached, sl_uuid=sl_uuid)
            if snapshot:
                return snapshot
        if time.monotonic() >= deadline:
            return None
        observed_at = timezone.now()
        data = sl_client.request_json(
            "GET",
            f"/api/lens/admin/lensnodes/{sl_uuid}/",
            timeout=_SL_STATUS_REFRESH_TIMEOUT_SECONDS,
            deadline=deadline,
        )
        if time.monotonic() >= deadline:
            return None
        if not isinstance(data, dict) or str(data.get("uuid") or "") != sl_uuid:
            cache.set(key, {}, timeout=_SL_STATUS_FAILURE_TTL_SECONDS)
            return None
        snapshot = LensNodeSnapshot(data=data, observed_at=observed_at)
        try:
            cache.set(
                key,
                {"data": data, "observed_at": observed_at.isoformat()},
                timeout=max(
                    1,
                    int(
                        (
                            _SL_SNAPSHOT_TTL - (timezone.now() - observed_at)
                        ).total_seconds()
                    ),
                ),
            )
        except Exception as exc:
            logger.warning(
                "Gateway snapshot cache unavailable error_type=%s", type(exc).__name__
            )
        return snapshot
    except Exception as exc:
        # Cache/transport outages must never turn directory membership into errors.
        logger.warning("Gateway status unavailable error_type=%s", type(exc).__name__)
        if claimed and cache is not None and time.monotonic() < deadline:
            try:
                cache.set(key, {}, timeout=_SL_STATUS_FAILURE_TTL_SECONDS)
            except Exception:
                pass
        return None
    finally:
        if claimed and cache is not None:
            try:
                cache.delete(claim_key)
            except Exception:
                pass


def _directory_status_patch(link: LensGatewayLink) -> dict[str, Any]:
    """Return runtime fields only, never HFL identity or lifecycle fields."""
    sl_node = _cached_lensnode_row(link)
    state = gateway_readiness.gateway_runtime_state(
        link, sl_runtime_status=sl_node["status"]
    )
    return {
        "id": link.gateway_id,
        "gateway_link_id": link.id,
        "sl_lensnode_uuid": str(link.sl_lensnode_uuid)
        if link.sl_lensnode_uuid
        else None,
        "lensnode_status": sl_node["status"],
        "sidecar_status": link.sidecar_status,
        "ai_enabled": state["hfl_usable"],
        "availability": link.gateway.availability,
        "availability_updated_at": link.gateway.availability_updated_at,
        "routable": state["hfl_agent_online"],
        "last_seen_at": link.gateway.last_seen_at,
        **state,
        "sl_name": sl_node["name"],
        "sl_status": sl_node["status"],
        "sl_workspace_path": sl_node["workspace_path"],
        "sl_agent_version": sl_node["agent_version"],
        "sl_last_heartbeat_at": sl_node["last_heartbeat_at"],
        "sl_registered_at": sl_node["registered_at"],
        "sl_tasks": sl_node["tasks"],
    }


def refresh_organization_gateway_directory_status(
    *,
    organization,
    gateway_ids: list[int],
    user=None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Refresh only requested organization Gateways and return safe row patches."""

    requested_ids = list(dict.fromkeys(int(value) for value in gateway_ids))
    if not requested_ids:
        return []
    if len(requested_ids) > max(GATEWAY_DIRECTORY_PAGE_SIZES):
        raise ValidationError(
            {"gateway_ids": "At most 100 Data Gateways can be refreshed at once."}
        )
    links = list(
        gateway_ownership.organization_gateway_links(
            organization=organization,
        )
        .filter(gateway_id__in=requested_ids, gateway__is_deleted=False)
        .order_by("gateway__name", "gateway_id", "id")
    )
    if {link.gateway_id for link in links} != set(requested_ids):
        raise ValidationError(
            {"gateway_ids": "One or more Data Gateways are unavailable."}
        )

    now = timezone.now()
    stale_links = [
        link
        for link in links
        if link.sl_lensnode_uuid and (force or not _snapshot_is_fresh(link, now=now))
    ]
    # Spend the bounded refresh budget on missing/oldest successful snapshots
    # first, so repeated slow batches do not always refresh the same page prefix.
    # This changes work priority only, not directory or response ordering.
    stale_links.sort(
        key=lambda link: _snapshot_timestamp(
            (link.config_json or {}).get(_SL_SNAPSHOT_REFRESHED_AT_KEY)
        )
        or datetime.min.replace(tzinfo=now.tzinfo)
    )
    snapshots: dict[int, LensNodeSnapshot] = {}
    if stale_links:
        deadline = time.monotonic() + _SL_STATUS_REFRESH_BUDGET_SECONDS
        futures = {}
        for link in stale_links:
            if not _SL_STATUS_SLOTS.acquire(blocking=False):
                break
            try:
                future = _SL_STATUS_EXECUTOR.submit(
                    _fetch_lensnode_snapshot,
                    str(link.sl_lensnode_uuid),
                    deadline=deadline,
                    force=force,
                )
            except Exception:
                _SL_STATUS_SLOTS.release()
                raise
            future.add_done_callback(lambda _future: _SL_STATUS_SLOTS.release())
            futures[future] = link
        done, pending = wait(
            futures,
            timeout=max(0, deadline - time.monotonic()),
        )
        for future in pending:
            future.cancel()
        for future in done:
            link = futures[future]
            try:
                snapshot = future.result()
            except Exception:
                continue
            if snapshot:
                snapshots[link.id] = snapshot

    expected_uuids = {link.id: str(link.sl_lensnode_uuid or "") for link in links}
    if snapshots:
        # Re-read under a row lock: a slow SL call must not overwrite a concurrent
        # install/remove/upgrade's config or authoritative sidecar state.
        with transaction.atomic():
            current_links = (
                gateway_ownership.organization_gateway_links(
                    organization=organization,
                )
                .filter(id__in=snapshots, gateway__is_deleted=False)
                .select_for_update(of=("self",))
                .order_by("id")
            )
            for link in current_links:
                if str(link.sl_lensnode_uuid or "") != expected_uuids[link.id]:
                    continue
                snapshot = snapshots[link.id]
                current_observed_at = _snapshot_timestamp(
                    (link.config_json or {}).get(_SL_SNAPSHOT_REFRESHED_AT_KEY)
                )
                if (
                    current_observed_at is not None
                    and current_observed_at >= snapshot.observed_at
                ) or snapshot.observed_at < timezone.now() - _SL_SNAPSHOT_TTL:
                    continue
                provisioning.apply_gateway_lensnode_snapshot(link, snapshot.data)
                config = dict(link.config_json or {})
                config[_SL_SNAPSHOT_REFRESHED_AT_KEY] = snapshot.observed_at.isoformat()
                link.config_json = config
                link.save(update_fields=["config_json", "updated_at"])

    current_links = _organization_gateway_queryset(organization=organization).filter(
        gateway_id__in=requested_ids,
    )
    return [_directory_status_patch(link) for link in current_links]


def list_admin_gateway_insight_rows(*, user=None) -> list[dict[str, Any]]:
    """All SL-admin LensNodes with optional HFL ownership metadata."""
    links = _link_index()
    release_targets: dict[tuple[str, str, str, str], str] = {}
    return [
        _serialize_row(
            position=index,
            sl_node=sl_node,
            link=links.get(str(sl_node["uuid"])),
            user=user,
            release_targets=release_targets,
        )
        for index, sl_node in enumerate(_lensnode_rows())
    ]


def list_organization_gateway_insight_rows(
    *, organization, user=None
) -> list[dict[str, Any]]:
    """Private Data Gateways in one organization, with live SL status."""

    links = _link_index(organization=organization)
    if not links:
        return []
    release_targets: dict[tuple[str, str, str, str], str] = {}
    return [
        _serialize_row(
            position=index,
            sl_node=sl_node,
            link=links.get(str(sl_node["uuid"])),
            user=user,
            release_targets=release_targets,
        )
        for index, sl_node in enumerate(_lensnode_rows())
        if str(sl_node["uuid"]) in links
    ]
