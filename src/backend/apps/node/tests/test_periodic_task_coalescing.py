"""Tests for high-frequency node maintenance coalescing."""

from contextlib import contextmanager
from threading import Event
from unittest.mock import patch
from unittest.mock import Mock

from django.test import SimpleTestCase
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.node import conf as node_conf
from apps.node.services.internal import redis_store
from apps.node.tasks.lifecycle import advance_active_lifecycle_nodes
from apps.node.tasks.periodic_tasks import register_periodic_tasks
from apps.node.tasks.uplink_ingest import ingest_node_uplink_streams
from common.scheduling.registry import TASK_REGISTRY


@contextmanager
def _lease(acquired: bool, *, refresh: bool = True):
    lease = Mock()
    lease.__bool__ = Mock(return_value=acquired)
    lease.refresh.return_value = refresh
    yield lease


@contextmanager
def _lease_states(*states: bool):
    lease = Mock()
    lease.__bool__ = Mock(side_effect=states)
    yield lease


class PeriodicTaskCoalescingTests(SimpleTestCase):
    def setUp(self) -> None:
        TASK_REGISTRY.clear()
        self.addCleanup(TASK_REGISTRY.clear)

    def test_high_frequency_registry_entries_expire(self) -> None:
        register_periodic_tasks()

        ingest = TASK_REGISTRY._entries["node_ingest_uplink_streams"]
        lifecycle = TASK_REGISTRY._entries["node_advance_active_lifecycle_nodes"]
        availability = TASK_REGISTRY._entries["node_reconcile_availability"]
        self.assertEqual(
            ingest["expire_seconds"],
            node_conf.UPLINK_INGEST_EXPIRE_SECONDS,
        )
        self.assertEqual(
            lifecycle["expire_seconds"],
            node_conf.LIFECYCLE_ADVANCE_EXPIRE_SECONDS,
        )
        self.assertEqual(
            availability["schedule"],
            node_conf.STALE_NODE_RECONCILE_INTERVAL_SECONDS,
        )
        self.assertEqual(availability["kwargs"], {"limit": 200})

    @patch(
        "apps.node.tasks.uplink_ingest.redis_store.periodic_lease",
        return_value=_lease(False),
    )
    @patch("apps.node.tasks.uplink_ingest.drain_uplink_stream")
    def test_duplicate_ingest_wakeup_is_coalesced(self, drain, _lease_patch) -> None:
        result = ingest_node_uplink_streams.run()

        self.assertEqual(result, {"processed": 0, "coalesced": 1})
        drain.assert_not_called()

    @patch(
        "apps.node.tasks.uplink_ingest.redis_store.periodic_lease",
        return_value=_lease_states(True, False),
    )
    @patch("apps.node.tasks.uplink_ingest.drain_uplink_stream")
    def test_ingest_stops_before_next_batch_after_lease_loss(
        self,
        drain,
        _lease_patch,
    ) -> None:
        result = ingest_node_uplink_streams.run()

        self.assertEqual(
            result,
            {"processed": 0, "coalesced": 0, "lease_lost": 1},
        )
        drain.assert_not_called()

    @patch(
        "apps.node.tasks.lifecycle.redis_store.periodic_lease",
        return_value=_lease(False),
    )
    def test_duplicate_lifecycle_wakeup_is_coalesced(self, _lease_patch) -> None:
        result = advance_active_lifecycle_nodes.run()

        self.assertEqual(result, {"advanced": 0, "coalesced": 1})

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_unacquired_lease_does_not_suppress_body_exception(self, get_redis) -> None:
        redis = Mock()
        redis.set.return_value = False
        get_redis.return_value = redis

        with self.assertRaisesRegex(RuntimeError, "body failed"):
            with redis_store.periodic_lease(name="busy", ttl_seconds=10):
                raise RuntimeError("body failed")

    @patch("apps.node.services.internal.redis_store.get_redis", return_value=None)
    def test_redis_outage_fails_lease_closed(self, _get_redis) -> None:
        with redis_store.periodic_lease(name="unavailable", ttl_seconds=10) as lease:
            self.assertFalse(lease)
            self.assertFalse(lease.refresh())

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_lease_refresh_and_release_require_current_token(self, get_redis) -> None:
        redis = Mock()
        redis.set.return_value = True
        redis.eval.side_effect = [1, 1]
        get_redis.return_value = redis

        with redis_store.periodic_lease(name="renewable", ttl_seconds=30) as lease:
            self.assertTrue(lease)
            self.assertTrue(lease.refresh())

        self.assertEqual(redis.eval.call_count, 2)
        refresh_args = redis.eval.call_args_list[0].args
        self.assertEqual(refresh_args[-1], 30)

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_lease_heartbeat_renews_while_body_is_running(self, get_redis) -> None:
        redis = Mock()
        redis.set.return_value = True
        renewed = Event()

        def eval_script(script, *_args):
            if "expire" in script:
                renewed.set()
            return 1

        redis.eval.side_effect = eval_script
        get_redis.return_value = redis

        with redis_store.periodic_lease(name="heartbeat", ttl_seconds=1) as lease:
            self.assertTrue(lease)
            self.assertTrue(renewed.wait(timeout=2))

        self.assertGreaterEqual(redis.eval.call_count, 2)

    @patch(
        "apps.node.services.internal.redis_store._broker_url",
        return_value="redis://test",
    )
    @patch("apps.node.services.internal.redis_store.redis.Redis.from_url")
    def test_failed_ping_does_not_cache_unverified_client(
        self,
        from_url,
        _broker_url,
    ) -> None:
        client = Mock()
        client.ping.side_effect = ConnectionError("redis unavailable")
        from_url.return_value = client
        previous_client = redis_store._client
        redis_store._client = None
        self.addCleanup(setattr, redis_store, "_client", previous_client)

        self.assertIsNone(redis_store.get_redis())
        self.assertIsNone(redis_store.get_redis())

        self.assertEqual(from_url.call_count, 2)

    @patch(
        "apps.node.tasks.lifecycle.redis_store.periodic_lease",
        return_value=_lease(True, refresh=False),
    )
    @patch("apps.node.tasks.lifecycle.NodeTask.objects.filter")
    def test_lifecycle_stops_when_lease_renewal_is_lost(
        self,
        node_task_filter,
        _lease_patch,
    ) -> None:
        node_task_filter.return_value.values_list.return_value.distinct.return_value = [
            1
        ]

        result = advance_active_lifecycle_nodes.run()

        self.assertEqual(
            result,
            {"advanced": 0, "coalesced": 0, "lease_lost": 1},
        )

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_watchdog_uplink_markers_are_batch_loaded(self, get_redis) -> None:
        redis = Mock()
        pipeline = Mock()
        pipeline.execute.return_value = [
            '{"message_type":"task.result","received_at":123.0}',
            None,
        ]
        redis.pipeline.return_value = pipeline
        get_redis.return_value = redis

        result = redis_store.get_task_uplink_activities(task_ids=["one", "two"])

        self.assertEqual(result["one"]["message_type"], "task.result")
        self.assertNotIn("two", result)
        redis.pipeline.assert_called_once_with(transaction=False)
        self.assertEqual(pipeline.get.call_count, 2)

    @patch("apps.node.services.internal.redis_store.get_redis")
    def test_task_info_projection_fails_open_during_redis_outage(
        self, get_redis
    ) -> None:
        redis = Mock()
        redis.set.side_effect = RedisConnectionError("redis unavailable")
        redis.get.side_effect = RedisConnectionError("redis unavailable")
        get_redis.return_value = redis

        redis_store.set_task_info(task_id="task-1", data={"status": "running"})

        self.assertIsNone(redis_store.get_task_info(task_id="task-1"))
