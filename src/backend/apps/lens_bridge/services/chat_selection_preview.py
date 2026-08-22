"""User-safe selection summaries and quota previews for New Chat."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import gateway_readiness, snapshot_scope_tasks
from apps.lens_bridge.services.chat_lifecycle import _configured_gateway_link_for_chat
from apps.node.models import NodeTask
from apps.node.services.interface import cancel_agent_task
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.subscription.constants import UNLIMITED
from apps.subscription.services.quota import normalize_scope_path, relative_scope_path
from common.extension_spi import get_quota_provider


_CORRELATION_PREFIX = "selection:user:"
_MAX_ACTIVE_PREVIEWS_PER_USER = 2
_SUMMARY_INVALID_MESSAGE = (
    "The Repository Reader returned an invalid selected-data summary. "
    "Upgrade the Reader and select the file or folder again."
)

logger = logging.getLogger(__name__)


def _user_correlation_prefix(user_id: int) -> str:
    return f"{_CORRELATION_PREFIX}{int(user_id)}:"


def _scope_correlation_id(
    *,
    user_id: int,
    snapshot_id: int,
    directory_id: int,
    source_path: str,
    gateway_link_id: int,
    request_token: str,
    attempt: int,
) -> str:
    identity = ":".join(
        (
            str(int(snapshot_id)),
            str(int(directory_id)),
            normalize_scope_path(source_path),
            str(int(gateway_link_id)),
            str(request_token),
            str(int(attempt)),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:48]
    return f"{_user_correlation_prefix(user_id)}{digest}"


def _directory_for_preview(
    *,
    organization_id: int,
    snapshot_id: int,
    directory_id: int,
) -> BackupSourceSnapshotDirectory:
    directory = (
        BackupSourceSnapshotDirectory.objects.select_related("source_snapshot")
        .filter(
            id=directory_id,
            organization_id=organization_id,
            source_snapshot_id=snapshot_id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
        .first()
    )
    if directory is None:
        raise ValidationError(
            {"directory_id": "Snapshot directory is no longer available."}
        )
    if (
        directory.source_snapshot.status
        not in {
            BackupSourceSnapshot.Status.AVAILABLE,
            BackupSourceSnapshot.Status.PARTIAL,
        }
        or not str(directory.kopia_snapshot_id or "").strip()
    ):
        raise ValidationError(
            {"directory_id": "Snapshot directory is no longer available."}
        )
    return directory


def _root_summary(directory: BackupSourceSnapshotDirectory) -> dict[str, Any]:
    path_type = (
        "file"
        if directory.path_type == BackupSourceSnapshotDirectory.PathType.FILE
        else "dir"
    )
    return {
        "path_type": path_type,
        "file_count": (
            1 if path_type == "file" else max(0, int(directory.file_count or 0))
        ),
        "size_bytes": max(0, int(directory.size_bytes or 0)),
        "skipped_special_count": 0,
    }


def scope_task_payload(task: NodeTask) -> dict[str, Any]:
    """Return the safe status of one selection summary task."""

    terminal_failure = task.status in {
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    }
    failure = (
        snapshot_scope_tasks.snapshot_task_failure(
            task,
            default=(
                "Unable to calculate the selected data. Check the Repository "
                "Reader and select the file or folder again."
            ),
        )
        if terminal_failure
        else None
    )
    payload: dict[str, Any] = {
        "task_id": str(task.id),
        "status": str(task.status),
        "error": failure.message if failure else "",
    }
    if failure:
        payload["error_code"] = failure.code
        payload["retryable"] = failure.retryable
    if task.status == NodeTask.Status.SUCCESS:
        try:
            payload["summary"] = snapshot_scope_tasks.resolved_scope_summary(task)
        except RuntimeError:
            logger.warning(
                "invalid selection preview summary task_id=%s organization_id=%s",
                task.id,
                task.requesting_organization_id,
            )
            payload.update(
                {
                    "status": NodeTask.Status.FAILED,
                    "error": _SUMMARY_INVALID_MESSAGE,
                    "error_code": "INSIGHT.SCOPE_SUMMARY_INVALID",
                    "retryable": False,
                }
            )
    return payload


@transaction.atomic
def start_scope_preview(
    *,
    organization,
    user,
    snapshot_id: int,
    directory_id: int,
    source_path: str,
    gateway_link_id: int,
    request_token: str,
    attempt: int,
) -> dict[str, Any]:
    """Return a stored root summary or dispatch one bounded path summary task."""

    directory = _directory_for_preview(
        organization_id=int(organization.id),
        snapshot_id=int(snapshot_id),
        directory_id=int(directory_id),
    )
    selected = normalize_scope_path(source_path)
    root = normalize_scope_path(directory.source_path)
    if selected == root:
        return {
            "task_id": None,
            "status": NodeTask.Status.SUCCESS,
            "summary": _root_summary(directory),
        }
    relative_path = relative_scope_path(root=root, selected=selected)
    if not relative_path:
        raise ValidationError(
            {"source_path": "Selected path is outside the snapshot directory."}
        )

    correlation_id = _scope_correlation_id(
        user_id=int(user.id),
        snapshot_id=int(snapshot_id),
        directory_id=int(directory_id),
        source_path=selected,
        gateway_link_id=int(gateway_link_id),
        request_token=str(request_token),
        attempt=int(attempt),
    )
    Organization.objects.select_for_update().only("id").get(pk=organization.id)
    existing = snapshot_scope_tasks.scope_task_for_correlation(
        organization=organization,
        correlation_id=correlation_id,
    )
    if existing is not None:
        return scope_task_payload(existing)
    active_count = NodeTask.objects.filter(
        requesting_organization_id=int(organization.id),
        kind="lens.snapshot.scope.resolve",
        correlation_type=snapshot_scope_tasks.SCOPE_CORRELATION_TYPE,
        correlation_id__startswith=_user_correlation_prefix(int(user.id)),
        status__in=(NodeTask.Status.PENDING, NodeTask.Status.RUNNING),
    ).count()
    if active_count >= _MAX_ACTIVE_PREVIEWS_PER_USER:
        return {
            "task_id": None,
            "status": "waiting",
            "error": (
                "Waiting for another selected-data calculation to finish. "
                "Calculation will resume automatically."
            ),
            "error_code": "INSIGHT.SELECTION_PREVIEW_BUSY",
            "retryable": True,
        }
    task = snapshot_scope_tasks.dispatch_scope_resolution(
        organization_id=int(organization.id),
        directory_id=int(directory_id),
        backup_source_snapshot_id=int(snapshot_id),
        gateway_link_id=int(gateway_link_id),
        requesting_user_id=int(user.id),
        path=relative_path,
        correlation_id=correlation_id,
    )
    return scope_task_payload(task)


def get_scope_preview_task(*, organization, user, task_id: str) -> NodeTask:
    """Resolve only selection-preview tasks owned by the current user."""

    try:
        task = snapshot_scope_tasks.task_for_org(
            organization=organization,
            task_id=str(task_id),
        )
    except ValidationError as exc:
        raise NotFound("Selection preview was not found.") from exc
    if (
        task.kind != "lens.snapshot.scope.resolve"
        or task.correlation_type != snapshot_scope_tasks.SCOPE_CORRELATION_TYPE
        or not str(task.correlation_id or "").startswith(
            _user_correlation_prefix(int(user.id))
        )
    ):
        raise NotFound("Selection preview was not found.")
    return task


def cancel_scope_preview_task(*, organization, user, task_id: str) -> NodeTask:
    """Best-effort cancellation for a stale selection revision."""

    task = get_scope_preview_task(
        organization=organization,
        user=user,
        task_id=task_id,
    )
    return cancel_agent_task(
        task_id=task.id,
        reason="selection preview is no longer active",
    ) or task


def _effective_limits(organization, *, provider) -> dict[str, int]:
    if provider is None:
        return {}
    return {
        str(key): int(value)
        for key, value in (provider.get_limits(organization) or {}).items()
    }


def admission_preview(
    *,
    organization,
    user,
    gateway_mode: str,
    gateway_link_id: int | None,
    file_count: int,
    size_bytes: int,
) -> dict[str, Any]:
    """Return product quota facts without exposing Gateway infrastructure capacity."""

    gateway_link = _configured_gateway_link_for_chat(
        organization,
        user=user,
        gateway_mode=gateway_mode,
        gateway_link_id=gateway_link_id,
    )
    if gateway_link is None:
        raise ValidationError(
            {"gateway_link_id": "The selected Data Gateway is not available."}
        )
    gateway_readiness.require_copilot_gateway(gateway_link)

    provider = get_quota_provider()
    limits = _effective_limits(organization, provider=provider)
    max_files = int(limits.get("gateway_select_max_files", UNLIMITED))
    max_bytes = int(limits.get("gateway_select_max_bytes", UNLIMITED))
    reasons: list[str] = []
    if max_files >= 0 and int(file_count) > max_files:
        reasons.append("selection_file_limit")
    if max_bytes >= 0 and int(size_bytes) > max_bytes:
        reasons.append("selection_size_limit")

    capacity: dict[str, Any] = {"applicable": False}
    if gateway_link.scope == LensGatewayLink.GatewayScope.PLATFORM:
        from apps.lens_bridge.services.public_gateway_capacity import (
            org_public_gateway_used_bytes,
        )

        used_bytes, usage_incomplete = org_public_gateway_used_bytes(
            organization_id=int(organization.id)
        )
        limit_available = provider is None or "max_public_gateway_capacity_bytes" in limits
        limit_bytes = int(
            limits.get("max_public_gateway_capacity_bytes", UNLIMITED)
        )
        remaining_bytes = (
            None
            if limit_bytes < 0 or usage_incomplete
            else max(0, limit_bytes - int(used_bytes))
        )
        after_create_bytes = (
            None
            if remaining_bytes is None
            else max(0, remaining_bytes - int(size_bytes))
        )
        capacity = {
            "applicable": True,
            "limit_available": limit_available,
            "limit_bytes": limit_bytes,
            "used_bytes": int(used_bytes),
            "remaining_bytes": remaining_bytes,
            "after_create_bytes": after_create_bytes,
            "usage_incomplete": bool(usage_incomplete),
        }
        if not limit_available or (usage_incomplete and limit_bytes >= 0):
            reasons.append("organization_capacity_unavailable")
        elif remaining_bytes is not None:
            requested_bytes = max(0, int(size_bytes))
            if requested_bytes > remaining_bytes or (
                requested_bytes == 0 and int(used_bytes) >= limit_bytes
            ):
                reasons.append("organization_capacity")

    return {
        "gateway_scope": str(gateway_link.scope),
        "selection": {
            "file_count": int(file_count),
            "size_bytes": int(size_bytes),
        },
        "selection_limits": {
            "max_files": max_files,
            "max_bytes": max_bytes,
        },
        "organization_capacity": capacity,
        "admission": {
            "allowed": not reasons,
            "reasons": reasons,
        },
    }
