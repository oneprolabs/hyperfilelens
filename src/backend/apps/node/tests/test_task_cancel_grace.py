from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.task import (
    _is_managed_workspace_restore_task,
    cancel_task,
    complete_task,
    sweep_cancel_grace_expired,
)
from apps.node.ws.uplink import _handle_task_result
from apps.node.ws.wire import ParsedUplink, WireType


class NodeTaskCancelGraceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="cancel-grace", name="Cancel Grace")
        self.node = Node.objects.create(
            organization=self.org,
            name="cancel-grace-agent",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )

    def create_task(self, *, status=NodeTask.Status.RUNNING) -> NodeTask:
        return NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id="backup-task-id",
            status=status,
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=2),
        )

    @patch("apps.node.services.internal.task._send_cancel_command")
    def test_running_cancel_records_stable_request_time(self, _send_cancel):
        task = self.create_task()

        first = cancel_task(task_id=task.id, reason="Task cancelled by user")
        first_requested_at = first.cancel_requested_at
        second = cancel_task(task_id=task.id, reason="Task cancelled by user")

        self.assertEqual(second.status, NodeTask.Status.RUNNING)
        self.assertEqual(second.cancel_requested_at, first_requested_at)
        self.assertEqual(
            second.result["cancel_requested_at"], first_requested_at.isoformat()
        )

    @patch("apps.node.services.internal.task._send_cancel_command")
    def test_pending_cancel_is_immediately_terminal(self, _send_cancel):
        task = self.create_task(status=NodeTask.Status.PENDING)

        canceled = cancel_task(task_id=task.id, reason="Task cancelled by user")

        self.assertEqual(canceled.status, NodeTask.Status.CANCELED)
        self.assertIsNone(canceled.cancel_requested_at)

    @patch("apps.node.services.internal.task._project_terminal_node_task")
    def test_sweep_cancels_after_grace_and_records_audit_metadata(self, projection):
        requested_at = timezone.now() - timezone.timedelta(seconds=301)
        task = self.create_task()
        task.cancel_requested_at = requested_at
        task.result = {
            "cancel_requested": True,
            "cancel_requested_at": requested_at.isoformat(),
        }
        task.last_error = "Task cancelled by user"
        task.save(
            update_fields=["cancel_requested_at", "result", "last_error", "updated_at"]
        )

        with patch("apps.node.conf.TASK_CANCEL_GRACE_SECONDS", 300):
            marked = sweep_cancel_grace_expired()

        self.assertEqual(marked, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.CANCELED)
        self.assertEqual(task.last_error, "Task cancelled by user")
        self.assertEqual(task.result["cancel_finalized_by"], "grace_timeout")
        self.assertIn("cancel_finalized_at", task.result)
        projection.assert_called_once()

    @patch("apps.node.services.internal.task._project_terminal_node_task")
    def test_sweep_leaves_cancel_within_grace_running(self, projection):
        task = self.create_task()
        task.cancel_requested_at = timezone.now() - timezone.timedelta(seconds=299)
        task.save(update_fields=["cancel_requested_at", "updated_at"])

        with patch("apps.node.conf.TASK_CANCEL_GRACE_SECONDS", 300):
            marked = sweep_cancel_grace_expired()

        self.assertEqual(marked, 0)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        projection.assert_not_called()

    def test_cancelled_managed_restore_accepts_late_executor_stop_evidence(self):
        task = self.create_task(status=NodeTask.Status.CANCELED)
        task.kind = "restore.run"
        task.correlation_type = "restore.record"
        task.payload = {
            "workspace_kind": "managed_restore",
            "workspace_uid": "8f65d43a-09fd-4ae7-b5f1-159352838a23",
            "managed_workspace_path": "/var/lib/hyperfilelens/insight/workspace",
        }
        task.result = {
            "cancel_requested": True,
            "cancel_finalized_by": "grace_timeout",
        }
        task.save(
            update_fields=[
                "kind",
                "correlation_type",
                "payload",
                "result",
                "updated_at",
            ]
        )
        self.assertTrue(_is_managed_workspace_restore_task(task), task.payload)

        completed = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="failed",
            result={
                "executor_finished": True,
                "completion_source": "agent_executor",
            },
            error="canceled",
        )

        self.assertEqual(completed.status, NodeTask.Status.CANCELED)
        self.assertTrue(completed.result["cancel_requested"])
        self.assertEqual(completed.result["cancel_finalized_by"], "grace_timeout")
        self.assertTrue(completed.result["executor_finished"])
        self.assertEqual(completed.result["completion_source"], "agent_executor")

    def test_cancelled_managed_restore_rejects_unrelated_failed_result(self):
        task = self.create_task(status=NodeTask.Status.CANCELED)
        task.kind = "restore.run"
        task.correlation_type = "restore.record"
        task.payload = {
            "workspace_kind": "managed_restore",
            "workspace_uid": "8f65d43a-09fd-4ae7-b5f1-159352838a23",
            "managed_workspace_path": "/var/lib/hyperfilelens/insight/workspace",
        }
        task.save(update_fields=["kind", "correlation_type", "payload", "updated_at"])

        completed = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="failed",
            result={
                "executor_finished": True,
                "completion_source": "agent_executor",
            },
            error="restore failed",
        )

        self.assertEqual(completed.status, NodeTask.Status.CANCELED)
        self.assertNotIn("executor_finished", completed.result)

    def test_websocket_failed_cancel_preserves_executor_stop_evidence(self):
        task = self.create_task(status=NodeTask.Status.CANCELED)
        task.kind = "restore.run"
        task.correlation_type = "restore.record"
        task.correlation_id = "managed-restore-record"
        task.payload = {
            "workspace_kind": "managed_restore",
            "workspace_uid": "8f65d43a-09fd-4ae7-b5f1-159352838a23",
            "managed_workspace_path": "/var/lib/hyperfilelens/insight/workspace",
        }
        task.result = {"cancel_finalized_by": "grace_timeout"}
        task.save(
            update_fields=[
                "kind",
                "correlation_type",
                "correlation_id",
                "payload",
                "result",
                "updated_at",
            ]
        )

        completed = _handle_task_result(
            node_id=self.node.id,
            message=ParsedUplink(
                msg_type=WireType.TASK_RESULT,
                task_id=str(task.id),
                status="failed",
                result={
                    "executor_finished": True,
                    "completion_source": "agent_executor",
                    "executor_finished_at": "2026-08-31T14:00:00Z",
                },
                error="canceled",
            ),
        )

        self.assertEqual(completed.status, NodeTask.Status.CANCELED)
        self.assertEqual(completed.result["cancel_finalized_by"], "grace_timeout")
        self.assertTrue(completed.result["executor_finished"])
        self.assertEqual(completed.result["completion_source"], "agent_executor")
