from __future__ import annotations

import base64
import logging
import ntpath
import posixpath
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.node.services.internal.agent_log import task_log_context
from apps.node.services.interface import run_agent_task_sync
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.backup_task import _resolve_execution_target
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_reader,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import repository_uses_bound_proxy

DEFAULT_SNAPSHOT_BROWSE_LIMIT = 200
DEFAULT_SNAPSHOT_BROWSER_TIMEOUT_SECONDS = 120

logger = logging.getLogger(__name__)


class SnapshotBrowserError(ValueError):
    """Invalid snapshot browser request or Agent failure."""


class SnapshotBrowserForbidden(PermissionError):
    """Snapshot directory exists but cannot be browsed."""


class SnapshotArtifactUploadUnsupported(SnapshotBrowserError):
    """The selected repository reader predates artifact upload support."""


@dataclass(frozen=True)
class SnapshotFileDownload:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"
    artifact_id: int | None = None


def _clean_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value:
        return ""
    if "\x00" in value:
        raise SnapshotBrowserForbidden("Path contains invalid characters.")
    if value.startswith("/") or ntpath.splitdrive(value)[0]:
        raise SnapshotBrowserForbidden("Absolute paths are not allowed.")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise SnapshotBrowserForbidden("Parent path traversal is not allowed.")
    return posixpath.normpath("/".join(parts)) if parts else ""


def _parent_path(path: str) -> str:
    if not path:
        return ""
    parent = posixpath.dirname(path)
    return "" if parent == "." else parent


def _filename(path: str) -> str:
    return posixpath.basename(path.rstrip("/")) or "download"


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
    if row.source_snapshot.status not in {
        BackupSourceSnapshot.Status.AVAILABLE,
        BackupSourceSnapshot.Status.PARTIAL,
    }:
        raise SnapshotBrowserForbidden("Logical snapshot is not available.")
    if not str(row.kopia_snapshot_id or "").strip():
        raise SnapshotBrowserForbidden("Snapshot directory has no Kopia snapshot id.")
    return row


def _repository_for_directory(row: BackupSourceSnapshotDirectory) -> Repository:
    repository = (
        Repository.objects.filter(
            organization_id=row.organization_id,
            id=row.repository_id,
        )
        .exclude(status=Repository.Status.REMOVED)
        .first()
    )
    if repository is None:
        raise SnapshotBrowserError("Repository not found.")
    return repository


def _agent_result_error(outcome: Any, default: str) -> str:
    result = outcome.result if isinstance(getattr(outcome, "result", None), dict) else {}
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    task = getattr(outcome, "task", None)
    last_error = str(getattr(task, "last_error", "") or "").strip()
    return (last_error or stderr or stdout or default)[-2000:]


def _normalize_entries(raw_entries: Any, *, base_path: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_entries[: max(0, limit)]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw_path = str(item.get("path") or name).strip()
        try:
            rel_path = _clean_relative_path(raw_path)
        except SnapshotBrowserForbidden:
            continue
        if base_path and rel_path and not (rel_path == base_path or rel_path.startswith(f"{base_path}/")):
            rel_path = posixpath.join(base_path, name)
        if not name:
            name = _filename(rel_path)
        item_type = str(item.get("type") or "").strip().lower()
        mode = str(item.get("mode") or item.get("permissions") or "").strip().lower()
        is_dir = bool(item.get("is_dir")) or item_type in {"dir", "directory", "d", "folder"} or mode.startswith("d")
        rows.append(
            {
                "name": name,
                "path": rel_path,
                "type": "dir" if is_dir else "file",
                "size_bytes": int(item.get("size_bytes") or item.get("size") or 0),
                "modified_at": item.get("modified_at") or item.get("mod_time") or None,
                "downloadable": item.get("downloadable", True) is not False,
                "has_children": None if not is_dir else item.get("has_children"),
            }
        )
    return rows


def _run_snapshot_agent_task(
    *,
    row: BackupSourceSnapshotDirectory,
    kind: str,
    path: str,
    wait_timeout_seconds: int,
    extra_payload: dict[str, Any] | None = None,
) -> Any:
    snapshot = row.source_snapshot
    repository = _repository_for_directory(row)
    fallback_target = None
    if not repository_uses_bound_proxy(repository):
        fallback_target = _resolve_execution_target(source_snapshot=snapshot)
    repository_access = resolve_snapshot_repository_reader(
        directory=row,
        repository=repository,
        fallback_node=fallback_target.node if fallback_target is not None else None,
        source_type=snapshot.source_type,
        source_ref_id=snapshot.source_ref_id,
    )
    ctx = task_log_context(
        node_id=repository_access.node.id,
        kind=kind,
        correlation_type="protection.snapshot_browser",
        correlation_id=str(row.id),
    )
    logger.info(
        "snapshot browser agent dispatch %s directory_id=%s path=%s timeout=%ss",
        ctx,
        row.id,
        path,
        wait_timeout_seconds,
    )
    payload = {
        "repository": repository_access.repository_payload,
        "snapshot_id": row.kopia_snapshot_id,
        "path": path,
    }
    if extra_payload:
        payload.update(extra_payload)
    persisted_payload = {
        "snapshot_id": row.kopia_snapshot_id,
        "path": path,
    }
    if isinstance(payload.get("paths"), list):
        persisted_payload["paths"] = payload["paths"]
    if isinstance(payload.get("artifact_upload"), dict):
        upload = payload["artifact_upload"]
        persisted_payload["artifact_upload"] = {
            "artifact_id": upload.get("artifact_id"),
            "path": upload.get("path"),
            "max_bytes": upload.get("max_bytes"),
        }
    outcome = run_agent_task_sync(
        organization_id=row.organization_id,
        node_id=repository_access.node.id,
        kind=kind,
        payload=payload,
        persisted_payload=persisted_payload,
        correlation_type="protection.snapshot_browser",
        correlation_id=str(row.id),
        wait_timeout_seconds=wait_timeout_seconds,
    )
    if outcome.timed_out:
        logger.warning("snapshot browser agent timed out %s", ctx)
    elif not outcome.ok:
        logger.warning(
            "snapshot browser agent failed %s task_status=%s error=%s",
            ctx,
            outcome.task.status,
            _agent_result_error(outcome, "snapshot browser task failed")[:500],
        )
    else:
        logger.info(
            "snapshot browser agent ok %s task_id=%s",
            ctx,
            getattr(outcome.task, "id", "-"),
        )
    return outcome


def _snapshot_browser_timeout_seconds(default: int = DEFAULT_SNAPSHOT_BROWSER_TIMEOUT_SECONDS) -> int:
    raw = getattr(settings, "PROTECTION_SNAPSHOT_BROWSER_TIMEOUT_SECONDS", default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def browse_snapshot_directory(
    *,
    organization_id: int,
    directory_id: int,
    path: str = "",
    limit: int = DEFAULT_SNAPSHOT_BROWSE_LIMIT,
    wait_timeout_seconds: int | None = None,
) -> dict[str, Any]:
    clean_path = _clean_relative_path(path)
    row = _get_directory(organization_id=organization_id, directory_id=directory_id)
    outcome = _run_snapshot_agent_task(
        row=row,
        kind="snapshot.browse",
        path=clean_path,
        wait_timeout_seconds=_snapshot_browser_timeout_seconds() if wait_timeout_seconds is None else wait_timeout_seconds,
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
    return {
        "directory_id": row.id,
        "snapshot_id": row.source_snapshot_id,
        "path": clean_path,
        "parent_path": _parent_path(clean_path),
        "entries": entries,
        "has_more": bool(result.get("has_more")) or len(entries) >= limit,
        "next_cursor": str(result.get("next_cursor") or ""),
    }


def download_snapshot_file(
    *,
    organization_id: int,
    directory_id: int,
    path: str,
    wait_timeout_seconds: int | None = None,
    upload_artifact=None,
    paths: list[str] | None = None,
) -> SnapshotFileDownload:
    clean_path = _clean_relative_path(path)
    row = _get_directory(organization_id=organization_id, directory_id=directory_id)
    clean_paths = [_clean_relative_path(item) for item in paths or []]
    if not clean_path and not clean_paths and row.path_type != BackupSourceSnapshotDirectory.PathType.FILE:
        raise SnapshotBrowserForbidden("File path is required.")
    extra_payload: dict[str, Any] = {}
    if clean_paths:
        extra_payload["paths"] = clean_paths
    if upload_artifact is not None:
        from apps.protection.services.snapshot_download import prepare_snapshot_artifact_upload

        repository = _repository_for_directory(row)
        fallback_target = None
        if not repository_uses_bound_proxy(repository):
            fallback_target = _resolve_execution_target(source_snapshot=row.source_snapshot)
        repository_access = resolve_snapshot_repository_reader(
            directory=row,
            repository=repository,
            fallback_node=fallback_target.node if fallback_target is not None else None,
            source_type=row.source_snapshot.source_type,
            source_ref_id=row.source_snapshot.source_ref_id,
        )
        metadata = repository_access.node.metadata if isinstance(repository_access.node.metadata, dict) else {}
        inventory = metadata.get("inventory") if isinstance(metadata.get("inventory"), dict) else {}
        capabilities = inventory.get("capabilities", metadata.get("capabilities", []))
        capabilities = capabilities if isinstance(capabilities, (list, tuple, set)) else []
        if clean_paths and "snapshot_artifact_upload_v1" not in capabilities:
            raise SnapshotArtifactUploadUnsupported(
                "The repository access Agent must be upgraded before downloading multiple snapshot paths."
            )
        extra_payload["artifact_upload"] = prepare_snapshot_artifact_upload(
            artifact=upload_artifact,
            node_id=repository_access.node.id,
        )
    outcome = _run_snapshot_agent_task(
        row=row,
        kind="snapshot.download",
        path=clean_path,
        wait_timeout_seconds=_snapshot_browser_timeout_seconds() if wait_timeout_seconds is None else wait_timeout_seconds,
        extra_payload=extra_payload,
    )
    if upload_artifact is not None:
        upload_artifact.refresh_from_db()
        if upload_artifact.status == upload_artifact.Status.READY:
            return SnapshotFileDownload(
                filename=upload_artifact.filename,
                content=b"",
                content_type=upload_artifact.content_type,
                artifact_id=upload_artifact.id,
            )
    if getattr(outcome, "timed_out", False):
        raise SnapshotBrowserError("Snapshot download timed out.")
    if not getattr(outcome, "ok", False):
        raise SnapshotBrowserError(_agent_result_error(outcome, "Snapshot download failed."))
    result = outcome.result if isinstance(outcome.result, dict) else {}
    if result.get("result_truncated"):
        raise SnapshotBrowserError(
            "Snapshot download content exceeded the legacy Agent result limit. Upgrade the Agent and retry."
        )
    if upload_artifact is not None and int(result.get("artifact_id") or 0) == int(upload_artifact.id):
        upload_artifact.refresh_from_db()
        if upload_artifact.status != upload_artifact.Status.READY:
            raise SnapshotBrowserError("Snapshot download upload did not finalize the artifact.")
        return SnapshotFileDownload(
            filename=upload_artifact.filename,
            content=b"",
            content_type=upload_artifact.content_type,
            artifact_id=upload_artifact.id,
        )
    raw = str(result.get("content_base64") or "").strip()
    size_bytes: int | None
    try:
        size_bytes = int(result.get("size_bytes")) if result.get("size_bytes") is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    if not raw:
        if size_bytes == 0:
            content = b""
        else:
            raise SnapshotBrowserError("Snapshot download returned no content.")
    else:
        try:
            content = base64.b64decode(raw, validate=True)
        except ValueError as exc:
            raise SnapshotBrowserError("Snapshot download returned invalid content.") from exc
    filename = str(result.get("filename") or "").strip()
    if not filename or (not clean_path and filename == "download"):
        filename = _filename(clean_path or row.source_path)
    content_type = str(result.get("content_type") or "").strip() or "application/octet-stream"
    return SnapshotFileDownload(filename=filename, content=content, content_type=content_type)
