"""Tests for Platform Ops queue and uplink backlog snapshots."""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from redis.exceptions import ResponseError

from common.ops import runtime_backlog

_uplink_group_lag = runtime_backlog._uplink_group_lag


class RuntimeBacklogTests(SimpleTestCase):
    def setUp(self) -> None:
        self.group_lag = patch.object(
            runtime_backlog,
            "_uplink_group_lag",
            return_value=(0, 0.0),
        ).start()
        self.memory = patch.object(
            runtime_backlog,
            "_redis_memory_snapshot",
            return_value=(0, 0, 0.0),
        ).start()
        self.task_streams = patch.object(
            runtime_backlog,
            "_task_stream_stats",
            return_value=(0, 0),
        ).start()
        self.addCleanup(patch.stopall)

    @patch.object(runtime_backlog.redis_store, "get_redis")
    @patch.object(runtime_backlog, "stream_entry_age_seconds", return_value=90.0)
    def test_large_or_old_backlog_is_degraded(self, entry_age, get_redis) -> None:
        redis = Mock()
        redis.llen.side_effect = lambda name: 700 if name == "node.ingest" else 0
        redis.xlen.side_effect = (
            lambda name: 30 if name == runtime_backlog.NODE_UPLINK_STREAM else 0
        )
        redis.xpending.return_value = {"pending": 4, "min": "1000-0"}
        get_redis.return_value = redis

        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertEqual(snapshot["queue_depths"]["node.ingest"], 700)
        self.assertEqual(snapshot["uplink_oldest_pending_seconds"], 90.0)
        self.assertEqual(len(snapshot["warnings"]), 2)
        entry_age.assert_called_once_with("1000-0")

    @patch.object(runtime_backlog.redis_store, "get_redis")
    def test_dead_letters_degrade_backlog_health(self, get_redis) -> None:
        redis = Mock()
        redis.llen.return_value = 0
        redis.xlen.side_effect = (
            lambda name: 3
            if name == runtime_backlog.NODE_UPLINK_DEAD_LETTER_STREAM
            else 0
        )
        redis.xpending.side_effect = ResponseError("NOGROUP no such key")
        get_redis.return_value = redis

        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertEqual(snapshot["uplink_dead_letter"], 3)
        self.assertIn("dead-letter", snapshot["warnings"][0])

    @patch.object(runtime_backlog.redis_store, "get_redis", return_value=None)
    def test_redis_outage_returns_sanitized_error(self, _get_redis) -> None:
        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "error")
        self.assertEqual(snapshot["error"], "Redis is unavailable.")

    @patch.object(runtime_backlog.redis_store, "get_redis")
    def test_missing_uplink_group_is_an_empty_healthy_queue(self, get_redis) -> None:
        redis = Mock()
        redis.llen.return_value = 0
        redis.xlen.return_value = 0
        redis.xpending.side_effect = ResponseError("NOGROUP no such key")
        get_redis.return_value = redis

        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["uplink_pending"], 0)
        self.assertEqual(snapshot["uplink_dead_letter"], 0)

    @patch.object(runtime_backlog.redis_store, "get_redis")
    def test_unread_lag_degrades_even_when_pending_is_zero(self, get_redis) -> None:
        redis = Mock()
        redis.llen.return_value = 0
        redis.xlen.side_effect = (
            lambda name: 2_000 if name == runtime_backlog.NODE_UPLINK_STREAM else 0
        )
        redis.xpending.return_value = {"pending": 0}
        get_redis.return_value = redis
        self.group_lag.return_value = (1_500, 90.0)

        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertEqual(snapshot["uplink_pending"], 0)
        self.assertEqual(snapshot["uplink_lag"], 1_500)
        self.assertTrue(any("unread" in warning for warning in snapshot["warnings"]))

    @patch.object(runtime_backlog.redis_store, "get_redis")
    def test_memory_and_legacy_task_streams_degrade_health(self, get_redis) -> None:
        redis = Mock()
        redis.llen.return_value = 0
        redis.xlen.return_value = 0
        redis.xpending.return_value = {"pending": 0}
        get_redis.return_value = redis
        self.memory.return_value = (850, 1_000, 0.85)
        self.task_streams.return_value = (12, 7)

        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertEqual(snapshot["redis_memory_ratio"], 0.85)
        self.assertEqual(snapshot["task_stream_keys_without_ttl"], 7)
        self.assertEqual(len(snapshot["warnings"]), 2)

    @patch.object(runtime_backlog.redis_store, "get_redis")
    def test_acknowledged_history_degrades_backlog_health(self, get_redis) -> None:
        redis = Mock()
        redis.llen.return_value = 0
        redis.xlen.side_effect = (
            lambda name: 700 if name == runtime_backlog.NODE_UPLINK_STREAM else 0
        )
        redis.xpending.return_value = {"pending": 0}
        get_redis.return_value = redis

        snapshot = runtime_backlog.runtime_backlog_snapshot()

        self.assertEqual(snapshot["status"], "degraded")
        self.assertEqual(snapshot["uplink_acknowledged_history"], 700)
        self.assertIn("already-acknowledged", snapshot["warnings"][0])

    @patch.object(runtime_backlog, "stream_entry_age_seconds", return_value=75.0)
    def test_group_lag_uses_first_unread_entry_age(self, entry_age) -> None:
        redis = Mock()
        redis.xinfo_groups.return_value = [
            {
                "name": runtime_backlog.UPLINK_INGEST_GROUP,
                "lag": 3,
                "last-delivered-id": "1000-0",
            }
        ]
        redis.xrange.return_value = [("2000-0", {"payload": "next"})]

        lag, oldest = _uplink_group_lag(
            redis,
            stream_length=10,
        )

        self.assertEqual(lag, 3)
        self.assertEqual(oldest, 75.0)
        redis.xrange.assert_called_once_with(
            runtime_backlog.NODE_UPLINK_STREAM,
            min="(1000-0",
            max="+",
            count=1,
        )
        entry_age.assert_called_once_with("2000-0")

    @patch.object(runtime_backlog, "stream_entry_age_seconds", return_value=25.0)
    def test_group_lag_preserves_zero_entries_read(self, entry_age) -> None:
        redis = Mock()
        redis.xinfo_groups.return_value = [
            {
                "name": runtime_backlog.UPLINK_INGEST_GROUP,
                "lag": None,
                "entries-read": 0,
                "last-delivered-id": "0-0",
            }
        ]
        redis.xrange.return_value = [("1000-0", {"payload": "first"})]

        lag, oldest = _uplink_group_lag(
            redis,
            stream_length=10,
        )

        self.assertEqual(lag, 10)
        self.assertEqual(oldest, 25.0)
        entry_age.assert_called_once_with("1000-0")
