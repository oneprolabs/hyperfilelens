"""Node lifecycle operations: async upgrade/remove with derived console state."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.constants import AuditResult
from apps.audit.services.interface import write_audit_log
from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.exceptions import AgentUpgradeError, NodeLifecycleError
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.selectors.interface import get_node_task_runtime_info
from apps.node.selectors.internal.node_task_query import node_tasks_queryset
from apps.node.services.internal.agent_release import (
    agent_release_commit,
    agent_version_compare,
    is_agent_artifact_id,
)
from apps.node.services.internal.agent_task import run_agent_task_async
from apps.node.services.internal.agent_uninstall import (
    _purge_agent_server_records,
)
from apps.node.services.internal.agent_upgrade import (
    node_os_version,
    node_platform_arch,
    validate_agent_upgrade,
)
from apps.node.services.internal.node_registry import agent_session_registered, agent_ws_routable
from apps.node.services.internal.node_workload import (
    assert_node_available_for_lifecycle,
    assert_node_available_for_removal,
    get_node_remove_blockers,
    get_node_workload_blockers,
    node_workload_payload,
)

logger = logging.getLogger(__name__)

LIFECYCLE_KIND_UPGRADE = "upgrade"
LIFECYCLE_KIND_REMOVE = "remove"
REQUIRED_UNINSTALL_CAPABILITY = "detached_uninstall_v2"
_LIFECYCLE_TASK_KINDS = {
    LIFECYCLE_KIND_UPGRADE: "agent.upgrade",
    LIFECYCLE_KIND_REMOVE: "agent.uninstall",
}
_ACTIVE_TASK_STATUSES = frozenset(
    {NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
)
_TERMINAL_TASK_STATUSES = frozenset(
    {
        NodeTask.Status.SUCCESS,
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    },
)


def _correlation_id(*, node_id: int, kind: str) -> str:
    return f"{kind}:{node_id}"


def _latest_lifecycle_task(*, org: Organization, node: Node, kind: str) -> NodeTask | None:
    return (
        node_tasks_queryset(
            organization_id=org.id,
            node_id=node.id,
            kind=_LIFECYCLE_TASK_KINDS[kind],
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=_correlation_id(node_id=node.id, kind=kind),
        )
        .first()
    )


def _active_lifecycle_task(*, org: Organization, node: Node) -> NodeTask | None:
    for kind in (LIFECYCLE_KIND_UPGRADE, LIFECYCLE_KIND_REMOVE):
        task = _latest_lifecycle_task(org=org, node=node, kind=kind)
        if task is not None and task.status in _ACTIVE_TASK_STATUSES:
            return task
    return None


def _task_progress_phase(task: NodeTask) -> str | None:
    runtime = get_node_task_runtime_info(task_id=str(task.id)) or {}
    progress = runtime.get("progress")
    if isinstance(progress, dict):
        phase = progress.get("phase")
        if phase:
            return str(phase)
    result = task.result if isinstance(task.result, dict) else {}
    mode = result.get("mode")
    if mode:
        return str(mode)
    return None


def _target_version_from_task(task: NodeTask) -> str:
    result = task.result if isinstance(task.result, dict) else {}
    target = str(result.get("target_version") or "").strip()
    if target:
        return target
    payload = task.payload if isinstance(task.payload, dict) else {}
    return str(payload.get("target_version") or "").strip()


def _target_commit_from_task(task: NodeTask) -> str:
    result = task.result if isinstance(task.result, dict) else {}
    target = str(result.get("target_commit") or "").strip().lower()
    if target:
        return target
    payload = task.payload if isinstance(task.payload, dict) else {}
    return str(payload.get("target_commit") or "").strip().lower()


def _node_installed_version(node: Node) -> str:
    version = str(node.version or "").strip()
    if version:
        return version
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory")
    if isinstance(inv, dict):
        return str(inv.get("agent_version") or "").strip()
    return str(meta.get("agent_version") or "").strip()


def _node_installed_commit(node: Node) -> str:
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory")
    if isinstance(inv, dict):
        return str(inv.get("agent_commit") or "").strip().lower()
    return str(meta.get("agent_commit") or "").strip().lower()


def _node_capabilities(node: Node) -> set[str]:
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory") if isinstance(meta.get("inventory"), dict) else meta
    raw = inv.get("capabilities") if isinstance(inv, dict) else []
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _supports_reliable_uninstall(node: Node) -> bool:
    return REQUIRED_UNINSTALL_CAPABILITY in _node_capabilities(node)


def _force_purge_without_remote_uninstall(
    *,
    org: Organization,
    node: Node,
    user=None,
    reason_code: str,
    reason_detail: str,
    defer_control_plane_purge: bool = False,
) -> dict[str, Any]:
    retained_resources = ["agent_installation"]
    if node.role == NodeRole.GATEWAY:
        retained_resources.append("lensnode_sidecar")
    summary = (
        {}
        if defer_control_plane_purge
        else _purge_agent_server_records(org=org, node=node, user=user)
    )
    result = {
        "operation_id": f"force-remove:{node.id}",
        "task_id": None,
        "node_id": node.id,
        "kind": LIFECYCLE_KIND_REMOVE,
        "state": "completed",
        "phase": (
            "awaiting_parent_finalize"
            if defer_control_plane_purge
            else "control_plane_purged"
        ),
        "purged": not defer_control_plane_purge,
        "control_plane_purge_deferred": defer_control_plane_purge,
        "force": True,
        "outcome": "force_cleanup_success",
        "cleanup_complete": False,
        "cleanup_failures": [{"code": reason_code, "detail": reason_detail}],
        "retained_resources": retained_resources,
        "summary": summary,
    }
    if not defer_control_plane_purge and node.role in {NodeRole.PROXY, NodeRole.GATEWAY}:
        from apps.node.services.internal.node_lifecycle_task import (
            record_immediate_node_remove_task,
        )

        try:
            record_immediate_node_remove_task(
                node=node,
                force=True,
                result=result,
            )
        except Exception:
            logger.exception(
                "failed to record immediate node removal node_id=%s",
                node.id,
            )
    return result


def _version_matches_target(
    *,
    node: Node,
    target_version: str,
    target_commit: str = "",
) -> bool:
    current = _node_installed_version(node)
    if not is_agent_artifact_id(target_version) or not is_agent_artifact_id(current):
        return False
    if agent_version_compare(current, target_version) < 0:
        return False
    expected_commit = str(target_commit or "").strip().lower()
    return not expected_commit or _node_installed_commit(node) == expected_commit


def _is_detached_lifecycle_task(task: NodeTask) -> bool:
    if task.kind not in _LIFECYCLE_TASK_KINDS.values():
        return False
    result = task.result if isinstance(task.result, dict) else {}
    if str(result.get("mode") or "").strip() == "local_detached":
        return True
    progress = result.get("last_progress")
    if isinstance(progress, dict) and str(progress.get("mode") or "").strip() == "local_detached":
        return True
    return False


def _detached_at_from_task(task: NodeTask) -> timezone.datetime | None:
    result = task.result if isinstance(task.result, dict) else {}
    raw = result.get("detached_at")
    if raw:
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(str(raw))
        if parsed is not None:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed)
            return parsed
    if _is_detached_lifecycle_task(task):
        return task.updated_at or task.created_at
    return None


def _elapsed_since_detached(task: NodeTask) -> timedelta | None:
    detached_at = _detached_at_from_task(task)
    if detached_at is None:
        return None
    return timezone.now() - detached_at


def _node_disk_metrics(node: Node) -> tuple[int | None, int | None]:
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory")
    if not isinstance(inv, dict):
        inv = meta
    if not isinstance(inv, dict):
        return None, None
    total_raw = inv.get("disk_total_bytes")
    free_raw = inv.get("disk_free_bytes")
    total = int(total_raw) if total_raw not in (None, "") else None
    free = int(free_raw) if free_raw not in (None, "") else None
    if total is not None and total <= 0:
        total = None
    if free is not None and free < 0:
        free = None
    return total, free


def _disk_blocks_upgrade(node: Node) -> bool:
    total, free = _node_disk_metrics(node)
    min_free = int(node_conf.UPGRADE_MIN_FREE_BYTES)
    if free is not None and free < min_free:
        return True
    if total and free is not None and total > 0:
        used_pct = ((total - free) / total) * 100
        if used_pct > float(node_conf.UPGRADE_MAX_DISK_USED_PCT):
            return True
    return False


def _running_lifecycle_task(
    *,
    org: Organization,
    node: Node,
    kind: str,
) -> NodeTask | None:
    task = _latest_lifecycle_task(org=org, node=node, kind=kind)
    if task is not None and task.status in _ACTIVE_TASK_STATUSES:
        return task
    return None


def _verify_started_at_from_task(task: NodeTask) -> timezone.datetime | None:
    result = task.result if isinstance(task.result, dict) else {}
    raw = result.get("verify_started_at")
    if not raw:
        return None
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(str(raw))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def _persist_upgrade_task_result(
    *,
    node: Node,
    task: NodeTask,
    result_patch: dict[str, Any],
) -> NodeTask:
    from apps.node.services.internal.task import complete_task

    merged = dict(task.result or {})
    for key, value in result_patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return complete_task(
        task_id=task.id,
        node_id=node.id,
        status=NodeTask.Status.RUNNING,
        result=merged,
        replace_result=True,
    )


def _clear_upgrade_verify_clock(*, node: Node, task: NodeTask) -> NodeTask:
    result = task.result if isinstance(task.result, dict) else {}
    if not result.get("verify_started_at"):
        return task
    return _persist_upgrade_task_result(
        node=node,
        task=task,
        result_patch={"verify_started_at": None},
    )


def _upgrade_verify_ready(*, node: Node, task: NodeTask) -> bool:
    if not _is_detached_lifecycle_task(task):
        return False
    if not agent_session_registered(agent_id=node.id):
        return False
    if node.availability != Node.Availability.ONLINE:
        return False
    result = task.result if isinstance(task.result, dict) else {}
    if not result.get("disconnect_observed_at"):
        return False
    target_version = _target_version_from_task(task)
    if not target_version:
        return False
    return _version_matches_target(
        node=node,
        target_version=target_version,
        target_commit=_target_commit_from_task(task),
    )


def record_upgrade_disconnect(*, node_id: int) -> bool:
    """Durably record the effective WS break required by detached upgrade."""

    with transaction.atomic():
        node = Node.objects.select_related("organization").filter(pk=node_id).first()
        if node is None:
            return False
        candidate = _running_lifecycle_task(
            org=node.organization,
            node=node,
            kind=LIFECYCLE_KIND_UPGRADE,
        )
        if candidate is None:
            return False
        task = NodeTask.objects.select_for_update().get(pk=candidate.pk)
        if task.status not in _ACTIVE_TASK_STATUSES or not _is_detached_lifecycle_task(
            task
        ):
            return False
        result = dict(task.result or {})
        result["disconnect_observed_at"] = timezone.now().isoformat()
        result.pop("verify_started_at", None)
        task.result = result
        task.save(update_fields=["result", "updated_at"])
        return True


def _advance_upgrade_verify(*, node: Node, task: NodeTask) -> bool:
    """
    Finalize detached upgrade only after WS + version stayed stable long enough.

    Returns True when the task was marked SUCCESS.
    """
    if task.kind != _LIFECYCLE_TASK_KINDS[LIFECYCLE_KIND_UPGRADE]:
        return False
    if task.status not in _ACTIVE_TASK_STATUSES:
        return False

    if not _upgrade_verify_ready(node=node, task=task):
        _clear_upgrade_verify_clock(node=node, task=task)
        return False

    verify_started = _verify_started_at_from_task(task)
    now = timezone.now()
    if verify_started is None:
        task = _persist_upgrade_task_result(
            node=node,
            task=task,
            result_patch={"verify_started_at": now.isoformat()},
        )
        verify_started = now

    stable_for = now - verify_started
    if stable_for < timedelta(seconds=node_conf.UPGRADE_STABLE_SECONDS):
        return False

    from apps.node.services.internal.task import complete_task

    merged = dict(task.result or {})
    merged["verified"] = True
    merged.pop("verify_started_at", None)
    complete_task(
        task_id=task.id,
        node_id=node.id,
        status=NodeTask.Status.SUCCESS,
        result=merged,
    )
    logger.info(
        "node lifecycle upgrade verified after stable reconnect node_id=%s task_id=%s stable_seconds=%s",
        node.id,
        task.id,
        int(stable_for.total_seconds()),
    )
    write_audit_log(
        organization=node.organization,
        action="node.lifecycle.upgrade.complete",
        target_type="node",
        target_id=str(node.id),
        resource_type="node",
        resource_id=str(node.id),
        resource_name=node.name,
        result=AuditResult.SUCCESS,
        metadata={
            "kind": LIFECYCLE_KIND_UPGRADE,
            "role": node.role,
            "task_id": str(task.id),
            "target_version": _target_version_from_task(task),
            "current_version": _node_installed_version(node),
            "stable_seconds": int(stable_for.total_seconds()),
        },
    )
    return True


def _finalize_upgrade_on_reconnect(*, node: Node, task: NodeTask) -> bool:
    return _advance_upgrade_verify(node=node, task=task)


def _fail_stale_upgrade_task(*, node: Node, task: NodeTask) -> bool:
    if task.kind != _LIFECYCLE_TASK_KINDS[LIFECYCLE_KIND_UPGRADE]:
        return False
    if task.status not in _ACTIVE_TASK_STATUSES:
        return False
    if not _is_detached_lifecycle_task(task):
        return False
    detached_at = _detached_at_from_task(task)
    if detached_at is None:
        return False
    if timezone.now() - detached_at < timedelta(
        seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS,
    ):
        return False
    target_version = _target_version_from_task(task)
    if target_version and _version_matches_target(
        node=node,
        target_version=target_version,
        target_commit=_target_commit_from_task(task),
    ):
        if _advance_upgrade_verify(node=node, task=task):
            return True

    from apps.node.services.internal.task import complete_task

    complete_task(
        task_id=task.id,
        node_id=node.id,
        status=NodeTask.Status.FAILED,
        error="Upgrade timed out waiting for agent to reconnect.",
        result=dict(task.result or {}),
    )
    logger.warning(
        "node lifecycle upgrade timed out node_id=%s task_id=%s",
        node.id,
        task.id,
    )
    write_audit_log(
        organization=node.organization,
        action="node.lifecycle.upgrade.failed",
        target_type="node",
        target_id=str(node.id),
        resource_type="node",
        resource_id=str(node.id),
        resource_name=node.name,
        result=AuditResult.FAILURE,
        error_message="Upgrade timed out waiting for agent to reconnect.",
        metadata={
            "kind": LIFECYCLE_KIND_UPGRADE,
            "role": node.role,
            "task_id": str(task.id),
            "target_version": target_version,
            "current_version": _node_installed_version(node),
        },
    )
    return True


def _build_upgrade_timeline(*, node: Node, task: NodeTask) -> list[dict[str, Any]]:
    """Build a timeline of upgrade phases with timestamps."""

    def _ts(dt) -> str | None:
        if dt is None:
            return None
        return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)

    def _phase_status(at) -> str:
        if at is None:
            return "pending"
        return "completed"

    is_failed = task.status in {
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    }
    is_success = task.status == NodeTask.Status.SUCCESS
    is_active = task.status in _ACTIVE_TASK_STATUSES

    detached_at = None
    verify_started_at = None
    result = task.result if isinstance(task.result, dict) else {}
    if result.get("verify_started_at"):
        from django.utils.dateparse import parse_datetime

        parsed = parse_datetime(str(result["verify_started_at"]))
        if parsed is not None:
            verify_started_at = parsed
    detached = _detached_at_from_task(task)

    # Determine the active phase for in-progress tasks
    active_phase = None
    if is_active:
        if is_failed:
            active_phase = None
        elif verify_started_at is not None:
            active_phase = "verifying"
        elif detached is not None:
            active_phase = "restarting"
        elif task.dispatched_at is not None:
            active_phase = "upgrading"
        else:
            active_phase = "dispatching"

    phases = [
        {
            "phase": "dispatching",
            "label": "Dispatching",
            "at": _ts(task.created_at) if task.created_at else None,
            "status": (
                "active"
                if active_phase == "dispatching"
                else ("completed" if task.created_at else "pending")
            ),
        },
        {
            "phase": "upgrading",
            "label": "Upgrading",
            "at": _ts(task.dispatched_at) if task.dispatched_at else None,
            "status": (
                "active"
                if active_phase == "upgrading"
                else (
                    "completed"
                    if task.dispatched_at or active_phase in ("restarting", "verifying")
                    or is_success
                    else "pending"
                )
            ),
        },
        {
            "phase": "restarting",
            "label": "Restarting",
            "at": _ts(detached) if detached else None,
            "status": (
                "active"
                if active_phase == "restarting"
                else (
                    "completed"
                    if detached and (active_phase == "verifying" or is_success)
                    else "pending"
                )
            ),
        },
        {
            "phase": "verifying",
            "label": "Verifying",
            "at": _ts(verify_started_at) if verify_started_at else None,
            "status": (
                "active"
                if active_phase == "verifying"
                else ("completed" if is_success else "pending")
            ),
        },
        {
            "phase": "success" if not is_failed else "failed",
            "label": "Success" if not is_failed else "Failed",
            "at": _ts(task.updated_at) if (is_success or is_failed) else None,
            "status": (
                "completed"
                if is_success
                else ("failed" if is_failed else "pending")
            ),
            "error": task.last_error if is_failed else None,
        },
    ]

    return phases


def _upgrade_lifecycle_payload(
    *,
    org: Organization,
    node: Node,
    task: NodeTask | None,
) -> dict[str, Any] | None:
    if task is None:
        return None

    target_version = _target_version_from_task(task)
    if not target_version and task.status in _ACTIVE_TASK_STATUSES:
        try:
            target_version = validate_agent_upgrade(node=node)
        except Exception:
            target_version = ""

    current_version = _node_installed_version(node)

    base: dict[str, Any] = {
        "kind": LIFECYCLE_KIND_UPGRADE,
        "task_id": str(task.id),
        "target_version": target_version,
        "current_version": current_version,
        "started_at": task.created_at.isoformat() if task.created_at else None,
        "timeline": _build_upgrade_timeline(node=node, task=task),
    }

    if task.status in _ACTIVE_TASK_STATUSES:
        phase = _task_progress_phase(task) or "dispatching"
        if _is_detached_lifecycle_task(task):
            if agent_session_registered(agent_id=node.id):
                if _upgrade_verify_ready(node=node, task=task):
                    return {**base, "state": "verifying", "phase": "waiting_for_version"}
                return {**base, "state": "upgrading", "phase": phase}
            return {
                **base,
                "state": "restarting",
                "phase": phase or "waiting_for_agent",
            }
        return {
            **base,
            "state": "upgrading",
            "phase": phase,
        }

    if task.status in {NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT, NodeTask.Status.CANCELED}:
        return {
            **base,
            "state": "failed",
            "phase": "failed",
            "error": task.last_error or task.status,
        }

    if task.status != NodeTask.Status.SUCCESS:
        return None

    if target_version and _version_matches_target(
        node=node,
        target_version=target_version,
        target_commit=_target_commit_from_task(task),
    ):
        return None

    return {**base, "state": "verifying", "phase": "waiting_for_version"}


def _remove_lifecycle_payload(
    *,
    node: Node,
    task: NodeTask | None,
) -> dict[str, Any] | None:
    if task is None:
        return None

    base: dict[str, Any] = {
        "kind": LIFECYCLE_KIND_REMOVE,
        "task_id": str(task.id),
        "started_at": task.created_at.isoformat() if task.created_at else None,
    }

    if task.status in _ACTIVE_TASK_STATUSES:
        phase = _task_progress_phase(task) or "dispatching"
        if _is_detached_lifecycle_task(task):
            return {
                **base,
                "state": "removing",
                "phase": "waiting_for_completion",
            }
        return {
            **base,
            "state": "removing",
            "phase": phase,
        }

    if task.status in {NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT, NodeTask.Status.CANCELED}:
        return {
            **base,
            "state": "failed",
            "phase": "failed",
            "error": task.last_error or task.status,
        }

    if task.status != NodeTask.Status.SUCCESS:
        return None

    result = task.result if isinstance(task.result, dict) else {}
    if result.get("completion_received_at") or result.get(
        "completion_timed_out_at"
    ):
        return {**base, "state": "cleaning_up", "phase": "completion_received"}

    return {**base, "state": "removing", "phase": "waiting_for_completion"}


def _fail_stale_remove_task(*, node: Node, task: NodeTask) -> bool:
    """Finalize a detached uninstall that never delivered its signed callback."""
    if task.kind != _LIFECYCLE_TASK_KINDS[LIFECYCLE_KIND_REMOVE]:
        return False
    if task.status != NodeTask.Status.RUNNING:
        return False
    if not _is_detached_lifecycle_task(task):
        return False
    elapsed = _elapsed_since_detached(task)
    if elapsed is None or elapsed < timedelta(
        seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS,
    ):
        return False
    from apps.node.services.internal.task import complete_task

    payload = task.payload if isinstance(task.payload, dict) else {}
    force_cleanup = bool(payload.get("force_cleanup"))
    result = dict(task.result or {})
    retained_resources = ["unverified_agent_installation"]
    if node.role == NodeRole.GATEWAY:
        retained_resources.append("unverified_lensnode_sidecar")
    result.update(
        {
            "mode": "local_detached",
            "completion_timed_out_at": timezone.now().isoformat(),
            "cleanup_complete": False,
            "cleanup_failures": [
                {
                    "code": "completion_callback_timeout",
                    "detail": "Detached uninstall did not report a terminal cleanup result.",
                }
            ],
            "retained_resources": retained_resources,
            "force": force_cleanup,
            "outcome": (
                "force_cleanup_success" if force_cleanup else "cleanup_failed"
            ),
        }
    )
    complete_task(
        task_id=task.id,
        node_id=node.id,
        status=(
            NodeTask.Status.SUCCESS if force_cleanup else NodeTask.Status.FAILED
        ),
        error="Uninstall timed out waiting for its completion callback.",
        result=result,
        replace_result=True,
    )
    logger.warning(
        "node lifecycle remove callback timed out node_id=%s task_id=%s force=%s",
        node.id,
        task.id,
        force_cleanup,
    )
    return True


def queue_detached_remove_verification(*, node_task: NodeTask) -> bool:
    """Queue the next remove step without relying exclusively on Celery Beat."""
    if node_task.kind != _LIFECYCLE_TASK_KINDS[LIFECYCLE_KIND_REMOVE]:
        return False
    if node_task.correlation_type != node_conf.LIFECYCLE_CORRELATION_TYPE:
        return False

    task_payload = node_task.payload if isinstance(node_task.payload, dict) else {}
    try:
        source_unregister_task_id = int(
            task_payload.get("source_unregister_task_id") or 0
        )
    except (TypeError, ValueError):
        source_unregister_task_id = 0

    if source_unregister_task_id > 0 and node_task.status in {
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    }:
        from apps.source.tasks.source_unregister import queue_source_unregister_task

        queue_source_unregister_task(task_id=source_unregister_task_id)
        logger.info(
            "source unregister parent queued after Agent uninstall terminal failure "
            "node_id=%s node_task_id=%s source_unregister_task_id=%s status=%s",
            node_task.node_id,
            node_task.id,
            source_unregister_task_id,
            node_task.status,
        )
        return True

    if node_task.status != NodeTask.Status.RUNNING:
        return False
    if not _is_detached_lifecycle_task(node_task):
        return False

    elapsed = _elapsed_since_detached(node_task) or timedelta(0)
    elapsed_seconds = max(0, int(elapsed.total_seconds()))
    timeout_delay = max(
        1,
        int(node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS) - elapsed_seconds + 1,
    )

    from apps.node.tasks.lifecycle import advance_node_lifecycle_for_node

    advance_node_lifecycle_for_node.apply_async(
        kwargs={"node_id": int(node_task.node_id)},
        countdown=timeout_delay,
    )
    return True


def compute_node_lifecycle(*, org: Organization, node: Node) -> dict[str, Any] | None:
    remove_task = _latest_lifecycle_task(org=org, node=node, kind=LIFECYCLE_KIND_REMOVE)
    if remove_task is not None:
        payload = _remove_lifecycle_payload(node=node, task=remove_task)
        if payload is not None:
            return payload

    upgrade_task = _latest_lifecycle_task(org=org, node=node, kind=LIFECYCLE_KIND_UPGRADE)
    if upgrade_task is not None:
        return _upgrade_lifecycle_payload(org=org, node=node, task=upgrade_task)

    return None


def advance_node_lifecycle(
    *,
    org: Organization,
    node: Node,
    user=None,
) -> dict[str, Any] | None:
    """
    Advance lifecycle when agent WS drops during detached upgrade/remove.
    Returns purge summary when remove records were removed.
    """
    upgrade_task = _running_lifecycle_task(org=org, node=node, kind=LIFECYCLE_KIND_UPGRADE)
    if upgrade_task is not None:
        if _finalize_upgrade_on_reconnect(node=node, task=upgrade_task):
            upgrade_task.refresh_from_db()
        else:
            _fail_stale_upgrade_task(node=node, task=upgrade_task)

    remove_task = _latest_lifecycle_task(org=org, node=node, kind=LIFECYCLE_KIND_REMOVE)
    if remove_task is None:
        return None

    if _fail_stale_remove_task(node=node, task=remove_task):
        remove_task.refresh_from_db()

    payload = _remove_lifecycle_payload(node=node, task=remove_task)
    if payload is None or payload.get("state") != "cleaning_up":
        return None

    task_payload = remove_task.payload if isinstance(remove_task.payload, dict) else {}
    source_unregister_task_id = int(
        task_payload.get("source_unregister_task_id") or 0
    )
    if source_unregister_task_id > 0:
        from apps.source.tasks.source_unregister import queue_source_unregister_task

        transaction.on_commit(
            lambda: queue_source_unregister_task(
                task_id=source_unregister_task_id,
                countdown_seconds=1,
            )
        )
        return {
            "node_id": node.id,
            "source_unregister_task_id": source_unregister_task_id,
            "waiting_for_parent_finalize": True,
        }

    if node.is_deleted:
        return {"node_id": node.id, "already_removed": True}

    summary = _purge_agent_server_records(org=org, node=node, user=user)
    summary.update({"node_id": node.id, "purged": True})
    logger.info("node lifecycle remove purged node_id=%s", node.id)
    return summary


@transaction.atomic
def start_node_upgrade(
    *,
    org: Organization,
    node: Node,
    user=None,
) -> dict[str, Any]:
    node = Node.objects.select_for_update().get(
        pk=node.id,
        organization_id=org.id,
        is_deleted=False,
    )
    if node.role not in (NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY):
        raise NodeLifecycleError("Only enrolled agents support remote upgrade.", code="role_not_managed")

    if node.role == NodeRole.AGENT:
        from apps.source.services.internal.source_operation_fence import (
            assert_source_product_operation_allowed,
        )

        try:
            assert_source_product_operation_allowed(
                organization_id=org.id,
                source_type="agent",
                source_ref_id=node.id,
            )
        except ValidationError as exc:
            raise NodeLifecycleError(
                "Backup source has an active Reset or Deregistration operation.",
                code="source_operation_in_progress",
            ) from exc

    active = _active_lifecycle_task(org=org, node=node)
    if active is not None:
        raise NodeLifecycleError(
            "Node already has an active lifecycle operation.",
            code="lifecycle_in_progress",
        )

    assert_node_available_for_lifecycle(node=node)

    try:
        target_version = validate_agent_upgrade(node=node)
    except AgentUpgradeError as exc:
        raise NodeLifecycleError(str(exc), code=exc.code) from exc
    platform, arch = node_platform_arch(node)
    target_commit = agent_release_commit(
        target_version,
        platform,
        arch,
        role=node.role,
        os_version=node_os_version(node),
    )
    payload = {"target_version": target_version}
    if target_commit:
        payload["target_commit"] = target_commit
    handle = run_agent_task_async(
        org=org,
        node_id=node.id,
        kind="agent.upgrade",
        payload=payload,
        correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
        correlation_id=_correlation_id(node_id=node.id, kind=LIFECYCLE_KIND_UPGRADE),
    )
    task = handle.task
    logger.info(
        "node lifecycle dispatch kind=%s node_id=%s task_id=%s target_version=%s",
        LIFECYCLE_KIND_UPGRADE,
        node.id,
        task.id,
        target_version,
    )
    return {
        "operation_id": str(task.id),
        "task_id": str(task.id),
        "node_id": node.id,
        "kind": LIFECYCLE_KIND_UPGRADE,
        "state": "upgrading",
        "phase": "dispatching",
        "target_version": target_version,
        "target_commit": target_commit,
    }


def start_node_remove(
    *,
    org: Organization,
    node: Node,
    user=None,
    force: bool = False,
    keep_data: bool = False,
    triggered_by_task_id: int | None = None,
    triggered_by_task_attempt: int = 0,
) -> dict[str, Any]:
    with transaction.atomic():
        locked_node = Node.objects.select_for_update().get(
            pk=node.id,
            organization_id=org.id,
            is_deleted=False,
        )
        return _start_node_remove_locked(
            org=org,
            node=locked_node,
            user=user,
            force=force,
            keep_data=keep_data,
            triggered_by_task_id=triggered_by_task_id,
            triggered_by_task_attempt=triggered_by_task_attempt,
        )


def _start_node_remove_locked(
    *,
    org: Organization,
    node: Node,
    user=None,
    force: bool,
    keep_data: bool,
    triggered_by_task_id: int | None,
    triggered_by_task_attempt: int,
) -> dict[str, Any]:
    """Run authoritative node-remove preflight while holding the node fence."""
    if node.role not in (NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY):
        raise NodeLifecycleError("Only enrolled agents support remote removal.", code="role_not_managed")

    if node.role == NodeRole.AGENT:
        from apps.source.services.internal.source_operation_fence import (
            assert_source_product_operation_allowed,
        )

        try:
            assert_source_product_operation_allowed(
                organization_id=org.id,
                source_type="agent",
                source_ref_id=node.id,
                allowed_task_id=triggered_by_task_id,
            )
        except ValidationError as exc:
            raise NodeLifecycleError(
                "Backup source has an active Reset or Deregistration operation.",
                code="source_operation_in_progress",
            ) from exc

    active = _active_lifecycle_task(org=org, node=node)
    if active is not None:
        raise NodeLifecycleError(
            "Node already has an active lifecycle operation.",
            code="lifecycle_in_progress",
        )

    assert_node_available_for_removal(node=node)

    if node.role == NodeRole.PROXY:
        from apps.node.services.internal.bindings import collect_proxy_bindings

        bindings = collect_proxy_bindings(
            organization_id=org.id,
            proxy_id=node.id,
        )
        if not bindings.is_empty():
            raise NodeLifecycleError(
                "Proxy has bound resources. Replace or remove them before deleting the Proxy.",
                code="proxy_has_bindings",
                blockers=[
                    {
                        "code": "proxy_has_bindings",
                        "detail": "Proxy bindings cannot be bypassed by Force Cleanup.",
                        "bound": bindings.to_payload(),
                    }
                ],
            )

    if not _supports_reliable_uninstall(node):
        detail = (
            f"Agent does not report required capability "
            f'"{REQUIRED_UNINSTALL_CAPABILITY}". Upgrade the Agent before Strict Cleanup.'
        )
        if not force:
            raise NodeLifecycleError(detail, code="agent_upgrade_required")
        return _force_purge_without_remote_uninstall(
            org=org,
            node=node,
            user=user,
            reason_code="agent_upgrade_required",
            reason_detail=f"{detail} Remote uninstall was not attempted.",
            defer_control_plane_purge=triggered_by_task_id is not None,
        )

    if not agent_ws_routable(agent_id=node.id):
        if not force:
            raise NodeLifecycleError(
                "Node is offline. Strict Cleanup requires the Agent to be reachable.",
                code="node_offline",
            )
        result = _force_purge_without_remote_uninstall(
            org=org,
            node=node,
            user=user,
            reason_code="agent_offline",
            reason_detail="Remote uninstall was not executed because the Agent was offline.",
            defer_control_plane_purge=triggered_by_task_id is not None,
        )
        result.update(
            {
                "operation_id": f"offline-remove:{node.id}",
                "phase": (
                    "awaiting_parent_finalize"
                    if result.get("control_plane_purge_deferred")
                    else "offline_purged"
                ),
                "offline": True,
            }
        )
        return result

    uninstall_payload: dict[str, Any] = {
        "keep_data": bool(keep_data),
        "force_cleanup": bool(force),
    }
    if triggered_by_task_id:
        uninstall_payload["source_unregister_task_id"] = int(
            triggered_by_task_id
        )
        uninstall_payload["source_unregister_attempt"] = max(
            0,
            int(triggered_by_task_attempt),
        )
    handle = run_agent_task_async(
        org=org,
        node_id=node.id,
        kind="agent.uninstall",
        payload=uninstall_payload,
        correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
        correlation_id=_correlation_id(node_id=node.id, kind=LIFECYCLE_KIND_REMOVE),
    )
    from apps.node.services.internal.uninstall_completion import (
        attach_uninstall_completion,
    )

    task = attach_uninstall_completion(task=handle.task)
    logger.info(
        "node lifecycle dispatch kind=%s node_id=%s task_id=%s offline=%s",
        LIFECYCLE_KIND_REMOVE,
        node.id,
        task.id,
        False,
    )
    return {
        "operation_id": str(task.id),
        "task_id": str(task.id),
        "node_id": node.id,
        "kind": LIFECYCLE_KIND_REMOVE,
        "state": "removing",
        "phase": "dispatching",
        "offline": False,
        "force": bool(force),
    }


def preview_batch_operations(
    *,
    org: Organization,
    node_ids: list[int],
    kind: str,
) -> dict[str, Any]:
    if kind not in {LIFECYCLE_KIND_UPGRADE, LIFECYCLE_KIND_REMOVE}:
        raise NodeLifecycleError("Unsupported lifecycle kind.", code="invalid_kind")

    nodes = list(
        Node.objects.filter(
            organization_id=org.id,
            pk__in=node_ids,
            is_deleted=False,
        ).order_by("name", "id")
    )
    found_ids = {node.id for node in nodes}
    missing = [node_id for node_id in node_ids if node_id not in found_ids]

    eligible: list[dict[str, Any]] = []
    skipped_offline: list[dict[str, Any]] = []
    skipped_workload: list[dict[str, Any]] = []
    skipped_in_progress: list[dict[str, Any]] = []
    skipped_not_upgradeable: list[dict[str, Any]] = []
    skipped_proxy_bound: list[dict[str, Any]] = []
    skipped_disk_full: list[dict[str, Any]] = []

    for node in nodes:
        item = {"node_id": node.id, "name": node.name}
        if _active_lifecycle_task(org=org, node=node):
            skipped_in_progress.append({**item, "reason": "lifecycle_in_progress"})
            continue

        blockers = (
            get_node_remove_blockers(node=node)
            if kind == LIFECYCLE_KIND_REMOVE
            else get_node_workload_blockers(node=node)
        )
        if blockers:
            skipped_workload.append(
                {
                    **item,
                    "reason": "node_workload_active",
                    "blockers": [b.to_payload() for b in blockers],
                }
            )
            continue

        if kind == LIFECYCLE_KIND_UPGRADE:
            if node.availability != Node.Availability.ONLINE or not agent_ws_routable(agent_id=node.id):
                skipped_offline.append({**item, "reason": "offline"})
                continue
            if _disk_blocks_upgrade(node):
                skipped_disk_full.append({**item, "reason": "disk_full"})
                continue
            try:
                target_version = validate_agent_upgrade(node=node)
            except Exception as exc:
                code = getattr(exc, "code", "not_upgradeable")
                if code == "node_offline":
                    skipped_offline.append({**item, "reason": "offline"})
                else:
                    skipped_not_upgradeable.append(
                        {**item, "reason": code, "message": str(exc)}
                    )
                continue
            eligible.append({**item, "target_version": target_version})
            continue

        if kind == LIFECYCLE_KIND_REMOVE:
            item.update(
                {
                    "agent_version": _node_installed_version(node),
                    "required_capabilities": [REQUIRED_UNINSTALL_CAPABILITY],
                    "missing_capabilities": (
                        []
                        if _supports_reliable_uninstall(node)
                        else [REQUIRED_UNINSTALL_CAPABILITY]
                    ),
                    "upgrade_required": not _supports_reliable_uninstall(node),
                }
            )
            if node.role == NodeRole.PROXY:
                from apps.node.services.internal.bindings import collect_proxy_bindings

                bindings = collect_proxy_bindings(
                    organization_id=org.id,
                    proxy_id=node.id,
                )
                if not bindings.is_empty():
                    skipped_proxy_bound.append({**item, "reason": "proxy_has_bindings"})
                    continue
            if not agent_ws_routable(agent_id=node.id):
                eligible.append({**item, "offline": True})
            else:
                eligible.append({**item, "offline": False})
            continue

    return {
        "kind": kind,
        "requested": len(node_ids),
        "eligible": eligible,
        "skipped_offline": skipped_offline,
        "skipped_workload": skipped_workload,
        "skipped_in_progress": skipped_in_progress,
        "skipped_not_upgradeable": skipped_not_upgradeable,
        "skipped_proxy_bound": skipped_proxy_bound,
        "skipped_disk_full": skipped_disk_full,
        "missing_node_ids": missing,
        "max_concurrent": node_conf.LIFECYCLE_MAX_CONCURRENT,
    }


def enrich_node_row(*, org: Organization, node: Node, user=None) -> dict[str, Any]:
    """Read-only lifecycle/workload enrichment for console list (no side effects)."""
    lifecycle = None if node.is_deleted else compute_node_lifecycle(org=org, node=node)
    workload = None if node.is_deleted else node_workload_payload(node=node)
    return {
        "lifecycle": lifecycle,
        "workload": workload,
    }
