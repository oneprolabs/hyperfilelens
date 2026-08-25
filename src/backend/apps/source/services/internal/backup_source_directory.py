"""Directory browsing for backup-selectable sources via Agent WSS tasks."""

from __future__ import annotations

import logging
import ntpath
import posixpath
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.monitor.services.internal.node_monitor import resource_type_for_role
from apps.monitor.services.internal.resource_metrics import latest_resource_metric
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.interface import run_agent_task_sync
from apps.source.constants import ResourceType
from apps.source.services.internal.nas_agent import nas_payload_for_resource
from apps.source.services.internal.nas_display import nfs_export_path
from apps.source.services.internal.nas_share_path import (
    normalize_user_share_path,
    share_root_label,
    to_mount_path,
    to_share_relative,
)
from apps.source.models import SourceResource
from apps.source.services.internal.selectable_ids import parse_selectable_id


DEFAULT_DIRECTORY_LIMIT = 200
_MOUNT_CACHE_MAX_AGE = timedelta(seconds=120)

logger = logging.getLogger(__name__)


class BackupSourceDirectoryError(ValueError):
    """Invalid directory browse request."""

    def __init__(self, message: str, *, agent_error_code: str = "") -> None:
        super().__init__(message)
        self.agent_error_code = str(agent_error_code or "").strip()


class BackupSourceDirectoryInvalid(BackupSourceDirectoryError):
    """The source selection or local source configuration is invalid."""


class BackupSourceDirectoryNotFound(LookupError):
    """The requested source does not exist in the organization."""


class BackupSourceDirectoryForbidden(PermissionError):
    """The requested path is outside the permitted source root."""


class BackupSourceDirectoryTimeout(TimeoutError):
    """The Agent did not return a directory listing in time."""


@dataclass(frozen=True)
class BrowseTarget:
    source_kind: str
    source_ref_id: int
    node: Node
    path: str
    root_path: str
    nas_payload: dict[str, Any] | None = None
    share_root_label: str = ""
    share_export_path: str = ""
    user_path: str = ""


def _agent_task_error_code(outcome: Any) -> str:
    """Return a stable Agent error code when the task supplied one."""
    try:
        result = outcome.result
    except (AttributeError, TypeError, ValueError):
        return ""
    if not isinstance(result, dict):
        return ""
    return str(result.get("error_code") or "").strip()


def _is_windows_path(path: str) -> bool:
    return "\\" in path or (len(path) >= 2 and path[1] == ":")


def _clean_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    if _is_windows_path(value):
        clean = ntpath.normpath(value)
        if len(clean) >= 2 and clean[1] == ":":
            rest = clean[2:].lstrip("\\/")
            if rest in {"", "."}:
                return f"{clean[0].upper()}:\\"
        return clean
    return posixpath.normpath(value)


def _node_os_name(node: Node) -> str:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = (
        metadata.get("inventory") if isinstance(metadata.get("inventory"), dict) else {}
    )
    return str(inventory.get("os") or node.os_name or "").strip().lower()


def _is_windows_node(node: Node) -> bool:
    return "windows" in _node_os_name(node)


def _agent_default_path(node: Node) -> str:
    if _is_windows_node(node):
        return "C:\\"
    return "/"


def _is_current_user_agent(node: Node) -> bool:
    return (
        node.role == NodeRole.AGENT
        and node.installation_mode
        in (
            Node.InstallationMode.USER,
            Node.InstallationMode.USER_CONTINUOUS,
        )
    )


def _is_subpath(root: str, path: str) -> bool:
    clean_root = _clean_path(root)
    clean_path = _clean_path(path)
    if not clean_root or not clean_path:
        return False
    if _is_windows_path(clean_root) or _is_windows_path(clean_path):
        root_cmp = ntpath.normcase(clean_root.rstrip("\\/"))
        path_cmp = ntpath.normcase(clean_path.rstrip("\\/"))
        return path_cmp == root_cmp or path_cmp.startswith(root_cmp + "\\")
    root_cmp = clean_root.rstrip("/") or "/"
    path_cmp = clean_path.rstrip("/") or "/"
    return path_cmp == root_cmp or path_cmp.startswith(root_cmp + "/")


def _basename(path: str) -> str:
    clean = _clean_path(path)
    if _is_windows_path(clean):
        return ntpath.basename(clean.rstrip("\\/")) or clean
    return posixpath.basename(clean.rstrip("/")) or clean


def _nas_payload_for_log(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    safe = dict(payload)
    if safe.get("password"):
        safe["password"] = "***"
    return safe


def _resolve_agent_target(
    *, organization_id: int, ref_id: int, path: str
) -> BrowseTarget:
    node = Node.objects.filter(
        organization_id=organization_id,
        id=ref_id,
        role=NodeRole.AGENT,
        is_deleted=False,
    ).first()
    if node is None:
        raise BackupSourceDirectoryNotFound("Agent source not found.")
    path_clean = _clean_path(path)
    resolved_path = (
        path_clean
        if path_clean or _is_current_user_agent(node)
        else _agent_default_path(node)
    )
    return BrowseTarget(
        source_kind="agent",
        source_ref_id=ref_id,
        node=node,
        path=resolved_path,
        root_path="",
    )


def _resolve_proxy_target(
    *, organization_id: int, ref_id: int, path: str
) -> BrowseTarget:
    node = Node.objects.filter(
        organization_id=organization_id,
        id=ref_id,
        role=NodeRole.PROXY,
        is_deleted=False,
    ).first()
    if node is None:
        raise BackupSourceDirectoryNotFound("Proxy node not found.")
    resolved_path = _clean_path(path) or _agent_default_path(node)
    return BrowseTarget(
        source_kind="proxy",
        source_ref_id=ref_id,
        node=node,
        path=resolved_path,
        root_path="",
    )


def _resolve_nas_target(
    *, organization_id: int, ref_id: int, path: str
) -> BrowseTarget:
    resource = (
        SourceResource.objects.filter(
            organization_id=organization_id,
            id=ref_id,
            resource_type=ResourceType.NAS,
            is_deleted=False,
        )
        .select_related("bound_node")
        .first()
    )
    if resource is None:
        raise BackupSourceDirectoryNotFound("NAS source not found.")
    if resource.bound_node is None:
        raise BackupSourceDirectoryInvalid("NAS source is not bound to a proxy node.")
    if resource.bound_node.role != NodeRole.PROXY:
        raise BackupSourceDirectoryInvalid("NAS source must be bound to a proxy node.")

    root_path = _clean_path(resource.effective_mount_point())
    if not root_path:
        raise BackupSourceDirectoryInvalid("NAS source mount point is empty.")

    config = resource.config if isinstance(resource.config, dict) else {}
    export_path = nfs_export_path(resource_type=resource.resource_type, config=config)
    path_clean = _clean_path(path)
    if not path_clean:
        user_path = "/"
        resolved_path = root_path
    else:
        user_path = normalize_user_share_path(
            mount_root=root_path,
            export_path=export_path,
            user_path=path_clean,
        )
        resolved_path = to_mount_path(root_path, user_path)
    if not _is_subpath(root_path, resolved_path):
        raise BackupSourceDirectoryForbidden(
            "Path is outside the mounted NAS directory."
        )

    return BrowseTarget(
        source_kind="nas",
        source_ref_id=ref_id,
        node=resource.bound_node,
        path=resolved_path,
        root_path=root_path,
        nas_payload=nas_payload_for_resource(resource),
        share_root_label=share_root_label(
            resource_type=resource.resource_type, config=config
        ),
        share_export_path=export_path,
        user_path=user_path,
    )


def _resolve_target(*, organization_id: int, source_id: str, path: str) -> BrowseTarget:
    parsed = parse_selectable_id(source_id)
    if parsed is None:
        raise BackupSourceDirectoryInvalid(
            "source_id must be an agent:/nas:/proxy: id."
        )
    kind, ref_id = parsed
    if kind == "agent":
        return _resolve_agent_target(
            organization_id=organization_id, ref_id=ref_id, path=path
        )
    if kind == "nas":
        return _resolve_nas_target(
            organization_id=organization_id, ref_id=ref_id, path=path
        )
    if kind == "proxy":
        return _resolve_proxy_target(
            organization_id=organization_id, ref_id=ref_id, path=path
        )
    raise BackupSourceDirectoryInvalid("Unsupported backup source kind.")


def _entry_is_dir(entry: dict[str, Any]) -> bool:
    type_value = str(entry.get("path_type") or entry.get("type") or "").strip().lower()
    if type_value in {"directory", "dir", "folder", "d"}:
        return True
    mode = str(entry.get("mode") or entry.get("permissions") or "").strip().lower()
    if mode.startswith("d"):
        return True
    value = entry.get("is_dir")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "dir", "directory"}
    return False


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_entries(
    *,
    target: BrowseTarget,
    raw_entries: Any,
    include_files: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        is_dir = _entry_is_dir(item)
        if not include_files and not is_dir:
            continue
        path = _normalize_mount_path(str(item.get("path") or ""))
        if not path:
            continue
        if target.source_kind == "nas" and not _is_subpath(target.root_path, path):
            continue
        if target.source_kind == "nas":
            path = to_share_relative(target.root_path, path)
        if path in seen:
            continue
        seen.add(path)
        name = str(item.get("name") or "").strip() or _basename(path)
        protected = _safe_bool(item.get("protected", False))
        selectable = _safe_bool(item.get("selectable", True)) and not protected
        rows.append(
            {
                "label": name,
                "path": path,
                "isLeaf": not is_dir or not selectable,
                "is_dir": is_dir,
                "path_type": "directory" if is_dir else "file",
                "size": _safe_int(item.get("size")),
                "mod_time": str(item.get("mod_time") or ""),
                "selectable": selectable,
                "protected": protected,
                "protection_reason": str(item.get("protection_reason") or ""),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            0 if row["is_dir"] else 1,
            str(row["label"]).lower(),
            str(row["path"]).lower(),
        ),
    )


def _mount_label(mountpoint: str, device: str = "") -> str:
    clean = _clean_path(mountpoint)
    if _is_windows_path(clean):
        if len(clean) >= 2 and clean[1] == ":":
            return f"{clean[:2]}\\"
    name = _basename(clean)
    if name and name not in {"/", "\\"}:
        return name
    return clean or mountpoint


def _normalize_mount_path(path: str) -> str:
    return _clean_path(path)


def _is_top_level_mount_request(
    *, target: BrowseTarget, path_clean: str, include_files: bool, cursor: str
) -> bool:
    if target.source_kind not in ("agent", "proxy"):
        return False
    if _is_current_user_agent(target.node) and not _is_windows_node(target.node):
        return False
    if cursor:
        return False
    return not path_clean


def _entries_from_cached_disks(disks: Any) -> list[dict[str, Any]]:
    if not isinstance(disks, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in disks:
        if not isinstance(item, dict):
            continue
        path = _normalize_mount_path(str(item.get("mountpoint") or ""))
        if not path:
            continue
        key = path.lower() if _is_windows_path(path) else path
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "label": _mount_label(path, str(item.get("device") or "")),
                "path": path,
                "isLeaf": False,
                "is_dir": True,
                "path_type": "directory",
                "size": _safe_int(item.get("total")),
                "mod_time": "",
            }
        )
    return sorted(
        rows, key=lambda row: (str(row["label"]).lower(), str(row["path"]).lower())
    )


def _try_cached_mount_listing(
    *,
    organization_id: int,
    node: Node,
    target: BrowseTarget,
    source_id: str,
) -> dict[str, Any] | None:
    """Serve top-level disk/mount listing from recent heartbeat metrics when available."""

    resource_type = resource_type_for_role(node.role)
    if not resource_type:
        return None
    row = latest_resource_metric(
        organization_id=organization_id,
        resource_type=resource_type,
        resource_id=str(node.id),
    )
    if row is None:
        return None
    if row.timestamp < timezone.now() - _MOUNT_CACHE_MAX_AGE:
        return None
    metrics = row.metrics if isinstance(row.metrics, dict) else {}
    entries = _entries_from_cached_disks(metrics.get("disks"))
    if not entries:
        return None
    root_path = entries[0]["path"] if entries else ""
    return {
        "source_id": source_id,
        "source_kind": target.source_kind,
        "source_ref_id": target.source_ref_id,
        "node_id": target.node.id,
        "path": "",
        "root_path": target.root_path,
        "root": _root_entry(
            BrowseTarget(
                source_kind=target.source_kind,
                source_ref_id=target.source_ref_id,
                node=target.node,
                path=root_path,
                root_path=target.root_path,
            )
        ),
        "task_id": "",
        "count": len(entries),
        "has_more": False,
        "next_cursor": "",
        "entries": entries,
        "cached": True,
    }


def _root_entry(target: BrowseTarget) -> dict[str, Any]:
    if target.source_kind == "nas":
        return {
            "label": target.share_root_label or "/",
            "path": "/",
            "isLeaf": False,
            "is_dir": True,
            "path_type": "directory",
            "size": 0,
            "mod_time": "",
        }
    path = target.root_path or target.path
    return {
        "label": "Home"
        if _is_current_user_agent(target.node) and target.root_path
        else _basename(path),
        "path": path,
        "isLeaf": False,
        "is_dir": True,
        "path_type": "directory",
        "size": 0,
        "mod_time": "",
    }


def _nas_directory_response_base(
    *, target: BrowseTarget, source_id: str
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_kind": target.source_kind,
        "source_ref_id": target.source_ref_id,
        "node_id": target.node.id,
        "path": target.user_path or "/",
        "root_path": "/",
        "mount_path": target.root_path,
        "root": _root_entry(target),
    }


def list_backup_source_directories(
    *,
    organization_id: int,
    source_id: str,
    path: str = "",
    wait_timeout_seconds: int = 10,
    limit: int = DEFAULT_DIRECTORY_LIMIT,
    include_files: bool = False,
    include_metadata: bool | None = None,
    cursor: str = "",
) -> dict[str, Any]:
    """List child paths for a backup source through its Agent/Proxy WSS channel."""

    path_clean = _clean_path(path)
    target = _resolve_target(
        organization_id=organization_id,
        source_id=source_id,
        path=path,
    )
    request_root = not path_clean
    if target.source_kind == "nas" and request_root:
        logger.info(
            "backup source directory nas root source_id=%s source_ref_id=%s mount_path=%s",
            source_id,
            target.source_ref_id,
            target.root_path,
        )
        return {
            **_nas_directory_response_base(target=target, source_id=source_id),
            "task_id": "",
            "count": 0,
            "has_more": False,
            "next_cursor": "",
            "entries": [],
        }

    use_mount_listing = _is_top_level_mount_request(
        target=target,
        path_clean=path_clean,
        include_files=include_files,
        cursor=cursor,
    )
    if use_mount_listing:
        # A current-user Windows Agent must enumerate drives with its own
        # process token. Heartbeat disk inventory may reflect broader access.
        if not _is_current_user_agent(target.node):
            cached = _try_cached_mount_listing(
                organization_id=organization_id,
                node=target.node,
                target=target,
                source_id=source_id,
            )
            if cached is not None:
                return cached
    collect_metadata = (
        bool(include_files) if include_metadata is None else bool(include_metadata)
    )

    payload: dict[str, Any] = {
        "path": "" if use_mount_listing else target.path,
        "list_mounts": use_mount_listing,
        "dirs_only": True if use_mount_listing else not include_files,
        "include_metadata": False if use_mount_listing else collect_metadata,
        "limit": limit,
    }
    if cursor:
        payload["cursor"] = cursor
    if target.nas_payload:
        payload["nas"] = target.nas_payload

    logger.info(
        "backup source directory dispatch source_id=%s source_kind=%s node_id=%s path=%s timeout=%ss nas=%s",
        source_id,
        target.source_kind,
        target.node.id,
        payload.get("path") or target.path,
        wait_timeout_seconds,
        _nas_payload_for_log(target.nas_payload),
    )

    outcome = run_agent_task_sync(
        organization_id=organization_id,
        node_id=target.node.id,
        kind="explorer.list",
        payload=payload,
        correlation_type="source.directory",
        correlation_id=source_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    task_id = getattr(outcome.task, "id", "")
    task_status = getattr(outcome.task, "status", "")
    last_error = getattr(outcome.task, "last_error", "") or ""
    if outcome.timed_out:
        logger.warning(
            "backup source directory timed out source_id=%s node_id=%s task_id=%s "
            "wait_seconds=%s task_status=%s last_error=%s",
            source_id,
            target.node.id,
            task_id,
            wait_timeout_seconds,
            task_status,
            last_error,
        )
        raise BackupSourceDirectoryTimeout("Directory listing timed out.")
    if not outcome.ok:
        error = last_error
        if not error and isinstance(outcome.stream_message, dict):
            error = str(
                outcome.stream_message.get("error")
                or outcome.stream_message.get("message")
                or ""
            )
        if not error:
            error = f"Directory listing failed (task status: {task_status})."
        logger.warning(
            "backup source directory failed source_id=%s node_id=%s task_id=%s task_status=%s error=%s",
            source_id,
            target.node.id,
            task_id,
            task_status,
            error,
        )
        raise BackupSourceDirectoryError(
            error,
            agent_error_code=_agent_task_error_code(outcome),
        )

    try:
        result = outcome.result
    except (TypeError, ValueError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    entries = _normalize_entries(
        target=target,
        raw_entries=result.get("entries"),
        include_files=include_files,
    )
    logger.info(
        "backup source directory ok source_id=%s node_id=%s task_id=%s path=%s count=%s",
        source_id,
        target.node.id,
        task_id,
        target.user_path or target.path,
        len(entries),
    )
    if target.source_kind == "nas":
        return {
            **_nas_directory_response_base(target=target, source_id=source_id),
            "task_id": outcome.task_id,
            "count": len(entries),
            "has_more": _safe_bool(result.get("has_more")),
            "next_cursor": str(result.get("next_cursor") or ""),
            "entries": entries,
        }
    response_path = target.path
    response_root_path = target.root_path
    response_target = target
    response_entries = entries
    response_has_more = _safe_bool(result.get("has_more"))
    response_next_cursor = str(result.get("next_cursor") or "")
    if (
        request_root
        and _is_current_user_agent(target.node)
        and not _is_windows_node(target.node)
    ):
        home_path = _normalize_mount_path(str(result.get("path") or ""))
        if not home_path:
            raise BackupSourceDirectoryError(
                "Current User Agent did not return its Home directory path."
            )
        response_path = home_path
        response_root_path = home_path
        response_target = BrowseTarget(
            source_kind=target.source_kind,
            source_ref_id=target.source_ref_id,
            node=target.node,
            path=home_path,
            root_path=home_path,
        )
        # The top-level item is a virtual Home root. Its children are loaded
        # only after that root is expanded, so Agent-side pagination here must
        # not create a sibling "load more" item in the tree.
        response_entries = []
        response_has_more = False
        response_next_cursor = ""
    elif (
        request_root
        and use_mount_listing
        and response_entries
        and not response_target.path
    ):
        response_target = BrowseTarget(
            source_kind=target.source_kind,
            source_ref_id=target.source_ref_id,
            node=target.node,
            path=response_entries[0]["path"],
            root_path=target.root_path,
        )
    return {
        "source_id": source_id,
        "source_kind": target.source_kind,
        "source_ref_id": target.source_ref_id,
        "node_id": target.node.id,
        "path": response_path,
        "root_path": response_root_path,
        "root": _root_entry(response_target),
        "task_id": outcome.task_id,
        "count": len(response_entries),
        "has_more": response_has_more,
        "next_cursor": response_next_cursor,
        "entries": response_entries,
    }


def get_backup_source_path_info(
    *,
    organization_id: int,
    source_id: str,
    path: str,
    wait_timeout_seconds: int = 10,
    include_metadata: bool | None = None,
) -> dict[str, Any]:
    """Validate one source path and return file/folder metadata."""

    path_clean = _clean_path(path)
    if not path_clean:
        raise BackupSourceDirectoryInvalid("path is required.")

    target = _resolve_target(
        organization_id=organization_id,
        source_id=source_id,
        path=path_clean,
    )
    payload: dict[str, Any] = {
        "path": target.path,
    }
    if include_metadata is not None:
        payload["include_metadata"] = bool(include_metadata)
    if target.nas_payload:
        payload["nas"] = target.nas_payload

    logger.info(
        "backup source path info dispatch source_id=%s source_kind=%s node_id=%s path=%s timeout=%ss",
        source_id,
        target.source_kind,
        target.node.id,
        target.path,
        wait_timeout_seconds,
    )

    outcome = run_agent_task_sync(
        organization_id=organization_id,
        node_id=target.node.id,
        kind="path.info",
        payload=payload,
        correlation_type="source.path_info",
        correlation_id=source_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    task_id = getattr(outcome.task, "id", "")
    task_status = getattr(outcome.task, "status", "")
    last_error = getattr(outcome.task, "last_error", "") or ""
    try:
        raw_result = outcome.result
    except (TypeError, ValueError):
        raw_result = {}
    if outcome.timed_out:
        logger.warning(
            "backup source path info timed out source_id=%s node_id=%s task_id=%s path=%s",
            source_id,
            target.node.id,
            task_id,
            target.path,
        )
        raise BackupSourceDirectoryTimeout("Path validation timed out.")
    if not outcome.ok:
        error = last_error
        if not error and isinstance(outcome.stream_message, dict):
            error = str(
                outcome.stream_message.get("error")
                or outcome.stream_message.get("message")
                or ""
            )
        if not error and isinstance(raw_result, dict):
            error = str(raw_result.get("error") or raw_result.get("stderr") or "")
        if not error:
            error = f"Path validation failed (task status: {task_status})."
        logger.warning(
            "backup source path info failed source_id=%s node_id=%s task_id=%s path=%s error=%s",
            source_id,
            target.node.id,
            task_id,
            target.path,
            error[:500],
        )
        raise BackupSourceDirectoryError(
            error,
            agent_error_code=_agent_task_error_code(outcome),
        )

    result = raw_result if isinstance(raw_result, dict) else {}
    path_value = _normalize_mount_path(str(result.get("path") or target.path))
    if target.source_kind == "nas":
        if not _is_subpath(target.root_path, path_value):
            raise BackupSourceDirectoryForbidden(
                "Path is outside the mounted NAS directory."
            )
        user_path = to_share_relative(target.root_path, path_value)
        is_dir = _entry_is_dir(result)
        path_type = "directory" if is_dir else "file"
        response = {
            **_nas_directory_response_base(target=target, source_id=source_id),
            "path": user_path,
            "label": str(result.get("name") or "").strip() or _basename(path_value),
            "exists": True,
            "is_dir": is_dir,
            "path_type": path_type,
            "isLeaf": not is_dir,
            "task_id": str(task_id),
        }
        if include_metadata is not False:
            response["size"] = _safe_int(result.get("size"))
            response["mod_time"] = str(result.get("mod_time") or "")
        logger.info(
            "backup source path info ok source_id=%s node_id=%s task_id=%s path=%s path_type=%s",
            source_id,
            target.node.id,
            task_id,
            user_path,
            path_type,
        )
        return response

    is_dir = _entry_is_dir(result)
    path_type = "directory" if is_dir else "file"
    response = {
        "source_id": source_id,
        "source_kind": target.source_kind,
        "source_ref_id": target.source_ref_id,
        "node_id": target.node.id,
        "path": path_value,
        "label": str(result.get("name") or "").strip() or _basename(path_value),
        "exists": True,
        "is_dir": is_dir,
        "path_type": path_type,
        "isLeaf": not is_dir,
        "task_id": str(task_id),
    }
    if include_metadata is not False:
        response["size"] = _safe_int(result.get("size"))
        response["mod_time"] = str(result.get("mod_time") or "")
    logger.info(
        "backup source path info ok source_id=%s node_id=%s task_id=%s path=%s path_type=%s",
        source_id,
        target.node.id,
        task_id,
        path_value,
        path_type,
    )
    return response
