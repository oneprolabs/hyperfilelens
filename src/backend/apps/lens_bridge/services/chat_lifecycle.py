"""Chat 1:1 lifecycle — each New Chat owns restore+KS+Ass; delete tears them down (not DG)."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid as uuid_lib
from datetime import timedelta
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.exceptions import APIException, ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensAssistantLink,
    LensChatBinding,
    LensGatewayChatSlot,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services import (
    assistant_access,
    chat_user_provisioning,
    gateway_chat_queue,
    ingest_policy,
    knowledge_source_sync,
    platform_lens,
    provisioning,
    sl_client,
    teardown_blocking,
)
from apps.lens_bridge.services.chat_binding import _grant_assistant_to_chat_user
from apps.lens_bridge.services.chat_lifecycle_errors import (
    lifecycle_error_state_from_exception,
)
from apps.lens_bridge.services.teardown_claims import (
    PROVISION_CLAIM_TTL_SECONDS,
    TEARDOWN_CLAIM_TTL_SECONDS,
    next_retry_at,
)
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.storage.services.internal.repository_workload import (
    RepositoryWorkload,
    lock_repositories_for_workload,
)
from apps.protection.services.source_identity import resolve_source_display_name

logger = logging.getLogger(__name__)

_ASSISTANT_CREATE_OPERATION = "assistant_create"
_SESSION_CREATE_OPERATION = "session_create"
_TEARDOWN_INTENT_DELETE = "delete_session"
_TEARDOWN_INTENT_RESET_FOR_RETRY = "reset_for_retry"
_PROVISION_TRANSIENT_RETRY_BASE_SECONDS = 15
_PROVISION_TRANSIENT_RETRY_MAX_SECONDS = 300
_SCOPE_TASK_STATE_KEY = "scope_resolution"
_MAX_BIGINT = 2**63 - 1
_MIN_BIGINT = -(2**63)


class ChatProvisionLeaseLostError(RuntimeError):
    """Raised when provisioning no longer owns the Chat lifecycle lease."""


class ChatTeardownIncompleteError(RuntimeError):
    """Raised after durable teardown state is saved so Celery retries it."""


class ChatCreateIdempotencyConflict(APIException):
    """Raised when an idempotency key is reused for another Chat request."""

    status_code = http_status.HTTP_409_CONFLICT
    default_detail = "The chat request key was already used with different data."
    default_code = "chat_create_idempotency_conflict"


def _chat_create_request_identity(
    *,
    backup_config_id: int,
    backup_source_snapshot_id: int,
    source_scopes: list[dict[str, Any]],
    gateway_mode: str,
    gateway_link_id: int | None,
    title: str | None,
    analysis_type: str | None = None,
    analysis_mode: str | None = None,
    agent_model_ref: str | uuid_lib.UUID | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return a stable request hash and normalized user-selected scopes."""

    from apps.subscription.services.quota import normalize_scope_path

    request_scopes: list[dict[str, Any]] = []
    for index, scope in enumerate(source_scopes):
        raw_path = str(scope.get("source_path") or "").strip()
        try:
            directory_id = int(scope.get("backup_snapshot_directory_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                {"source_scopes": {index: "Select a valid file or directory."}}
            ) from exc
        if not raw_path or directory_id <= 0:
            raise ValidationError(
                {"source_scopes": {index: "Select a valid file or directory."}}
            )
        selected_path = normalize_scope_path(raw_path)
        request_scopes.append(
            {
                "source_path": selected_path,
                "backup_snapshot_directory_id": directory_id,
            }
        )
    deduplicated_scopes: list[dict[str, Any]] = []
    for candidate in request_scopes:
        directory_id = candidate["backup_snapshot_directory_id"]
        candidate_path = candidate["source_path"]
        covered = False
        retained: list[dict[str, Any]] = []
        for existing in deduplicated_scopes:
            if existing["backup_snapshot_directory_id"] != directory_id:
                retained.append(existing)
                continue
            existing_path = existing["source_path"]
            if candidate_path == existing_path or candidate_path.startswith(
                f"{existing_path.rstrip('/')}/"
            ):
                covered = True
                retained.append(existing)
                continue
            if existing_path.startswith(f"{candidate_path.rstrip('/')}/"):
                continue
            retained.append(existing)
        if not covered:
            retained.append(candidate)
        deduplicated_scopes = retained
    request_scopes = deduplicated_scopes
    if not request_scopes:
        raise ValidationError({"source_scopes": "Select at least one file or folder."})

    canonical_request = {
        "backup_config_id": int(backup_config_id),
        "backup_source_snapshot_id": int(backup_source_snapshot_id),
        "source_scopes": request_scopes,
        "gateway_mode": str(gateway_mode),
        "gateway_link_id": (
            int(gateway_link_id) if gateway_link_id is not None else None
        ),
        "title": str(title or "").strip(),
    }
    # Keep omitted optional fields out of the identity so idempotent retries
    # from older clients remain compatible with records created before the
    # advanced execution options were introduced.
    if analysis_mode is not None:
        canonical_request["analysis_mode"] = str(analysis_mode)
    if analysis_type is not None:
        canonical_request["analysis_type"] = str(analysis_type)
    if agent_model_ref is not None:
        canonical_request["agent_model_ref"] = str(agent_model_ref)
    request_hash = hashlib.sha256(
        json.dumps(
            canonical_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return request_hash, request_scopes


def _scope_has_trusted_summary(scope: Any) -> bool:
    """Return whether a scope has a complete nonnegative Agent/root summary."""

    if not isinstance(scope, dict):
        return False
    path_type = str(scope.get("path_type") or "").lower()
    if path_type not in {"file", "dir"}:
        return False
    try:
        file_count = _exact_summary_int(scope["file_count"])
        size_bytes = _exact_summary_int(scope["size_bytes"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if file_count < 0 or size_bytes < 0:
        return False
    return path_type != "file" or file_count == 1


def _exact_summary_int(value: Any) -> int:
    """Parse one JSON integer without truncating fractional numeric values."""

    if isinstance(value, bool):
        raise ValueError("boolean is not an integer summary")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("summary must be an exact integer")
        parsed = int(value)
    elif isinstance(value, str):
        parsed = int(value.strip())
    else:
        raise TypeError("summary must be an integer")
    if parsed < _MIN_BIGINT or parsed > _MAX_BIGINT:
        raise OverflowError("summary is outside the database integer range")
    return parsed


def _source_path_basename(path: str) -> str:
    normalized = path.strip().replace("\\", "/").rstrip("/")
    if not normalized or normalized.endswith(":"):
        return ""
    return normalized.rsplit("/", 1)[-1].strip()


def _unique_session_title(
    org: Organization,
    *,
    user: AbstractBaseUser,
    base_title: str,
) -> str:
    base = base_title.strip()[:160] or "New Chat"
    existing = {
        title.casefold()
        for title in LensSessionLink.objects.filter(
            organization=org,
            hfl_user=user,
            status=LensSessionLink.Status.ACTIVE,
        ).values_list("title", flat=True)
        if title
    }
    if base.casefold() not in existing:
        return base
    suffix_number = 2
    while True:
        suffix = f" ({suffix_number})"
        candidate = f"{base[: 160 - len(suffix)]}{suffix}"
        if candidate.casefold() not in existing:
            return candidate
        suffix_number += 1


def _default_session_title(
    org: Organization,
    *,
    user: AbstractBaseUser,
    source_name: str | None,
    source_scopes: list[dict[str, Any]],
) -> str:
    source_label = (source_name or "").strip() or "New Chat"
    first_item = _source_path_basename(str(source_scopes[0].get("source_path") or ""))
    base_title = first_item or source_label
    if len(source_scopes) > 1:
        base_title = f"{base_title} +{len(source_scopes) - 1}"
    return _unique_session_title(org, user=user, base_title=base_title)


def _configured_gateway_link_for_chat(
    org: Organization,
    *,
    user: AbstractBaseUser,
    gateway_mode: str,
    gateway_link_id: int | None,
):
    """Select an authorized gateway using the same readiness-aware policy as Copilot."""

    from apps.lens_bridge.models import LensGatewayLink

    if gateway_mode == LensSessionLink.GatewaySelectionMode.AUTO:
        return platform_lens.resolve_auto_gateway_link_for_copilot(user=user)
    return (
        LensGatewayLink.objects.filter(
            pk=gateway_link_id,
            organization=org,
            owner_user=user,
            scope=LensGatewayLink.GatewayScope.USER,
            sl_lensnode_uuid__isnull=False,
            is_deleted=False,
        )
        .select_related("organization", "gateway")
        .first()
    )


def start_copilot_chat(
    org: Organization,
    *,
    user: AbstractBaseUser,
    binding: LensChatBinding,
    title: str | None = None,
) -> LensSessionLink:
    """Legacy adapter for old clients that still submit a prepared binding."""
    if not binding.gateway_link_id:
        raise ValidationError({"gateway_link_id": "Data gateway is required."})
    scopes = [
        {
            "source_path": binding.source_path,
            "backup_snapshot_directory_id": binding.backup_snapshot_directory_id,
            "path_type": "unknown",
        }
    ]
    link = create_copilot_chat(
        org,
        user=user,
        backup_config_id=binding.backup_config_id,
        backup_source_snapshot_id=binding.backup_source_snapshot_id,
        source_scopes=scopes,
        gateway_mode=LensSessionLink.GatewaySelectionMode.MANUAL,
        gateway_link_id=binding.gateway_link_id,
        title=title,
        idempotency_key=str(uuid_lib.uuid4()),
    )
    link.chat_binding = binding
    link.save(update_fields=["chat_binding", "updated_at"])
    return link


@transaction.atomic
def create_copilot_chat(
    org: Organization,
    *,
    user: AbstractBaseUser,
    backup_config_id: int,
    backup_source_snapshot_id: int,
    source_scopes: list[dict[str, Any]],
    gateway_mode: str,
    gateway_link_id: int | None,
    idempotency_key: str,
    title: str | None = None,
    analysis_type: str | None = None,
    analysis_mode: str | None = None,
    agent_model_ref: str | uuid_lib.UUID | None = None,
) -> LensSessionLink:
    """Persist an idempotent local Chat shell without remote service calls."""
    request_key = str(idempotency_key or "").strip()
    if not request_key:
        raise ValidationError({"idempotency_key": "A chat request key is required."})
    request_hash, request_scopes = _chat_create_request_identity(
        backup_config_id=backup_config_id,
        backup_source_snapshot_id=backup_source_snapshot_id,
        source_scopes=source_scopes,
        gateway_mode=gateway_mode,
        gateway_link_id=gateway_link_id,
        title=title,
        analysis_type=analysis_type,
        analysis_mode=analysis_mode,
        agent_model_ref=agent_model_ref,
    )
    existing = LensSessionLink.objects.filter(
        organization=org,
        hfl_user=user,
        create_idempotency_key=request_key,
        is_deleted=False,
    ).first()
    if existing is not None:
        if existing.create_request_hash != request_hash:
            raise ChatCreateIdempotencyConflict()
        return existing

    config = BackupConfig.objects.filter(
        id=backup_config_id, organization_id=org.id
    ).first()
    if config is None:
        raise ValidationError({"backup_config_id": "Backup source not found."})
    try:
        lock_repositories_for_workload(
            organization_id=org.id,
            repository_ids=[config.repository_id],
            workload=RepositoryWorkload.RESTORE_READ,
        )
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict) from exc
    snapshot = BackupSourceSnapshot.objects.filter(
        id=backup_source_snapshot_id,
        organization_id=org.id,
        backup_config_id=config.id,
    ).first()
    if snapshot is None:
        raise ValidationError(
            {"backup_source_snapshot_id": "Snapshot not found for this backup source."}
        )
    if snapshot.status not in {
        BackupSourceSnapshot.Status.AVAILABLE,
        BackupSourceSnapshot.Status.PARTIAL,
    }:
        raise ValidationError(
            {"backup_source_snapshot_id": "Snapshot is no longer available."}
        )

    normalized_scopes: list[dict[str, Any]] = []
    from apps.subscription.services.quota import (
        normalize_scope_path,
        relative_scope_path,
    )

    for index, scope in enumerate(request_scopes):
        path = str(scope["source_path"])
        directory_id = int(scope["backup_snapshot_directory_id"])
        directory = BackupSourceSnapshotDirectory.objects.filter(
            id=directory_id,
            source_snapshot_id=snapshot.id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        ).first()
        if not path or directory is None:
            raise ValidationError(
                {
                    "source_scopes": {
                        index: "Select a valid file or directory from this snapshot."
                    }
                }
            )
        root_path = str(directory.source_path)
        relative_path = relative_scope_path(root=root_path, selected=path)
        if path != normalize_scope_path(root_path) and not relative_path:
            raise ValidationError(
                {
                    "source_scopes": {
                        index: "Selected path is outside the snapshot directory."
                    }
                }
            )
        is_root = path == normalize_scope_path(root_path)
        trusted_type = (
            "file"
            if is_root and str(directory.path_type or "").lower() == "file"
            else "dir"
            if is_root
            else "unknown"
        )
        normalized = {
            "source_path": path,
            "backup_snapshot_directory_id": directory.id,
            "path_type": trusted_type,
        }
        if is_root:
            normalized["file_count"] = (
                1 if trusted_type == "file" else max(0, int(directory.file_count or 0))
            )
            normalized["size_bytes"] = max(0, int(directory.size_bytes or 0))
        normalized_scopes.append(normalized)
    all_scopes_resolved = all(
        _scope_has_trusted_summary(scope) for scope in normalized_scopes
    )
    gateway_link = _configured_gateway_link_for_chat(
        org,
        user=user,
        gateway_mode=gateway_mode,
        gateway_link_id=gateway_link_id,
    )
    if gateway_link is None:
        raise ValidationError(
            {"gateway_link_id": (platform_lens.NO_PUBLIC_DATA_GATEWAY_AVAILABLE)}
        )

    from apps.lens_bridge.services.gateway_execution import context_for_gateway_link

    context_for_gateway_link(
        tenant_organization=org,
        gateway_link=gateway_link,
        expected_owner_user_id=(
            user.id if gateway_link.scope == gateway_link.GatewayScope.USER else None
        ),
        require_ready=False,
    )
    default_model_ref, multimodal_model_ref = (
        provisioning.configured_default_model_refs_for_org(org)
    )
    model_ref = str(agent_model_ref or default_model_ref or "")
    if not model_ref:
        raise ValidationError(
            {"model": "Configure an active AI model before creating a chat."}
        )
    if agent_model_ref is not None:
        from apps.lens_bridge.services import org_models

        org_models.validate_agent_model_ref(
            org,
            uuid_lib.UUID(str(agent_model_ref)),
        )
    normalized_analysis_type = provisioning.validate_analysis_type_for_gateway(
        gateway_link,
        analysis_type,
    )
    normalized_analysis_mode = str(
        analysis_mode or LensSessionLink.AnalysisMode.STANDARD
    )
    if normalized_analysis_mode not in LensSessionLink.AnalysisMode.values:
        raise ValidationError({"analysis_mode": "Select a supported analysis mode."})

    source_display_name = resolve_source_display_name(
        organization_id=org.id,
        source_type=config.source_type,
        source_ref_id=config.source_ref_id,
        fallback=config.name,
    )
    default_title = _default_session_title(
        org,
        user=user,
        source_name=source_display_name,
        source_scopes=normalized_scopes,
    )

    def _create_session_link() -> LensSessionLink:
        return LensSessionLink.objects.create(
            organization=org,
            hfl_user=user,
            create_idempotency_key=request_key,
            create_request_hash=request_hash,
            title=(title or "").strip() or default_title,
            backup_config_id=config.id,
            backup_source_snapshot_id=snapshot.id,
            source_scopes_json=normalized_scopes,
            gateway_link=gateway_link,
            gateway_selection_mode=gateway_mode,
            agent_model_ref=uuid_lib.UUID(model_ref),
            multimodal_model_ref=multimodal_model_ref,
            analysis_type=normalized_analysis_type,
            analysis_mode=normalized_analysis_mode,
            scope_resolution_status=(
                LensSessionLink.ScopeResolutionStatus.RESOLVED
                if all_scopes_resolved
                else LensSessionLink.ScopeResolutionStatus.PENDING
            ),
            capacity_reservation_status=(
                LensSessionLink.CapacityReservationStatus.PENDING
            ),
            capacity_reserved_bytes=0,
            status=LensSessionLink.Status.ACTIVE,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_phase=LensSessionLink.ProvisionPhase.QUEUED,
            provision_detail="Chat creation is queued.",
            lifecycle_error="",
            lifecycle_error_state_json={},
        )

    try:
        with transaction.atomic():
            gateway_link = (
                LensGatewayLink.objects.select_for_update()
                .select_related("gateway", "organization")
                .get(pk=gateway_link.pk)
            )
            existing = LensSessionLink.objects.filter(
                organization=org,
                hfl_user=user,
                create_idempotency_key=request_key,
                is_deleted=False,
            ).first()
            if existing is not None:
                if existing.create_request_hash != request_hash:
                    raise ChatCreateIdempotencyConflict()
                return existing
            gateway_link = gateway_chat_queue.assert_chat_queue_admission(
                gateway_link=gateway_link,
            )
            link = _create_session_link()
            link.gateway_queue_entered_at = timezone.now()
            link.save(
                update_fields=["gateway_queue_entered_at", "updated_at"]
            )
    except IntegrityError:
        link = LensSessionLink.objects.filter(
            organization=org,
            hfl_user=user,
            create_idempotency_key=request_key,
            is_deleted=False,
        ).first()
        if link is None:
            raise
        if link.create_request_hash != request_hash:
            raise ChatCreateIdempotencyConflict()
        return link

    transaction.on_commit(lambda: _queue_provision_or_mark_failed(link.id))
    return link


@transaction.atomic
def request_copilot_chat_teardown(link: LensSessionLink) -> LensSessionLink:
    """Mark chat deleting and enqueue teardown. Never touches DG."""
    locked = LensSessionLink.objects.select_for_update().get(pk=link.pk)
    if locked.lifecycle_status == LensSessionLink.LifecycleStatus.DELETED:
        return locked

    legacy_teardown_intent = str(
        (locked.teardown_state_json or {}).get("intent") or ""
    )
    delete_intent_already_active = (
        locked.lifecycle_status == LensSessionLink.LifecycleStatus.DELETING
        and (
            (
                locked.cleanup_intent
                == LensSessionLink.CleanupIntent.DELETE_SESSION
                and locked.cleanup_status
                in {
                    LensSessionLink.CleanupStatus.PENDING,
                    LensSessionLink.CleanupStatus.RUNNING,
                    LensSessionLink.CleanupStatus.BLOCKED,
                }
            )
            or legacy_teardown_intent == _TEARDOWN_INTENT_DELETE
        )
    )
    if not delete_intent_already_active:
        locked.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        locked.provision_phase = LensSessionLink.ProvisionPhase.DELETING
        locked.provision_detail = "Deleting chat resources."
        locked.lifecycle_error = ""
        locked.lifecycle_error_state_json = {}
        locked.status = LensSessionLink.Status.ARCHIVED
        locked.provision_claim_token = None
        locked.provision_claimed_at = None
        locked.provision_next_retry_at = None
        locked.provision_generation += 1
        locked.provision_poll_sequence = 0
        locked.cleanup_intent = LensSessionLink.CleanupIntent.DELETE_SESSION
        locked.cleanup_status = LensSessionLink.CleanupStatus.PENDING
        locked.teardown_attempts = 0
        locked.teardown_claim_token = None
        locked.teardown_claimed_at = None
        locked.teardown_next_retry_at = None
        locked.teardown_state_json = {"intent": _TEARDOWN_INTENT_DELETE}
        locked.save(
            update_fields=[
                "lifecycle_status",
                "provision_phase",
                "provision_detail",
                "lifecycle_error",
                "lifecycle_error_state_json",
                "status",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "provision_generation",
                "provision_poll_sequence",
                "cleanup_intent",
                "cleanup_status",
                "teardown_attempts",
                "teardown_claim_token",
                "teardown_claimed_at",
                "teardown_next_retry_at",
                "teardown_state_json",
                "updated_at",
            ]
        )
    transaction.on_commit(lambda: _queue_teardown_or_record_error(locked.id))
    if locked.gateway_link_id is not None:
        transaction.on_commit(
            lambda: gateway_chat_queue.wake_gateway_queue(locked.gateway_link_id)
        )
    return locked


def _claim_copilot_chat_provision(
    session_link_id: int,
    *,
    expected_generation: int | None = None,
    expected_poll_sequence: int | None = None,
) -> tuple[str | None, str]:
    """Claim one queued or stale Chat provisioning execution."""
    now = timezone.now()
    with transaction.atomic():
        link = (
            LensSessionLink.objects.select_for_update()
            .filter(pk=session_link_id)
            .first()
        )
        if link is None:
            return None, "missing"
        if link.lifecycle_status != LensSessionLink.LifecycleStatus.PROVISIONING:
            return None, str(link.lifecycle_status)
        if expected_generation is None and expected_poll_sequence is None:
            # Messages published before poll fencing was deployed carry no
            # tokens. They may claim only the initial migrated generation;
            # accepting them after Retry/Delete has advanced the generation
            # would let an old message take ownership of a new lifecycle run.
            if link.provision_generation != 1 or link.provision_poll_sequence != 0:
                return None, "stale"
        elif expected_generation is None or expected_poll_sequence is None:
            return None, "stale"
        elif (
            link.provision_generation != int(expected_generation)
            or link.provision_poll_sequence != int(expected_poll_sequence)
        ):
            return None, "stale"
        if link.provision_claimed_at and link.provision_claimed_at > now - timedelta(
            seconds=PROVISION_CLAIM_TTL_SECONDS
        ):
            return None, "busy"
        if link.provision_next_retry_at and link.provision_next_retry_at > now:
            return None, "scheduled"

        claim_token = uuid_lib.uuid4()
        if link.provision_poll_sequence == 0:
            link.provision_attempts += 1
        link.provision_claim_token = claim_token
        link.provision_claimed_at = now
        link.provision_next_retry_at = next_retry_at(link.provision_attempts)
        link.lifecycle_error = ""
        link.lifecycle_error_state_json = {}
        link.save(
            update_fields=[
                "provision_attempts",
                "provision_claim_token",
                "provision_claimed_at",
                "provision_next_retry_at",
                "lifecycle_error",
                "lifecycle_error_state_json",
                "updated_at",
            ]
        )
    return str(claim_token), "claimed"


def run_copilot_chat_provision(
    *,
    session_link_id: int,
    expected_generation: int | None = None,
    expected_poll_sequence: int | None = None,
) -> dict[str, Any]:
    """Provision one chat and persist failures from every execution stage."""
    claim_token, claim_status = _claim_copilot_chat_provision(
        session_link_id,
        expected_generation=expected_generation,
        expected_poll_sequence=expected_poll_sequence,
    )
    if claim_token is None:
        return {"session_link_id": session_link_id, "status": claim_status}
    try:
        result = _run_copilot_chat_provision(
            session_link_id=session_link_id,
            claim_token=claim_token,
        )
        if result.get("status") == "waiting":
            sync_result = result.get("sync") if isinstance(result.get("sync"), dict) else {}
            retry_after_seconds = int(
                result.get("retry_after_seconds")
                or sync_result.get("retry_after_seconds")
                or 5
            )
            result["next_poll"] = _defer_provision_poll(
                session_link_id,
                claim_token,
                retry_after_seconds=retry_after_seconds,
            )
        return result
    except ChatProvisionLeaseLostError:
        current_status = (
            LensSessionLink.objects.filter(pk=session_link_id)
            .values_list("lifecycle_status", flat=True)
            .first()
        )
        logger.info(
            "copilot chat provision fenced session_link_id=%s status=%s",
            session_link_id,
            current_status,
        )
        return {
            "session_link_id": session_link_id,
            "status": current_status or "missing",
        }
    except sl_client.LensBridgeUnavailable as exc:
        next_poll = _defer_provision_for_transient_error(
            session_link_id,
            claim_token,
            detail=str(exc.detail),
        )
        return {
            "session_link_id": session_link_id,
            "status": "waiting",
            "detail": str(exc.detail),
            "next_poll": next_poll,
        }
    except Exception as exc:
        logger.exception(
            "copilot chat provision failed session_link_id=%s",
            session_link_id,
        )
        error_state = lifecycle_error_state_from_exception(exc)
        link = LensSessionLink.objects.filter(pk=session_link_id).first()
        cleanup_errors: list[str] = []
        if link is not None:
            try:
                cleanup_errors = _cleanup_failed_provision(link, claim_token)
            except ChatProvisionLeaseLostError:
                logger.info(
                    "copilot cleanup fenced session_link_id=%s",
                    session_link_id,
                )
            except Exception as cleanup_exc:
                logger.exception(
                    "copilot cleanup failed session_link_id=%s",
                    session_link_id,
                )
                cleanup_errors = [f"cleanup_failed_provision: {cleanup_exc}"]
        if cleanup_errors:
            _transition_failed_provision_to_teardown(
                session_link_id,
                claim_token,
                message=f"{exc}; {'; '.join(cleanup_errors)}",
                error_state=error_state,
            )
        else:
            _mark_provision_failed_by_id(
                session_link_id,
                claim_token,
                str(exc),
                error_state=error_state,
                expected_generation=link.provision_generation if link else 0,
            )
        raise


def _run_copilot_chat_provision(
    *,
    session_link_id: int,
    claim_token: str,
) -> dict[str, Any]:
    link = (
        LensSessionLink.objects.select_related(
            "chat_binding",
            "chat_binding__gateway_link",
            "chat_binding__gateway_link__gateway",
            "gateway_link",
            "gateway_link__gateway",
            "hfl_user",
            "organization",
        )
        .filter(pk=session_link_id)
        .first()
    )
    if link is None:
        raise ValidationError({"session": "Session not found."})
    _require_provision_claim(link.id, claim_token)

    binding = link.chat_binding
    gateway_link = link.gateway_link or (binding.gateway_link if binding else None)
    if gateway_link is None:
        raise ValidationError({"gateway_link": "Data gateway is missing."})
    if link.gateway_link_id is None:
        # Sessions created before the direct Gateway binding was introduced
        # may still resolve it through their durable Chat binding. Persist the
        # canonical link before queue admission so they cannot wait forever
        # outside the per-Gateway scheduler.
        link.gateway_link = gateway_link
        _update_provision_claim(link, claim_token, "gateway_link")
    scopes = list(link.source_scopes_json or [])
    if not scopes and binding is not None:
        scopes = [
            {
                "source_path": binding.source_path,
                "backup_snapshot_directory_id": binding.backup_snapshot_directory_id,
                "path_type": "unknown",
            }
        ]
    if not scopes:
        raise ValidationError({"source_scopes": "Backup content selection is missing."})
    snapshot_id = link.backup_source_snapshot_id or (
        binding.backup_source_snapshot_id if binding else None
    )
    if not snapshot_id:
        raise ValidationError(
            {"backup_source_snapshot_id": "Backup snapshot is missing."}
        )
    org = link.organization
    user = link.hfl_user

    from apps.lens_bridge.services.gateway_execution import context_for_gateway_link

    context_for_gateway_link(
        tenant_organization=org,
        gateway_link=gateway_link,
        expected_owner_user_id=(
            user.id if gateway_link.scope == gateway_link.GatewayScope.USER else None
        ),
        require_ready=True,
    )

    scope_result = _resolve_chat_scopes(
        link=link,
        claim_token=claim_token,
        scopes=scopes,
    )
    if scope_result is not None:
        return scope_result
    link.refresh_from_db()
    _reserve_chat_capacity(link=link, claim_token=claim_token)
    link.refresh_from_db()
    scopes = list(link.source_scopes_json or [])

    slot = gateway_chat_queue.try_acquire_chat_prepare_slot(
        session_link_id=link.id,
        expected_generation=link.provision_generation,
    )
    if not slot.acquired:
        ahead = gateway_chat_queue.chat_queue_ahead(session=link)
        detail = "Waiting for Data Gateway."
        if ahead:
            detail = f"Waiting for Data Gateway. {ahead} Chat(s) ahead."
        _set_phase(
            link,
            claim_token,
            LensSessionLink.ProvisionPhase.QUEUED,
            detail,
        )
        return {
            "session_link_id": link.id,
            "status": "waiting",
            "queue_position": slot.position,
            "retry_after_seconds": slot.retry_after_seconds,
        }
    gateway_chat_queue.heartbeat_chat_prepare_slot(
        session_link_id=link.id,
        generation=link.provision_generation,
    )

    _set_phase(
        link,
        claim_token,
        LensSessionLink.ProvisionPhase.RESTORING,
        "Restoring selected backup data.",
    )
    sl_user_link = chat_user_provisioning.ensure_sl_chat_user(user)

    # 1) Always create a fresh KS for this chat (no reuse).
    ks = link.knowledge_source
    if ks is None:
        first_path = str(scopes[0].get("source_path") or "Copilot")
        ks_name = (
            f"{first_path.rstrip('/').split('/')[-1] or 'Copilot'} · Chat {link.id}"
        )
        first_directory_id = scopes[0].get("backup_snapshot_directory_id")
        ks = LensKnowledgeSource.objects.create(
            organization=org,
            name=ks_name[:160],
            gateway=gateway_link.gateway,
            gateway_link=gateway_link,
            backup_source_snapshot_id=snapshot_id,
            backup_snapshot_directory_id=first_directory_id,
            source_path=first_path,
            source_scopes_json=scopes,
            linked_version_mode=LensKnowledgeSource.LinkedVersionMode.PINNED,
            pinned_snapshot_id=snapshot_id,
            sl_lensnode_uuid=gateway_link.sl_lensnode_uuid,
            ingest_policy_json=ingest_policy.normalize_ingest_policy(
                {
                    "document": True,
                    "image": bool(link.multimodal_model_ref),
                    "embedded_image": bool(link.multimodal_model_ref),
                    "pdf_render_scanned_pages": bool(link.multimodal_model_ref),
                    "vision_model_ref": (
                        str(link.multimodal_model_ref)
                        if link.multimodal_model_ref
                        else None
                    ),
                },
            ),
            created_by=user,
        )
        ks = knowledge_source_sync.prepare_new_knowledge_source(org=org, ks=ks)
        link.knowledge_source = ks
        try:
            _update_provision_claim(link, claim_token, "knowledge_source")
        except ChatProvisionLeaseLostError:
            _cleanup_orphan_knowledge_source(ks, owner_session_link_id=link.id)
            raise

    # 2) Advance the durable restore + conversion state machine. External
    # restore work returns ``waiting`` so this worker never blocks on polling.
    def sync_progress(phase: str, detail: str) -> None:
        provision_phase = (
            LensSessionLink.ProvisionPhase.CONVERTING
            if phase in {"ensure_managed_datasource", "convert_documents"}
            else LensSessionLink.ProvisionPhase.RESTORING
        )
        if phase in {"push_assistant", "finalize"}:
            provision_phase = LensSessionLink.ProvisionPhase.CREATING_KNOWLEDGE_SOURCE
        _set_phase(link, claim_token, provision_phase, detail)

    sync_result = knowledge_source_sync.run_knowledge_source_sync(
        organization_id=org.id,
        knowledge_source_id=ks.id,
        progress_callback=sync_progress,
    )
    if sync_result.get("status") in {"waiting", "busy", "scheduled"}:
        retry_after_seconds = int(sync_result.get("retry_after_seconds") or 0)
        if retry_after_seconds <= 0:
            next_poll_at = (
                LensKnowledgeSource.objects.filter(pk=ks.id)
                .values_list("sync_next_poll_at", flat=True)
                .first()
            )
            if next_poll_at is not None:
                retry_after_seconds = max(
                    1,
                    int((next_poll_at - timezone.now()).total_seconds()),
                )
        if retry_after_seconds <= 0:
            retry_after_seconds = 10 if sync_result.get("status") == "busy" else 5
        return {
            "session_link_id": link.id,
            "status": "waiting",
            "knowledge_source_id": ks.id,
            "sync": sync_result,
            "retry_after_seconds": retry_after_seconds,
        }
    ks.refresh_from_db()
    if ks.status not in (
        LensKnowledgeSource.Status.READY,
        LensKnowledgeSource.Status.DEGRADED,
    ):
        raise ValidationError(
            {
                "knowledge_source": f"Knowledge source sync did not complete ({ks.status})."
            }
        )

    # 3) Create Assistant (SL Admin) and grant to Chat User.
    _set_phase(
        link,
        claim_token,
        LensSessionLink.ProvisionPhase.CREATING_KNOWLEDGE_SOURCE,
        "Finalizing the private knowledge source.",
    )
    _set_phase(
        link,
        claim_token,
        LensSessionLink.ProvisionPhase.CREATING_ASSISTANT,
        "Creating the private assistant.",
    )
    assistant_uuid = link.sl_assistant_uuid or ks.sl_assistant_uuid
    if assistant_uuid is None:
        assistant_slug = provisioning.assistant_slug_for_ks(org=org, ks=ks)
        operation = _prepare_remote_operation(
            link,
            claim_token,
            kind=_ASSISTANT_CREATE_OPERATION,
            lookup_key=assistant_slug,
        )
        stored_remote_uuid = str(operation.get("remote_uuid") or "").strip()
        assistant_uuid = (
            uuid_lib.UUID(stored_remote_uuid)
            if stored_remote_uuid
            else _find_remote_uuid(
                path="/api/lens/assistants/",
                field="slug",
                value=assistant_slug,
            )
        )
        if assistant_uuid is None:
            assistant_uuid = provisioning.create_sl_assistant_for_ks(
                org=org,
                ks=ks,
                gateway_link=gateway_link,
                model_ref=link.agent_model_ref,
                multimodal_model_ref=link.multimodal_model_ref,
                analysis_type=link.analysis_type,
                analysis_mode=link.analysis_mode,
                slug=assistant_slug,
            )
    try:
        _bind_assistant_to_provision_claim(
            link,
            claim_token,
            knowledge_source=ks,
            assistant_uuid=assistant_uuid,
        )
    except ChatProvisionLeaseLostError:
        _compensate_late_assistant(link.id, assistant_uuid)
        raise
    _set_phase(
        link,
        claim_token,
        LensSessionLink.ProvisionPhase.GRANTING_ASSISTANT,
        "Granting assistant access.",
    )
    _grant_assistant_to_chat_user(
        assistant_uuid=assistant_uuid,
        sl_user_id=sl_user_link.sl_user_id,
    )

    # 4) Create SL session as Chat User.
    _set_phase(
        link,
        claim_token,
        LensSessionLink.ProvisionPhase.CREATING_SESSION,
        "Opening the chat session.",
    )
    session_uuid = link.sl_session_uuid
    if session_uuid is None:
        operation = _prepare_remote_operation(
            link,
            claim_token,
            kind=_SESSION_CREATE_OPERATION,
        )
        session_marker = str(operation["lookup_key"])
        stored_remote_uuid = str(operation.get("remote_uuid") or "").strip()
        session_uuid = (
            uuid_lib.UUID(stored_remote_uuid)
            if stored_remote_uuid
            else _find_remote_uuid(
                path="/api/lens/sessions/",
                field="title",
                value=session_marker,
                hfl_user=user,
            )
        )
        if session_uuid is None:
            sl_session = sl_client.request_json(
                "POST",
                "/api/lens/sessions/",
                json_body={
                    "assistant_uuid": str(assistant_uuid),
                    "title": session_marker,
                },
                hfl_user=user,
            )
            session_uuid = uuid_lib.UUID(str(sl_session["uuid"]))
        try:
            _record_remote_operation_resource(
                link,
                claim_token,
                kind=_SESSION_CREATE_OPERATION,
                field="sl_session_uuid",
                remote_uuid=session_uuid,
            )
        except ChatProvisionLeaseLostError:
            _compensate_late_session(link.id, session_uuid, user=user)
            raise

    sl_client.request_json(
        "PATCH",
        f"/api/lens/sessions/{session_uuid}/",
        json_body={"title": link.title},
        hfl_user=user,
    )
    _require_provision_claim(link.id, claim_token)

    _complete_copilot_chat_provision(
        link_id=link.id,
        claim_token=claim_token,
        knowledge_source_id=ks.id,
        assistant_uuid=assistant_uuid,
        session_uuid=session_uuid,
    )
    return {
        "session_link_id": link.id,
        "status": "ready",
        "knowledge_source_id": ks.id,
        "sync": sync_result,
    }


def _resolve_chat_scopes(
    *,
    link: LensSessionLink,
    claim_token: str,
    scopes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve one selected scope per pass and release the worker while pending."""

    if link.scope_resolution_status == LensSessionLink.ScopeResolutionStatus.RESOLVED:
        return None
    from apps.lens_bridge.services import snapshot_scope_tasks
    from apps.node.models import NodeTask
    from apps.subscription.services.quota import normalize_scope_path

    state = dict(link.provision_state_json or {})
    scope_state = dict(state.get(_SCOPE_TASK_STATE_KEY) or {})
    for index, scope in enumerate(scopes):
        if _scope_has_trusted_summary(scope):
            continue
        _set_phase(
            link,
            claim_token,
            LensSessionLink.ProvisionPhase.RESOLVING_SCOPE,
            "Validating selected backup data.",
        )
        task_id = str(scope_state.get("task_id") or "")
        task_index = scope_state.get("scope_index")
        correlation_id = str(scope_state.get("correlation_id") or "")
        task = None
        if task_id and task_index == index:
            task = snapshot_scope_tasks.scope_task_for_reference(
                organization=link.organization,
                task_id=task_id,
                correlation_id=correlation_id,
            )
        if task is None and correlation_id and task_index == index:
            task = snapshot_scope_tasks.scope_task_for_correlation(
                organization=link.organization,
                correlation_id=correlation_id,
            )
            if task is not None:
                scope_state["task_id"] = str(task.id)
                state[_SCOPE_TASK_STATE_KEY] = scope_state
                link.provision_state_json = state
                _update_provision_claim(
                    link,
                    claim_token,
                    "provision_state_json",
                )
        if task is not None:
            if task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
                return {
                    "session_link_id": link.id,
                    "status": "waiting",
                    "scope_task_id": str(task.id),
                }
            if task.status != NodeTask.Status.SUCCESS:
                logger.warning(
                    "copilot scope validation failed session_link_id=%s "
                    "node_task_id=%s task_status=%s error=%s",
                    link.id,
                    task.id,
                    task.status,
                    str(task.last_error or "")[:500],
                )
                raise ValidationError(
                    {
                        "source_scopes": snapshot_scope_tasks.snapshot_task_error(
                            task,
                            default=(
                                "Selected backup data could not be validated. "
                                "Try again or choose another file or folder."
                            ),
                        )
                    }
                )
            summary = snapshot_scope_tasks.resolved_scope_summary(task)
            updated_scopes = list(scopes)
            updated_scopes[index] = {
                **scope,
                **summary,
            }
            state.pop(_SCOPE_TASK_STATE_KEY, None)
            link.source_scopes_json = updated_scopes
            link.provision_state_json = state
            link.scope_resolution_status = (
                LensSessionLink.ScopeResolutionStatus.RESOLVED
                if all(_scope_has_trusted_summary(row) for row in updated_scopes)
                else LensSessionLink.ScopeResolutionStatus.PENDING
            )
            _update_provision_claim(
                link,
                claim_token,
                "source_scopes_json",
                "provision_state_json",
                "scope_resolution_status",
            )
            return _resolve_chat_scopes(
                link=link,
                claim_token=claim_token,
                scopes=updated_scopes,
            )

        directory = BackupSourceSnapshotDirectory.objects.filter(
            id=scope.get("backup_snapshot_directory_id"),
            source_snapshot_id=link.backup_source_snapshot_id,
            organization_id=link.organization_id,
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        ).first()
        if directory is None:
            raise ValidationError(
                {"source_scopes": "Snapshot directory is no longer available."}
            )
        selected = normalize_scope_path(str(scope.get("source_path") or ""))
        root = normalize_scope_path(directory.source_path)
        relative_path = (
            "" if selected == root else selected[len(root.rstrip("/") + "/") :]
        )
        if not correlation_id or task_index != index:
            correlation_id = f"chat:{link.id}:scope:{index}:{uuid_lib.uuid4().hex}"
            state[_SCOPE_TASK_STATE_KEY] = {
                "scope_index": index,
                "correlation_id": correlation_id,
            }
            link.provision_state_json = state
            _update_provision_claim(link, claim_token, "provision_state_json")
        task = snapshot_scope_tasks.dispatch_scope_resolution(
            organization_id=link.organization_id,
            directory_id=directory.id,
            backup_source_snapshot_id=link.backup_source_snapshot_id,
            gateway_link_id=link.gateway_link_id,
            requesting_user_id=link.hfl_user_id,
            path=relative_path,
            correlation_id=correlation_id,
        )
        state[_SCOPE_TASK_STATE_KEY] = {
            "scope_index": index,
            "correlation_id": correlation_id,
            "task_id": str(task.id),
        }
        link.provision_state_json = state
        _update_provision_claim(link, claim_token, "provision_state_json")
        return {
            "session_link_id": link.id,
            "status": "waiting",
            "scope_task_id": str(task.id),
        }

    link.scope_resolution_status = LensSessionLink.ScopeResolutionStatus.RESOLVED
    _update_provision_claim(link, claim_token, "scope_resolution_status")
    return None


@transaction.atomic
def _reserve_chat_capacity(
    *,
    link: LensSessionLink,
    claim_token: str,
) -> None:
    """Atomically validate trusted summaries and reserve Chat capacity once."""

    locked = (
        LensSessionLink.objects.select_for_update(of=("self",))
        .select_related("gateway_link", "organization")
        .filter(
            pk=link.id,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_claim_token=claim_token,
        )
        .first()
    )
    if locked is None:
        raise ChatProvisionLeaseLostError("Chat provisioning lease was lost.")
    if (
        locked.capacity_reservation_status
        == LensSessionLink.CapacityReservationStatus.RESERVED
    ):
        return
    if locked.scope_resolution_status != LensSessionLink.ScopeResolutionStatus.RESOLVED:
        raise RuntimeError("Chat capacity cannot be reserved before scope resolution.")
    scopes = list(locked.source_scopes_json or [])
    if not scopes or not all(_scope_has_trusted_summary(scope) for scope in scopes):
        raise RuntimeError("Chat capacity requires trusted scope summaries.")

    total_files = sum(max(0, int(scope.get("file_count") or 0)) for scope in scopes)
    total_bytes = sum(max(0, int(scope.get("size_bytes") or 0)) for scope in scopes)
    if total_files > _MAX_BIGINT or total_bytes > _MAX_BIGINT:
        raise RuntimeError("Chat scope totals exceed the supported integer range.")
    from apps.lens_bridge.models import LensGatewayLink
    from apps.subscription.services.quota import assert_gateway_select_within_limits

    assert_gateway_select_within_limits(
        organization=locked.organization,
        file_count=total_files,
        size_bytes=total_bytes,
        unknown_directory=False,
    )
    if locked.gateway_link.scope == LensGatewayLink.GatewayScope.PLATFORM:
        from common.errors import AppError
        from common.extension_spi import get_quota_provider
        from apps.lens_bridge.services.public_gateway_capacity import (
            assert_public_gateway_capacity,
            lock_public_gateway_capacity,
        )
        from apps.subscription.services.interface import enforce_license_quota

        # Keep the reservation lock order stable: Session -> Organization ->
        # Public Gateway. Future admission locks must preserve this ordering.
        Organization.objects.select_for_update().get(pk=locked.organization_id)
        gateway = lock_public_gateway_capacity(gateway_link=locked.gateway_link)
        assert_public_gateway_capacity(
            gateway_link=gateway,
            additional_bytes=total_bytes,
            unknown_size=False,
        )
        provider = get_quota_provider()
        if provider is not None:
            limits = provider.get_limits(locked.organization) or {}
            if "max_public_gateway_capacity_bytes" not in limits:
                raise AppError(
                    code="SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE",
                    status=503,
                    retryable=True,
                    title="Organization public gateway capacity is unavailable.",
                    diagnostic="max_public_gateway_capacity_bytes missing from quota limits",
                    meta={
                        "quota_type": "max_public_gateway_capacity_bytes",
                        "scope": "organization",
                    },
                )
            enforce_license_quota(
                locked.organization,
                "max_public_gateway_capacity_bytes",
                additional=total_bytes,
            )

    now = timezone.now()
    locked.provision_phase = LensSessionLink.ProvisionPhase.RESERVING_CAPACITY
    locked.provision_detail = "Reserving Data Gateway capacity."
    locked.capacity_reservation_status = (
        LensSessionLink.CapacityReservationStatus.RESERVED
    )
    locked.capacity_reserved_bytes = total_bytes
    locked.capacity_reserved_at = now
    locked.provision_claimed_at = now
    locked.save(
        update_fields=[
            "provision_phase",
            "provision_detail",
            "capacity_reservation_status",
            "capacity_reserved_bytes",
            "capacity_reserved_at",
            "provision_claimed_at",
            "updated_at",
        ]
    )
    link.capacity_reservation_status = (
        LensSessionLink.CapacityReservationStatus.RESERVED
    )
    link.capacity_reserved_bytes = total_bytes
    link.capacity_reserved_at = now


def _require_provision_claim(link_id: int, claim_token: str) -> None:
    if not LensSessionLink.objects.filter(
        pk=link_id,
        lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        provision_claim_token=claim_token,
    ).exists():
        raise ChatProvisionLeaseLostError("Chat provisioning lease was lost.")


@transaction.atomic
def _defer_provision_poll(
    link_id: int,
    claim_token: str,
    *,
    retry_after_seconds: int,
    transient_detail: str = "",
) -> dict[str, int]:
    """Advance one durable poll generation and release the worker lease."""

    link = (
        LensSessionLink.objects.select_for_update()
        .filter(
            pk=link_id,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_claim_token=claim_token,
        )
        .first()
    )
    if link is None:
        raise ChatProvisionLeaseLostError("Chat provisioning lease was lost.")
    delay = max(1, min(int(retry_after_seconds), _PROVISION_TRANSIENT_RETRY_MAX_SECONDS))
    link.provision_poll_sequence += 1
    link.provision_claim_token = None
    link.provision_claimed_at = None
    link.provision_next_retry_at = timezone.now() + timedelta(seconds=delay)
    state = dict(link.provision_state_json or {})
    if transient_detail:
        transient = dict(state.get("source_lens_transient") or {})
        transient.update(
            {
                "count": int(transient.get("count") or 0) + 1,
                "detail": transient_detail[:500],
                "last_seen_at": timezone.now().isoformat(),
            }
        )
        state["source_lens_transient"] = transient
        link.provision_state_json = state
        link.provision_detail = (
            "SourceLens is temporarily unavailable. Retrying automatically."
        )
    else:
        state.pop("source_lens_transient", None)
        link.provision_state_json = state
    link.save(
        update_fields=[
            "provision_poll_sequence",
            "provision_claim_token",
            "provision_claimed_at",
            "provision_next_retry_at",
            "provision_state_json",
            "provision_detail",
            "updated_at",
        ]
    )
    return {
        "generation": int(link.provision_generation),
        "sequence": int(link.provision_poll_sequence),
        "retry_after_seconds": delay,
    }


def _defer_provision_for_transient_error(
    link_id: int,
    claim_token: str,
    *,
    detail: str,
) -> dict[str, int]:
    state = (
        LensSessionLink.objects.filter(pk=link_id)
        .values_list("provision_state_json", flat=True)
        .first()
        or {}
    )
    transient = state.get("source_lens_transient") if isinstance(state, dict) else {}
    count = int((transient or {}).get("count") or 0) + 1
    delay = min(
        _PROVISION_TRANSIENT_RETRY_MAX_SECONDS,
        _PROVISION_TRANSIENT_RETRY_BASE_SECONDS * (2 ** min(count - 1, 4)),
    )
    return _defer_provision_poll(
        link_id,
        claim_token,
        retry_after_seconds=delay,
        transient_detail=detail,
    )


def _update_provision_claim(
    link: LensSessionLink,
    claim_token: str,
    *fields: str,
) -> None:
    """Persist provisioning progress only while the current lease is valid."""
    now = timezone.now()
    values = {field: getattr(link, field) for field in fields}
    values.update(provision_claimed_at=now, updated_at=now)
    updated = LensSessionLink.objects.filter(
        pk=link.id,
        lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        provision_claim_token=claim_token,
    ).update(**values)
    if updated != 1:
        raise ChatProvisionLeaseLostError("Chat provisioning lease was lost.")
    link.provision_claimed_at = now


def _remote_items(raw: Any) -> list[dict[str, Any]]:
    """Normalize SourceLens list and paginated-list responses."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("results", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _remote_items(value)
                if nested:
                    return nested
    return []


def _prepare_remote_operation(
    link: LensSessionLink,
    claim_token: str,
    *,
    kind: str,
    lookup_key: str = "",
) -> dict[str, Any]:
    """Persist remote-create intent before SourceLens receives the request."""
    state = dict(link.provision_state_json or {})
    operation = dict(state.get(kind) or {})
    if operation.get("status") in {"compensated", "not_created"}:
        operation = {}
    if not operation:
        operation_id = uuid_lib.uuid4()
        if kind == _SESSION_CREATE_OPERATION:
            lookup_key = f"__hfl_provision_{operation_id.hex}__"
        operation = {
            "operation_id": str(operation_id),
            "kind": kind,
            "lookup_key": lookup_key,
            "remote_uuid": "",
            "status": "intent",
            "created_at": timezone.now().isoformat(),
            "updated_at": timezone.now().isoformat(),
        }
    elif lookup_key and operation.get("lookup_key") != lookup_key:
        raise RuntimeError(f"Remote operation lookup key changed for {kind}.")
    state[kind] = operation
    link.provision_state_json = state
    _update_provision_claim(link, claim_token, "provision_state_json")
    return operation


def _record_remote_operation_resource(
    link: LensSessionLink,
    claim_token: str,
    *,
    kind: str,
    field: str,
    remote_uuid: uuid_lib.UUID,
) -> None:
    """Atomically bind a returned UUID to its journal and Chat field."""
    state = dict(link.provision_state_json or {})
    operation = dict(state.get(kind) or {})
    if not operation:
        raise RuntimeError(f"Remote operation intent is missing for {kind}.")
    operation["remote_uuid"] = str(remote_uuid)
    operation["status"] = "remote_created"
    operation["updated_at"] = timezone.now().isoformat()
    state[kind] = operation
    link.provision_state_json = state
    setattr(link, field, remote_uuid)
    _update_provision_claim(
        link,
        claim_token,
        "provision_state_json",
        field,
    )


@transaction.atomic
def _bind_assistant_to_provision_claim(
    link: LensSessionLink,
    claim_token: str,
    *,
    knowledge_source: LensKnowledgeSource,
    assistant_uuid: uuid_lib.UUID,
) -> None:
    """Atomically bind Assistant ownership while provisioning owns the Chat."""

    locked = (
        LensSessionLink.objects.select_for_update()
        .select_related("organization", "hfl_user")
        .filter(
            pk=link.id,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_claim_token=claim_token,
        )
        .first()
    )
    if locked is None:
        raise ChatProvisionLeaseLostError("Chat provisioning lease was lost.")
    locked_knowledge_source = (
        LensKnowledgeSource.objects.select_for_update()
        .filter(
            pk=knowledge_source.id,
            organization_id=locked.organization_id,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
        )
        .first()
    )
    if locked_knowledge_source is None:
        raise ChatProvisionLeaseLostError(
            "Knowledge Source was deleted during Chat provisioning."
        )

    state = dict(locked.provision_state_json or {})
    operation = dict(state.get(_ASSISTANT_CREATE_OPERATION) or {})
    if operation:
        recorded_uuid = str(operation.get("remote_uuid") or "").strip()
        if recorded_uuid and recorded_uuid != str(assistant_uuid):
            raise RuntimeError("Assistant create journal references another resource.")
        operation["remote_uuid"] = str(assistant_uuid)
        operation["status"] = "remote_created"
        operation["updated_at"] = timezone.now().isoformat()
        state[_ASSISTANT_CREATE_OPERATION] = operation

    locked.provision_state_json = state
    locked.sl_assistant_uuid = assistant_uuid
    locked_knowledge_source.sl_assistant_uuid = assistant_uuid
    locked_knowledge_source.save(update_fields=["sl_assistant_uuid", "updated_at"])
    assistant_access.ensure_assistant_link(
        org=locked.organization,
        sl_assistant_uuid=assistant_uuid,
        knowledge_source=locked_knowledge_source,
        created_by=locked.hfl_user,
        owner_user=locked.hfl_user,
        visibility_scope="user",
        lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
    )
    now = timezone.now()
    locked.provision_claimed_at = now
    locked.save(
        update_fields=[
            "provision_state_json",
            "sl_assistant_uuid",
            "provision_claimed_at",
            "updated_at",
        ]
    )
    link.provision_state_json = state
    link.sl_assistant_uuid = assistant_uuid
    link.provision_claimed_at = now
    knowledge_source.sl_assistant_uuid = assistant_uuid


def _operation_remote_uuid(
    link: LensSessionLink,
    kind: str,
) -> uuid_lib.UUID | None:
    operation = dict((link.provision_state_json or {}).get(kind) or {})
    value = str(operation.get("remote_uuid") or "").strip()
    return uuid_lib.UUID(value) if value else None


def _set_operation_status(
    link: LensSessionLink,
    kind: str,
    *,
    status: str,
    error: str = "",
) -> None:
    state = dict(link.provision_state_json or {})
    operation = dict(state.get(kind) or {})
    if not operation:
        return
    operation["status"] = status
    operation["last_error"] = error[:1000]
    operation["updated_at"] = timezone.now().isoformat()
    state[kind] = operation
    link.provision_state_json = state


def _late_remote_uuids(
    link: LensSessionLink,
    resource_kind: str,
) -> set[uuid_lib.UUID]:
    state = dict(link.provision_state_json or {})
    return {
        uuid_lib.UUID(str(item["remote_uuid"]))
        for item in state.get("late_resources") or []
        if isinstance(item, dict)
        and item.get("kind") == resource_kind
        and item.get("remote_uuid")
    }


def _retain_failed_late_resources(
    link: LensSessionLink,
    resource_kind: str,
    failed_uuids: list[uuid_lib.UUID],
) -> None:
    state = dict(link.provision_state_json or {})
    retained = [
        item
        for item in state.get("late_resources") or []
        if isinstance(item, dict) and item.get("kind") != resource_kind
    ]
    retained.extend(
        {
            "kind": resource_kind,
            "remote_uuid": str(remote_uuid),
            "updated_at": timezone.now().isoformat(),
        }
        for remote_uuid in failed_uuids
    )
    state["late_resources"] = retained
    link.provision_state_json = state


def _find_remote_uuid(
    *,
    path: str,
    field: str,
    value: str,
    hfl_user: AbstractBaseUser | None = None,
) -> uuid_lib.UUID | None:
    page_size = 100
    seen_pages: set[tuple[str, ...]] = set()
    for page in range(1, 1001):
        raw = sl_client.request_json(
            "GET",
            path,
            params={"page": page, "page_size": page_size},
            hfl_user=hfl_user,
        )
        items = _remote_items(raw)
        matches = [item for item in items if str(item.get(field) or "") == value]
        if len(matches) > 1:
            raise sl_client.LensBridgeError(
                f"SourceLens returned multiple {field}={value!r} resources."
            )
        if matches:
            remote_uuid = matches[0].get("uuid")
            if not remote_uuid:
                raise sl_client.LensBridgeError(
                    f"SourceLens {field}={value!r} resource has no uuid."
                )
            return uuid_lib.UUID(str(remote_uuid))
        if isinstance(raw, list) or not items or len(items) < page_size:
            return None
        signature = tuple(
            str(item.get("uuid") or item.get(field) or "") for item in items
        )
        if signature in seen_pages:
            raise sl_client.LensBridgeError(
                "SourceLens pagination did not advance while finding "
                f"{field}={value!r}."
            )
        seen_pages.add(signature)
    raise sl_client.LensBridgeError(
        f"SourceLens pagination limit reached while finding {field}={value!r}."
    )


def _recover_journal_resource(
    link: LensSessionLink,
    kind: str,
    *,
    hfl_user: AbstractBaseUser | None = None,
) -> uuid_lib.UUID | None:
    """Resolve an intent whose worker may have crashed after remote creation."""
    known_uuid = _operation_remote_uuid(link, kind)
    if known_uuid is not None:
        return known_uuid
    state = dict(link.provision_state_json or {})
    operation = dict(state.get(kind) or {})
    if not operation:
        return None
    lookup_key = str(operation.get("lookup_key") or "").strip()
    if not lookup_key:
        raise RuntimeError(f"Remote operation lookup key is missing for {kind}.")
    if kind == _ASSISTANT_CREATE_OPERATION:
        remote_uuid = _find_remote_uuid(
            path="/api/lens/assistants/",
            field="slug",
            value=lookup_key,
        )
    elif kind == _SESSION_CREATE_OPERATION:
        remote_uuid = _find_remote_uuid(
            path="/api/lens/sessions/",
            field="title",
            value=lookup_key,
            hfl_user=hfl_user,
        )
    else:
        raise ValueError(f"Unsupported remote operation kind: {kind}.")
    operation["status"] = "remote_created" if remote_uuid else "not_created"
    operation["remote_uuid"] = str(remote_uuid) if remote_uuid else ""
    operation["updated_at"] = timezone.now().isoformat()
    state[kind] = operation
    link.provision_state_json = state
    return remote_uuid


def _set_phase(
    link: LensSessionLink,
    claim_token: str,
    phase: str,
    detail: str,
) -> None:
    link.provision_phase = phase
    link.provision_detail = detail[:300]
    _update_provision_claim(
        link,
        claim_token,
        "provision_phase",
        "provision_detail",
    )


@transaction.atomic
def _complete_copilot_chat_provision(
    *,
    link_id: int,
    claim_token: str,
    knowledge_source_id: int,
    assistant_uuid: uuid_lib.UUID,
    session_uuid: uuid_lib.UUID,
) -> None:
    """Commit READY only if provisioning still owns the lifecycle lease."""
    link = (
        LensSessionLink.objects.select_for_update()
        .filter(
            pk=link_id,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_claim_token=claim_token,
        )
        .first()
    )
    if link is None:
        raise ChatProvisionLeaseLostError("Chat provisioning lease was lost.")
    if link.knowledge_source_id != knowledge_source_id:
        raise RuntimeError("Chat knowledge source changed during provisioning.")
    updated_ks = LensKnowledgeSource.objects.filter(
        pk=knowledge_source_id,
        lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
    ).update(
        status=LensKnowledgeSource.Status.READY,
        status_detail="Restored data and Assistant are ready for chat.",
        updated_at=timezone.now(),
    )
    if updated_ks != 1:
        raise ChatProvisionLeaseLostError(
            "Knowledge Source was deleted during Chat provisioning."
        )
    link.sl_assistant_uuid = assistant_uuid
    link.sl_session_uuid = session_uuid
    link.lifecycle_status = LensSessionLink.LifecycleStatus.READY
    link.provision_phase = LensSessionLink.ProvisionPhase.READY
    link.provision_detail = "Chat is ready."
    link.lifecycle_error = ""
    link.lifecycle_error_state_json = {}
    provision_state = dict(link.provision_state_json or {})
    for kind in (_ASSISTANT_CREATE_OPERATION, _SESSION_CREATE_OPERATION):
        operation = dict(provision_state.get(kind) or {})
        if operation:
            operation["status"] = "bound"
            operation["updated_at"] = timezone.now().isoformat()
            provision_state[kind] = operation
    provision_state.pop("source_lens_transient", None)
    link.provision_state_json = provision_state
    link.cleanup_intent = LensSessionLink.CleanupIntent.NONE
    link.cleanup_status = LensSessionLink.CleanupStatus.NONE
    link.provision_claim_token = None
    link.provision_claimed_at = None
    link.provision_next_retry_at = None
    link.save(
        update_fields=[
            "sl_assistant_uuid",
            "sl_session_uuid",
            "lifecycle_status",
            "provision_phase",
            "provision_detail",
            "lifecycle_error",
            "lifecycle_error_state_json",
            "provision_state_json",
            "cleanup_intent",
            "cleanup_status",
            "provision_claim_token",
            "provision_claimed_at",
            "provision_next_retry_at",
            "updated_at",
        ]
    )
    transaction.on_commit(
        lambda: gateway_chat_queue.release_chat_prepare_slot(
            session_link_id=link.id,
            expected_generation=link.provision_generation,
        )
    )


def _orphan_knowledge_source_needs_enqueue(knowledge_source_id: int) -> bool:
    """Return True when no live lease or future retry already covers the KS."""
    now = timezone.now()
    knowledge_source = (
        LensKnowledgeSource.all_objects.filter(pk=knowledge_source_id)
        .only(
            "id",
            "lifecycle_status",
            "teardown_claimed_at",
            "teardown_next_retry_at",
            "teardown_state_json",
        )
        .first()
    )
    if knowledge_source is None:
        return False
    if knowledge_source.lifecycle_status == LensKnowledgeSource.LifecycleStatus.DELETED:
        return False
    if teardown_blocking.intervention_required(
        knowledge_source.teardown_state_json
    ):
        return False
    if (
        knowledge_source.teardown_claimed_at
        and knowledge_source.teardown_claimed_at
        > now - timedelta(seconds=TEARDOWN_CLAIM_TTL_SECONDS)
    ):
        return False
    if (
        knowledge_source.teardown_next_retry_at
        and knowledge_source.teardown_next_retry_at > now
    ):
        return False
    return True


def _enqueue_orphan_knowledge_source_teardown(
    knowledge_source_id: int,
) -> None:
    """Enqueue durable KS teardown when no backoff/lease already covers it."""
    if not _orphan_knowledge_source_needs_enqueue(knowledge_source_id):
        return
    from apps.lens_bridge.services.knowledge_source_teardown import _queue_teardown

    _queue_teardown(knowledge_source_id)


def _cleanup_orphan_knowledge_source(
    knowledge_source: LensKnowledgeSource,
    *,
    owner_session_link_id: int,
) -> None:
    """Durably tear down a KS that could not be attached to its Chat.

    Prefer an owner-aware inline teardown. ``busy`` / ``scheduled`` already have
    a live worker or a recorded ``teardown_next_retry_at`` for reconciler, so
    they are not re-queued. Unexpected failures enqueue only when no live lease
    or future retry is already recorded (avoids IncompleteError no-op Celery).
    """
    from apps.lens_bridge.services.knowledge_source_teardown import (
        run_knowledge_source_teardown,
    )

    LensKnowledgeSource.all_objects.filter(pk=knowledge_source.id).exclude(
        lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETED
    ).update(
        lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
        status_detail="Knowledge source deletion is queued.",
        updated_at=timezone.now(),
    )
    try:
        result = run_knowledge_source_teardown(
            knowledge_source_id=knowledge_source.id,
            owner_session_link_id=owner_session_link_id,
        )
        status = str(result.get("status") or "")
        if status in {"deleted", "busy", "scheduled"}:
            return
        raise RuntimeError("Knowledge Source teardown is " + status)
    except Exception:
        logger.exception(
            "orphan knowledge source cleanup deferred knowledge_source_id=%s",
            knowledge_source.id,
        )
        _enqueue_orphan_knowledge_source_teardown(knowledge_source.id)


@transaction.atomic
def _record_late_source_lens_resource(
    link_id: int,
    *,
    field: str,
    resource_uuid: uuid_lib.UUID,
    error: str,
) -> None:
    """Reopen teardown when immediate compensation cannot delete a late resource."""
    if field not in {"sl_session_uuid", "sl_assistant_uuid"}:
        raise ValueError("Unsupported late SourceLens resource field.")
    link = LensSessionLink.objects.select_for_update().get(pk=link_id)
    existing = getattr(link, field)
    if existing not in {None, resource_uuid}:
        resource_kind = "session" if field == "sl_session_uuid" else "assistant"
        late_resources = _late_remote_uuids(link, resource_kind)
        late_resources.add(resource_uuid)
        _retain_failed_late_resources(
            link,
            resource_kind,
            sorted(late_resources, key=str),
        )
        update_fields = [
            "provision_state_json",
            "lifecycle_error",
            "updated_at",
        ]
    else:
        setattr(link, field, resource_uuid)
        update_fields = [field, "lifecycle_error", "updated_at"]
    delete_session = (
        link.lifecycle_status
        in {
            LensSessionLink.LifecycleStatus.DELETING,
            LensSessionLink.LifecycleStatus.DELETED,
        }
        or link.cleanup_intent == LensSessionLink.CleanupIntent.DELETE_SESSION
        or link.status == LensSessionLink.Status.ARCHIVED
    )
    cleanup_intent = (
        LensSessionLink.CleanupIntent.DELETE_SESSION
        if delete_session
        else LensSessionLink.CleanupIntent.RESET_FOR_RETRY
    )
    link.lifecycle_error = error[:2000]
    link.lifecycle_error_state_json = lifecycle_error_state_from_exception(
        RuntimeError(link.lifecycle_error)
    )
    link.cleanup_intent = cleanup_intent
    link.cleanup_status = LensSessionLink.CleanupStatus.PENDING
    teardown_state = dict(link.teardown_state_json or {})
    teardown_state["intent"] = cleanup_intent
    link.teardown_state_json = teardown_state
    update_fields.extend(
        [
            "lifecycle_error_state_json",
            "cleanup_intent",
            "cleanup_status",
            "teardown_state_json",
        ]
    )
    link.teardown_claim_token = None
    link.teardown_claimed_at = None
    link.teardown_next_retry_at = None
    update_fields.extend(
        [
            "teardown_claim_token",
            "teardown_claimed_at",
            "teardown_next_retry_at",
        ]
    )
    link.provision_claim_token = None
    link.provision_claimed_at = None
    link.provision_next_retry_at = None
    link.provision_generation += 1
    link.provision_poll_sequence = 0
    update_fields.extend(
        [
            "provision_claim_token",
            "provision_claimed_at",
            "provision_next_retry_at",
            "provision_generation",
            "provision_poll_sequence",
        ]
    )
    if delete_session:
        link.lifecycle_status = LensSessionLink.LifecycleStatus.DELETING
        link.status = LensSessionLink.Status.ARCHIVED
        link.provision_phase = LensSessionLink.ProvisionPhase.DELETING
        link.provision_detail = "Deleting a resource returned after Chat deletion."
    else:
        link.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        link.status = LensSessionLink.Status.ACTIVE
        link.provision_phase = LensSessionLink.ProvisionPhase.CLEANING_UP
        link.provision_detail = "Cleaning up a late resource from failed preparation."
    update_fields.extend(
        [
            "lifecycle_status",
            "status",
            "provision_phase",
            "provision_detail",
        ]
    )
    link.save(update_fields=update_fields)
    transaction.on_commit(lambda: _queue_teardown_or_record_error(link.id))


def _compensate_late_session(
    link_id: int,
    session_uuid: uuid_lib.UUID,
    *,
    user: AbstractBaseUser,
) -> None:
    try:
        sl_client.request_json(
            "DELETE",
            f"/api/lens/sessions/{session_uuid}/",
            hfl_user=user,
        )
    except Exception as exc:
        if _source_lens_not_found(exc):
            return
        _record_late_source_lens_resource(
            link_id,
            field="sl_session_uuid",
            resource_uuid=session_uuid,
            error=f"Late session compensation failed: {exc}",
        )


def _compensate_late_assistant(
    link_id: int,
    assistant_uuid: uuid_lib.UUID,
) -> None:
    link = (
        LensSessionLink.objects.select_related("organization")
        .filter(pk=link_id)
        .first()
    )
    if link is None:
        return
    try:
        from apps.lens_bridge.services.assistants import _delete_sl_assistant

        _delete_sl_assistant(assistant_uuid)
        assistant_access.soft_delete_assistant_link(
            link.organization,
            assistant_uuid,
        )
    except Exception as exc:
        if _source_lens_not_found(exc):
            return
        _record_late_source_lens_resource(
            link_id,
            field="sl_assistant_uuid",
            resource_uuid=assistant_uuid,
            error=f"Late assistant compensation failed: {exc}",
        )


def _claim_copilot_chat_teardown(session_link_id: int) -> tuple[str | None, str]:
    now = timezone.now()
    with transaction.atomic():
        link = (
            LensSessionLink.objects.select_for_update()
            .filter(pk=session_link_id)
            .first()
        )
        if link is None:
            return None, "missing"
        if link.lifecycle_status == LensSessionLink.LifecycleStatus.DELETED:
            return None, "deleted"
        if link.cleanup_intent == LensSessionLink.CleanupIntent.NONE:
            legacy_intent = str((link.teardown_state_json or {}).get("intent") or "")
            if (
                link.lifecycle_status == LensSessionLink.LifecycleStatus.DELETING
                and not legacy_intent
            ):
                legacy_intent = _TEARDOWN_INTENT_DELETE
            if (
                link.lifecycle_status == LensSessionLink.LifecycleStatus.DELETING
                and legacy_intent
                in {_TEARDOWN_INTENT_DELETE, _TEARDOWN_INTENT_RESET_FOR_RETRY}
            ):
                link.cleanup_intent = legacy_intent
                link.cleanup_status = LensSessionLink.CleanupStatus.PENDING
                if legacy_intent == _TEARDOWN_INTENT_RESET_FOR_RETRY:
                    link.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        delete_cleanup = (
            link.cleanup_intent == LensSessionLink.CleanupIntent.DELETE_SESSION
            and link.lifecycle_status == LensSessionLink.LifecycleStatus.DELETING
        )
        reset_cleanup = (
            link.cleanup_intent == LensSessionLink.CleanupIntent.RESET_FOR_RETRY
            and link.lifecycle_status == LensSessionLink.LifecycleStatus.FAILED
        )
        if not (delete_cleanup or reset_cleanup):
            return None, str(link.lifecycle_status)
        if link.cleanup_status == LensSessionLink.CleanupStatus.COMPLETE:
            return None, "complete"
        if teardown_blocking.intervention_required(link.teardown_state_json):
            return None, "intervention_required"
        if link.teardown_claimed_at and link.teardown_claimed_at > now - timedelta(
            seconds=TEARDOWN_CLAIM_TTL_SECONDS
        ):
            return None, "busy"
        if link.teardown_next_retry_at and link.teardown_next_retry_at > now:
            return None, "scheduled"
        claim_token = uuid_lib.uuid4()
        link.teardown_attempts += 1
        link.cleanup_status = LensSessionLink.CleanupStatus.RUNNING
        link.teardown_claim_token = claim_token
        link.teardown_claimed_at = now
        link.teardown_next_retry_at = next_retry_at(link.teardown_attempts)
        link.save(
            update_fields=[
                "teardown_attempts",
                "lifecycle_status",
                "cleanup_intent",
                "cleanup_status",
                "teardown_claim_token",
                "teardown_claimed_at",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )
    return str(claim_token), "claimed"


def _source_lens_not_found(exc: Exception) -> bool:
    return (
        isinstance(exc, sl_client.LensBridgeError)
        and getattr(exc, "status_code", None) == 404
    )


def _teardown_step(
    state: dict[str, Any],
    step: str,
    *,
    status: str,
    error: str = "",
) -> None:
    state[step] = {
        "status": status,
        "error": error[:1000],
        "updated_at": timezone.now().isoformat(),
    }


def _update_chat_claim(
    link: LensSessionLink,
    claim_token: str,
    *fields: str,
) -> None:
    """Persist intermediate Chat teardown state under the current lease."""

    values = {field: getattr(link, field) for field in fields}
    values["updated_at"] = timezone.now()
    updated = LensSessionLink.objects.filter(
        pk=link.id,
        teardown_claim_token=claim_token,
        cleanup_status=LensSessionLink.CleanupStatus.RUNNING,
    ).update(**values)
    if updated != 1:
        raise ChatTeardownIncompleteError("Chat teardown lease was lost.")


def run_copilot_chat_teardown(*, session_link_id: int) -> dict[str, Any]:
    claim_token, claim_status = _claim_copilot_chat_teardown(session_link_id)
    if claim_token is None:
        return {"session_link_id": session_link_id, "status": claim_status}
    link = (
        LensSessionLink.objects.select_related(
            "knowledge_source",
            "knowledge_source__workspace_binding",
            "hfl_user",
            "organization",
            "chat_binding",
        )
        .filter(pk=session_link_id)
        .first()
    )
    if link is None:
        return {"session_link_id": session_link_id, "status": "missing"}

    org = link.organization
    user = link.hfl_user
    critical_errors: list[str] = []
    warnings: list[str] = []
    teardown_state = dict(link.teardown_state_json or {})
    teardown_intent = str(link.cleanup_intent or teardown_state.get("intent") or "")
    if teardown_intent not in {
        _TEARDOWN_INTENT_DELETE,
        _TEARDOWN_INTENT_RESET_FOR_RETRY,
    }:
        teardown_intent = _TEARDOWN_INTENT_DELETE
    teardown_state["intent"] = teardown_intent

    if link.active_run_uuid:
        try:
            sl_client.request_json(
                "POST",
                f"/api/lens/runs/{link.active_run_uuid}/cancel/",
                hfl_user=user,
            )
        except Exception as exc:
            if not _source_lens_not_found(exc):
                warnings.append(f"cancel_run: {exc}")
                _teardown_step(
                    teardown_state, "cancel_run", status="warning", error=str(exc)
                )
            else:
                _teardown_step(teardown_state, "cancel_run", status="success")
        else:
            _teardown_step(teardown_state, "cancel_run", status="success")

    share_cleanup_complete = True
    try:
        from apps.lens_bridge.services.copilot_sharing import (
            revoke_session_shares,
        )

        revoked_share_count = revoke_session_shares(link)
        _teardown_step(teardown_state, "revoke_shares", status="success")
        teardown_state["revoke_shares"]["count"] = revoked_share_count
    except Exception as exc:
        share_cleanup_complete = False
        critical_errors.append(f"revoke_shares: {exc}")
        _teardown_step(
            teardown_state,
            "revoke_shares",
            status="retry",
            error=str(exc),
        )

    session_recovery_failed = False
    journal_session_uuid = None
    if share_cleanup_complete:
        try:
            journal_session_uuid = _recover_journal_resource(
                link,
                _SESSION_CREATE_OPERATION,
                hfl_user=user,
            )
        except Exception as exc:
            session_recovery_failed = True
            critical_errors.append(f"recover_session_operation: {exc}")
    session_uuids = {
        item
        for item in (link.sl_session_uuid, journal_session_uuid)
        if item is not None
    }
    session_uuids.update(_late_remote_uuids(link, "session"))
    failed_session_uuids: list[uuid_lib.UUID] = []
    if share_cleanup_complete:
        for session_uuid in sorted(session_uuids, key=str):
            try:
                sl_client.request_json(
                    "DELETE",
                    f"/api/lens/sessions/{session_uuid}/",
                    hfl_user=user,
                )
            except Exception as exc:
                if not _source_lens_not_found(exc):
                    failed_session_uuids.append(session_uuid)
                    critical_errors.append(f"delete_session {session_uuid}: {exc}")
    else:
        failed_session_uuids = sorted(session_uuids, key=str)
    link.sl_session_uuid = failed_session_uuids[0] if failed_session_uuids else None
    if journal_session_uuid and journal_session_uuid not in failed_session_uuids:
        _set_operation_status(
            link,
            _SESSION_CREATE_OPERATION,
            status="compensated",
        )
    _retain_failed_late_resources(
        link,
        "session",
        failed_session_uuids,
    )
    if not share_cleanup_complete:
        _teardown_step(
            teardown_state,
            "delete_session",
            status="blocked",
            error="Shared Q&A revocation must finish before session deletion.",
        )
    elif failed_session_uuids or session_recovery_failed:
        detail = "; ".join(
            error
            for error in critical_errors
            if error.startswith(("delete_session ", "recover_session_operation:"))
        )
        _teardown_step(
            teardown_state,
            "delete_session",
            status="retry",
            error=detail,
        )
    else:
        _teardown_step(teardown_state, "delete_session", status="success")
    _update_chat_claim(
        link,
        claim_token,
        "sl_session_uuid",
        "provision_state_json",
    )

    session_cleanup_complete = (
        share_cleanup_complete
        and not failed_session_uuids
        and not session_recovery_failed
    )
    assistant_recovery_failed = False
    journal_assistant_uuid: uuid_lib.UUID | None = None
    failed_assistant_uuids: list[uuid_lib.UUID] = []
    ks = link.knowledge_source
    cleanup_waiting_for_conversion_stop = False
    workspace_intervention_required = False
    workspace_blocking: dict[str, Any] = {}
    assistant_uuids: set[uuid_lib.UUID] = set()
    if session_cleanup_complete:
        try:
            journal_assistant_uuid = _recover_journal_resource(
                link,
                _ASSISTANT_CREATE_OPERATION,
            )
        except Exception as exc:
            assistant_recovery_failed = True
            critical_errors.append(f"recover_assistant_operation: {exc}")
        assistant_uuids = {
            item
            for item in (link.sl_assistant_uuid, journal_assistant_uuid)
            if item is not None
        }
        assistant_uuids.update(_late_remote_uuids(link, "assistant"))
        for assistant_uuid in sorted(assistant_uuids, key=str):
            try:
                from apps.lens_bridge.services.assistants import (
                    _delete_sl_assistant,
                )

                _delete_sl_assistant(assistant_uuid)
                assistant_access.soft_delete_assistant_link(org, assistant_uuid)
            except Exception as exc:
                failed_assistant_uuids.append(assistant_uuid)
                critical_errors.append(f"delete_assistant {assistant_uuid}: {exc}")
        link.sl_assistant_uuid = (
            failed_assistant_uuids[0] if failed_assistant_uuids else None
        )
        if (
            journal_assistant_uuid
            and journal_assistant_uuid not in failed_assistant_uuids
        ):
            _set_operation_status(
                link,
                _ASSISTANT_CREATE_OPERATION,
                status="compensated",
            )
        _retain_failed_late_resources(
            link,
            "assistant",
            failed_assistant_uuids,
        )
        if ks is not None:
            deleted_assistant_uuids = assistant_uuids.difference(failed_assistant_uuids)
            if ks.sl_assistant_uuid in deleted_assistant_uuids:
                LensKnowledgeSource.objects.filter(
                    pk=ks.id,
                    sl_assistant_uuid=ks.sl_assistant_uuid,
                ).update(sl_assistant_uuid=None, updated_at=timezone.now())
                ks.sl_assistant_uuid = None
        if failed_assistant_uuids or assistant_recovery_failed:
            detail = "; ".join(
                error
                for error in critical_errors
                if error.startswith(
                    ("delete_assistant ", "recover_assistant_operation:")
                )
            )
            _teardown_step(
                teardown_state,
                "delete_assistant",
                status="retry",
                error=detail,
            )
        else:
            _teardown_step(
                teardown_state,
                "delete_assistant",
                status="success",
            )
    else:
        _teardown_step(
            teardown_state,
            "delete_assistant",
            status="blocked",
            error="Session deletion must finish before Assistant deletion.",
        )
    _update_chat_claim(
        link,
        claim_token,
        "sl_assistant_uuid",
        "provision_state_json",
    )

    if (
        ks is not None
        and session_cleanup_complete
        and not failed_assistant_uuids
        and not assistant_recovery_failed
    ):
        try:
            from apps.lens_bridge.services.knowledge_source_teardown import (
                run_knowledge_source_teardown,
            )

            result = run_knowledge_source_teardown(
                knowledge_source_id=ks.id,
                owner_session_link_id=link.id,
            )
            if result.get("status") not in {"deleted"}:
                raise RuntimeError(
                    "Knowledge Source teardown is " + str(result.get("status"))
                )
            link.knowledge_source = None
            link.sl_assistant_uuid = None
            _update_chat_claim(
                link,
                claim_token,
                "knowledge_source",
                "sl_assistant_uuid",
                "provision_state_json",
            )
            _teardown_step(teardown_state, "delete_assistant", status="success")
            _teardown_step(teardown_state, "cleanup_workspace", status="success")
        except Exception as exc:
            critical_errors.append(f"cleanup_workspace: {exc}")
            latest_ks_teardown_state = (
                LensKnowledgeSource.all_objects.filter(pk=ks.id)
                .values_list("teardown_state_json", flat=True)
                .first()
                or {}
            )
            if isinstance(latest_ks_teardown_state, dict):
                candidate_blocking = latest_ks_teardown_state.get("blocking")
                if isinstance(candidate_blocking, dict):
                    workspace_blocking = candidate_blocking
                workspace_intervention_required = (
                    teardown_blocking.intervention_required(
                        latest_ks_teardown_state
                    )
                )
            cleanup_waiting_for_conversion_stop = bool(
                isinstance(latest_ks_teardown_state, dict)
                and (
                    latest_ks_teardown_state.get("cancel_conversion") or {}
                ).get("status")
                == "waiting"
            )
            _teardown_step(
                teardown_state, "cleanup_workspace", status="retry", error=str(exc)
            )
    elif ks is None:
        _teardown_step(teardown_state, "cleanup_workspace", status="success")
    else:
        dependency = (
            "Session deletion must finish before workspace cleanup."
            if not session_cleanup_complete
            else "Assistant deletion must finish before workspace cleanup."
        )
        _teardown_step(
            teardown_state,
            "cleanup_workspace",
            status="blocked",
            error=dependency,
        )

    reset_for_retry = teardown_intent == _TEARDOWN_INTENT_RESET_FOR_RETRY
    blocking: dict[str, Any] = {}
    if critical_errors:
        if workspace_blocking:
            blocking_reason = str(
                workspace_blocking.get("reason")
                or "conversion_stop_unconfirmed"
            )
            blocking_task_id = str(workspace_blocking.get("task_id") or "")
            blocking_remote_status = str(
                workspace_blocking.get("remote_status") or ""
            )
            blocking_stop_source = str(
                workspace_blocking.get("stop_confirmation_source") or ""
            )
        else:
            # Exception messages may contain transient details. Use the stable
            # step prefix for the retry fingerprint and retain full details in
            # lifecycle_error.
            blocking_reason = str(critical_errors[0].split(":", 1)[0])[:300]
            blocking_task_id = ""
            blocking_remote_status = ""
            blocking_stop_source = ""
        if workspace_intervention_required and workspace_blocking:
            blocking = dict(workspace_blocking)
            teardown_state["blocking"] = blocking
        else:
            teardown_state, blocking = teardown_blocking.record_blocking(
                teardown_state,
                reason=blocking_reason,
                task_id=blocking_task_id,
                gateway_link_id=link.gateway_link_id,
                remote_status=blocking_remote_status,
                stop_confirmation_source=blocking_stop_source,
            )
    else:
        teardown_state = teardown_blocking.clear_blocking(teardown_state)
    intervention_required = bool(blocking.get("intervention_required"))
    cleanup_blocked = (
        cleanup_waiting_for_conversion_stop or intervention_required
    )
    if critical_errors:
        link.lifecycle_status = (
            LensSessionLink.LifecycleStatus.FAILED
            if reset_for_retry
            else LensSessionLink.LifecycleStatus.DELETING
        )
        link.status = (
            LensSessionLink.Status.ACTIVE
            if reset_for_retry
            else LensSessionLink.Status.ARCHIVED
        )
        link.provision_phase = LensSessionLink.ProvisionPhase.CLEANING_UP
        link.cleanup_status = (
            LensSessionLink.CleanupStatus.BLOCKED
            if cleanup_blocked
            else LensSessionLink.CleanupStatus.PENDING
        )
        link.provision_detail = (
            "Chat cleanup requires operator intervention."
            if intervention_required
            else (
                "Cleanup is waiting for SourceLens to confirm conversion has stopped."
                if cleanup_waiting_for_conversion_stop
                else "Chat cleanup is incomplete and will be retried."
            )
        )
        link.lifecycle_error = "; ".join([*critical_errors, *warnings])[:2000]
        link.lifecycle_error_state_json = lifecycle_error_state_from_exception(
            RuntimeError(link.lifecycle_error)
        )
    elif reset_for_retry:
        link.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
        link.status = LensSessionLink.Status.ACTIVE
        link.provision_phase = LensSessionLink.ProvisionPhase.QUEUED
        link.provision_detail = "Chat preparation failed. Retry to try again."
        link.cleanup_status = LensSessionLink.CleanupStatus.COMPLETE
        provision_error = str(teardown_state.get("provision_error") or "")
        link.lifecycle_error = "; ".join(
            item for item in [provision_error, *warnings] if item
        )[:2000]
        provision_error_state = teardown_state.get("provision_error_state")
        link.lifecycle_error_state_json = (
            dict(provision_error_state)
            if isinstance(provision_error_state, dict)
            else {}
        )
    else:
        link.lifecycle_status = LensSessionLink.LifecycleStatus.DELETED
        link.status = LensSessionLink.Status.ARCHIVED
        link.provision_phase = LensSessionLink.ProvisionPhase.DELETED
        link.provision_detail = "Chat resources deleted."
        link.cleanup_status = LensSessionLink.CleanupStatus.COMPLETE
        link.lifecycle_error = "; ".join(warnings)[:2000]
        link.lifecycle_error_state_json = {}
    link.active_run_uuid = None
    link.active_run_status = ""
    link.teardown_state_json = teardown_state
    link.teardown_claim_token = None
    link.teardown_claimed_at = None
    if critical_errors:
        link.teardown_next_retry_at = (
            None
            if intervention_required
            else next_retry_at(int(blocking["consecutive_attempts"]))
        )
    else:
        link.teardown_next_retry_at = None
    slot_generation = (
        LensGatewayChatSlot.objects.filter(session_link_id=link.id)
        .values_list("session_generation", flat=True)
        .first()
    )
    final_query = LensSessionLink.objects.filter(
        pk=link.id,
        teardown_claim_token=claim_token,
        cleanup_status=LensSessionLink.CleanupStatus.RUNNING,
    )
    if not critical_errors:
        final_query = final_query.filter(
            sl_session_uuid__isnull=True,
            sl_assistant_uuid__isnull=True,
            knowledge_source__isnull=True,
        )
    updated = final_query.update(
        lifecycle_status=link.lifecycle_status,
        status=link.status,
        provision_phase=link.provision_phase,
        provision_detail=link.provision_detail,
        lifecycle_error=link.lifecycle_error,
        lifecycle_error_state_json=link.lifecycle_error_state_json,
        active_run_uuid=None,
        active_run_status="",
        teardown_state_json=teardown_state,
        cleanup_status=link.cleanup_status,
        teardown_claim_token=None,
        teardown_claimed_at=None,
        teardown_next_retry_at=link.teardown_next_retry_at,
        capacity_reservation_status=(
            LensSessionLink.CapacityReservationStatus.RELEASED
            if not critical_errors
            else link.capacity_reservation_status
        ),
        updated_at=timezone.now(),
    )
    if updated != 1:
        raise ChatTeardownIncompleteError("Chat teardown lease was lost.")
    if critical_errors:
        logger.warning(
            "chat teardown blocked chat_id=%s knowledge_source_id=%s "
            "gateway_link_id=%s task_id=%s remote_status=%s reason=%s "
            "attempts=%s intervention_required=%s",
            link.id,
            ks.id if ks is not None else None,
            link.gateway_link_id,
            blocking.get("task_id"),
            blocking.get("remote_status"),
            blocking.get("reason"),
            blocking.get("consecutive_attempts"),
            intervention_required,
        )
        raise ChatTeardownIncompleteError("; ".join(critical_errors))
    if slot_generation is not None:
        gateway_chat_queue.release_chat_prepare_slot(
            session_link_id=link.id,
            expected_generation=int(slot_generation),
        )
    return {
        "session_link_id": link.id,
        "status": "retryable" if reset_for_retry else "deleted",
        "warnings": warnings,
        "gateway_untouched": True,
    }


def _mark_provision_failed_by_id(
    session_link_id: int,
    claim_token: str,
    message: str,
    *,
    error_state: dict[str, Any] | None = None,
    expected_generation: int,
) -> None:
    updated = LensSessionLink.objects.filter(
        pk=session_link_id,
        lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        provision_claim_token=claim_token,
    ).update(
        lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
        provision_phase=LensSessionLink.ProvisionPhase.QUEUED,
        provision_detail="Chat preparation failed.",
        lifecycle_error=(message or "provision failed")[:2000],
        lifecycle_error_state_json=dict(error_state or {}),
        provision_claim_token=None,
        provision_claimed_at=None,
        provision_next_retry_at=None,
        cleanup_intent=LensSessionLink.CleanupIntent.RESET_FOR_RETRY,
        cleanup_status=LensSessionLink.CleanupStatus.COMPLETE,
        capacity_reservation_status=LensSessionLink.CapacityReservationStatus.RELEASED,
        updated_at=timezone.now(),
    )
    if updated:
        released_gateway_id = gateway_chat_queue.release_chat_prepare_slot(
            session_link_id=session_link_id,
            expected_generation=expected_generation,
        )
        if released_gateway_id is None:
            gateway_link_id = (
                LensSessionLink.objects.filter(pk=session_link_id)
                .values_list("gateway_link_id", flat=True)
                .first()
            )
            if gateway_link_id is not None:
                # The failed session may have been the FIFO head without ever
                # acquiring a heavy-work slot (for example, scope validation
                # failed). There is no slot-release callback in that case.
                gateway_chat_queue.wake_gateway_queue(int(gateway_link_id))


@transaction.atomic
def _transition_failed_provision_to_teardown(
    session_link_id: int,
    claim_token: str,
    *,
    message: str,
    error_state: dict[str, Any] | None = None,
) -> bool:
    """Fence retry and hand incomplete compensation to durable teardown."""
    link = (
        LensSessionLink.objects.select_for_update()
        .filter(
            pk=session_link_id,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_claim_token=claim_token,
        )
        .first()
    )
    if link is None:
        return False
    link.lifecycle_status = LensSessionLink.LifecycleStatus.FAILED
    link.status = LensSessionLink.Status.ACTIVE
    link.provision_phase = LensSessionLink.ProvisionPhase.CLEANING_UP
    link.provision_detail = "Provisioning cleanup is incomplete and will be retried."
    link.lifecycle_error = message[:2000]
    link.lifecycle_error_state_json = dict(error_state or {})
    link.provision_claim_token = None
    link.provision_claimed_at = None
    link.provision_next_retry_at = None
    link.provision_generation += 1
    link.provision_poll_sequence = 0
    link.cleanup_intent = LensSessionLink.CleanupIntent.RESET_FOR_RETRY
    link.cleanup_status = LensSessionLink.CleanupStatus.PENDING
    link.teardown_attempts = 0
    link.teardown_claim_token = None
    link.teardown_claimed_at = None
    link.teardown_next_retry_at = None
    link.teardown_state_json = {
        "intent": _TEARDOWN_INTENT_RESET_FOR_RETRY,
        "provision_error": message[:2000],
        "provision_error_state": dict(error_state or {}),
    }
    link.save(
        update_fields=[
            "lifecycle_status",
            "status",
            "provision_phase",
            "provision_detail",
            "lifecycle_error",
            "lifecycle_error_state_json",
            "provision_claim_token",
            "provision_claimed_at",
            "provision_next_retry_at",
            "provision_generation",
            "provision_poll_sequence",
            "cleanup_intent",
            "cleanup_status",
            "teardown_attempts",
            "teardown_claim_token",
            "teardown_claimed_at",
            "teardown_next_retry_at",
            "teardown_state_json",
            "updated_at",
        ]
    )
    transaction.on_commit(lambda: _queue_teardown_or_record_error(link.id))
    if link.gateway_link_id is not None:
        # A failure before slot acquisition removes this session from the FIFO
        # head without releasing a slot. Wake the next waiter immediately; if
        # this session does own a slot, the occupied-slot count keeps the
        # Gateway fenced until teardown confirms cleanup.
        transaction.on_commit(
            lambda: gateway_chat_queue.wake_gateway_queue(link.gateway_link_id)
        )
    return True


def _assert_retry_public_gateway_capacity(*, session: LensSessionLink) -> None:
    """Recheck Public Gateway admission before a failed Chat is requeued."""
    from apps.lens_bridge.models import LensGatewayLink

    if (
        session.gateway_link_id is None
        or session.gateway_link.scope != LensGatewayLink.GatewayScope.PLATFORM
    ):
        return

    # A stale provisioning attempt with a durable reservation, or a failed
    # attempt that already owns a Knowledge Source, is resuming an existing
    # allocation. Rechecking it as an addition would count the same workspace
    # in both current usage and requested usage.
    if (
        session.capacity_reservation_status
        == LensSessionLink.CapacityReservationStatus.RESERVED
        and (
            session.lifecycle_status
            == LensSessionLink.LifecycleStatus.PROVISIONING
            or session.knowledge_source_id is not None
        )
    ):
        return

    from common.errors import AppError
    from common.extension_spi import get_quota_provider
    from apps.lens_bridge.services.public_gateway_capacity import (
        assert_public_gateway_capacity,
        lock_public_gateway_capacity,
        session_scope_occupancy,
    )
    from apps.subscription.services.interface import enforce_license_quota

    requested_bytes, unknown_size = session_scope_occupancy(session=session)
    Organization.objects.select_for_update().get(pk=session.organization_id)
    gateway = lock_public_gateway_capacity(gateway_link=session.gateway_link)
    assert_public_gateway_capacity(
        gateway_link=gateway,
        additional_bytes=requested_bytes,
        unknown_size=unknown_size,
    )

    provider = get_quota_provider()
    if provider is None:
        return
    limits = provider.get_limits(session.organization) or {}
    if "max_public_gateway_capacity_bytes" not in limits:
        raise AppError(
            code="SUBSCRIPTION.QUOTA_USAGE_UNAVAILABLE",
            status=503,
            retryable=True,
            title="Organization public gateway capacity is unavailable.",
            diagnostic="max_public_gateway_capacity_bytes missing from quota limits",
            meta={
                "quota_type": "max_public_gateway_capacity_bytes",
                "scope": "organization",
            },
        )
    enforce_license_quota(
        session.organization,
        "max_public_gateway_capacity_bytes",
        additional=requested_bytes,
    )


@transaction.atomic
def retry_copilot_chat_provision(link: LensSessionLink) -> LensSessionLink:
    locked = (
        LensSessionLink.objects.select_for_update(of=("self",))
        .select_related(
            "gateway_link",
            "organization",
            "chat_binding__gateway_link",
        )
        .get(pk=link.pk)
    )
    if locked.lifecycle_status == LensSessionLink.LifecycleStatus.READY:
        return locked
    if locked.lifecycle_status == LensSessionLink.LifecycleStatus.PROVISIONING:
        claim_is_live = (
            locked.provision_claimed_at is not None
            and locked.provision_claimed_at
            > timezone.now() - timedelta(seconds=PROVISION_CLAIM_TTL_SECONDS)
        )
        if claim_is_live:
            return locked
    elif locked.lifecycle_status != LensSessionLink.LifecycleStatus.FAILED:
        raise ValidationError({"lifecycle_status": "Session is not retryable."})
    if locked.cleanup_status in {
        LensSessionLink.CleanupStatus.PENDING,
        LensSessionLink.CleanupStatus.RUNNING,
        LensSessionLink.CleanupStatus.BLOCKED,
    }:
        raise ValidationError(
            {
                "lifecycle_status": (
                    "Chat recovery must finish before preparation can be retried."
                )
            }
        )

    gateway_link = locked.gateway_link or (
        locked.chat_binding.gateway_link if locked.chat_binding_id else None
    )
    if gateway_link is not None:
        # Backfill the canonical binding for sessions created by an older
        # release. Invalid historical rows without any Gateway keep the prior
        # retry behavior and fail with the existing explicit validation when
        # provisioning runs.
        locked.gateway_link = gateway_link
        _assert_retry_public_gateway_capacity(session=locked)
        gateway_chat_queue.assert_chat_queue_admission(
            gateway_link=gateway_link,
        )

    locked.lifecycle_status = LensSessionLink.LifecycleStatus.PROVISIONING
    locked.provision_phase = LensSessionLink.ProvisionPhase.QUEUED
    locked.provision_detail = "Chat creation is queued."
    locked.lifecycle_error = ""
    locked.lifecycle_error_state_json = {}
    locked.provision_claim_token = None
    locked.provision_claimed_at = None
    locked.provision_next_retry_at = None
    locked.gateway_queue_entered_at = timezone.now()
    locked.provision_generation += 1
    locked.provision_poll_sequence = 0
    locked.cleanup_intent = LensSessionLink.CleanupIntent.NONE
    locked.cleanup_status = LensSessionLink.CleanupStatus.NONE
    locked.teardown_attempts = 0
    locked.teardown_claim_token = None
    locked.teardown_claimed_at = None
    locked.teardown_next_retry_at = None
    locked.teardown_state_json = {}
    provision_state = dict(locked.provision_state_json or {})
    provision_state.pop(_SCOPE_TASK_STATE_KEY, None)
    provision_state.pop("source_lens_transient", None)
    locked.provision_state_json = provision_state
    if locked.knowledge_source_id is None:
        locked.capacity_reservation_status = (
            LensSessionLink.CapacityReservationStatus.PENDING
        )
    locked.save(
        update_fields=[
            "lifecycle_status",
            "provision_phase",
            "provision_detail",
            "lifecycle_error",
            "lifecycle_error_state_json",
            "provision_claim_token",
            "provision_claimed_at",
            "provision_next_retry_at",
            "gateway_link",
            "gateway_queue_entered_at",
            "provision_generation",
            "provision_poll_sequence",
            "cleanup_intent",
            "cleanup_status",
            "teardown_attempts",
            "teardown_claim_token",
            "teardown_claimed_at",
            "teardown_next_retry_at",
            "teardown_state_json",
            "provision_state_json",
            "capacity_reservation_status",
            "updated_at",
        ]
    )
    transaction.on_commit(lambda: _queue_provision_or_mark_failed(locked.id))
    return locked


def _queue_provision_or_mark_failed(
    session_link_id: int,
) -> None:
    from apps.lens_bridge.services.sync_queue import queue_copilot_chat_provision

    try:
        tokens = (
            LensSessionLink.objects.filter(
                pk=session_link_id,
                lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            )
            .values_list("provision_generation", "provision_poll_sequence")
            .first()
        )
        if tokens is None:
            return
        generation, poll_sequence = tokens
        queue_copilot_chat_provision(
            session_link_id=session_link_id,
            expected_generation=generation,
            expected_poll_sequence=poll_sequence,
        )
    except Exception as exc:
        logger.exception(
            "copilot chat provision dispatch failed session_link_id=%s", session_link_id
        )
        error_state = lifecycle_error_state_from_exception(exc)
        LensSessionLink.objects.filter(
            pk=session_link_id,
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
            provision_claim_token__isnull=True,
        ).update(
            provision_phase=LensSessionLink.ProvisionPhase.QUEUED,
            provision_detail=("Chat preparation is waiting for the worker queue."),
            lifecycle_error=str(exc)[:2000],
            lifecycle_error_state_json=error_state,
            provision_next_retry_at=timezone.now() + timedelta(seconds=60),
            updated_at=timezone.now(),
        )


def _queue_teardown_or_record_error(session_link_id: int) -> None:
    from apps.lens_bridge.services.sync_queue import queue_copilot_chat_teardown

    try:
        queue_copilot_chat_teardown(session_link_id=session_link_id)
    except Exception as exc:
        logger.exception(
            "copilot chat teardown dispatch failed session_link_id=%s", session_link_id
        )
        error_state = lifecycle_error_state_from_exception(exc)
        cleanup_query = LensSessionLink.objects.filter(
            pk=session_link_id,
            cleanup_status__in=(
                LensSessionLink.CleanupStatus.PENDING,
                LensSessionLink.CleanupStatus.RUNNING,
                LensSessionLink.CleanupStatus.BLOCKED,
            ),
        )
        cleanup_intent = cleanup_query.values_list(
            "cleanup_intent", flat=True
        ).first()
        cleanup_query.update(
            lifecycle_error=("Teardown queue unavailable: " + str(exc))[:2000],
            lifecycle_error_state_json=error_state,
            provision_detail=(
                "Recovery cleanup is waiting for the worker queue."
                if cleanup_intent
                == LensSessionLink.CleanupIntent.RESET_FOR_RETRY
                else "Deletion is waiting for the worker queue."
            ),
            updated_at=timezone.now(),
        )


def _cleanup_failed_provision(
    link: LensSessionLink,
    claim_token: str,
) -> list[str]:
    """Best-effort compensation before a failed chat can be retried.

    The identifiers are retained when a remote deletion fails, allowing a
    retry to resume rather than create another orphaned SourceLens resource.
    """
    _set_phase(
        link,
        claim_token,
        LensSessionLink.ProvisionPhase.CLEANING_UP,
        "Cleaning up incomplete chat resources.",
    )
    link.refresh_from_db()
    errors: list[str] = []
    for kind in (_ASSISTANT_CREATE_OPERATION, _SESSION_CREATE_OPERATION):
        operation = dict((link.provision_state_json or {}).get(kind) or {})
        if (
            operation
            and not operation.get("remote_uuid")
            and operation.get("status") not in {"not_created", "compensated"}
        ):
            errors.append(f"{kind}: remote create outcome is unknown")
    journal_assistant_uuid = _operation_remote_uuid(
        link,
        _ASSISTANT_CREATE_OPERATION,
    )
    journal_session_uuid = _operation_remote_uuid(
        link,
        _SESSION_CREATE_OPERATION,
    )
    if journal_assistant_uuid and journal_assistant_uuid != link.sl_assistant_uuid:
        errors.append("assistant_create: journaled resource requires teardown")
    if journal_session_uuid and journal_session_uuid != link.sl_session_uuid:
        errors.append("session_create: journaled resource requires teardown")
    if _late_remote_uuids(link, "assistant") or _late_remote_uuids(link, "session"):
        errors.append("late_resources: durable teardown is required")
    if errors:
        _update_provision_claim(
            link,
            claim_token,
            "provision_state_json",
        )
        return errors
    session_cleanup_complete = True
    if link.sl_session_uuid:
        session_uuid = link.sl_session_uuid
        try:
            sl_client.request_json(
                "DELETE",
                f"/api/lens/sessions/{session_uuid}/",
                hfl_user=link.hfl_user,
            )
            link.sl_session_uuid = None
        except Exception as exc:
            if _source_lens_not_found(exc):
                link.sl_session_uuid = None
                if (
                    _operation_remote_uuid(link, _SESSION_CREATE_OPERATION)
                    == session_uuid
                ):
                    _set_operation_status(
                        link,
                        _SESSION_CREATE_OPERATION,
                        status="compensated",
                    )
            else:
                errors.append(f"delete_session: {exc}")
                session_cleanup_complete = False
        else:
            if _operation_remote_uuid(link, _SESSION_CREATE_OPERATION) == session_uuid:
                _set_operation_status(
                    link,
                    _SESSION_CREATE_OPERATION,
                    status="compensated",
                )
    assistant_cleanup_complete = session_cleanup_complete
    if session_cleanup_complete and link.sl_assistant_uuid:
        assistant_uuid = link.sl_assistant_uuid
        try:
            from apps.lens_bridge.services.assistants import _delete_sl_assistant

            _delete_sl_assistant(assistant_uuid)
            assistant_access.soft_delete_assistant_link(
                link.organization, assistant_uuid
            )
            link.sl_assistant_uuid = None
            if (
                _operation_remote_uuid(link, _ASSISTANT_CREATE_OPERATION)
                == assistant_uuid
            ):
                _set_operation_status(
                    link,
                    _ASSISTANT_CREATE_OPERATION,
                    status="compensated",
                )
            ks = link.knowledge_source
            if ks is not None and ks.sl_assistant_uuid == assistant_uuid:
                LensKnowledgeSource.objects.filter(
                    pk=ks.id,
                    sl_assistant_uuid=assistant_uuid,
                ).update(sl_assistant_uuid=None, updated_at=timezone.now())
                ks.sl_assistant_uuid = None
        except Exception as exc:
            errors.append(f"delete_assistant: {exc}")
            assistant_cleanup_complete = False
    if (
        link.knowledge_source_id
        and session_cleanup_complete
        and assistant_cleanup_complete
    ):
        try:
            ks = link.knowledge_source
            if ks is not None:
                from apps.lens_bridge.services.knowledge_source_teardown import (
                    run_knowledge_source_teardown,
                )

                result = run_knowledge_source_teardown(
                    knowledge_source_id=ks.id,
                    owner_session_link_id=link.id,
                )
                if result.get("status") != "deleted":
                    raise RuntimeError(
                        "Knowledge Source teardown is " + str(result.get("status"))
                    )
            link.knowledge_source = None
        except Exception as exc:
            errors.append(f"delete_knowledge_source: {exc}")
    _update_provision_claim(
        link,
        claim_token,
        "sl_session_uuid",
        "sl_assistant_uuid",
        "knowledge_source",
        "provision_state_json",
    )
    if errors:
        logger.warning(
            "partial Copilot cleanup session_link_id=%s errors=%s", link.id, errors
        )
    return errors
