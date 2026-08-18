"""Asynchronous snapshot operations owned by the Insight product surface."""

from __future__ import annotations

import ntpath
import posixpath
import math
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services.gateway_execution import context_for_gateway_link
from apps.node.models import NodeTask
from apps.node.services.capabilities import missing_node_capabilities
from apps.node.selectors.interface import (
    get_node_task_by_correlation_for_requesting_org,
    get_node_task_for_requesting_org,
)
from apps.node.services.interface import run_agent_task_async
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_reader,
)
from apps.storage.services.internal.repository_workload import (
    RepositoryWorkload,
    lock_repositories_for_workload,
)

BROWSE_CORRELATION_TYPE = "lens_bridge.snapshot_browse"
SCOPE_CORRELATION_TYPE = "lens_bridge.scope_resolve"
_MAX_BIGINT = 2**63 - 1
_MIN_BIGINT = -(2**63)
_BROWSE_CAPABILITY = "snapshot_browse_v1"
_SCOPE_CAPABILITY = "snapshot_scope_resolve_v1"
_UNSUPPORTED_CONTENT_ERROR_CODE = "INSIGHT_UNSUPPORTED_CONTENT_TYPE"
UNSUPPORTED_CONTENT_MESSAGE = (
    "Symbolic links, sockets, pipes, and device files cannot be used in Chat."
)


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
    backup_source_snapshot_id: int,
) -> BackupSourceSnapshotDirectory:
    directory = (
        BackupSourceSnapshotDirectory.objects.select_related("source_snapshot")
        .filter(
            organization_id=organization_id,
            id=directory_id,
            source_snapshot_id=backup_source_snapshot_id,
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


def _gateway_reader_context(
    *,
    organization_id: int,
    gateway_link_id: int,
    requesting_user_id: int,
):
    tenant_organization = Organization.objects.filter(pk=organization_id).first()
    if tenant_organization is None:
        raise ValidationError({"organization_id": "Organization is not available."})
    gateway_link = (
        LensGatewayLink.objects.select_related("organization", "gateway")
        .filter(pk=gateway_link_id, is_deleted=False)
        .first()
    )
    if gateway_link is None:
        raise ValidationError({"gateway_link_id": "Data gateway is not available."})
    expected_owner_user_id = (
        requesting_user_id
        if gateway_link.scope == LensGatewayLink.GatewayScope.USER
        else None
    )
    return context_for_gateway_link(
        tenant_organization=tenant_organization,
        gateway_link=gateway_link,
        expected_owner_user_id=expected_owner_user_id,
        require_ready=True,
    )


def _require_snapshot_capability(*, node, kind: str) -> None:
    capability = {
        "lens.snapshot.browse": _BROWSE_CAPABILITY,
        "lens.snapshot.scope.resolve": _SCOPE_CAPABILITY,
    }.get(kind)
    if capability and missing_node_capabilities(node, [capability]):
        raise ValidationError(
            {
                "detail": (
                    "Upgrade the Repository Reader Agent before browsing or "
                    "using this backup in Chat."
                )
            }
        )


@transaction.atomic
def dispatch_snapshot_operation(
    *,
    organization_id: int,
    directory_id: int,
    backup_source_snapshot_id: int,
    gateway_link_id: int,
    requesting_user_id: int,
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
        backup_source_snapshot_id=backup_source_snapshot_id,
    )
    if (
        directory.id != directory_id
        or directory.organization_id != organization_id
        or directory.source_snapshot_id != backup_source_snapshot_id
    ):
        raise ValidationError(
            {"directory_id": "Snapshot directory does not belong to the selected snapshot."}
        )
    gateway_context = _gateway_reader_context(
        organization_id=organization_id,
        gateway_link_id=gateway_link_id,
        requesting_user_id=requesting_user_id,
    )
    try:
        repository = lock_repositories_for_workload(
            organization_id=organization_id,
            repository_ids=[directory.repository_id],
            workload=RepositoryWorkload.RESTORE_READ,
        )[0]
    except (DjangoValidationError, ValidationError) as exc:
        raise ValidationError(
            {"directory_id": "Snapshot repository is not available."}
        ) from exc

    access = resolve_snapshot_repository_reader(
        directory=directory,
        repository=repository,
        fallback_node=gateway_context.gateway,
        source_type=directory.source_snapshot.source_type,
        source_ref_id=directory.source_snapshot.source_ref_id,
    )
    reader_identity_is_valid = (
        access.mode == "fallback_node"
        and access.node.id == gateway_context.gateway.id
        and access.node.organization_id
        == gateway_context.execution_organization.id
    ) or (
        access.mode == "bound_proxy"
        and access.node.organization_id == organization_id
    )
    if not reader_identity_is_valid:
        raise ValidationError(
            {"gateway_link_id": "Repository Reader execution identity is invalid."}
        )
    _require_snapshot_capability(node=access.node, kind=kind)
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
        organization_id=access.node.organization_id,
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
    backup_source_snapshot_id: int,
    gateway_link_id: int,
    requesting_user_id: int,
    path: str,
    limit: int,
    correlation_id: str,
) -> NodeTask:
    return dispatch_snapshot_operation(
        organization_id=organization_id,
        directory_id=directory_id,
        backup_source_snapshot_id=backup_source_snapshot_id,
        gateway_link_id=gateway_link_id,
        requesting_user_id=requesting_user_id,
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
    backup_source_snapshot_id: int,
    gateway_link_id: int,
    requesting_user_id: int,
    path: str,
    correlation_id: str,
) -> NodeTask:
    directory = _directory_for_org(
        organization_id=organization_id,
        directory_id=directory_id,
        backup_source_snapshot_id=backup_source_snapshot_id,
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
        backup_source_snapshot_id=backup_source_snapshot_id,
        gateway_link_id=gateway_link_id,
        requesting_user_id=requesting_user_id,
        path=path,
        kind="lens.snapshot.scope.resolve",
        correlation_type=SCOPE_CORRELATION_TYPE,
        correlation_id=correlation_id,
        extra_payload=extra_payload,
        directory=directory,
    )


def task_for_org(*, organization: Organization, task_id: str) -> NodeTask:
    task = get_node_task_for_requesting_org(org=organization, task_id=task_id)
    if task is None:
        raise ValidationError({"task_id": "Insight snapshot operation was not found."})
    return task


def scope_task_for_correlation(
    *,
    organization: Organization,
    correlation_id: str,
) -> NodeTask | None:
    """Recover a previously dispatched Chat scope task after worker interruption."""

    task = get_node_task_by_correlation_for_requesting_org(
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

    task = get_node_task_for_requesting_org(org=organization, task_id=task_id)
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


def browse_skipped_special_count(task: NodeTask) -> int:
    result = task.result if isinstance(task.result, dict) else {}
    try:
        value = _exact_result_int(result.get("skipped_special_count", 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, value)


def snapshot_task_error(task: NodeTask, *, default: str) -> str:
    """Expose only stable Insight business errors, never Agent diagnostics."""

    result = task.result if isinstance(task.result, dict) else {}
    if str(result.get("error_code") or "") == _UNSUPPORTED_CONTENT_ERROR_CODE:
        return UNSUPPORTED_CONTENT_MESSAGE
    return default


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
    if file_count < 0 or size_bytes < 0 or (path_type == "file" and file_count != 1):
        raise RuntimeError("Agent returned an invalid Insight scope summary.")
    try:
        skipped_special_count = _exact_result_int(
            result.get("skipped_special_count", 0)
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Agent returned an invalid Insight scope summary.") from exc
    if skipped_special_count < 0:
        raise RuntimeError("Agent returned an invalid Insight scope summary.")
    return {
        "path_type": path_type,
        "file_count": file_count,
        "size_bytes": size_bytes,
        "skipped_special_count": skipped_special_count,
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
    "UNSUPPORTED_CONTENT_MESSAGE",
    "browse_skipped_special_count",
    "dispatch_scope_resolution",
    "dispatch_snapshot_browse",
    "normalized_browse_entries",
    "resolved_scope_summary",
    "scope_task_for_correlation",
    "scope_task_for_reference",
    "snapshot_task_error",
    "task_for_org",
]
