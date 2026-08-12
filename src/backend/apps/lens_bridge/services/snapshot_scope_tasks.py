"""Asynchronous snapshot operations owned by the Insight product surface."""

from __future__ import annotations

import ntpath
import posixpath
import math
from typing import Any

from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.node.models import NodeTask
from apps.node.selectors.interface import (
    get_node_task_by_correlation,
    get_node_task_for_org,
)
from apps.node.services.interface import run_agent_task_async
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.backup_task import _resolve_execution_target
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_reader,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import (
    repository_uses_bound_proxy,
)

BROWSE_CORRELATION_TYPE = "lens_bridge.snapshot_browse"
SCOPE_CORRELATION_TYPE = "lens_bridge.scope_resolve"
_MAX_BIGINT = 2**63 - 1
_MIN_BIGINT = -(2**63)


def _clean_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value:
        return ""
    if "\x00" in value or value.startswith("/") or ntpath.splitdrive(value)[0]:
        raise ValidationError({"path": "Snapshot path must be relative."})
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValidationError(
            {"path": "Snapshot path cannot contain a parent traversal."}
        )
    return posixpath.normpath("/".join(parts)) if parts else ""


def _directory_for_org(
    *,
    organization_id: int,
    directory_id: int,
) -> BackupSourceSnapshotDirectory:
    directory = (
        BackupSourceSnapshotDirectory.objects.select_related("source_snapshot")
        .filter(
            organization_id=organization_id,
            id=directory_id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        .first()
    )
    if directory is None:
        raise ValidationError({"directory_id": "Snapshot directory is not available."})
    if directory.source_snapshot.status not in {
        BackupSourceSnapshot.Status.AVAILABLE,
        BackupSourceSnapshot.Status.PARTIAL,
    }:
        raise ValidationError({"directory_id": "Snapshot is not available."})
    if not str(directory.kopia_snapshot_id or "").strip():
        raise ValidationError(
            {"directory_id": "Snapshot directory has no snapshot identifier."}
        )
    return directory


def dispatch_snapshot_operation(
    *,
    organization_id: int,
    directory_id: int,
    path: str,
    kind: str,
    correlation_type: str,
    correlation_id: str,
    extra_payload: dict[str, Any] | None = None,
    directory: BackupSourceSnapshotDirectory | None = None,
) -> NodeTask:
    """Dispatch one Insight snapshot operation without waiting for the Agent."""

    directory = directory or _directory_for_org(
        organization_id=organization_id,
        directory_id=directory_id,
    )
    repository = (
        Repository.objects.filter(
            organization_id=organization_id,
            id=directory.repository_id,
        )
        .exclude(status=Repository.Status.REMOVED)
        .first()
    )
    if repository is None:
        raise ValidationError({"directory_id": "Snapshot repository is not available."})
    fallback_target = None
    if not repository_uses_bound_proxy(repository):
        fallback_target = _resolve_execution_target(
            source_snapshot=directory.source_snapshot,
        )
    access = resolve_snapshot_repository_reader(
        directory=directory,
        repository=repository,
        fallback_node=(fallback_target.node if fallback_target is not None else None),
        source_type=directory.source_snapshot.source_type,
        source_ref_id=directory.source_snapshot.source_ref_id,
    )
    clean_path = _clean_relative_path(path)
    delivery_payload = {
        "repository": access.repository_payload,
        "snapshot_id": directory.kopia_snapshot_id,
        "path": clean_path,
        **(extra_payload or {}),
    }
    persisted_payload = {
        "snapshot_directory_id": directory.id,
        "snapshot_id": directory.kopia_snapshot_id,
        "path": clean_path,
        **(extra_payload or {}),
    }
    handle = run_agent_task_async(
        organization_id=organization_id,
        node_id=access.node.id,
        kind=kind,
        payload=delivery_payload,
        persisted_payload=persisted_payload,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        requesting_organization_id=organization_id,
    )
    return handle.task


def dispatch_snapshot_browse(
    *,
    organization_id: int,
    directory_id: int,
    path: str,
    limit: int,
    correlation_id: str,
) -> NodeTask:
    return dispatch_snapshot_operation(
        organization_id=organization_id,
        directory_id=directory_id,
        path=path,
        kind="lens.snapshot.browse",
        correlation_type=BROWSE_CORRELATION_TYPE,
        correlation_id=correlation_id,
        extra_payload={"limit": max(1, min(int(limit), 500))},
    )


def dispatch_scope_resolution(
    *,
    organization_id: int,
    directory_id: int,
    path: str,
    correlation_id: str,
) -> NodeTask:
    directory = _directory_for_org(
        organization_id=organization_id,
        directory_id=directory_id,
    )
    extra_payload = None
    if (
        not _clean_relative_path(path)
        and directory.path_type == BackupSourceSnapshotDirectory.PathType.FILE
    ):
        extra_payload = {
            "root_path_type": "file",
            "root_size_bytes": max(0, int(directory.size_bytes or 0)),
        }
    return dispatch_snapshot_operation(
        organization_id=organization_id,
        directory_id=directory_id,
        path=path,
        kind="lens.snapshot.scope.resolve",
        correlation_type=SCOPE_CORRELATION_TYPE,
        correlation_id=correlation_id,
        extra_payload=extra_payload,
        directory=directory,
    )


def task_for_org(*, organization: Organization, task_id: str) -> NodeTask:
    task = get_node_task_for_org(org=organization, task_id=task_id)
    if task is None:
        raise ValidationError({"task_id": "Insight snapshot operation was not found."})
    return task


def scope_task_for_correlation(
    *,
    organization: Organization,
    correlation_id: str,
) -> NodeTask | None:
    """Recover a previously dispatched Chat scope task after worker interruption."""

    task = get_node_task_by_correlation(
        org=organization,
        correlation_type=SCOPE_CORRELATION_TYPE,
        correlation_id=correlation_id,
    )
    if task is None or task.kind != "lens.snapshot.scope.resolve":
        return None
    return task


def scope_task_for_reference(
    *,
    organization: Organization,
    task_id: str,
    correlation_id: str,
) -> NodeTask | None:
    """Resolve only the scope task represented by one durable correlation."""

    task = get_node_task_for_org(org=organization, task_id=task_id)
    if task is None:
        return None
    if (
        task.kind != "lens.snapshot.scope.resolve"
        or task.correlation_type != SCOPE_CORRELATION_TYPE
        or str(task.correlation_id or "") != str(correlation_id or "")
    ):
        return None
    return task


def normalized_browse_entries(
    task: NodeTask, *, limit: int = 500
) -> list[dict[str, Any]]:
    result = task.result if isinstance(task.result, dict) else {}
    rows: list[dict[str, Any]] = []
    for item in list(result.get("entries") or [])[: max(1, min(int(limit), 2000))]:
        if not isinstance(item, dict):
            continue
        try:
            path = _clean_relative_path(str(item.get("path") or ""))
        except ValidationError:
            continue
        name = posixpath.basename(
            str(item.get("name") or path).strip().replace("\\", "/").rstrip("/")
        )
        if not path or not name or name in {".", ".."}:
            continue
        entry_type = str(item.get("type") or "").strip().lower()
        is_directory = bool(item.get("is_dir")) or entry_type in {
            "dir",
            "directory",
            "folder",
            "d",
        }
        rows.append(
            {
                "name": name,
                "path": path,
                "type": "dir" if is_directory else "file",
                "size_bytes": _nonnegative_int(
                    item.get("size_bytes", item.get("size", 0))
                ),
                "modified_at": item.get("modified_at") or item.get("mod_time") or None,
                "downloadable": item.get("downloadable", True) is not False,
                "has_children": item.get("has_children"),
            }
        )
    return rows


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def resolved_scope_summary(task: NodeTask) -> dict[str, int | str]:
    result = task.result if isinstance(task.result, dict) else {}
    path_type = str(result.get("path_type") or "").strip().lower()
    if path_type not in {"file", "dir"}:
        raise RuntimeError("Agent returned an invalid Insight scope type.")
    try:
        file_count = _exact_result_int(result["file_count"])
        size_bytes = _exact_result_int(result["size_bytes"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Agent returned an invalid Insight scope summary.") from exc
    if (
        file_count < 0
        or size_bytes < 0
        or (path_type == "file" and file_count != 1)
    ):
        raise RuntimeError("Agent returned an invalid Insight scope summary.")
    return {
        "path_type": path_type,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def _exact_result_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer result")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("result must be an exact integer")
        parsed = int(value)
    elif isinstance(value, str):
        parsed = int(value.strip())
    else:
        raise TypeError("result must be an integer")
    if parsed < _MIN_BIGINT or parsed > _MAX_BIGINT:
        raise OverflowError("result is outside the database integer range")
    return parsed


__all__ = [
    "BROWSE_CORRELATION_TYPE",
    "SCOPE_CORRELATION_TYPE",
    "dispatch_scope_resolution",
    "dispatch_snapshot_browse",
    "normalized_browse_entries",
    "resolved_scope_summary",
    "scope_task_for_correlation",
    "scope_task_for_reference",
    "task_for_org",
]
