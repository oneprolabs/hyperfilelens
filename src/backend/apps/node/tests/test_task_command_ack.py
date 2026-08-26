from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.protection import conf as protection_conf
from apps.node.services.internal.task import (
    _RouteState,
    accept_task,
    cancel_task,
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
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
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
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
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
    @patch("apps.node.services.internal.task._send_task_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_background_operations_use_durable_acceptance(
        self, _route, send_command, _set_info
    ):
        for correlation_type, kind in (
            ("protection.snapshot_delete", "snapshot.delete"),
            ("protection.backup_config_reset", "snapshot.delete"),
            ("source.connection_probe", "nas.test"),
            ("storage.repository_health", "repo.status"),
            ("repository_create", "repo.initialize"),
            ("protection.backup_config", "repo.initialize"),
        ):
            with self.subTest(correlation_type=correlation_type, kind=kind):
                task = deliver_agent_task(
                    task=self.task(
                        kind=kind,
                        correlation_type=correlation_type,
                    )
                )

                self.assertEqual(task.status, NodeTask.Status.PENDING)
                self.assertEqual(task.delivery_attempt_count, 1)
                self.assertIsNotNone(task.dispatched_at)
                self.assertIsNone(task.accepted_at)

        self.assertEqual(send_command.call_count, 6)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_repeated_orchestrator_observation_does_not_redeliver_ack_task(
        self, _route, send_command, _set_info
    ):
        task = deliver_agent_task(task=self.task())

        observed_again = deliver_agent_task(task=task)

        self.assertEqual(observed_again.status, NodeTask.Status.PENDING)
        self.assertEqual(observed_again.delivery_attempt_count, 1)
        self.assertEqual(send_command.call_count, 1)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    @patch("apps.node.services.internal.task._persist_delivery_protocol")
    def test_acceptance_race_prevents_duplicate_ack_delivery(
        self, select_protocol, _route, send_command, _set_info
    ):
        task = self.task()

        def accept_before_delivery(*, task):
            NodeTask.objects.filter(pk=task.pk).update(
                status=NodeTask.Status.RUNNING,
                accepted_at=timezone.now(),
            )
            return True

        select_protocol.side_effect = accept_before_delivery

        delivered = deliver_agent_task(task=task)

        self.assertEqual(delivered.status, NodeTask.Status.RUNNING)
        self.assertIsNotNone(delivered.accepted_at)
        send_command.assert_not_called()

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

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_source_nas_probe_progress_does_not_extend_absolute_deadline(
        self, _push, _set_info
    ):
        accepted_at = timezone.now() - timezone.timedelta(seconds=10)
        task = self.task(
            kind="nas.test",
            correlation_type="source.connection_probe",
            correlation_id="732",
            status=NodeTask.Status.RUNNING,
            accepted_at=accepted_at,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        renewed = record_task_progress(
            task_id=task.id,
            node_id=self.node.id,
            progress={"phase": "running"},
        )

        self.assertEqual(
            renewed.watchdog_deadline_at,
            accepted_at
            + timezone.timedelta(
                seconds=node_conf.SOURCE_NAS_PROBE_EXECUTION_TIMEOUT_SECONDS
            ),
        )

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_legacy_source_nas_probe_progress_uses_dispatch_start(
        self, _push, _set_info
    ):
        dispatched_at = timezone.now() - timezone.timedelta(seconds=20)
        task = self.task(
            kind="nas.test",
            correlation_type="source.connection_probe",
            correlation_id="732",
            status=NodeTask.Status.RUNNING,
            dispatched_at=dispatched_at,
            accepted_at=None,
            last_progress_at=dispatched_at,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        renewed = record_task_progress(
            task_id=task.id,
            node_id=self.node.id,
            progress={"phase": "running"},
        )

        self.assertEqual(
            renewed.watchdog_deadline_at,
            dispatched_at
            + timezone.timedelta(
                seconds=node_conf.SOURCE_NAS_PROBE_EXECUTION_TIMEOUT_SECONDS
            ),
        )

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_repository_initialize_alive_does_not_extend_absolute_deadline(
        self, _push, _set_info
    ):
        accepted_at = timezone.now() - timezone.timedelta(seconds=10)
        task = self.task(
            kind="repo.initialize",
            correlation_type="repository_create",
            correlation_id="repository-task",
            status=NodeTask.Status.RUNNING,
            accepted_at=accepted_at,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        renewed = record_task_progress(
            task_id=task.id,
            node_id=self.node.id,
            progress={},
            alive=True,
        )

        self.assertEqual(
            renewed.watchdog_deadline_at,
            accepted_at
            + timezone.timedelta(
                seconds=node_conf.REPOSITORY_INITIALIZE_WATCHDOG_SECONDS
            ),
        )

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_legacy_repository_initialize_uses_dispatch_start(
        self, _push, _set_info
    ):
        dispatched_at = timezone.now() - timezone.timedelta(seconds=20)
        task = self.task(
            kind="repo.initialize",
            correlation_type="repository_create",
            correlation_id="legacy-repository-task",
            status=NodeTask.Status.RUNNING,
            dispatched_at=dispatched_at,
            accepted_at=None,
            last_progress_at=dispatched_at,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        renewed = record_task_progress(
            task_id=task.id,
            node_id=self.node.id,
            progress={"phase": "running"},
        )

        self.assertEqual(
            renewed.watchdog_deadline_at,
            dispatched_at
            + timezone.timedelta(
                seconds=node_conf.REPOSITORY_INITIALIZE_WATCHDOG_SECONDS
            ),
        )

    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch(
        "apps.node.services.internal.task_offline_reconcile.sync_platform_tasks_for_node_task"
    )
    @patch("apps.node.services.internal.task._sync_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_source_nas_probe_generic_progress_does_not_defer_timeout(
        self,
        _push,
        _set_info,
        _sync_platform_task,
        send_cancel,
    ):
        task = self.task(
            kind="nas.test",
            correlation_type="source.connection_probe",
            correlation_id="732",
            status=NodeTask.Status.RUNNING,
            accepted_at=timezone.now() - timezone.timedelta(minutes=1),
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        activity = {
            str(task.id): {
                "received_at": timezone.now().timestamp(),
                "message_type": "task.progress",
            }
        }

        with patch(
            "apps.node.services.internal.task.redis_store.get_task_uplink_activities",
            return_value=activity,
        ):
            marked = sweep_watchdog_timeouts(
                queryset=NodeTask.objects.filter(pk=task.pk),
            )

        task.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)
        send_cancel.assert_called_once()

    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch(
        "apps.node.services.internal.task_offline_reconcile.sync_platform_tasks_for_node_task"
    )
    @patch("apps.node.services.internal.task._sync_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    def test_repository_initialize_alive_does_not_defer_timeout(
        self,
        _push,
        _set_info,
        _sync_platform_task,
        send_cancel,
    ):
        task = self.task(
            kind="repo.initialize",
            correlation_type="protection.backup_config",
            correlation_id="backup-config-task",
            status=NodeTask.Status.RUNNING,
            accepted_at=timezone.now() - timezone.timedelta(minutes=10),
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        activity = {
            str(task.id): {
                "received_at": timezone.now().timestamp(),
                "message_type": "task.alive",
            }
        }

        with patch(
            "apps.node.services.internal.task.redis_store.get_task_uplink_activities",
            return_value=activity,
        ):
            marked = sweep_watchdog_timeouts(
                queryset=NodeTask.objects.filter(pk=task.pk),
            )

        task.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)
        send_cancel.assert_called_once()

    @patch(
        "apps.node.services.internal.task.redis_store.get_task_uplink_activities",
        return_value={},
    )
    def test_pending_ack_task_is_not_execution_watchdog_timeout(self, _uplink_activity):
        task = self.task(
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        marked = sweep_watchdog_timeouts(
            queryset=NodeTask.objects.filter(pk=task.pk),
        )

        task.refresh_from_db()
        self.assertEqual(marked, 0)
        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertLess(task.watchdog_deadline_at, timezone.now())

    @patch(
        "apps.node.services.internal.task.redis_store.get_task_uplink_activities",
        return_value={},
    )
    def test_dispatched_ack_task_keeps_delivery_watchdog_after_capability_change(
        self, _uplink_activity
    ):
        task = self.task(
            delivery_attempt_count=1,
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])

        marked = sweep_watchdog_timeouts(
            queryset=NodeTask.objects.filter(pk=task.pk),
        )

        task.refresh_from_db()
        self.assertEqual(marked, 0)
        self.assertEqual(task.status, NodeTask.Status.PENDING)

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=False,
    )
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_selected_ack_protocol_survives_capability_loss(
        self, _route, _send_command, _set_info, _hold
    ):
        old = timezone.now() - timezone.timedelta(seconds=60)
        task = deliver_agent_task(task=self.task())
        self.assertEqual(task.result["_delivery_protocol"], "command_ack_v1")

        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])
        NodeTask.objects.filter(pk=task.pk).update(last_delivery_at=old)

        summary = reconcile_unaccepted_agent_tasks(limit=10)

        task.refresh_from_db()
        self.assertEqual(summary["redelivered"], 1)
        self.assertEqual(task.delivery_attempt_count, 2)

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=False,
    )
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_legacy_candidate_does_not_starve_ack_reconciliation(
        self, _route, _send_command, _set_info, _hold
    ):
        self.task(
            correlation_id="legacy-task",
            result={"_delivery_protocol": "legacy"},
        )
        ack_task = self.task(
            correlation_id="ack-task",
            result={"_delivery_protocol": "command_ack_v1"},
            last_delivery_at=timezone.now() - timezone.timedelta(seconds=60),
        )

        summary = reconcile_unaccepted_agent_tasks(limit=1)

        ack_task.refresh_from_db()
        self.assertEqual(summary["candidates"], 1)
        self.assertEqual(summary["redelivered"], 1)
        self.assertEqual(ack_task.delivery_attempt_count, 1)

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=False,
    )
    def test_unknown_delivery_protocol_is_reclassified_instead_of_stranded(
        self, _hold
    ):
        task = self.task(result={"_delivery_protocol": "future-protocol"})

        summary = reconcile_unaccepted_agent_tasks(limit=10)

        task.refresh_from_db()
        self.assertEqual(summary["candidates"], 1)
        self.assertEqual(task.result["_delivery_protocol"], "command_ack_v1")

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=True,
    )
    def test_recovery_hold_does_not_extend_unrelated_same_kind_task(self, _hold):
        expired = timezone.now() - timezone.timedelta(seconds=1)
        unrelated = self.task(
            correlation_type="unrelated.workflow",
            result={"_delivery_protocol": "command_ack_v1"},
            watchdog_deadline_at=expired,
        )

        summary = reconcile_unaccepted_agent_tasks(limit=10)

        unrelated.refresh_from_db()
        self.assertTrue(summary["recovery_hold"])
        self.assertEqual(unrelated.watchdog_deadline_at, expired)

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=True,
    )
    def test_recovery_hold_does_not_extend_unselected_legacy_candidate(self, _hold):
        expired = timezone.now() - timezone.timedelta(seconds=1)
        legacy = self.task(
            correlation_id="unselected-legacy-task",
            watchdog_deadline_at=expired,
        )

        summary = reconcile_unaccepted_agent_tasks(limit=10)

        legacy.refresh_from_db()
        self.assertTrue(summary["recovery_hold"])
        self.assertEqual(legacy.watchdog_deadline_at, expired)

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

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=False,
    )
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task._send_task_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
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

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=True,
    )
    @patch("apps.node.services.internal.task._send_task_command")
    def test_recovery_hold_does_not_consume_retry(self, send_command, _hold):
        expired = timezone.now() - timezone.timedelta(seconds=1)
        task = self.task(
            delivery_attempt_count=1,
            result={"_delivery_protocol": "command_ack_v1"},
            watchdog_deadline_at=expired,
        )
        summary = reconcile_unaccepted_agent_tasks(limit=10)
        task.refresh_from_db()
        self.assertTrue(summary["recovery_hold"])
        self.assertEqual(task.delivery_attempt_count, 1)
        self.assertGreater(task.watchdog_deadline_at, timezone.now())
        send_command.assert_not_called()

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=False,
    )
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.RECONNECTING,
    )
    @patch(
        "apps.node.services.internal.task_offline_reconcile.sync_platform_tasks_for_node_task"
    )
    def test_persistently_reconnecting_agent_has_bounded_delivery_wait(
        self, sync_parent, _route, _cancel, _push, _set_info, _hold
    ):
        task = self.task(
            result={"_delivery_protocol": "command_ack_v1"},
            watchdog_deadline_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        summary = reconcile_unaccepted_agent_tasks(limit=10)

        task.refresh_from_db()
        self.assertEqual(summary["timed_out"], 1)
        self.assertEqual(task.status, NodeTask.Status.TIMEOUT)
        self.assertEqual(
            task.result["diagnostic_error_code"],
            "AGENT_CONNECTION_UNSTABLE",
        )
        self.assertNotIn("_delivery_protocol", task.result)
        sync_parent.assert_called_once()

    @patch("apps.node.services.internal.task._schedule_agent_task_redelivery")
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.RECONNECTING,
    )
    def test_reconnecting_ack_task_relies_on_coalesced_reconciliation(
        self, _route, _set_info, schedule_redelivery
    ):
        task = deliver_agent_task(task=self.task())

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.result["_delivery_protocol"], "command_ack_v1")
        schedule_redelivery.assert_not_called()

    @patch("apps.node.services.internal.task._schedule_agent_task_redelivery")
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=True,
    )
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
        self.assertEqual(
            task.result["diagnostic_error_code"],
            "AGENT_CONNECTION_UNSTABLE",
        )
        send_command.assert_not_called()
        schedule_redelivery.assert_called_once()

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.OFFLINE,
    )
    def test_unroutable_ack_delivery_records_structured_diagnostic(
        self, _route, _set_info
    ):
        task = deliver_agent_task(task=self.task())

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.last_error, "agent websocket is not routable")
        self.assertEqual(
            task.result["diagnostic_error_code"],
            "AGENT_UNAVAILABLE",
        )

        accepted = accept_task(task_id=task.id, node_id=self.node.id)
        self.assertEqual(accepted.status, NodeTask.Status.RUNNING)
        self.assertNotIn("diagnostic_error_code", accepted.result)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    @patch(
        "apps.node.services.internal.task._send_task_command",
        side_effect=RedisConnectionError("redis unavailable"),
    )
    def test_redis_downlink_failure_remains_durable_pending_ack(
        self, _send, _route, _set_info
    ):
        task = deliver_agent_task(task=self.task())

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.delivery_attempt_count, 1)
        self.assertEqual(task.result["diagnostic_error_code"], "AGENT_DELIVERY_FAILED")

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch(
        "apps.node.ws.downlink.send_task_cancel",
        side_effect=RedisConnectionError("redis unavailable"),
    )
    def test_redis_cancel_failure_does_not_roll_back_terminal_state(
        self, _send_cancel, _push, _set_info
    ):
        task = cancel_task(task_id=self.task().id, reason="user canceled")

        self.assertIsNotNone(task)
        self.assertEqual(task.status, NodeTask.Status.CANCELED)
        task.refresh_from_db()
        self.assertEqual(task.status, NodeTask.Status.CANCELED)

    @patch(
        "apps.node.services.internal.task.redis_store.ws_recovery_hold_active",
        return_value=False,
    )
    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task._send_cancel_command")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    @patch(
        "apps.node.services.internal.task_offline_reconcile.sync_platform_tasks_for_node_task"
    )
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
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_legacy_agent_keeps_send_then_running(self, _route, _send, _set_info):
        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])
        task = deliver_agent_task(task=self.task())
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        self.assertIsNone(task.accepted_at)
        self.assertNotIn("_delivery_protocol", task.result)

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_legacy_delivery_does_not_overwrite_fast_terminal_result(
        self, _route, _set_info
    ):
        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])
        task = self.task()

        def complete_during_send(*, task):
            NodeTask.objects.filter(pk=task.pk).update(
                status=NodeTask.Status.SUCCESS,
                accepted_at=timezone.now(),
                result={"completed": True},
            )

        with patch(
            "apps.node.services.internal.task._send_task_command",
            side_effect=complete_during_send,
        ):
            delivered = deliver_agent_task(task=task)

        self.assertEqual(delivered.status, NodeTask.Status.SUCCESS)
        self.assertEqual(delivered.result, {"completed": True})

    @patch("apps.node.services.internal.task.redis_store.set_task_info")
    @patch(
        "apps.node.services.internal.task._node_route_state",
        return_value=_RouteState.ONLINE,
    )
    def test_legacy_transport_error_does_not_overwrite_fast_terminal_result(
        self, _route, _set_info
    ):
        self.node.metadata = {"inventory": {"capabilities": []}}
        self.node.save(update_fields=["metadata", "updated_at"])
        task = self.task()

        def complete_then_raise(*, task):
            NodeTask.objects.filter(pk=task.pk).update(
                status=NodeTask.Status.SUCCESS,
                accepted_at=timezone.now(),
                result={"completed": True},
            )
            raise RedisConnectionError("ambiguous transport failure")

        with patch(
            "apps.node.services.internal.task._send_task_command",
            side_effect=complete_then_raise,
        ):
            delivered = deliver_agent_task(task=task)

        self.assertEqual(delivered.status, NodeTask.Status.SUCCESS)
        self.assertEqual(delivered.result, {"completed": True})
