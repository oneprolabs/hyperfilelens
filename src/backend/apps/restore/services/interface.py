from __future__ import annotations

import hashlib
import logging
import ntpath
import posixpath
import secrets
from contextlib import nullcontext
from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.errors import AppError
from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.services.capabilities import missing_node_capabilities
from apps.node.services.internal.node_registry import node_is_available_for_work
from apps.node.services.internal.repository_server import (
    repository_server_diagnostic_code,
    repository_server_public_error_message,
)
from apps.node.services.interface import cancel_agent_task, run_agent_task_async
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_log import (
    log_agent_dispatch,
    log_agent_exception,
    task_log_context,
)
from apps.protection import conf as protection_conf
from apps.protection.services.progress.orchestrated_progress import (
    RESTORE_ESTIMATE_END,
    RESTORE_PREPARE_END,
)
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_reader,
)
from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.restore.models import RestorePlan, RestoreRecord, RestoreRecordItem
from apps.restore.services.task_events import (
    RESTORE_EVENT_SCHEMA_KEY,
    RESTORE_EVENT_SCHEMA_VERSION,
    append_restore_execution_started_event,
    append_restore_item_terminal_event,
)
from apps.restore.services.task_classification import normalize_restore_task_type
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import (
    explicit_repository_server_host,
)
from apps.storage.services.internal.nas_repository import (
    nas_agent_repository_subdir,
    nas_restore_mount_point,
)
from apps.storage.services.internal.repository_workload import (
    RepositoryWorkload,
    lock_repositories_for_workload,
)
from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import (
    append_task_step_event,
    complete_task,
    create_task,
    finalize_task_cancellation,
    start_task,
)

SOURCE_TYPES = {"agent", "nas"}
CONFLICT_MODES = {"skip", "overwrite"}
SCOPES = {"snapshot", "paths"}
RESTORABLE_SNAPSHOT_STATUSES = (
    BackupSourceSnapshot.Status.AVAILABLE,
    BackupSourceSnapshot.Status.PARTIAL,
)
ACTIVE_RESTORE_TASK_STATUSES = (
    Task.Status.PENDING,
    Task.Status.WAITING,
    Task.Status.BLOCKED,
    Task.Status.RUNNING,
)
INSIGHT_SAFE_RESTORE_CAPABILITY = "insight_safe_restore_v1"
INSIGHT_SAFE_CONTENT_POLICY = "regular_files_only_v1"

logger = logging.getLogger(__name__)

_TASK_TERMINAL = frozenset(
    {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }
)
_ACTIVE_NODE_STATUSES = frozenset({NodeTask.Status.PENDING, NodeTask.Status.RUNNING})


@transaction.atomic
def create_restore_plan(*, organization_id: int, data: dict[str, Any]) -> RestorePlan:
    payload = _plan_payload(organization_id=organization_id, data=data)
    _validate_restore_plan_configuration(
        organization_id=organization_id, payload=payload
    )
    return RestorePlan.objects.create(organization_id=organization_id, **payload)


@transaction.atomic
def update_restore_plan(*, plan: RestorePlan, data: dict[str, Any]) -> RestorePlan:
    merged = {
        "backup_config_id": plan.backup_config_id,
        "backup_config_dir_id": plan.backup_config_dir_id,
        "scope": plan.scope,
        "source_type": plan.source_type,
        "source_ref_id": plan.source_ref_id,
        "source_path": plan.source_path,
        "target_type": plan.target_type,
        "target_ref_id": plan.target_ref_id,
        "restore_dir": plan.restore_dir,
        "conflict_mode": plan.conflict_mode,
        "enabled": plan.enabled,
        "sort_order": plan.sort_order,
    }
    merged.update(data)
    payload = _plan_payload(organization_id=plan.organization_id, data=merged)
    _validate_restore_plan_configuration(
        organization_id=plan.organization_id, payload=payload, exclude_plan_id=plan.id
    )
    for field, value in payload.items():
        setattr(plan, field, value)
    plan.save()
    return plan


@transaction.atomic
def delete_restore_plan(*, plan: RestorePlan) -> dict[str, Any]:
    plan_id = int(plan.id)
    plan.delete()
    return {"deleted": True, "id": plan_id}


@transaction.atomic
def run_restore_plan(
    *,
    organization_id: int,
    plan: RestorePlan,
    user_id: int | None = None,
    idempotency_key: str | None = None,
    source_snapshot_id: int | None = None,
) -> RestoreRecord:
    if not plan.enabled:
        raise ValidationError({"enabled": "Restore plan is disabled."})
    _validate_plan_source_path_absolute(plan)
    logger.info(
        "restore plan run started plan_id=%s org_id=%s source_type=%s source_ref_id=%s target_type=%s target_ref_id=%s user_id=%s",
        plan.id,
        organization_id,
        plan.source_type,
        plan.source_ref_id,
        plan.target_type,
        plan.target_ref_id,
        user_id,
    )
    snapshot = _restore_plan_snapshot(
        organization_id=organization_id,
        source_type=plan.source_type,
        source_ref_id=plan.source_ref_id,
        plans=[plan],
        source_snapshot_id=source_snapshot_id,
    )
    if snapshot is None:
        raise ValidationError(
            {
                "source_snapshot_id": "No restorable source snapshot found for restore plan."
            }
        )
    item_inputs = _restore_plan_item_inputs(
        organization_id=organization_id,
        snapshot=snapshot,
        plan=plan,
    )
    record = _create_restore_record(
        organization_id=organization_id,
        source_mode=RestoreRecord.SourceMode.PLAN,
        plan_id=plan.id,
        source_type=plan.source_type,
        source_ref_id=plan.source_ref_id,
        backup_config_id=plan.backup_config_id,
        source_snapshot=snapshot,
        target_type=plan.target_type,
        target_ref_id=plan.target_ref_id,
        target_path=plan.restore_dir,
        scope=RestoreRecord.Scope.SNAPSHOT
        if plan.scope == RestorePlan.Scope.SNAPSHOT
        else RestoreRecord.Scope.PATHS,
        conflict_mode=plan.conflict_mode,
        item_inputs=item_inputs,
        request_payload={
            "plan_id": plan.id,
            "source_snapshot_id": snapshot.id,
            "idempotency_key": idempotency_key or "",
        },
        created_by_id=user_id,
        idempotency_key=str(idempotency_key or ""),
    )
    logger.info(
        "restore plan run ok plan_id=%s restore_record_id=%s restore_uid=%s task_uuid=%s",
        plan.id,
        record.id,
        record.restore_uid,
        record.task_uuid,
    )
    return record


@transaction.atomic
def run_restore_plans_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    user_id: int | None = None,
    idempotency_key: str | None = None,
    source_snapshot_id: int | None = None,
) -> list[RestoreRecord]:
    source_type = _choice({"source_type": source_type}, "source_type", SOURCE_TYPES)
    source_ref_id = int(source_ref_id)
    plans = list(
        RestorePlan.objects.filter(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            enabled=True,
        ).order_by(
            "backup_config_id",
            "target_type",
            "target_ref_id",
            "restore_dir",
            "conflict_mode",
            "sort_order",
            "id",
        )
    )
    if not plans:
        raise ValidationError(
            {"plans": "No enabled restore plans found for this source."}
        )
    for plan in plans:
        _validate_plan_source_path_absolute(plan)
    records: list[RestoreRecord] = []
    plan_groups: dict[tuple[int, str, int, str], list[RestorePlan]] = {}
    for plan in plans:
        key = (
            int(plan.backup_config_id),
            str(plan.target_type),
            int(plan.target_ref_id),
            _normalize_path(plan.restore_dir),
        )
        plan_groups.setdefault(key, []).append(plan)
    if len(plan_groups) > 1:
        raise ValidationError(
            {
                "plans": "Multiple restore groups would create multiple restore tasks for the same source."
            }
        )
    for (
        backup_config_id,
        target_type,
        target_ref_id,
        restore_dir,
    ), group_plans in plan_groups.items():
        snapshot = _restore_plan_snapshot(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            plans=group_plans,
            source_snapshot_id=source_snapshot_id,
        )
        if snapshot is None:
            raise ValidationError(
                {
                    "source_snapshot_id": "No restorable source snapshot found for restore plan."
                }
            )
        item_inputs = _restore_plan_item_inputs_for_plans(
            organization_id=organization_id,
            snapshot=snapshot,
            plans=group_plans,
            restore_dir=restore_dir,
        )
        first_plan = group_plans[0]
        records.append(
            _create_restore_record(
                organization_id=organization_id,
                source_mode=RestoreRecord.SourceMode.PLAN,
                plan_id=first_plan.id,
                source_type=source_type,
                source_ref_id=source_ref_id,
                backup_config_id=backup_config_id,
                source_snapshot=snapshot,
                target_type=target_type,
                target_ref_id=target_ref_id,
                target_path=restore_dir,
                scope=RestoreRecord.Scope.SNAPSHOT
                if any(plan.scope == RestorePlan.Scope.SNAPSHOT for plan in group_plans)
                else RestoreRecord.Scope.PATHS,
                conflict_mode=first_plan.conflict_mode,
                item_inputs=item_inputs,
                request_payload={
                    "plan_id": first_plan.id,
                    "plan_ids": [plan.id for plan in group_plans],
                    "backup_config_id": backup_config_id,
                    "source_snapshot_id": snapshot.id,
                    "idempotency_key": idempotency_key or "",
                },
                created_by_id=user_id,
                idempotency_key=str(idempotency_key or ""),
            )
        )
    return records


@transaction.atomic
def run_restore_plan_batch(
    *,
    organization_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
) -> RestoreRecord:
    backup_config_id = _int(data, "backup_config_id")
    target_type = _choice(data, "target_type", SOURCE_TYPES)
    target_ref_id = _int(data, "target_ref_id")
    restore_dir = _path(data, "restore_dir")
    conflict_mode = _choice(data, "conflict_mode", CONFLICT_MODES)
    source_snapshot_id = _optional_int(data, "source_snapshot_id")
    config = BackupConfig.objects.filter(
        organization_id=organization_id, pk=backup_config_id
    ).first()
    if config is None:
        raise ValidationError({"backup_config_id": "Backup config not found."})
    plans = list(
        RestorePlan.objects.filter(
            organization_id=organization_id,
            backup_config_id=backup_config_id,
            target_type=target_type,
            target_ref_id=target_ref_id,
            restore_dir=restore_dir,
            conflict_mode=conflict_mode,
            enabled=True,
        ).order_by("sort_order", "id")
    )
    if not plans:
        raise ValidationError(
            {"plans": "No enabled restore plans found for this restore group."}
        )
    for plan in plans:
        _validate_plan_source_path_absolute(plan)
    source_type = str(config.source_type)
    source_ref_id = int(config.source_ref_id)
    for plan in plans:
        if plan.source_type != source_type or plan.source_ref_id != source_ref_id:
            raise ValidationError(
                {"source_ref_id": "Restore plan source does not match backup config."}
            )
    snapshot = _restore_plan_snapshot(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        plans=plans,
        source_snapshot_id=source_snapshot_id,
    )
    if snapshot is None:
        raise ValidationError(
            {
                "source_snapshot_id": "No restorable source snapshot found for restore plan."
            }
        )
    item_inputs = _restore_plan_item_inputs_for_plans(
        organization_id=organization_id,
        snapshot=snapshot,
        plans=plans,
        restore_dir=restore_dir,
    )
    first_plan = plans[0]
    return _create_restore_record(
        organization_id=organization_id,
        source_mode=RestoreRecord.SourceMode.PLAN,
        plan_id=first_plan.id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        backup_config_id=backup_config_id,
        source_snapshot=snapshot,
        target_type=target_type,
        target_ref_id=target_ref_id,
        target_path=restore_dir,
        scope=RestoreRecord.Scope.SNAPSHOT
        if any(plan.scope == RestorePlan.Scope.SNAPSHOT for plan in plans)
        else RestoreRecord.Scope.PATHS,
        conflict_mode=conflict_mode,
        item_inputs=item_inputs,
        request_payload={
            "plan_id": first_plan.id,
            "plan_ids": [plan.id for plan in plans],
            "backup_config_id": backup_config_id,
            "source_snapshot_id": snapshot.id,
            "idempotency_key": str(data.get("idempotency_key") or ""),
        },
        created_by_id=user_id,
        idempotency_key=str(data.get("idempotency_key") or ""),
    )


@transaction.atomic
def create_manual_restore_record(
    *,
    organization_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
) -> RestoreRecord:
    return _create_manual_restore_record(
        organization_id=organization_id,
        data=data,
        user_id=user_id,
        purpose=RestoreRecord.Purpose.USER_DATA,
    )


def _create_manual_restore_record(
    *,
    organization_id: int,
    data: dict[str, Any],
    user_id: int | None,
    purpose: str,
    target_execution_organization_id: int | None = None,
    target_execution_node_id: int | None = None,
    workspace_binding_id: int | None = None,
    expected_target_path: str | None = None,
) -> RestoreRecord:
    source_type = _choice(data, "source_type", SOURCE_TYPES)
    target_type = _choice(data, "target_type", SOURCE_TYPES)
    source_ref_id = _int(data, "source_ref_id")
    target_ref_id = _int(data, "target_ref_id")
    source_snapshot_id = _int(data, "source_snapshot_id")
    target_path = _path(data, "target_path")
    scope = _choice(data, "scope", SCOPES)
    conflict_mode = _choice(data, "conflict_mode", CONFLICT_MODES)
    _validate_endpoint_exists(
        organization_id=organization_id,
        endpoint_type=source_type,
        ref_id=source_ref_id,
        field="source_ref_id",
    )
    if purpose == RestoreRecord.Purpose.USER_DATA:
        if (
            target_execution_organization_id is not None
            or target_execution_node_id is not None
        ):
            raise ValueError("public restore cannot override its execution identity")
        _validate_endpoint_exists(
            organization_id=organization_id,
            endpoint_type=target_type,
            ref_id=target_ref_id,
            field="target_ref_id",
        )
        execution_node = _target_execution_node(
            organization_id=organization_id,
            target_type=target_type,
            target_ref_id=target_ref_id,
        )
        target_execution_organization_id = organization_id
        target_execution_node_id = execution_node.id
    else:
        if purpose != RestoreRecord.Purpose.LENS_WORKSPACE:
            raise ValueError("unsupported restore purpose")
        if target_execution_organization_id is None or target_execution_node_id is None:
            raise ValueError(
                "lens workspace restore requires a validated execution binding"
            )
        if workspace_binding_id is None or expected_target_path is None:
            raise ValueError(
                "lens workspace restore requires an authoritative workspace binding"
            )
        if target_type != RestoreRecord.EndpointType.AGENT:
            raise ValidationError(
                {"target_type": "Lens workspace restore requires a data gateway."}
            )
        if target_execution_node_id != target_ref_id:
            raise ValidationError(
                {
                    "target_ref_id": "Lens workspace target does not match its gateway binding."
                }
            )
        if target_path != expected_target_path:
            raise ValidationError(
                {"target_path": "Lens workspace target path is not authoritative."}
            )
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        pk=source_snapshot_id,
        status__in=RESTORABLE_SNAPSHOT_STATUSES,
    ).first()
    if snapshot is None:
        raise ValidationError(
            {"source_snapshot_id": "No restorable source snapshot found."}
        )
    if snapshot.source_type != source_type or snapshot.source_ref_id != source_ref_id:
        raise ValidationError(
            {"source_snapshot_id": "Source snapshot does not match restore source."}
        )
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError({"items": "At least one restore item is required."})
    item_inputs: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError({"items": "Each restore item must be an object."})
        selected_paths = _selected_paths(item.get("selected_paths", []))
        item_inputs.append(
            {
                "source_snapshot_directory_id": _int(
                    item, "source_snapshot_directory_id"
                ),
                "selected_paths": selected_paths,
                "target_path": _path(item, "target_path", default=target_path),
                "conflict_mode": _choice(
                    item, "conflict_mode", CONFLICT_MODES, default=conflict_mode
                ),
            }
        )
    if purpose == RestoreRecord.Purpose.LENS_WORKSPACE and any(
        item["target_path"] != expected_target_path for item in item_inputs
    ):
        raise ValidationError(
            {
                "items": "Lens workspace restore item paths must match the workspace binding."
            }
        )
    logger.info(
        "restore manual create started org_id=%s source_snapshot_id=%s source_type=%s source_ref_id=%s target_type=%s target_ref_id=%s item_count=%s user_id=%s",
        organization_id,
        source_snapshot_id,
        source_type,
        source_ref_id,
        target_type,
        target_ref_id,
        len(item_inputs),
        user_id,
    )
    record = _create_restore_record(
        organization_id=organization_id,
        source_mode=RestoreRecord.SourceMode.MANUAL,
        plan_id=None,
        source_type=source_type,
        source_ref_id=source_ref_id,
        backup_config_id=snapshot.backup_config_id,
        source_snapshot=snapshot,
        target_type=target_type,
        target_ref_id=target_ref_id,
        target_path=target_path,
        scope=scope,
        conflict_mode=conflict_mode,
        item_inputs=item_inputs,
        request_payload={
            "source_snapshot_id": source_snapshot_id,
            "scope": scope,
            "idempotency_key": str(data.get("idempotency_key") or ""),
        },
        created_by_id=user_id,
        purpose=purpose,
        requesting_organization_id=organization_id,
        target_execution_organization_id=target_execution_organization_id,
        target_execution_node_id=target_execution_node_id,
        idempotency_key=str(data.get("idempotency_key") or ""),
        workspace_binding_id=workspace_binding_id,
    )
    logger.info(
        "restore manual create ok restore_record_id=%s restore_uid=%s task_uuid=%s",
        record.id,
        record.restore_uid,
        record.task_uuid,
    )
    return record


@transaction.atomic
def create_lens_workspace_restore_record(
    *,
    organization_id: int,
    workspace_binding_id: int,
    data: dict[str, Any],
) -> RestoreRecord:
    """Create the only restore type allowed to execute on a Platform gateway."""

    tenant_organization = (
        Organization.objects.select_for_update().filter(pk=organization_id).first()
    )
    if tenant_organization is None:
        raise ValidationError({"organization_id": "Organization is not available."})
    from apps.lens_bridge.services.gateway_execution import (
        context_for_workspace_binding,
    )

    execution_context, workspace_binding = context_for_workspace_binding(
        tenant_organization=tenant_organization,
        workspace_binding_id=workspace_binding_id,
    )
    expected_target_path = workspace_binding.resolved_path()
    idempotency_key = str(data.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise ValidationError(
            {"idempotency_key": "Lens workspace restore requires an idempotency key."}
        )
    existing = RestoreRecord.objects.filter(
        organization_id=organization_id,
        purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        expected = (
            int(execution_context.execution_organization.id),
            int(execution_context.gateway.id),
            workspace_binding.id,
            _int(data, "source_snapshot_id"),
            expected_target_path,
        )
        actual = (
            existing.target_execution_organization_id,
            existing.target_execution_node_id,
            existing.workspace_binding_id,
            existing.source_snapshot_id,
            existing.target_path,
        )
        if actual != expected:
            raise ValidationError(
                {
                    "idempotency_key": "Lens workspace idempotency key is bound to another execution request."
                }
            )
        return existing
    try:
        with transaction.atomic():
            return _create_manual_restore_record(
                organization_id=organization_id,
                data=data,
                user_id=None,
                purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
                target_execution_organization_id=execution_context.execution_organization.id,
                target_execution_node_id=execution_context.gateway.id,
                workspace_binding_id=workspace_binding.id,
                expected_target_path=expected_target_path,
            )
    except IntegrityError:
        existing = RestoreRecord.objects.filter(
            organization_id=organization_id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key=idempotency_key,
        ).first()
        if existing is None:
            raise
        expected = (
            execution_context.execution_organization.id,
            execution_context.gateway.id,
            workspace_binding.id,
            _int(data, "source_snapshot_id"),
            expected_target_path,
        )
        actual = (
            existing.target_execution_organization_id,
            existing.target_execution_node_id,
            existing.workspace_binding_id,
            existing.source_snapshot_id,
            existing.target_path,
        )
        if actual != expected:
            raise ValidationError(
                {
                    "idempotency_key": "Lens workspace idempotency key is bound to another execution request."
                }
            )
        return existing


def _restore_item_signature(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "source_snapshot_directory_id",
        "backup_config_dir_id",
        "repository_id",
        "kopia_snapshot_id",
        "source_path",
        "source_path_type",
        "selected_paths",
        "target_path",
        "target_path_semantics",
        "conflict_mode",
    )
    return [{key: item.get(key) for key in keys} for item in items]


def _idempotent_restore_record(
    *,
    organization_id: int,
    purpose: str,
    idempotency_key: str,
    source_mode: str,
    plan_id: int | None,
    source_type: str,
    source_ref_id: int,
    backup_config_id: int | None,
    source_snapshot_id: int,
    target_type: str,
    target_ref_id: int,
    target_path: str,
    scope: str,
    conflict_mode: str,
    request_payload: dict[str, Any],
    directories: list[dict[str, Any]],
) -> RestoreRecord | None:
    if not idempotency_key:
        return None
    existing = (
        RestoreRecord.objects.select_for_update()
        .filter(
            organization_id=organization_id,
            purpose=purpose,
            idempotency_key=idempotency_key,
        )
        .first()
    )
    if existing is None:
        return None
    expected = (
        source_mode,
        plan_id,
        source_type,
        int(source_ref_id),
        backup_config_id,
        int(source_snapshot_id),
        target_type,
        int(target_ref_id),
        target_path,
        scope,
        conflict_mode,
        request_payload,
        _restore_item_signature(directories),
    )
    expanded = (
        existing.expanded_payload
        if isinstance(existing.expanded_payload, dict)
        else {}
    )
    actual = (
        existing.source_mode,
        existing.plan_id,
        existing.source_type,
        int(existing.source_ref_id),
        existing.backup_config_id,
        int(existing.source_snapshot_id),
        existing.target_type,
        int(existing.target_ref_id),
        existing.target_path,
        existing.scope,
        existing.conflict_mode,
        existing.request_payload,
        _restore_item_signature(expanded.get("items", [])),
    )
    if actual != expected:
        raise ValidationError(
            {
                "idempotency_key": (
                    "Idempotency key is bound to another restore request."
                )
            }
        )
    return existing


def _create_restore_record(
    *,
    organization_id: int,
    source_mode: str,
    plan_id: int | None,
    source_type: str,
    source_ref_id: int,
    backup_config_id: int | None,
    source_snapshot: BackupSourceSnapshot,
    target_type: str,
    target_ref_id: int,
    target_path: str,
    scope: str,
    conflict_mode: str,
    item_inputs: list[dict[str, Any]],
    request_payload: dict[str, Any],
    created_by_id: int | None,
    purpose: str = RestoreRecord.Purpose.USER_DATA,
    requesting_organization_id: int | None = None,
    target_execution_organization_id: int | None = None,
    target_execution_node_id: int | None = None,
    idempotency_key: str = "",
    workspace_binding_id: int | None = None,
) -> RestoreRecord:
    if target_execution_node_id is None or target_execution_organization_id is None:
        _validate_endpoint_exists(
            organization_id=organization_id,
            endpoint_type=target_type,
            ref_id=target_ref_id,
            field="target_ref_id",
        )
        execution_node = _target_execution_node(
            organization_id=organization_id,
            target_type=target_type,
            target_ref_id=target_ref_id,
        )
        target_execution_node_id = execution_node.id
        target_execution_organization_id = execution_node.organization_id
    elif not Node.objects.filter(
        id=target_execution_node_id,
        organization_id=target_execution_organization_id,
        is_deleted=False,
    ).exists():
        raise ValidationError({"target_ref_id": "Restore execution node not found."})
    from apps.source.services.internal.source_operation_fence import (
        assert_source_product_operation_allowed,
    )

    assert_source_product_operation_allowed(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    directories = _expanded_directories(
        organization_id=organization_id,
        source_snapshot=source_snapshot,
        item_inputs=item_inputs,
    )
    normalized_idempotency_key = str(idempotency_key or "").strip()
    idempotency_lookup = {
        "organization_id": organization_id,
        "purpose": purpose,
        "idempotency_key": normalized_idempotency_key,
        "source_mode": source_mode,
        "plan_id": plan_id,
        "source_type": source_type,
        "source_ref_id": source_ref_id,
        "backup_config_id": backup_config_id,
        "source_snapshot_id": source_snapshot.id,
        "target_type": target_type,
        "target_ref_id": target_ref_id,
        "target_path": target_path,
        "scope": scope,
        "conflict_mode": conflict_mode,
        "request_payload": request_payload,
        "directories": directories,
    }
    existing = _idempotent_restore_record(**idempotency_lookup)
    if existing is not None:
        return existing
    _ensure_no_active_restore_for_source(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        purpose=purpose,
    )
    repository_ids = sorted(
        {int(directory["repository_id"]) for directory in directories}
    )
    lock_repositories_for_workload(
        organization_id=organization_id,
        repository_ids=repository_ids,
        workload=RepositoryWorkload.RESTORE_READ,
    )
    restore_uid = f"rst-{uuid4().hex[:16]}"
    task_type = (
        Task.Type.INSIGHT_WORKSPACE_RESTORE
        if purpose == RestoreRecord.Purpose.LENS_WORKSPACE
        else Task.Type.RESTORE
    )
    trigger_type = (
        Task.TriggerType.SYSTEM
        if purpose == RestoreRecord.Purpose.LENS_WORKSPACE
        else Task.TriggerType.MANUAL
    )
    task = create_task(
        organization_id=organization_id,
        task_type=task_type,
        display_name=(
            f"Insight workspace restore {restore_uid}"
            if purpose == RestoreRecord.Purpose.LENS_WORKSPACE
            else f"Restore {restore_uid}"
        ),
        trigger_type=trigger_type,
        request_payload={"restore_uid": restore_uid, "restore_record_id": None},
        resources=[
            {
                "resource_type": TaskResource.Type.BACKUP_SOURCE,
                "resource_subtype": source_type,
                "resource_id": source_ref_id,
                "is_primary": True,
            },
            *[
                {
                    "resource_type": TaskResource.Type.REPOSITORY,
                    "resource_id": repository_id,
                }
                for repository_id in repository_ids
            ],
        ],
        steps=["prepare_restore", "dispatch_agent", "restore", "finalize"],
    )
    try:
        # Keep the uniqueness violation inside a savepoint so an exact
        # concurrent replay can reuse the committed winner. The provisional
        # task is removed below before returning that winner.
        with transaction.atomic():
            record = RestoreRecord.objects.create(
                organization_id=organization_id,
                requesting_organization_id=requesting_organization_id
                or organization_id,
                target_execution_organization_id=target_execution_organization_id,
                target_execution_node_id=target_execution_node_id,
                purpose=purpose,
                idempotency_key=normalized_idempotency_key,
                workspace_binding_id=workspace_binding_id,
                restore_uid=restore_uid,
                source_mode=source_mode,
                plan_id=plan_id,
                task_id=task.id,
                task_uuid=task.task_uuid,
                source_type=source_type,
                source_ref_id=source_ref_id,
                backup_config_id=backup_config_id,
                source_snapshot_id=source_snapshot.id,
                target_type=target_type,
                target_ref_id=target_ref_id,
                target_path=target_path,
                scope=scope,
                conflict_mode=conflict_mode,
                request_payload=request_payload,
                created_by_id=created_by_id,
            )
    except IntegrityError:
        if not normalized_idempotency_key:
            raise
        task.delete()
        existing = _idempotent_restore_record(**idempotency_lookup)
        if existing is None:
            raise
        return existing
    items = [
        RestoreRecordItem(
            organization_id=organization_id,
            restore_record=record,
            source_snapshot_directory_id=directory["source_snapshot_directory_id"],
            backup_config_dir_id=directory["backup_config_dir_id"],
            repository_id=directory["repository_id"],
            kopia_snapshot_id=directory["kopia_snapshot_id"],
            source_path=directory["source_path"],
            selected_paths=directory["selected_paths"],
            target_path=directory["target_path"],
            conflict_mode=directory["conflict_mode"],
        )
        for directory in directories
    ]
    RestoreRecordItem.objects.bulk_create(items)
    expanded_items = [
        {
            "source_snapshot_directory_id": item.source_snapshot_directory_id,
            "backup_config_dir_id": item.backup_config_dir_id,
            "repository_id": item.repository_id,
            "kopia_snapshot_id": item.kopia_snapshot_id,
            "source_path": item.source_path,
            "source_path_type": directory["source_path_type"],
            "selected_paths": item.selected_paths,
            "target_path": item.target_path,
            "target_path_semantics": directory["target_path_semantics"],
            "conflict_mode": item.conflict_mode,
        }
        for item, directory in zip(record.items.all(), directories)
    ]
    record.expanded_payload = {"items": expanded_items}
    record.save(update_fields=["expanded_payload", "updated_at"])
    task.request_payload = {
        RESTORE_EVENT_SCHEMA_KEY: RESTORE_EVENT_SCHEMA_VERSION,
        "restore_record_id": record.id,
        "restore_uid": record.restore_uid,
        "source_mode": source_mode,
        "source_snapshot_id": source_snapshot.id,
        "target_type": target_type,
        "target_ref_id": target_ref_id,
        "target_path": target_path,
        "items": expanded_items,
        "conflict_mode": conflict_mode,
    }
    task.save(update_fields=["request_payload", "updated_at"])
    _dispatch_restore_items(
        organization_id=organization_id,
        record=record,
        task=task,
    )
    return record


def _expanded_directories(
    *,
    organization_id: int,
    source_snapshot: BackupSourceSnapshot,
    item_inputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if any(item.get("target_path_semantics") != "final" for item in item_inputs):
        raw_items: list[dict[str, Any]] = []
        for item in item_inputs:
            directory = BackupSourceSnapshotDirectory.objects.filter(
                organization_id=organization_id,
                source_snapshot=source_snapshot,
                pk=item["source_snapshot_directory_id"],
            ).first()
            if directory is None:
                raise ValidationError(
                    {
                        "items": "Snapshot directory does not belong to the source snapshot."
                    }
                )
            _ensure_directory_usable(directory)
            raw_items.append(
                {
                    "source_snapshot_directory_id": directory.id,
                    "selected_paths": item.get("selected_paths") or [],
                    "target_source_path": directory.source_path,
                    "source_path_type": directory.path_type
                    or BackupSourceSnapshotDirectory.PathType.UNKNOWN,
                    "restore_dir": item["target_path"],
                    "conflict_mode": item["conflict_mode"],
                }
            )
        item_inputs = _finalize_restore_item_targets(raw_items)

    result: list[dict[str, Any]] = []
    seen: set[tuple[int, tuple[str, ...], str]] = set()
    for item in item_inputs:
        directory = BackupSourceSnapshotDirectory.objects.filter(
            organization_id=organization_id,
            source_snapshot=source_snapshot,
            pk=item["source_snapshot_directory_id"],
        ).first()
        if directory is None:
            raise ValidationError(
                {"items": "Snapshot directory does not belong to the source snapshot."}
            )
        _ensure_directory_usable(directory)
        target_path = item["target_path"]
        selected_paths = item.get("selected_paths") or []
        token = (directory.id, tuple(selected_paths), target_path)
        if token in seen:
            continue
        seen.add(token)
        result.append(
            {
                "source_snapshot_directory_id": directory.id,
                "backup_config_dir_id": directory.backup_config_dir_id,
                "repository_id": directory.repository_id,
                "kopia_snapshot_id": directory.kopia_snapshot_id,
                "source_path": directory.source_path,
                "source_path_type": directory.path_type
                or BackupSourceSnapshotDirectory.PathType.UNKNOWN,
                "selected_paths": selected_paths,
                "target_path": target_path,
                "target_path_semantics": "final",
                "conflict_mode": item["conflict_mode"],
            }
        )
    return result


def _plan_payload(*, organization_id: int, data: dict[str, Any]) -> dict[str, Any]:
    backup_config_id = _int(data, "backup_config_id")
    scope = _choice(data, "scope", SCOPES, default=RestorePlan.Scope.PATHS)
    backup_config_dir_id = _optional_int(data, "backup_config_dir_id")
    source_type = _choice(data, "source_type", SOURCE_TYPES)
    target_type = _choice(data, "target_type", SOURCE_TYPES)
    source_ref_id = _int(data, "source_ref_id")
    target_ref_id = _int(data, "target_ref_id")
    source_path = (
        "" if scope == RestorePlan.Scope.SNAPSHOT else _path(data, "source_path")
    )
    restore_dir = _path(data, "restore_dir")
    conflict_mode = _choice(data, "conflict_mode", CONFLICT_MODES)
    config = BackupConfig.objects.filter(
        organization_id=organization_id, pk=backup_config_id
    ).first()
    if config is None:
        raise ValidationError({"backup_config_id": "Backup config not found."})
    if config.source_type != source_type or config.source_ref_id != source_ref_id:
        raise ValidationError(
            {"source_ref_id": "Restore plan source does not match backup config."}
        )
    if scope == RestorePlan.Scope.PATHS:
        if backup_config_dir_id is None:
            raise ValidationError(
                {
                    "backup_config_dir_id": "Backup config directory is required for path restore plans."
                }
            )
        if not _is_absolute_source_path(source_path):
            raise ValidationError(
                {"source_path": "Restore plan source path must be absolute."}
            )
        directory = BackupConfigDirectory.objects.filter(
            organization_id=organization_id,
            backup_config_id=backup_config_id,
            pk=backup_config_dir_id,
        ).first()
        if directory is None:
            raise ValidationError(
                {"backup_config_dir_id": "Backup config directory not found."}
            )
        if not _same_or_ancestor_path(directory.path, source_path):
            raise ValidationError(
                {
                    "source_path": "Restore plan source path must be inside backup config directory."
                }
            )
    else:
        backup_config_dir_id = None
    _validate_endpoint_exists(
        organization_id=organization_id,
        endpoint_type=source_type,
        ref_id=source_ref_id,
        field="source_ref_id",
    )
    _validate_endpoint_exists(
        organization_id=organization_id,
        endpoint_type=target_type,
        ref_id=target_ref_id,
        field="target_ref_id",
    )
    return {
        "backup_config_id": backup_config_id,
        "backup_config_dir_id": backup_config_dir_id,
        "scope": scope,
        "source_type": source_type,
        "source_ref_id": source_ref_id,
        "source_path": source_path,
        "target_type": target_type,
        "target_ref_id": target_ref_id,
        "restore_dir": restore_dir,
        "conflict_mode": conflict_mode,
        "enabled": bool(data.get("enabled", True)),
        "sort_order": _int(data, "sort_order", default=0, min_value=0),
    }


def _validate_restore_plan_configuration(
    *,
    organization_id: int,
    payload: dict[str, Any],
    exclude_plan_id: int | None = None,
) -> None:
    queryset = RestorePlan.objects.filter(
        organization_id=organization_id,
        backup_config_id=payload["backup_config_id"],
        source_type=payload["source_type"],
        source_ref_id=payload["source_ref_id"],
        target_type=payload["target_type"],
        target_ref_id=payload["target_ref_id"],
        restore_dir=payload["restore_dir"],
        enabled=True,
    )
    if exclude_plan_id is not None:
        queryset = queryset.exclude(pk=exclude_plan_id)
    if not payload.get("enabled", True):
        return
    if payload["scope"] == RestorePlan.Scope.SNAPSHOT:
        if queryset.filter(scope=RestorePlan.Scope.SNAPSHOT).exists():
            raise ValidationError(
                {
                    "restore_dir": "Duplicate whole-snapshot restore plan for the same destination."
                }
            )
        return
    if queryset.filter(
        scope=RestorePlan.Scope.PATHS, source_path=payload["source_path"]
    ).exists():
        raise ValidationError(
            {"source_path": "Duplicate restore plan source and destination."}
        )


def _validate_plan_source_path_absolute(plan: RestorePlan) -> None:
    if plan.scope == RestorePlan.Scope.PATHS and not _is_absolute_source_path(
        plan.source_path
    ):
        raise ValidationError(
            {"source_path": "Restore plan source path must be absolute."}
        )


def _latest_restorable_snapshot(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    plans: list[RestorePlan] | None = None,
) -> BackupSourceSnapshot | None:
    snapshots = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        status__in=RESTORABLE_SNAPSHOT_STATUSES,
    ).order_by("-finished_at", "-created_at", "-id")
    if not plans:
        return snapshots.first()
    for snapshot in snapshots:
        if all(
            _restore_plan_item_input_or_none(
                organization_id=organization_id,
                snapshot=snapshot,
                plan=plan,
            )
            for plan in plans
        ):
            return snapshot
    return None


def _restore_plan_snapshot(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    plans: list[RestorePlan],
    source_snapshot_id: int | None = None,
) -> BackupSourceSnapshot | None:
    if not source_snapshot_id:
        return _latest_restorable_snapshot(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            plans=plans,
        )
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        pk=int(source_snapshot_id),
        source_type=source_type,
        source_ref_id=source_ref_id,
        status__in=RESTORABLE_SNAPSHOT_STATUSES,
    ).first()
    if snapshot is None:
        raise ValidationError(
            {"source_snapshot_id": "Selected source snapshot is not restorable."}
        )
    if not all(
        _restore_plan_item_input_or_none(
            organization_id=organization_id,
            snapshot=snapshot,
            plan=plan,
        )
        for plan in plans
    ):
        raise ValidationError(
            {
                "source_snapshot_id": "Selected source snapshot does not cover the restore plan."
            }
        )
    return snapshot


def _snapshot_directory(
    *,
    organization_id: int,
    source_snapshot_id: int,
    backup_config_dir_id: int,
) -> BackupSourceSnapshotDirectory | None:
    return BackupSourceSnapshotDirectory.objects.filter(
        organization_id=organization_id,
        source_snapshot_id=source_snapshot_id,
        backup_config_dir_id=backup_config_dir_id,
    ).first()


def _restore_plan_item_inputs_for_plans(
    *,
    organization_id: int,
    snapshot: BackupSourceSnapshot,
    plans: list[RestorePlan],
    restore_dir: str,
) -> list[dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    for plan in plans:
        raw_items.extend(
            _restore_plan_raw_item_inputs(
                organization_id=organization_id,
                snapshot=snapshot,
                plan=plan,
            )
        )
    return _finalize_restore_item_targets(raw_items, restore_dir=restore_dir)


def _restore_plan_item_inputs(
    *,
    organization_id: int,
    snapshot: BackupSourceSnapshot,
    plan: RestorePlan,
) -> list[dict[str, Any]]:
    raw_items = _restore_plan_raw_item_inputs(
        organization_id=organization_id,
        snapshot=snapshot,
        plan=plan,
    )
    return _finalize_restore_item_targets(raw_items, restore_dir=plan.restore_dir)


def _restore_plan_raw_item_inputs(
    *,
    organization_id: int,
    snapshot: BackupSourceSnapshot,
    plan: RestorePlan,
) -> list[dict[str, Any]]:
    if plan.scope == RestorePlan.Scope.SNAPSHOT:
        directories = list(
            BackupSourceSnapshotDirectory.objects.filter(
                organization_id=organization_id,
                source_snapshot=snapshot,
                status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
            )
            .exclude(kopia_snapshot_id="")
            .order_by("id")
        )
        if not directories:
            raise ValidationError(
                {"source_snapshot_id": "No restorable snapshot directories found."}
            )
        return [
            {
                "source_snapshot_directory_id": directory.id,
                "selected_paths": [],
                "target_source_path": directory.source_path,
                "source_path_type": directory.path_type
                or BackupSourceSnapshotDirectory.PathType.UNKNOWN,
                "conflict_mode": plan.conflict_mode,
            }
            for directory in directories
        ]
    if plan.backup_config_dir_id is None:
        raise ValidationError(
            {"backup_config_dir_id": "Restore plan directory is required."}
        )
    _validate_plan_source_path_absolute(plan)
    directory = _snapshot_directory(
        organization_id=organization_id,
        source_snapshot_id=snapshot.id,
        backup_config_dir_id=int(plan.backup_config_dir_id),
    )
    if directory is None:
        raise ValidationError(
            {"backup_config_dir_id": "Restore plan directory snapshot not found."}
        )
    if not _same_or_ancestor_path(directory.source_path, plan.source_path):
        raise ValidationError(
            {
                "source_path": "Restore plan source path must be inside snapshot directory."
            }
        )
    _ensure_directory_usable(directory)
    selected_paths = []
    if directory.source_path != plan.source_path:
        selected_paths = [_relative_path(directory.source_path, plan.source_path)]
    return [
        {
            "source_snapshot_directory_id": directory.id,
            "selected_paths": selected_paths,
            "target_source_path": plan.source_path,
            "source_path_type": directory.path_type
            or BackupSourceSnapshotDirectory.PathType.UNKNOWN,
            "conflict_mode": plan.conflict_mode,
        }
    ]


def _finalize_restore_item_targets(
    raw_items: list[dict[str, Any]], *, restore_dir: str | None = None
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_sources: set[tuple[int, tuple[str, ...], str, str]] = set()
    natural_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in raw_items:
        source_path = _normalize_path(str(item["target_source_path"]))
        selected_paths = tuple(item.get("selected_paths") or [])
        effective_source_path = _restore_item_effective_source_path(
            source_path, selected_paths
        )
        raw_target_root = item.get("restore_dir") or restore_dir
        if raw_target_root is None or str(raw_target_root).strip() == "":
            raise ValidationError({"restore_dir": "Restore directory is required."})
        target_root = _normalize_path(str(raw_target_root))
        natural_target = (
            _join_target_path(target_root, _path_basename(effective_source_path))
            if len(selected_paths) <= 1
            else target_root
        )
        group_key = ("target", natural_target)
        source_key = (
            int(item["source_snapshot_directory_id"]),
            selected_paths,
            str(item["conflict_mode"]),
            natural_target,
        )
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        next_item = {
            "source_snapshot_directory_id": int(item["source_snapshot_directory_id"]),
            "selected_paths": list(selected_paths),
            "source_path_type": item.get("source_path_type"),
            "target_source_path": source_path,
            "effective_source_path": effective_source_path,
            "natural_target_path": natural_target,
            "dispatch_target_path": natural_target,
            "conflict_mode": str(item["conflict_mode"]),
        }
        deduped.append(next_item)
        natural_groups.setdefault(group_key, []).append(next_item)
    final_items: list[dict[str, Any]] = []
    final_targets: dict[str, tuple[int, tuple[str, ...]]] = {}
    for group in natural_groups.values():
        for item in group:
            natural_target = item["natural_target_path"]
            target_path = item["dispatch_target_path"]
            if len(group) > 1:
                target_path = _join_target_path(
                    _path_parent(natural_target),
                    _safe_restore_name(
                        item["effective_source_path"],
                        source_path_type=item.get("source_path_type"),
                        selected_paths=tuple(item["selected_paths"]),
                    ),
                )
            final_key = (
                int(item["source_snapshot_directory_id"]),
                tuple(item["selected_paths"]),
            )
            base_target_path = target_path
            existing = final_targets.get(target_path)
            counter = 2
            while existing is not None and existing != final_key:
                target_path = _numbered_restore_target_path(
                    base_target_path,
                    counter=counter,
                    source_path=item["effective_source_path"],
                    source_path_type=item.get("source_path_type"),
                    selected_paths=tuple(item["selected_paths"]),
                )
                existing = final_targets.get(target_path)
                counter += 1
            final_targets[target_path] = final_key
            final_items.append(
                {
                    "source_snapshot_directory_id": item[
                        "source_snapshot_directory_id"
                    ],
                    "selected_paths": item["selected_paths"],
                    "target_path": target_path,
                    "target_path_semantics": "final",
                    "conflict_mode": item["conflict_mode"],
                }
            )
    return final_items


def _restore_item_effective_source_path(
    source_path: str, selected_paths: tuple[str, ...]
) -> str:
    if len(selected_paths) != 1:
        return source_path
    selected_path = str(selected_paths[0] or "").strip()
    if not selected_path:
        return source_path
    if _path_basename(source_path) == _path_basename(selected_path):
        return source_path
    return _normalize_path(posixpath.join(source_path, selected_path))


def _restore_plan_item_input_or_none(
    *,
    organization_id: int,
    snapshot: BackupSourceSnapshot,
    plan: RestorePlan,
) -> list[dict[str, Any]] | None:
    try:
        return _restore_plan_item_inputs(
            organization_id=organization_id,
            snapshot=snapshot,
            plan=plan,
        )
    except ValidationError:
        return None


def _ensure_directory_usable(directory: BackupSourceSnapshotDirectory) -> None:
    if directory.status != BackupSourceSnapshotDirectory.Status.AVAILABLE:
        raise ValidationError(
            {"source_snapshot_directory_id": "Snapshot directory is not available."}
        )
    if not directory.kopia_snapshot_id:
        raise ValidationError(
            {
                "source_snapshot_directory_id": "Snapshot directory has no Kopia snapshot id."
            }
        )


def _validate_endpoint_exists(
    *, organization_id: int, endpoint_type: str, ref_id: int, field: str
) -> None:
    if endpoint_type == "agent":
        roles = [NodeRole.AGENT]
        if field == "target_ref_id":
            roles.extend([NodeRole.PROXY, NodeRole.GATEWAY])
        exists = Node.objects.filter(
            organization_id=organization_id,
            role__in=roles,
            id=ref_id,
            is_deleted=False,
        ).exists()
    else:
        exists = SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=ref_id,
            is_deleted=False,
        ).exists()
    if not exists:
        raise ValidationError({field: "Endpoint not found."})


def _lock_restore_source_endpoint(
    *, organization_id: int, source_type: str, source_ref_id: int
) -> None:
    if source_type == "agent":
        exists = (
            Node.objects.select_for_update()
            .filter(
                organization_id=organization_id,
                role=NodeRole.AGENT,
                id=source_ref_id,
                is_deleted=False,
            )
            .exists()
        )
    else:
        exists = (
            SourceResource.objects.select_for_update()
            .filter(
                organization_id=organization_id,
                resource_type=ResourceType.NAS,
                id=source_ref_id,
                is_deleted=False,
            )
            .exists()
        )
    if not exists:
        raise ValidationError({"source_ref_id": "Endpoint not found."})


def _active_restore_task_for_source(
    *, organization_id: int, source_type: str, source_ref_id: int
) -> Task | None:
    insight_restore_task_ids = (
        RestoreRecord.objects.filter(
            organization_id=organization_id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
        .order_by()
        .values("task_id")
    )
    active = (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.RESTORE,
            status__in=ACTIVE_RESTORE_TASK_STATUSES,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_subtype=source_type,
            resources__resource_id=source_ref_id,
        )
        .exclude(id__in=insight_restore_task_ids)
        .order_by("created_at", "id")
        .first()
    )
    if active is not None:
        return active
    cancelled = Task.objects.filter(
        organization_id=organization_id,
        task_type=Task.Type.RESTORE,
        status=Task.Status.CANCELLED,
        resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
        resources__resource_subtype=source_type,
        resources__resource_id=source_ref_id,
    ).exclude(id__in=insight_restore_task_ids).order_by("created_at", "id")
    for task in cancelled:
        if restore_task_is_stopping(task=task):
            return task
    return None


def _ensure_no_active_restore_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    purpose: str = RestoreRecord.Purpose.USER_DATA,
) -> None:
    from apps.source.services.internal.source_operation_fence import (
        assert_source_product_operation_allowed,
    )

    assert_source_product_operation_allowed(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if purpose == RestoreRecord.Purpose.LENS_WORKSPACE:
        return
    active_task = _active_restore_task_for_source(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if active_task is None:
        return
    active_status = (
        "stopping"
        if restore_task_is_stopping(task=active_task)
        else active_task.status
    )
    record = (
        RestoreRecord.objects.filter(
            organization_id=organization_id,
            task_id=active_task.id,
        )
        .only("id")
        .first()
    )
    raise AppError(
        code="RESTORE.ALREADY_RUNNING",
        status=409,
        title="Restore already running",
        diagnostic="A restore task is already running for this backup source.",
        retryable=False,
        meta={
            "task_uuid": str(active_task.task_uuid),
            "task_id": active_task.id,
            "restore_record_id": record.id if record is not None else None,
            "display_name": active_task.display_name,
            "status": active_status,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "created_at": active_task.created_at.isoformat()
            if active_task.created_at
            else "",
        },
    )


def _task_progress(value: int | float | Decimal) -> Decimal:
    number = Decimal(str(value)).quantize(Decimal("0.01"))
    if number < 0:
        return Decimal("0.00")
    if number > 100:
        return Decimal("100.00")
    return number


def _set_step_status(
    *,
    task: Task,
    step_name: str,
    status: str,
    progress: int | float | Decimal | None = None,
    task_progress: int | float | Decimal | None = None,
    current_step: str | None = None,
) -> None:
    step = TaskStep.objects.filter(task=task, step_name=step_name).first()
    if step is not None:
        update_fields = ["status"]
        step.status = status
        if progress is not None:
            step.progress = _task_progress(progress)
            update_fields.append("progress")
        step.save(update_fields=update_fields)
    task_updates: list[str] = []
    if current_step is not None:
        task.current_step = current_step
        task_updates.append("current_step")
    if task_progress is not None:
        task.progress = _task_progress(task_progress)
        task_updates.append("progress")
    if task_updates:
        task_updates.append("updated_at")
        task.save(update_fields=task_updates)


def _target_execution_node(
    *, organization_id: int, target_type: str, target_ref_id: int
) -> Node:
    if target_type == "agent":
        node = Node.objects.filter(
            organization_id=organization_id,
            role__in=[NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY],
            id=target_ref_id,
            is_deleted=False,
        ).first()
        if node is None:
            raise ValidationError({"target_ref_id": "Target agent not found."})
        if not node_is_available_for_work(node):
            raise ValidationError(
                {"target_ref_id": "Target agent is unavailable or busy."}
            )
        return node
    resource = (
        SourceResource.objects.filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=target_ref_id,
            is_deleted=False,
        )
        .select_related("bound_node")
        .first()
    )
    if resource is None:
        raise ValidationError({"target_ref_id": "Target NAS not found."})
    if resource.bound_node is None or resource.bound_node.role != NodeRole.PROXY:
        raise ValidationError(
            {"target_ref_id": "Target NAS is not bound to a proxy node."}
        )
    if resource.availability != "online" or not node_is_available_for_work(
        resource.bound_node
    ):
        raise ValidationError(
            {"target_ref_id": "Target NAS or proxy node is unavailable or busy."}
        )
    return resource.bound_node


def _dispatch_restore_items(
    *, organization_id: int, record: RestoreRecord, task: Task
) -> None:
    managed_workspace_payload: dict[str, Any] = {}
    if record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE:
        if record.workspace_binding_id is None:
            raise ValidationError(
                {
                    "workspace_binding_id": "Lens workspace restore has no execution binding."
                }
            )
        tenant_organization = Organization.objects.filter(
            pk=record.organization_id
        ).first()
        if tenant_organization is None:
            raise ValidationError(
                {"organization_id": "Restore organization is not available."}
            )
        from apps.lens_bridge.services.gateway_execution import (
            context_for_workspace_binding,
            workspace_identity_payload,
        )

        execution_context, workspace_binding = context_for_workspace_binding(
            tenant_organization=tenant_organization,
            workspace_binding_id=record.workspace_binding_id,
        )
        if (
            record.target_execution_organization_id
            != execution_context.execution_organization.id
            or record.target_execution_node_id != execution_context.gateway.id
            or record.target_path != workspace_binding.resolved_path()
            or any(
                not _same_or_ancestor_path(record.target_path, item_path)
                for item_path in record.items.values_list("target_path", flat=True)
            )
        ):
            raise ValidationError(
                {
                    "workspace_binding_id": "Restore execution no longer matches its workspace binding."
                }
            )
        managed_workspace_payload = {
            "managed_workspace_path": workspace_binding.resolved_path(),
            "insight_content_policy": INSIGHT_SAFE_CONTENT_POLICY,
            **workspace_identity_payload(workspace_binding),
        }
    node = Node.objects.filter(
        organization_id=record.target_execution_organization_id,
        id=record.target_execution_node_id,
        is_deleted=False,
    ).first()
    if node is None:
        raise ValidationError({"target_ref_id": "Restore execution node not found."})
    if not node_is_available_for_work(node):
        raise ValidationError(
            {"target_ref_id": "Restore execution node is unavailable or busy."}
        )
    if (
        record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE
        and missing_node_capabilities(node, [INSIGHT_SAFE_RESTORE_CAPABILITY])
    ):
        raise ValidationError(
            {
                "target_ref_id": (
                    "Upgrade the selected Data Gateway Agent before creating "
                    "a Chat from backup data."
                )
            }
        )
    target_nas_payload: dict[str, Any] = {}
    if record.target_type == RestoreRecord.EndpointType.NAS:
        target_nas = SourceResource.objects.filter(
            organization_id=record.organization_id,
            resource_type=ResourceType.NAS,
            id=record.target_ref_id,
            bound_node_id=node.id,
            is_deleted=False,
        ).first()
        if target_nas is None:
            raise ValidationError(
                {"target_ref_id": "Restore target NAS is not available."}
            )
        from apps.source.services.internal.nas_agent import nas_payload_for_resource

        target_nas_payload = {"nas": nas_payload_for_resource(target_nas)}
    all_items = list(record.items.all())
    if record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE and any(
        item.node_task_id is not None
        and item.status
        in {RestoreRecordItem.Status.PENDING, RestoreRecordItem.Status.RUNNING}
        for item in all_items
    ):
        # The completion callback and periodic reconciler can both request a
        # continuation.  Keep this guard at the shared dispatch boundary so a
        # second caller cannot start the next directory while one is active.
        logger.info(
            "restore dispatch skipped restore_record_id=%s task_uuid=%s "
            "reason=insight_item_active",
            record.id,
            record.task_uuid,
        )
        return
    items = [
        item
        for item in all_items
        if item.node_task_id is None
        and item.status
        in {RestoreRecordItem.Status.PENDING, RestoreRecordItem.Status.RUNNING}
    ]
    if record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE:
        # One Chat is one Gateway scheduling unit. Dispatching every selected
        # directory at once would let a single Chat bypass the Gateway limit.
        items = items[:1]
    if not items:
        logger.info(
            "restore dispatch skipped restore_record_id=%s task_uuid=%s reason=no_pending_items",
            record.id,
            record.task_uuid,
        )
        return
    if task.status == Task.Status.PENDING:
        task = start_task(task_uuid=task.task_uuid, organization_id=organization_id)
    _set_step_status(
        task=task,
        step_name="prepare_restore",
        status=TaskStep.Status.SUCCESS,
        progress=100,
        task_progress=RESTORE_PREPARE_END,
        current_step="dispatch_agent",
    )
    _set_step_status(
        task=task,
        step_name="dispatch_agent",
        status=TaskStep.Status.RUNNING,
        progress=10,
        task_progress=RESTORE_PREPARE_END,
    )
    repositories = {
        repository.id: repository
        for repository in Repository.objects.filter(
            organization_id=organization_id,
            id__in=[item.repository_id for item in items],
        )
    }
    RestoreRecordItem.objects.filter(id__in=[item.id for item in items]).update(
        status=RestoreRecordItem.Status.RUNNING
    )
    logger.info(
        "restore dispatch started restore_record_id=%s restore_uid=%s task_uuid=%s item_count=%s %s",
        record.id,
        record.restore_uid,
        record.task_uuid,
        len(items),
        task_log_context(
            node_id=node.id,
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
        ),
    )
    try:
        snapshot_directories = {
            directory.id: directory
            for directory in BackupSourceSnapshotDirectory.objects.filter(
                organization_id=organization_id,
                id__in=[item.source_snapshot_directory_id for item in items],
            )
        }
        for item in items:
            repository = repositories.get(item.repository_id)
            if repository is None:
                raise ValidationError({"repository_id": "Repository not found."})
            snapshot_directory = snapshot_directories.get(
                item.source_snapshot_directory_id
            )
            source_path_type = (
                snapshot_directory.path_type
                if snapshot_directory is not None
                else BackupSourceSnapshotDirectory.PathType.UNKNOWN
            )
            if snapshot_directory is None:
                raise ValidationError(
                    {"source_snapshot_directory_id": "Snapshot directory not found."}
                )
            repository_access = resolve_snapshot_repository_reader(
                directory=snapshot_directory,
                repository=repository,
                fallback_node=node,
                source_type=record.target_type,
                source_ref_id=record.target_ref_id,
            )
            repository_payload = repository_access.repository_payload
            restore_transfer_mode = (
                "direct_proxy_restore"
                if repository_access.mode == "bound_proxy"
                else "direct_restore"
            )
            if repository_access.node.id != node.id:
                repository_payload = _ensure_restore_repository_server_payload(
                    task=task,
                    repository=repository,
                    repository_node=repository_access.node,
                    repository_payload=repository_access.repository_payload,
                    server_username=_restore_repository_server_username(
                        organization_id=organization_id,
                        item=item,
                        repository_node=repository_access.node,
                        task=task,
                    ),
                )
                if repository_payload is None:
                    return
                restore_transfer_mode = "proxy_repository_server_restore"
            elif (
                repository.repo_type == Repository.Type.NAS
                and repository.bind_node_type != Repository.BindNodeType.PROXY
                and repository_access.mode == "fallback_node"
                and repository_payload.get("subdir")
                != nas_agent_repository_subdir(node.id)
            ):
                repository_payload = dict(repository_payload)
                nas_payload = dict(repository_payload.get("nas") or {})
                nas_payload["mount_point"] = nas_restore_mount_point(
                    repository,
                    node_id=node.id,
                )
                repository_payload["nas"] = nas_payload
            payload = {
                "restore_record_id": record.id,
                "restore_record_item_id": item.id,
                "snapshot_id": item.kopia_snapshot_id,
                "kopia_snapshot_id": item.kopia_snapshot_id,
                "target_path": item.target_path,
                "path": item.target_path,
                "target_path_semantics": "final",
                "selected_paths": item.selected_paths,
                "conflict_mode": item.conflict_mode,
                "source_path": item.source_path,
                "source_path_type": source_path_type,
                "path_type": source_path_type,
                "repository_id": item.repository_id,
                "repository": repository_payload,
                "repository_reader_node_id": repository_access.node.id,
                "restore_transfer_mode": restore_transfer_mode,
                # Restore only reads the snapshot repository. Do not apply
                # the ownership gate used by repository initialization.
                "probe": "restore_execution",
                "skip_ownership_check": True,
                **target_nas_payload,
                **managed_workspace_payload,
            }
            persisted_payload = payload
            if target_nas_payload:
                from apps.source.services.internal.source_credentials import (
                    scrub_source_secrets,
                )

                persisted_payload = {
                    **payload,
                    "nas": scrub_source_secrets(payload["nas"]),
                }
            from apps.restore.services.direct_nas_mounts import (
                acquire_for_restore,
                requires_lease_for_restore,
            )

            # Keep the durable lease, NodeTask and item projection in one
            # transaction. Agent delivery is registered with on_commit(), so
            # the restore cannot start before its control-plane lease exists.
            lease_required = requires_lease_for_restore(
                record=record,
                repository=repository,
                reader_node_id=repository_access.node.id,
                access_mode=repository_access.mode,
                repository_payload=repository_payload,
            )
            dispatch_scope = transaction.atomic() if lease_required else nullcontext()
            with dispatch_scope:
                handle = run_agent_task_async(
                    organization_id=record.target_execution_organization_id,
                    node_id=node.id,
                    kind="restore.run",
                    payload=payload,
                    persisted_payload=(
                        persisted_payload if target_nas_payload else None
                    ),
                    correlation_type="restore.record",
                    correlation_id=str(record.task_uuid),
                    requesting_organization_id=record.requesting_organization_id,
                )
                if lease_required:
                    acquire_for_restore(
                        record=record,
                        repository=repository,
                        reader_node_id=repository_access.node.id,
                        access_mode=repository_access.mode,
                        repository_payload=repository_payload,
                    )
                log_agent_dispatch(
                    "restore item",
                    node_id=node.id,
                    kind="restore.run",
                    correlation_type="restore.record",
                    correlation_id=str(record.task_uuid),
                    restore_record_item_id=item.id,
                    node_task_id=str(handle.task.id),
                    kopia_snapshot_id=item.kopia_snapshot_id,
                )
                item.node_task_id = handle.task.id
                item.status = RestoreRecordItem.Status.RUNNING
                item.save(update_fields=["node_task_id", "status", "updated_at"])
                append_task_step_event(
                    task=task,
                    step_name="dispatch_agent",
                    message="Restore item dispatched to agent",
                    metadata={
                        "restore_record_item_id": item.id,
                        "node_task_id": str(handle.task.id),
                        "target_node_id": node.id,
                        "repository_reader_node_id": repository_access.node.id,
                        "restore_transfer_mode": payload["restore_transfer_mode"],
                        "kopia_snapshot_id": item.kopia_snapshot_id,
                        "source_path": item.source_path,
                        "target_path": item.target_path,
                        "object_id": item.kopia_snapshot_id,
                        "object_name": item.source_path,
                    },
                )
        _set_step_status(
            task=task,
            step_name="dispatch_agent",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=RESTORE_ESTIMATE_END,
            current_step="restore",
        )
        _set_step_status(
            task=task,
            step_name="restore",
            status=TaskStep.Status.RUNNING,
            progress=10,
            task_progress=RESTORE_ESTIMATE_END,
        )
        append_restore_execution_started_event(
            task=task,
            record=record,
            items=items,
        )
        logger.info(
            "restore dispatch ok restore_record_id=%s task_uuid=%s item_count=%s target_node_id=%s",
            record.id,
            record.task_uuid,
            len(items),
            node.id,
        )
    except Exception as exc:
        error_message = str(exc)[:2000]
        log_agent_exception(
            "restore dispatch",
            node_id=node.id,
            kind="restore.run",
            exc=exc,
            correlation_type="restore.record",
            correlation_id=str(record.task_uuid),
            restore_record_id=record.id,
        )
        RestoreRecordItem.objects.filter(
            restore_record=record, status=RestoreRecordItem.Status.RUNNING
        ).update(
            status=RestoreRecordItem.Status.FAILED,
            error_code="RESTORE_DISPATCH_FAILED",
            error_message=error_message,
            terminal_projection_at=timezone.now(),
        )
        if record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE:
            RestoreRecordItem.objects.filter(
                restore_record=record,
                node_task_id__isnull=True,
                status=RestoreRecordItem.Status.PENDING,
            ).update(
                status=RestoreRecordItem.Status.SKIPPED,
                error_code="RESTORE_PREVIOUS_ITEM_FAILED",
                error_message=(
                    "A previous Chat workspace restore item did not complete."
                ),
                terminal_projection_at=timezone.now(),
            )
        append_task_step_event(
            task=task,
            step_name="dispatch_agent",
            level=TaskEvent.Level.ERROR,
            message="Restore dispatch failed",
            metadata={
                "error_code": "RESTORE_DISPATCH_FAILED",
                "error_message": error_message,
                "object_name": record.restore_uid,
            },
        )
        _set_step_status(
            task=task,
            step_name="dispatch_agent",
            status=TaskStep.Status.FAILED,
            progress=100,
            task_progress=RESTORE_ESTIMATE_END,
            current_step="dispatch_agent",
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.FAILED,
            progress=45,
            result_payload={"restore_record_id": record.id},
            error_code="RESTORE_DISPATCH_FAILED",
            error_message=error_message,
        )
        from apps.restore.services.direct_nas_mounts import release_for_record

        release_for_record(record=record)
        stop_restore_repository_servers(task=task)
        raise


def _task_result_payload(task: Task) -> dict[str, Any]:
    return dict(task.result_payload) if isinstance(task.result_payload, dict) else {}


def _save_task_result_payload(task: Task, payload: dict[str, Any]) -> None:
    task.result_payload = payload
    task.save(update_fields=["result_payload", "updated_at"])


def _restore_repository_servers(task: Task) -> list[dict[str, Any]]:
    servers = _task_result_payload(task).get("repository_servers")
    return (
        [server for server in servers if isinstance(server, dict)]
        if isinstance(servers, list)
        else []
    )


def _repository_public_host(*, repository: Repository, node: Node) -> tuple[str, str]:
    return explicit_repository_server_host(repository=repository, node=node)


def _repository_server_payload_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": result.get("repository_id"),
        "type": "kopia_server",
        "url": str(result.get("server_url") or result.get("url") or "").strip(),
        "username": str(result.get("username") or "").strip(),
        "password": str(result.get("password") or "").strip(),
        "server_cert_fingerprint": str(
            result.get("server_cert_fingerprint") or ""
        ).strip(),
        "kopia_password": str(result.get("kopia_password") or "").strip(),
        "session_id": str(result.get("session_id") or "").strip(),
    }


def _restore_repository_server_username(
    *,
    organization_id: int,
    item: RestoreRecordItem,
    repository_node: Node,
    task: Task,
) -> str:
    directory = BackupSourceSnapshotDirectory.objects.filter(
        organization_id=organization_id,
        id=item.source_snapshot_directory_id,
    ).first()
    if directory is not None and directory.node_task_id:
        node_task = NodeTask.objects.filter(
            organization_id=organization_id,
            id=directory.node_task_id,
        ).first()
        if node_task is not None:
            username = _snapshot_source_server_username(node_task.result)
            if username:
                return username
    return f"hfl-restore-{task.id}@hfl-proxy-{repository_node.id}".lower()


def _snapshot_source_server_username(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    snapshot = result.get("snapshot")
    if not isinstance(snapshot, dict):
        return ""
    source = snapshot.get("source")
    if not isinstance(source, dict):
        return ""
    user = str(
        source.get("userName")
        or source.get("username")
        or source.get("user_name")
        or ""
    ).strip()
    host = str(source.get("host") or source.get("hostname") or "").strip()
    if not user or not host:
        return ""
    return f"{user}@{host}".lower()


def _restore_repository_server_session_id(
    *, task: Task, repository: Repository, server_username: str
) -> str:
    username_hash = hashlib.sha256(server_username.encode("utf-8")).hexdigest()[:12]
    return f"restore-{task.task_uuid}-repo-{repository.id}-{username_hash}"


def _ensure_restore_repository_server_payload(
    *,
    task: Task,
    repository: Repository,
    repository_node: Node,
    repository_payload: dict[str, Any],
    server_username: str,
) -> dict[str, Any] | None:
    if not protection_conf.PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED:
        raise ValidationError(
            {
                "target_ref_id": (
                    "Cross-node restore from a Proxy-bound repository requires proxy repository server mode, "
                    "but PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED is disabled."
                )
            }
        )
    for state in _restore_repository_servers(task):
        if int(state.get("repository_id") or 0) != int(repository.id):
            continue
        if str(state.get("server_username") or "").strip() != server_username:
            continue
        node_task_id = str(state.get("node_task_id") or "").strip()
        node_task = (
            NodeTask.objects.filter(
                organization_id=task.organization_id,
                id=node_task_id,
            ).first()
            if node_task_id
            else None
        )
        if node_task is None:
            return None
        if node_task.status == NodeTask.Status.SUCCESS:
            result = node_task.result if isinstance(node_task.result, dict) else {}
            result["kopia_password"] = str(
                repository_payload.get("kopia_password") or ""
            )
            result["repository_id"] = repository.id
            result["repository_node_id"] = repository_node.id
            result["node_task_id"] = str(node_task.id)
            result["status"] = node_task.status
            result["session_id"] = str(
                result.get("session_id") or state.get("session_id") or ""
            )
            result["server_username"] = server_username
            result["public_host"] = str(state.get("public_host") or "")
            result["public_host_source"] = str(state.get("public_host_source") or "")
            payload = _repository_server_payload_from_result(result)
            if payload["url"] and payload["username"] and payload["password"]:
                task_payload = _task_result_payload(task)
                servers = [
                    result
                    if int(existing.get("repository_id") or 0) == int(repository.id)
                    else existing
                    for existing in _restore_repository_servers(task)
                ]
                task_payload["repository_servers"] = servers
                _save_task_result_payload(task, task_payload)
                return payload
            raise ValidationError(
                {
                    "target_ref_id": "Restore repository server start returned incomplete connection info."
                }
            )
        if node_task.status in {
            NodeTask.Status.FAILED,
            NodeTask.Status.TIMEOUT,
            NodeTask.Status.CANCELED,
        }:
            message = str(node_task.last_error or "").strip()
            result = node_task.result if isinstance(node_task.result, dict) else {}
            diagnostic_code = repository_server_diagnostic_code(result, message)
            message = repository_server_public_error_message(diagnostic_code) or message
            if not message:
                message = str(result.get("error") or "").strip()
            raise ValidationError(
                {
                    "target_ref_id": (
                        message
                        or f"Restore repository server start failed: node_task_id={node_task.id}, status={node_task.status}."
                    )
                }
            )
        return None

    public_host, public_host_source = _repository_public_host(
        repository=repository, node=repository_node
    )
    if not public_host:
        raise ValidationError(
            {
                "target_ref_id": "Proxy node has no reachable IP address for restore repository server."
            }
        )
    session_id = _restore_repository_server_session_id(
        task=task,
        repository=repository,
        server_username=server_username,
    )
    password = secrets.token_urlsafe(24)
    handle = run_agent_task_async(
        organization_id=task.organization_id,
        node_id=repository_node.id,
        kind="repository.server.start",
        payload={
            "session_id": session_id,
            "username": server_username,
            "password": password,
            "public_host": public_host,
            "public_host_source": public_host_source,
            "repository": repository_payload,
        },
        correlation_type="restore.repository_server",
        correlation_id=str(task.task_uuid),
    )
    task_payload = _task_result_payload(task)
    servers = _restore_repository_servers(task)
    servers.append(
        {
            "repository_id": repository.id,
            "repository_node_id": repository_node.id,
            "node_task_id": str(handle.task.id),
            "status": handle.task.status,
            "session_id": session_id,
            "server_username": server_username,
            "public_host": public_host,
            "public_host_source": public_host_source,
        }
    )
    task_payload["repository_servers"] = servers
    _save_task_result_payload(task, task_payload)
    append_task_step_event(
        task=task,
        step_name="dispatch_agent",
        message="Restore repository server started",
        metadata={
            "repository_id": repository.id,
            "repository_node_id": repository_node.id,
            "node_task_id": str(handle.task.id),
            "session_id": session_id,
            "server_username": server_username,
            "public_host": public_host,
            "public_host_source": public_host_source,
            "object_name": repository.name,
        },
    )
    return None


def stop_restore_repository_servers(*, task: Task) -> None:
    servers = _restore_repository_servers(task)
    if not servers:
        return
    for state in servers:
        session_id = str(state.get("session_id") or "").strip()
        node_id = int(state.get("repository_node_id") or 0)
        if not session_id or not node_id:
            continue
        # A retrying projector must not schedule duplicate cleanup commands.
        # Agent delivery itself is registered with transaction.on_commit(), so
        # a surrounding restore-finalization rollback also rolls back this
        # durable command before it can be sent.
        if NodeTask.objects.filter(
            organization_id=task.organization_id,
            kind="repository.server.stop",
            correlation_type="restore.repository_server",
            correlation_id=str(task.task_uuid),
            payload__session_id=session_id,
        ).exists():
            continue
        try:
            run_agent_task_async(
                organization_id=task.organization_id,
                node_id=node_id,
                kind="repository.server.stop",
                payload={"session_id": session_id},
                correlation_type="restore.repository_server",
                correlation_id=str(task.task_uuid),
            )
        except Exception:
            logger.exception(
                "failed to dispatch restore repository server cleanup task_uuid=%s",
                task.task_uuid,
            )


@transaction.atomic
def cancel_restore(
    *, organization_id: int, task_uuid: str, reason: str = ""
) -> dict[str, Any]:
    task = (
        Task.objects.select_for_update()
        .filter(
            organization_id=organization_id,
            task_uuid=task_uuid,
            task_type__in=RESTORE_TASK_TYPES,
        )
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    if task.status in _TASK_TERMINAL:
        return {
            "task_uuid": str(task.task_uuid),
            "status": task.status,
            "restore_record_id": RestoreRecord.objects.filter(
                organization_id=organization_id,
                task_uuid=task.task_uuid,
            )
            .values_list("id", flat=True)
            .first(),
        }

    record = (
        RestoreRecord.objects.filter(
            organization_id=organization_id,
            task_uuid=task.task_uuid,
        )
        .prefetch_related("items")
        .first()
    )
    message = str(reason or "Stopped from console").strip() or "Stopped from console"

    # Publish the product-level cancellation while holding its row lock before
    # a pending NodeTask can synchronously project a terminal Agent result.
    # This makes late or immediate Agent callbacks cancellation-only.
    if record is not None:
        normalize_restore_task_type(record=record, task=task)
    task = finalize_task_cancellation(task=task, reason=message)

    if record is not None:
        for item in record.items.all():
            if item.status in {
                RestoreRecordItem.Status.SUCCESS,
                RestoreRecordItem.Status.FAILED,
                RestoreRecordItem.Status.SKIPPED,
                RestoreRecordItem.Status.CANCELLED,
            }:
                continue
            previous_status = item.status
            if item.node_task_id:
                try:
                    cancel_agent_task(task_id=item.node_task_id, reason=message)
                except Exception:
                    logger.exception(
                        "failed to dispatch restore item cancel node_task_id=%s task_uuid=%s",
                        item.node_task_id,
                        task.task_uuid,
                    )
            item.status = RestoreRecordItem.Status.CANCELLED
            item.error_code = "TASK_CANCELLED"
            item.error_message = message
            item.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            append_restore_item_terminal_event(
                task=task,
                item=item,
                node_task_id=item.node_task_id,
                previous_status=previous_status,
            )
            item.terminal_projection_at = timezone.now()
            item.save(update_fields=["terminal_projection_at", "updated_at"])

    execution_org_ids = {organization_id}
    if record is not None:
        execution_org_ids.add(record.target_execution_organization_id)
    for node_task in NodeTask.objects.filter(
        organization_id__in=execution_org_ids,
        correlation_id=str(task.task_uuid),
        status__in=_ACTIVE_NODE_STATUSES,
    ):
        try:
            cancel_agent_task(task_id=node_task.id, reason=message)
        except Exception:
            logger.exception(
                "failed to dispatch restore node cancel node_task_id=%s task_uuid=%s",
                node_task.id,
                task.task_uuid,
            )

    if record is not None:
        from apps.restore.services.direct_nas_mounts import release_for_record

        release_for_record(record=record)
    stop_restore_repository_servers(task=task)

    append_task_step_event(
        task=task,
        step_name="restore",
        level=TaskEvent.Level.WARN,
        message="Restore stopped by user",
        metadata={
            "reason": message,
            "restore_record_id": record.id if record else None,
            "object_name": record.restore_uid if record else task.display_name,
        },
    )
    for step_name in ("restore", "finalize"):
        _set_step_status(
            task=task,
            step_name=step_name,
            status=TaskStep.Status.SKIPPED,
        )

    task.refresh_from_db()
    return {
        "task_uuid": str(task.task_uuid),
        "status": task.status,
        "restore_record_id": record.id if record else None,
    }


def restore_task_is_stopping(*, task: Task) -> bool:
    if task.status != Task.Status.CANCELLED:
        return False
    record = RestoreRecord.objects.filter(
        organization_id=task.organization_id,
        task_uuid=task.task_uuid,
    ).first()
    organization_ids = {task.organization_id}
    if record is not None:
        organization_ids.add(record.target_execution_organization_id)
    return NodeTask.objects.filter(
        organization_id__in=organization_ids,
        correlation_id=str(task.task_uuid),
        status__in=_ACTIVE_NODE_STATUSES,
    ).exists()


def _choice(
    data: dict[str, Any], key: str, choices: set[str], default: str | None = None
) -> str:
    raw = data.get(key, default)
    value = str(raw or "").strip().lower()
    if value not in choices:
        raise ValidationError({key: f"Must be one of: {', '.join(sorted(choices))}."})
    return value


def _int(
    data: dict[str, Any],
    key: str,
    default: int | None = None,
    *,
    min_value: int = 1,
) -> int:
    raw = data.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({key: f"{key} must be an integer."}) from exc
    if value < min_value:
        message = (
            "Must be a non-negative integer."
            if min_value == 0
            else "Must be a positive integer."
        )
        raise ValidationError({key: message})
    return value


def _path(data: dict[str, Any], key: str, default: str | None = None) -> str:
    raw = data.get(key, default)
    value = str(raw or "").strip()
    if not value:
        raise ValidationError({key: "Path is required."})
    return _normalize_path(value)


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    raw = data.get(key)
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError({key: f"{key} must be an integer."}) from exc
    if value <= 0:
        raise ValidationError({key: "Must be a positive integer."})
    return value


def _normalize_path(path: str) -> str:
    return posixpath.normpath(str(path or "").strip() or "/")


def _is_absolute_source_path(path: str) -> bool:
    clean_path = str(path or "").strip()
    if not clean_path:
        return False
    if _is_windows_path(clean_path):
        return ntpath.isabs(clean_path)
    return posixpath.isabs(clean_path)


def _is_windows_path(path: str) -> bool:
    return "\\" in path or (len(path) >= 2 and path[1] == ":")


def _path_parent(path: str) -> str:
    if _is_windows_path(str(path)):
        return ntpath.dirname(path)
    return posixpath.dirname(path)


def _path_basename(path: str) -> str:
    raw = str(path or "").strip().rstrip("/\\")
    if _is_windows_path(raw):
        return ntpath.basename(ntpath.normpath(raw)) or "snapshot"
    return posixpath.basename(_normalize_path(raw)) or "snapshot"


def _join_target_path(parent: str, leaf: str) -> str:
    if _is_windows_path(str(parent)) or _is_windows_path(str(leaf)):
        return ntpath.normpath(ntpath.join(str(parent or ""), str(leaf or "")))
    return _normalize_path(posixpath.join(_normalize_path(parent), leaf))


def _safe_restore_name(
    source_path: str,
    *,
    source_path_type: str | None = None,
    selected_paths: tuple[str, ...] = (),
) -> str:
    parts = _restore_path_parts(source_path)
    basename = parts[-1] if parts else "snapshot"
    parent_parts = parts[:-1]
    if _restore_source_is_file_like(
        source_path=source_path,
        source_path_type=source_path_type,
        selected_paths=selected_paths,
    ):
        stem, ext = posixpath.splitext(basename)
        display_name = _restore_slug(stem or basename) or "snapshot"
        source_slug = _restore_slug("/".join(parent_parts)) or "root"
        return f"{display_name}--from-{source_slug}{ext}"

    display_name = _restore_slug(basename) or "snapshot"
    source_slug = (
        _restore_slug("/".join(parent_parts))
        or _restore_slug("/".join(parts))
        or "root"
    )
    return f"{display_name}--from-{source_slug}"


def _numbered_restore_target_path(
    target_path: str,
    *,
    counter: int,
    source_path: str,
    source_path_type: str | None = None,
    selected_paths: tuple[str, ...] = (),
) -> str:
    parent = _path_parent(target_path)
    leaf = (
        ntpath.basename(target_path)
        if _is_windows_path(target_path)
        else posixpath.basename(target_path)
    )
    ext = _restore_file_extension(
        source_path=source_path,
        source_path_type=source_path_type,
        selected_paths=selected_paths,
    )
    if ext and leaf.endswith(ext):
        leaf = f"{leaf[: -len(ext)]}-{counter}{ext}"
    else:
        leaf = f"{leaf}-{counter}"
    return _join_target_path(parent, leaf)


def _restore_path_parts(path: str) -> list[str]:
    raw = str(path or "").strip().replace("\\", "/")
    if len(raw) >= 2 and raw[1] == ":":
        raw = raw[2:]
    return [part for part in raw.strip("/").split("/") if part not in ("", ".")]


def _restore_source_is_file_like(
    *,
    source_path: str,
    source_path_type: str | None = None,
    selected_paths: tuple[str, ...] = (),
) -> bool:
    selected_parts = _restore_path_parts(selected_paths[-1]) if selected_paths else []
    selected_leaf = selected_parts[-1] if selected_parts else ""
    if selected_leaf and posixpath.splitext(selected_leaf)[1]:
        return True
    if source_path_type == BackupSourceSnapshotDirectory.PathType.FILE:
        return True
    if source_path_type == BackupSourceSnapshotDirectory.PathType.DIRECTORY:
        return False
    parts = _restore_path_parts(source_path)
    return bool(parts and posixpath.splitext(parts[-1])[1])


def _restore_file_extension(
    *,
    source_path: str,
    source_path_type: str | None = None,
    selected_paths: tuple[str, ...] = (),
) -> str:
    if not _restore_source_is_file_like(
        source_path=source_path,
        source_path_type=source_path_type,
        selected_paths=selected_paths,
    ):
        return ""
    parts = _restore_path_parts(source_path)
    if not parts:
        return ""
    return posixpath.splitext(parts[-1])[1]


def _restore_slug(value: str) -> str:
    result: list[str] = []
    previous_underscore = False
    for ch in str(value or ""):
        next_ch = ch if ch.isalnum() or ch in ".-" else "_"
        if next_ch == "_":
            if previous_underscore:
                continue
            previous_underscore = True
        else:
            previous_underscore = False
        result.append(next_ch)
    return "".join(result).strip("_")


def _same_or_ancestor_path(ancestor_path: str, child_path: str) -> bool:
    if _is_windows_path(ancestor_path) or _is_windows_path(child_path):
        ancestor = ntpath.normcase(ntpath.normpath(ancestor_path).rstrip("\\/"))
        child = ntpath.normcase(ntpath.normpath(child_path).rstrip("\\/"))
        return child == ancestor or child.startswith(ancestor + "\\")
    ancestor = posixpath.normpath(ancestor_path).rstrip("/") or "/"
    child = posixpath.normpath(child_path).rstrip("/") or "/"
    return child == ancestor or child.startswith(ancestor + "/")


def _relative_path(ancestor_path: str, child_path: str) -> str:
    if _is_windows_path(ancestor_path) or _is_windows_path(child_path):
        relative = ntpath.relpath(
            ntpath.normpath(child_path), ntpath.normpath(ancestor_path)
        )
        return "" if relative == "." else relative
    relative = posixpath.relpath(
        posixpath.normpath(child_path), posixpath.normpath(ancestor_path)
    )
    return "" if relative == "." else relative


def _selected_paths(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValidationError({"selected_paths": "selected_paths must be a list."})
    result: list[str] = []
    for value in raw:
        path = str(value or "").strip()
        if not path:
            raise ValidationError({"selected_paths": "selected path cannot be empty."})
        normalized = posixpath.normpath(path)
        if (
            normalized.startswith("/")
            or normalized == ".."
            or normalized.startswith("../")
        ):
            raise ValidationError(
                {"selected_paths": "selected path must be relative to source_path."}
            )
        result.append(normalized)
    return result


__all__ = [
    "create_manual_restore_record",
    "create_restore_plan",
    "delete_restore_plan",
    "run_restore_plan",
    "run_restore_plan_batch",
    "update_restore_plan",
]
