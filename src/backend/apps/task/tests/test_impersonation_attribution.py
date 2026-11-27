"""Operation provenance must not replace the original task initiator."""

from django.test import TestCase

from apps.task.models import Task, TaskEvent
from apps.task.services.interface import cancel_task, retry_task
from common.impersonation import attribution


class TaskImpersonationAttributionTests(TestCase):
    def setUp(self):
        self.info = {
            "session_id": "delegation-reference",
            "actor_id": 10,
            "actor_email": "support@example.test",
            "subject_id": 20,
            "subject_email": "customer@example.test",
            "organization_id": 1,
        }
        token = attribution.set(None)
        self.addCleanup(attribution.reset, token)

    def test_existing_task_keeps_creator_and_attributes_retry_and_cancel_events(self):
        original = {"impersonation": {"actor_id": 5, "session_id": "original"}}
        task = Task.objects.create(
            organization_id=1, task_type="backup", display_name="Existing backup",
            status="failed", initiator_context=original,
        )
        attribution.set(self.info)

        retry_task(task_uuid=task.task_uuid, organization_id=1, reason="Retry")
        cancel_task(task_uuid=task.task_uuid, organization_id=1, reason="Stop")

        task.refresh_from_db()
        self.assertEqual(task.initiator_context, original)
        self.assertEqual(task.retry_count, 1)
        self.assertEqual(task.status, "cancelled")
        for event in task.events.all():
            self.assertEqual(event.metadata["impersonation"], self.info)
        self.assertEqual(task.events.count(), 2)

    def test_event_uses_trusted_context_and_does_not_mutate_input_metadata(self):
        task = Task.objects.create(task_type="backup", display_name="Existing")
        metadata = {"reason": "Retry", "impersonation": {"actor_id": -1}}
        attribution.set(self.info)

        event = TaskEvent.objects.create(
            task=task, seq=1, message="Task queued for retry", metadata=metadata,
        )

        event.refresh_from_db()
        self.assertEqual(event.metadata["impersonation"], self.info)
        self.assertEqual(metadata["impersonation"], {"actor_id": -1})

    def test_later_normal_events_do_not_inherit_the_task_creator(self):
        attribution.set(self.info)
        task = Task.objects.create(task_type="backup", display_name="Delegated")
        attribution.set(None)

        event = TaskEvent.objects.create(
            task=task, seq=1, message="Worker progress", metadata={"progress": 10},
        )

        self.assertEqual(task.initiator_context["impersonation"], self.info)
        self.assertEqual(event.metadata, {"progress": 10})

    def test_existing_event_provenance_is_not_replaced_by_later_edits(self):
        task = Task.objects.create(task_type="backup", display_name="Existing")
        event = TaskEvent.objects.create(
            task=task, seq=1, message="Original event", metadata={"reason": "Original"},
        )
        attribution.set(self.info)

        event.message = "Updated details"
        event.save()

        event.refresh_from_db()
        self.assertEqual(event.metadata, {"reason": "Original"})
