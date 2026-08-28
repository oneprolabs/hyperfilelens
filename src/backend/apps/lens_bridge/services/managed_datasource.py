"""Durable SourceLens managed-workspace conversion orchestration."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from django.utils import timezone

from apps.lens_bridge.models import LensKnowledgeSource, LensWorkspaceBinding
from apps.lens_bridge.services import sl_client

CONVERSION_WAIT_SECONDS = 6 * 3600
CONVERSION_RETRY_SECONDS = 5
CONVERSION_RECOVERY_CLOCK_SKEW_SECONDS = 60
CONVERSION_TRANSIENT_RETRY_MAX_SECONDS = 300
CONVERSION_IDLE_RETRY_MAX_SECONDS = 60


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


def _conversion_deadline_exceeded(started_at: Any) -> bool:
    """Return whether a durable conversion exceeded the end-to-end budget."""

    raw = str(started_at or "").strip()
    if not raw:
        return False
    try:
        started = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.get_current_timezone())
    return (timezone.now() - started).total_seconds() >= CONVERSION_WAIT_SECONDS


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
            state["lookup_missing_at"] = timezone.now().isoformat()
            _persist_conversion_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            if _conversion_deadline_exceeded(state.get("started_at")):
                raise ManagedDatasourceError(
                    "SourceLens conversion start could not be recovered."
                )
            raise ManagedDatasourcePending(
                "Document conversion start is being reconciled."
            )
        task_id = str(task.get("task_id") or "")
        state.update(
            {
                "task_id": task_id,
                "task_execution_id": task.get("id"),
                "status": str(task.get("status") or "PENDING"),
                "recovered_at": timezone.now().isoformat(),
            }
        )
        _persist_conversion_state(
            ks=ks,
            sync_state=sync_state,
            state=state,
        )
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
        if task is None:
            state["lookup_missing_at"] = timezone.now().isoformat()
            _persist_conversion_state(
                ks=ks,
                sync_state=sync_state,
                state=state,
            )
            if _conversion_deadline_exceeded(state.get("started_at")):
                raise ManagedDatasourceError(
                    "SourceLens conversion task could not be recovered."
                )
            raise ManagedDatasourcePending(
                "Document conversion state is being reconciled."
            )
        elif str(task.get("status") or "") == "SUCCESS":
            _clear_transient_state(state)
            summary = _conversion_summary(task)
            state.update(
                {
                    "status": "SUCCESS",
                    "summary": summary,
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
    if _conversion_deadline_exceeded(state.get("started_at")):
        raise ManagedDatasourceError(
            "SourceLens conversion did not complete before the wait timeout."
        )
    retry_after = min(
        CONVERSION_IDLE_RETRY_MAX_SECONDS,
        CONVERSION_RETRY_SECONDS * (2 ** min(idle_polls, 4)),
    )
    raise ManagedDatasourcePending(
        str(state.get("progress_message") or "Document conversion is running."),
        retry_after_seconds=retry_after,
    )
