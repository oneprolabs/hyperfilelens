"""Resume blocked Chat or orphan KS cleanup after operator confirmation."""

from __future__ import annotations

import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.lens_bridge.models import LensKnowledgeSource, LensSessionLink
from apps.lens_bridge.services import sl_client, teardown_blocking


def _conversion_blocked(blocking: dict[str, object]) -> bool:
    reason = str(blocking.get("reason") or "")
    return bool(
        reason == "conversion_stop_unconfirmed"
        or (
            str(blocking.get("task_id") or "").strip()
            and not _restore_blocked(blocking)
        )
    )


def _restore_blocked(blocking: dict[str, object]) -> bool:
    return str(blocking.get("reason") or "") in {
        "restore_executor_still_stopping",
        "restore_dispatch_still_stopping",
        "restore_node_task_missing",
        "restore_node_task_identity_mismatch",
        "restore_task_missing",
    }


def _require_matching_blocked_task(
    blocking: dict[str, object],
    task_id: str,
) -> None:
    blocked_task_id = str(blocking.get("task_id") or "").strip()
    if blocked_task_id and blocked_task_id != task_id:
        raise CommandError("Task id does not match the recorded blocking condition.")


def _record_cleanup_confirmation(
    teardown_state: dict,
    *,
    confirmation_key: str,
    confirmation: dict,
    restore_task_id: str,
) -> None:
    """Record one confirmation while preserving prior restore task evidence."""

    teardown_state[confirmation_key] = confirmation
    if not restore_task_id:
        return
    confirmations = teardown_state.get("manual_restore_stop_confirmations")
    confirmations = dict(confirmations) if isinstance(confirmations, dict) else {}
    confirmations[restore_task_id] = confirmation
    teardown_state["manual_restore_stop_confirmations"] = confirmations


class Command(BaseCommand):
    help = (
        "Resume one blocked Chat or orphan Knowledge Source cleanup after an "
        "operator confirms the recorded blocking condition has been resolved."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--session-id", type=int)
        target.add_argument("--knowledge-source-id", type=int)
        parser.add_argument("--source-lens-task-id")
        parser.add_argument("--restore-task-id")
        parser.add_argument("--reason", required=True)
        parser.add_argument(
            "--confirm-executor-stopped",
            action="store_true",
            help="Required acknowledgement that the old executor no longer runs.",
        )
        parser.add_argument(
            "--confirm-retry",
            action="store_true",
            help=(
                "Required acknowledgement when resuming a non-conversion "
                "cleanup after its underlying fault was corrected."
            ),
        )

    def handle(self, *args, **options):
        task_id = str(options.get("source_lens_task_id") or "").strip()
        restore_task_id = str(options.get("restore_task_id") or "").strip()
        if task_id and restore_task_id:
            raise CommandError(
                "Use either --source-lens-task-id or --restore-task-id, not both."
            )
        reason = str(options["reason"] or "").strip()
        if not reason:
            raise CommandError("Reason is required.")
        if task_id or restore_task_id:
            if not options["confirm_executor_stopped"]:
                raise CommandError(
                    "Refusing to resume cleanup without --confirm-executor-stopped."
                )
        if task_id:
            remote_task = sl_client.get_task_by_id(task_id)
            if remote_task is None:
                raise CommandError(
                    "SourceLens task was not found; cleanup remains blocked."
                )
            remote_status = str(remote_task.get("status") or "").upper()
            returned_task_id = str(remote_task.get("task_id") or "").strip()
            if returned_task_id != task_id:
                raise CommandError(
                    "SourceLens did not return the exact requested task identity."
                )
            if remote_status not in {"FAILURE", "REVOKED"}:
                raise CommandError(
                    f"SourceLens task is {remote_status or 'UNKNOWN'}, not terminal."
                )
        elif restore_task_id:
            remote_status = "OPERATOR_CONFIRMED"
        else:
            if not options.get("confirm_retry"):
                raise CommandError(
                    "Refusing to resume non-conversion cleanup without --confirm-retry."
                )
            remote_status = ""

        knowledge_source_id = options.get("knowledge_source_id")
        if knowledge_source_id is not None:
            if restore_task_id:
                raise CommandError(
                    "Restore executor confirmation must target the owning Chat session."
                )
            self._resume_knowledge_source_cleanup(
                knowledge_source_id=int(knowledge_source_id),
                task_id=task_id,
                remote_status=remote_status,
                reason=reason,
            )
            return

        session_id = options.get("session_id")
        if session_id is None:
            raise CommandError("Chat session id is required.")

        with transaction.atomic():
            session = (
                LensSessionLink.objects.select_for_update()
                .filter(pk=session_id)
                .first()
            )
            if session is None:
                raise CommandError("Chat session was not found.")
            if session.cleanup_status != LensSessionLink.CleanupStatus.BLOCKED:
                raise CommandError("Chat cleanup is not blocked.")
            teardown_state = dict(session.teardown_state_json or {})
            blocking = teardown_state.get("blocking")
            blocking = blocking if isinstance(blocking, dict) else {}
            session_conversion_blocked = _conversion_blocked(blocking)
            session_restore_blocked = _restore_blocked(blocking)
            if not task_id and session_conversion_blocked:
                raise CommandError(
                    "Conversion cleanup requires --source-lens-task-id and "
                    "--confirm-executor-stopped."
                )
            knowledge_source_id = session.knowledge_source_id
            if (task_id or restore_task_id) and knowledge_source_id is None:
                raise CommandError("Blocked Chat has no Knowledge Source.")

            knowledge_source = None
            if knowledge_source_id is not None:
                knowledge_source = (
                    LensKnowledgeSource.all_objects.select_for_update()
                    .filter(pk=knowledge_source_id)
                    .first()
                )
            if (task_id or restore_task_id) and knowledge_source is None:
                raise CommandError("Blocked Chat Knowledge Source was not found.")
            if (
                knowledge_source is not None
                and session.knowledge_source_id != knowledge_source.id
            ):
                raise CommandError(
                    "Blocked Chat Knowledge Source changed during recovery."
                )
            knowledge_source_blocking: dict[str, object] = {}
            if knowledge_source is not None:
                knowledge_source_state = dict(
                    knowledge_source.teardown_state_json or {}
                )
                candidate_blocking = knowledge_source_state.get("blocking")
                knowledge_source_blocking = (
                    candidate_blocking if isinstance(candidate_blocking, dict) else {}
                )
            knowledge_source_conversion_blocked = _conversion_blocked(
                knowledge_source_blocking
            )
            knowledge_source_restore_blocked = _restore_blocked(
                knowledge_source_blocking
            )
            conversion_blocked = (
                session_conversion_blocked or knowledge_source_conversion_blocked
            )
            restore_blocked = (
                session_restore_blocked or knowledge_source_restore_blocked
            )
            if task_id:
                if not conversion_blocked:
                    raise CommandError(
                        "Cleanup is not blocked on SourceLens conversion stop."
                    )
                _require_matching_blocked_task(blocking, task_id)
                _require_matching_blocked_task(
                    knowledge_source_blocking,
                    task_id,
                )
            elif conversion_blocked:
                raise CommandError(
                    "Conversion cleanup requires --source-lens-task-id and "
                    "--confirm-executor-stopped."
                )
            if restore_task_id:
                if not restore_blocked:
                    raise CommandError(
                        "Cleanup is not blocked on a Chat workspace restore."
                    )
                _require_matching_blocked_task(blocking, restore_task_id)
                _require_matching_blocked_task(
                    knowledge_source_blocking,
                    restore_task_id,
                )
            elif restore_blocked:
                raise CommandError(
                    "Restore cleanup requires --restore-task-id and "
                    "--confirm-executor-stopped."
                )

            confirmation = {
                "confirmed": True,
                "task_id": task_id or restore_task_id,
                "remote_status": remote_status,
                "operator": getpass.getuser(),
                "reason": reason[:1000],
                "blocking_reason": str(blocking.get("reason") or ""),
                "confirmed_at": timezone.now().isoformat(),
            }
            if knowledge_source is not None and (
                task_id
                or restore_task_id
                or "blocking" in (knowledge_source.teardown_state_json or {})
            ):
                self._resume_locked_knowledge_source(
                    knowledge_source,
                    task_id=task_id,
                    restore_task_id=restore_task_id,
                    confirmation=confirmation,
                )

            teardown_state.pop("blocking", None)
            confirmation_key = (
                "manual_stop_confirmation"
                if task_id
                else (
                    "manual_restore_stop_confirmation"
                    if restore_task_id
                    else "manual_cleanup_confirmation"
                )
            )
            _record_cleanup_confirmation(
                teardown_state,
                confirmation_key=confirmation_key,
                confirmation=confirmation,
                restore_task_id=restore_task_id,
            )
            session.teardown_state_json = teardown_state
            session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
            session.teardown_attempts = 0
            session.teardown_claim_token = None
            session.teardown_claimed_at = None
            session.teardown_next_retry_at = None
            session.save(
                update_fields=[
                    "teardown_state_json",
                    "cleanup_status",
                    "teardown_attempts",
                    "teardown_claim_token",
                    "teardown_claimed_at",
                    "teardown_next_retry_at",
                    "updated_at",
                ]
            )

            from apps.lens_bridge.services.chat_lifecycle import (
                _queue_teardown_or_record_error,
            )

            transaction.on_commit(lambda: _queue_teardown_or_record_error(session.id))

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued audited cleanup recovery for Chat {session.id}."
            )
        )

    def _resume_knowledge_source_cleanup(
        self,
        *,
        knowledge_source_id: int,
        task_id: str,
        remote_status: str,
        reason: str,
    ) -> None:
        """Resume an orphan KS whose bounded retry budget was exhausted."""

        with transaction.atomic():
            knowledge_source = (
                LensKnowledgeSource.all_objects.select_for_update()
                .filter(pk=knowledge_source_id)
                .first()
            )
            if knowledge_source is None:
                raise CommandError("Knowledge Source was not found.")
            if (
                knowledge_source.lifecycle_status
                != LensKnowledgeSource.LifecycleStatus.DELETING
            ):
                raise CommandError("Knowledge Source cleanup is not pending.")
            if knowledge_source.session_links.exclude(
                lifecycle_status=LensSessionLink.LifecycleStatus.DELETED
            ).exists():
                raise CommandError(
                    "Knowledge Source still belongs to a Chat; resume the Chat "
                    "cleanup by session id."
                )

            teardown_state = dict(knowledge_source.teardown_state_json or {})
            blocking = teardown_state.get("blocking")
            blocking = blocking if isinstance(blocking, dict) else {}
            conversion_blocked = _conversion_blocked(blocking)
            if task_id:
                if not conversion_blocked:
                    raise CommandError(
                        "Knowledge Source is not blocked on conversion stop."
                    )
                _require_matching_blocked_task(blocking, task_id)
            elif conversion_blocked:
                raise CommandError(
                    "Conversion cleanup requires --source-lens-task-id and "
                    "--confirm-executor-stopped."
                )
            elif not teardown_blocking.intervention_required(teardown_state):
                raise CommandError(
                    "Knowledge Source cleanup does not require operator intervention."
                )

            confirmation = {
                "confirmed": True,
                "task_id": task_id,
                "remote_status": remote_status,
                "operator": getpass.getuser(),
                "reason": reason[:1000],
                "blocking_reason": str(blocking.get("reason") or ""),
                "confirmed_at": timezone.now().isoformat(),
            }
            self._resume_locked_knowledge_source(
                knowledge_source,
                task_id=task_id,
                restore_task_id="",
                confirmation=confirmation,
            )

            from apps.lens_bridge.services.knowledge_source_teardown import (
                _queue_teardown,
            )

            transaction.on_commit(lambda: _queue_teardown(knowledge_source.id))

        self.stdout.write(
            self.style.SUCCESS(
                "Queued audited cleanup recovery for Knowledge Source "
                f"{knowledge_source.id}."
            )
        )

    @staticmethod
    def _resume_locked_knowledge_source(
        knowledge_source: LensKnowledgeSource,
        *,
        task_id: str,
        restore_task_id: str,
        confirmation: dict,
    ) -> None:
        """Reset one locked KS after its blocking condition was confirmed."""

        sync_state = dict(knowledge_source.sync_state_json or {})
        conversion = dict(sync_state.get("conversion") or {})
        recorded_task_id = str(conversion.get("task_id") or "").strip()
        if task_id and recorded_task_id != task_id:
            raise CommandError(
                "SourceLens task id does not match the blocked Knowledge Source."
            )
        if task_id:
            conversion["manual_stop_confirmation"] = confirmation
            sync_state["conversion"] = conversion
            knowledge_source.sync_state_json = sync_state

        teardown_state = dict(knowledge_source.teardown_state_json or {})
        teardown_state.pop("blocking", None)
        confirmation_key = (
            "manual_stop_confirmation"
            if task_id
            else (
                "manual_restore_stop_confirmation"
                if restore_task_id
                else "manual_cleanup_confirmation"
            )
        )
        _record_cleanup_confirmation(
            teardown_state,
            confirmation_key=confirmation_key,
            confirmation=confirmation,
            restore_task_id=restore_task_id,
        )
        knowledge_source.teardown_state_json = teardown_state
        knowledge_source.teardown_attempts = 0
        knowledge_source.teardown_claim_token = None
        knowledge_source.teardown_claimed_at = None
        knowledge_source.teardown_next_retry_at = None
        knowledge_source.status_detail = (
            "Conversion stop was confirmed; cleanup is queued."
            if task_id
            else (
                "Restore executor stop was confirmed; cleanup is queued."
                if restore_task_id
                else "Cleanup recovery was confirmed and queued."
            )
        )
        knowledge_source.save(
            update_fields=[
                "sync_state_json",
                "teardown_state_json",
                "teardown_attempts",
                "teardown_claim_token",
                "teardown_claimed_at",
                "teardown_next_retry_at",
                "status_detail",
                "updated_at",
            ]
        )
