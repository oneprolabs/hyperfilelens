from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_log import log_agent_dispatch, log_agent_outcome, task_log_context
from apps.node.services.interface import run_agent_task_sync
from apps.protection.models import BackupSourceSnapshotDirectory
from apps.protection.services.snapshot_browser import (
    DEFAULT_SNAPSHOT_BROWSER_TIMEOUT_SECONDS,
    DEFAULT_SNAPSHOT_BROWSE_LIMIT,
    SnapshotBrowserError,
    SnapshotBrowserForbidden,
    _agent_result_error,
    _clean_relative_path,
    _normalize_entries,
    _parent_path,
    _repository_for_directory,
)
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_reader,
)

logger = logging.getLogger(__name__)


def _get_directory(*, organization_id: int, directory_id: int) -> BackupSourceSnapshotDirectory:
    row = (
        BackupSourceSnapshotDirectory.objects.select_related("source_snapshot")
        .filter(
            organization_id=organization_id,
            id=directory_id,
        )
        .first()
    )
    if row is None:
        raise SnapshotBrowserError("Snapshot directory not found.")
    if row.status != BackupSourceSnapshotDirectory.Status.AVAILABLE:
        raise SnapshotBrowserForbidden("Snapshot directory is not available.")
    if not str(row.kopia_snapshot_id or "").strip():
        raise SnapshotBrowserForbidden("Snapshot directory has no Kopia snapshot id.")
    return row


def _target_node(*, organization_id: int, target_node_id: int) -> Node:
    node = Node.objects.filter(
        organization_id=organization_id,
        id=target_node_id,
        role__in=[NodeRole.AGENT, NodeRole.PROXY],
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError({"target_node_id": "Target node not found."})
    if node.availability != Node.Availability.ONLINE:
        raise ValidationError({"target_node_id": "Target node is offline."})
    return node


def browse_snapshot_directory_from_target(
    *,
    organization_id: int,
    directory_id: int,
    target_node_id: int,
    path: str = "",
    limit: int = DEFAULT_SNAPSHOT_BROWSE_LIMIT,
    wait_timeout_seconds: int = DEFAULT_SNAPSHOT_BROWSER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    clean_path = _clean_relative_path(path)
    row = _get_directory(organization_id=organization_id, directory_id=directory_id)
    node = _target_node(organization_id=organization_id, target_node_id=target_node_id)
    repository = _repository_for_directory(row)
    repository_access = resolve_snapshot_repository_reader(
        directory=row,
        repository=repository,
        fallback_node=node,
        source_type="agent",
        source_ref_id=node.id,
    )
    ctx = task_log_context(
        node_id=repository_access.node.id,
        kind="snapshot.browse",
        correlation_type="restore.snapshot_browser",
        correlation_id=str(row.id),
    )
    log_agent_dispatch(
        "restore snapshot browse",
        node_id=repository_access.node.id,
        kind="snapshot.browse",
        correlation_type="restore.snapshot_browser",
        correlation_id=str(row.id),
        directory_id=directory_id,
        path=clean_path,
        timeout_seconds=wait_timeout_seconds,
    )
    outcome = run_agent_task_sync(
        organization_id=organization_id,
        node_id=repository_access.node.id,
        kind="snapshot.browse",
        payload={
            "repository": repository_access.repository_payload,
            "snapshot_id": row.kopia_snapshot_id,
            "path": clean_path,
        },
        correlation_type="restore.snapshot_browser",
        correlation_id=str(row.id),
        wait_timeout_seconds=wait_timeout_seconds,
    )
    log_agent_outcome(
        "restore snapshot browse",
        outcome=outcome,
        node_id=repository_access.node.id,
        kind="snapshot.browse",
        correlation_type="restore.snapshot_browser",
        correlation_id=str(row.id),
        directory_id=directory_id,
        path=clean_path,
    )
    if getattr(outcome, "timed_out", False):
        raise SnapshotBrowserError("Snapshot browse timed out.")
    if not getattr(outcome, "ok", False):
        raise SnapshotBrowserError(_agent_result_error(outcome, "Snapshot browse failed."))
    result = outcome.result if isinstance(outcome.result, dict) else {}
    entries = _normalize_entries(
        result.get("entries"),
        base_path=clean_path,
        limit=limit,
    )
    logger.info(
        "restore snapshot browse ok %s entry_count=%s has_more=%s",
        ctx,
        len(entries),
        bool(result.get("has_more")) or len(entries) >= limit,
    )
    return {
        "directory_id": row.id,
        "snapshot_id": row.source_snapshot_id,
        "path": clean_path,
        "parent_path": _parent_path(clean_path),
        "entries": entries,
        "has_more": bool(result.get("has_more")) or len(entries) >= limit,
        "next_cursor": str(result.get("next_cursor") or ""),
    }


def get_snapshot_path_info_from_target(
    *,
    organization_id: int,
    directory_id: int,
    target_node_id: int,
    path: str,
    wait_timeout_seconds: int = DEFAULT_SNAPSHOT_BROWSER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    clean_path = _clean_relative_path(path)
    row = _get_directory(organization_id=organization_id, directory_id=directory_id)
    _target_node(organization_id=organization_id, target_node_id=target_node_id)
    if not clean_path:
        return {
            "directory_id": row.id,
            "snapshot_id": row.source_snapshot_id,
            "path": "",
            "name": "",
            "type": "dir",
            "is_dir": True,
            "size_bytes": 0,
            "modified_at": None,
            "exists": True,
        }

    parent = _parent_path(clean_path)
    logger.info(
        "restore snapshot path info started directory_id=%s target_node_id=%s path=%s",
        directory_id,
        target_node_id,
        clean_path,
    )
    parent_listing = browse_snapshot_directory_from_target(
        organization_id=organization_id,
        directory_id=directory_id,
        target_node_id=target_node_id,
        path=parent,
        limit=1000,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    match = next(
        (
            entry
            for entry in parent_listing.get("entries", [])
            if entry.get("path") == clean_path
        ),
        None,
    )
    if match is None:
        raise SnapshotBrowserError("Snapshot path not found.")
    item_type = "dir" if match.get("type") == "dir" else "file"
    logger.info(
        "restore snapshot path info ok directory_id=%s path=%s type=%s",
        directory_id,
        clean_path,
        item_type,
    )
    return {
        "directory_id": row.id,
        "snapshot_id": row.source_snapshot_id,
        "path": clean_path,
        "name": str(match.get("name") or ""),
        "type": item_type,
        "is_dir": item_type == "dir",
        "size_bytes": int(match.get("size_bytes") or 0),
        "modified_at": match.get("modified_at") or None,
        "exists": True,
    }
