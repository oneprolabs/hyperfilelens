"""complete_task must not downgrade a terminal success on stale agent flush."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.task import complete_task
from apps.node.ws.uplink import _handle_task_result
from apps.node.ws.wire import ParsedUplink, WireType


class CompleteTaskIdempotencyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="complete-task-org", name="Complete Task Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="complete-task-node",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
        )
        self.task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.SUCCESS,
            result={"mode": "local_detached"},
            watchdog_deadline_at=timezone.now(),
        )

    def test_stale_failed_result_does_not_overwrite_success(self):
        updated = complete_task(
            task_id=self.task.id,
            node_id=self.node.id,
            status="failed",
            error="agent restarted before task completed",
        )

        self.assertEqual(updated.status, NodeTask.Status.SUCCESS)
        self.assertEqual(updated.result, {"mode": "local_detached"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, NodeTask.Status.SUCCESS)
        self.assertEqual(self.task.last_error, "")

    def test_running_result_keeps_task_active_and_merges_result(self):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.PENDING,
            watchdog_deadline_at=timezone.now(),
        )
        updated = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="running",
            result={
                "mode": "local_detached",
                "target_version": "1.2.0",
            },
        )
        self.assertEqual(updated.status, NodeTask.Status.RUNNING)
        self.assertEqual(updated.result.get("mode"), "local_detached")
        self.assertEqual(updated.result.get("target_version"), "1.2.0")

    def test_late_success_upgrades_failed_task_and_clears_error(self):
        self.task.status = NodeTask.Status.FAILED
        self.task.last_error = "Agent went offline during task execution."
        self.task.result = {"last_progress": {"phase": "repository_ready"}}
        self.task.save(update_fields=["status", "last_error", "result", "updated_at"])

        updated = complete_task(
            task_id=self.task.id,
            node_id=self.node.id,
            status="success",
            result={"kopia_snapshot_id": "late-kopia-id"},
        )

        self.assertEqual(updated.status, NodeTask.Status.SUCCESS)
        self.assertEqual(updated.last_error, "")
        self.assertEqual(updated.result["kopia_snapshot_id"], "late-kopia-id")

    def test_late_success_does_not_revive_cancelled_task(self):
        self.task.status = NodeTask.Status.CANCELED
        self.task.last_error = "canceled by user"
        self.task.save(update_fields=["status", "last_error", "updated_at"])

        updated = complete_task(
            task_id=self.task.id,
            node_id=self.node.id,
            status="success",
            result={"kopia_snapshot_id": "must-not-apply"},
        )

        self.assertEqual(updated.status, NodeTask.Status.CANCELED)
        self.assertEqual(updated.last_error, "canceled by user")
        self.assertNotIn("kopia_snapshot_id", updated.result)

    def test_late_backup_success_requires_snapshot_identity(self):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="backup.snapshot.create",
            correlation_type="protection.backup",
            correlation_id="backup-1",
            status=NodeTask.Status.TIMEOUT,
            last_error="watchdog timeout (no progress)",
            watchdog_deadline_at=timezone.now(),
        )

        updated = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="success",
            result={"result_truncated": True},
        )

        self.assertEqual(updated.status, NodeTask.Status.TIMEOUT)
        self.assertEqual(updated.last_error, "watchdog timeout (no progress)")

    def test_late_backup_success_with_snapshot_identity_recovers_timeout(self):
        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="backup.snapshot.create",
            correlation_type="protection.backup",
            correlation_id="backup-2",
            status=NodeTask.Status.TIMEOUT,
            last_error="watchdog timeout (no progress)",
            watchdog_deadline_at=timezone.now(),
        )

        updated = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="success",
            result={"kopia_snapshot_id": "late-snapshot"},
        )

        self.assertEqual(updated.status, NodeTask.Status.SUCCESS)
        self.assertEqual(updated.last_error, "")
        self.assertEqual(updated.result["kopia_snapshot_id"], "late-snapshot")

    @patch("apps.node.ws.uplink.TASK_RESULT_RETRANSMISSIONS")
    def test_repeated_success_is_idempotent_and_counted_as_retransmission(self, metric):
        message = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id=str(self.task.id),
            status="success",
            result={"kopia_snapshot_id": "snapshot-repeat"},
        )

        updated = _handle_task_result(node_id=self.node.id, message=message)

        self.assertEqual(updated.status, NodeTask.Status.SUCCESS)
        self.assertEqual(updated.result["kopia_snapshot_id"], "snapshot-repeat")
        metric.inc.assert_called_once_with()

    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task._sync_task_info")
    @patch("apps.node.ws.uplink.TASK_RESULT_RETRANSMISSIONS")
    def test_identical_terminal_retransmission_skips_duplicate_writes_and_streams(
        self,
        metric,
        sync_task_info,
        push_task_stream,
    ):
        message = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id=str(self.task.id),
            status="success",
            result={"mode": "local_detached"},
        )

        updated = _handle_task_result(node_id=self.node.id, message=message)

        self.assertTrue(updated._result_retransmission_unchanged)
        sync_task_info.assert_not_called()
        push_task_stream.assert_not_called()
        metric.inc.assert_called_once_with()
