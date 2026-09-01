"""Unified async sync pipeline for Knowledge Sources (Add = first Sync, Sync = resume/rerun)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import (
    gateway_readiness,
    ingest_policy,
    managed_datasource,
    provisioning,
    sl_client,
)
from apps.lens_bridge.services.gateway_execution import context_for_knowledge_source
from apps.node.models.node import Node
from apps.node.services.internal.node_workload import get_node_workload_blockers
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.restore.services import interface as restore_services
from apps.task.models import Task

logger = logging.getLogger(__name__)

SYNC_PHASES = (
    "prepare_workspace",
    "restore_snapshot",
    "ensure_managed_datasource",
    "convert_documents",
    "push_assistant",
    "finalize",
)

_PHASE_LABELS = {
    "prepare_workspace": "Preparing workspace on gateway…",
    "restore_snapshot": "Restoring snapshot data…",
    "ensure_managed_datasource": "Preparing document conversion…",
    "convert_documents": "Extracting document content…",
    "push_assistant": "Syncing linked Assistant configuration…",
    "finalize": "Finalizing…",
}

_RESTORE_POLL_SECONDS = 5
SYNC_CLAIM_TTL_SECONDS = int(getattr(settings, "LENS_KS_SYNC_TIME_LIMIT", 7200)) + 300
_TERMINAL_TASK_STATUSES = frozenset(
    {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }
)


class KnowledgeSourceSyncError(Exception):
    """Non-validation failure during knowledge source sync."""


class KnowledgeSourceSyncPending(Exception):
    """External restore work is active; persist state and resume later."""

    def __init__(
        self, detail: str, *, retry_after_seconds: int = _RESTORE_POLL_SECONDS
    ):
        super().__init__(detail)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


@transaction.atomic
def _claim_sync(
    *,
    organization_id: int,
    knowledge_source_id: int,
) -> tuple[str | None, str]:
    """Atomically claim one due Knowledge Source sync execution."""

    now = timezone.now()
    ks = (
        LensKnowledgeSource.all_objects.select_for_update()
        .filter(
            organization_id=organization_id,
            pk=knowledge_source_id,
        )
        .first()
    )
    if ks is None:
        return None, "missing"
    if ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY:
        return None, "inactive"
    if ks.status != LensKnowledgeSource.Status.SYNCING:
        return None, str(ks.status)
    if ks.sync_next_poll_at and ks.sync_next_poll_at > now:
        return None, "scheduled"
    stale_before = now - timedelta(seconds=SYNC_CLAIM_TTL_SECONDS)
    if ks.sync_claimed_at and ks.sync_claimed_at > stale_before:
        return None, "busy"

    claim_token = uuid.uuid4()
    ks.sync_claim_token = claim_token
    ks.sync_claimed_at = now
    ks.sync_next_poll_at = None
    ks.save(
        update_fields=[
            "sync_claim_token",
            "sync_claimed_at",
            "sync_next_poll_at",
            "updated_at",
        ]
    )
    return str(claim_token), "claimed"


def _release_sync_claim(
    *,
    knowledge_source_id: int,
    claim_token: str,
    next_poll_at: datetime | None = None,
) -> None:
    """Release a sync lease without overwriting a successor's claim."""

    LensKnowledgeSource.all_objects.filter(
        pk=knowledge_source_id,
        sync_claim_token=claim_token,
    ).update(
        sync_claim_token=None,
        sync_claimed_at=None,
        sync_next_poll_at=next_poll_at,
        updated_at=timezone.now(),
    )


def _require_active_lifecycle(ks: LensKnowledgeSource) -> None:
    ks.refresh_from_db(fields=["lifecycle_status"])
    if ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY:
        raise KnowledgeSourceSyncError("Knowledge source deletion was requested.")


def is_gateway_local_ks(ks: LensKnowledgeSource) -> bool:
    return not ks.backup_source_snapshot_id and not ks.backup_snapshot_directory_id


def managed_conversion_enabled(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
) -> bool:
    """Return whether this managed restore requests any conversion work."""

    policy = ingest_policy.normalize_ingest_policy(
        ks.ingest_policy_json,
    )
    return any(bool(policy.get(key)) for key in ("document", "image", "embedded_image"))


def scope_entries(ks: LensKnowledgeSource) -> list[dict[str, Any]]:
    rows = [
        item
        for item in (ks.source_scopes_json or [])
        if isinstance(item, dict) and str(item.get("source_path") or "").strip()
    ]
    if rows:
        return rows
    if ks.source_path:
        row: dict[str, Any] = {"source_path": ks.source_path.strip()}
        if ks.backup_snapshot_directory_id:
            row["backup_snapshot_directory_id"] = ks.backup_snapshot_directory_id
        return [row]
    return []


def _path_parts(path: str) -> list[str]:
    raw = str(path or "").strip().replace("\\", "/")
    if len(raw) >= 2 and raw[1] == ":":
        raw = raw[2:]
    return [part for part in raw.strip("/").split("/") if part not in ("", ".")]


def _common_path_parts(paths: list[str]) -> list[str]:
    parts_list = [_path_parts(path) for path in paths if str(path).strip()]
    if not parts_list:
        return []
    common: list[str] = []
    for index in range(min(len(parts) for parts in parts_list)):
        token = parts_list[0][index].lower()
        if any(parts[index].lower() != token for parts in parts_list):
            break
        common.append(parts_list[0][index])
    return common


def _relative_scope_path(*, ancestor_path: str, scope_path: str) -> str:
    ancestor = _path_parts(ancestor_path)
    child = _path_parts(scope_path)
    if len(child) < len(ancestor) or [
        part.lower() for part in child[: len(ancestor)]
    ] != [part.lower() for part in ancestor]:
        return "/".join(child)
    relative = child[len(ancestor) :]
    return "/".join(relative)


def _restore_selected_paths(
    *, directory_source_path: str, scope_path: str
) -> list[str]:
    relative = _relative_scope_path(
        ancestor_path=directory_source_path, scope_path=scope_path
    )
    return [] if not relative else [relative]


def map_scope_to_workspace(
    *, workspace_root: str, scope_paths: list[str], scope_path: str
) -> str:
    root = workspace_root.rstrip("/") or "/"
    normalized = [str(path).strip() for path in scope_paths if str(path).strip()]
    scope_parts = _path_parts(scope_path)
    if not normalized:
        rel = "/".join(scope_parts)
    else:
        common_parts = _common_path_parts(normalized)
        if scope_parts == common_parts:
            rel = scope_parts[-1] if scope_parts else "data"
        elif len(scope_parts) > len(common_parts) and [
            part.lower() for part in scope_parts[: len(common_parts)]
        ] == [part.lower() for part in common_parts]:
            rel = "/".join(scope_parts[len(common_parts) :])
        else:
            rel = "/".join(scope_parts)
    if not rel:
        rel = "data"
    return f"{root}/{rel}"


def indexed_dir_paths(ks: LensKnowledgeSource) -> list[str]:
    if is_gateway_local_ks(ks):
        return [
            str(item.get("source_path") or ks.source_path).strip()
            for item in scope_entries(ks)
        ]
    workspace = (ks.workspace_path_on_lensnode or "").strip()
    if not workspace:
        raise KnowledgeSourceSyncError("Knowledge source workspace path is not set.")
    # SourceLens assistant create/update only accepts top-level LensNode dirs.
    return [workspace]


def resolve_snapshot_id_for_sync(*, ks: LensKnowledgeSource) -> int:
    if ks.linked_version_mode == LensKnowledgeSource.LinkedVersionMode.PINNED:
        pinned = ks.pinned_snapshot_id or ks.backup_source_snapshot_id
        if not pinned:
            raise KnowledgeSourceSyncError("Pinned snapshot is not configured.")
        return int(pinned)

    base_snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=ks.organization_id,
        pk=ks.backup_source_snapshot_id,
        status__in=restore_services.RESTORABLE_SNAPSHOT_STATUSES,
    ).first()
    if base_snapshot is None:
        raise KnowledgeSourceSyncError("Configured backup snapshot is not restorable.")

    latest = (
        BackupSourceSnapshot.objects.filter(
            organization_id=ks.organization_id,
            source_type=base_snapshot.source_type,
            source_ref_id=base_snapshot.source_ref_id,
            backup_config_id=base_snapshot.backup_config_id,
            status__in=restore_services.RESTORABLE_SNAPSHOT_STATUSES,
        )
        .order_by("-finished_at", "-created_at", "-id")
        .first()
    )
    return int(latest.id if latest else base_snapshot.id)


def should_run_restore_phase(
    *, ks: LensKnowledgeSource, sync_state: dict[str, Any]
) -> bool:
    if is_gateway_local_ks(ks):
        return False
    snapshot_id = resolve_snapshot_id_for_sync(ks=ks)
    used = sync_state.get("snapshot_id_used")
    completed = set(sync_state.get("completed_phases") or [])
    if "restore_snapshot" not in completed:
        return True
    if (
        ks.linked_version_mode == LensKnowledgeSource.LinkedVersionMode.LATEST
        and used != snapshot_id
    ):
        return True
    restore_record_id = sync_state.get("restore_record_id")
    if restore_record_id and not _restore_record_succeeded(
        record_id=int(restore_record_id),
        organization_id=ks.organization_id,
    ):
        return True
    pending_scopes = sync_state.get("restore_scope_status") or {}
    if any(status != "done" for status in pending_scopes.values()):
        return True
    return False


def enqueue_knowledge_source_sync(
    *,
    organization_id: int,
    knowledge_source_id: int,
    mode: str = "resume",
) -> None:
    from apps.lens_bridge.services.sync_queue import queue_knowledge_source_sync

    queue_knowledge_source_sync(
        organization_id=organization_id,
        knowledge_source_id=knowledge_source_id,
        mode=mode,
    )


def request_knowledge_source_sync(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    mode: str = "resume",
) -> LensKnowledgeSource:
    if ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY:
        raise ValidationError(
            {"lifecycle_status": "Knowledge source is being deleted."}
        )
    if ks.status == LensKnowledgeSource.Status.SYNCING:
        raise ValidationError(
            {"status": "Knowledge source sync is already in progress."}
        )

    execution = context_for_knowledge_source(
        tenant_organization=org,
        knowledge_source=ks,
    )
    blockers = get_node_workload_blockers(node=execution.gateway)
    if blockers:
        raise ValidationError(
            {
                "gateway": (
                    "Data gateway has active backup or restore tasks. "
                    "Wait for them to finish before syncing."
                )
            }
        )

    if ks.gateway.availability != Node.Availability.ONLINE:
        raise ValidationError({"gateway": "Data gateway must be online to sync."})

    from apps.node.services.internal.node_lifecycle import _active_lifecycle_task

    if _active_lifecycle_task(
        org=execution.execution_organization,
        node=execution.gateway,
    ):
        raise ValidationError(
            {"gateway": "Data gateway lifecycle operation is in progress."}
        )

    gateway_link = execution.gateway_link
    if gateway_link.sidecar_status in {
        LensGatewayLink.SidecarStatus.UPGRADING,
        LensGatewayLink.SidecarStatus.REMOVING,
    }:
        raise ValidationError(
            {"gateway": "Gateway AI engine is busy (upgrade or removal in progress)."}
        )
    gateway_readiness.require_hfl_usable_gateway(gateway_link, field="gateway")

    sync_state = dict(ks.sync_state_json or {})
    completed = set(sync_state.get("completed_phases") or [])
    previous_restore_record_id = sync_state.get("restore_record_id")
    if (
        mode != "full"
        and previous_restore_record_id
        and _restore_record_failed(
            record_id=int(previous_restore_record_id),
            organization_id=org.id,
        )
    ):
        sync_state["restore_generation"] = max(
            1,
            int(sync_state.get("restore_generation") or 0) + 1,
        )
        sync_state["restore_scope_status"] = {}
        sync_state.pop("restore_record_id", None)
        sync_state.pop("snapshot_id_used", None)
        sync_state.pop("conversion", None)
        completed.difference_update(
            {
                "restore_snapshot",
                "ensure_managed_datasource",
                "convert_documents",
                "push_assistant",
                "finalize",
            }
        )
        sync_state["completed_phases"] = list(completed)
    conversion_state = sync_state.get("conversion")
    if isinstance(conversion_state, dict) and str(
        conversion_state.get("status") or ""
    ) in {"FAILURE", "REVOKED"}:
        if not managed_datasource.conversion_stop_confirmed(ks):
            raise ValidationError(
                {
                    "status": (
                        "The previous document conversion is still stopping. "
                        "Retry after SourceLens confirms the final LensNode callback."
                    )
                }
            )
        sync_state.pop("conversion", None)
        completed.difference_update({"convert_documents", "push_assistant", "finalize"})
        sync_state["completed_phases"] = list(completed)
    start_new_cycle = mode == "full" or set(SYNC_PHASES).issubset(completed)
    effective_mode = "full" if start_new_cycle else mode
    if start_new_cycle:
        next_generation = max(1, int(sync_state.get("restore_generation") or 0) + 1)
        sync_state = {
            "mode": "full",
            "started_at": timezone.now().isoformat(),
            "completed_phases": [],
            "phase": "prepare_workspace",
            "restore_scope_status": {},
            "restore_generation": next_generation,
            "last_error": None,
        }
    else:
        sync_state.setdefault("mode", "resume")
        sync_state["started_at"] = timezone.now().isoformat()
        sync_state["phase"] = _resume_phase(sync_state)
        sync_state["last_error"] = None

    ks.status = LensKnowledgeSource.Status.SYNCING
    ks.status_detail = _PHASE_LABELS.get(str(sync_state.get("phase") or ""), "Syncing…")
    ks.sync_state_json = sync_state
    ks.sync_claim_token = None
    ks.sync_claimed_at = None
    ks.sync_next_poll_at = timezone.now()
    ks.save(
        update_fields=[
            "status",
            "status_detail",
            "sync_state_json",
            "sync_claim_token",
            "sync_claimed_at",
            "sync_next_poll_at",
            "updated_at",
        ]
    )

    enqueue_knowledge_source_sync(
        organization_id=org.id,
        knowledge_source_id=ks.id,
        mode=effective_mode,
    )
    return ks


def run_knowledge_source_sync(
    *,
    organization_id: int,
    knowledge_source_id: int,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    from django.db import close_old_connections

    close_old_connections()
    claim_token, claim_status = _claim_sync(
        organization_id=organization_id,
        knowledge_source_id=knowledge_source_id,
    )
    if claim_token is None:
        return {
            "knowledge_source_id": knowledge_source_id,
            "status": claim_status,
        }

    ks = (
        LensKnowledgeSource.objects.select_related(
            "gateway",
            "gateway_link",
            "gateway_link__gateway",
            "gateway_link__organization",
            "workspace_binding",
        )
        .filter(
            organization_id=organization_id,
            pk=knowledge_source_id,
        )
        .first()
    )
    if ks is None:
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
        )
        return {
            "knowledge_source_id": knowledge_source_id,
            "status": "missing",
        }

    org = Organization.objects.filter(pk=organization_id).first()
    if org is None:
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
        )
        raise KnowledgeSourceSyncError(f"Organization {organization_id} not found.")

    try:
        result = _run_sync_pipeline(
            org=org,
            ks=ks,
            progress_callback=progress_callback,
        )
        _clear_source_lens_transient_state(ks)
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
        )
        return result
    except KnowledgeSourceSyncPending as exc:
        retry_after_seconds = exc.retry_after_seconds
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
            next_poll_at=timezone.now() + timedelta(seconds=retry_after_seconds),
        )
        return {
            "knowledge_source_id": ks.id,
            "status": "waiting",
            "detail": str(exc),
            "retry_after_seconds": retry_after_seconds,
        }
    except managed_datasource.ManagedDatasourcePending as exc:
        retry_after_seconds = exc.retry_after_seconds
        _clear_source_lens_transient_state(ks)
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
            next_poll_at=(timezone.now() + timedelta(seconds=retry_after_seconds)),
        )
        return {
            "knowledge_source_id": ks.id,
            "status": "waiting",
            "detail": str(exc),
            "retry_after_seconds": retry_after_seconds,
        }
    except sl_client.LensBridgeUnavailable as exc:
        retry_after_seconds = (
            managed_datasource.CONVERSION_TRANSIENT_RETRY_MAX_SECONDS // 10
        )
        sync_state = dict(ks.sync_state_json or {})
        transient = dict(sync_state.get("source_lens_transient") or {})
        transient_count = int(transient.get("count") or 0) + 1
        retry_after_seconds = min(
            managed_datasource.CONVERSION_TRANSIENT_RETRY_MAX_SECONDS,
            retry_after_seconds * (2 ** max(0, min(transient_count - 1, 3))),
        )
        sync_state["source_lens_transient"] = {
            "count": transient_count,
            "last_seen_at": timezone.now().isoformat(),
        }
        ks.sync_state_json = sync_state
        ks.status = LensKnowledgeSource.Status.SYNCING
        ks.status_detail = (
            "SourceLens is temporarily unavailable. Retrying automatically."
        )
        ks.save(
            update_fields=["sync_state_json", "status", "status_detail", "updated_at"]
        )
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
            next_poll_at=timezone.now() + timedelta(seconds=retry_after_seconds),
        )
        return {
            "knowledge_source_id": ks.id,
            "status": "waiting",
            "detail": str(exc.detail),
            "retry_after_seconds": retry_after_seconds,
        }
    except Exception as exc:
        logger.exception(
            "knowledge source sync failed ks_id=%s org_id=%s",
            knowledge_source_id,
            organization_id,
        )
        _mark_sync_error(ks=ks, message=str(exc))
        _release_sync_claim(
            knowledge_source_id=knowledge_source_id,
            claim_token=claim_token,
        )
        raise


def _clear_source_lens_transient_state(ks: LensKnowledgeSource) -> None:
    """Clear a recovered bridge outage without overwriting pipeline progress."""

    current_state = (
        LensKnowledgeSource.all_objects.filter(pk=ks.id)
        .values_list("sync_state_json", flat=True)
        .first()
    )
    if (
        not isinstance(current_state, dict)
        or "source_lens_transient" not in current_state
    ):
        return
    current_state = dict(current_state)
    current_state.pop("source_lens_transient", None)
    LensKnowledgeSource.all_objects.filter(pk=ks.id).update(
        sync_state_json=current_state,
        updated_at=timezone.now(),
    )


def _run_sync_pipeline(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    sync_state = dict(ks.sync_state_json or {})
    completed = set(sync_state.get("completed_phases") or [])

    _require_active_lifecycle(ks)
    if "prepare_workspace" not in completed:
        _notify_progress(progress_callback, "prepare_workspace")
        _run_phase_prepare_workspace(org=org, ks=ks, sync_state=sync_state)
        completed.add("prepare_workspace")
        sync_state["completed_phases"] = list(completed)
        ks.sync_state_json = sync_state
        ks.save(update_fields=["sync_state_json", "updated_at"])

    _require_active_lifecycle(ks)
    conversion_state = sync_state.get("conversion")
    conversion_status = (
        str(conversion_state.get("status") or "").upper()
        if isinstance(conversion_state, dict)
        else ""
    )
    conversion_in_progress = bool(
        conversion_state and conversion_status not in {"SUCCESS", "FAILURE", "REVOKED"}
    )
    restore_required = (
        False
        if conversion_in_progress
        else should_run_restore_phase(
            ks=ks,
            sync_state=sync_state,
        )
    )
    if restore_required:
        completed.difference_update(
            {
                "restore_snapshot",
                "ensure_managed_datasource",
                "convert_documents",
                "push_assistant",
                "finalize",
            }
        )
        sync_state["completed_phases"] = list(completed)
        sync_state.pop("conversion", None)
        ks.sync_state_json = sync_state
        ks.save(update_fields=["sync_state_json", "updated_at"])
        _notify_progress(progress_callback, "restore_snapshot")
        _run_phase_restore_snapshot(org=org, ks=ks, sync_state=sync_state)
        completed.add("restore_snapshot")
    elif not is_gateway_local_ks(ks):
        completed.add("restore_snapshot")
    sync_state["completed_phases"] = list(completed)
    ks.sync_state_json = sync_state
    ks.save(update_fields=["sync_state_json", "updated_at"])

    _require_active_lifecycle(ks)
    policy = ingest_policy.normalize_ingest_policy(
        ks.ingest_policy_json,
    )
    conversion = ingest_policy.conversion_payload_for_sl(policy)
    conversion_state = sync_state.get("conversion")
    recorded_fingerprint = (
        str(conversion_state.get("policy_fingerprint") or "")
        if isinstance(conversion_state, dict)
        else ""
    )
    current_fingerprint = managed_datasource.conversion_policy_fingerprint(conversion)
    if "convert_documents" in completed and recorded_fingerprint != current_fingerprint:
        completed.difference_update({"convert_documents", "push_assistant", "finalize"})
        sync_state["completed_phases"] = list(completed)
        sync_state.pop("conversion", None)
        ks.sync_state_json = sync_state
        ks.save(update_fields=["sync_state_json", "updated_at"])

    if (
        not is_gateway_local_ks(ks)
        and ks.scan_enabled
        and managed_conversion_enabled(org=org, ks=ks)
    ):
        if "ensure_managed_datasource" not in completed:
            _notify_progress(progress_callback, "ensure_managed_datasource")
            _run_phase_ensure_managed_datasource(
                ks=ks,
                sync_state=sync_state,
            )
            completed.add("ensure_managed_datasource")
            sync_state["completed_phases"] = list(completed)
            ks.sync_state_json = sync_state
            ks.save(update_fields=["sync_state_json", "updated_at"])

        _require_active_lifecycle(ks)
        if "convert_documents" not in completed:
            _notify_progress(progress_callback, "convert_documents")
            _run_phase_convert_documents(
                ks=ks,
                sync_state=sync_state,
                conversion=conversion,
                progress_callback=progress_callback,
            )
            completed.add("convert_documents")
            sync_state["completed_phases"] = list(completed)
            ks.sync_state_json = sync_state
            ks.save(update_fields=["sync_state_json", "updated_at"])
    else:
        completed.update({"ensure_managed_datasource", "convert_documents"})
        sync_state["completed_phases"] = list(completed)
        ks.sync_state_json = sync_state
        ks.save(update_fields=["sync_state_json", "updated_at"])

    _require_active_lifecycle(ks)
    if "push_assistant" not in completed:
        _notify_progress(progress_callback, "push_assistant")
        _run_phase_push_assistant(org=org, ks=ks, sync_state=sync_state)
        completed.add("push_assistant")
        sync_state["completed_phases"] = list(completed)
        ks.sync_state_json = sync_state
        ks.save(update_fields=["sync_state_json", "updated_at"])

    _require_active_lifecycle(ks)
    _notify_progress(progress_callback, "finalize")
    _run_phase_finalize(org=org, ks=ks, sync_state=sync_state)
    completed.add("finalize")

    sync_state["completed_phases"] = list(SYNC_PHASES)
    sync_state["phase"] = "finalize"
    sync_state["last_sync_at"] = timezone.now().isoformat()
    sync_state["last_error"] = None
    ks.sync_state_json = sync_state
    ks.save(update_fields=["sync_state_json", "updated_at"])
    return {"knowledge_source_id": ks.id, "status": ks.status}


def _run_phase_prepare_workspace(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> None:
    _update_sync_phase(ks=ks, sync_state=sync_state, phase="prepare_workspace")
    execution = context_for_knowledge_source(
        tenant_organization=org,
        knowledge_source=ks,
    )
    gateway = execution.gateway
    gateway_link = execution.gateway_link
    try:
        workspace_binding = ks.workspace_binding
    except LensWorkspaceBinding.DoesNotExist as exc:
        raise KnowledgeSourceSyncError(
            "Knowledge source has no authoritative workspace binding."
        ) from exc

    if is_gateway_local_ks(ks):
        if (
            workspace_binding.workspace_kind
            != LensWorkspaceBinding.WorkspaceKind.GATEWAY_LOCAL
        ):
            raise KnowledgeSourceSyncError(
                "Gateway-local knowledge source binding is inconsistent."
            )
        from apps.node.services.internal.agent_task import run_agent_task_sync

        for scope in scope_entries(ks):
            source_path = str(scope.get("source_path") or "").strip()
            outcome = run_agent_task_sync(
                org=execution.execution_organization,
                node_id=gateway.id,
                kind="lens.workspace.validate-local",
                payload={
                    "path": source_path,
                    "allowed_root": gateway_link.resolved_workspace_root(),
                },
                correlation_type="lens_knowledge_source.validate_local",
                correlation_id=str(ks.id),
                requesting_organization_id=org.id,
                wait_timeout_seconds=30,
            )
            if not outcome.ok:
                detail = (
                    outcome.task.last_error or "Gateway directory validation failed."
                )
                raise KnowledgeSourceSyncError(detail)
        ks.workspace_path_on_lensnode = ks.source_path
        ks.mount_path_on_gateway = ks.source_path
        ks.save(
            update_fields=[
                "workspace_path_on_lensnode",
                "mount_path_on_gateway",
                "updated_at",
            ]
        )
        if workspace_binding.state != LensWorkspaceBinding.State.READY:
            workspace_binding.state = LensWorkspaceBinding.State.READY
            workspace_binding.last_error = ""
            workspace_binding.save(update_fields=["state", "last_error", "updated_at"])
    else:
        if (
            workspace_binding.workspace_kind
            != LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE
        ):
            raise KnowledgeSourceSyncError(
                "Managed knowledge source binding is inconsistent."
            )
        workspace_path = workspace_binding.resolved_path()
        ks.workspace_path_on_lensnode = workspace_path
        ks.mount_path_on_gateway = workspace_path
        ks.save(
            update_fields=[
                "workspace_path_on_lensnode",
                "mount_path_on_gateway",
                "updated_at",
            ]
        )
        provisioning.ensure_ks_workspace_on_gateway(
            org=org,
            gateway=gateway,
            gateway_link=gateway_link,
            workspace_binding=workspace_binding,
        )
        if workspace_binding.state != LensWorkspaceBinding.State.READY:
            workspace_binding.state = LensWorkspaceBinding.State.READY
            workspace_binding.last_error = ""
            workspace_binding.save(update_fields=["state", "last_error", "updated_at"])


def _create_and_publish_workspace_restore(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    restore_data: dict[str, Any],
    sync_state: dict[str, Any],
    snapshot_id: int,
    restore_scope_status: dict[str, str],
) -> RestoreRecord:
    """Atomically bind a restore before its on-commit Agent delivery."""

    with transaction.atomic():
        Organization.objects.select_for_update().only("id").get(pk=org.id)
        locked_ks = LensKnowledgeSource.all_objects.select_for_update().get(
            pk=ks.id,
            organization_id=org.id,
        )
        if locked_ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY:
            raise KnowledgeSourceSyncError("Knowledge source deletion was requested.")
        try:
            workspace_binding = locked_ks.workspace_binding
        except LensWorkspaceBinding.DoesNotExist as exc:
            raise KnowledgeSourceSyncError(
                "Knowledge source has no authoritative workspace binding."
            ) from exc
        record = restore_services.create_lens_workspace_restore_record(
            organization_id=org.id,
            workspace_binding_id=workspace_binding.id,
            data=restore_data,
        )
        sync_state["restore_record_id"] = record.id
        sync_state["snapshot_id_used"] = snapshot_id
        sync_state["restore_scope_status"] = restore_scope_status
        locked_ks.last_restore_record_id = record.id
        locked_ks.sync_state_json = sync_state
        locked_ks.save(
            update_fields=[
                "last_restore_record_id",
                "sync_state_json",
                "updated_at",
            ]
        )
    ks.last_restore_record_id = record.id
    ks.sync_state_json = sync_state
    return record


def _run_phase_restore_snapshot(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> None:
    _update_sync_phase(ks=ks, sync_state=sync_state, phase="restore_snapshot")
    generation = max(1, int(sync_state.get("restore_generation") or 1))
    previous_record_id = sync_state.get("restore_record_id")
    if previous_record_id and _restore_record_failed(
        record_id=int(previous_record_id),
        organization_id=org.id,
    ):
        record = RestoreRecord.objects.filter(
            pk=int(previous_record_id),
            organization_id=org.id,
        ).first()
        task = (
            Task.objects.filter(
                organization_id=org.id,
                task_uuid=record.task_uuid,
            ).first()
            if record is not None
            else None
        )
        message = (task.error_message if task else None) or "Snapshot restore failed."
        raise KnowledgeSourceSyncError(message)
    previous_snapshot_id = sync_state.get("snapshot_id_used")
    snapshot_id = (
        int(previous_snapshot_id)
        if previous_record_id and previous_snapshot_id is not None
        else resolve_snapshot_id_for_sync(ks=ks)
    )
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=org.id,
        pk=snapshot_id,
        status__in=restore_services.RESTORABLE_SNAPSHOT_STATUSES,
    ).first()
    if snapshot is None:
        raise KnowledgeSourceSyncError(
            "No restorable snapshot found for knowledge source."
        )

    workspace = ks.workspace_path_on_lensnode
    items: list[dict[str, Any]] = []
    restore_scope_status: dict[str, str] = dict(
        sync_state.get("restore_scope_status") or {}
    )

    for index, entry in enumerate(scope_entries(ks)):
        key = str(index)
        if restore_scope_status.get(key) == "done":
            continue
        scope_path = str(entry.get("source_path") or "").strip()
        directory_id = (
            entry.get("backup_snapshot_directory_id") or ks.backup_snapshot_directory_id
        )
        if not directory_id:
            raise KnowledgeSourceSyncError(
                f"Index scope {index + 1} is missing snapshot directory id."
            )
        directory = BackupSourceSnapshotDirectory.objects.filter(
            organization_id=org.id,
            source_snapshot=snapshot,
            pk=int(directory_id),
        ).first()
        if directory is None:
            raise KnowledgeSourceSyncError(
                f"Snapshot directory {directory_id} not found for restore."
            )
        items.append(
            {
                "source_snapshot_directory_id": int(directory_id),
                "selected_paths": _restore_selected_paths(
                    directory_source_path=directory.source_path,
                    scope_path=scope_path,
                ),
                "target_path": workspace,
                "conflict_mode": "overwrite",
            }
        )
        restore_scope_status[key] = "pending"

    if not items:
        sync_state["snapshot_id_used"] = snapshot_id
        sync_state["restore_scope_status"] = restore_scope_status
        ks.sync_state_json = sync_state
        ks.save(update_fields=["sync_state_json", "updated_at"])
        return

    record = _create_and_publish_workspace_restore(
        org=org,
        ks=ks,
        restore_data={
            "source_type": snapshot.source_type,
            "source_ref_id": snapshot.source_ref_id,
            "target_type": "agent",
            "target_ref_id": ks.gateway_id,
            "source_snapshot_id": snapshot.id,
            "target_path": workspace,
            "scope": "paths",
            "conflict_mode": "overwrite",
            "items": items,
            "idempotency_key": (
                f"lens-workspace:{ks.id}:generation:{generation}:snapshot:{snapshot_id}"
            ),
        },
        sync_state=sync_state,
        snapshot_id=snapshot_id,
        restore_scope_status=restore_scope_status,
    )

    if _restore_record_failed(record_id=record.id, organization_id=org.id):
        task = Task.objects.filter(
            organization_id=org.id,
            task_uuid=record.task_uuid,
        ).first()
        message = (task.error_message if task else None) or "Snapshot restore failed."
        raise KnowledgeSourceSyncError(message)
    if not _restore_record_succeeded(record_id=record.id, organization_id=org.id):
        raise KnowledgeSourceSyncPending("Snapshot restore is still running.")

    for key in restore_scope_status:
        if restore_scope_status[key] != "done":
            restore_scope_status[key] = "done"
    sync_state["restore_scope_status"] = restore_scope_status
    ks.sync_state_json = sync_state
    ks.save(update_fields=["sync_state_json", "updated_at"])


def _run_phase_push_assistant(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> None:
    _update_sync_phase(ks=ks, sync_state=sync_state, phase="push_assistant")
    gateway_link = context_for_knowledge_source(
        tenant_organization=org,
        knowledge_source=ks,
    ).gateway_link
    lensnode_uuid = gateway_link.sl_lensnode_uuid
    if lensnode_uuid:
        provisioning.wait_for_lensnode_ready(
            lensnode_uuid=lensnode_uuid,
            workspace_root=gateway_link.resolved_workspace_root(),
            selected_dir=(
                None if is_gateway_local_ks(ks) else ks.workspace_path_on_lensnode
            ),
        )
    provisioning.sync_linked_assistant_for_ks(
        org=org,
        ks=ks,
        gateway_link=gateway_link,
    )


def _run_phase_ensure_managed_datasource(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> None:
    _update_sync_phase(
        ks=ks,
        sync_state=sync_state,
        phase="ensure_managed_datasource",
    )
    managed_datasource.ensure_managed_datasource(
        ks=ks,
        sync_state=sync_state,
    )


def _run_phase_convert_documents(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    conversion: dict[str, Any],
    progress_callback: Callable[[str, str], None] | None,
) -> None:
    _update_sync_phase(
        ks=ks,
        sync_state=sync_state,
        phase="convert_documents",
    )

    def report(detail: str) -> None:
        if progress_callback is not None:
            progress_callback(
                "convert_documents",
                detail or _PHASE_LABELS["convert_documents"],
            )

    managed_datasource.convert_documents(
        ks=ks,
        sync_state=sync_state,
        conversion=conversion,
        progress=report,
    )


def _run_phase_finalize(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> None:
    _update_sync_phase(ks=ks, sync_state=sync_state, phase="finalize")
    ks.refresh_from_db()
    if not ks.scan_enabled:
        ks.status = LensKnowledgeSource.Status.PAUSED
        policy = ingest_policy.normalize_ingest_policy(ks.ingest_policy_json)
        ks.status_detail = ingest_policy.ingest_summary(policy)
        ks.save(update_fields=["status", "status_detail", "updated_at"])
        return

    if not provisioning.assistant_uuid_for_ks(ks):
        policy = ingest_policy.normalize_ingest_policy(ks.ingest_policy_json)
        ks.status = LensKnowledgeSource.Status.READY
        ks.status_detail = "Workspace ready. Create an Assistant to enable indexing."
        ks.save(update_fields=["status", "status_detail", "updated_at"])
        return

    provisioning.refresh_ks_status_from_sl(ks)
    ks.refresh_from_db()
    if ks.status == LensKnowledgeSource.Status.ERROR:
        return

    if _is_degraded(ks=ks, sync_state=sync_state):
        ks.status = LensKnowledgeSource.Status.DEGRADED
        ks.status_detail = "A newer backup snapshot is available. Sync to refresh."
        ks.save(update_fields=["status", "status_detail", "updated_at"])
        return

    policy = ingest_policy.normalize_ingest_policy(ks.ingest_policy_json)
    ks.status = LensKnowledgeSource.Status.READY
    ks.status_detail = ingest_policy.ingest_summary(policy)
    ks.save(update_fields=["status", "status_detail", "updated_at"])


def _is_degraded(*, ks: LensKnowledgeSource, sync_state: dict[str, Any]) -> bool:
    if is_gateway_local_ks(ks):
        return False
    if ks.linked_version_mode != LensKnowledgeSource.LinkedVersionMode.LATEST:
        return False
    try:
        latest_id = resolve_snapshot_id_for_sync(ks=ks)
    except KnowledgeSourceSyncError:
        return False
    used = sync_state.get("snapshot_id_used")
    return used is not None and int(used) != int(latest_id)


def _restore_record_succeeded(*, record_id: int, organization_id: int) -> bool:
    record = RestoreRecord.objects.filter(
        pk=record_id, organization_id=organization_id
    ).first()
    if record is None:
        return False
    statuses = list(record.items.values_list("status", flat=True))
    if not statuses:
        task = Task.objects.filter(
            organization_id=organization_id, task_uuid=record.task_uuid
        ).first()
        return task is not None and task.status == Task.Status.SUCCESS
    return all(status == RestoreRecordItem.Status.SUCCESS for status in statuses)


def _restore_record_failed(*, record_id: int, organization_id: int) -> bool:
    record = RestoreRecord.objects.filter(
        pk=record_id, organization_id=organization_id
    ).first()
    if record is None:
        return False
    statuses = list(record.items.values_list("status", flat=True))
    if any(status == RestoreRecordItem.Status.FAILED for status in statuses):
        return True
    task = Task.objects.filter(
        organization_id=organization_id, task_uuid=record.task_uuid
    ).first()
    return task is not None and task.status in {
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }


def _resume_phase(sync_state: dict[str, Any]) -> str:
    completed = set(sync_state.get("completed_phases") or [])
    for phase in SYNC_PHASES:
        if phase not in completed:
            return phase
    return "prepare_workspace"


def _notify_progress(
    callback: Callable[[str, str], None] | None,
    phase: str,
    detail: str = "",
) -> None:
    """Publish an optional owning-workflow progress update."""

    if callback is None:
        return
    callback(phase, detail or _PHASE_LABELS.get(phase, "Syncing…"))


def _update_sync_phase(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    phase: str,
) -> None:
    sync_state["phase"] = phase
    ks.status = LensKnowledgeSource.Status.SYNCING
    ks.status_detail = _PHASE_LABELS.get(phase, "Syncing…")
    ks.sync_state_json = sync_state
    ks.save(update_fields=["status", "status_detail", "sync_state_json", "updated_at"])


def _mark_sync_error(*, ks: LensKnowledgeSource, message: str) -> None:
    sync_state = dict(ks.sync_state_json or {})
    sync_state["last_error"] = message
    sync_state["phase"] = sync_state.get("phase") or "error"
    ks.status = LensKnowledgeSource.Status.ERROR
    ks.status_detail = message[:2000]
    ks.sync_state_json = sync_state
    ks.save(update_fields=["status", "status_detail", "sync_state_json", "updated_at"])


def maybe_refresh_degraded_status(*, ks: LensKnowledgeSource) -> LensKnowledgeSource:
    """Mark ready backup sources as degraded when a newer snapshot is available."""
    if ks.status != LensKnowledgeSource.Status.READY:
        return ks
    if is_gateway_local_ks(ks):
        return ks
    if ks.linked_version_mode != LensKnowledgeSource.LinkedVersionMode.LATEST:
        return ks
    sync_state = dict(ks.sync_state_json or {})
    used = sync_state.get("snapshot_id_used")
    if used is None:
        return ks
    try:
        latest_id = resolve_snapshot_id_for_sync(ks=ks)
    except KnowledgeSourceSyncError:
        return ks
    if int(latest_id) == int(used):
        return ks
    ks.status = LensKnowledgeSource.Status.DEGRADED
    ks.status_detail = "A newer backup snapshot is available. Sync to refresh."
    ks.save(update_fields=["status", "status_detail", "updated_at"])
    return ks


def prepare_new_knowledge_source(
    *, org: Organization, ks: LensKnowledgeSource
) -> LensKnowledgeSource:
    """Allocate workspace paths and mark the row syncing before async pipeline starts."""
    from apps.lens_bridge.services.gateway_execution import create_workspace_binding

    workspace_binding = create_workspace_binding(
        tenant_organization=org,
        knowledge_source=ks,
    )
    gateway_link = workspace_binding.gateway_link
    ks.sl_lensnode_uuid = gateway_link.sl_lensnode_uuid
    if is_gateway_local_ks(ks):
        ks.workspace_path_on_lensnode = ks.source_path
        ks.mount_path_on_gateway = ks.source_path
    else:
        ks.workspace_path_on_lensnode = workspace_binding.resolved_path()
        ks.mount_path_on_gateway = ks.workspace_path_on_lensnode

    ks.status = LensKnowledgeSource.Status.SYNCING
    ks.status_detail = _PHASE_LABELS["prepare_workspace"]
    ks.sync_claim_token = None
    ks.sync_claimed_at = None
    ks.sync_next_poll_at = timezone.now()
    ks.sync_state_json = {
        "mode": "full",
        "phase": "prepare_workspace",
        "started_at": timezone.now().isoformat(),
        "completed_phases": [],
        "restore_scope_status": {},
        "restore_generation": 1,
        "last_error": None,
    }
    ks.save(
        update_fields=[
            "sl_lensnode_uuid",
            "workspace_path_on_lensnode",
            "mount_path_on_gateway",
            "status",
            "status_detail",
            "sync_state_json",
            "sync_claim_token",
            "sync_claimed_at",
            "sync_next_poll_at",
            "updated_at",
        ]
    )
    return ks
