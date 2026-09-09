import copy
import json
import logging

from django.test import TestCase

from apps.iam.models import Organization
from apps.task.models import Task
from apps.task.services.interface import append_task_event, complete_task, create_task


class TaskEventTextTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="event-text", name="Event Text")
        self.task = create_task(
            organization_id=self.org.id, task_type=Task.Type.BACKUP,
            display_name="Event text", steps=["snapshot", "finalize"],
        )

    def test_collection_normalizes_nested_text_without_changing_log_payload(self):
        metadata = {
            "error_code": "KOPIA_MAINTENANCE_FAILED",
            "details": [{"output": "KoPiA failed", "count": 2, "enabled": True}],
            "nullable": None,
        }
        original = copy.deepcopy(metadata)
        event = append_task_event(task=self.task, message="Kopia failed", metadata=metadata)
        event.refresh_from_db()
        self.assertNotIn("kopia", json.dumps([event.message, event.metadata]).lower())
        self.assertEqual(event.metadata["error_code"], "REPOSITORY_MAINTENANCE_FAILED")
        self.assertEqual(event.metadata["details"][0]["count"], 2)
        self.assertEqual(metadata, original)
        with self.assertLogs("event-text", level="WARNING") as captured:
            logging.getLogger("event-text").warning("Kopia diagnostics: %s", metadata)
        self.assertIn("Kopia", captured.output[0])
        self.assertIn("KoPiA", captured.output[0])

    def test_event_update_is_normalized_and_preserves_routing_identifiers(self):
        event = append_task_event(task=self.task, message="Directory backup failed")
        event.metadata = {
            "error_message": "kopia failed", "current_step": "kopia_snapshot",
            "kopia_statistics": {"text": "KOPIA failed"},
            "object_id": "kopia-snapshot-1",
            "kopia_snapshot_ids": ["kopia-snapshot-1"],
        }
        event.save(update_fields=["metadata"])
        event.refresh_from_db()
        self.assertEqual(event.metadata["error_message"], "repository engine failed")
        self.assertEqual(event.metadata["current_step"], "kopia_snapshot")
        self.assertIn("kopia_statistics", event.metadata)
        self.assertEqual(event.metadata["object_id"], "kopia-snapshot-1")
        self.assertEqual(event.metadata["kopia_snapshot_ids"], ["kopia-snapshot-1"])

    def test_generic_completion_does_not_skip_partial_success_workflows(self):
        complete_task(
            task_uuid=self.task.task_uuid, organization_id=self.org.id,
            status=Task.Status.FAILED, error_message="Kopia failed",
        )
        self.assertEqual(list(self.task.steps.values_list("status", flat=True)), ["pending", "pending"])
        event = self.task.events.latest("seq")
        self.assertNotIn("kopia", event.metadata["error_message"].lower())
