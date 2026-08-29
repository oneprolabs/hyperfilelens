"""Durable asynchronous teardown for HFL Knowledge Sources."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.lens_bridge.models import (
    LensKnowledgeSource,
    LensSessionLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import (
    assistant_access,
    managed_datasource,
    sl_client,
    teardown_blocking,
)
from apps.lens_bridge.services.teardown_claims import (
    TEARDOWN_CLAIM_TTL_SECONDS,
    next_retry_at,
)


class KnowledgeSourceTeardownIncompleteError(RuntimeError):
    """Raised after durable failure state is stored for Celery retry."""


class KnowledgeSourceTeardownBusyError(RuntimeError):
    """Raised when another worker owns the Knowledge Source lease."""


logger = logging.getLogger(__name__)


def _session_links_blocking_ks_teardown(
    knowledge_source: LensKnowledgeSource,
    *,
    owner_session_link_id: int | None = None,
):
    """Return chats that still own this KS and must be deleted first.

    Chats already in ``deleting``/``deleted`` are not blockers: they are tearing
    the KS down (or finished). Treating ``deleting`` as active caused a deadlock
    when Chat teardown and standalone KS teardown raced — KS waited for Chat to
    become ``deleted``, while Chat waited for KS teardown to finish.
    """

    blockers = knowledge_source.session_links.exclude(
        lifecycle_status__in=(
            LensSessionLink.LifecycleStatus.DELETED,
            LensSessionLink.LifecycleStatus.DELETING,
        )
    )
    if owner_session_link_id is not None:
        blockers = blockers.exclude(pk=owner_session_link_id)
    return blockers


def _claimed_update(
    knowledge_source_id: int,
    claim_token: str,
    **values: Any,
) -> None:
    """Persist state only while this worker still owns the lease."""

    values["updated_at"] = timezone.now()
    updated = LensKnowledgeSource.all_objects.filter(
        pk=knowledge_source_id,
        teardown_claim_token=claim_token,
        lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
    ).update(**values)
    if updated != 1:
        raise KnowledgeSourceTeardownBusyError(
            "Knowledge Source teardown lease was lost."
        )


def _queue_teardown(knowledge_source_id: int) -> None:
    from apps.lens_bridge.services.sync_queue import queue_knowledge_source_teardown

    try:
        queue_knowledge_source_teardown(knowledge_source_id=knowledge_source_id)
    except Exception as exc:
        LensKnowledgeSource.all_objects.filter(pk=knowledge_source_id).update(
            status_detail=(
                "Deletion is waiting for the worker queue: " + str(exc)
            )[:300],
            updated_at=timezone.now(),
        )


@transaction.atomic
def request_knowledge_source_teardown(
    knowledge_source: LensKnowledgeSource,
) -> LensKnowledgeSource:
    """Persist deletion intent and enqueue it after the transaction commits."""

    locked = LensKnowledgeSource.objects.select_for_update().get(pk=knowledge_source.pk)
    if _session_links_blocking_ks_teardown(locked).exists():
        raise ValidationError(
            {"knowledge_source": "Delete the owning Chat before deleting this knowledge source."}
        )
    if locked.lifecycle_status == LensKnowledgeSource.LifecycleStatus.DELETED:
        return locked
    locked.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETING
    locked.status_detail = "Knowledge source deletion is queued."
    locked.save(update_fields=["lifecycle_status", "status_detail", "updated_at"])
    transaction.on_commit(lambda: _queue_teardown(locked.id))
    return locked


def _claim(knowledge_source_id: int) -> tuple[str | None, str]:
    now = timezone.now()
    with transaction.atomic():
        knowledge_source = (
            LensKnowledgeSource.all_objects.select_for_update()
            .filter(pk=knowledge_source_id)
            .first()
        )
        if knowledge_source is None:
            return None, "missing"
        if knowledge_source.lifecycle_status == LensKnowledgeSource.LifecycleStatus.DELETED:
            return None, "deleted"
        if teardown_blocking.intervention_required(
            knowledge_source.teardown_state_json
        ):
            return None, "intervention_required"
        if (
            knowledge_source.teardown_claimed_at
            and knowledge_source.teardown_claimed_at
            > now - timedelta(seconds=TEARDOWN_CLAIM_TTL_SECONDS)
        ):
            return None, "busy"
        if (
            knowledge_source.teardown_next_retry_at
            and knowledge_source.teardown_next_retry_at > now
        ):
            return None, "scheduled"
        token = uuid.uuid4()
        knowledge_source.lifecycle_status = LensKnowledgeSource.LifecycleStatus.DELETING
        knowledge_source.teardown_attempts += 1
        knowledge_source.teardown_claim_token = token
        knowledge_source.teardown_claimed_at = now
        knowledge_source.teardown_next_retry_at = next_retry_at(
            knowledge_source.teardown_attempts
        )
        knowledge_source.save(
            update_fields=[
                "lifecycle_status",
                "teardown_attempts",
                "teardown_claim_token",
                "teardown_claimed_at",
                "teardown_next_retry_at",
                "updated_at",
            ]
        )
    return str(token), "claimed"


def _renew(knowledge_source_id: int, claim_token: str) -> None:
    _claimed_update(
        knowledge_source_id,
        claim_token,
        teardown_claimed_at=timezone.now(),
    )


def _save_step(
    knowledge_source_id: int,
    claim_token: str,
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
    _claimed_update(
        knowledge_source_id,
        claim_token,
        teardown_state_json=state,
    )


def cleanup_knowledge_source_workspace(
    knowledge_source: LensKnowledgeSource,
    *,
    claim_token: str,
) -> None:
    """Identity-check managed data cleanup; detach gateway-local directories."""

    sync_state = dict(knowledge_source.sync_state_json or {})
    sync_state["teardown_requested_at"] = timezone.now().isoformat()
    try:
        workspace_binding = knowledge_source.workspace_binding
    except LensWorkspaceBinding.DoesNotExist as exc:
        raise ValidationError(
            {"workspace": "Knowledge source has no workspace binding."}
        ) from exc
    sync_state["teardown_workspace_path"] = workspace_binding.resolved_path()
    if workspace_binding.workspace_kind == LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE:
        from apps.lens_bridge.services.gateway_execution import (
            context_for_workspace_binding,
            workspace_identity_payload,
        )
        from apps.node.services.internal.agent_task import run_agent_task_sync

        if workspace_binding.state == LensWorkspaceBinding.State.DELETED:
            return
        workspace_binding.state = LensWorkspaceBinding.State.DELETING
        workspace_binding.last_error = ""
        workspace_binding.save(update_fields=["state", "last_error", "updated_at"])
        execution, workspace_binding = context_for_workspace_binding(
            tenant_organization=knowledge_source.organization,
            workspace_binding_id=workspace_binding.id,
            require_ready=False,
            allow_deleting=True,
        )
        _renew(knowledge_source.id, claim_token)
        outcome = run_agent_task_sync(
            org=execution.execution_organization,
            node_id=execution.gateway.id,
            kind="lens.ks.cleanup",
            payload={
                "path": workspace_binding.resolved_path(),
                **workspace_identity_payload(workspace_binding),
            },
            correlation_type="lens_knowledge_source.cleanup",
            correlation_id=str(knowledge_source.id),
            requesting_organization_id=knowledge_source.organization_id,
            wait_timeout_seconds=300,
        )
        _renew(knowledge_source.id, claim_token)
        sync_state["teardown_node_task_id"] = str(outcome.task.id)
        _claimed_update(
            knowledge_source.id,
            claim_token,
            sync_state_json=sync_state,
        )
        if not outcome.ok:
            detail = outcome.task.last_error or (
                "Workspace cleanup timed out."
                if outcome.timed_out
                else "Workspace cleanup failed."
            )
            workspace_binding.last_error = detail
            workspace_binding.save(update_fields=["last_error", "updated_at"])
            raise ValidationError({"workspace": detail})
    workspace_binding.state = LensWorkspaceBinding.State.DELETED
    workspace_binding.last_error = ""
    workspace_binding.save(update_fields=["state", "last_error", "updated_at"])
    _claimed_update(
        knowledge_source.id,
        claim_token,
        sync_state_json=sync_state,
    )


def run_knowledge_source_teardown(
    *,
    knowledge_source_id: int,
    owner_session_link_id: int | None = None,
) -> dict[str, Any]:
    """Execute idempotent Assistant-before-Workspace teardown under a lease."""

    claim_token, claim_status = _claim(knowledge_source_id)
    if claim_token is None:
        return {"knowledge_source_id": knowledge_source_id, "status": claim_status}
    knowledge_source = (
        LensKnowledgeSource.all_objects.select_related(
            "organization", "workspace_binding", "gateway", "gateway_link"
        )
        .filter(pk=knowledge_source_id)
        .first()
    )
    if knowledge_source is None:
        return {"knowledge_source_id": knowledge_source_id, "status": "missing"}
    state = dict(knowledge_source.teardown_state_json or {})
    stop_assessment: managed_datasource.ConversionStopAssessment | None = None
    blocking_step = "validate_gateway_workload"
    try:
        from apps.node.services.internal.node_workload import (
            get_node_workload_blockers,
        )

        if get_node_workload_blockers(node=knowledge_source.gateway):
            raise ValidationError(
                {"knowledge_source": "Data gateway restore work is still stopping."}
            )
        blocking_step = "validate_chat_ownership"
        if _session_links_blocking_ks_teardown(
            knowledge_source,
            owner_session_link_id=owner_session_link_id,
        ).exists():
            raise ValidationError(
                {"knowledge_source": "Knowledge source still belongs to an active Chat."}
            )

        if knowledge_source.sl_datasource_uuid:
            blocking_step = "cancel_conversion"
            _renew(knowledge_source.id, claim_token)
            sl_client.cancel_managed_datasource_conversion(
                str(knowledge_source.sl_datasource_uuid)
            )
            stop_assessment = managed_datasource.assess_conversion_stop(
                knowledge_source
            )
            if not stop_assessment.confirmed:
                _save_step(
                    knowledge_source.id,
                    claim_token,
                    state,
                    "cancel_conversion",
                    status="waiting",
                )
                raise KnowledgeSourceTeardownIncompleteError(
                    "Waiting for LensNode to stop document conversion."
                )
        _save_step(
            knowledge_source.id,
            claim_token,
            state,
            "cancel_conversion",
            status="success",
        )

        from apps.lens_bridge.services.assistants import _delete_sl_assistant

        blocking_step = "delete_assistants"
        assistant_uuids = {
            link.sl_assistant_uuid
            for link in knowledge_source.assistant_links.filter(is_deleted=False).only(
                "sl_assistant_uuid"
            )
        }
        if knowledge_source.sl_assistant_uuid:
            assistant_uuids.add(knowledge_source.sl_assistant_uuid)
        for assistant_uuid in sorted(assistant_uuids, key=str):
            _renew(knowledge_source.id, claim_token)
            _delete_sl_assistant(assistant_uuid)
            assistant_access.soft_delete_assistant_link(
                knowledge_source.organization, assistant_uuid
            )
        _claimed_update(
            knowledge_source.id,
            claim_token,
            sl_assistant_uuid=None,
        )
        _save_step(
            knowledge_source.id,
            claim_token,
            state,
            "delete_assistants",
            status="success",
        )

        if knowledge_source.sl_datasource_uuid:
            blocking_step = "delete_datasource"
            _renew(knowledge_source.id, claim_token)
            sl_client.delete_managed_datasource(
                str(knowledge_source.sl_datasource_uuid)
            )
        _claimed_update(
            knowledge_source.id,
            claim_token,
            sl_datasource_uuid=None,
        )
        _save_step(
            knowledge_source.id,
            claim_token,
            state,
            "delete_datasource",
            status="success",
        )

        blocking_step = "cleanup_workspace"
        cleanup_knowledge_source_workspace(
            knowledge_source,
            claim_token=claim_token,
        )
        _save_step(
            knowledge_source.id,
            claim_token,
            state,
            "cleanup_workspace",
            status="success",
        )
        state = teardown_blocking.clear_blocking(state)
        now = timezone.now()
        updated = LensKnowledgeSource.all_objects.filter(
            pk=knowledge_source.id,
            teardown_claim_token=claim_token,
        ).update(
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETED,
            is_deleted=True,
            deleted_at=now,
            status_detail="Knowledge source resources deleted.",
            teardown_claim_token=None,
            teardown_claimed_at=None,
            teardown_next_retry_at=None,
            teardown_state_json=state,
            updated_at=now,
        )
        if updated != 1:
            raise KnowledgeSourceTeardownBusyError(
                "Knowledge Source teardown lease was lost."
            )
        return {"knowledge_source_id": knowledge_source.id, "status": "deleted"}
    except Exception as exc:
        _save_step(
            knowledge_source.id,
            claim_token,
            state,
            "failure",
            status="retry",
            error=str(exc),
        )
        if stop_assessment is not None and not stop_assessment.confirmed:
            blocking_reason = "conversion_stop_unconfirmed"
            task_id = stop_assessment.task_id
            remote_status = stop_assessment.remote_status
            stop_confirmation_source = (
                stop_assessment.stop_confirmation_source
            )
        else:
            # The stage is stable across retries but changes when teardown
            # makes substantive progress. Full exception details remain in
            # the step journal.
            blocking_reason = blocking_step
            task_id = ""
            remote_status = ""
            stop_confirmation_source = ""
        state, blocking = teardown_blocking.record_blocking(
            state,
            reason=blocking_reason,
            task_id=task_id,
            gateway_link_id=knowledge_source.gateway_link_id,
            remote_status=remote_status,
            stop_confirmation_source=stop_confirmation_source,
        )
        requires_intervention = bool(blocking["intervention_required"])
        retry_at = (
            None
            if requires_intervention
            else next_retry_at(int(blocking["consecutive_attempts"]))
        )
        LensKnowledgeSource.all_objects.filter(
            pk=knowledge_source.id,
            teardown_claim_token=claim_token,
        ).update(
            status_detail=(
                "Knowledge source cleanup requires operator intervention."
                if requires_intervention
                else "Knowledge source cleanup is incomplete and will be retried."
            ),
            teardown_claim_token=None,
            teardown_claimed_at=None,
            teardown_next_retry_at=retry_at,
            teardown_state_json=state,
            updated_at=timezone.now(),
        )
        logger.warning(
            "knowledge source teardown blocked ks_id=%s gateway_link_id=%s "
            "task_id=%s remote_status=%s reason=%s attempts=%s "
            "intervention_required=%s",
            knowledge_source.id,
            knowledge_source.gateway_link_id,
            task_id,
            remote_status,
            blocking_reason,
            blocking["consecutive_attempts"],
            requires_intervention,
        )
        raise KnowledgeSourceTeardownIncompleteError(str(exc)) from exc
