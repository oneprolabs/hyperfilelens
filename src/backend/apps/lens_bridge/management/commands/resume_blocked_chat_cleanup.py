"""Resume one blocked Chat cleanup through an audited operator confirmation."""

from __future__ import annotations

import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.lens_bridge.models import LensKnowledgeSource, LensSessionLink
from apps.lens_bridge.services import sl_client


class Command(BaseCommand):
    help = (
        "Resume one blocked Chat cleanup after an operator has independently "
        "confirmed that its LensNode conversion executor stopped."
    )

    def add_arguments(self, parser):
        parser.add_argument("--session-id", type=int, required=True)
        parser.add_argument("--source-lens-task-id", required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument(
            "--confirm-executor-stopped",
            action="store_true",
            help="Required acknowledgement that the old executor no longer runs.",
        )

    def handle(self, *args, **options):
        if not options["confirm_executor_stopped"]:
            raise CommandError(
                "Refusing to resume cleanup without --confirm-executor-stopped."
            )
        task_id = str(options["source_lens_task_id"] or "").strip()
        reason = str(options["reason"] or "").strip()
        if not task_id or not reason:
            raise CommandError("SourceLens task id and reason are required.")

        remote_task = sl_client.get_task_by_id(task_id)
        if remote_task is None:
            raise CommandError("SourceLens task was not found; cleanup remains blocked.")
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

        with transaction.atomic():
            session = (
                LensSessionLink.objects.select_for_update()
                .select_related("knowledge_source")
                .filter(pk=options["session_id"])
                .first()
            )
            if session is None:
                raise CommandError("Chat session was not found.")
            if session.cleanup_status != LensSessionLink.CleanupStatus.BLOCKED:
                raise CommandError("Chat cleanup is not blocked.")
            knowledge_source = session.knowledge_source
            if knowledge_source is None:
                raise CommandError("Blocked Chat has no Knowledge Source.")

            knowledge_source = LensKnowledgeSource.all_objects.select_for_update().get(
                pk=knowledge_source.pk
            )
            sync_state = dict(knowledge_source.sync_state_json or {})
            conversion = dict(sync_state.get("conversion") or {})
            recorded_task_id = str(conversion.get("task_id") or "").strip()
            if recorded_task_id != task_id:
                raise CommandError(
                    "SourceLens task id does not match the blocked Knowledge Source."
                )

            confirmation = {
                "confirmed": True,
                "task_id": task_id,
                "remote_status": remote_status,
                "operator": getpass.getuser(),
                "reason": reason[:1000],
                "confirmed_at": timezone.now().isoformat(),
            }
            conversion["manual_stop_confirmation"] = confirmation
            sync_state["conversion"] = conversion
            knowledge_source.sync_state_json = sync_state
            knowledge_source.teardown_next_retry_at = None
            knowledge_source.status_detail = (
                "Conversion stop was confirmed; cleanup is queued."
            )
            knowledge_source.save(
                update_fields=[
                    "sync_state_json",
                    "teardown_next_retry_at",
                    "status_detail",
                    "updated_at",
                ]
            )

            teardown_state = dict(session.teardown_state_json or {})
            teardown_state["manual_stop_confirmation"] = confirmation
            session.teardown_state_json = teardown_state
            session.cleanup_status = LensSessionLink.CleanupStatus.PENDING
            session.teardown_claim_token = None
            session.teardown_claimed_at = None
            session.teardown_next_retry_at = None
            session.save(
                update_fields=[
                    "teardown_state_json",
                    "cleanup_status",
                    "teardown_claim_token",
                    "teardown_claimed_at",
                    "teardown_next_retry_at",
                    "updated_at",
                ]
            )

            from apps.lens_bridge.services.chat_lifecycle import (
                _queue_teardown_or_record_error,
            )

            transaction.on_commit(
                lambda: _queue_teardown_or_record_error(session.id)
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued audited cleanup recovery for Chat {session.id}."
            )
        )
