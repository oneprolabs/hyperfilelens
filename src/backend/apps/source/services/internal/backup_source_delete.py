"""Unified backup source deletion with strict / force modes."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.constants import AuditAction, AuditResult
from apps.audit.services.interface import write_audit_log
from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal import redis_store
from apps.node.services.internal.node_registry import (
    CONNECTION_OFFLINE,
    agent_connection_status,
)
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
    SnapshotDownloadArtifact,
)
from apps.protection.services.snapshot_delete import (
    create_and_queue_snapshot_delete_task,
    create_snapshot_delete_task,
    run_snapshot_delete_task,
)
from apps.restore.models import RestorePlan, RestoreRecord
from apps.source.constants import (
    ConnectionTestStatus,
    ResourceStatus,
    ResourceType,
    SelectableSourceKind,
)
from apps.source.models import BackupSourceRepositoryPurgePending, SourceResource
from apps.source.services.internal.selectable_ids import parse_selectable_id
from apps.source.services.internal.source_pipeline import delete_pipeline_entry
from apps.source.services.interface import unmount_resource
from apps.storage.repositories.models import (
    Repository,
    RepositoryTask,
    RepositoryUsageShard,
)
from apps.storage.services.interface import (
    RepositoryCleanupBlocked,
    create_direct_nas_target_cleanup_task,
    direct_nas_cleanup_target_ids,
    repository_active_task_blockers,
    repository_cleanup_task_payload,
    run_repository_cleanup_task,
)
from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.models import Task, TaskDependency, TaskResource, TaskStep
from apps.task.services.interface import (
    append_task_step_event,
    complete_task,
    create_task,
    start_task,
)
from apps.task.signals import task_updated

logger = logging.getLogger(__name__)

_ACTIVE_TASK_STATUSES = {
    Task.Status.PENDING,
    Task.Status.WAITING,
    Task.Status.BLOCKED,
    Task.Status.RUNNING,
}

_SOURCE_UNREGISTER_STEPS = [
    "prepare_source_unregister",
    "cleanup_direct_nas_repositories",
    "cleanup_source_endpoint",
    "reset_backup_config",
    "finalize_source_unregister",
]

_UNREGISTER_TERMINAL = {
    Task.Status.SUCCESS,
    Task.Status.FAILED,
    Task.Status.CANCELLED,
    Task.Status.TIMEOUT,
}

_UNREGISTER_NOT_STARTED_ERROR_CODES = {
    "SOURCE_UNREGISTER_PREFLIGHT_FAILED",
    "SOURCE_UNREGISTER_DEFERRED_CANCELLED",
    "SOURCE_UNREGISTER_INVALID_REQUEST",
    "TASK_CANCELLED",
}


@dataclass
class DeleteReason:
    code: str
    detail: str
    source_id: str = ""
    source_name: str = ""
    repository_id: int | None = None
    repository_name: str = ""
    reference_type: str = ""
    reference_id: str = ""
    reference_task_type: str = ""
    blocking_task_uuid: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.source_id:
            payload["source_id"] = self.source_id
        if self.source_name:
            payload["source_name"] = self.source_name
        if self.repository_id is not None:
            payload["repository_id"] = self.repository_id
        if self.repository_name:
            payload["repository_name"] = self.repository_name
        if self.reference_type:
            payload["reference_type"] = self.reference_type
        if self.reference_id:
            payload["reference_id"] = self.reference_id
        if self.reference_task_type:
            payload["reference_task_type"] = self.reference_task_type
        if self.blocking_task_uuid:
            payload["blocking_task_uuid"] = self.blocking_task_uuid
        return payload


@dataclass(frozen=True)
class SourceDeregistrationDecision:
    disposition: str
    reasons: tuple[DeleteReason, ...] = ()

    @property
    def ready(self) -> bool:
        return self.disposition == "ready"


@dataclass
class DeleteWarning:
    code: str
    detail: str
    source_id: str = ""
    source_name: str = ""
    retained_resources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.source_id:
            payload["source_id"] = self.source_id
        if self.source_name:
            payload["source_name"] = self.source_name
        if self.retained_resources:
            payload["retained_resources"] = list(self.retained_resources)
        return payload


@dataclass
class SourceDeleteContext:
    selectable_id: str
    source_kind: str
    source_ref_id: int
    source_type: str
    display_name: str
    agent_node: Node | None = None
    nas_resource: SourceResource | None = None

    @property
    def is_agent(self) -> bool:
        return self.source_kind == SelectableSourceKind.AGENT


@dataclass(frozen=True)
class DirectNasCleanupOutcome:
    """Current state of Direct NAS physical cleanup for one source."""

    cleaned_repository_ids: set[int]
    cleanup_tasks: list[dict[str, Any]]
    warnings: tuple[DeleteWarning, ...] = ()
    retained_resources: tuple[str, ...] = ()
    waiting: bool = False


class BackupSourceDeleteFailed(Exception):
    def __init__(
        self,
        *,
        message: str,
        reasons: list[DeleteReason],
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reasons = reasons
        self.hint = hint or (
            "Fix the issues above and try again, or use Force Cleanup where allowed."
        )


def _emit_task_update_after_commit(*, task: Task) -> None:
    task_uuid = str(task.task_uuid)
    organization_id = int(task.organization_id)
    status = str(task.status)
    progress = float(task.progress)
    transaction.on_commit(
        lambda: task_updated.send(
            sender=Task,
            task_uuid=task_uuid,
            organization_id=organization_id,
            status=status,
            progress=progress,
        )
    )


def _source_key(source_type: str, source_ref_id: int) -> str:
    kind = "agent" if source_type == "agent" else source_type
    return f"{kind}:{source_ref_id}"


def _resolve_context(
    *, organization_id: int, selectable_id: str
) -> SourceDeleteContext | None:
    parsed = parse_selectable_id(selectable_id)
    if parsed is None or parsed[0] not in (
        SelectableSourceKind.AGENT,
        SelectableSourceKind.NAS,
    ):
        return None
    kind, ref_id = parsed
    if kind == SelectableSourceKind.AGENT:
        node = Node.objects.filter(
            pk=ref_id,
            organization_id=organization_id,
            is_deleted=False,
            role=NodeRole.AGENT,
        ).first()
        if node is None:
            return None
        return SourceDeleteContext(
            selectable_id=selectable_id,
            source_kind=kind,
            source_ref_id=ref_id,
            source_type="agent",
            display_name=str(node.name or selectable_id),
            agent_node=node,
        )
    resource = SourceResource.objects.filter(
        pk=ref_id,
        organization_id=organization_id,
        is_deleted=False,
        resource_type=ResourceType.NAS,
    ).first()
    if resource is None:
        return None
    return SourceDeleteContext(
        selectable_id=selectable_id,
        source_kind=kind,
        source_ref_id=ref_id,
        source_type="nas",
        display_name=str(resource.name or selectable_id),
        nas_resource=resource,
    )


def _running_tasks_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> list[Task]:
    from apps.node.services.internal.task_offline_reconcile import (
        product_task_blocks_cleanup,
    )

    subtype_query = TaskResource.objects.filter(
        resource_type=TaskResource.Type.BACKUP_SOURCE,
        resource_id=source_ref_id,
    ).filter(
        models_Q_subtype(source_type),
    )
    task_ids = subtype_query.values_list("task_id", flat=True)
    tasks = list(
        Task.objects.filter(
            organization_id=organization_id,
            id__in=task_ids,
            status__in=_ACTIVE_TASK_STATUSES,
            task_type__in=[
                Task.Type.BACKUP,
                Task.Type.BACKUP_CONFIG_PROVISION,
                *RESTORE_TASK_TYPES,
            ],
        ).order_by("-created_at", "-id")
    )
    return [
        task
        for task in tasks
        if task.task_type == Task.Type.BACKUP_CONFIG_PROVISION
        or product_task_blocks_cleanup(task=task)
    ]


def models_Q_subtype(source_type: str):
    from django.db.models import Q

    query = Q(resource_subtype=source_type)
    if source_type == "agent":
        query |= Q(resource_subtype="")
    return query


def _task_resources_join_subtype_q(source_type: str):
    from django.db.models import Q

    query = Q(resources__resource_subtype=source_type)
    if source_type == "agent":
        query |= Q(resources__resource_subtype="")
    return query


def _source_resource_defs(ids: list[str]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for selectable_id in ids:
        parsed = parse_selectable_id(selectable_id)
        if not parsed:
            continue
        source_type, source_ref_id = parsed
        if source_type not in {"agent", "nas"}:
            continue
        key = (source_type, int(source_ref_id))
        if key in seen:
            continue
        seen.add(key)
        resources.append(
            {
                "resource_type": TaskResource.Type.BACKUP_SOURCE,
                "resource_subtype": source_type,
                "resource_id": int(source_ref_id),
                "is_primary": True,
            }
        )
    return resources


def _create_source_unregister_task(
    *,
    org: Organization,
    selectable_id: str,
    force: bool,
    group_uuid: UUID | None = None,
    idempotency_key: str | None = None,
) -> Task:
    display_name = "Deregister backup source"
    return create_task(
        organization_id=org.id,
        task_type=Task.Type.SOURCE_UNREGISTER,
        display_name=display_name,
        trigger_type=Task.TriggerType.MANUAL,
        request_payload={
            "source_ids": [selectable_id],
            "force": bool(force),
            "cleanup_plan": _source_unregister_cleanup_plan(
                org=org,
                selectable_id=selectable_id,
            ),
        },
        resources=_source_resource_defs([selectable_id]),
        steps=_SOURCE_UNREGISTER_STEPS,
        group_uuid=group_uuid,
        idempotency_key=idempotency_key,
    )


def _resolve_unregister_dependencies(*, task: Task) -> None:
    TaskDependency.objects.filter(task=task, is_active=True).update(
        is_active=False,
        resolved_at=timezone.now(),
    )


def _fail_unregister_before_start(
    *,
    task: Task,
    reasons: list[DeleteReason],
    error_code: str = "SOURCE_UNREGISTER_PREFLIGHT_FAILED",
    error_message: str = "Source deregistration prerequisites are not satisfied.",
) -> Task:
    """Finish one rejected attempt without preserving a future delete intent."""
    reason_payload = [reason.as_dict() for reason in reasons]
    _resolve_unregister_dependencies(task=task)
    _set_unregister_step(
        task=task,
        step_name="prepare_source_unregister",
        status=TaskStep.Status.FAILED,
        progress=0,
        message=error_message,
        level="ERROR",
        metadata={"reasons": reason_payload},
    )
    TaskStep.objects.filter(
        task=task,
        status__in={TaskStep.Status.PENDING, TaskStep.Status.RUNNING},
    ).exclude(step_name="prepare_source_unregister").update(
        status=TaskStep.Status.SKIPPED,
    )
    _complete_unregister_task(
        task=task,
        status=Task.Status.FAILED,
        result_payload={
            "ok": False,
            "accepted": False,
            "result": "failed",
            "source_ids": list((task.request_payload or {}).get("source_ids") or []),
            "reasons": reason_payload,
        },
        error_code=error_code,
        error_message=error_message,
    )
    task.refresh_from_db()
    return task


def _terminalize_legacy_deferred_unregister(task: Task) -> Task:
    """End a pre-existing deferred attempt so it cannot delete later."""
    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    raw_reasons = payload.get("waiting_reasons") or payload.get("blocked_reasons") or []
    reasons = [
        DeleteReason(
            code=str(item.get("code") or "deferred_unregister_cancelled"),
            detail=str(
                item.get("detail") or "The previous deferred deregistration was ended."
            ),
            source_id=str(item.get("source_id") or ""),
            source_name=str(item.get("source_name") or ""),
            reference_type=str(item.get("reference_type") or ""),
            reference_id=str(item.get("reference_id") or ""),
            reference_task_type=str(item.get("reference_task_type") or ""),
            blocking_task_uuid=str(item.get("blocking_task_uuid") or ""),
        )
        for item in raw_reasons
        if isinstance(item, dict)
    ]
    task = _fail_unregister_before_start(
        task=task,
        reasons=reasons,
        error_code="SOURCE_UNREGISTER_DEFERRED_CANCELLED",
        error_message=(
            "The previous deferred deregistration was ended. "
            "Submit a new request after resolving the prerequisite."
        ),
    )
    nas_ids = task.resources.filter(
        resource_type=TaskResource.Type.BACKUP_SOURCE,
        resource_subtype="nas",
    ).values_list("resource_id", flat=True)
    active_nas_ids = (
        TaskResource.objects.filter(
            task__organization_id=task.organization_id,
            task__task_type=Task.Type.SOURCE_UNREGISTER,
            task__status__in={Task.Status.PENDING, Task.Status.RUNNING},
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="nas",
            resource_id__in=nas_ids,
        )
        .exclude(task_id=task.id)
        .values_list("resource_id", flat=True)
    )
    SourceResource.all_objects.filter(
        organization_id=task.organization_id,
        id__in=nas_ids,
        is_deleted=False,
        status=ResourceStatus.REMOVING,
    ).exclude(id__in=active_nas_ids).update(
        status=ResourceStatus.ACTIVE,
        status_message=(
            "The previous deferred deregistration ended. "
            "Submit a new request after resolving the prerequisite."
        ),
        updated_at=timezone.now(),
    )
    return task


def _is_legacy_deferred_unregister(task: Task) -> bool:
    if task.status in {Task.Status.WAITING, Task.Status.BLOCKED}:
        return True
    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    return bool(
        task.status == Task.Status.RUNNING
        and task.current_step == "prepare_source_unregister"
        and any(key in payload for key in ("waiting_reasons", "blocked_reasons"))
    )


def _source_unregister_cleanup_plan(
    *,
    org: Organization,
    selectable_id: str,
) -> dict[str, Any]:
    """Capture immutable source/config/repository identities for retries."""
    ctx = _resolve_context(
        organization_id=org.id,
        selectable_id=selectable_id,
    )
    if ctx is None:
        return {"version": 1, "source_id": selectable_id}
    configs = list(
        BackupConfig.objects.filter(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        ).values("id", "repository_id")
    )
    config_ids = [int(config["id"]) for config in configs]
    repository_ids = sorted({int(config["repository_id"]) for config in configs})
    snapshot_ids = list(
        BackupSourceSnapshot.objects.filter(
            organization_id=org.id,
            backup_config_id__in=config_ids,
        )
        .exclude(status=BackupSourceSnapshot.Status.DELETED)
        .order_by("id")
        .values_list("id", flat=True)
    )
    shards = list(
        RepositoryUsageShard.objects.filter(
            organization_id=org.id,
            repository_id__in=repository_ids,
            node_id=(ctx.agent_node.id if ctx.agent_node is not None else 0),
        )
        .order_by("id")
        .values(
            "id",
            "repository_id",
            "node_id",
            "repository_subdir",
            "mount_point",
        )
    )
    endpoint: dict[str, Any] = {
        "kind": ctx.source_kind,
        "ref_id": ctx.source_ref_id,
        "name": ctx.display_name,
    }
    if ctx.nas_resource is not None:
        endpoint.update(
            {
                "bound_node_id": ctx.nas_resource.bound_node_id,
                "mount_point": ctx.nas_resource.effective_mount_point(),
                "resource_type": ctx.nas_resource.resource_type,
                "config": _cleanup_config_identity(ctx.nas_resource.config),
            }
        )
    elif ctx.agent_node is not None:
        endpoint.update(
            {
                "node_id": ctx.agent_node.id,
                "role": ctx.agent_node.role,
                "endpoint": ctx.agent_node.ip_address,
                "registered_at": (
                    ctx.agent_node.created_at.isoformat()
                    if ctx.agent_node.created_at is not None
                    else None
                ),
            }
        )
    if ctx.nas_resource is not None:
        endpoint["registered_at"] = (
            ctx.nas_resource.created_at.isoformat()
            if ctx.nas_resource.created_at is not None
            else None
        )
    return {
        "version": 1,
        "source_id": selectable_id,
        "source": endpoint,
        "backup_config_ids": config_ids,
        "repository_ids": repository_ids,
        "snapshot_ids": [int(value) for value in snapshot_ids],
        "usage_shards": shards,
    }


def _cleanup_config_identity(config: Any) -> dict[str, Any]:
    """Keep endpoint identity fields while excluding credentials and tokens."""
    if not isinstance(config, dict):
        return {}
    sanitized = _sanitize_cleanup_value(config)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_cleanup_value(value: Any) -> Any:
    """Recursively remove secret-bearing fields from immutable cleanup plans."""
    secret_markers = (
        "password",
        "secret",
        "token",
        "access_key",
        "credential",
        "private_key",
        "ciphertext",
    )
    if isinstance(value, dict):
        return {
            str(key): _sanitize_cleanup_value(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in secret_markers)
        }
    if isinstance(value, list):
        return [_sanitize_cleanup_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_cleanup_value(item) for item in value)
    return value


def _set_unregister_step(
    *,
    task: Task | None,
    step_name: str,
    status: str,
    progress: int,
    message: str,
    level: str = "INFO",
    metadata: dict[str, Any] | None = None,
) -> None:
    if task is None:
        return
    TaskStep.objects.filter(task=task, step_name=step_name).update(
        status=status,
        progress=Decimal("100.00")
        if status == TaskStep.Status.SUCCESS
        else Decimal(str(progress)),
    )
    task.current_step = step_name
    task.progress = Decimal(str(progress))
    task.save(update_fields=["current_step", "progress", "updated_at"])
    append_task_step_event(
        task=task,
        step_name=step_name,
        level=level,
        message=message,
        metadata=metadata,
    )


def _complete_unregister_task(
    *,
    task: Task | None,
    status: str,
    result_payload: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
) -> None:
    if task is None:
        return
    complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=status,
        progress=100
        if status == Task.Status.SUCCESS
        else max(1, int(task.progress or 0)),
        result_payload=result_payload,
        error_code=error_code,
        error_message=error_message,
    )


def _active_unregister_task_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> Task | None:
    tasks = (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.SOURCE_UNREGISTER,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_subtype=source_type,
            resources__resource_id=source_ref_id,
        )
        .exclude(status__in=_UNREGISTER_TERMINAL)
        .order_by("-created_at", "-id")
        .distinct()
    )
    return tasks.first()


def _active_reset_task_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> Task | None:
    tasks = (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_subtype=source_type,
            resources__resource_id=source_ref_id,
        )
        .exclude(status__in=_UNREGISTER_TERMINAL)
        .order_by("-created_at", "-id")
        .distinct()
    )
    return tasks.first()


def _product_task_for_node_task(
    *,
    organization_id: int,
    node_task: NodeTask,
) -> Task | None:
    """Resolve the product task that owns one active Node execution."""
    if node_task.parent_task_id:
        product_task = Task.objects.filter(
            organization_id=organization_id,
            id=node_task.parent_task_id,
        ).first()
        if product_task is not None:
            return product_task
    if not node_task.correlation_id:
        return None
    try:
        product_task_uuid = UUID(node_task.correlation_id)
    except (TypeError, ValueError):
        return None
    return Task.objects.filter(
        organization_id=organization_id,
        task_uuid=product_task_uuid,
    ).first()


def _snapshot_delete_owned_by_unregister_attempt(
    *,
    product_task: Task | None,
    unregister_task: Task | None,
) -> bool:
    """Return whether a snapshot delete belongs to this unregister attempt."""
    if (
        product_task is None
        or unregister_task is None
        or product_task.task_type != Task.Type.SNAPSHOT_DELETE
    ):
        return False
    payload = (
        product_task.request_payload
        if isinstance(product_task.request_payload, dict)
        else {}
    )
    if (
        "source_unregister_task_id" not in payload
        or "source_unregister_attempt" not in payload
    ):
        return False
    try:
        parent_id = int(payload["source_unregister_task_id"])
        parent_attempt = int(payload["source_unregister_attempt"])
    except (TypeError, ValueError):
        return False
    return parent_id == int(unregister_task.id) and parent_attempt == int(
        unregister_task.retry_count or 0
    )


def source_needs_reset_protection(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> bool:
    from apps.source.constants import PipelineStep
    from apps.source.models import SourceBackupPipelineEntry

    if BackupConfig.objects.filter(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    ).exists():
        return True
    config_ids = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        ).values_list("id", flat=True)
    )
    if (
        config_ids
        and BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        )
        .exclude(status=BackupSourceSnapshot.Status.DELETED)
        .exists()
    ):
        return True
    if (
        config_ids
        and RestorePlan.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        ).exists()
    ):
        return True
    endpoint = (
        RestoreRecord.EndpointType.AGENT
        if source_type == "agent"
        else RestoreRecord.EndpointType.NAS
    )
    if RestoreRecord.objects.filter(
        organization_id=organization_id,
        source_type=endpoint,
        source_ref_id=source_ref_id,
    ).exists():
        return True
    source_kind = SelectableSourceKind.AGENT if source_type == "agent" else source_type
    pipeline = SourceBackupPipelineEntry.objects.filter(
        organization_id=organization_id,
        source_kind=source_kind,
        ref_id=source_ref_id,
    ).first()
    return pipeline is not None and int(pipeline.step) == PipelineStep.READY


def _assert_strict_delete_blockers(
    *,
    ctx: SourceDeleteContext,
    force: bool,
    allow_terminal_agent_uninstall: bool = False,
) -> None:
    if force:
        return
    reasons: list[DeleteReason] = []
    if (
        ctx.is_agent
        and ctx.agent_node is not None
        and not allow_terminal_agent_uninstall
    ):
        if agent_connection_status(node=ctx.agent_node) == CONNECTION_OFFLINE:
            reasons.append(
                DeleteReason(
                    code="agent_offline",
                    detail=(
                        f'Agent "{ctx.display_name}" is offline — remote uninstall is required '
                        "in strict mode."
                    ),
                    source_id=ctx.selectable_id,
                    source_name=ctx.display_name,
                )
            )
    if ctx.nas_resource is not None:
        proxy = ctx.nas_resource.bound_node
        if proxy is None or proxy.availability != Node.Availability.ONLINE:
            reasons.append(
                DeleteReason(
                    code="proxy_offline",
                    detail=(
                        f'Proxy for "{ctx.display_name}" is offline — NAS unmount is required '
                        "in strict mode."
                    ),
                    source_id=ctx.selectable_id,
                    source_name=ctx.display_name,
                )
            )
    if reasons:
        raise BackupSourceDeleteFailed(
            message="Backup source was not deleted.", reasons=reasons
        )


def _nas_remote_operation_blockers(*, ctx: SourceDeleteContext) -> list[DeleteReason]:
    resource = ctx.nas_resource
    if resource is None:
        return []
    active_probe = resource.connection_test_status in ConnectionTestStatus.ACTIVE
    active_node_tasks: list[NodeTask] = []
    if resource.bound_node_id:
        active_node_tasks = list(
            NodeTask.objects.filter(
                organization_id=resource.organization_id,
                node_id=resource.bound_node_id,
                correlation_type__in={
                    "source.connection_test",
                    "source.mount",
                    "source.unmount",
                },
                correlation_id=str(resource.id),
                status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
            ).order_by("created_at", "id")
        )
    if not active_probe and not active_node_tasks:
        return []
    active_node_task = active_node_tasks[0] if active_node_tasks else None
    return [
        DeleteReason(
            code="source_operation_in_progress",
            detail=(
                "A Source NAS connection or mount operation is still running. "
                "Wait for it to finish before deregistering the source."
            ),
            source_id=ctx.selectable_id,
            source_name=ctx.display_name,
            reference_type=(
                TaskDependency.ReferenceType.NODE_TASK
                if active_node_task is not None
                else TaskDependency.ReferenceType.EXTERNAL
            ),
            reference_id=str(active_node_task.id)
            if active_node_task is not None
            else "",
            reference_task_type=(
                str(active_node_task.kind)
                if active_node_task is not None
                else "source.probe"
            ),
        )
    ]


def _direct_nas_repository_blockers(
    *,
    organization_id: int,
    ctx: SourceDeleteContext,
    executing_task_id: int | None = None,
    executing_task_attempt: int = 0,
) -> list[DeleteReason]:
    """Return stable active-task blockers for linked Direct NAS repositories."""
    configs = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        ).values("id", "repository_id")
    )
    repository_ids = {int(config["repository_id"]) for config in configs}
    repositories = Repository.objects.filter(
        organization_id=organization_id,
        id__in=repository_ids,
        repo_type=Repository.Type.NAS,
        bind_node_id__isnull=True,
    )
    reasons: list[DeleteReason] = []
    for repository in repositories:
        ignored_task_ids: list[int] = []
        if executing_task_id:
            ignored_task_ids.append(int(executing_task_id))
            ignored_task_ids.extend(
                RepositoryTask.objects.filter(
                    repository=repository,
                    operation_type=RepositoryTask.OperationType.CLEANUP_TARGET,
                    triggered_by_task_id=int(executing_task_id),
                    task__status__in=_ACTIVE_TASK_STATUSES,
                    task__request_payload__source_unregister_attempt=int(
                        executing_task_attempt
                    ),
                ).values_list("task_id", flat=True)
            )
        for blocker in repository_active_task_blockers(
            repository=repository,
            ignored_task_ids=ignored_task_ids,
        ):
            reasons.append(
                DeleteReason(
                    code="repository_cleanup_blocked",
                    detail=str(
                        blocker.get("detail")
                        or "A repository operation is still active."
                    ),
                    source_id=ctx.selectable_id,
                    source_name=ctx.display_name,
                    repository_id=repository.id,
                    repository_name=repository.name,
                    reference_type=TaskDependency.ReferenceType.TASK,
                    reference_id=str(blocker.get("task_uuid") or ""),
                    reference_task_type=str(blocker.get("task_type") or ""),
                    blocking_task_uuid=str(blocker.get("task_uuid") or ""),
                )
            )
    return reasons


def _normalize_delete_ids(ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in ids:
        key = str(value).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    if not normalized:
        raise BackupSourceDeleteFailed(
            message="No backup sources were specified.",
            reasons=[DeleteReason(code="empty_ids", detail="ids must not be empty.")],
        )
    return normalized


def _prepare_delete_batch(
    *,
    org: Organization,
    ids: list[str],
    force: bool,
    executing_task_uuid: str | None = None,
) -> list[tuple[SourceDeleteContext, dict[str, int], list[DeleteWarning]]]:
    prepared: list[tuple[SourceDeleteContext, dict[str, int], list[DeleteWarning]]] = []
    for selectable_id in ids:
        ctx = _resolve_context(organization_id=org.id, selectable_id=selectable_id)
        if ctx is None:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=[
                    DeleteReason(
                        code="source_not_found",
                        detail="Backup source was not found.",
                        source_id=selectable_id,
                    )
                ],
            )
        active_unregister = _active_unregister_task_for_source(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        )
        owns_unregister = bool(
            active_unregister is not None
            and executing_task_uuid
            and str(active_unregister.task_uuid) == executing_task_uuid
        )
        if active_unregister is not None and not owns_unregister:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=[
                    DeleteReason(
                        code="unregister_in_progress",
                        detail="A source deregistration task is already running.",
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                    )
                ],
            )
        active_reset = _active_reset_task_for_source(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        )
        if active_reset:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=[
                    DeleteReason(
                        code="reset_in_progress",
                        detail="A backup configuration reset is already running.",
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                        reference_type=TaskDependency.ReferenceType.TASK,
                        reference_id=str(active_reset.task_uuid),
                        reference_task_type=active_reset.task_type,
                        blocking_task_uuid=str(active_reset.task_uuid),
                    )
                ],
            )
        running = _running_tasks_for_source(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        )
        if running:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=[
                    DeleteReason(
                        code="running_tasks",
                        detail=(
                            f'{running_task.task_type} task "{running_task.display_name}" '
                            "is still active. Submit a new deregistration request after it finishes."
                        ),
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                        reference_type=TaskDependency.ReferenceType.TASK,
                        reference_id=str(running_task.task_uuid),
                        reference_task_type=running_task.task_type,
                        blocking_task_uuid=str(running_task.task_uuid),
                    )
                    for running_task in running
                ],
            )
        nas_operation_blockers = _nas_remote_operation_blockers(ctx=ctx)
        if nas_operation_blockers:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=nas_operation_blockers,
            )
        repository_blockers = _direct_nas_repository_blockers(
            organization_id=org.id,
            ctx=ctx,
            executing_task_id=(active_unregister.id if owns_unregister else None),
            executing_task_attempt=(
                int(active_unregister.retry_count or 0) if owns_unregister else 0
            ),
        )
        if repository_blockers:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=repository_blockers,
            )
        if ctx.is_agent and ctx.agent_node is not None:
            from apps.node.services.internal.node_lifecycle import (
                _active_lifecycle_task,
            )
            from apps.node.services.internal.node_workload import (
                get_node_workload_blockers,
            )

            active_lifecycle = _active_lifecycle_task(org=org, node=ctx.agent_node)
            lifecycle_payload = (
                active_lifecycle.payload
                if active_lifecycle is not None
                and isinstance(active_lifecycle.payload, dict)
                else {}
            )
            owns_lifecycle = bool(
                owns_unregister
                and active_lifecycle is not None
                and active_lifecycle.kind == "agent.uninstall"
                and int(lifecycle_payload.get("source_unregister_task_id") or 0)
                == int(active_unregister.id)
                and int(lifecycle_payload.get("source_unregister_attempt") or 0)
                == int(active_unregister.retry_count or 0)
            )
            if active_lifecycle is not None and not owns_lifecycle:
                raise BackupSourceDeleteFailed(
                    message="Backup source was not deleted.",
                    reasons=[
                        DeleteReason(
                            code="lifecycle_in_progress",
                            detail="A lifecycle operation is already in progress.",
                            source_id=ctx.selectable_id,
                            source_name=ctx.display_name,
                            reference_type=TaskDependency.ReferenceType.NODE_TASK,
                            reference_id=str(active_lifecycle.id),
                            reference_task_type=str(
                                active_lifecycle.kind or "node.lifecycle"
                            ),
                        )
                    ],
                )
            node_blockers = get_node_workload_blockers(node=ctx.agent_node)
            non_product_blockers = [
                blocker
                for blocker in node_blockers
                if blocker.code not in {"backup_running", "restore_running"}
            ]
            if non_product_blockers:
                blocker_reasons: list[DeleteReason] = []
                for blocker in non_product_blockers:
                    product_task = None
                    if blocker.code == "node_task_running":
                        node_task = NodeTask.objects.filter(
                            pk=blocker.task_uuid
                        ).first()
                        if node_task is not None:
                            product_task = _product_task_for_node_task(
                                organization_id=org.id,
                                node_task=node_task,
                            )
                    if (
                        owns_unregister
                        and blocker.task_type == "snapshot.delete"
                        and _snapshot_delete_owned_by_unregister_attempt(
                            product_task=product_task,
                            unregister_task=active_unregister,
                        )
                    ):
                        continue
                    blocker_reasons.append(
                        DeleteReason(
                            code="node_workload_active",
                            detail=blocker.label,
                            source_id=ctx.selectable_id,
                            source_name=ctx.display_name,
                            reference_type=(
                                TaskDependency.ReferenceType.TASK
                                if product_task is not None
                                else TaskDependency.ReferenceType.NODE_TASK
                            ),
                            reference_id=(
                                str(product_task.task_uuid)
                                if product_task is not None
                                else blocker.task_uuid
                            ),
                            reference_task_type=blocker.task_type,
                            blocking_task_uuid=(
                                str(product_task.task_uuid)
                                if product_task is not None
                                else ""
                            ),
                        )
                    )
                if blocker_reasons:
                    raise BackupSourceDeleteFailed(
                        message="Backup source was not deleted.",
                        reasons=blocker_reasons,
                    )
            latest_remove = (
                NodeTask.objects.filter(
                    organization_id=org.id,
                    node_id=ctx.agent_node.id,
                    kind="agent.uninstall",
                    correlation_type="node.lifecycle",
                    correlation_id=f"remove:{ctx.agent_node.id}",
                )
                .order_by("-created_at", "-id")
                .first()
            )
            latest_remove_payload = (
                latest_remove.payload
                if latest_remove is not None and isinstance(latest_remove.payload, dict)
                else {}
            )
            latest_remove_result = (
                latest_remove.result
                if latest_remove is not None and isinstance(latest_remove.result, dict)
                else {}
            )
            successful_prior_uninstall = bool(
                latest_remove is not None
                and latest_remove.status == NodeTask.Status.SUCCESS
                and latest_remove_result.get("completion_received_at")
                and bool(latest_remove_result.get("cleanup_complete"))
            )
            owns_terminal_agent_uninstall = bool(
                owns_unregister
                and latest_remove is not None
                and latest_remove.status
                in {
                    NodeTask.Status.SUCCESS,
                    NodeTask.Status.FAILED,
                    NodeTask.Status.TIMEOUT,
                    NodeTask.Status.CANCELED,
                }
                and int(latest_remove_payload.get("source_unregister_task_id") or 0)
                == int(active_unregister.id)
                and (
                    int(latest_remove_payload.get("source_unregister_attempt") or 0)
                    == int(active_unregister.retry_count or 0)
                    or successful_prior_uninstall
                )
            )
        else:
            owns_terminal_agent_uninstall = False
        _assert_strict_delete_blockers(
            ctx=ctx,
            force=force,
            allow_terminal_agent_uninstall=owns_terminal_agent_uninstall,
        )
        prepared.append((ctx, {}, []))
    return prepared


def evaluate_source_deregistration(
    *,
    org: Organization,
    selectable_id: str,
    force: bool,
    executing_task_uuid: str | None = None,
) -> SourceDeregistrationDecision:
    """Return the authoritative eligibility decision used by every entry point."""
    try:
        _prepare_delete_batch(
            org=org,
            ids=[selectable_id],
            force=force,
            executing_task_uuid=executing_task_uuid,
        )
    except BackupSourceDeleteFailed as exc:
        reasons = tuple(exc.reasons)
        codes = {reason.code for reason in reasons}
        if reasons and codes <= {
            "lifecycle_in_progress",
            "node_workload_active",
            "repository_cleanup_blocked",
            "reset_in_progress",
            "running_tasks",
            "source_operation_in_progress",
        }:
            return SourceDeregistrationDecision("waiting", reasons)
        if codes <= {"source_not_found", "invalid_id", "empty_ids"}:
            return SourceDeregistrationDecision("invalid", reasons)
        return SourceDeregistrationDecision("blocked", reasons)
    return SourceDeregistrationDecision("ready")


def _lock_delete_identities(*, organization_id: int, ids: list[str]) -> None:
    """Lock source identity rows before the authoritative delete preflight."""
    node_ids: set[int] = set()
    resource_ids: set[int] = set()
    for selectable_id in ids:
        parsed = parse_selectable_id(selectable_id)
        if parsed is None:
            continue
        kind, ref_id = parsed
        if kind == SelectableSourceKind.AGENT:
            node_ids.add(int(ref_id))
        elif kind == SelectableSourceKind.NAS:
            resource_ids.add(int(ref_id))

    if node_ids:
        list(
            Node.objects.select_for_update()
            .filter(
                organization_id=organization_id,
                id__in=sorted(node_ids),
            )
            .order_by("id")
        )
    if resource_ids:
        list(
            SourceResource.all_objects.select_for_update()
            .filter(
                organization_id=organization_id,
                id__in=sorted(resource_ids),
            )
            .order_by("id")
        )


def _set_source_nas_removal_status(
    *,
    organization_id: int,
    ids: list[str],
    status: str,
    message: str,
) -> None:
    """Persist the Source NAS removal fence or retryable failure state."""
    resource_ids = [
        int(parsed[1])
        for selectable_id in ids
        if (parsed := parse_selectable_id(selectable_id)) is not None
        and parsed[0] == SelectableSourceKind.NAS
    ]
    if not resource_ids:
        return
    SourceResource.all_objects.filter(
        organization_id=organization_id,
        id__in=resource_ids,
        is_deleted=False,
    ).update(
        status=status,
        status_message=message[:2000],
        connection_test_status=ConnectionTestStatus.IDLE,
        connection_probe_token=None,
        updated_at=timezone.now(),
    )


def _resolve_unregister_user(*, org: Organization, task: Task):
    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    user_id = int(payload.get("user_id") or 0)
    if user_id <= 0:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=user_id).first()


def _renew_unregister_execution_lease(
    *,
    task: Task,
    owner_token: str,
) -> None:
    """Renew and fence one asynchronous unregister execution."""
    if not owner_token:
        return
    from apps.source.tasks.source_unregister import (
        SourceUnregisterLeaseLost,
        renew_source_unregister_lease,
    )

    if not renew_source_unregister_lease(
        task_id=int(task.id),
        owner_token=owner_token,
    ):
        raise SourceUnregisterLeaseLost(
            f"source unregister task id={task.id} no longer owns its lease"
        )


def _cleanup_direct_nas_for_unregister(
    *,
    org: Organization,
    ctx: SourceDeleteContext,
    unregister_task: Task,
    user,
    force: bool,
    lease_owner_token: str = "",
) -> DirectNasCleanupOutcome:
    configs = list(
        BackupConfig.objects.filter(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        ).values("id", "repository_id")
    )
    configs_by_repository: dict[int, list[int]] = {}
    repository_ids = {int(config["repository_id"]) for config in configs}
    repositories = {
        repository.id: repository
        for repository in Repository.objects.filter(
            organization_id=org.id,
            id__in=repository_ids,
        )
    }
    for config in configs:
        repository = repositories.get(int(config["repository_id"]))
        if repository is None:
            continue
        if (
            repository.repo_type != Repository.Type.NAS
            or repository.bind_node_id is not None
        ):
            continue
        configs_by_repository.setdefault(repository.id, []).append(int(config["id"]))

    cleaned_repository_ids: set[int] = set()
    cleanup_tasks: list[dict[str, Any]] = []
    warnings: list[DeleteWarning] = []
    retained_resources: list[str] = []

    def cleanup_failure(
        *,
        repository: Repository,
        code: str,
        detail: str,
        target_id: int | None = None,
        retained: list[str] | None = None,
    ) -> DeleteReason:
        reason = DeleteReason(
            code=code,
            detail=detail,
            source_id=ctx.selectable_id,
            source_name=ctx.display_name,
            repository_id=repository.id,
            repository_name=repository.name,
        )
        if force:
            warnings.append(
                DeleteWarning(
                    code=reason.code,
                    detail=reason.detail,
                    source_id=reason.source_id,
                    source_name=reason.source_name,
                )
            )
            if retained:
                retained_resources.extend(retained)
            else:
                target_suffix = (
                    f":target:{target_id}" if target_id is not None else ":unresolved"
                )
                retained_resources.append(
                    f"repository_target:repository:{repository.id}{target_suffix}"
                )
        return reason

    for repository_id, config_ids in configs_by_repository.items():
        repository = repositories[repository_id]
        target_ids = direct_nas_cleanup_target_ids(
            repository=repository,
            backup_config_ids=config_ids,
            owner_node_id=ctx.agent_node.id if ctx.agent_node is not None else None,
        )
        if not target_ids:
            reason = cleanup_failure(
                repository=repository,
                code="repository_cleanup_target_missing",
                detail="The Direct NAS physical repository target could not be resolved.",
            )
            if not force:
                raise BackupSourceDeleteFailed(
                    message="Direct NAS repository cleanup failed.",
                    reasons=[reason],
                )
            continue
        repository_failed = False
        for target_id in target_ids:
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
            try:
                repository_task = create_direct_nas_target_cleanup_task(
                    repository=repository,
                    target_id=target_id,
                    triggered_by_task=unregister_task,
                    requested_by=user,
                    force=force,
                    dispatch=False,
                )
            except RepositoryCleanupBlocked as exc:
                details = "; ".join(
                    str(item.get("detail") or item.get("code") or "cleanup blocked")
                    for item in exc.preflight.get("blockers", [])
                )
                raise BackupSourceDeleteFailed(
                    message="Direct NAS repository cleanup was blocked.",
                    reasons=[
                        DeleteReason(
                            code="repository_cleanup_blocked",
                            detail=details or "Repository cleanup was blocked.",
                            source_id=ctx.selectable_id,
                            source_name=ctx.display_name,
                            repository_id=repository.id,
                            repository_name=repository.name,
                        )
                    ],
                ) from exc
            except Exception as exc:
                logger.exception(
                    "Direct NAS cleanup task creation failed "
                    "source=%s repository=%s target=%s",
                    ctx.selectable_id,
                    repository.id,
                    target_id,
                )
                reason = cleanup_failure(
                    repository=repository,
                    target_id=target_id,
                    code="repository_cleanup_create_failed",
                    detail=str(exc),
                )
                if not force:
                    raise BackupSourceDeleteFailed(
                        message="Direct NAS repository cleanup failed.",
                        reasons=[reason],
                    ) from exc
                repository_failed = True
                continue

            try:
                run_repository_cleanup_task(repository_task_id=repository_task.id)
                repository_task.task.refresh_from_db()
            except Exception as exc:
                _renew_unregister_execution_lease(
                    task=unregister_task,
                    owner_token=lease_owner_token,
                )
                logger.exception(
                    "Direct NAS cleanup task execution failed "
                    "source=%s repository=%s target=%s",
                    ctx.selectable_id,
                    repository.id,
                    target_id,
                )
                reason = cleanup_failure(
                    repository=repository,
                    target_id=target_id,
                    code="repository_cleanup_execution_failed",
                    detail=str(exc),
                )
                if not force:
                    raise BackupSourceDeleteFailed(
                        message="Direct NAS repository cleanup failed.",
                        reasons=[reason],
                    ) from exc
                repository_failed = True
                continue
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
            cleanup_tasks.append(repository_cleanup_task_payload(repository_task))
            if repository_task.task.status in _ACTIVE_TASK_STATUSES:
                return DirectNasCleanupOutcome(
                    cleaned_repository_ids=cleaned_repository_ids,
                    cleanup_tasks=cleanup_tasks,
                    warnings=tuple(warnings),
                    retained_resources=tuple(dict.fromkeys(retained_resources)),
                    waiting=True,
                )
            if repository_task.task.status != Task.Status.SUCCESS:
                if repository_task.task.error_code == "REPOSITORY_CLEANUP_BLOCKED":
                    raise BackupSourceDeleteFailed(
                        message="Direct NAS repository cleanup was blocked.",
                        reasons=[
                            DeleteReason(
                                code="repository_cleanup_blocked",
                                detail=(
                                    repository_task.task.error_message
                                    or "Repository cleanup was blocked."
                                ),
                                source_id=ctx.selectable_id,
                                source_name=ctx.display_name,
                                repository_id=repository.id,
                                repository_name=repository.name,
                            )
                        ],
                    )
                reason = cleanup_failure(
                    repository=repository,
                    target_id=target_id,
                    code=repository_task.task.error_code or "repository_cleanup_failed",
                    detail=(
                        repository_task.task.error_message
                        or "Physical repository cleanup failed."
                    ),
                )
                if not force:
                    raise BackupSourceDeleteFailed(
                        message="Direct NAS repository cleanup failed.",
                        reasons=[reason],
                    )
                repository_failed = True
                continue

            child_result = (
                repository_task.task.result_payload
                if isinstance(repository_task.task.result_payload, dict)
                else {}
            )
            if not bool(child_result.get("cleanup_complete", True)):
                child_failures = [
                    item
                    for item in child_result.get("cleanup_failures") or []
                    if isinstance(item, dict)
                ]
                child_retained = [
                    str(item)
                    for item in child_result.get("retained_resources") or []
                    if str(item).strip()
                ]
                if not child_failures:
                    child_failures = [
                        {
                            "code": "repository_cleanup_incomplete",
                            "detail": "Physical repository cleanup retained residue.",
                        }
                    ]
                incomplete_reasons: list[DeleteReason] = []
                for child_failure in child_failures:
                    incomplete_reasons.append(
                        cleanup_failure(
                            repository=repository,
                            target_id=target_id,
                            code=str(
                                child_failure.get("code")
                                or "repository_cleanup_incomplete"
                            ),
                            detail=str(
                                child_failure.get("detail")
                                or "Physical repository cleanup retained residue."
                            ),
                            retained=child_retained,
                        )
                    )
                if not force:
                    raise BackupSourceDeleteFailed(
                        message="Direct NAS repository cleanup failed.",
                        reasons=incomplete_reasons,
                    )
                repository_failed = True
        if not repository_failed:
            cleaned_repository_ids.add(repository.id)
    return DirectNasCleanupOutcome(
        cleaned_repository_ids=cleaned_repository_ids,
        cleanup_tasks=cleanup_tasks,
        warnings=tuple(warnings),
        retained_resources=tuple(dict.fromkeys(retained_resources)),
    )


def _merge_source_cleanup_outcome(
    source: dict[str, Any],
    *,
    cleanup_complete: bool,
    cleanup_failures: list[dict[str, Any]],
    retained_resources: list[str],
) -> None:
    """Merge one cleanup stage without discarding residue from prior stages."""
    source["cleanup_complete"] = bool(source.get("cleanup_complete", True)) and bool(
        cleanup_complete
    )

    merged_failures: list[dict[str, Any]] = []
    seen_failures: set[tuple[str, str, str]] = set()
    for failure in [
        *(source.get("cleanup_failures") or []),
        *cleanup_failures,
    ]:
        if not isinstance(failure, dict):
            continue
        item = dict(failure)
        key = (
            str(item.get("code") or "cleanup_failed"),
            str(item.get("detail") or ""),
            str(item.get("source_id") or source.get("source_id") or ""),
        )
        if key in seen_failures:
            continue
        seen_failures.add(key)
        merged_failures.append(item)
    source["cleanup_failures"] = merged_failures

    source["retained_resources"] = list(
        dict.fromkeys(
            str(resource)
            for resource in [
                *(source.get("retained_resources") or []),
                *retained_resources,
            ]
            if str(resource).strip()
        )
    )


def _dedupe_cleanup_items(items: list[Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in items:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        key = (
            str(item.get("source_id") or "").strip(),
            str(item.get("code") or item.get("task_id") or "").strip().lower(),
            " ".join(
                str(
                    item.get("detail") or item.get("task_uuid") or item.get("id") or ""
                ).split()
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _merge_cleanup_task_items(
    previous: Any,
    current: Any,
) -> list[dict[str, Any]]:
    """Merge child-task evidence while preferring the newest task state."""
    merged: list[dict[str, Any]] = []
    indexes: dict[tuple[str, str], int] = {}
    for value in [
        *(previous if isinstance(previous, list) else []),
        *(current if isinstance(current, list) else []),
    ]:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        task_identity = str(
            item.get("task_id") or item.get("task_uuid") or item.get("id") or ""
        )
        key = (str(item.get("source_id") or ""), task_identity)
        if task_identity and key in indexes:
            merged[indexes[key]] = item
            continue
        if task_identity:
            indexes[key] = len(merged)
        merged.append(item)
    return merged


def _merge_cleanup_counts(previous: Any, current: Any) -> dict[str, int]:
    previous_dict = previous if isinstance(previous, dict) else {}
    current_dict = current if isinstance(current, dict) else {}
    return {
        str(key): max(
            int(previous_dict.get(key) or 0),
            int(current_dict.get(key) or 0),
        )
        for key in {*previous_dict, *current_dict}
    }


def _merge_checkpoint_source(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    merged.setdefault("source_id", previous.get("source_id"))
    merged.setdefault("source_name", previous.get("source_name"))
    source_id = str(merged.get("source_id") or "")
    source_name = str(merged.get("source_name") or "")
    cleanup_failures: list[dict[str, Any]] = []
    for value in [
        *(previous.get("cleanup_failures") or []),
        *(current.get("cleanup_failures") or []),
    ]:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        if source_id and not item.get("source_id"):
            item["source_id"] = source_id
        if source_name and not item.get("source_name"):
            item["source_name"] = source_name
        cleanup_failures.append(item)
    merged["cleanup"] = _merge_cleanup_counts(
        previous.get("cleanup"),
        current.get("cleanup"),
    )
    merged["cleanup_failures"] = []
    _merge_source_cleanup_outcome(
        merged,
        cleanup_complete=(
            bool(previous.get("cleanup_complete", True))
            and bool(current.get("cleanup_complete", True))
        ),
        cleanup_failures=_dedupe_cleanup_items(cleanup_failures),
        retained_resources=[
            *(previous.get("retained_resources") or []),
            *(current.get("retained_resources") or []),
        ],
    )
    merged["warnings"] = _dedupe_cleanup_items(
        [
            *(previous.get("warnings") or []),
            *(current.get("warnings") or []),
        ]
    )
    return merged


def _merge_unregister_checkpoint(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Merge durable cleanup evidence across asynchronous unregister advances."""
    if not previous:
        merged = dict(current)
    else:
        merged = dict(current)
        merged["warnings"] = _dedupe_cleanup_items(
            [
                *(previous.get("warnings") or []),
                *(current.get("warnings") or []),
            ]
        )
        merged["cleanup"] = _merge_cleanup_counts(
            previous.get("cleanup"),
            current.get("cleanup"),
        )
        merged["deleted"] = list(
            dict.fromkeys(
                str(value)
                for value in [
                    *(previous.get("deleted") or []),
                    *(current.get("deleted") or []),
                ]
                if str(value).strip()
            )
        )
        merged["repository_cleanup_tasks"] = _merge_cleanup_task_items(
            previous.get("repository_cleanup_tasks"),
            current.get("repository_cleanup_tasks"),
        )
        merged["snapshot_cleanup_tasks"] = _merge_cleanup_task_items(
            previous.get("snapshot_cleanup_tasks"),
            current.get("snapshot_cleanup_tasks"),
        )

    previous_sources = {
        str(item.get("source_id") or ""): dict(item)
        for item in previous.get("sources") or []
        if isinstance(item, dict) and str(item.get("source_id") or "")
    }
    current_sources = {
        str(item.get("source_id") or ""): dict(item)
        for item in current.get("sources") or []
        if isinstance(item, dict) and str(item.get("source_id") or "")
    }
    source_ids = list(dict.fromkeys([*previous_sources, *current_sources]))
    merged_sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        prior = previous_sources.get(source_id, {})
        latest = current_sources.get(source_id)
        if latest is None:
            merged_sources.append(prior)
            continue
        merged_sources.append(_merge_checkpoint_source(prior, latest))
    merged["sources"] = merged_sources

    default_source = merged_sources[0] if len(merged_sources) == 1 else {}

    def normalized_failure(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = dict(value)
        if default_source:
            if not item.get("source_id"):
                item["source_id"] = default_source.get("source_id")
            if not item.get("source_name"):
                item["source_name"] = default_source.get("source_name")
        return item

    cleanup_failures = _dedupe_cleanup_items(
        [
            *(
                normalized_failure(item)
                for item in previous.get("cleanup_failures") or []
            ),
            *(
                normalized_failure(item)
                for item in current.get("cleanup_failures") or []
            ),
            *(
                failure
                for source in merged_sources
                for failure in source.get("cleanup_failures") or []
            ),
        ]
    )
    retained_resources = list(
        dict.fromkeys(
            str(resource)
            for resource in [
                *(previous.get("retained_resources") or []),
                *(current.get("retained_resources") or []),
                *(
                    resource
                    for source in merged_sources
                    for resource in source.get("retained_resources") or []
                ),
            ]
            if str(resource).strip()
        )
    )
    sources_complete = all(
        bool(source.get("cleanup_complete", True)) for source in merged_sources
    )
    cleanup_complete = (
        bool(previous.get("cleanup_complete", True))
        and bool(current.get("cleanup_complete", True))
        and sources_complete
        and not cleanup_failures
        and not retained_resources
    )
    merged["cleanup_complete"] = cleanup_complete
    merged["cleanup_failures"] = cleanup_failures
    merged["retained_resources"] = retained_resources
    if str(current.get("result") or "") != "waiting" and (
        not cleanup_complete or merged.get("warnings")
    ):
        merged["result"] = "partial_success"
    return merged


def _source_unregister_checkpoint(task: Task) -> dict[str, Any]:
    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    return dict(payload) if payload.get("result") == "waiting" else {}


def _save_source_unregister_checkpoint(
    *,
    task: Task,
    response: dict[str, Any],
) -> dict[str, Any]:
    checkpoint = _merge_unregister_checkpoint(
        _source_unregister_checkpoint(task),
        response,
    )
    checkpoint["result"] = "waiting"
    Task.objects.filter(pk=task.pk, status=Task.Status.RUNNING).update(
        result_payload=checkpoint,
        updated_at=timezone.now(),
    )
    task.result_payload = checkpoint
    return checkpoint


def _purge_source_control_plane(
    *,
    org: Organization,
    ctx: SourceDeleteContext,
    user,
) -> dict[str, int]:
    """Purge protection rows and identity within the final task transaction."""
    config_ids = list(
        BackupConfig.objects.filter(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        ).values_list("id", flat=True)
    )
    _cleanup_download_artifacts(organization_id=org.id, config_ids=config_ids)
    cleanup = _purge_protection_db(
        organization_id=org.id,
        source_type=ctx.source_type,
        source_ref_id=ctx.source_ref_id,
    )
    cleanup["tasks_orphaned"] = _mark_tasks_orphaned(
        organization_id=org.id,
        source_type=ctx.source_type,
        source_ref_id=ctx.source_ref_id,
        source_name=ctx.display_name,
    )
    current_ctx = _resolve_context(
        organization_id=org.id,
        selectable_id=ctx.selectable_id,
    )
    if current_ctx is not None:
        _soft_delete_identity(org=org, ctx=current_ctx, user=user)
    return cleanup


@transaction.atomic
def _complete_source_unregister_transaction(
    *,
    org: Organization,
    contexts: list[SourceDeleteContext],
    force: bool,
    user,
    unregister_task: Task,
    cleanup_checkpoint: dict[str, Any],
    deleted: list[str],
    all_warnings: list[dict[str, Any]],
    aggregate_cleanup: dict[str, int],
    direct_cleanup_by_source: dict[str, list[dict[str, Any]]],
    snapshot_cleanup_by_source: dict[str, list[dict[str, Any]]],
    per_source: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically purge control-plane state and complete unregister."""
    _lock_delete_identities(
        organization_id=org.id,
        ids=[ctx.selectable_id for ctx in contexts],
    )
    locked_task = Task.objects.select_for_update().get(pk=unregister_task.pk)
    if locked_task.status in _UNREGISTER_TERMINAL:
        return (
            dict(locked_task.result_payload)
            if isinstance(locked_task.result_payload, dict)
            else {}
        )

    for ctx in contexts:
        running = _running_tasks_for_source(
            organization_id=org.id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        )
        if running:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=[
                    DeleteReason(
                        code="running_tasks",
                        detail=(
                            f"{len(running)} backup or restore task(s) started "
                            "while deregistration was running."
                        ),
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                    )
                ],
            )

    sources_by_id = {
        str(source.get("source_id") or ""): source
        for source in per_source
        if isinstance(source, dict)
    }
    for ctx in contexts:
        cleanup = _purge_source_control_plane(org=org, ctx=ctx, user=user)
        source = sources_by_id.get(ctx.selectable_id)
        if source is not None:
            source["cleanup"] = _merge_cleanup_counts(
                source.get("cleanup"),
                cleanup,
            )
        for key in aggregate_cleanup:
            aggregate_cleanup[key] = max(
                int(aggregate_cleanup.get(key) or 0),
                int(cleanup.get(key) or 0),
            )

    cleanup_failures: list[dict[str, Any]] = []
    seen_cleanup_failures: set[tuple[str, str, str]] = set()

    def append_cleanup_failure(
        failure: dict[str, Any],
        *,
        source_id: str = "",
        source_name: str = "",
    ) -> None:
        item = dict(failure)
        if source_id:
            item.setdefault("source_id", source_id)
        if source_name:
            item.setdefault("source_name", source_name)
        key = (
            str(item.get("source_id") or ""),
            str(item.get("code") or "cleanup_failed"),
            str(item.get("detail") or ""),
        )
        if key in seen_cleanup_failures:
            return
        seen_cleanup_failures.add(key)
        cleanup_failures.append(item)

    for source in per_source:
        for failure in source.get("cleanup_failures") or []:
            if not isinstance(failure, dict):
                continue
            append_cleanup_failure(
                failure,
                source_id=str(source.get("source_id") or ""),
                source_name=str(source.get("source_name") or ""),
            )

    retained_resources = list(
        dict.fromkeys(
            str(resource)
            for source in per_source
            for resource in source.get("retained_resources") or []
            if str(resource).strip()
        )
    )
    if force:
        for warning in all_warnings:
            if not isinstance(warning, dict):
                continue
            append_cleanup_failure(
                {
                    "code": str(warning.get("code") or "cleanup_warning"),
                    "detail": str(warning.get("detail") or "Cleanup retained residue."),
                },
                source_id=str(warning.get("source_id") or ""),
                source_name=str(warning.get("source_name") or ""),
            )
    cleanup_complete = (
        all(bool(source.get("cleanup_complete", True)) for source in per_source)
        and not cleanup_failures
        and not retained_resources
    )
    result = "partial_success" if not cleanup_complete or all_warnings else "success"
    response = _merge_unregister_checkpoint(
        cleanup_checkpoint,
        {
            "ok": True,
            "accepted": False,
            "result": result,
            "deleted": deleted,
            "pending_removals": [],
            "warnings": all_warnings,
            "cleanup": aggregate_cleanup,
            "force": bool(force),
            "outcome": ("force_cleanup_success" if force else "cleanup_success"),
            "cleanup_complete": cleanup_complete,
            "cleanup_failures": cleanup_failures,
            "retained_resources": retained_resources,
            "repository_cleanup_tasks": [
                item
                for cleanup_tasks in direct_cleanup_by_source.values()
                for item in cleanup_tasks
            ],
            "snapshot_cleanup_tasks": [
                item
                for cleanup_tasks in snapshot_cleanup_by_source.values()
                for item in cleanup_tasks
            ],
            "sources": per_source,
            "task_id": locked_task.id,
            "task_uuid": str(locked_task.task_uuid),
        },
    )
    result = str(response.get("result") or "success")
    deleted = [str(value) for value in response.get("deleted") or []]
    warnings = [
        dict(value)
        for value in response.get("warnings") or []
        if isinstance(value, dict)
    ]
    cleanup = {
        str(key): int(value or 0)
        for key, value in (response.get("cleanup") or {}).items()
    }
    _set_unregister_step(
        task=locked_task,
        step_name="cleanup_source_endpoint",
        status=(
            TaskStep.Status.WARNING
            if result == "partial_success"
            else TaskStep.Status.SUCCESS
        ),
        progress=85,
        message=(
            "Source endpoint cleanup completed with retained resources"
            if result == "partial_success"
            else "Source endpoint cleanup completed"
        ),
        level="WARN" if result == "partial_success" else "INFO",
        metadata={
            "pending_removals": [],
            "warnings": warnings,
            "cleanup_complete": bool(response.get("cleanup_complete", True)),
            "retained_resources": response.get("retained_resources") or [],
        },
    )
    _set_unregister_step(
        task=locked_task,
        step_name="reset_backup_config",
        status=TaskStep.Status.SUCCESS,
        progress=95,
        message="Backup configuration data reset",
        metadata={"cleanup": cleanup},
    )
    _set_unregister_step(
        task=locked_task,
        step_name="finalize_source_unregister",
        status=TaskStep.Status.SUCCESS,
        progress=100,
        message="Source deregistration finalized",
        metadata={"result": result, "deleted": deleted},
    )
    write_audit_log(
        organization=org,
        user=user,
        action=AuditAction.DELETE,
        resource_type="backup_source",
        resource_id=",".join(deleted),
        resource_name=f"{len(deleted)} source(s)",
        result=(
            AuditResult.PARTIAL if result == "partial_success" else AuditResult.SUCCESS
        ),
        metadata={
            "force": force,
            "result": result,
            "deleted": deleted,
            "cleanup": cleanup,
            "warnings": warnings,
        },
    )
    _complete_unregister_task(
        task=locked_task,
        status=Task.Status.SUCCESS,
        result_payload=response,
    )
    return response


def _execute_source_unregister_work(
    *,
    org: Organization,
    prepared: list[tuple[SourceDeleteContext, dict[str, int], list[DeleteWarning]]],
    force: bool,
    user,
    unregister_task: Task,
    lease_owner_token: str = "",
) -> dict[str, Any]:
    normalized = [ctx.selectable_id for ctx, _, _ in prepared]
    cleanup_checkpoint = _source_unregister_checkpoint(unregister_task)
    _set_unregister_step(
        task=unregister_task,
        step_name="cleanup_direct_nas_repositories",
        status=TaskStep.Status.RUNNING,
        progress=20,
        message="Cleaning Direct NAS physical repositories",
    )

    deleted: list[str] = []
    per_source: list[dict[str, Any]] = []
    pending_after_commit: list[tuple[str, int]] = []
    all_warnings: list[dict[str, Any]] = []
    aggregate_cleanup: dict[str, int] = {
        "snapshots_purged": 0,
        "repository_blobs_deleted": 0,
        "repository_purge_pending": 0,
        "backup_configs_removed": 0,
        "snapshots_removed": 0,
        "restore_plans_removed": 0,
        "restore_records_removed": 0,
        "tasks_orphaned": 0,
    }
    direct_cleanup_by_source: dict[str, list[dict[str, Any]]] = {}
    snapshot_cleanup_by_source: dict[str, list[dict[str, Any]]] = {}
    direct_cleanup_failures_by_source: dict[str, list[dict[str, Any]]] = {}
    direct_retained_by_source: dict[str, list[str]] = {}

    try:
        _renew_unregister_execution_lease(
            task=unregister_task,
            owner_token=lease_owner_token,
        )
        prepared_for_finalize: list[
            tuple[SourceDeleteContext, dict[str, int], list[DeleteWarning]]
        ] = []
        for ctx, _blob_stats, _warnings in prepared:
            cleanup_outcome = _cleanup_direct_nas_for_unregister(
                org=org,
                ctx=ctx,
                unregister_task=unregister_task,
                user=user,
                force=force,
                lease_owner_token=lease_owner_token,
            )
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
            direct_cleanup_by_source[ctx.selectable_id] = cleanup_outcome.cleanup_tasks
            direct_cleanup_failures_by_source[ctx.selectable_id] = [
                warning.as_dict() for warning in cleanup_outcome.warnings
            ]
            direct_retained_by_source[ctx.selectable_id] = list(
                cleanup_outcome.retained_resources
            )
            if cleanup_outcome.waiting:
                _set_unregister_step(
                    task=unregister_task,
                    step_name="cleanup_direct_nas_repositories",
                    status=TaskStep.Status.RUNNING,
                    progress=20,
                    message="Waiting for Direct NAS repository cleanup",
                    metadata={"cleanup_tasks": direct_cleanup_by_source},
                )
                waiting_failures = [
                    warning.as_dict() for warning in cleanup_outcome.warnings
                ]
                response = {
                    "ok": True,
                    "accepted": True,
                    "result": "waiting",
                    "deleted": [],
                    "pending_removals": [],
                    "warnings": waiting_failures,
                    "cleanup": {},
                    "cleanup_complete": not (
                        waiting_failures or cleanup_outcome.retained_resources
                    ),
                    "cleanup_failures": waiting_failures,
                    "retained_resources": list(cleanup_outcome.retained_resources),
                    "repository_cleanup_tasks": [
                        item
                        for cleanup_tasks in direct_cleanup_by_source.values()
                        for item in cleanup_tasks
                    ],
                    "sources": [
                        {
                            "source_id": ctx.selectable_id,
                            "source_name": ctx.display_name,
                            "cleanup": {},
                            "cleanup_complete": not (
                                waiting_failures or cleanup_outcome.retained_resources
                            ),
                            "cleanup_failures": waiting_failures,
                            "retained_resources": list(
                                cleanup_outcome.retained_resources
                            ),
                            "warnings": waiting_failures,
                        }
                    ],
                    "task_id": unregister_task.id,
                    "task_uuid": str(unregister_task.task_uuid),
                    "status": Task.Status.RUNNING,
                }
                return _save_source_unregister_checkpoint(
                    task=unregister_task,
                    response=_merge_unregister_checkpoint(
                        cleanup_checkpoint,
                        response,
                    ),
                )
            (
                blob_stats,
                warnings,
                reasons,
                snapshot_cleanup_waiting,
                snapshot_cleanup_tasks,
            ) = _prepare_single_source_snapshot_cleanup(
                organization_id=org.id,
                ctx=ctx,
                force=force,
                skip_repository_ids=cleanup_outcome.cleaned_repository_ids,
                unregister_task=unregister_task,
                lease_owner_token=lease_owner_token,
            )
            snapshot_cleanup_by_source[ctx.selectable_id] = snapshot_cleanup_tasks
            if snapshot_cleanup_waiting:
                _set_unregister_step(
                    task=unregister_task,
                    step_name="cleanup_direct_nas_repositories",
                    status=TaskStep.Status.RUNNING,
                    progress=25,
                    message="Waiting for backup snapshot cleanup",
                    metadata={"snapshot_cleanup_tasks": snapshot_cleanup_by_source},
                )
                waiting_failures = [warning.as_dict() for warning in warnings]
                response = {
                    "ok": True,
                    "accepted": True,
                    "result": "waiting",
                    "deleted": [],
                    "pending_removals": [],
                    "warnings": waiting_failures,
                    "cleanup": blob_stats,
                    "cleanup_complete": not waiting_failures,
                    "cleanup_failures": waiting_failures,
                    "retained_resources": [
                        resource
                        for warning in warnings
                        for resource in warning.retained_resources
                    ],
                    "repository_cleanup_tasks": [
                        item
                        for cleanup_tasks in direct_cleanup_by_source.values()
                        for item in cleanup_tasks
                    ],
                    "snapshot_cleanup_tasks": [
                        item
                        for cleanup_tasks in snapshot_cleanup_by_source.values()
                        for item in cleanup_tasks
                    ],
                    "sources": [
                        {
                            "source_id": ctx.selectable_id,
                            "source_name": ctx.display_name,
                            "cleanup": blob_stats,
                            "cleanup_complete": not waiting_failures,
                            "cleanup_failures": waiting_failures,
                            "retained_resources": [
                                resource
                                for warning in warnings
                                for resource in warning.retained_resources
                            ],
                            "warnings": waiting_failures,
                        }
                    ],
                    "task_id": unregister_task.id,
                    "task_uuid": str(unregister_task.task_uuid),
                    "status": Task.Status.RUNNING,
                }
                return _save_source_unregister_checkpoint(
                    task=unregister_task,
                    response=_merge_unregister_checkpoint(
                        cleanup_checkpoint,
                        response,
                    ),
                )
            if reasons:
                raise BackupSourceDeleteFailed(
                    message="Backup source was not deleted.",
                    reasons=reasons,
                )
            warnings.extend(cleanup_outcome.warnings)
            prepared_for_finalize.append((ctx, blob_stats, warnings))
        _set_unregister_step(
            task=unregister_task,
            step_name="cleanup_direct_nas_repositories",
            status=TaskStep.Status.SUCCESS,
            progress=30,
            message="Direct NAS repository cleanup completed",
            metadata={"cleanup_tasks": direct_cleanup_by_source},
        )
        _set_unregister_step(
            task=unregister_task,
            step_name="reset_backup_config",
            status=TaskStep.Status.PENDING,
            progress=35,
            message="Backup configuration reset deferred until endpoint cleanup completes",
        )
        # Remote NAS work must run after the preparation transaction commits
        # and outside the database cleanup transaction below.  Agent task
        # callbacks use another connection and cannot observe uncommitted
        # NodeTask rows.
        for ctx, _blob_stats, warnings in prepared_for_finalize:
            if ctx.nas_resource is None:
                continue
            reasons: list[DeleteReason] = []
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
            umount_result = _strict_nas_umount(
                ctx=ctx,
                force=force,
                reasons=reasons,
                warnings=warnings,
                unregister_task=unregister_task,
            )
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
            if umount_result.get("failed"):
                raise BackupSourceDeleteFailed(
                    message="Backup source was not deleted.",
                    reasons=reasons,
                )
        with transaction.atomic():
            for ctx, blob_stats, warnings in prepared_for_finalize:
                summary = _finalize_single_source_delete(
                    org=org,
                    ctx=ctx,
                    blob_stats=blob_stats,
                    warnings=warnings,
                    force=force,
                    unregister_task_id=unregister_task.id,
                    unregister_task_attempt=int(unregister_task.retry_count or 0),
                )
                summary["repository_cleanup_tasks"] = direct_cleanup_by_source.get(
                    ctx.selectable_id, []
                )
                direct_failures = direct_cleanup_failures_by_source.get(
                    ctx.selectable_id, []
                )
                direct_retained = direct_retained_by_source.get(ctx.selectable_id, [])
                if direct_failures or direct_retained:
                    _merge_source_cleanup_outcome(
                        summary,
                        cleanup_complete=False,
                        cleanup_failures=direct_failures,
                        retained_resources=direct_retained,
                    )
                warning_failures = [warning.as_dict() for warning in warnings]
                warning_retained = [
                    resource
                    for warning in warnings
                    for resource in warning.retained_resources
                ]
                if warning_failures or warning_retained:
                    _merge_source_cleanup_outcome(
                        summary,
                        cleanup_complete=False,
                        cleanup_failures=warning_failures,
                        retained_resources=warning_retained,
                    )
                per_source.append(summary)
                all_warnings.extend(summary.get("warnings") or [])
                cleanup = summary.get("cleanup") or {}
                for key in aggregate_cleanup:
                    aggregate_cleanup[key] += int(cleanup.get(key) or 0)
                if summary.get("pending_removal"):
                    pending_after_commit.append(
                        (ctx.selectable_id, int(summary["node_id"]))
                    )
                else:
                    deleted.append(ctx.selectable_id)
    except BackupSourceDeleteFailed as exc:
        _renew_unregister_execution_lease(
            task=unregister_task,
            owner_token=lease_owner_token,
        )
        _set_source_nas_removal_status(
            organization_id=org.id,
            ids=normalized,
            status=ResourceStatus.REMOVE_FAILED,
            message=exc.message,
        )
        _set_unregister_step(
            task=unregister_task,
            step_name=str(unregister_task.current_step or "reset_backup_config"),
            status=TaskStep.Status.FAILED,
            progress=max(1, int(unregister_task.progress or 0)),
            message=exc.message,
            level="ERROR",
            metadata={
                "reasons": [reason.as_dict() for reason in exc.reasons],
                "hint": exc.hint,
            },
        )
        _complete_unregister_task(
            task=unregister_task,
            status=Task.Status.FAILED,
            result_payload={
                "source_ids": normalized,
                "reasons": [reason.as_dict() for reason in exc.reasons],
            },
            error_code="SOURCE_UNREGISTER_FAILED",
            error_message=exc.message,
        )
        raise

    _set_unregister_step(
        task=unregister_task,
        step_name="reset_backup_config",
        status=TaskStep.Status.PENDING,
        progress=35,
        message="Backup configuration reset deferred until endpoint cleanup completes",
        metadata={"cleanup": aggregate_cleanup},
    )
    _set_unregister_step(
        task=unregister_task,
        step_name="cleanup_source_endpoint",
        status=TaskStep.Status.RUNNING,
        progress=70,
        message="Cleaning up source endpoints",
    )

    pending_removals: list[dict[str, Any]] = []
    if pending_after_commit:
        from apps.node.services.internal.node_lifecycle import (
            NodeLifecycleError,
            start_node_remove,
        )

        for selectable_id, node_id in pending_after_commit:
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
            node = Node.objects.filter(
                pk=node_id,
                organization_id=org.id,
                is_deleted=False,
            ).first()
            if node is None:
                deleted.append(selectable_id)
                continue
            existing_remove = (
                NodeTask.objects.filter(
                    organization_id=org.id,
                    node_id=node.id,
                    kind="agent.uninstall",
                    correlation_type="node.lifecycle",
                    correlation_id=f"remove:{node.id}",
                    status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
                )
                .order_by("-created_at", "-id")
                .first()
            )
            existing_payload = (
                existing_remove.payload
                if existing_remove is not None
                and isinstance(existing_remove.payload, dict)
                else {}
            )
            if (
                existing_remove is not None
                and int(existing_payload.get("source_unregister_task_id") or 0)
                == unregister_task.id
                and int(existing_payload.get("source_unregister_attempt") or 0)
                == int(unregister_task.retry_count or 0)
            ):
                pending_removals.append(
                    {
                        "source_id": selectable_id,
                        "node_id": node.id,
                        "task_id": str(existing_remove.id),
                        "operation_id": existing_remove.correlation_id,
                        "state": "removing",
                    }
                )
                continue
            try:
                removal = start_node_remove(
                    org=org,
                    node=node,
                    user=user,
                    force=force,
                    triggered_by_task_id=unregister_task.id,
                    triggered_by_task_attempt=int(unregister_task.retry_count or 0),
                )
                _renew_unregister_execution_lease(
                    task=unregister_task,
                    owner_token=lease_owner_token,
                )
                if (
                    removal.get("purged")
                    or removal.get("control_plane_purge_deferred")
                    or removal.get("state") == "removed"
                ):
                    deleted.append(selectable_id)
                    removal_cleanup = (
                        dict(removal.get("summary") or {})
                        if isinstance(removal.get("summary"), dict)
                        else {}
                    )
                    removal_failures = [
                        dict(item)
                        for item in removal.get("cleanup_failures") or []
                        if isinstance(item, dict)
                    ]
                    removal_retained = [
                        str(item)
                        for item in removal.get("retained_resources") or []
                        if str(item).strip()
                    ]
                    for source in per_source:
                        if source.get("source_id") != selectable_id:
                            continue
                        source["cleanup"] = _merge_cleanup_counts(
                            source.get("cleanup"),
                            removal_cleanup,
                        )
                        _merge_source_cleanup_outcome(
                            source,
                            cleanup_complete=bool(
                                removal.get("cleanup_complete", True)
                            ),
                            cleanup_failures=removal_failures,
                            retained_resources=removal_retained,
                        )
                        source.pop("pending_removal", None)
                        source.pop("node_id", None)
                        break
                    for key in aggregate_cleanup:
                        aggregate_cleanup[key] = max(
                            int(aggregate_cleanup.get(key) or 0),
                            int(removal_cleanup.get(key) or 0),
                        )
                    all_warnings.extend(
                        {
                            **failure,
                            "source_id": selectable_id,
                        }
                        for failure in removal_failures
                    )
                    continue
                pending_removals.append(
                    {
                        "source_id": selectable_id,
                        "node_id": node.id,
                        "task_id": removal.get("task_id"),
                        "operation_id": removal.get("operation_id"),
                        "state": removal.get("state") or "removing",
                    }
                )
            except Exception as exc:
                _renew_unregister_execution_lease(
                    task=unregister_task,
                    owner_token=lease_owner_token,
                )
                if isinstance(exc, NodeLifecycleError):
                    failure_code = getattr(exc, "code", "lifecycle_rejected")
                    failure_detail = str(exc)
                    logger.warning(
                        "backup source delete lifecycle dispatch failed "
                        "source=%s node=%s: %s",
                        selectable_id,
                        node_id,
                        exc,
                    )
                else:
                    failure_code = "agent_uninstall_dispatch_failed"
                    failure_detail = (
                        "Agent uninstall dispatch failed unexpectedly "
                        f"({exc.__class__.__name__})."
                    )
                    logger.exception(
                        "backup source delete lifecycle dispatch raised "
                        "source=%s node=%s",
                        selectable_id,
                        node_id,
                    )
                if isinstance(exc, NodeLifecycleError) or not force:
                    failure = BackupSourceDeleteFailed(
                        message="Agent uninstall could not be started.",
                        reasons=[
                            DeleteReason(
                                code=failure_code,
                                detail=failure_detail,
                                source_id=selectable_id,
                            )
                        ],
                    )
                    _set_unregister_step(
                        task=unregister_task,
                        step_name="cleanup_source_endpoint",
                        status=TaskStep.Status.FAILED,
                        progress=70,
                        message=failure.message,
                        level="ERROR",
                        metadata={
                            "reasons": [reason.as_dict() for reason in failure.reasons]
                        },
                    )
                    _complete_unregister_task(
                        task=unregister_task,
                        status=Task.Status.FAILED,
                        result_payload={
                            "source_ids": normalized,
                            "reasons": [reason.as_dict() for reason in failure.reasons],
                        },
                        error_code="AGENT_UNINSTALL_START_FAILED",
                        error_message=failure.message,
                    )
                    raise failure
                deleted.append(selectable_id)
                failure = {
                    "code": failure_code,
                    "detail": failure_detail,
                    "source_id": selectable_id,
                }
                all_warnings.append(failure)
                for source in per_source:
                    if source.get("source_id") != selectable_id:
                        continue
                    _merge_source_cleanup_outcome(
                        source,
                        cleanup_complete=False,
                        cleanup_failures=[failure],
                        retained_resources=["agent_installation"],
                    )
                    source.pop("pending_removal", None)
                    source.pop("node_id", None)
                    break

    if pending_removals:
        _set_unregister_step(
            task=unregister_task,
            step_name="cleanup_source_endpoint",
            status=TaskStep.Status.RUNNING,
            progress=75,
            message="Waiting for Agent uninstall completion",
            metadata={"pending_removals": pending_removals},
        )
        response = {
            "ok": True,
            "accepted": True,
            "result": "waiting",
            "deleted": deleted,
            "pending_removals": pending_removals,
            "warnings": all_warnings,
            "cleanup": aggregate_cleanup,
            "repository_cleanup_tasks": [
                item
                for cleanup_tasks in direct_cleanup_by_source.values()
                for item in cleanup_tasks
            ],
            "sources": per_source,
            "task_id": unregister_task.id,
            "task_uuid": str(unregister_task.task_uuid),
            "status": Task.Status.RUNNING,
        }
        return _save_source_unregister_checkpoint(
            task=unregister_task,
            response=_merge_unregister_checkpoint(
                cleanup_checkpoint,
                response,
            ),
        )

    _renew_unregister_execution_lease(
        task=unregister_task,
        owner_token=lease_owner_token,
    )
    try:
        return _complete_source_unregister_transaction(
            org=org,
            contexts=[ctx for ctx, _blob_stats, _warnings in prepared],
            force=force,
            user=user,
            unregister_task=unregister_task,
            cleanup_checkpoint=cleanup_checkpoint,
            deleted=deleted,
            all_warnings=all_warnings,
            aggregate_cleanup=aggregate_cleanup,
            direct_cleanup_by_source=direct_cleanup_by_source,
            snapshot_cleanup_by_source=snapshot_cleanup_by_source,
            per_source=per_source,
        )
    except BackupSourceDeleteFailed as exc:
        _set_source_nas_removal_status(
            organization_id=org.id,
            ids=normalized,
            status=ResourceStatus.REMOVE_FAILED,
            message=exc.message,
        )
        _set_unregister_step(
            task=unregister_task,
            step_name="finalize_source_unregister",
            status=TaskStep.Status.FAILED,
            progress=max(1, int(unregister_task.progress or 0)),
            message=exc.message,
            level="ERROR",
            metadata={
                "reasons": [reason.as_dict() for reason in exc.reasons],
                "hint": exc.hint,
            },
        )
        _complete_unregister_task(
            task=unregister_task,
            status=Task.Status.FAILED,
            result_payload={
                "source_ids": normalized,
                "reasons": [reason.as_dict() for reason in exc.reasons],
            },
            error_code="SOURCE_UNREGISTER_FAILED",
            error_message=exc.message,
        )
        raise


def queue_delete_backup_sources(
    *,
    org: Organization,
    ids: list[str],
    force: bool = False,
    user=None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Create durable source-unregister tasks and queue those ready to run."""
    normalized = _normalize_delete_ids(ids)
    user_id = getattr(user, "id", None)
    accepted_pairs: list[tuple[str, Task]] = []
    rejected: list[dict[str, Any]] = []
    ready_task_ids: set[int] = set()
    ready_source_ids: list[str] = []
    operation_group_uuid = uuid4()
    clean_idempotency_key = idempotency_key.strip()
    with transaction.atomic():
        _lock_delete_identities(organization_id=org.id, ids=normalized)
        idempotency_keys = {
            selectable_id: f"{clean_idempotency_key}:{selectable_id}"
            for selectable_id in normalized
            if clean_idempotency_key
        }
        idempotent_tasks = {
            str(task.idempotency_key): task
            for task in Task.objects.filter(
                organization_id=org.id,
                idempotency_key__in=idempotency_keys.values(),
                task_type=Task.Type.SOURCE_UNREGISTER,
            )
        }
        existing_group_uuid = next(
            (
                task.group_uuid
                for task in idempotent_tasks.values()
                if task.group_uuid is not None
            ),
            None,
        )
        if existing_group_uuid is not None:
            operation_group_uuid = existing_group_uuid
        for selectable_id in normalized:
            task_idempotency_key = idempotency_keys.get(selectable_id)
            if task_idempotency_key:
                idempotent_task = idempotent_tasks.get(task_idempotency_key)
                if idempotent_task is not None:
                    accepted_pairs.append((selectable_id, idempotent_task))
                    continue

            ctx = _resolve_context(
                organization_id=org.id,
                selectable_id=selectable_id,
            )
            if ctx is None:
                reason = DeleteReason(
                    code="source_not_found",
                    detail="Backup source was not found.",
                    source_id=selectable_id,
                )
                rejected.append(
                    {"source_id": selectable_id, "reasons": [reason.as_dict()]}
                )
                continue
            existing = _active_unregister_task_for_source(
                organization_id=org.id,
                source_type=ctx.source_type,
                source_ref_id=ctx.source_ref_id,
            )
            if existing is not None:
                if _is_legacy_deferred_unregister(existing):
                    _terminalize_legacy_deferred_unregister(existing)
                else:
                    accepted_pairs.append((selectable_id, existing))
                    continue

            decision = evaluate_source_deregistration(
                org=org,
                selectable_id=selectable_id,
                force=force,
            )
            if decision.disposition == "invalid":
                rejected.append(
                    {
                        "source_id": selectable_id,
                        "reasons": [reason.as_dict() for reason in decision.reasons],
                    }
                )
                continue

            unregister_task = _create_source_unregister_task(
                org=org,
                selectable_id=selectable_id,
                force=force,
                group_uuid=operation_group_uuid,
                idempotency_key=task_idempotency_key,
            )
            payload = dict(unregister_task.request_payload or {})
            if user_id:
                payload["user_id"] = int(user_id)
            unregister_task.request_payload = payload
            unregister_task.save(update_fields=["request_payload", "updated_at"])
            if decision.disposition in {"waiting", "blocked"}:
                unregister_task = _fail_unregister_before_start(
                    task=unregister_task,
                    reasons=list(decision.reasons),
                )
            else:
                unregister_task = start_task(
                    task_uuid=unregister_task.task_uuid,
                    organization_id=org.id,
                )
                _set_unregister_step(
                    task=unregister_task,
                    step_name="prepare_source_unregister",
                    status=TaskStep.Status.SUCCESS,
                    progress=15,
                    message="Source deregistration prepared",
                    metadata={"source_ids": [selectable_id], "force": bool(force)},
                )
                ready_task_ids.add(int(unregister_task.id))
                ready_source_ids.append(selectable_id)
            accepted_pairs.append((selectable_id, unregister_task))

        if ready_source_ids:
            _set_source_nas_removal_status(
                organization_id=org.id,
                ids=ready_source_ids,
                status=ResourceStatus.REMOVING,
                message="Source deregistration is in progress.",
            )

        if not accepted_pairs:
            reasons = [
                DeleteReason(
                    code=str(reason.get("code") or "invalid_source"),
                    detail=str(
                        reason.get("detail") or "Backup source was not accepted."
                    ),
                    source_id=str(item.get("source_id") or ""),
                )
                for item in rejected
                for reason in item.get("reasons") or []
            ]
            raise BackupSourceDeleteFailed(
                message="No backup source deregistration was accepted.",
                reasons=reasons,
            )

    from apps.source.tasks.source_unregister import execute_source_unregister_task

    def _dispatch(task_id: int) -> None:
        execute_source_unregister_task.delay(task_id=task_id)

    from django.conf import settings

    unregister_tasks = [task for _source_id, task in accepted_pairs]
    ready_tasks = [task for task in unregister_tasks if task.id in ready_task_ids]
    if getattr(settings, "SOURCE_UNREGISTER_EAGER", False) and ready_tasks:
        for task in ready_tasks:
            run_source_unregister_task(
                organization_id=org.id, task_uuid=str(task.task_uuid)
            )
    else:
        for task in ready_tasks:
            transaction.on_commit(lambda task_id=task.id: _dispatch(task_id))

    refreshed_tasks = Task.objects.in_bulk({task.id for task in unregister_tasks})
    accepted_pairs = [
        (source_id, refreshed_tasks.get(task.id, task))
        for source_id, task in accepted_pairs
    ]
    unregister_tasks = [task for _source_id, task in accepted_pairs]
    response_payloads = [
        payload
        for task in unregister_tasks
        if isinstance((payload := task.result_payload), dict)
    ]
    deleted = [
        item for payload in response_payloads for item in payload.get("deleted", [])
    ]
    pending_removals = [
        item
        for payload in response_payloads
        for item in payload.get("pending_removals", [])
    ]
    warnings = [
        item for payload in response_payloads for item in payload.get("warnings", [])
    ]
    sources = [
        item for payload in response_payloads for item in payload.get("sources", [])
    ]

    statuses = {task.status for task in unregister_tasks}
    if Task.Status.BLOCKED in statuses:
        aggregate_status = Task.Status.BLOCKED
    elif Task.Status.WAITING in statuses:
        aggregate_status = Task.Status.WAITING
    elif statuses & {Task.Status.PENDING, Task.Status.RUNNING}:
        aggregate_status = Task.Status.RUNNING
    elif statuses & {Task.Status.FAILED, Task.Status.TIMEOUT}:
        aggregate_status = Task.Status.FAILED
    elif Task.Status.CANCELLED in statuses:
        aggregate_status = Task.Status.CANCELLED
    else:
        aggregate_status = Task.Status.SUCCESS

    accepted = aggregate_status in {
        Task.Status.BLOCKED,
        Task.Status.WAITING,
        Task.Status.RUNNING,
    }
    completed_with_warnings = any(
        str(payload.get("result") or "").strip().lower() == "partial_success"
        or payload.get("cleanup_complete") is False
        for payload in response_payloads
    )
    if accepted:
        result = "pending"
    elif aggregate_status in {Task.Status.FAILED, Task.Status.CANCELLED}:
        has_success = Task.Status.SUCCESS in statuses
        result = "partial_failure" if has_success else "failed"
    else:
        result = (
            "partial_success" if rejected or completed_with_warnings else "completed"
        )

    first_task = unregister_tasks[0]
    task_group_uuids = {task.group_uuid for task in unregister_tasks}
    response_group_uuid = (
        next(iter(task_group_uuids))
        if len(task_group_uuids) == 1 and None not in task_group_uuids
        else None
    )

    return {
        "ok": True,
        "accepted": accepted,
        "result": result,
        "deleted": deleted,
        "pending_removals": pending_removals,
        "warnings": warnings,
        "cleanup": {},
        "sources": sources,
        "task_id": first_task.id,
        "task_uuid": str(first_task.task_uuid),
        "task_ids": [task.id for task in unregister_tasks],
        "task_uuids": [str(task.task_uuid) for task in unregister_tasks],
        "group_uuid": str(response_group_uuid) if response_group_uuid else None,
        "tasks": [
            {
                "source_id": selectable_id,
                "task_id": task.id,
                "task_uuid": str(task.task_uuid),
                "status": task.status,
                "group_uuid": str(task.group_uuid) if task.group_uuid else None,
            }
            for selectable_id, task in accepted_pairs
        ],
        "rejected": rejected,
        "status": aggregate_status,
        "source_ids": [source_id for source_id, _task in accepted_pairs],
        "requested_source_ids": normalized,
    }


def run_source_unregister_task(
    *,
    organization_id: int,
    task_uuid: str,
    lease_owner_token: str = "",
) -> dict[str, Any]:
    task = Task.objects.filter(
        organization_id=organization_id, task_uuid=task_uuid
    ).first()
    if task is None:
        raise Task.DoesNotExist
    if _is_legacy_deferred_unregister(task):
        legacy_result = end_legacy_deferred_source_unregister_task(task_id=int(task.id))
        if legacy_result.get("legacy_deferred_ended"):
            task.refresh_from_db()
            return task.result_payload if isinstance(task.result_payload, dict) else {}
        task.refresh_from_db()
    if task.status in _UNREGISTER_TERMINAL:
        return task.result_payload if isinstance(task.result_payload, dict) else {}

    org = Organization.objects.filter(pk=organization_id).first()
    if org is None:
        raise Task.DoesNotExist

    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    normalized = [
        str(value).strip()
        for value in payload.get("source_ids") or []
        if str(value).strip()
    ]
    force = bool(payload.get("force"))
    user = _resolve_unregister_user(org=org, task=task)
    try:
        if not normalized:
            raise BackupSourceDeleteFailed(
                message="Backup source was not deleted.",
                reasons=[
                    DeleteReason(code="empty_ids", detail="ids must not be empty.")
                ],
            )
        prepared = _prepare_delete_batch(
            org=org,
            ids=normalized,
            force=force,
            executing_task_uuid=str(task.task_uuid),
        )
    except BackupSourceDeleteFailed as exc:
        _set_source_nas_removal_status(
            organization_id=organization_id,
            ids=normalized,
            status=ResourceStatus.REMOVE_FAILED,
            message=exc.message,
        )
        _set_unregister_step(
            task=task,
            step_name=str(task.current_step or "prepare_source_unregister"),
            status=TaskStep.Status.FAILED,
            progress=max(1, int(task.progress or 0)),
            message=exc.message,
            level="ERROR",
            metadata={
                "reasons": [reason.as_dict() for reason in exc.reasons],
                "hint": exc.hint,
            },
        )
        _complete_unregister_task(
            task=task,
            status=Task.Status.FAILED,
            result_payload={
                "source_ids": normalized,
                "reasons": [reason.as_dict() for reason in exc.reasons],
            },
            error_code="SOURCE_UNREGISTER_PREFLIGHT_FAILED",
            error_message=exc.message,
        )
        raise
    return _execute_source_unregister_work(
        org=org,
        prepared=prepared,
        force=force,
        user=user,
        unregister_task=task,
        lease_owner_token=lease_owner_token,
    )


@transaction.atomic
def fail_source_unregister_task_unexpectedly(
    *,
    task_id: int,
    exc: BaseException,
) -> bool:
    """Best-effort terminalization after Celery exhausts automatic retries."""
    task = Task.objects.select_for_update().filter(pk=int(task_id)).first()
    if task is None or task.status in _UNREGISTER_TERMINAL:
        return False

    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    source_ids = [
        str(value).strip()
        for value in payload.get("source_ids") or []
        if str(value).strip()
    ]
    detail = (
        "Source deregistration failed after automatic retries due to an unexpected "
        f"control-plane error ({exc.__class__.__name__})."
    )
    reason = {
        "code": "source_unregister_unexpected_failure",
        "detail": detail,
    }
    checkpoint = _source_unregister_checkpoint(task)
    result_payload = {
        **checkpoint,
        "ok": False,
        "accepted": False,
        "result": "failed",
        "source_ids": source_ids,
        "reasons": [reason],
    }

    _set_source_nas_removal_status(
        organization_id=int(task.organization_id),
        ids=source_ids,
        status=ResourceStatus.REMOVE_FAILED,
        message=detail,
    )
    _set_unregister_step(
        task=task,
        step_name=str(task.current_step or "finalize_source_unregister"),
        status=TaskStep.Status.FAILED,
        progress=max(1, int(task.progress or 0)),
        message=detail,
        level="ERROR",
        metadata={"reasons": [reason]},
    )
    _complete_unregister_task(
        task=task,
        status=Task.Status.FAILED,
        result_payload=result_payload,
        error_code="SOURCE_UNREGISTER_UNEXPECTED_FAILURE",
        error_message=detail,
    )
    return True


def preflight_delete_backup_sources(
    *,
    organization_id: int,
    ids: list[str],
    force: bool = False,
) -> dict[str, Any]:
    """Return the authoritative eligibility decision plus non-blocking risks."""
    risks: list[dict[str, Any]] = []
    for selectable_id in ids:
        ctx = _resolve_context(
            organization_id=organization_id, selectable_id=selectable_id
        )
        if ctx is None:
            continue
        if ctx.nas_resource is not None:
            resource = ctx.nas_resource
            config_ids = list(
                BackupConfig.objects.filter(
                    organization_id=organization_id,
                    source_type=ctx.source_type,
                    source_ref_id=ctx.source_ref_id,
                ).values_list("id", flat=True)
            )
            if config_ids and resource.bound_node_id is None:
                risks.append(
                    DeleteReason(
                        code="proxy_unbound",
                        detail=(
                            f'NAS source "{ctx.display_name}" has no bound Proxy. '
                            "Strict deregistration may fail if repository cleanup is required."
                        ),
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                    ).as_dict()
                )
            elif (
                resource.bound_node_id is None
                and str(resource.status_message or "").strip().lower() == "needs_proxy"
            ):
                risks.append(
                    DeleteReason(
                        code="proxy_unbound",
                        detail=(
                            f'NAS source "{ctx.display_name}" requires a Proxy but none is bound. '
                            "Strict deregistration may fail."
                        ),
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                    ).as_dict()
                )
        for risk in _repository_unreachable_preflight_risks(
            organization_id=organization_id,
            ctx=ctx,
        ):
            risks.append(risk.as_dict())
    org = Organization.objects.filter(pk=organization_id).first()
    decisions = (
        [
            evaluate_source_deregistration(
                org=org,
                selectable_id=selectable_id,
                force=force,
            )
            for selectable_id in ids
        ]
        if org is not None
        else []
    )
    blocking = [
        reason.as_dict()
        for decision in decisions
        if decision.disposition != "ready"
        for reason in decision.reasons
    ]
    decision_codes = {str(reason.get("code") or "") for reason in blocking}
    risks = [
        risk for risk in risks if str(risk.get("code") or "") not in decision_codes
    ]
    return {
        "risks": risks,
        "blocking": blocking,
        "strict_may_fail": bool(risks or blocking),
        "delete_disabled": bool(blocking),
    }


def _repository_unreachable_preflight_risks(
    *,
    organization_id: int,
    ctx: SourceDeleteContext,
) -> list[DeleteReason]:
    """Warn only when a linked repository is offline — snapshot cleanup is automatic on delete."""
    config_ids = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        ).values_list("id", flat=True)
    )
    if not config_ids:
        return []
    snapshots = list(
        BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        ).exclude(status=BackupSourceSnapshot.Status.DELETED)
    )
    if not snapshots:
        return []
    risks: list[DeleteReason] = []
    seen_repos: set[int] = set()
    for snapshot in snapshots:
        repo_id = int(snapshot.repository_id)
        if repo_id in seen_repos:
            continue
        seen_repos.add(repo_id)
        repo = Repository.objects.filter(
            organization_id=organization_id,
            id=repo_id,
        ).first()
        if repo is None or repo.health != Repository.Health.OFFLINE:
            continue
        repo_name = str(repo.name or repo_id)
        risks.append(
            DeleteReason(
                code="repository_unreachable",
                detail=(
                    f'Target repository "{repo_name}" is offline. Strict delete may fail; '
                    "use Force Cleanup to remove the source and record cleanup residue."
                ),
                source_id=ctx.selectable_id,
                source_name=ctx.display_name,
                repository_id=repo_id,
                repository_name=repo_name,
            )
        )
    return risks


def _snapshot_delete_error_detail(task: Task, result: dict[str, Any]) -> str:
    parts: list[str] = []
    last_error = str(getattr(task, "last_error", "") or "").strip()
    if last_error:
        parts.append(last_error)
    item_results = (
        result.get("results") if isinstance(result.get("results"), list) else []
    )
    for item in item_results:
        if not isinstance(item, dict) or str(item.get("status") or "") != "failed":
            continue
        snapshot_id = str(item.get("kopia_snapshot_id") or "").strip()
        item_error = str(item.get("error_message") or "").strip()
        if snapshot_id and item_error:
            parts.append(f"{snapshot_id}: {item_error}")
        elif item_error:
            parts.append(item_error)
    task_uuid = str(getattr(task, "task_uuid", "") or "").strip()
    if task_uuid:
        parts.append(f"(task {task_uuid})")
    if parts:
        return " ".join(parts)[:2000]
    return "One or more physical snapshots failed to delete."


def _snapshot_delete_strict(
    *,
    source_snapshot: BackupSourceSnapshot,
) -> tuple[bool, str | None]:
    if source_snapshot.status == BackupSourceSnapshot.Status.DELETED:
        return True, None
    task = create_snapshot_delete_task(
        source_snapshot=source_snapshot,
        trigger_type=Task.TriggerType.SYSTEM,
    )
    result = run_snapshot_delete_task(
        organization_id=source_snapshot.organization_id,
        task_uuid=str(task.task_uuid),
        source_snapshot_id=source_snapshot.id,
    )
    task.refresh_from_db()
    failed_count = int(result.get("failed_count") or 0)
    if failed_count > 0:
        return False, _snapshot_delete_error_detail(
            task, result if isinstance(result, dict) else {}
        )
    source_snapshot.refresh_from_db()
    if source_snapshot.status != BackupSourceSnapshot.Status.DELETED:
        return False, _snapshot_delete_error_detail(
            task, result if isinstance(result, dict) else {}
        ) or ("Snapshot delete did not finalize.")
    return True, None


def _snapshot_delete_for_unregister(
    *,
    source_snapshot: BackupSourceSnapshot,
    unregister_task: Task,
) -> tuple[bool | None, str | None, dict[str, Any]]:
    """Return terminal cleanup state or asynchronously wait on one child task."""
    attempt = int(unregister_task.retry_count or 0)
    task = (
        Task.objects.filter(
            organization_id=source_snapshot.organization_id,
            task_type=Task.Type.SNAPSHOT_DELETE,
            request_payload__source_snapshot_id=source_snapshot.id,
            request_payload__source_unregister_task_id=unregister_task.id,
            request_payload__source_unregister_attempt=attempt,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if task is None:
        task = create_and_queue_snapshot_delete_task(
            source_snapshot=source_snapshot,
            trigger_type=Task.TriggerType.SYSTEM,
            source_unregister_task=unregister_task,
        )
    task.refresh_from_db()
    payload = {
        "id": task.id,
        "task_id": task.id,
        "task_uuid": str(task.task_uuid),
        "source_snapshot_id": source_snapshot.id,
        "repository_id": source_snapshot.repository_id,
        "status": task.status,
        "error_code": task.error_code,
        "error_message": task.error_message,
    }
    if task.status in _ACTIVE_TASK_STATUSES:
        return None, None, payload
    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    if task.status != Task.Status.SUCCESS:
        return False, _snapshot_delete_error_detail(task, result), payload
    source_snapshot.refresh_from_db()
    if source_snapshot.status != BackupSourceSnapshot.Status.DELETED:
        return False, "Snapshot delete did not finalize.", payload
    return True, None, payload


def _repository_purge_idempotency_key(
    *,
    organization_id: int,
    source_kind: str,
    source_ref_id: int,
    repository_id: int,
    snapshot_ids: list[int],
) -> str:
    identity = ":".join(
        [
            str(organization_id),
            source_kind,
            str(source_ref_id),
            str(repository_id),
            ",".join(str(value) for value in snapshot_ids),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _normalize_pending_snapshot_ids(values: Any) -> list[int]:
    if not isinstance(values, (list, tuple, set)):
        return []
    normalized: set[int] = set()
    for value in values:
        try:
            snapshot_id = int(value)
        except (TypeError, ValueError):
            continue
        if snapshot_id > 0:
            normalized.add(snapshot_id)
    return sorted(normalized)


@transaction.atomic
def _enqueue_repository_purge_pending(
    *,
    organization_id: int,
    ctx: SourceDeleteContext,
    repository_id: int,
    snapshot_ids: list[int],
    kopia_snapshot_ids: list[str],
    error: str,
) -> int:
    normalized_snapshot_ids = _normalize_pending_snapshot_ids(snapshot_ids)
    idempotency_key = _repository_purge_idempotency_key(
        organization_id=organization_id,
        source_kind=ctx.source_kind,
        source_ref_id=ctx.source_ref_id,
        repository_id=repository_id,
        snapshot_ids=normalized_snapshot_ids,
    )
    # The repository may already be missing, so serialize new workers on the
    # Source identity that is retained until unregister finalization.
    if ctx.agent_node is not None:
        Node.objects.select_for_update().filter(
            organization_id=organization_id,
            id=ctx.agent_node.id,
            is_deleted=False,
        ).first()
    elif ctx.nas_resource is not None:
        SourceResource.all_objects.select_for_update().filter(
            organization_id=organization_id,
            id=ctx.nas_resource.id,
            is_deleted=False,
        ).first()
    canonical = (
        BackupSourceRepositoryPurgePending.objects.select_for_update()
        .filter(idempotency_key=idempotency_key)
        .first()
    )
    legacy_rows = list(
        BackupSourceRepositoryPurgePending.objects.select_for_update().filter(
            organization_id=organization_id,
            source_kind=ctx.source_kind,
            source_ref_id=ctx.source_ref_id,
            repository_id=repository_id,
            idempotency_key__isnull=True,
        )
    )
    matching_legacy: list[BackupSourceRepositoryPurgePending] = []
    for legacy in legacy_rows:
        payload = legacy.payload if isinstance(legacy.payload, dict) else {}
        legacy_snapshot_ids = _normalize_pending_snapshot_ids(
            payload.get("source_snapshot_ids")
        )
        if legacy_snapshot_ids == normalized_snapshot_ids:
            matching_legacy.append(legacy)

    if canonical is None and matching_legacy:
        canonical = matching_legacy.pop(0)
        canonical.idempotency_key = idempotency_key

    merged_kopia_snapshot_ids = list(
        dict.fromkeys(
            str(value)
            for row in [canonical, *matching_legacy]
            if row is not None
            for value in (
                row.payload.get("kopia_snapshot_ids", [])
                if isinstance(row.payload, dict)
                else []
            )
            if str(value).strip()
        )
    )
    merged_kopia_snapshot_ids = list(
        dict.fromkeys(
            [
                *merged_kopia_snapshot_ids,
                *(str(value) for value in kopia_snapshot_ids if str(value).strip()),
            ]
        )
    )
    payload = {
        "source_snapshot_ids": normalized_snapshot_ids,
        "kopia_snapshot_ids": merged_kopia_snapshot_ids,
        "error": error[:2000],
    }

    if canonical is None:
        canonical = BackupSourceRepositoryPurgePending.objects.create(
            organization_id=organization_id,
            source_kind=ctx.source_kind,
            source_ref_id=ctx.source_ref_id,
            repository_id=repository_id,
            idempotency_key=idempotency_key,
            payload=payload,
            last_error=error[:2000],
        )
    else:
        canonical.payload = payload
        canonical.retry_count = max(
            [
                int(canonical.retry_count or 0),
                *(int(row.retry_count or 0) for row in matching_legacy),
            ]
        )
        canonical.last_error = error[:2000]
        canonical.save(
            update_fields=[
                "idempotency_key",
                "payload",
                "retry_count",
                "last_error",
                "updated_at",
            ]
        )

    if matching_legacy:
        BackupSourceRepositoryPurgePending.objects.filter(
            id__in=[row.id for row in matching_legacy]
        ).delete()
    return int(canonical.id)


def _delete_repository_snapshots(
    *,
    organization_id: int,
    ctx: SourceDeleteContext,
    force: bool,
    reasons: list[DeleteReason],
    warnings: list[DeleteWarning],
    skip_repository_ids: set[int] | None = None,
    unregister_task: Task | None = None,
    lease_owner_token: str = "",
) -> dict[str, Any]:
    configs = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=ctx.source_type,
            source_ref_id=ctx.source_ref_id,
        ).values_list("id", flat=True)
    )
    if not configs:
        return {
            "snapshots_purged": 0,
            "repository_blobs_deleted": 0,
            "repository_purge_pending": 0,
        }

    snapshots_queryset = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        backup_config_id__in=configs,
    )
    cleanup_plan = (
        unregister_task.request_payload.get("cleanup_plan")
        if unregister_task is not None
        and isinstance(unregister_task.request_payload, dict)
        and isinstance(unregister_task.request_payload.get("cleanup_plan"), dict)
        else None
    )
    if cleanup_plan is not None and isinstance(cleanup_plan.get("snapshot_ids"), list):
        planned_snapshot_ids = _normalize_pending_snapshot_ids(
            cleanup_plan.get("snapshot_ids")
        )
        snapshots_queryset = snapshots_queryset.filter(id__in=planned_snapshot_ids)
    else:
        snapshots_queryset = snapshots_queryset.exclude(
            status=BackupSourceSnapshot.Status.DELETED
        )
    snapshots = list(snapshots_queryset.order_by("id"))
    blobs_deleted = 0
    pending_count = 0
    snapshot_cleanup_tasks: list[dict[str, Any]] = []
    snapshot_cleanup_waiting = False
    skipped_repositories = {int(value) for value in (skip_repository_ids or set())}
    for snapshot in snapshots:
        if snapshot.status == BackupSourceSnapshot.Status.DELETED:
            blobs_deleted += 1
            continue
        if unregister_task is not None:
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
        repo = Repository.objects.filter(
            organization_id=organization_id,
            id=snapshot.repository_id,
        ).first()
        repo_name = str(repo.name if repo else snapshot.repository_id)
        if int(snapshot.repository_id) in skipped_repositories:
            blobs_deleted += 1
            continue
        try:
            if unregister_task is not None and lease_owner_token:
                ok, err, child_payload = _snapshot_delete_for_unregister(
                    source_snapshot=snapshot,
                    unregister_task=unregister_task,
                )
                snapshot_cleanup_tasks.append(child_payload)
                if ok is None:
                    snapshot_cleanup_waiting = True
                    continue
            else:
                ok, err = _snapshot_delete_strict(source_snapshot=snapshot)
        except Exception as exc:
            logger.exception(
                "Backup source snapshot cleanup raised "
                "source=%s snapshot=%s repository=%s",
                ctx.selectable_id,
                snapshot.id,
                snapshot.repository_id,
            )
            ok = False
            err = (
                "Snapshot delete execution failed unexpectedly "
                f"({exc.__class__.__name__})."
            )
        if unregister_task is not None:
            _renew_unregister_execution_lease(
                task=unregister_task,
                owner_token=lease_owner_token,
            )
        if ok:
            blobs_deleted += 1
            continue
        detail = err or "Repository snapshot delete failed."
        if not force:
            reasons.append(
                DeleteReason(
                    code="repository_snapshot_delete_failed",
                    detail=detail,
                    source_id=ctx.selectable_id,
                    source_name=ctx.display_name,
                    repository_id=int(snapshot.repository_id),
                    repository_name=repo_name,
                )
            )
            continue
        rows = BackupSourceSnapshotDirectory.objects.filter(source_snapshot=snapshot)
        kopia_ids = [
            snapshot_id
            for row in rows
            if (snapshot_id := str(row.kopia_snapshot_id or "").strip())
        ]
        pending_id = _enqueue_repository_purge_pending(
            organization_id=organization_id,
            ctx=ctx,
            repository_id=int(snapshot.repository_id),
            snapshot_ids=[snapshot.id],
            kopia_snapshot_ids=kopia_ids,
            error=detail,
        )
        pending_count += 1
        warnings.append(
            DeleteWarning(
                code="repository_cleanup_required",
                detail=(
                    f'Backup data could not be removed from repository "{repo_name}" '
                    f"(ID {snapshot.repository_id}). "
                    "Manual cleanup is required because the source endpoint is no longer available."
                ),
                source_id=ctx.selectable_id,
                source_name=ctx.display_name,
                retained_resources=(f"repository_cleanup_record:{pending_id}",),
            )
        )
    result: dict[str, Any] = {
        "snapshots_purged": blobs_deleted,
        "repository_blobs_deleted": blobs_deleted,
        "repository_purge_pending": pending_count,
    }
    if unregister_task is not None and lease_owner_token:
        result["_snapshot_cleanup_waiting"] = snapshot_cleanup_waiting
        result["_snapshot_cleanup_tasks"] = snapshot_cleanup_tasks
    return result


def _purge_protection_db(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> dict[str, int]:
    configs = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        ).values_list("id", flat=True)
    )
    config_ids = configs
    if not config_ids:
        RestoreRecord.objects.filter(
            organization_id=organization_id,
            source_type=source_type
            if source_type != "agent"
            else RestoreRecord.EndpointType.AGENT,
            source_ref_id=source_ref_id,
        ).delete()
        return {
            "backup_configs_removed": 0,
            "snapshots_removed": 0,
            "restore_plans_removed": 0,
            "restore_records_removed": 0,
        }

    endpoint = (
        RestoreRecord.EndpointType.AGENT
        if source_type == "agent"
        else RestoreRecord.EndpointType.NAS
    )
    restore_records_removed = RestoreRecord.objects.filter(
        organization_id=organization_id,
        source_type=endpoint,
        source_ref_id=source_ref_id,
    ).delete()[0]
    restore_plans_removed = RestorePlan.objects.filter(
        organization_id=organization_id,
        backup_config_id__in=config_ids,
    ).delete()[0]
    snapshots_removed = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        backup_config_id__in=config_ids,
    ).delete()[0]
    backup_configs_removed = BackupConfig.objects.filter(id__in=config_ids).delete()[0]
    return {
        "backup_configs_removed": backup_configs_removed,
        "snapshots_removed": snapshots_removed,
        "restore_plans_removed": restore_plans_removed,
        "restore_records_removed": restore_records_removed,
    }


def _cleanup_download_artifacts(*, organization_id: int, config_ids: list[int]) -> int:
    if not config_ids:
        return 0
    snapshot_dir_ids = BackupSourceSnapshotDirectory.objects.filter(
        organization_id=organization_id,
        backup_config_id__in=config_ids,
    ).values_list("id", flat=True)
    if not snapshot_dir_ids:
        return 0
    artifacts = SnapshotDownloadArtifact.objects.filter(
        organization_id=organization_id,
        source_snapshot_directory_id__in=list(snapshot_dir_ids),
    )
    count = artifacts.count()
    for artifact in artifacts:
        task = artifact.task
        artifact.delete()
        if task is not None:
            task.delete()
    return count


def _nas_unmount_retained_warning(
    *,
    ctx: SourceDeleteContext,
    result: dict[str, Any],
) -> DeleteWarning | None:
    """Normalize Agent NAS residue into the source unregister result."""
    resource = ctx.nas_resource
    if resource is None or bool(result.get("cleanup_complete", True)):
        return None

    detail = "NAS mount was detached with retained local references."
    raw_warnings = result.get("warnings")
    if isinstance(raw_warnings, list) and raw_warnings:
        detail = str(raw_warnings[0] or detail)
    retained_resources = []
    for retained in result.get("retained_resources") or []:
        retained_name = str(retained or "").strip()
        if retained_name == "nas_mount_reference":
            retained_name = f"source_nas_mount:{resource.id}"
        elif retained_name == "nas_mount_directory":
            retained_name = f"source_nas_mount_directory:{resource.id}"
        elif retained_name:
            retained_name = f"source_nas:{resource.id}:{retained_name}"
        if retained_name:
            retained_resources.append(retained_name)
    return DeleteWarning(
        code="nas_umount_retained",
        detail=detail,
        source_id=ctx.selectable_id,
        source_name=ctx.display_name,
        retained_resources=tuple(
            retained_resources or [f"source_nas_mount:{resource.id}"]
        ),
    )


def _recent_nas_unmount_residue_warnings(
    *,
    ctx: SourceDeleteContext,
    unregister_task: Task,
) -> list[DeleteWarning]:
    """Return residue reported by concurrent compensating unmount tasks."""
    resource = ctx.nas_resource
    if resource is None or resource.bound_node_id is None:
        return []
    results = (
        NodeTask.objects.filter(
            organization_id=resource.organization_id,
            node_id=resource.bound_node_id,
            kind="nas.unmount",
            correlation_type="source.unmount",
            correlation_id=str(resource.id),
            status=NodeTask.Status.SUCCESS,
            created_at__gte=unregister_task.created_at,
        )
        .order_by("created_at", "id")
        .values_list("result", flat=True)
    )
    warnings: list[DeleteWarning] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        warning = _nas_unmount_retained_warning(ctx=ctx, result=result)
        if warning is not None:
            warnings.append(warning)
    return warnings


def _strict_nas_umount(
    *,
    ctx: SourceDeleteContext,
    force: bool,
    reasons: list[DeleteReason],
    warnings: list[DeleteWarning],
    unregister_task: Task,
) -> dict[str, Any]:
    resource = ctx.nas_resource
    if resource is None:
        return {"skipped": True}
    try:
        result = unmount_resource(resource=resource, force=force)
    except Exception as exc:
        logger.exception(
            "Backup source NAS unmount raised source=%s resource=%s",
            ctx.selectable_id,
            resource.id,
        )
        result = {
            "success": False,
            "message": (f"NAS unmount failed unexpectedly ({exc.__class__.__name__})."),
        }
    if result.get("success"):
        warning = _nas_unmount_retained_warning(ctx=ctx, result=result)
        if warning is not None:
            if not force:
                reasons.append(
                    DeleteReason(
                        code=warning.code,
                        detail=warning.detail,
                        source_id=warning.source_id,
                        source_name=warning.source_name,
                    )
                )
                return {"failed": True}
            if warning not in warnings:
                warnings.append(warning)
        if force:
            for residue_warning in _recent_nas_unmount_residue_warnings(
                ctx=ctx,
                unregister_task=unregister_task,
            ):
                if residue_warning not in warnings:
                    warnings.append(residue_warning)
        return {"success": True}
    message = str(result.get("message") or "NAS unmount failed.")
    failure_code = (
        "mount_directory_cleanup_failed"
        if "cleanup mount directory" in message.lower()
        else "nas_umount_failed"
    )
    if force:
        warnings.append(
            DeleteWarning(
                code=failure_code,
                detail=f"{message} Check the proxy host manually.",
                source_id=ctx.selectable_id,
                source_name=ctx.display_name,
            )
        )
        return {"skipped": True}
    reasons.append(
        DeleteReason(
            code=failure_code,
            detail=message,
            source_id=ctx.selectable_id,
            source_name=ctx.display_name,
        )
    )
    return {"failed": True}


def _mark_tasks_orphaned(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    source_name: str,
) -> int:
    return _mark_task_payload_flag(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        flag_at="source_orphaned_at",
        reason_key="source_orphaned_reason",
        flag_reason="source_removed",
        extra={"source_orphan_display_name": source_name},
        skip_if="source_orphaned_at",
    )


def _mark_tasks_reconfigured(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> int:
    return _mark_task_payload_flag(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        flag_at="source_reconfigured_at",
        reason_key="source_reconfigured_reason",
        flag_reason="revert_to_configuration",
        skip_if="source_reconfigured_at",
    )


def _mark_task_payload_flag(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    flag_at: str,
    reason_key: str,
    flag_reason: str,
    skip_if: str,
    extra: dict[str, str] | None = None,
) -> int:
    tasks = (
        Task.objects.filter(
            organization_id=organization_id,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_id=source_ref_id,
        )
        .filter(_task_resources_join_subtype_q(source_type))
        .distinct()
    )
    now_iso = timezone.now().isoformat()
    updated = 0
    for task in tasks:
        payload = (
            dict(task.request_payload) if isinstance(task.request_payload, dict) else {}
        )
        if payload.get(skip_if):
            continue
        payload[flag_at] = now_iso
        payload[reason_key] = flag_reason
        if extra:
            payload.update(extra)
        task.request_payload = payload
        task.save(update_fields=["request_payload", "updated_at"])
        updated += 1
    return updated


def _soft_delete_identity(
    *,
    org: Organization,
    ctx: SourceDeleteContext,
    user,
) -> None:
    if ctx.is_agent and ctx.agent_node is not None:
        node = ctx.agent_node
        for resource in SourceResource.objects.filter(
            organization_id=org.id,
            bound_node=node,
            is_deleted=False,
        ):
            write_audit_log(
                organization=org,
                user=user,
                action=AuditAction.DELETE,
                resource_type="source_resource",
                resource_id=str(resource.id),
                resource_name=resource.name,
                result=AuditResult.SUCCESS,
                metadata={"reason": "backup_source_delete", "node_id": node.id},
            )
            resource.soft_delete()
        redis_store.clear_agent_location(agent_id=node.id)
        delete_pipeline_entry(
            organization_id=org.id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=node.id,
        )
        write_audit_log(
            organization=org,
            user=user,
            action=AuditAction.DELETE,
            resource_type="node",
            resource_id=str(node.id),
            resource_name=node.name,
            result=AuditResult.SUCCESS,
            metadata={"role": node.role, "reason": "backup_source_delete"},
        )
        node.soft_delete()
        return

    resource = ctx.nas_resource
    if resource is None:
        return
    write_audit_log(
        organization=org,
        user=user,
        action=AuditAction.DELETE,
        resource_type="source_resource",
        resource_id=str(resource.id),
        resource_name=resource.name,
        result=AuditResult.SUCCESS,
        metadata={"reason": "backup_source_delete"},
    )
    delete_pipeline_entry(
        organization_id=org.id,
        source_kind=SelectableSourceKind.NAS,
        ref_id=resource.id,
    )
    resource.status = ResourceStatus.REMOVED
    resource.status_message = "Source deregistration completed."
    resource.save(update_fields=["status", "status_message", "updated_at"])
    resource.soft_delete()


def _finalize_single_source_delete(
    *,
    org: Organization,
    ctx: SourceDeleteContext,
    blob_stats: dict[str, int],
    warnings: list[DeleteWarning],
    force: bool,
    unregister_task_id: int,
    unregister_task_attempt: int,
) -> dict[str, Any]:
    # Protection rows and Source identity are intentionally retained until
    # endpoint cleanup has reached a terminal state. The caller purges them in
    # the same transaction that completes the domain task.
    cleanup = {
        **blob_stats,
        "backup_configs_removed": 0,
        "snapshots_removed": 0,
        "restore_plans_removed": 0,
        "restore_records_removed": 0,
        "tasks_orphaned": 0,
    }
    warning_payload = [warning.as_dict() for warning in warnings]

    if ctx.is_agent and ctx.agent_node is not None:
        node = ctx.agent_node
        remove_task = (
            NodeTask.objects.filter(
                organization_id=org.id,
                node_id=node.id,
                kind="agent.uninstall",
                correlation_type="node.lifecycle",
                correlation_id=f"remove:{node.id}",
            )
            .order_by("-created_at", "-id")
            .first()
        )
        remove_result = (
            dict(remove_task.result or {}) if remove_task is not None else {}
        )
        remove_payload = (
            remove_task.payload
            if remove_task is not None and isinstance(remove_task.payload, dict)
            else {}
        )
        belongs_to_unregister = bool(
            remove_task is not None
            and int(remove_payload.get("source_unregister_task_id") or 0)
            == unregister_task_id
            and int(remove_payload.get("source_unregister_attempt") or 0)
            == unregister_task_attempt
        )
        if (
            belongs_to_unregister
            and remove_task is not None
            and remove_task.status
            in {
                NodeTask.Status.FAILED,
                NodeTask.Status.TIMEOUT,
                NodeTask.Status.CANCELED,
            }
        ):
            failure = {
                "code": "agent_uninstall_failed",
                "detail": (
                    remove_task.last_error
                    or "Agent uninstall did not complete successfully."
                ),
            }
            if force:
                cleanup_failures = [
                    dict(item)
                    for item in remove_result.get("cleanup_failures") or []
                    if isinstance(item, dict)
                ]
                if not cleanup_failures:
                    cleanup_failures = [failure]
                retained_resources = [
                    str(item)
                    for item in remove_result.get("retained_resources") or []
                    if str(item).strip()
                ]
                if not retained_resources:
                    retained_resources = ["agent_installation"]
                return {
                    "source_id": ctx.selectable_id,
                    "source_name": ctx.display_name,
                    "cleanup": cleanup,
                    "cleanup_complete": False,
                    "cleanup_failures": cleanup_failures,
                    "retained_resources": retained_resources,
                    "warnings": [warning.as_dict() for warning in warnings],
                }
            raise BackupSourceDeleteFailed(
                message="Agent uninstall failed.",
                reasons=[
                    DeleteReason(
                        code=str(failure["code"]),
                        detail=str(failure["detail"]),
                        source_id=ctx.selectable_id,
                        source_name=ctx.display_name,
                    )
                ],
            )
        if (
            remove_task is not None
            and remove_task.status == NodeTask.Status.SUCCESS
            and (
                remove_result.get("completion_received_at")
                or remove_result.get("completion_timed_out_at")
            )
            and (
                belongs_to_unregister
                or bool(remove_result.get("cleanup_complete"))
                or force
            )
        ):
            return {
                "source_id": ctx.selectable_id,
                "source_name": ctx.display_name,
                "cleanup": cleanup,
                "cleanup_complete": bool(remove_result.get("cleanup_complete")),
                "cleanup_failures": remove_result.get("cleanup_failures") or [],
                "retained_resources": remove_result.get("retained_resources") or [],
                "warnings": [warning.as_dict() for warning in warnings],
            }
        conn = agent_connection_status(node=node)
        if conn == CONNECTION_OFFLINE:
            if not force:
                raise BackupSourceDeleteFailed(
                    message="Backup source was not deleted.",
                    reasons=[
                        DeleteReason(
                            code="agent_offline",
                            detail=(
                                f'Agent "{ctx.display_name}" is offline — remote '
                                "uninstall is required in strict mode."
                            ),
                            source_id=ctx.selectable_id,
                            source_name=ctx.display_name,
                        )
                    ],
                )
            warnings.append(
                DeleteWarning(
                    code="agent_offline",
                    detail=f'Agent "{ctx.display_name}" is offline — uninstall was skipped.',
                    source_id=ctx.selectable_id,
                    source_name=ctx.display_name,
                )
            )
            return {
                "source_id": ctx.selectable_id,
                "source_name": ctx.display_name,
                "cleanup": cleanup,
                "cleanup_complete": False,
                "cleanup_failures": [
                    {
                        "code": "agent_offline",
                        "detail": "Remote uninstall was skipped because the Agent was offline.",
                    }
                ],
                "retained_resources": ["agent_installation"],
                "warnings": [warning.as_dict() for warning in warnings],
            }
        return {
            "source_id": ctx.selectable_id,
            "source_name": ctx.display_name,
            "pending_removal": True,
            "node_id": node.id,
            "cleanup": cleanup,
            "warnings": warning_payload,
        }

    nas_unmount_failed = any(
        warning.code in {"nas_umount_failed", "mount_directory_cleanup_failed"}
        for warning in warnings
    )
    retained_resources: list[str] = []
    if nas_unmount_failed and ctx.nas_resource is not None:
        if any(warning.code == "nas_umount_failed" for warning in warnings):
            retained_resources.append(f"source_nas_mount:{ctx.nas_resource.id}")
        if any(
            warning.code == "mount_directory_cleanup_failed"
            for warning in warnings
        ):
            retained_resources.append(
                f"source_nas_mount_directory:{ctx.nas_resource.id}"
            )

    return {
        "source_id": ctx.selectable_id,
        "source_name": ctx.display_name,
        "cleanup": cleanup,
        "cleanup_complete": not nas_unmount_failed,
        "cleanup_failures": [
            warning.as_dict()
            for warning in warnings
            if warning.code in {"nas_umount_failed", "mount_directory_cleanup_failed"}
        ],
        "retained_resources": retained_resources,
        "warnings": warning_payload,
    }


def _prepare_single_source_snapshot_cleanup(
    *,
    organization_id: int,
    ctx: SourceDeleteContext,
    force: bool,
    skip_repository_ids: set[int] | None = None,
    unregister_task: Task | None = None,
    lease_owner_token: str = "",
) -> tuple[
    dict[str, int],
    list[DeleteWarning],
    list[DeleteReason],
    bool,
    list[dict[str, Any]],
]:
    reasons: list[DeleteReason] = []
    warnings: list[DeleteWarning] = []
    running = _running_tasks_for_source(
        organization_id=organization_id,
        source_type=ctx.source_type,
        source_ref_id=ctx.source_ref_id,
    )
    if running:
        reasons.append(
            DeleteReason(
                code="running_tasks",
                detail=f"{len(running)} backup or restore task(s) are still running.",
                source_id=ctx.selectable_id,
                source_name=ctx.display_name,
            )
        )
        return {}, warnings, reasons, False, []

    cleanup_result = _delete_repository_snapshots(
        organization_id=organization_id,
        ctx=ctx,
        force=force,
        reasons=reasons,
        warnings=warnings,
        skip_repository_ids=skip_repository_ids,
        unregister_task=unregister_task,
        lease_owner_token=lease_owner_token,
    )
    snapshot_cleanup_waiting = bool(
        cleanup_result.pop("_snapshot_cleanup_waiting", False)
    )
    snapshot_cleanup_tasks = [
        dict(item)
        for item in cleanup_result.pop("_snapshot_cleanup_tasks", [])
        if isinstance(item, dict)
    ]
    blob_stats = {str(key): int(value or 0) for key, value in cleanup_result.items()}
    return (
        blob_stats,
        warnings,
        reasons,
        snapshot_cleanup_waiting,
        snapshot_cleanup_tasks,
    )


def delete_backup_sources(
    *,
    org: Organization,
    ids: list[str],
    force: bool = False,
    user=None,
) -> dict[str, Any]:
    """Synchronously unregister backup-selectable sources (tests and internal callers)."""
    normalized = _normalize_delete_ids(ids)
    if len(normalized) != 1:
        raise BackupSourceDeleteFailed(
            message="Synchronous source deregistration accepts one source at a time.",
            reasons=[
                DeleteReason(
                    code="batch_not_supported", detail="Provide exactly one source ID."
                )
            ],
        )
    with transaction.atomic():
        _lock_delete_identities(organization_id=org.id, ids=normalized)
        prepared = _prepare_delete_batch(org=org, ids=normalized, force=force)
        _set_source_nas_removal_status(
            organization_id=org.id,
            ids=normalized,
            status=ResourceStatus.REMOVING,
            message="Source deregistration is in progress.",
        )
        unregister_task = _create_source_unregister_task(
            org=org,
            selectable_id=normalized[0],
            force=force,
        )
        payload = dict(unregister_task.request_payload or {})
        user_id = getattr(user, "id", None)
        if user_id:
            payload["user_id"] = int(user_id)
        unregister_task.request_payload = payload
        unregister_task.save(update_fields=["request_payload", "updated_at"])

        start_task(task_uuid=unregister_task.task_uuid, organization_id=org.id)
        _set_unregister_step(
            task=unregister_task,
            step_name="prepare_source_unregister",
            status=TaskStep.Status.SUCCESS,
            progress=15,
            message="Source deregistration prepared",
            metadata={"source_ids": normalized, "force": bool(force)},
        )
    return _execute_source_unregister_work(
        org=org,
        prepared=prepared,
        force=force,
        user=user,
        unregister_task=unregister_task,
    )


def end_legacy_deferred_source_unregister_task(*, task_id: int) -> dict[str, Any]:
    """End a legacy deferred request without executing its stale delete intent."""
    task_snapshot = (
        Task.objects.filter(pk=int(task_id))
        .values("organization_id", "request_payload")
        .first()
    )
    if task_snapshot is None:
        return {"status": "missing"}
    payload = (
        task_snapshot["request_payload"]
        if isinstance(task_snapshot["request_payload"], dict)
        else {}
    )
    source_ids = [
        str(value).strip()
        for value in payload.get("source_ids") or []
        if str(value).strip()
    ]
    source_ids.extend(
        f"{resource_subtype}:{resource_id}"
        for resource_subtype, resource_id in TaskResource.objects.filter(
            task_id=int(task_id),
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype__in={"agent", "nas"},
        ).values_list("resource_subtype", "resource_id")
    )
    with transaction.atomic():
        # Match normal submission's source -> task lock order. Otherwise a new
        # request and a legacy queue message can deadlock while ending the same
        # stale intent and restoring its NAS state.
        _lock_delete_identities(
            organization_id=int(task_snapshot["organization_id"]),
            ids=source_ids,
        )
        task = Task.objects.select_for_update().filter(pk=int(task_id)).first()
        if task is None:
            return {"status": "missing"}
        if not _is_legacy_deferred_unregister(task):
            return {"status": task.status, "unchanged": True}
        task = _terminalize_legacy_deferred_unregister(task)
        return {"status": task.status, "legacy_deferred_ended": True}


def retry_source_unregister_task(
    *,
    task_uuid: UUID | str,
    organization_id: int,
    reason: str = "",
) -> Task:
    """Retry deregistration through the same eligibility state machine as submit."""
    from apps.source.tasks.source_unregister import execute_source_unregister_task
    from apps.task.services.interface import retry_task

    with transaction.atomic():
        existing = (
            Task.objects.select_for_update()
            .filter(
                task_uuid=task_uuid,
                organization_id=organization_id,
                task_type=Task.Type.SOURCE_UNREGISTER,
            )
            .first()
        )
        if existing is None:
            raise Task.DoesNotExist
        if existing.error_code in _UNREGISTER_NOT_STARTED_ERROR_CODES:
            raise ValidationError(
                "This deregistration did not start. Resolve the prerequisite and "
                "submit a new request."
            )

        task = retry_task(
            task_uuid=task_uuid,
            organization_id=organization_id,
            reason=reason,
        )
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        source_ids = [
            str(value).strip()
            for value in payload.get("source_ids") or []
            if str(value).strip()
        ]
        org = Organization.objects.filter(pk=organization_id).first()
        if org is None or len(source_ids) != 1:
            _complete_unregister_task(
                task=task,
                status=Task.Status.FAILED,
                error_code="SOURCE_UNREGISTER_INVALID_REQUEST",
                error_message="Source deregistration task has no valid organization or source.",
            )
            return Task.objects.get(pk=task.pk)

        _lock_delete_identities(
            organization_id=organization_id,
            ids=source_ids,
        )
        decision = evaluate_source_deregistration(
            org=org,
            selectable_id=source_ids[0],
            force=bool(payload.get("force")),
            executing_task_uuid=str(task.task_uuid),
        )
        if decision.disposition == "invalid":
            invalid_codes = {reason.code for reason in decision.reasons}
            _resolve_unregister_dependencies(task=task)
            if invalid_codes == {"source_not_found"}:
                _complete_unregister_task(
                    task=task,
                    status=Task.Status.SUCCESS,
                    result_payload={
                        "source_ids": source_ids,
                        "deleted": source_ids,
                        "already_absent": True,
                    },
                )
            else:
                _complete_unregister_task(
                    task=task,
                    status=Task.Status.FAILED,
                    result_payload={
                        "source_ids": source_ids,
                        "reasons": [item.as_dict() for item in decision.reasons],
                    },
                    error_code="SOURCE_UNREGISTER_INVALID_REQUEST",
                    error_message="Source deregistration task has an invalid source identifier.",
                )
            return Task.objects.get(pk=task.pk)
        if decision.disposition in {"waiting", "blocked"}:
            return _fail_unregister_before_start(
                task=task,
                reasons=list(decision.reasons),
            )

        _resolve_unregister_dependencies(task=task)
        task = start_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
        )
        _set_unregister_step(
            task=task,
            step_name="prepare_source_unregister",
            status=TaskStep.Status.SUCCESS,
            progress=15,
            message="Source deregistration prepared for retry",
            metadata={"source_ids": source_ids, "force": bool(payload.get("force"))},
        )
        _set_source_nas_removal_status(
            organization_id=organization_id,
            ids=source_ids,
            status=ResourceStatus.REMOVING,
            message="Source deregistration is in progress.",
        )
        transaction.on_commit(
            lambda ready_task_id=int(task.id): execute_source_unregister_task.delay(
                task_id=ready_task_id
            )
        )
        return task


def reconcile_stuck_source_unregister_tasks(
    *,
    limit: int = 50,
    stale_seconds: int = 90,
) -> dict[str, int]:
    """End legacy deferred requests and re-dispatch stuck executions."""
    from apps.source.tasks.source_unregister import execute_source_unregister_task

    deferred_ids = list(
        Task.objects.filter(task_type=Task.Type.SOURCE_UNREGISTER)
        .filter(
            Q(status__in={Task.Status.WAITING, Task.Status.BLOCKED})
            | Q(
                status=Task.Status.RUNNING,
                current_step="prepare_source_unregister",
                result_payload__has_any_keys=["waiting_reasons", "blocked_reasons"],
            )
        )
        .order_by("id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    deferred_ended = 0
    for task_id in deferred_ids:
        result = end_legacy_deferred_source_unregister_task(task_id=int(task_id))
        if result.get("legacy_deferred_ended"):
            deferred_ended += 1

    cutoff = timezone.now() - timedelta(seconds=max(30, int(stale_seconds)))
    stuck = list(
        Task.objects.filter(
            task_type=Task.Type.SOURCE_UNREGISTER,
            status=Task.Status.RUNNING,
            current_step__in={
                "prepare_source_unregister",
                "cleanup_direct_nas_repositories",
                "reset_backup_config",
                "cleanup_source_endpoint",
            },
            updated_at__lt=cutoff,
        ).order_by("updated_at", "id")[: max(1, int(limit))]
    )
    redispatched = 0
    for row in stuck:
        execute_source_unregister_task.delay(task_id=int(row.id))
        redispatched += 1
        logger.warning(
            "re-dispatched stuck source_unregister task id=%s uuid=%s updated_at=%s",
            row.id,
            row.task_uuid,
            row.updated_at,
        )
    return {
        "scanned": len(stuck),
        "redispatched": redispatched,
        "deferred_scanned": len(deferred_ids),
        "deferred_ended": deferred_ended,
    }


__all__ = [
    "BackupSourceDeleteFailed",
    "delete_backup_sources",
    "fail_source_unregister_task_unexpectedly",
    "preflight_delete_backup_sources",
    "queue_delete_backup_sources",
    "reconcile_stuck_source_unregister_tasks",
    "end_legacy_deferred_source_unregister_task",
    "retry_source_unregister_task",
    "run_source_unregister_task",
    "source_needs_reset_protection",
]
