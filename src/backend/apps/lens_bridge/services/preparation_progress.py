"""Read-only, tenant-scoped progress summaries for Chat preparation."""

from __future__ import annotations

import math
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.lens_bridge.models import LensSessionLink, LensWorkspaceBinding
from apps.protection.services.progress.restore_runtime import build_restore_kopia_progress
from apps.restore.models import RestoreRecord
from apps.task.models import Task


def nonnegative_number(value: Any) -> float | None:
    """Reject missing, invalid, negative, and non-finite progress values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def restore_progress_view(payload: dict[str, Any], *, status: str) -> dict[str, Any]:
    """Reuse Protection metrics without exposing paths or task identities."""
    aggregate = payload.get("aggregate") or {}
    transfer = payload.get("transfer_progress") or {}
    phase = str(payload.get("orchestration_phase") or transfer.get("phase") or "preparing")
    total = nonnegative_number(aggregate.get("bytes_total"))
    done = nonnegative_number(aggregate.get("bytes_done"))
    if status == "success":
        terminal_done = nonnegative_number(transfer.get("bytes_done"))
        if terminal_done is not None:
            done = terminal_done
    known = (
        aggregate.get("bytes_total_known") is True
        and not aggregate.get("bytes_total_reference")
        and total is not None and total > 0
    )
    percent = None
    if known and phase in {"transferring", "finalizing", "done"}:
        percent = nonnegative_number(aggregate.get("percent"))
        if percent is not None:
            percent = min(100 if status == "success" else 99, percent)

    # A fast lane's ETA cannot represent another lane that has not started
    # or whose metrics have expired. Protection already expires stale samples.
    active_lanes = [
        row for row in payload.get("lanes") or []
        if row.get("status") in {"pending", "dispatching", "running", "creating"}
    ]
    eta = nonnegative_number(transfer.get("eta_seconds"))
    if (
        status != "running" or phase != "transferring"
        or not known or done is None or done <= 0 or done >= total
        or not active_lanes
        or any(nonnegative_number(row.get("eta_seconds")) in (None, 0) for row in active_lanes)
    ):
        eta = None
    if status not in {"running", "success"}:
        percent = None
        eta = None
    return {
        "status": status,
        "phase": phase,
        "progress_percent": percent,
        "bytes_done": int(done) if done is not None else None,
        "bytes_total": int(total) if known else None,
        "eta_seconds": int(eta) if eta is not None and eta > 0 else None,
    }


def _restore_metrics_are_fresh(record: RestoreRecord) -> bool:
    """Require every active lane's received sample within the 30-second window."""
    samples = list(record.items.filter(status__in=("pending", "running")).values_list(
        "last_progress_sample", flat=True,
    ))
    if not samples:
        return False
    now = timezone.now()
    for sample in samples:
        raw = sample.get("sampled_at") if isinstance(sample, dict) else None
        try:
            sampled_at = parse_datetime(str(raw or ""))
        except (ValueError, TypeError, OverflowError):
            return False
        if sampled_at is None or timezone.is_naive(sampled_at):
            return False
        if not 0 <= (now - sampled_at).total_seconds() <= 30:
            return False
    return True


def preparation_progress_for_session(link: LensSessionLink) -> dict[str, Any] | None:
    """Resolve only the restore journal belonging to this Chat's workspace."""
    if link.lifecycle_status != LensSessionLink.LifecycleStatus.PROVISIONING:
        return None
    state = link.provision_state_json or {}
    reused = bool(state.get("reuse_existing_resources"))
    result: dict[str, Any] = {
        "reused_data": reused, "restore": None,
        "assistant_state": assistant_preparation_state(link),
    }
    restore_phases = {
        LensSessionLink.ProvisionPhase.RESTORING,
        LensSessionLink.ProvisionPhase.CONVERTING,
        LensSessionLink.ProvisionPhase.CREATING_KNOWLEDGE_SOURCE,
        LensSessionLink.ProvisionPhase.CREATING_ASSISTANT,
        LensSessionLink.ProvisionPhase.GRANTING_ASSISTANT,
        LensSessionLink.ProvisionPhase.CREATING_SESSION,
    }
    if reused or link.provision_phase not in restore_phases:
        return result
    ks = link.knowledge_source
    if ks is None or ks.organization_id != link.organization_id:
        return result
    sync = ks.sync_state_json or {}
    record_id = nonnegative_number(sync.get("restore_record_id"))
    snapshot_id = nonnegative_number(sync.get("snapshot_id_used"))
    if not record_id or not snapshot_id:
        return result
    record = RestoreRecord.objects.filter(
        pk=int(record_id),
        organization_id=link.organization_id,
        purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
        workspace_binding_id__in=LensWorkspaceBinding.objects.filter(
            knowledge_source_id=ks.pk, organization_id=link.organization_id,
        ).values_list("pk", flat=True),
        source_snapshot_id=int(snapshot_id),
    ).first()
    if record is None:
        return result
    task = Task.objects.filter(
        pk=record.task_id,
        organization_id=link.organization_id,
        task_uuid=record.task_uuid,
    ).first()
    if task is None:
        return result
    if link.provision_phase != LensSessionLink.ProvisionPhase.RESTORING and task.status != Task.Status.SUCCESS:
        return result
    result["restore"] = restore_progress_view(
        build_restore_kopia_progress(record=record, task=task),
        status=str(task.status),
    )
    if result["restore"]["eta_seconds"] is not None and not _restore_metrics_are_fresh(record):
        result["restore"]["eta_seconds"] = None
    return result


def assistant_preparation_state(link: LensSessionLink) -> str | None:
    """Describe final-stage work without exposing raw errors or retry metadata."""
    phase = link.provision_phase
    ks = link.knowledge_source
    sync = (
        ks.sync_state_json or {}
        if ks is not None and ks.organization_id == link.organization_id else {}
    )
    if phase == LensSessionLink.ProvisionPhase.CONVERTING:
        if str((sync.get("conversion") or {}).get("status") or "").upper() != "SUCCESS":
            return None
    elif phase not in {
        LensSessionLink.ProvisionPhase.CREATING_KNOWLEDGE_SOURCE,
        LensSessionLink.ProvisionPhase.CREATING_ASSISTANT,
        LensSessionLink.ProvisionPhase.GRANTING_ASSISTANT,
        LensSessionLink.ProvisionPhase.CREATING_SESSION,
    }:
        return None
    if (link.provision_state_json or {}).get("source_lens_transient") or sync.get("source_lens_transient"):
        return "retrying"
    if not getattr(link, "provision_claim_token", None):
        return "waiting"
    if phase in {
        LensSessionLink.ProvisionPhase.GRANTING_ASSISTANT,
        LensSessionLink.ProvisionPhase.CREATING_SESSION,
    }:
        return "opening_session"
    if phase == LensSessionLink.ProvisionPhase.CONVERTING:
        return "waiting"
    return "configuring"
