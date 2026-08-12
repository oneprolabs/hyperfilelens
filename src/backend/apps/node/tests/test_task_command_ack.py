from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.protection import conf as protection_conf
from apps.node.services.internal.task import (
    _RouteState,
    accept_task,
    complete_task,
    deliver_agent_task,
    reconcile_unaccepted_agent_tasks,
    record_task_progress,
    sweep_watchdog_timeouts,
)


class TaskCommandAckTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="task-ack-org", name="Task ACK Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="ack-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE, availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
            metadata={"inventory": {"capabilities": ["task_command_ack_v1"]}},
        )

    def task(self, **overrides):
        values = {
            "organization": self.org,
            "node": self.node,
            "kind": "backup.run",
            "correlation_type": "protection.backup",
            "correlation_id": "platform-task-1",
            "status": NodeTask.Status.PENDING,
            "watchdog_deadline_at": timezone.now() + timezone.timedelta(hours=2),
        }
        values.update(overrides)
        return NodeTask.objects.create(**values)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch("apps.node.services.internal.task._node_route_state", return_value=_RouteState.ONLINE)
    def test_ack_capable_delivery_remains_pending_until_accepted(
        self, _route, send_command, _set_info
    ):
        task = deliver_agent_task(task=self.task())

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.delivery_attempt_count, 1)
        self.assertIsNotNone(task.dispatched_at)
        self.assertIsNone(task.accepted_at)
        self.assertEqual(send_command.call_args.kwargs["task"].id, task.id)

        accepted = accept_task(task_id=task.id, node_id=self.node.id)
        self.assertEqual(accepted.status, NodeTask.Status.RUNNING)
        self.assertIsNotNone(accepted.accepted_at)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_progress_and_result_are_implicit_acceptance(self, _push, _set_info):
        progressed = record_task_progress(
            task_id=self.task().id,
            node_id=self.node.id,
            progress={},
            alive=True,
        )
        self.assertEqual(progressed.status, NodeTask.Status.RUNNING)
        self.assertIsNotNone(progressed.accepted_at)

        result_task = self.task(correlation_id="platform-task-2")
        completed = complete_task(
            task_id=result_task.id,
            node_id=self.node.id,
            status="success",
            result={"kopia_snapshot_id": "logical-snapshot-64"},
        )
        self.assertEqual(completed.status, NodeTask.Status.SUCCESS)
        self.assertIsNotNone(completed.accepted_at)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_backup_alive_renews_activity_lease_without_substantive_progress(
        self, _push, _set_info
    ):
        task = self.task(
            status=NodeTask.Status.RUNNING,
            accepted_at=timezone.now() - timezone.timedelta(hours=3),
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        renewed = record_task_progress(
            task_id=task.id,
            node_id=self.node.id,
            progress={},
            alive=True,
        )

        self.assertEqual(renewed.status, NodeTask.Status.RUNNING)
        remaining = (renewed.watchdog_deadline_at - timezone.now()).total_seconds()
        self.assertGreater(
            remaining,
            protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS - 5,
        )
        self.assertLessEqual(
            remaining,
            protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS,
        )

    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch(
        "apps.node.services.internal.task_offline_reconcile.sync_platform_tasks_for_node_task"
    )
    @patch("apps.node.services.internal.task._sync_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch(
        "apps.node.services.internal.task.redis_store.get_task_uplink_activities",
        return_value={},
    )
    def test_backup_without_activity_expires_after_lease(
        self,
        _uplink_activity,
        _push,
        _set_info,
        _sync_platform_task,
        send_cancel,
    ):
        task = self.task(
            status=NodeTask.Status.RUNNING,
            accepted_at=timezone.now() - timezone.timedelta(hours=3),
            last_progress_at=timezone.now()
            - timezone.timedelta(
                seconds=protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS + 1
            ),
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        marked = sweep_watchdog_timeouts(
            queryset=NodeTask.objects.filter(pk=task.pk),
        )

        task.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)
        self.assertEqual(task.last_error, "watchdog timeout (no progress)")
        send_cancel.assert_called_once()

    @patch("apps.node.services.internal.task.redis_store.ws_recovery_hold_active", return_value=False)
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch("apps.node.services.internal.task._node_route_state", return_value=_RouteState.ONLINE)
    def test_retry_reuses_exact_node_task_id(
        self, _route, send_command, _set_info, _hold
    ):
        old = timezone.now() - timezone.timedelta(seconds=60)
        task = self.task(
            dispatched_at=old,
            last_delivery_at=old,
            delivery_attempt_count=1,
        )

        summary = reconcile_unaccepted_agent_tasks(limit=10)
        task.refresh_from_db()

        self.assertEqual(summary["redelivered"], 1)
        self.assertEqual(task.delivery_attempt_count, 2)
        self.assertEqual(send_command.call_args.kwargs["task"].id, task.id)

    @patch("apps.node.services.internal.task.redis_store.ws_recovery_hold_active", return_value=True)
    @patch("apps.node.services.internal.task._send_task_command")
    def test_recovery_hold_does_not_consume_retry(self, send_command, _hold):
        task = self.task(delivery_attempt_count=1)
        summary = reconcile_unaccepted_agent_tasks(limit=10)
        task.refresh_from_db()
        self.assertTrue(summary["recovery_hold"])
        self.assertEqual(task.delivery_attempt_count, 1)
        send_command.assert_not_called()

    @patch("apps.node.services.internal.task._schedule_agent_task_redelivery")
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.ws_recovery_hold_active", return_value=True)
    @patch("apps.node.services.internal.task._send_task_command")
    def test_recovery_hold_delays_legacy_first_delivery(
        self, send_command, _hold, _set_info, schedule_redelivery
    ):
        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])
        task = deliver_agent_task(task=self.task())

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.delivery_attempt_count, 0)
        self.assertEqual(task.last_error, "agent websocket is reconnecting")
        send_command.assert_not_called()
        schedule_redelivery.assert_called_once()

    @patch("apps.node.services.internal.task.redis_store.ws_recovery_hold_active", return_value=False)
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch("apps.node.services.internal.task._node_route_state", return_value=_RouteState.ONLINE)
    @patch("apps.node.services.internal.task_offline_reconcile.sync_platform_tasks_for_node_task")
    def test_retry_exhaustion_seals_timeout_against_late_result(
        self, sync_parent, _route, _cancel, _push, _set_info, _hold
    ):
        old = timezone.now() - timezone.timedelta(seconds=60)
        task = self.task(
            dispatched_at=old,
            last_delivery_at=old,
            delivery_attempt_count=4,
        )
        summary = reconcile_unaccepted_agent_tasks(limit=10)
        task.refresh_from_db()
        self.assertEqual(summary["timed_out"], 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)
        self.assertEqual(task.last_error.split(":", 1)[0], "AGENT_ACK_TIMEOUT")
        self.assertTrue(task.result["delivery_timeout_sealed"])
        sync_parent.assert_called_once()

        late = complete_task(
            task_id=task.id,
            node_id=self.node.id,
            status="success",
            result={"kopia_snapshot_id": "late"},
        )
        self.assertEqual(late.status, NodeTask.Status.TIMEOUT)
        self.assertNotIn("kopia_snapshot_id", late.result)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch("apps.node.services.internal.task._node_route_state", return_value=_RouteState.ONLINE)
    def test_legacy_agent_keeps_send_then_running(self, _route, _send, _set_info):
        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])
        task = deliver_agent_task(task=self.task())
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        self.assertIsNone(task.accepted_at)
