"""Synchronous Agent task waiting semantics."""

from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal import redis_store
from apps.node.services.internal.agent_task import (
    AgentTaskSyncResult,
    _attach_node_kopia_cache_policy,
    run_agent_task_sync,
    wait_for_agent_task,
)
from apps.node.services.internal.task import (
    deliver_agent_task,
    protect_task_delivery_payload,
    redeliver_pending_agent_task,
)


class AgentTaskSyncWaitTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="agent-task-sync-org", name="Agent Task Sync Org"
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-task-sync",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
        )
        self.task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="explorer.list",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

    def test_repository_task_receives_default_kopia_cache_policy(self):
        payload = _attach_node_kopia_cache_policy(
            node=self.node,
            payload={"repository": {"id": 9, "type": "s3"}},
        )

        self.assertEqual(payload["kopia_cache_size_mb"], 1024)

    def test_repository_task_preserves_explicit_zero_cache_policy(self):
        self.node.metadata = {"perf_settings": {"kopiaCacheMb": 0}}
        payload = _attach_node_kopia_cache_policy(
            node=self.node,
            payload={"repository": {"id": 9, "type": "s3"}},
        )

        self.assertEqual(payload["kopia_cache_size_mb"], 0)

    def test_invalid_repository_cache_policy_uses_safe_default(self):
        for value in (True, "2048", -1, 65537):
            self.node.metadata = {"perf_settings": {"kopiaCacheMb": value}}
            payload = _attach_node_kopia_cache_policy(
                node=self.node,
                payload={"repository": {"id": 9, "type": "s3"}},
            )

            self.assertEqual(payload["kopia_cache_size_mb"], 1024)

    def test_payload_without_repository_is_not_modified(self):
        payload = {"path": "/tmp"}
        self.assertIs(
            _attach_node_kopia_cache_policy(
                node=self.node,
                payload=payload,
            ),
            payload,
        )

    def test_gateway_repository_payload_is_not_modified(self):
        self.node.role = NodeRole.GATEWAY
        payload = {"repository": {"id": 9, "type": "s3"}}

        self.assertIs(
            _attach_node_kopia_cache_policy(node=self.node, payload=payload),
            payload,
        )

    @patch(
        "apps.node.services.internal.agent_task.redis_store.task_stream_waiter",
        return_value=nullcontext(True),
    )
    @patch("apps.node.services.internal.agent_task.redis_store.bpop_task_stream")
    def test_wait_ignores_progress_until_terminal_result(self, mock_bpop, _waiter):
        def pop_stream(*, task_id: str, timeout_seconds: int):
            if mock_bpop.call_count == 1:
                return {
                    "task_id": task_id,
                    "status": NodeTask.Status.RUNNING,
                    "progress": {"phase": "listing"},
                }
            NodeTask.objects.filter(pk=self.task.id).update(
                status=NodeTask.Status.SUCCESS,
                result={"entries": [{"name": "data", "path": "/data", "is_dir": True}]},
            )
            return {
                "task_id": task_id,
                "status": NodeTask.Status.SUCCESS,
                "result": {
                    "entries": [{"name": "data", "path": "/data", "is_dir": True}]
                },
            }

        mock_bpop.side_effect = pop_stream

        outcome = wait_for_agent_task(task_id=self.task.id, timeout_seconds=5)

        self.assertFalse(outcome.timed_out)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.result["entries"][0]["path"], "/data")
        self.assertEqual(mock_bpop.call_count, 2)

    @patch(
        "apps.node.services.internal.agent_task.redis_store.task_stream_waiter",
        return_value=nullcontext(False),
    )
    @patch("apps.node.services.internal.agent_task.redis_store.bpop_task_stream")
    def test_wait_rechecks_database_when_stream_terminal_message_is_missing(
        self,
        mock_bpop,
        _waiter,
    ):
        def pop_stream(*, task_id: str, timeout_seconds: int):
            self.assertLessEqual(timeout_seconds, 5)
            NodeTask.objects.filter(pk=self.task.id).update(
                status=NodeTask.Status.SUCCESS,
                result={"entries": [{"name": "data", "path": "/data", "is_dir": True}]},
            )
            return None

        mock_bpop.side_effect = pop_stream

        outcome = wait_for_agent_task(task_id=self.task.id, timeout_seconds=3600)

        self.assertFalse(outcome.timed_out)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.result["entries"][0]["path"], "/data")
        self.assertEqual(mock_bpop.call_count, 1)

    @patch(
        "apps.node.services.internal.agent_task.redis_store.task_stream_waiter",
        return_value=nullcontext(True),
    )
    @patch("apps.node.services.internal.agent_task.time.sleep")
    @patch("apps.node.services.internal.agent_task.redis_store.bpop_task_stream")
    def test_wait_continues_after_empty_stream_when_task_is_still_running(
        self,
        mock_bpop,
        sleep,
        _waiter,
    ):
        def pop_stream(*, task_id: str, timeout_seconds: int):
            self.assertLessEqual(timeout_seconds, 5)
            if mock_bpop.call_count == 1:
                return None
            NodeTask.objects.filter(pk=self.task.id).update(
                status=NodeTask.Status.SUCCESS,
                result={"entries": [{"name": "data", "path": "/data", "is_dir": True}]},
            )
            return None

        mock_bpop.side_effect = pop_stream

        outcome = wait_for_agent_task(task_id=self.task.id, timeout_seconds=6)

        self.assertFalse(outcome.timed_out)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.result["entries"][0]["path"], "/data")
        self.assertEqual(mock_bpop.call_count, 2)
        sleep.assert_called_once()

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_task_stream_redis_socket_timeout_returns_no_message(self, mock_get_redis):
        class RedisClient:
            def blpop(self, key, timeout):
                raise RedisTimeoutError("Timeout reading from socket")

        mock_get_redis.return_value = RedisClient()

        message = redis_store.bpop_task_stream(
            task_id=str(self.task.id), timeout_seconds=10
        )

        self.assertIsNone(message)

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_task_stream_redis_disconnect_returns_no_message(self, mock_get_redis):
        class RedisClient:
            def blpop(self, key, timeout):
                raise RedisConnectionError("connection lost")

        mock_get_redis.return_value = RedisClient()

        message = redis_store.bpop_task_stream(
            task_id=str(self.task.id), timeout_seconds=10
        )

        self.assertIsNone(message)


class _TaskStreamRedis:
    def __init__(self) -> None:
        self.waiters: dict[str, set[str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}

    def eval(self, script, numkeys, *args):
        keys = [str(value) for value in args[:numkeys]]
        argv = args[numkeys:]
        if "redis.call('hset'" in script:
            key = keys[0]
            waiter_token = str(argv[0])
            self.waiters.setdefault(key, set()).add(waiter_token)
            requested_ttl = int(argv[1])
            if self.ttls.get(key, -1) < requested_ttl:
                self.ttls[key] = requested_ttl
            return len(self.waiters[key])
        if "redis.call('hdel'" in script:
            waiter_key, stream_key = keys
            self.waiters.get(waiter_key, set()).discard(str(argv[0]))
            count = len(self.waiters.get(waiter_key, set()))
            if count == 0:
                self.waiters.pop(waiter_key, None)
                self.ttls.pop(waiter_key, None)
                self.lists.pop(stream_key, None)
                self.ttls.pop(stream_key, None)
                return 0
            return count
        if "redis.call('lpush'" in script:
            waiter_key, stream_key = keys
            if waiter_key not in self.waiters:
                return 0
            self.lists.setdefault(stream_key, []).insert(0, str(argv[0]))
            self.lists[stream_key] = self.lists[stream_key][: int(argv[1])]
            self.ttls[stream_key] = max(
                self.ttls[waiter_key],
                int(argv[2]),
            )
            return 1
        raise AssertionError("unexpected Lua script")


class AgentTaskDeliveryAndLeaseTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="agent-task-delivery-org",
            name="Agent Task Delivery Org",
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-task-delivery",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
        )
        self.task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="explorer.list",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now(),
        )

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_waiter_count_does_not_shorten_peer_ttl_and_last_release_cleans(
        self, get_redis
    ):
        client = _TaskStreamRedis()
        get_redis.return_value = client
        task_id = "task-lease"

        first_token = redis_store.register_task_stream_waiter(
            task_id=task_id,
            ttl_seconds=120,
        )
        second_token = redis_store.register_task_stream_waiter(
            task_id=task_id,
            ttl_seconds=30,
        )
        self.assertIsNotNone(first_token)
        self.assertIsNotNone(second_token)
        waiter_key = redis_store.task_stream_waiters_key(task_id)
        stream_key = redis_store.task_stream_key(task_id)
        self.assertEqual(len(client.waiters[waiter_key]), 2)
        self.assertEqual(client.ttls[waiter_key], 120)

        for sequence in range(node_conf.TASK_STREAM_MAX_MESSAGES + 10):
            redis_store.push_task_stream(
                task_id=task_id,
                message={"status": "running", "sequence": sequence},
            )
        self.assertEqual(
            len(client.lists[stream_key]),
            node_conf.TASK_STREAM_MAX_MESSAGES,
        )
        self.assertEqual(
            json.loads(client.lists[stream_key][0])["sequence"],
            node_conf.TASK_STREAM_MAX_MESSAGES + 9,
        )

        redis_store.unregister_task_stream_waiter(
            task_id=task_id,
            waiter_token=str(first_token),
        )
        self.assertEqual(len(client.waiters[waiter_key]), 1)
        self.assertIn(stream_key, client.lists)
        redis_store.unregister_task_stream_waiter(
            task_id=task_id,
            waiter_token=str(second_token),
        )
        self.assertNotIn(waiter_key, client.waiters)
        self.assertNotIn(stream_key, client.lists)

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_async_notification_is_not_retained_without_waiter(self, get_redis):
        client = _TaskStreamRedis()
        get_redis.return_value = client

        redis_store.push_task_stream(
            task_id="async-task",
            message={"status": "success", "result": {"ok": True}},
        )

        self.assertNotIn(
            redis_store.task_stream_key("async-task"),
            client.lists,
        )

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_expired_waiter_cannot_release_a_new_waiter(self, get_redis):
        client = _TaskStreamRedis()
        get_redis.return_value = client
        task_id = "reused-lease"
        waiter_key = redis_store.task_stream_waiters_key(task_id)

        expired_token = redis_store.register_task_stream_waiter(
            task_id=task_id,
            ttl_seconds=30,
        )
        client.waiters.pop(waiter_key)
        client.ttls.pop(waiter_key)
        current_token = redis_store.register_task_stream_waiter(
            task_id=task_id,
            ttl_seconds=30,
        )

        redis_store.unregister_task_stream_waiter(
            task_id=task_id,
            waiter_token=str(expired_token),
        )

        self.assertEqual(client.waiters[waiter_key], {str(current_token)})

    def test_sync_waiter_is_registered_before_agent_delivery(self):
        events: list[str] = []
        delivered: list[NodeTask] = []

        @contextmanager
        def waiter(**_kwargs):
            events.append("waiter-enter")
            try:
                yield True
            finally:
                events.append("waiter-exit")

        def deliver(*, task, delivery_payload):
            events.append("deliver")
            task.status = NodeTask.Status.SUCCESS
            delivered.append(task)
            return task

        def wait(**_kwargs):
            events.append("wait")
            return AgentTaskSyncResult(
                task=delivered[0],
                stream_message=None,
                timed_out=False,
            )

        with (
            patch(
                "apps.node.services.internal.agent_task.redis_store.task_stream_waiter",
                side_effect=waiter,
            ),
            patch(
                "apps.node.services.internal.agent_task.deliver_agent_task",
                side_effect=deliver,
            ),
            patch(
                "apps.node.services.internal.agent_task._wait_for_agent_task",
                side_effect=wait,
            ),
        ):
            outcome = run_agent_task_sync(
                org=self.org,
                node_id=self.node.id,
                kind="explorer.list",
            )

        self.assertTrue(outcome.ok)
        self.assertEqual(events, ["waiter-enter", "deliver", "wait", "waiter-exit"])

    @patch("apps.node.services.internal.task._schedule_agent_task_redelivery")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_deliver_waits_when_agent_location_is_stale_but_recently_seen(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_schedule_redelivery,
    ):
        class RedisClient:
            def exists(self, key):
                return False

            def set(self, *args, **kwargs):
                return True

        self.task.status = NodeTask.Status.PENDING
        self.task.save(update_fields=["status"])
        mock_get_agent_location.return_value = "stale-ws"
        mock_get_redis.return_value = RedisClient()

        task = deliver_agent_task(task=self.task)

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.last_error, "agent websocket is reconnecting")
        mock_schedule_redelivery.assert_called_once()

    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task.redis_store.clear_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_deliver_fails_when_agent_location_is_stale_beyond_task_grace(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_clear_agent_location,
        mock_push_task_stream,
    ):
        class RedisClient:
            def exists(self, key):
                return False

            def set(self, *args, **kwargs):
                return True

        NodeTask.objects.filter(pk=self.task.pk).update(
            created_at=timezone.now()
            - timezone.timedelta(
                seconds=node_conf.TASK_ROUTE_RECONNECT_GRACE_SECONDS + 1
            ),
            status=NodeTask.Status.PENDING,
        )
        self.task.refresh_from_db()
        mock_get_agent_location.return_value = "stale-ws"
        mock_get_redis.return_value = RedisClient()

        task = deliver_agent_task(task=self.task)

        self.assertEqual(task.status, NodeTask.Status.FAILED)
        self.assertEqual(task.last_error, "agent websocket is not routable")
        self.node.refresh_from_db()
        self.assertEqual(self.node.availability, Node.Availability.ONLINE)
        mock_clear_agent_location.assert_called_once_with(agent_id=self.node.id)
        mock_push_task_stream.assert_called_once()

    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task.redis_store.clear_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_deliver_fails_immediately_when_node_is_offline(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_clear_agent_location,
        mock_push_task_stream,
    ):
        class RedisClient:
            def exists(self, key):
                return False

            def set(self, *args, **kwargs):
                return True

        self.node.availability = Node.Availability.OFFLINE
        self.node.save(update_fields=["availability"])
        self.task.status = NodeTask.Status.PENDING
        self.task.save(update_fields=["status"])
        self.task.refresh_from_db()
        mock_get_agent_location.return_value = "stale-ws"
        mock_get_redis.return_value = RedisClient()

        task = deliver_agent_task(task=self.task)

        self.assertEqual(task.status, NodeTask.Status.FAILED)
        self.assertEqual(task.last_error, "agent websocket is not routable")
        mock_clear_agent_location.assert_called_once_with(agent_id=self.node.id)
        mock_push_task_stream.assert_called_once()

    @patch("apps.node.services.internal.task._schedule_agent_task_redelivery")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_lifecycle_delivery_waits_through_short_offline_flap(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_schedule_redelivery,
    ):
        class RedisClient:
            def exists(self, key):
                return False

            def set(self, *args, **kwargs):
                return True

        NodeTask.objects.filter(pk=self.task.pk).update(
            kind="agent.upgrade",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            status=NodeTask.Status.PENDING,
        )
        self.node.availability = Node.Availability.OFFLINE
        self.node.save(update_fields=["availability"])
        self.task.refresh_from_db()
        mock_get_agent_location.return_value = "stale-ws"
        mock_get_redis.return_value = RedisClient()

        task = deliver_agent_task(task=self.task)

        self.assertEqual(task.status, NodeTask.Status.PENDING)
        self.assertEqual(task.last_error, "agent websocket is reconnecting")
        mock_schedule_redelivery.assert_called_once_with(task=task)

    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task.redis_store.clear_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_lifecycle_delivery_fails_after_pre_dispatch_route_grace(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_clear_agent_location,
        mock_push_task_stream,
    ):
        class RedisClient:
            def exists(self, key):
                return False

            def set(self, *args, **kwargs):
                return True

        NodeTask.objects.filter(pk=self.task.pk).update(
            kind="agent.upgrade",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            status=NodeTask.Status.PENDING,
            created_at=timezone.now()
            - timezone.timedelta(
                seconds=node_conf.TASK_ROUTE_RECONNECT_GRACE_SECONDS + 1
            ),
        )
        self.node.availability = Node.Availability.OFFLINE
        self.node.save(update_fields=["availability"])
        self.task.refresh_from_db()
        mock_get_agent_location.return_value = "stale-ws"
        mock_get_redis.return_value = RedisClient()

        task = deliver_agent_task(task=self.task)

        self.assertEqual(task.status, NodeTask.Status.FAILED)
        self.assertEqual(task.last_error, "agent websocket is not routable")
        mock_clear_agent_location.assert_called_once_with(agent_id=self.node.id)
        mock_push_task_stream.assert_called_once()

    @patch("apps.node.services.internal.task._schedule_agent_task_redelivery")
    @patch("apps.node.services.internal.task.redis_store.push_task_stream")
    @patch("apps.node.services.internal.task.redis_store.clear_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_lifecycle_delivery_never_retries_after_command_was_sent(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_clear_agent_location,
        mock_push_task_stream,
        mock_schedule_redelivery,
    ):
        class RedisClient:
            def exists(self, key):
                return False

            def set(self, *args, **kwargs):
                return True

        NodeTask.objects.filter(pk=self.task.pk).update(
            kind="agent.upgrade",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            status=NodeTask.Status.PENDING,
            dispatched_at=timezone.now(),
            delivery_attempt_count=1,
        )
        self.node.availability = Node.Availability.OFFLINE
        self.node.save(update_fields=["availability"])
        self.task.refresh_from_db()
        mock_get_agent_location.return_value = "stale-ws"
        mock_get_redis.return_value = RedisClient()

        task = deliver_agent_task(task=self.task)

        self.assertEqual(task.status, NodeTask.Status.FAILED)
        mock_clear_agent_location.assert_called_once_with(agent_id=self.node.id)
        mock_push_task_stream.assert_called_once()
        mock_schedule_redelivery.assert_not_called()

    @patch("apps.node.services.internal.task._send_task_command")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_redeliver_pending_task_sends_when_route_recovers(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_send_task_command,
    ):
        class RedisClient:
            def exists(self, key):
                return key != redis_store.ws_recovery_hold_key()

            def set(self, *args, **kwargs):
                return True

        self.task.status = NodeTask.Status.PENDING
        self.task.last_error = "agent websocket is reconnecting"
        self.task.save(update_fields=["status", "last_error"])
        mock_get_agent_location.return_value = "live-ws"
        mock_get_redis.return_value = RedisClient()

        task = redeliver_pending_agent_task(task_id=self.task.id)

        self.assertIsNotNone(task)
        self.assertEqual(task.status, NodeTask.Status.RUNNING)
        mock_send_task_command.assert_called_once()

    @patch("apps.node.services.internal.task._send_task_command")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_redelivery_decrypts_delivery_payload_without_persisting_plaintext(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_send_task_command,
    ):
        class RedisClient:
            def exists(self, key):
                return key != redis_store.ws_recovery_hold_key()

            def set(self, *_args, **_kwargs):
                return True

        delivered_payload = {}

        def capture_payload(*, task):
            delivered_payload.update(task.payload)

        self.task.status = NodeTask.Status.PENDING
        self.task.payload = protect_task_delivery_payload(
            delivery_payload={"nas": {"username": "user", "password": "plain-secret"}},
            persisted_payload={"nas": {"username": "user"}},
        )
        self.task.save(update_fields=["status", "payload"])
        mock_get_agent_location.return_value = "live-ws"
        mock_get_redis.return_value = RedisClient()
        mock_send_task_command.side_effect = capture_payload

        task = redeliver_pending_agent_task(task_id=self.task.id)

        self.assertIsNotNone(task)
        self.assertEqual(delivered_payload["nas"]["password"], "plain-secret")
        self.task.refresh_from_db()
        self.assertNotIn("password", self.task.payload["nas"])
        self.assertNotIn("plain-secret", str(self.task.payload))

    @patch("apps.node.services.internal.task._send_task_command")
    @patch("apps.node.services.internal.task.redis_store.get_agent_session")
    @patch("apps.node.services.internal.task.redis_store.get_agent_location")
    @patch("apps.node.services.internal.task.redis_store.get_redis")
    def test_plain_upgrade_payload_does_not_leak_session_baseline(
        self,
        mock_get_redis,
        mock_get_agent_location,
        mock_get_agent_session,
        mock_send_task_command,
    ):
        class RedisClient:
            def exists(self, key):
                return key != redis_store.ws_recovery_hold_key()

            def set(self, *_args, **_kwargs):
                return True

        task = NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=NodeTask.Status.PENDING,
            payload={"target_version": "1.2.0"},
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
        )
        mock_get_agent_location.return_value = "live-ws"
        mock_get_agent_session.return_value = "session-live"
        mock_get_redis.return_value = RedisClient()

        def capture(*, task):
            self.assertNotIn("pre_upgrade_session_id", task.payload)

        mock_send_task_command.side_effect = capture

        delivered = deliver_agent_task(task=task)

        self.assertEqual(delivered.status, NodeTask.Status.RUNNING)
        task.refresh_from_db()
        self.assertEqual(task.payload["pre_upgrade_session_id"], "session-live")

    @patch("apps.node.services.internal.task._send_task_command")
    def test_redeliver_terminal_task_is_noop(self, mock_send_task_command):
        self.task.status = NodeTask.Status.SUCCESS
        self.task.save(update_fields=["status"])

        task = redeliver_pending_agent_task(task_id=self.task.id)

        self.assertIsNotNone(task)
        self.assertEqual(task.status, NodeTask.Status.SUCCESS)
        mock_send_task_command.assert_not_called()
