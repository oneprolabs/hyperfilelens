"""Durable SourceLens managed-workspace conversion orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from django.utils import timezone

from apps.lens_bridge.models import LensKnowledgeSource, LensWorkspaceBinding
from apps.lens_bridge.services import sl_client

logger = logging.getLogger(__name__)

# A running SourceLens conversion may legitimately take longer than this.
# This attention window applies only when HFL cannot account for the remote
# task identity; it is not a conversion runtime limit.
CONVERSION_RECONCILIATION_ATTENTION_SECONDS = 2 * 3600
CONVERSION_RETRY_SECONDS = 5
CONVERSION_RECOVERY_CLOCK_SKEW_SECONDS = 60
CONVERSION_TRANSIENT_RETRY_MAX_SECONDS = 300
CONVERSION_IDLE_RETRY_MAX_SECONDS = 60
CONVERSION_RESUME_MAX_ATTEMPTS = 3
CONVERSION_REBIND_MAX_ATTEMPTS = 3
CONVERSION_REBIND_BASE_SECONDS = 30


class ManagedDatasourceError(RuntimeError):
    """Raised when a managed datasource cannot be safely reconciled."""


class ManagedDatasourcePending(RuntimeError):
    """Raised when durable conversion work must be polled by a later task."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int = CONVERSION_RETRY_SECONDS,
    ):
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


def _transient_retry_seconds(attempt: int) -> int:
    return min(
        CONVERSION_TRANSIENT_RETRY_MAX_SECONDS,
        15 * (2 ** max(0, min(int(attempt) - 1, 5))),
    )


def _record_transient_state(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    state: dict[str, Any],
    operation: str,
) -> int:
    count = int(state.get("transient_error_count") or 0) + 1
    state.update(
        {
            "transient_error_count": count,
            "last_transient_operation": operation,
            "last_transient_error_at": timezone.now().isoformat(),
        }
    )
    _persist_conversion_state(ks=ks, sync_state=sync_state, state=state)
    return _transient_retry_seconds(count)


def _clear_transient_state(state: dict[str, Any]) -> None:
    state.pop("transient_error_count", None)
    state.pop("last_transient_operation", None)
    state.pop("last_transient_error_at", None)


def _save_sync_state(
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> None:
    ks.sync_state_json = sync_state
    ks.save(update_fields=["sync_state_json", "updated_at"])


def _datasource_identity(ks: LensKnowledgeSource) -> tuple[str, str, str]:
    """Return the deterministic name, LensNode, and target path."""

    if not ks.sl_lensnode_uuid:
        raise ManagedDatasourceError(
            "Knowledge source has no linked SourceLens LensNode."
        )
    target_path = str(ks.workspace_path_on_lensnode or "").strip()
    if not target_path:
        raise ManagedDatasourceError("Knowledge source workspace path is not prepared.")
    try:
        workspace_uid = str(ks.workspace_binding.workspace_uid)
    except LensWorkspaceBinding.DoesNotExist as exc:
        raise ManagedDatasourceError(
            "Knowledge source has no authoritative workspace binding."
        ) from exc
    return (
        f"hfl-ks-{ks.id}-{workspace_uid}",
        str(ks.sl_lensnode_uuid),
        target_path,
    )


def _datasource_matches(
    row: dict[str, Any],
    *,
    name: str,
    lensnode_uuid: str,
    target_path: str,
) -> bool:
    """Return whether a remote row is the exact HFL-owned datasource."""

    remote_lensnode = str(row.get("lensnode_uuid") or row.get("lensnode") or "")
    return bool(
        row.get("source_type") == "managed_workspace"
        and str(row.get("name") or "") == name
        and remote_lensnode == lensnode_uuid
        and str(row.get("target_path") or "").rstrip("/") == target_path.rstrip("/")
    )


def _find_matching_datasource(
    *,
    name: str,
    lensnode_uuid: str,
    target_path: str,
) -> dict[str, Any] | None:
    """Find an exact datasource without adopting a path collision."""

    rows = sl_client.list_managed_datasources(target_path=target_path)
    exact = [
        row
        for row in rows
        if _datasource_matches(
            row,
            name=name,
            lensnode_uuid=lensnode_uuid,
            target_path=target_path,
        )
    ]
    if len(exact) > 1:
        raise ManagedDatasourceError(
            "Multiple SourceLens datasources match this HFL workspace."
        )
    if exact:
        return exact[0]
    if any(
        str(row.get("target_path") or "").rstrip("/") == target_path.rstrip("/")
        for row in rows
    ):
        raise ManagedDatasourceError(
            "SourceLens datasource path is owned by another resource."
        )
    return None


def ensure_managed_datasource(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
) -> uuid.UUID:
    """Create or recover the SourceLens datasource for one restore workspace."""

    name, lensnode_uuid, target_path = _datasource_identity(ks)
    journal = dict(sync_state.get("managed_datasource") or {})
    journal.update(
        {
            "lookup_key": name,
            "lensnode_uuid": lensnode_uuid,
            "target_path": target_path,
            "operation_status": "prepared",
            "updated_at": timezone.now().isoformat(),
        }
    )
    sync_state["managed_datasource"] = journal
    _save_sync_state(ks, sync_state)

    remote: dict[str, Any] | None = None
    if ks.sl_datasource_uuid:
        remote = sl_client.get_managed_datasource(str(ks.sl_datasource_uuid))
        if remote is not None and not _datasource_matches(
            remote,
            name=name,
            lensnode_uuid=lensnode_uuid,
            target_path=target_path,
        ):
            raise ManagedDatasourceError(
                "Stored SourceLens datasource identity does not match."
            )
    if remote is None:
        remote = _find_matching_datasource(
            name=name,
            lensnode_uuid=lensnode_uuid,
            target_path=target_path,
        )
    if remote is None:
        try:
            remote = sl_client.create_managed_datasource(
                name=name,
                lensnode_uuid=lensnode_uuid,
                target_path=target_path,
            )
        except sl_client.LensBridgeError:
            remote = _find_matching_datasource(
                name=name,
                lensnode_uuid=lensnode_uuid,
                target_path=target_path,
            )
            if remote is None:
                raise

    datasource_uuid = uuid.UUID(str(remote["uuid"]))
    journal.update(
        {
            "operation_status": "confirmed",
            "remote_uuid": str(datasource_uuid),
            "updated_at": timezone.now().isoformat(),
        }
    )
    sync_state["managed_datasource"] = journal
    ks.sl_datasource_uuid = datasource_uuid
    ks.sync_state_json = sync_state
    ks.save(
        update_fields=[
            "sl_datasource_uuid",
            "sync_state_json",
            "updated_at",
        ]
    )
    return datasource_uuid


def conversion_policy_fingerprint(conversion: dict[str, Any]) -> str:
    """Return a stable fingerprint for one SourceLens conversion policy."""

    raw = json.dumps(
        conversion,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_missing_conversion(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """Track a missing remote task without treating it as stopped."""

    now = timezone.now()
    first_missing = _parse_timestamp(state.get("lookup_first_missing_at"))
    if first_missing is None:
        first_missing = now
        state["lookup_first_missing_at"] = now.isoformat()
    state["lookup_missing_at"] = now.isoformat()
    needs_attention = (
        (now - first_missing).total_seconds()
        >= CONVERSION_RECONCILIATION_ATTENTION_SECONDS
    )
    if needs_attention and not state.get("reconciliation_attention_at"):
        state["reconciliation_attention_at"] = now.isoformat()
        logger.warning(
            "conversion task is still unaccounted for "
            "knowledge_source_id=%s datasource_uuid=%s task_id=%s",
            ks.id,
            ks.sl_datasource_uuid,
            state.get("task_id"),
        )
    detail = (
        "Document conversion cannot be located in SourceLens; recovery needs "
        "attention. The workspace is retained until its task is accounted for."
        if needs_attention
        else "Document conversion state is being reconciled."
    )
    state["progress_message"] = detail
    _persist_conversion_state(ks=ks, sync_state=sync_state, state=state)
    return detail


def _clear_missing_conversion(state: dict[str, Any]) -> None:
    state.pop("lookup_first_missing_at", None)
    state.pop("lookup_missing_at", None)
    state.pop("reconciliation_attention_at", None)


def _recover_missing_conversion_task(
    *,
    datasource_uuid: str,
    task_id: str,
) -> dict[str, Any] | None:
    """Recover the exact task from the datasource-scoped task list."""

    for row in sl_client.list_managed_datasource_conversion_tasks(datasource_uuid):
        if str(row.get("task_id") or "") == task_id:
            return row
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse one upstream timestamp into an aware datetime."""

    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.get_current_timezone())
    return parsed


def _conversion_summary(task: dict[str, Any]) -> dict[str, Any]:
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    summary = result.get("conversion_summary") or metadata.get("conversion_summary")
    return dict(summary) if isinstance(summary, dict) else {}


def _conversion_warnings(
    summary: dict[str, Any],
    *,
    visual_model_configured: bool,
) -> list[str]:
    warnings = [str(item) for item in summary.get("warnings") or []]
    if int(summary.get("failed") or 0) > 0:
        warnings.append("CONVERSION_PARTIAL_FAILED")
    if not visual_model_configured:
        warnings.append("VISUAL_MODEL_NOT_CONFIGURED")
    return list(dict.fromkeys(warnings))


def _progress_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _all_supported_documents_unreadable(
    summary: dict[str, Any],
) -> bool:
    """Return true only when a complete summary proves no document is usable."""

    total = int(summary.get("total") or 0)
    unsupported = int(summary.get("unsupported") or 0)
    candidates = int(summary.get("candidates") or max(total - unsupported, 0))
    success = int(summary.get("success") or 0)
    truncated = int(summary.get("items_truncated") or 0)
    items = [row for row in summary.get("items") or [] if isinstance(row, dict)]
    unchanged = sum(1 for row in items if str(row.get("reason") or "") == "UNCHANGED")
    return bool(
        candidates > 0
        and unsupported == 0
        and success + unchanged == 0
        and truncated == 0
    )


def _persist_conversion_state(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    state: dict[str, Any],
) -> None:
    sync_state["conversion"] = state
    _save_sync_state(ks, sync_state)


def _restart_stopped_conversion_on_manual_retry(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    task_id: str,
    reason: str,
) -> None:
    """Discard a terminal task only after SL proves its executor stopped."""

    if not conversion_stop_confirmed(ks):
        raise ManagedDatasourcePending(
            "Waiting for SourceLens to confirm the previous conversion stopped.",
            retry_after_seconds=CONVERSION_IDLE_RETRY_MAX_SECONDS,
        )
    history = list(sync_state.get("conversion_history") or [])
    history.append(
        {"task_id": task_id, "reason": reason, "at": timezone.now().isoformat()}
    )
    sync_state["conversion_history"] = history[-10:]
    sync_state.pop("conversion", None)
    _save_sync_state(ks, sync_state)
    raise ManagedDatasourcePending(
        "Previous conversion stopped; preparing a fresh conversion."
    )


def _task_conversion_policy(task: dict[str, Any]) -> dict[str, Any] | None:
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return None
    conversion = metadata.get("conversion")
    return conversion if isinstance(conversion, dict) else None


def _task_operation_id(task: dict[str, Any]) -> str:
    """Return an HFL operation id exposed by a compatible SourceLens."""

    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(
        metadata.get("hfl_operation_id")
        or metadata.get("operation_id")
        or metadata.get("idempotency_key")
        or ""
    ).strip()


def _recover_started_conversion(
    *,
    datasource_uuid: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Recover a conversion whose POST response was not durably observed."""

    fingerprint = str(state.get("policy_fingerprint") or "")
    operation_id = str(state.get("operation_id") or "").strip()
    requested_at = _parse_timestamp(
        state.get("start_requested_at") or state.get("started_at")
    )
    earliest = (
        requested_at - timedelta(seconds=CONVERSION_RECOVERY_CLOCK_SKEW_SECONDS)
        if requested_at
        else None
    )
    matches: list[dict[str, Any]] = []
    rows = sl_client.list_managed_datasource_conversion_tasks(datasource_uuid)
    for row in rows:
        task = row
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            continue
        if not isinstance(row.get("metadata"), dict):
            task = sl_client.get_task_by_id(task_id) or row
        created_at = _parse_timestamp(task.get("created_at") or row.get("created_at"))
        if earliest and (created_at is None or created_at < earliest):
            continue
        remote_operation_id = _task_operation_id(task)
        if operation_id:
            if remote_operation_id != operation_id:
                continue
            policy = _task_conversion_policy(task)
            if (
                policy is not None
                and conversion_policy_fingerprint(policy) != fingerprint
            ):
                continue
            matches.append(task)
            continue
        policy = _task_conversion_policy(task)
        if policy is None:
            continue
        if conversion_policy_fingerprint(policy) == fingerprint:
            matches.append(task)
    if not matches:
        return None
    matches.sort(
        key=lambda row: _parse_timestamp(row.get("created_at")) or datetime.min.replace(
            tzinfo=timezone.get_current_timezone()
        ),
        reverse=True,
    )
    return matches[0]


def _final_callback_acknowledged(task: dict[str, Any]) -> bool:
    """Return whether a cancelled dispatch received its final LensNode callback."""

    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("conversion_stop_acknowledged_at")
        or metadata.get("lensnode_final_callback_at")
    )


@dataclass(frozen=True)
class ConversionStopAssessment:
    """SourceLens evidence used to fence destructive workspace cleanup."""

    confirmed: bool
    task_id: str = ""
    remote_status: str = ""
    stop_confirmation_source: str = ""
    reason: str = ""


def assess_conversion_stop(ks: LensKnowledgeSource) -> ConversionStopAssessment:
    """Return SourceLens' durable proof that a conversion executor stopped."""

    conversion_state = (ks.sync_state_json or {}).get("conversion")
    if not isinstance(conversion_state, dict):
        return ConversionStopAssessment(True, reason="no_conversion")
    task_id = str(conversion_state.get("task_id") or "").strip()
    if not task_id:
        if str(conversion_state.get("status") or "").upper() != "STARTING":
            return ConversionStopAssessment(True, reason="no_dispatched_task")
        if not ks.sl_datasource_uuid:
            return ConversionStopAssessment(False, reason="unresolved_start")
        task = _recover_started_conversion(
            datasource_uuid=str(ks.sl_datasource_uuid),
            state=conversion_state,
        )
        if task is None:
            # An empty list/cancel probe can race an in-flight conversion POST.
            # Only an operation-key lookup or a final callback can prove safety.
            return ConversionStopAssessment(False, reason="unresolved_start")
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return ConversionStopAssessment(False, reason="unresolved_start")
        conversion_state["task_id"] = task_id
        conversion_state["task_execution_id"] = task.get("id")
        conversion_state["status"] = str(task.get("status") or "PENDING")
        sync_state = dict(ks.sync_state_json or {})
        sync_state["conversion"] = conversion_state
        _save_sync_state(ks, sync_state)
    else:
        task = None
    task = task or sl_client.get_task_by_id(task_id)
    if task is None:
        return ConversionStopAssessment(
            False,
            task_id=task_id,
            reason="remote_task_missing",
        )
    returned_task_id = str(task.get("task_id") or "").strip()
    if returned_task_id != task_id:
        return ConversionStopAssessment(
            False,
            task_id=task_id,
            remote_status=str(task.get("status") or "").upper(),
            reason="remote_task_identity_mismatch",
        )
    status = str(task.get("status") or "").upper()
    if status == "SUCCESS":
        return ConversionStopAssessment(
            True,
            task_id=task_id,
            remote_status=status,
            reason="conversion_completed",
        )
    if status not in {"FAILURE", "REVOKED"}:
        return ConversionStopAssessment(
            False,
            task_id=task_id,
            remote_status=status,
            reason="conversion_still_running",
        )
    manual_confirmation = conversion_state.get("manual_stop_confirmation")
    if (
        isinstance(manual_confirmation, dict)
        and manual_confirmation.get("confirmed") is True
        and str(manual_confirmation.get("task_id") or "") == task_id
    ):
        return ConversionStopAssessment(
            True,
            task_id=task_id,
            remote_status=status,
            stop_confirmation_source="operator",
            reason="manual_stop_confirmation",
        )
    metadata = (
        task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    )
    completion_source = str(metadata.get("completion_source") or "").strip()
    stop_source = str(metadata.get("stop_confirmation_source") or "").strip()
    if stop_source == "queued_before_dispatch":
        return ConversionStopAssessment(
            True,
            task_id=task_id,
            remote_status=status,
            stop_confirmation_source=stop_source,
            reason="queued_before_dispatch",
        )
    if (
        completion_source == "lensnode_callback"
        and stop_source == "lensnode_callback"
    ):
        return ConversionStopAssessment(
            True,
            task_id=task_id,
            remote_status=status,
            stop_confirmation_source=stop_source,
            reason="lensnode_callback",
        )
    if _final_callback_acknowledged(task):
        return ConversionStopAssessment(
            True,
            task_id=task_id,
            remote_status=status,
            stop_confirmation_source="legacy_callback_timestamp",
            reason="legacy_callback_timestamp",
        )
    if not (
        metadata.get("timeout_cancelled_at")
        or metadata.get("manual_revoked_at")
    ):
        # Older SourceLens versions exposed the completed conversion summary
        # before adding explicit callback provenance.
        if status == "FAILURE" and "conversion_summary" in metadata:
            return ConversionStopAssessment(
                True,
                task_id=task_id,
                remote_status=status,
                stop_confirmation_source="legacy_conversion_summary",
                reason="legacy_conversion_summary",
            )
    return ConversionStopAssessment(
        False,
        task_id=task_id,
        remote_status=status,
        stop_confirmation_source=stop_source,
        reason="terminal_stop_unconfirmed",
    )


def conversion_stop_confirmed(ks: LensKnowledgeSource) -> bool:
    """Return whether SourceLens proves the LensNode conversion has stopped."""

    return assess_conversion_stop(ks).confirmed


def convert_documents(
    *,
    ks: LensKnowledgeSource,
    sync_state: dict[str, Any],
    conversion: dict[str, Any],
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Start or poll one conversion, yielding while durable work continues."""

    if not ks.sl_datasource_uuid:
        raise ManagedDatasourceError("Knowledge source has no SourceLens datasource.")
    fingerprint = conversion_policy_fingerprint(conversion)
    state = dict(sync_state.get("conversion") or {})
    task_id = str(state.get("task_id") or "")
    task: dict[str, Any] | None = None
    if state and state.get("policy_fingerprint") != fingerprint:
        raise ManagedDatasourcePending(
            "A previous document conversion must finish before applying "
            "the updated conversion policy."
        )
    if (
        not task_id
        and state.get("policy_fingerprint") == fingerprint
        and str(state.get("status") or "").upper() == "STARTING"
    ):
        try:
            task = _recover_started_conversion(
                datasource_uuid=str(ks.sl_datasource_uuid),
                state=state,
            )
        except sl_client.LensBridgeUnavailable as exc:
            retry_after = _record_transient_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
                operation="recover_conversion_start",
            )
            raise ManagedDatasourcePending(
                "SourceLens is temporarily unavailable while reconciling the conversion.",
                retry_after_seconds=retry_after,
            ) from exc
        if task is None:
            detail = _record_missing_conversion(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            raise ManagedDatasourcePending(
                detail,
                retry_after_seconds=CONVERSION_IDLE_RETRY_MAX_SECONDS,
            )
        task_id = str(task.get("task_id") or "")
        state.update(
            {
                "task_id": task_id,
                "task_execution_id": task.get("id"),
                # The datasource task list is an identity/summary lookup.
                # Do not expose its terminal-looking status before the full
                # task response has been fetched below.
                "status": "PENDING",
                "recovered_at": timezone.now().isoformat(),
            }
        )
        _persist_conversion_state(
            ks=ks,
            sync_state=sync_state,
            state=state,
        )
        # The datasource list is used only to recover identity. Its rows may
        # lack the final conversion summary; always poll the full task before
        # advancing this Knowledge Source.
        task = None
    if task_id and state.get("policy_fingerprint") == fingerprint:
        try:
            task = task or sl_client.get_task_by_id(task_id)
        except sl_client.LensBridgeUnavailable as exc:
            retry_after = _record_transient_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
                operation="poll_conversion",
            )
            raise ManagedDatasourcePending(
                "SourceLens is temporarily unavailable while checking conversion progress.",
                retry_after_seconds=retry_after,
            ) from exc
        if task is not None and str(task.get("task_id") or "") != task_id:
            detail = _record_missing_conversion(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            raise ManagedDatasourcePending(
                detail,
                retry_after_seconds=CONVERSION_IDLE_RETRY_MAX_SECONDS,
            )
        if task is None:
            try:
                listed_task = _recover_missing_conversion_task(
                    datasource_uuid=str(ks.sl_datasource_uuid),
                    task_id=task_id,
                )
            except sl_client.LensBridgeUnavailable as exc:
                retry_after = _record_transient_state(
                    ks=ks,
                    sync_state=sync_state,
                    state=state,
                    operation="reconcile_conversion",
                )
                raise ManagedDatasourcePending(
                    "SourceLens is temporarily unavailable while reconciling conversion.",
                    retry_after_seconds=retry_after,
                ) from exc
            detail = _record_missing_conversion(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            if listed_task is not None:
                # SL's datasource task list is a paginated summary; it may
                # omit the result and final callback evidence. Only the full
                # by-task-id response may advance conversion or prove success.
                detail = (
                    "SourceLens lists the conversion task, but its full "
                    "status is unavailable; recovery needs attention."
                    if state.get("reconciliation_attention_at")
                    else "SourceLens lists the conversion task, but its full "
                    "status is temporarily unavailable."
                )
                state["progress_message"] = detail
                _persist_conversion_state(ks=ks, sync_state=sync_state, state=state)
            raise ManagedDatasourcePending(
                detail,
                retry_after_seconds=CONVERSION_IDLE_RETRY_MAX_SECONDS,
            )
        _clear_missing_conversion(state)
        if str(task.get("status") or "") == "SUCCESS":
            _clear_transient_state(state)
            summary = _conversion_summary(task)
            metadata = (
                task.get("metadata")
                if isinstance(task.get("metadata"), dict)
                else {}
            )
            state.update(
                {
                    "status": "SUCCESS",
                    "summary": summary,
                    "progress_message": str(metadata.get("progress_message") or ""),
                    "warnings": _conversion_warnings(
                        summary,
                        visual_model_configured=bool(
                            conversion.get("vision_model_ref")
                        ),
                    ),
                    "finished_at": str(task.get("finished_at") or ""),
                }
            )
            _persist_conversion_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            if _all_supported_documents_unreadable(summary):
                raise ManagedDatasourceError(
                    "No selected document could be converted into readable text."
                )
            return summary

        elif str(task.get("status") or "") in {"FAILURE", "REVOKED"}:
            error = str(
                task.get("error") or "DATASOURCE_CONVERSION_FAILED"
            )
            recovery = None
            try:
                recovery = sl_client.get_managed_datasource_conversion_recovery(
                    str(ks.sl_datasource_uuid),
                    task_id,
                )
            except sl_client.LensBridgeUnavailable as exc:
                retry_after = _record_transient_state(
                    ks=ks,
                    sync_state=sync_state,
                    state=state,
                    operation="recover_conversion",
                )
                raise ManagedDatasourcePending(
                    "SourceLens is temporarily unavailable while checking "
                    "conversion recovery.",
                    retry_after_seconds=retry_after,
                ) from exc
            if isinstance(recovery, dict):
                state["recovery"] = recovery
                if recovery.get("restart_required") is True:
                    if state.get("manual_retry_pending"):
                        # Only a user-triggered retry may restart after SL
                        # requires restart. Never clear an active task.
                        _restart_stopped_conversion_on_manual_retry(
                            ks=ks,
                            sync_state=sync_state,
                            task_id=task_id,
                            reason="restart_required",
                        )
                    state["status"] = "FAILURE"
                    state["error"] = (
                        "DATASOURCE_CONVERSION_RESTART_REQUIRED"
                    )
                    _persist_conversion_state(
                        ks=ks,
                        sync_state=sync_state,
                        state=state,
                    )
                    raise ManagedDatasourceError(state["error"])
                # Only orphaned conversions may be resumed automatically.
                # A regular conversion failure can also carry a checkpoint,
                # but retrying it here would silently repeat a permanent
                # document/model failure forever.
                if recovery.get("orphaned") is True and recovery.get("resumable"):
                    resume_attempts = int(state.get("resume_attempts") or 0)
                    rebind_attempts = int(state.get("rebind_attempts") or 0)
                    rebind = recovery.get("resume_source") == "lensnode_executor"
                    if rebind and rebind_attempts >= CONVERSION_REBIND_MAX_ATTEMPTS:
                        state["status"] = "FAILURE"
                        state["error"] = "DATASOURCE_CONVERSION_REBIND_PAUSED"
                        _persist_conversion_state(
                            ks=ks, sync_state=sync_state, state=state
                        )
                        raise ManagedDatasourceError(state["error"])
                    if not rebind and resume_attempts >= CONVERSION_RESUME_MAX_ATTEMPTS:
                        state["status"] = "FAILURE"
                        state["error"] = (
                            "DATASOURCE_CONVERSION_RESUME_EXHAUSTED"
                        )
                        _persist_conversion_state(
                            ks=ks,
                            sync_state=sync_state,
                            state=state,
                        )
                        raise ManagedDatasourceError(state["error"])
                    rebind_due = _parse_timestamp(state.get("rebind_next_retry_at"))
                    if rebind and rebind_due and rebind_due > timezone.now():
                        raise ManagedDatasourcePending(
                            "Waiting for the LensNode connection to stabilize.",
                            retry_after_seconds=max(
                                1, int((rebind_due - timezone.now()).total_seconds()),
                            ),
                        )
                    try:
                        resumed = sl_client.resume_managed_datasource_conversion(
                            str(ks.sl_datasource_uuid),
                            task_id,
                        )
                    except sl_client.LensBridgeUnavailable as exc:
                        retry_after = _record_transient_state(
                            ks=ks,
                            sync_state=sync_state,
                            state=state,
                            operation="resume_conversion",
                        )
                        raise ManagedDatasourcePending(
                            "SourceLens is temporarily unavailable while "
                            "resuming conversion.",
                            retry_after_seconds=retry_after,
                        ) from exc
                    next_task_id = str(resumed.get("task_id") or "")
                    if resumed.get("restart_required"):
                        state["status"] = "FAILURE"
                        state["error"] = (
                            "SourceLens conversion restart is required."
                        )
                        _persist_conversion_state(
                            ks=ks,
                            sync_state=sync_state,
                            state=state,
                        )
                        raise ManagedDatasourceError(state["error"])
                    resume_reason = str(resumed.get("reason") or "")
                    task_already_running = resume_reason in {
                        "ALREADY_RESUMED",
                        "CONVERSION_ALREADY_RUNNING",
                        "CONVERSION_ALREADY_COMPLETED",
                    }
                    if (
                        next_task_id
                        and (
                            resumed.get("resumed") is True
                            or task_already_running
                        )
                    ):
                        resumed_at = timezone.now().isoformat()
                        original_task_id = str(
                            state.get("original_task_id") or task_id
                        )
                        state.pop("error", None)
                        state.pop("finished_at", None)
                        progress_message = (
                            "Document conversion is already running."
                            if resume_reason == "CONVERSION_ALREADY_RUNNING"
                            else (
                                "Document conversion has already completed."
                                if resume_reason == "CONVERSION_ALREADY_COMPLETED"
                                else (
                                    "Document conversion is resuming from "
                                    "the latest safe checkpoint."
                                )
                            )
                        )
                        state.update(
                            {
                                "original_task_id": original_task_id,
                                "task_id": next_task_id,
                                "status": str(
                                    resumed.get("status") or "PENDING"
                                ),
                                "resume_source": resumed.get("resume_source"),
                                "resume_reason": resumed.get("reason"),
                                "progress_message": progress_message,
                                "resumed_at": resumed_at,
                                "resume_started_at": resumed_at,
                            }
                        )
                        if (
                            resumed.get("resumed") is True
                            and resumed.get("resume_source") == "lensnode_executor"
                        ):
                            state["rebind_attempts"] = rebind_attempts + 1
                            wait_seconds = min(
                                300,
                                CONVERSION_REBIND_BASE_SECONDS * (2 ** rebind_attempts),
                            )
                            state["rebind_next_retry_at"] = (
                                timezone.now() + timedelta(seconds=wait_seconds)
                            ).isoformat()
                        elif (
                            resumed.get("resumed") is True
                            and resumed.get("resume_source") == "checkpoint"
                            and next_task_id != task_id
                            and resume_reason != "ALREADY_RESUMED"
                        ):
                            state["resume_attempts"] = resume_attempts + 1
                        state["recovery"] = {
                            **recovery,
                            **resumed,
                            "original_task_id": original_task_id,
                        }
                        _clear_transient_state(state)
                        _persist_conversion_state(
                            ks=ks,
                            sync_state=sync_state,
                            state=state,
                        )
                        raise ManagedDatasourcePending(
                            "Document conversion is resuming from the "
                            "latest safe checkpoint."
                        )
                if (
                    recovery.get("orphaned") is True
                    and recovery.get("reason") == "LENSNODE_UNAVAILABLE"
                ):
                    _persist_conversion_state(
                        ks=ks,
                        sync_state=sync_state,
                        state=state,
                    )
                    raise ManagedDatasourcePending(
                        "Waiting for the LensNode before resuming document conversion.",
                        retry_after_seconds=CONVERSION_IDLE_RETRY_MAX_SECONDS,
                    )
                if (
                    state.get("manual_retry_pending")
                    and recovery.get("orphaned") is False
                    and str(task.get("status") or "") in {"FAILURE", "REVOKED"}
                ):
                    # A permanent failure may still carry a checkpoint, but
                    # SL must not automatically repeat the same bad document.
                    # After a user fixes the cause, safely restart instead.
                    _restart_stopped_conversion_on_manual_retry(
                        ks=ks,
                        sync_state=sync_state,
                        task_id=task_id,
                        reason="manual_retry_terminal_failure",
                    )
            state["status"] = str(task.get("status") or "FAILURE")
            state["error"] = error
            _persist_conversion_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            raise ManagedDatasourceError(error)

    if task is None:
        requested_at = timezone.now().isoformat()
        operation_id = str(uuid.uuid4())
        state = {
            "operation_id": operation_id,
            "status": "STARTING",
            "policy_fingerprint": fingerprint,
            "start_requested_at": requested_at,
            "started_at": requested_at,
            "summary": {},
            "warnings": [],
        }
        _persist_conversion_state(
            ks=ks,
            sync_state=sync_state,
            state=state,
        )
        try:
            started = sl_client.start_managed_datasource_conversion(
                datasource_uuid=str(ks.sl_datasource_uuid),
                conversion=conversion,
                operation_id=operation_id,
                force=force,
            )
        except sl_client.LensBridgeError as exc:
            state["start_error"] = str(exc.detail)[:500]
            if 400 <= exc.status_code < 500:
                state["status"] = "FAILURE"
                _persist_conversion_state(
                    ks=ks,
                    sync_state=sync_state,
                    state=state,
                )
                raise ManagedDatasourceError(
                    "SourceLens rejected the document conversion request."
                ) from exc
            retry_after = _record_transient_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
                operation="start_conversion",
            )
            raise ManagedDatasourcePending(
                "Document conversion start is being reconciled.",
                retry_after_seconds=retry_after,
            ) from exc
        task_id = str(started["task_id"])
        state.update(
            {
                "task_id": task_id,
                "task_execution_id": started.get("task_execution_id"),
                "status": str(started.get("status") or "PENDING"),
                "start_confirmed_at": timezone.now().isoformat(),
            }
        )
        _clear_transient_state(state)
        _persist_conversion_state(
            ks=ks,
            sync_state=sync_state,
            state=state,
        )
        raise ManagedDatasourcePending("Document conversion is queued.")

    status = str(task.get("status") or "")
    metadata = (
        task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    )
    summary = _conversion_summary(task)
    progress_counts = (
        metadata.get("progress_counts")
        if isinstance(metadata.get("progress_counts"), dict)
        else {}
    )
    previous_counts = state.get("progress_counts") or {}
    previous_summary = state.get("summary") or {}
    substantive_keys = ("processed", "converted", "success", "skipped", "failed")
    if any(
        _progress_count(current.get(key)) > _progress_count(previous.get(key))
        for current, previous in (
            (progress_counts, previous_counts),
            (summary, previous_summary),
        )
        for key in substantive_keys
    ):
        # Count consecutive rebinds without actual file progress, not every
        # reconnect across a healthy multi-hour conversion.
        state["rebind_attempts"] = 0
        state.pop("rebind_next_retry_at", None)
    previous_progress = str(state.get("progress_fingerprint") or "")
    progress_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "status": status,
                "step": metadata.get("progress_step") or "",
                "message": metadata.get("progress_message") or "",
                "percent": metadata.get("progress_percent"),
                "summary": summary,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    idle_polls = (
        int(state.get("idle_poll_count") or 0) + 1
        if previous_progress == progress_fingerprint
        else 0
    )
    state.update(
        {
            "status": status,
            "summary": summary,
            "progress_counts": progress_counts,
            "progress_step": metadata.get("progress_step") or "",
            "progress_message": metadata.get("progress_message") or "",
            "progress_percent": metadata.get("progress_percent"),
            "warnings": _conversion_warnings(
                summary,
                visual_model_configured=bool(conversion.get("vision_model_ref")),
            ),
            "progress_fingerprint": progress_fingerprint,
            "idle_poll_count": idle_polls,
        }
    )
    _clear_transient_state(state)
    _persist_conversion_state(
        ks=ks,
        sync_state=sync_state,
        state=state,
    )
    if progress:
        progress(str(state.get("progress_message") or ""))
    if status == "SUCCESS":
        state["finished_at"] = str(task.get("finished_at") or "")
        _persist_conversion_state(
            ks=ks,
            sync_state=sync_state,
            state=state,
        )
        if _all_supported_documents_unreadable(summary):
            raise ManagedDatasourceError(
                "No selected document could be converted into readable text."
            )
        return summary
    if status in {"FAILURE", "REVOKED"}:
        error = str(task.get("error") or "DATASOURCE_CONVERSION_FAILED")
        state["error"] = error
        _persist_conversion_state(
            ks=ks,
            sync_state=sync_state,
            state=state,
        )
        raise ManagedDatasourceError(error)
    retry_after = min(
        CONVERSION_IDLE_RETRY_MAX_SECONDS,
        CONVERSION_RETRY_SECONDS * (2 ** min(idle_polls, 4)),
    )
    raise ManagedDatasourcePending(
        str(state.get("progress_message") or "Document conversion is running."),
        retry_after_seconds=retry_after,
    )
