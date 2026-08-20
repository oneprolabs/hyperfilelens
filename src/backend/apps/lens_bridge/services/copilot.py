"""AI Copilot helpers — assistant-centric sessions."""

from __future__ import annotations

import uuid as uuid_lib
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Membership, Organization
from apps.lens_bridge.models import (
    LensChatBinding,
    LensKnowledgeSource,
    LensRunSubmission,
    LensSessionLink,
)
from apps.lens_bridge.services import assistant_access, chat_user_provisioning, org_models, provisioning, sl_client
from apps.lens_bridge.services.assistants import get_org_assistant, list_org_assistants


def _ks_by_assistant_uuid(org: Organization) -> dict[str, LensKnowledgeSource]:
    return {
        str(ks.sl_assistant_uuid): ks
        for ks in LensKnowledgeSource.objects.filter(
            organization=org,
            sl_assistant_uuid__isnull=False,
        ).select_related("gateway")
    }


def _ks_by_id(org: Organization) -> dict[int, LensKnowledgeSource]:
    return {
        ks.id: ks
        for ks in LensKnowledgeSource.objects.filter(organization=org).select_related("gateway")
    }


def _resolve_knowledge_source(
    row: dict[str, Any],
    *,
    ks_by_uuid: dict[str, LensKnowledgeSource],
    ks_by_id: dict[int, LensKnowledgeSource],
) -> LensKnowledgeSource | None:
    uuid_str = str(row.get("uuid") or "")
    ks = ks_by_uuid.get(uuid_str)
    if ks is not None:
        return ks
    ks_id = row.get("knowledge_source_id")
    if ks_id in (None, ""):
        return None
    try:
        return ks_by_id.get(int(ks_id))
    except (TypeError, ValueError):
        return None


def _merge_knowledge_source(row: dict[str, Any], ks: LensKnowledgeSource) -> dict[str, Any]:
    merged = dict(row)
    merged["knowledge_source_id"] = ks.id
    merged["knowledge_source_name"] = ks.name
    merged["knowledge_source_status"] = ks.status
    if ks.gateway_id:
        merged["gateway_name"] = ks.gateway.name
    if ks.source_path:
        merged["source_path"] = ks.source_path
    return merged


def list_copilot_assistants(
    org: Organization,
    *,
    user: AbstractBaseUser,
    membership: Membership | None = None,
) -> list[dict[str, Any]]:
    rows = list_org_assistants(
        org,
        user=user,
        can_manage_all=False,
    )
    ks_by_uuid = _ks_by_assistant_uuid(org)
    ks_by_id = _ks_by_id(org)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "active":
            continue
        ks = _resolve_knowledge_source(row, ks_by_uuid=ks_by_uuid, ks_by_id=ks_by_id)
        if ks is not None:
            row = _merge_knowledge_source(row, ks)
        enriched.append(row)
    return enriched


def _default_session_title(assistant_name: str | None) -> str:
    name = (assistant_name or "Chat").strip() or "Chat"
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    return f"{name} · {stamp}"


def create_copilot_session(
    org: Organization,
    *,
    user: AbstractBaseUser,
    assistant_uuid: uuid_lib.UUID,
    title: str | None = None,
    chat_binding: LensChatBinding | None = None,
) -> LensSessionLink:
    chat_user_provisioning.ensure_sl_chat_user(user)
    assistant = get_org_assistant(
        org,
        assistant_uuid,
        user=user,
        can_manage_all=False,
    )
    if assistant.get("status") != "active":
        raise ValidationError({"assistant_uuid": "Assistant is not active."})

    ks = (
        LensKnowledgeSource.objects.filter(
            organization=org,
            sl_assistant_uuid=assistant_uuid,
        )
        .select_related("gateway")
        .first()
    )
    if ks is None:
        ks_id = assistant.get("knowledge_source_id")
        if ks_id:
            ks = (
                LensKnowledgeSource.objects.filter(organization=org, pk=ks_id)
                .select_related("gateway")
                .first()
            )
    if ks is not None:
        if not ks.scan_enabled:
            raise ValidationError({"assistant_uuid": "Knowledge source is paused."})
        if ks.status not in (
            LensKnowledgeSource.Status.READY,
            LensKnowledgeSource.Status.DEGRADED,
        ):
            raise ValidationError({"assistant_uuid": "Knowledge source sync is not complete."})

    link = assistant_access.get_assistant_link(org, assistant_uuid)
    if link is None:
        link = assistant_access.ensure_assistant_link(
            org=org,
            sl_assistant_uuid=assistant_uuid,
            knowledge_source=ks,
        )
    if not assistant_access.assistant_visible_to(
        user=user,
        link=link,
        can_manage_all=False,
    ):
        raise NotFound("Assistant not found.")

    model_ref_raw = assistant.get("agent_model_ref")
    multimodal_ref_raw = assistant.get("multimodal_model_ref")
    model_ref: uuid_lib.UUID | None = None
    multimodal_ref: uuid_lib.UUID | None = None
    if model_ref_raw:
        model_ref = uuid_lib.UUID(str(model_ref_raw))
        org_models.validate_default_model_ref(org, model_ref)
        if ks is not None:
            provisioning.sync_assistant_agent_model(
                ks=ks,
                model_ref=model_ref,
                assistant_uuid=assistant_uuid,
            )
    if multimodal_ref_raw:
        multimodal_ref = uuid_lib.UUID(str(multimodal_ref_raw))
        org_models.validate_default_model_ref(
            org,
            multimodal_ref,
            field_name="multimodal_model_ref",
        )

    session_title = (title or "").strip() or _default_session_title(str(assistant.get("name") or ""))
    sl_session = sl_client.request_json(
        "POST",
        "/api/lens/sessions/",
        json_body={
            "assistant_uuid": str(assistant_uuid),
            "title": session_title,
        },
        hfl_user=user,
    )
    return LensSessionLink.objects.create(
        organization=org,
        hfl_user=user,
        knowledge_source=ks,
        sl_session_uuid=sl_session["uuid"],
        sl_assistant_uuid=assistant_uuid,
        agent_model_ref=model_ref,
        multimodal_model_ref=multimodal_ref,
        title=session_title,
        chat_binding=chat_binding,
    )


ACTIVE_RUN_STATUSES = frozenset({"queued", "running", "streaming"})
TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "cancelled"})


def set_active_run(link: LensSessionLink, *, run_uuid: uuid_lib.UUID, status: str) -> None:
    link.active_run_uuid = run_uuid
    link.active_run_status = status
    link.save(update_fields=["active_run_uuid", "active_run_status", "updated_at"])


def clear_active_run(link: LensSessionLink) -> None:
    if link.active_run_uuid is None and not link.active_run_status:
        return
    link.active_run_uuid = None
    link.active_run_status = ""
    link.save(update_fields=["active_run_uuid", "active_run_status", "updated_at"])


def _fetch_sl_run(run_uuid: uuid_lib.UUID, *, user: AbstractBaseUser) -> dict[str, Any]:
    return sl_client.request_json("GET", f"/api/lens/runs/{run_uuid}/", hfl_user=user)


def _fetch_session_messages(link: LensSessionLink) -> list[dict[str, Any]]:
    data = sl_client.request_json(
        "GET",
        f"/api/lens/sessions/{link.sl_session_uuid}/messages/",
        hfl_user=link.hfl_user,
    )
    return data if isinstance(data, list) else []


def _update_last_assistant_message_at(
    link: LensSessionLink,
    messages: list[dict[str, Any]],
) -> None:
    latest = None
    for row in messages:
        if row.get("role") != "assistant":
            continue
        raw_created_at = row.get("created_at")
        created_at = (
            raw_created_at
            if hasattr(raw_created_at, "tzinfo")
            else parse_datetime(str(raw_created_at or ""))
        )
        if created_at is None:
            continue
        if timezone.is_naive(created_at):
            created_at = timezone.make_aware(created_at)
        if latest is None or created_at > latest:
            latest = created_at
    if latest is not None and (
        link.last_assistant_message_at is None or latest > link.last_assistant_message_at
    ):
        link.last_assistant_message_at = latest
        link.save(update_fields=["last_assistant_message_at", "updated_at"])


def assistant_message_for_run(
    messages: list[dict[str, Any]],
    run_uuid: str,
) -> dict[str, Any] | None:
    for row in messages:
        if str(row.get("run") or "") == run_uuid and row.get("role") == "assistant":
            return row
    return None


def require_assistant_run(
    link: LensSessionLink,
    run_uuid: uuid_lib.UUID,
) -> dict[str, Any]:
    """Require a completed answer Run to belong to this HFL chat."""

    messages = _fetch_session_messages(link)
    message = assistant_message_for_run(messages, str(run_uuid))
    if message is None:
        raise NotFound("Answer not found.")
    return message


def update_run_feedback(
    link: LensSessionLink,
    run_uuid: uuid_lib.UUID,
    feedback: str,
) -> dict[str, str | None]:
    """Persist one session answer's feedback through SourceLens.

    The session message lookup keeps HFL organization scoping authoritative;
    SourceLens remains authoritative for the feedback value itself.
    """

    require_assistant_run(link, run_uuid)

    data = sl_client.request_json(
        "PATCH",
        f"/api/lens/runs/{run_uuid}/feedback/",
        json_body={"feedback": feedback},
        hfl_user=link.hfl_user,
    )
    if not isinstance(data, dict) or data.get("feedback") != feedback:
        raise sl_client.LensBridgeError(
            "SourceLens returned invalid run feedback."
        )
    updated_at = data.get("feedback_updated_at")
    if updated_at is not None and not isinstance(updated_at, str):
        raise sl_client.LensBridgeError(
            "SourceLens returned invalid run feedback metadata."
        )
    return {
        "feedback": feedback,
        "feedback_updated_at": updated_at,
    }


def _build_active_run_payload(
    run: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    bound_run_uuid: uuid_lib.UUID | None = None,
) -> dict[str, Any]:
    run_uuid = str(bound_run_uuid or run.get("uuid") or "")
    submission_started_at = (
        LensRunSubmission.objects.filter(sl_run_uuid=run_uuid)
        .values_list("created_at", flat=True)
        .first()
    )
    assistant_msg = assistant_message_for_run(messages, run_uuid)
    thinking = {}
    if assistant_msg:
        thinking = assistant_msg.get("thinking") or {}
    return {
        "uuid": run_uuid,
        "status": run.get("status") or "",
        "partial_content": (assistant_msg or {}).get("content") or "",
        "thinking": thinking.get("steps") or [],
        "error": run.get("error") or "",
        "started_at": run.get("started_at"),
        "elapsed_anchor_at": (
            submission_started_at
            or run.get("created_at")
            or run.get("started_at")
        ),
    }


def _pending_response_state(link: LensSessionLink) -> dict[str, Any] | None:
    """Return the user-visible state for a durable submission awaiting a Run."""

    submission = (
        LensRunSubmission.objects.filter(
            session_link=link,
            status=LensRunSubmission.Status.PENDING,
        )
        .only("created_at", "question")
        .first()
    )
    if submission is None:
        return None
    return {
        "status": "submitting",
        "started_at": submission.created_at,
        "question": submission.question,
    }


def resolve_active_run(link: LensSessionLink) -> dict[str, Any] | None:
    """Return SL run dict when link has a non-terminal active run, else None."""
    if link.active_run_uuid is None:
        return None
    try:
        run = _fetch_sl_run(link.active_run_uuid, user=link.hfl_user)
    except sl_client.LensBridgeError as exc:
        if exc.status_code == 404:
            clear_active_run(link)
            return None
        raise
    status = str(run.get("status") or "")
    if status:
        LensRunSubmission.objects.filter(sl_run_uuid=link.active_run_uuid).update(
            run_status=status,
            updated_at=timezone.now(),
        )
    if status in TERMINAL_RUN_STATUSES:
        from apps.lens_bridge.services import usage

        usage.capture_run_usage(link, run)
        clear_active_run(link)
        return None
    if status and status != link.active_run_status:
        link.active_run_status = status
        link.save(update_fields=["active_run_status", "updated_at"])
    return run


def get_active_run_payload(link: LensSessionLink) -> dict[str, Any] | None:
    run = resolve_active_run(link)
    if run is None:
        return None
    messages = _fetch_session_messages(link)
    return _build_active_run_payload(
        run,
        messages,
        bound_run_uuid=link.active_run_uuid,
    )


def sync_copilot_session(link: LensSessionLink) -> dict[str, Any]:
    from apps.lens_bridge.services import usage

    messages = _fetch_session_messages(link)
    _update_last_assistant_message_at(link, messages)
    active_run = None
    run = resolve_active_run(link)
    if run is not None:
        active_run = _build_active_run_payload(
            run,
            messages,
            bound_run_uuid=link.active_run_uuid,
        )
        response_state = {
            "status": "running",
            "started_at": active_run["elapsed_anchor_at"],
        }
    else:
        response_state = _pending_response_state(link) or {
            "status": "idle",
            "started_at": None,
        }
    return {
        "session_id": link.id,
        "messages": messages,
        "active_run": active_run,
        "response_state": response_state,
        "run_outcomes": usage.run_outcomes_for_messages(link, messages),
    }


def cancel_copilot_run(link: LensSessionLink, run_uuid: uuid_lib.UUID) -> dict[str, Any]:
    if link.active_run_uuid and link.active_run_uuid != run_uuid:
        raise ValidationError({"run_uuid": "Run does not match the active session run."})
    data = sl_client.request_json(
        "POST",
        f"/api/lens/runs/{run_uuid}/cancel/",
        hfl_user=link.hfl_user,
    )
    from apps.lens_bridge.services import usage

    usage.capture_run_usage(link, data)
    LensRunSubmission.objects.filter(sl_run_uuid=run_uuid).update(
        run_status=str(data.get("status") or "cancelled"),
        updated_at=timezone.now(),
    )
    clear_active_run(link)
    return data
