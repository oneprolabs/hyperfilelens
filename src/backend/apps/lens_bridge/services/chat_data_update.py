"""Durable restore, reconciliation and conversion for a Chat-owned workspace."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from common.errors import AppError
from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import ingest_policy, managed_datasource
from apps.lens_bridge.services import knowledge_source_sync
from apps.lens_bridge.services.knowledge_source_sync import (
    _path_parts,
    map_scope_to_workspace,
    scope_entries,
)
from apps.node.services.capabilities import missing_node_capabilities
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
    SnapshotUsageLease,
)
from apps.protection.services.snapshot_usage import (
    acquire_snapshot_usage,
    release_snapshot_usage,
)
from apps.restore.services import interface as restore_services

UPDATE_STATE_KEY = "chat_data_update"
UPDATE_CLAIM_TTL = timedelta(hours=3)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateScope:
    source_path: str
    snapshot_directory_id: int
    snapshot_directory_source_path: str
    snapshot_directory_path_type: str
    selected_path: str
    path_type: str


def _scope_drive_and_parts(path: str) -> tuple[str, list[str]]:
    raw = str(path or "").strip().replace("\\", "/")
    if raw.startswith("//"):
        parts = _path_parts(raw)
        if len(parts) < 2:
            raise ValidationError({"snapshot_id": "Snapshot share path is invalid."})
        return f"//{parts[0].casefold()}/{parts[1].casefold()}", parts[2:]
    raw = raw.lstrip("/")
    drive = raw[:2].casefold() if len(raw) >= 2 and raw[1] == ":" else ""
    return drive, _path_parts(raw)


def resolve_update_scopes(
    *, chat: LensSessionLink, snapshot_id: int
) -> tuple[BackupSourceSnapshot, tuple[UpdateScope, ...]]:
    """Validate the new snapshot and rebind its directories before any write.

    A directory ID belongs to one snapshot and is never reusable in another.
    The actual selected path must also be checked inside Kopia by the restore
    admission step; a database directory can be an ancestor of a missing path.
    """

    ks = chat.knowledge_source
    if (
        chat.lifecycle_status != LensSessionLink.LifecycleStatus.READY
        or ks is None
        or ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY
        or ks.linked_version_mode != LensKnowledgeSource.LinkedVersionMode.PINNED
        or ks.sl_assistant_uuid is None
        or ks.sl_datasource_uuid is None
    ):
        raise ValidationError({"chat": "Chat data is not ready for an update."})
    try:
        binding = ks.workspace_binding
    except LensWorkspaceBinding.DoesNotExist as exc:
        raise ValidationError({"chat": "Chat workspace is unavailable."}) from exc
    if (
        binding.workspace_kind != LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE
        or binding.state != LensWorkspaceBinding.State.READY
    ):
        raise ValidationError({"chat": "Chat workspace is unavailable."})
    if missing_node_capabilities(ks.gateway, ["chat_workspace_reconcile_v1"]):
        raise ValidationError(
            {"gateway": "Upgrade the Data Gateway Agent before updating Chat data."}
        )
    if not knowledge_source_sync.managed_conversion_enabled(
        org=chat.organization, ks=ks
    ):
        raise ValidationError(
            {"chat": "This Chat does not have managed document conversion enabled."}
        )
    previous = BackupSourceSnapshot.objects.filter(
        pk=ks.backup_source_snapshot_id,
        organization_id=chat.organization_id,
    ).first()
    config = BackupConfig.objects.filter(
        pk=chat.backup_config_id,
        organization_id=chat.organization_id,
    ).first()
    target = BackupSourceSnapshot.objects.filter(
        pk=snapshot_id,
        organization_id=chat.organization_id,
        status__in=restore_services.RESTORABLE_SNAPSHOT_STATUSES,
    ).first()
    if (
        target is None
        or chat.backup_config_id != target.backup_config_id
        or (
            previous is not None
            and (
                previous.source_type,
                previous.source_ref_id,
                previous.backup_config_id,
            )
            != (
                target.source_type,
                target.source_ref_id,
                target.backup_config_id,
            )
        )
        or (
            previous is None
            and (
                config is None
                or (config.source_type, config.source_ref_id)
                != (target.source_type, target.source_ref_id)
            )
        )
    ):
        raise ValidationError(
            {
                "snapshot_id": "Choose an available snapshot of this Chat's backup source."
            }
        )
    scopes = scope_entries(ks)
    if not scopes:
        raise ValidationError({"chat": "Chat has no selected source paths."})
    scope_paths = [str(scope["source_path"]).strip() for scope in scopes]
    targets = [
        map_scope_to_workspace(
            workspace_root=binding.resolved_path(),
            scope_paths=scope_paths,
            scope_path=path,
        )
        for path in scope_paths
    ]
    for index, current in enumerate(targets):
        if any(
            current == other
            or current.startswith(other.rstrip("/") + "/")
            or other.startswith(current.rstrip("/") + "/")
            for other in targets[:index]
        ):
            raise ValidationError(
                {"chat": "Overlapping Chat data selections cannot be updated safely."}
            )
    directories = tuple(
        BackupSourceSnapshotDirectory.objects.filter(
            organization_id=chat.organization_id,
            source_snapshot_id=target.id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        )
    )
    try:
        old_directory_ids = {
            int(scope["backup_snapshot_directory_id"])
            for scope in scopes
            if scope.get("backup_snapshot_directory_id")
        }
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {"chat": "Stored Chat snapshot directory identity is invalid."}
        ) from exc
    old_directories = {
        row.id: row
        for row in BackupSourceSnapshotDirectory.objects.filter(
            organization_id=chat.organization_id,
            pk__in=old_directory_ids,
        )
    }
    rebound: list[UpdateScope] = []
    for scope in scopes:
        path = str(scope["source_path"]).strip()
        drive, parts = _scope_drive_and_parts(path)
        candidates: list[tuple[BackupSourceSnapshotDirectory, list[str]]] = []
        for directory in directories:
            directory_drive, directory_parts = _scope_drive_and_parts(
                directory.source_path
            )
            if (
                directory_drive == drive
                and len(directory_parts) <= len(parts)
                and [component.casefold() for component in directory_parts]
                == [component.casefold() for component in parts[: len(directory_parts)]]
            ):
                candidates.append((directory, directory_parts))
        if not candidates:
            raise ValidationError(
                {
                    "snapshot_id": f"Selected Chat path is absent from the new snapshot: {path}"
                }
            )
        old_id = scope.get("backup_snapshot_directory_id")
        old_directory = old_directories.get(int(old_id)) if old_id else None
        matched_old_root = False
        if old_directory is not None:
            same_root = [
                row
                for row in candidates
                if row[0].backup_config_dir_id == old_directory.backup_config_dir_id
            ]
            if same_root:
                candidates = same_root
                matched_old_root = True
        exact = [row for row in candidates if row[1] == parts[: len(row[1])]]
        if exact:
            candidates = exact
        elif (
            not matched_old_root
            and len({row[0].backup_config_dir_id for row in candidates}) > 1
        ):
            raise ValidationError(
                {
                    "snapshot_id": f"Selected Chat path is ambiguous in the new snapshot: {path}"
                }
            )
        longest = max(len(row[1]) for row in candidates)
        finalists = [row for row in candidates if len(row[1]) == longest]
        if len(finalists) != 1:
            raise ValidationError(
                {
                    "snapshot_id": f"Selected Chat path is ambiguous in the new snapshot: {path}"
                }
            )
        directory, directory_parts = finalists[0]
        selected_path = "/".join(parts[len(directory_parts) :])
        rebound.append(
            UpdateScope(
                source_path=path,
                snapshot_directory_id=directory.id,
                snapshot_directory_source_path=directory.source_path,
                snapshot_directory_path_type=directory.path_type,
                selected_path=selected_path,
                path_type=str(scope.get("path_type") or "unknown"),
            )
        )
    return target, tuple(rebound)


@transaction.atomic
def prepare_chat_data_update(*, chat: LensSessionLink, snapshot_id: int) -> dict:
    """Pin one target and journal an idempotent update request.

    Snapshot admission obtains the source/snapshot locks before the KS lock,
    matching the restore and source-reset lock order.
    """

    target, scopes = resolve_update_scopes(chat=chat, snapshot_id=snapshot_id)
    knowledge_source_id = chat.knowledge_source_id
    acquire_snapshot_usage(
        organization_id=chat.organization_id,
        snapshot_id=target.id,
        consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
        consumer_id=knowledge_source_id,
    )
    ks = (
        LensKnowledgeSource.objects.select_for_update()
        .filter(
            pk=knowledge_source_id,
            organization_id=chat.organization_id,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
        )
        .first()
    )
    if ks is None:
        raise ValidationError({"chat": "Chat resources are being deleted."})
    if not LensSessionLink.objects.filter(
        pk=chat.id,
        knowledge_source_id=ks.id,
        lifecycle_status=LensSessionLink.LifecycleStatus.READY,
    ).exists():
        raise ValidationError({"chat": "Chat resources changed during update."})
    state = dict(ks.sync_state_json or {})
    update = dict(state.get(UPDATE_STATE_KEY) or {})
    previous_target = update.get("target_snapshot_id")
    if update.get("status") in {"pending", "running"}:
        if previous_target == target.id:
            return update
        raise ValidationError(
            {"snapshot_id": "Another Chat data update is in progress."}
        )
    if update.get("status") == "complete" and previous_target == target.id:
        release_snapshot_usage(
            snapshot_id=target.id,
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=ks.id,
        )
        return update
    if update.get("status") == "failed" and previous_target != target.id:
        raise ValidationError(
            {
                "snapshot_id": "Retry the unfinished snapshot update before selecting another."
            }
        )
    if (
        update.get("status") not in {"failed", "pending", "running"}
        and (ks.pinned_snapshot_id or ks.backup_source_snapshot_id) == target.id
    ):
        raise ValidationError({"snapshot_id": "This snapshot is already applied."})
    if ks.status not in {
        LensKnowledgeSource.Status.READY,
        LensKnowledgeSource.Status.DEGRADED,
    }:
        raise ValidationError({"chat": "Chat data is already being prepared."})
    previous_conversion = dict(state.get("conversion") or {})
    if previous_conversion and str(
        previous_conversion.get("status") or ""
    ).upper() not in {
        "SUCCESS",
        "FAILURE",
        "REVOKED",
    }:
        raise ValidationError(
            {"chat": "Previous document conversion is still running."}
        )
    if update.get("status") == "failed":
        update["status"] = "pending"
        update["retry_count"] = int(update.get("retry_count") or 0) + 1
        if update.get("phase") == "validate":
            update["validation_generation"] = (
                int(update.get("validation_generation") or 0) + 1
            )
            update.pop("validation_task_id", None)
    else:
        previous_status = ks.status
        if update.get("status") == "abandoned":
            previous_status = update.get("previous_status") or ks.status
        update = {
            "status": "pending",
            "phase": "validate",
            "new_request": True,
            "previous_status": previous_status,
            "target_snapshot_id": target.id,
            "applied_snapshot_id": (
                update.get("applied_snapshot_id")
                or ks.pinned_snapshot_id
                or ks.backup_source_snapshot_id
            ),
            "generation": int(update.get("generation") or 0) + 1,
            "retry_count": 0,
            "requested_by_user_id": chat.hfl_user_id,
            "scopes": [
                {
                    "source_path": scope.source_path,
                    "snapshot_directory_id": scope.snapshot_directory_id,
                    "snapshot_directory_source_path": scope.snapshot_directory_source_path,
                    "snapshot_directory_path_type": scope.snapshot_directory_path_type,
                    "selected_path": scope.selected_path,
                    "path_type": scope.path_type,
                }
                for scope in scopes
            ],
        }
    state[UPDATE_STATE_KEY] = update
    ks.sync_state_json = state
    ks.save(update_fields=["sync_state_json", "updated_at"])
    transaction.on_commit(
        lambda: queue_chat_data_update(
            organization_id=chat.organization_id, knowledge_source_id=ks.id
        )
    )
    return update


def queue_chat_data_update(*, organization_id: int, knowledge_source_id: int) -> None:
    from apps.lens_bridge.tasks.chat_data_update import execute_chat_data_update_task

    try:
        execute_chat_data_update_task.delay(
            organization_id=organization_id,
            knowledge_source_id=knowledge_source_id,
        )
    except Exception:
        # The durable pending row is picked up by the update reconciler.
        logger.exception(
            "Chat data update dispatch deferred ks_id=%s", knowledge_source_id
        )


def abandon_chat_data_update(*, chat: LensSessionLink) -> dict:
    """Release a failed target only after no executor can still mutate the workspace."""

    from apps.lens_bridge.services.knowledge_source_teardown import (
        assess_chat_restore_stop,
    )

    ks = chat.knowledge_source
    if (
        ks is None
        or chat.lifecycle_status != LensSessionLink.LifecycleStatus.READY
        or ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY
    ):
        raise ValidationError({"chat": "Chat workspace is unavailable."})
    with transaction.atomic():
        locked = (
            LensKnowledgeSource.all_objects.select_for_update()
            .filter(
                pk=ks.id, lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY
            )
            .first()
        )
        if locked is None:
            raise ValidationError({"chat": "Chat workspace is being deleted."})
        current = dict(locked.sync_state_json or {})
        latest = dict(current.get(UPDATE_STATE_KEY) or {})
        if latest.get("status") != "failed":
            raise ValidationError({"chat": "Only a failed update can be abandoned."})
        if not assess_chat_restore_stop(
            locked, request_cancel=False
        ).confirmed or not managed_datasource.conversion_stop_confirmed(locked):
            raise ValidationError(
                {"chat": "Wait for the previous update tasks to stop."}
            )
        # Conversion reconciliation may persist a recovered task identity.
        # Re-read it under the same KS lock before changing only our marker.
        locked.refresh_from_db(fields=["sync_state_json", "last_restore_record_id"])
        current = dict(locked.sync_state_json or {})
        latest = dict(current.get(UPDATE_STATE_KEY) or {})
        if latest.get("status") != "failed":
            raise ValidationError(
                {"chat": "The data update state changed. Refresh and retry."}
            )
        latest["status"] = "abandoned"
        latest["error"] = (
            "Data may be partially updated. Choose a new snapshot to repair it."
        )
        current[UPDATE_STATE_KEY] = latest
        locked.sync_state_json = current
        locked.status = LensKnowledgeSource.Status.DEGRADED
        locked.status_detail = latest["error"]
        locked.save(
            update_fields=["sync_state_json", "status", "status_detail", "updated_at"]
        )
        release_snapshot_usage(
            snapshot_id=int(latest["target_snapshot_id"]),
            consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
            consumer_id=ks.id,
        )
        return latest


@transaction.atomic
def _claim_update(*, organization_id: int, knowledge_source_id: int) -> str | None:
    ks = (
        LensKnowledgeSource.all_objects.select_for_update()
        .filter(
            organization_id=organization_id,
            pk=knowledge_source_id,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
        )
        .first()
    )
    if ks is None:
        return None
    state = dict(ks.sync_state_json or {})
    update = dict(state.get(UPDATE_STATE_KEY) or {})
    if update.get("status") not in {"pending", "running"}:
        return None
    now = timezone.now()
    if (ks.sync_claimed_at and ks.sync_claimed_at > now - UPDATE_CLAIM_TTL) or (
        ks.sync_next_poll_at and ks.sync_next_poll_at > now
    ):
        return None

    def fail_unsafe_retry(message: str) -> None:
        update["status"] = "failed"
        update["error"] = message
        state[UPDATE_STATE_KEY] = update
        ks.sync_state_json = state
        ks.status = LensKnowledgeSource.Status.DEGRADED
        ks.status_detail = message
        ks.save(
            update_fields=["sync_state_json", "status", "status_detail", "updated_at"]
        )

    def conversion_stopped() -> bool:
        try:
            return managed_datasource.conversion_stop_confirmed(ks)
        except Exception:
            return False

    if update.get("phase") == "restore" and (
        update.get("status") == "pending" or update.get("new_request")
    ):
        previous_conversion = dict(state.get("conversion") or {})
        if previous_conversion and str(
            previous_conversion.get("status") or ""
        ).upper() not in {
            "SUCCESS",
            "FAILURE",
            "REVOKED",
        }:
            fail_unsafe_retry("An earlier conversion is still running.")
            return None
        if (
            str(previous_conversion.get("status") or "").upper()
            in {
                "FAILURE",
                "REVOKED",
            }
            and not conversion_stopped()
        ):
            fail_unsafe_retry("The previous conversion has not stopped.")
            return None
        previous_restore = state.get("restore_record_id")
        if update.pop("new_request", False):
            state.pop("restore_record_id", None)
            state.pop("snapshot_id_used", None)
            state.pop("restore_scope_status", None)
        elif previous_restore and knowledge_source_sync._restore_record_failed(
            record_id=int(previous_restore), organization_id=organization_id
        ):
            from apps.lens_bridge.services.knowledge_source_teardown import (
                assess_chat_restore_stop,
            )

            try:
                restore_stopped = assess_chat_restore_stop(
                    ks, request_cancel=False
                ).confirmed
            except Exception:
                restore_stopped = False
            if not restore_stopped:
                fail_unsafe_retry("The previous restore has not stopped.")
                return None
            # Retry uses a new idempotency generation only after the previous
            # restore has reached a terminal failure, never while it is unknown.
            update["generation"] = int(update.get("generation") or 1) + 1
            state.pop("restore_record_id", None)
            state.pop("snapshot_id_used", None)
            state.pop("restore_scope_status", None)
        elif previous_restore and state.get("snapshot_id_used") != update.get(
            "target_snapshot_id"
        ):
            state.pop("restore_record_id", None)
            state.pop("snapshot_id_used", None)
            state.pop("restore_scope_status", None)
        state["restore_generation"] = int(update.get("generation") or 1)
        state.pop("conversion", None)
    elif update.get("phase") == "convert" and update.get("status") == "pending":
        previous_conversion = dict(state.get("conversion") or {})
        previous_status = str(previous_conversion.get("status") or "").upper()
        if previous_status in {"FAILURE", "REVOKED"}:
            if not conversion_stopped():
                fail_unsafe_retry("The previous conversion has not stopped.")
                return None
            state.pop("conversion", None)
        elif previous_status == "SUCCESS" and int(
            (previous_conversion.get("summary") or {}).get("failed") or 0
        ):
            state.pop("conversion", None)
    token = str(uuid.uuid4())
    update["status"] = "running"
    state[UPDATE_STATE_KEY] = update
    ks.sync_state_json = state
    ks.sync_claim_token = uuid.UUID(token)
    ks.sync_claimed_at = now
    ks.sync_next_poll_at = None
    ks.save(
        update_fields=[
            "sync_state_json",
            "sync_claim_token",
            "sync_claimed_at",
            "sync_next_poll_at",
            "updated_at",
        ]
    )
    return token


def _finish_update_step(
    knowledge_source_id: int,
    token: str,
    *,
    status: str,
    phase: str | None = None,
    error: str = "",
    next_poll_at=None,
    apply_snapshot: bool = False,
    progress: dict | None = None,
) -> None:
    with transaction.atomic():
        ks = (
            LensKnowledgeSource.all_objects.select_for_update()
            .filter(pk=knowledge_source_id, sync_claim_token=token)
            .first()
        )
        if ks is None:
            raise RuntimeError("Chat data update lease was lost.")
        if (
            apply_snapshot
            and ks.lifecycle_status != LensKnowledgeSource.LifecycleStatus.READY
        ):
            raise RuntimeError("Chat workspace deletion superseded the data update.")
        state = dict(ks.sync_state_json or {})
        update = dict(state.get(UPDATE_STATE_KEY) or {})
        if progress:
            update.update(progress)
        update["status"] = status
        if phase is not None:
            update["phase"] = phase
        update["error"] = error[:1000]
        update["updated_at"] = timezone.now().isoformat()
        if apply_snapshot:
            ks.pinned_snapshot_id = int(update["target_snapshot_id"])
            ks.backup_source_snapshot_id = ks.pinned_snapshot_id
            rebound_scopes = list(update.get("scopes") or [])
            if rebound_scopes:
                prior_scopes = list(scope_entries(ks))
                if len(prior_scopes) != len(rebound_scopes):
                    raise RuntimeError("Chat data scope count changed during update.")
                if any(
                    str(prior.get("source_path") or "")
                    != str(rebound.get("source_path") or "")
                    for prior, rebound in zip(prior_scopes, rebound_scopes)
                ):
                    raise RuntimeError("Chat data source paths changed during update.")
                ks.source_scopes_json = [
                    {
                        **prior,
                        "backup_snapshot_directory_id": int(
                            rebound["snapshot_directory_id"]
                        ),
                        "size_bytes": int(rebound["size_bytes"]),
                        "file_count": int(rebound["file_count"]),
                        "path_type": str(rebound["path_type"]),
                    }
                    for prior, rebound in zip(prior_scopes, rebound_scopes)
                ]
                ks.backup_snapshot_directory_id = int(
                    rebound_scopes[0]["snapshot_directory_id"]
                )
            update["applied_snapshot_id"] = ks.pinned_snapshot_id
            state["phase"] = "finalize"
            state["last_sync_at"] = timezone.now().isoformat()
            ks.status = (
                LensKnowledgeSource.Status.DEGRADED
                if update.get("previous_status") == LensKnowledgeSource.Status.DEGRADED
                else LensKnowledgeSource.Status.READY
            )
            ks.status_detail = (
                "Chat data updated; the previous degraded state remains."
                if ks.status == LensKnowledgeSource.Status.DEGRADED
                else "Chat data update completed."
            )
            if "validated_bytes" in update:
                binding = LensWorkspaceBinding.objects.select_for_update().get(
                    knowledge_source=ks
                )
                binding.capacity_accounted_bytes = int(update["validated_bytes"])
                binding.capacity_accounting_status = (
                    LensWorkspaceBinding.CapacityAccountingStatus.EXACT
                )
                binding.save(
                    update_fields=[
                        "capacity_accounted_bytes",
                        "capacity_accounting_status",
                        "updated_at",
                    ]
                )
        elif (
            status == "failed"
            and ks.lifecycle_status == LensKnowledgeSource.LifecycleStatus.READY
        ):
            ks.status = LensKnowledgeSource.Status.DEGRADED
            ks.status_detail = "Chat data update failed. Chat remains available."
        elif status == "running":
            ks.status_detail = (
                "Extracting updated document content."
                if (phase or update.get("phase")) == "convert"
                else "Updating Chat data."
            )
        state[UPDATE_STATE_KEY] = update
        ks.sync_state_json = state
        ks.sync_claim_token = None
        ks.sync_claimed_at = None
        ks.sync_next_poll_at = next_poll_at
        ks.save(
            update_fields=[
                "pinned_snapshot_id",
                "backup_source_snapshot_id",
                "backup_snapshot_directory_id",
                "source_scopes_json",
                "status",
                "status_detail",
                "sync_state_json",
                "sync_claim_token",
                "sync_claimed_at",
                "sync_next_poll_at",
                "updated_at",
            ]
        )
        if apply_snapshot:
            release_snapshot_usage(
                snapshot_id=ks.pinned_snapshot_id,
                consumer_type=SnapshotUsageLease.ConsumerType.CHAT_UPDATE,
                consumer_id=ks.id,
            )


@transaction.atomic
def _reserve_capacity_and_start_restore(*, ks: LensKnowledgeSource, token: str) -> None:
    """Reserve the projected logical growth before changing any workspace data."""

    from apps.lens_bridge.services.public_gateway_capacity import (
        assert_public_gateway_capacity,
        lock_public_gateway_capacity,
        workspace_binding_occupancy,
    )
    from apps.subscription.services.quota import assert_gateway_select_within_limits

    org = Organization.objects.select_for_update().get(pk=ks.organization_id)
    gateway = (
        lock_public_gateway_capacity(gateway_link=ks.gateway_link)
        if ks.gateway_link.scope == LensGatewayLink.GatewayScope.PLATFORM
        else None
    )
    locked = (
        LensKnowledgeSource.all_objects.select_for_update()
        .filter(
            pk=ks.id,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
            sync_claim_token=token,
        )
        .first()
    )
    if locked is None:
        raise RuntimeError("Chat data update lease was lost during capacity admission.")
    state = dict(locked.sync_state_json or {})
    update = dict(state.get(UPDATE_STATE_KEY) or {})
    scopes = list(update.get("scopes") or [])
    if (
        not scopes
        or update.get("status") != "running"
        or update.get("phase") != "validate"
        or int(update.get("validation_index") or 0) != len(scopes)
        or "validated_bytes" not in update
        or "validated_files" not in update
        or any(
            scope.get("size_bytes") is None or scope.get("file_count") is None
            for scope in scopes
        )
    ):
        raise RuntimeError("Chat data scope validation is incomplete.")
    if any(not scope.get("snapshot_directory_source_path") for scope in scopes):
        raise RuntimeError("Chat data snapshot directory layout is missing.")
    restore_dir = locked.workspace_path_on_lensnode
    knowledge_source_sync._require_unchanged_chat_workspace_layout(
        org=org,
        ks=locked,
        update=update,
        restore_dir=restore_dir,
        candidate_items=[
            {
                "source_snapshot_directory_id": int(scope["snapshot_directory_id"]),
                "selected_paths": (
                    [str(scope["selected_path"])] if scope.get("selected_path") else []
                ),
                "target_source_path": str(scope["snapshot_directory_source_path"]),
                "source_path_type": str(
                    scope.get("snapshot_directory_path_type") or "unknown"
                ),
                "restore_dir": restore_dir,
                "conflict_mode": "overwrite",
            }
            for scope in scopes
        ],
    )
    total_bytes = int(update.get("validated_bytes") or 0)
    total_files = int(update.get("validated_files") or 0)
    if (
        total_bytes < 0
        or total_files < 0
        or total_bytes >= 2**63
        or total_files >= 2**63
    ):
        raise ValidationError({"snapshot_id": "Snapshot scope size is invalid."})
    assert_gateway_select_within_limits(
        organization=org,
        file_count=total_files,
        size_bytes=total_bytes,
        unknown_directory=False,
    )
    binding = LensWorkspaceBinding.objects.select_for_update().get(
        knowledge_source=locked,
        state=LensWorkspaceBinding.State.READY,
    )
    old_bytes, old_unknown = workspace_binding_occupancy(binding=binding)
    projected = max(old_bytes, total_bytes)
    delta = projected - old_bytes
    if gateway is not None and (delta or old_unknown):
        assert_public_gateway_capacity(
            gateway_link=gateway,
            additional_bytes=delta,
            unknown_size=old_unknown,
        )
        if delta:
            from common.extension_spi import get_quota_provider
            from apps.subscription.services.interface import enforce_license_quota

            provider = get_quota_provider()
            if provider is not None and "max_public_gateway_capacity_bytes" not in (
                provider.get_limits(org) or {}
            ):
                raise AppError(
                    code="SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE",
                    status=503,
                    retryable=True,
                    title="Organization public gateway capacity is unavailable.",
                    diagnostic="max_public_gateway_capacity_bytes missing from quota limits",
                )
            enforce_license_quota(
                org, "max_public_gateway_capacity_bytes", additional=delta
            )
    binding.capacity_accounted_bytes = projected
    binding.capacity_accounting_status = (
        LensWorkspaceBinding.CapacityAccountingStatus.UNKNOWN
        if old_unknown
        else LensWorkspaceBinding.CapacityAccountingStatus.CONSERVATIVE
    )
    binding.save(
        update_fields=[
            "capacity_accounted_bytes",
            "capacity_accounting_status",
            "updated_at",
        ]
    )
    update["capacity_reserved_bytes"] = projected
    update["phase"] = "restore"
    update["updated_at"] = timezone.now().isoformat()
    state[UPDATE_STATE_KEY] = update
    locked.sync_state_json = state
    locked.sync_claim_token = None
    locked.sync_claimed_at = None
    locked.sync_next_poll_at = timezone.now()
    locked.status_detail = "Restoring the selected Chat snapshot."
    locked.save(
        update_fields=[
            "sync_state_json",
            "sync_claim_token",
            "sync_claimed_at",
            "sync_next_poll_at",
            "status_detail",
            "updated_at",
        ]
    )


def _validate_target_scopes(
    *, ks: LensKnowledgeSource, token: str, update: dict
) -> dict:
    """Resolve every selected path before any restore mutates the workspace."""

    from apps.lens_bridge.services import snapshot_scope_tasks
    from apps.node.models import NodeTask

    scopes = list(update.get("scopes") or [])
    index = int(update.get("validation_index") or 0)
    if index >= len(scopes):
        _reserve_capacity_and_start_restore(ks=ks, token=token)
        return {"status": "waiting", "retry_after_seconds": 1}
    scope = scopes[index]
    correlation_id = (
        f"chat-update:{ks.id}:generation:{update['generation']}:"
        f"validation:{int(update.get('validation_generation') or 0)}:scope:{index}"
    )
    task_id = str(update.get("validation_task_id") or "")
    task = (
        snapshot_scope_tasks.scope_task_for_reference(
            organization=ks.organization,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        if task_id
        else None
    )
    if task is None:
        task = snapshot_scope_tasks.scope_task_for_correlation(
            organization=ks.organization, correlation_id=correlation_id
        )
    if task is None:
        task = snapshot_scope_tasks.dispatch_scope_resolution(
            organization_id=ks.organization_id,
            directory_id=int(scope["snapshot_directory_id"]),
            backup_source_snapshot_id=int(update["target_snapshot_id"]),
            gateway_link_id=ks.gateway_link_id,
            requesting_user_id=int(update["requested_by_user_id"]),
            path=str(scope.get("selected_path") or ""),
            correlation_id=correlation_id,
        )
    if task.status == NodeTask.Status.SUCCESS:
        summary = snapshot_scope_tasks.resolved_scope_summary(task)
        expected = str(scope.get("path_type") or "unknown")
        if expected in {"dir", "file"} and summary["path_type"] != expected:
            raise ValidationError({"snapshot_id": "Selected Chat path changed type."})
        if int(summary.get("skipped_special_count") or 0):
            raise ValidationError(
                {"snapshot_id": "New snapshot contains unsupported special files."}
            )
        scopes[index] = {
            **scope,
            "size_bytes": int(summary["size_bytes"]),
            "file_count": int(summary["file_count"]),
            "path_type": str(summary["path_type"]),
        }
        _finish_update_step(
            ks.id,
            token,
            status="running",
            phase="validate",
            progress={
                "validation_index": index + 1,
                "validation_task_id": "",
                "scopes": scopes,
                "validated_bytes": int(update.get("validated_bytes") or 0)
                + int(summary["size_bytes"]),
                "validated_files": int(update.get("validated_files") or 0)
                + int(summary["file_count"]),
            },
            next_poll_at=timezone.now(),
        )
        return {"status": "waiting", "retry_after_seconds": 1}
    if task.status not in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        raise ValidationError(
            {
                "snapshot_id": snapshot_scope_tasks.snapshot_task_error(
                    task, default="The selected path could not be checked."
                )
            }
        )
    _finish_update_step(
        ks.id,
        token,
        status="running",
        phase="validate",
        progress={"validation_task_id": str(task.id)},
        next_poll_at=timezone.now() + timedelta(seconds=5),
    )
    return {"status": "waiting", "retry_after_seconds": 5}


def run_chat_data_update(*, organization_id: int, knowledge_source_id: int) -> dict:
    """Resume the pinned restore and non-forced conversion without closing Chats."""

    token = _claim_update(
        organization_id=organization_id, knowledge_source_id=knowledge_source_id
    )
    if token is None:
        return {"status": "busy"}
    ks = LensKnowledgeSource.objects.select_related("organization").get(
        pk=knowledge_source_id
    )
    update: dict = {}
    try:
        state = dict(ks.sync_state_json or {})
        update = dict(state[UPDATE_STATE_KEY])
        knowledge_source_sync._require_active_lifecycle(ks)
        if update.get("phase") == "validate":
            return _validate_target_scopes(ks=ks, token=token, update=update)
        if update.get("phase") == "restore":
            knowledge_source_sync._run_phase_restore_snapshot(
                org=ks.organization, ks=ks, sync_state=state
            )
            _finish_update_step(
                ks.id,
                token,
                status="running",
                phase="convert",
                next_poll_at=timezone.now(),
            )
            return {"status": "waiting", "retry_after_seconds": 1}
        if update.get("phase") != "convert":
            raise ValueError("Unknown Chat data update phase.")
        knowledge_source_sync._require_active_lifecycle(ks)
        if not knowledge_source_sync.managed_conversion_enabled(
            org=ks.organization, ks=ks
        ):
            raise ValidationError(
                {"chat": "Managed document conversion is disabled for this Chat."}
            )
        policy = ingest_policy.normalize_ingest_policy(ks.ingest_policy_json)
        conversion = ingest_policy.conversion_payload_for_sl(policy)
        summary = managed_datasource.convert_documents(
            ks=ks, sync_state=state, conversion=conversion, force=False
        )
        if not isinstance(summary, dict) or "failed" not in summary:
            raise RuntimeError(
                "SourceLens did not confirm document conversion results."
            )
        if int(summary.get("failed") or 0):
            raise ValueError("Some documents could not be converted.")
        _finish_update_step(
            ks.id,
            token,
            status="complete",
            phase="complete",
            apply_snapshot=True,
        )
        return {"status": "complete"}
    except (
        knowledge_source_sync.KnowledgeSourceSyncPending,
        managed_datasource.ManagedDatasourcePending,
    ) as exc:
        delay = int(getattr(exc, "retry_after_seconds", 5))
        _finish_update_step(
            ks.id,
            token,
            status="running",
            next_poll_at=timezone.now() + timedelta(seconds=delay),
        )
        return {"status": "waiting", "retry_after_seconds": delay}
    except Exception as exc:
        logger.exception("Chat data update failed ks_id=%s", ks.id)
        if (
            isinstance(exc, AppError)
            and exc.code == "SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE"
        ):
            detail = (
                "Data Gateway capacity could not be verified. Retry the update later."
            )
        elif isinstance(exc, AppError) and exc.code.startswith("SUBSCRIPTION.QUOTA"):
            detail = (
                "The snapshot exceeds Data Gateway capacity or subscription limits."
            )
        elif (
            update.get("phase") == "restore"
            and "insufficient free space" in str(exc).lower()
        ):
            detail = "Data Gateway has insufficient free space. Free space and retry."
        elif isinstance(
            exc, knowledge_source_sync.KnowledgeSourceSyncError
        ) and "workspace layout" in str(exc):
            detail = (
                "The new snapshot cannot safely replace this Chat's workspace layout. "
                "Choose a compatible snapshot or create a new Chat."
            )
        elif update.get("phase") == "restore":
            detail = (
                "Chat data restore or reconciliation did not finish. Retry the update."
            )
        elif update.get("phase") == "convert":
            detail = "Document conversion did not finish. Retry the update."
        else:
            detail = "The selected paths could not be verified in the new snapshot."
        _finish_update_step(ks.id, token, status="failed", error=detail)
        return {"status": "failed", "error": detail}
