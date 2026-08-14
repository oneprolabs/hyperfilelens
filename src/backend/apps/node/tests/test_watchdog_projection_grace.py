"""Tests for watchdog handling of queued Agent uplink projection."""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.task import sweep_watchdog_timeouts


class WatchdogProjectionGraceTests(TestCase):
    def setUp(self) -> None:
        self.org = Organization.objects.create(
            key="watchdog-projection",
            name="Watchdog Projection",
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="projection-agent",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
        )

    def create_expired_task(self) -> NodeTask:
        return NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="backup.run",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

    @patch("apps.node.services.internal.task.redis_store.get_task_uplink_activities")
    def test_recent_queued_result_defers_timeout(self, mock_activity) -> None:
        task = self.create_expired_task()
        mock_activity.return_value = {
            str(task.id): {
                "message_type": "task.result",
                "received_at": timezone.now().timestamp(),
            }
        }

        marked = sweep_watchdog_timeouts()

        task.refresh_from_db()
        self.assertEqual(marked, 0)
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        self.assertGreater(
            task.watchdog_deadline_at,
            timezone.now() + timezone.timedelta(seconds=240),
        )
        mock_activity.assert_called_once_with(task_ids=[str(task.id)])

    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch("apps.node.services.internal.task._sync_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task.redis_store.get_task_uplink_activities")
    def test_stale_uplink_marker_does_not_hide_real_timeout(
        self,
        mock_activity,
        _push_stream,
        _sync_task_info,
        _send_cancel,
    ) -> None:
        task = self.create_expired_task()
        mock_activity.return_value = {
            str(task.id): {
                "message_type": "task_progress",
                "received_at": (
                    timezone.now()
                    - timezone.timedelta(
                        seconds=node_conf.TASK_UPLINK_PROJECTION_GRACE_SECONDS + 1
                    )
                ).timestamp(),
            }
        }

        marked = sweep_watchdog_timeouts()

        task.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)

    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch("apps.node.services.internal.task._sync_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task.redis_store.get_task_uplink_activities")
    def test_stale_result_marker_reports_ack_timeout(
        self,
        mock_activity,
        _push_stream,
        _sync_task_info,
        _send_cancel,
    ) -> None:
        task = self.create_expired_task()
        mock_activity.return_value = {
            str(task.id): {
                "message_type": "task.result",
                "received_at": (
                    timezone.now()
                    - timezone.timedelta(
                        seconds=node_conf.TASK_RESULT_UPLINK_PROJECTION_GRACE_SECONDS + 1
                    )
                ).timestamp(),
            }
        }

        marked = sweep_watchdog_timeouts()

        task.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)
        self.assertEqual(task.last_error, "result acknowledgement timeout")
        self.assertEqual(task.result["diagnostic_error_code"], "RESULT_ACK_TIMEOUT")
