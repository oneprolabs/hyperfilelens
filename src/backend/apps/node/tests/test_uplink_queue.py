"""Tests for Agent uplink Redis stream ingest."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node import conf as node_conf
from apps.node.services.internal.redis_store import task_uplink_activity_key
from apps.node.ws.uplink_queue import (
    NODE_UPLINK_DEAD_LETTER_STREAM,
    NODE_UPLINK_RECLAIM_CURSOR,
    NODE_UPLINK_STREAM,
    UPLINK_INGEST_GROUP,
    drain_uplink_stream,
    enqueue_uplink,
    _claim_stale_entries,
    replay_dead_letter_entry,
    touch_heartbeat_fast,
)
from apps.node.ws.wire import ParsedUplink, WireType


class _StreamRedis:
    def __init__(self) -> None:
        self.groups: set[tuple[str, str]] = set()
        self.stream: list[tuple[str, dict[str, str]]] = []
        self.acked: set[str] = set()
        self.delivered: set[str] = set()
        self.deleted: set[str] = set()
        self.values: dict[str, str] = {}
        self.delivery_counts: dict[str, int] = {}
        self.added_to: list[tuple[str, str]] = []
        self._seq = 0

    def ping(self) -> bool:
        return True

    def xgroup_create(self, name, groupname, id="0", mkstream=False):
        key = (name, groupname)
        if key in self.groups:
            from redis.exceptions import ResponseError

            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(key)

    def xadd(self, name, fields):
        self._seq += 1
        entry_id = f"{self._seq}-0"
        self.stream.append((entry_id, dict(fields)))
        self.added_to.append((name, entry_id))
        return entry_id

    def xreadgroup(self, group, consumer, streams, count=10, block=0):
        stream_name = next(iter(streams))
        pending = [
            (entry_id, fields)
            for entry_id, fields in self.stream
            if entry_id not in self.delivered and entry_id not in self.deleted
        ][:count]
        if not pending:
            return []
        self.delivered.update(entry_id for entry_id, _fields in pending)
        for entry_id, _fields in pending:
            self.delivery_counts[entry_id] = self.delivery_counts.get(entry_id, 0) + 1
        return [(stream_name, pending)]

    def xautoclaim(
        self,
        name,
        groupname,
        consumername,
        min_idle_time,
        start_id="0-0",
        count=10,
    ):
        pending = [
            (entry_id, fields)
            for entry_id, fields in self.stream
            if entry_id in self.delivered
            and entry_id not in self.acked
            and entry_id not in self.deleted
        ][:count]
        for entry_id, _fields in pending:
            self.delivery_counts[entry_id] = self.delivery_counts.get(entry_id, 0) + 1
        return ["0-0", pending, []]

    def xpending_range(self, name, groupname, min, max, count=1):
        if min not in self.delivery_counts:
            return []
        return [
            {
                "message_id": min,
                "times_delivered": self.delivery_counts[min],
                "time_since_delivered": 0,
            }
        ]

    def xack(self, stream, group, entry_id):
        self.acked.add(entry_id)

    def xdel(self, stream, entry_id):
        self.deleted.add(entry_id)
        return 1

    def pipeline(self, transaction=True):
        return _StreamPipeline(self, transaction=transaction)

    def set(self, key, value, ex=None):
        self.values[str(key)] = str(value)
        return True

    def get(self, key):
        return self.values.get(str(key))

    def eval(self, script, numkeys, *args):
        keys = args[:numkeys]
        argv = args[numkeys:]
        if "xadd" in script and "xack" in script:
            source_stream, dead_letter_stream, marker_key = keys
            group, entry_id = argv[:2]
            dead_letter_id = self.xadd(
                dead_letter_stream,
                {
                    "source_entry_id": argv[2],
                    "payload": argv[3],
                    "deliveries": argv[4],
                    "entry_age_seconds": argv[5],
                    "error_type": argv[6],
                    "quarantined_at": argv[7],
                },
            )
            self.xack(source_stream, group, entry_id)
            self.xdel(source_stream, entry_id)
            marker_token = str(argv[8])
            raw = self.values.get(str(marker_key))
            if marker_token and raw:
                marker = json.loads(raw)
                if str(marker.get("marker_token") or "") == marker_token:
                    del self.values[str(marker_key)]
            return dead_letter_id
        if "xack" in script:
            source_stream, marker_key = keys
            group, entry_id, marker_token = argv
            raw = self.values.get(str(marker_key))
            if marker_token and raw:
                marker = json.loads(raw)
                if str(marker.get("marker_token") or "") == str(marker_token):
                    del self.values[str(marker_key)]
            self.xack(source_stream, group, entry_id)
            self.xdel(source_stream, entry_id)
            return [1, 1, 1]
        if "cjson.decode" in script:
            key = str(keys[0])
            marker_token = str(argv[0])
            raw = self.values.get(key)
            if not raw:
                return 0
            payload = json.loads(raw)
            if str(payload.get("marker_token") or "") != marker_token:
                return 0
            del self.values[key]
            return 1
        if numkeys == 3:
            live_stream, dead_letter_stream, marker_key = keys
            payload, marker_payload, _ttl, dead_letter_id = argv
            live_entry_id = self.xadd(live_stream, {"payload": payload})
            self.set(marker_key, marker_payload)
            self.xdel(dead_letter_stream, dead_letter_id)
            return live_entry_id
        live_stream, dead_letter_stream = keys
        payload, dead_letter_id = argv
        live_entry_id = self.xadd(live_stream, {"payload": payload})
        self.xdel(dead_letter_stream, dead_letter_id)
        return live_entry_id


class _StreamPipeline:
    def __init__(self, redis, *, transaction):
        self.redis = redis
        self.transaction = transaction
        self.operations = []

    def xadd(self, name, fields):
        self.operations.append(("xadd", (name, fields), {}))
        return self

    def xack(self, stream, group, entry_id):
        self.operations.append(("xack", (stream, group, entry_id), {}))
        return self

    def xdel(self, stream, entry_id):
        self.operations.append(("xdel", (stream, entry_id), {}))
        return self

    def set(self, key, value, ex=None):
        self.operations.append(("set", (key, value), {"ex": ex}))
        return self

    def execute(self):
        return [
            getattr(self.redis, operation)(*args, **kwargs)
            for operation, args, kwargs in self.operations
        ]


class UplinkQueueTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="uplink-org", name="Uplink Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-uplink",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
            last_seen_at=timezone.now(),
        )
        self.redis = _StreamRedis()
        self._patch = patch(
            "apps.node.ws.uplink_queue._redis",
            return_value=self.redis,
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self._redis_store_patch = patch(
            "apps.node.services.internal.redis_store.get_redis",
            return_value=self.redis,
        )
        self._redis_store_patch.start()
        self.addCleanup(self._redis_store_patch.stop)

    @patch("apps.node.ws.uplink_queue.redis_store.ensure_agent_location_on_heartbeat")
    @patch("apps.node.ws.uplink_queue.redis_store.touch_ws_instance_alive")
    def test_touch_heartbeat_fast_only_touches_redis(self, mock_alive, mock_ensure):
        touch_heartbeat_fast(node_id=self.node.id, session_id="session-1")
        mock_ensure.assert_called_once_with(
            agent_id=self.node.id,
            session_id="session-1",
        )
        mock_alive.assert_called_once()

    @patch("apps.node.ws.uplink.handle_uplink")
    def test_drain_uplink_stream_processes_heartbeat(self, mock_handle):
        message = ParsedUplink(
            msg_type=WireType.HEARTBEAT, heartbeat_payload={"agent_version": "1.0.0"}
        )
        enqueue_uplink(node_id=self.node.id, message=message)
        processed = drain_uplink_stream(count=10)
        self.assertEqual(processed, 1)
        mock_handle.assert_called_once()
        self.assertEqual(mock_handle.call_args.kwargs["node_id"], self.node.id)
        self.assertEqual(self.redis.deleted, {"1-0"})

    @patch("apps.node.ws.uplink.handle_uplink")
    def test_failed_projection_remains_pending_and_is_reclaimed(self, mock_handle):
        mock_handle.side_effect = [RuntimeError("database unavailable"), None]
        message = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id="task-1",
            status="success",
            result={"ok": True},
        )
        enqueue_uplink(node_id=self.node.id, message=message)

        self.assertEqual(drain_uplink_stream(count=10), 0)
        self.assertNotIn("1-0", self.redis.acked)
        self.assertEqual(drain_uplink_stream(count=10), 1)
        self.assertIn("1-0", self.redis.acked)
        self.assertIn("1-0", self.redis.deleted)

    @patch("apps.node.ws.uplink.handle_uplink")
    def test_older_projection_cannot_clear_newer_task_marker(self, mock_handle):
        progress = ParsedUplink(
            msg_type=WireType.TASK_PROGRESS,
            task_id="task-ordered-marker",
            progress={"percent": 50},
        )
        result = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id="task-ordered-marker",
            status="success",
            result={"ok": True},
        )
        enqueue_uplink(node_id=self.node.id, message=progress)
        enqueue_uplink(node_id=self.node.id, message=result)
        marker_key = task_uplink_activity_key("task-ordered-marker")
        newest_marker = self.redis.values[marker_key]

        self.assertEqual(drain_uplink_stream(count=1), 1)
        self.assertEqual(self.redis.values[marker_key], newest_marker)

        self.assertEqual(drain_uplink_stream(count=1), 1)
        self.assertNotIn(marker_key, self.redis.values)
        self.assertEqual(mock_handle.call_count, 2)

    @patch("apps.node.ws.uplink.handle_uplink")
    def test_failed_pending_entry_does_not_starve_fresh_uplink(self, mock_handle):
        mock_handle.side_effect = [
            RuntimeError("permanent projection failure"),
            RuntimeError("permanent projection failure"),
            None,
        ]
        poison = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id="task-poison",
            status="success",
            result={"ok": True},
        )
        enqueue_uplink(node_id=self.node.id, message=poison)
        self.assertEqual(drain_uplink_stream(count=1), 0)

        fresh = ParsedUplink(
            msg_type=WireType.HEARTBEAT,
            heartbeat_payload={"agent_version": "1.0.1"},
        )
        enqueue_uplink(node_id=self.node.id, message=fresh)

        self.assertEqual(drain_uplink_stream(count=2), 1)
        self.assertIn("2-0", self.redis.acked)

    @patch("apps.node.ws.uplink.handle_uplink")
    def test_persistent_projection_failure_is_quarantined(self, mock_handle):
        mock_handle.side_effect = RuntimeError("deterministic projection failure")
        message = ParsedUplink(
            msg_type=WireType.TASK_RESULT,
            task_id="task-dlq",
            status="success",
            result={"ok": True},
        )
        enqueue_uplink(node_id=self.node.id, message=message)

        with (
            patch.object(node_conf, "UPLINK_DLQ_MIN_DELIVERIES", 2),
            patch.object(node_conf, "UPLINK_DLQ_MIN_AGE_SECONDS", 0),
        ):
            self.assertEqual(drain_uplink_stream(count=1), 0)
            self.assertEqual(drain_uplink_stream(count=1), 0)

        self.assertIn("1-0", self.redis.acked)
        self.assertIn("1-0", self.redis.deleted)
        self.assertTrue(
            any(
                name == NODE_UPLINK_DEAD_LETTER_STREAM
                for name, _id in self.redis.added_to
            )
        )
        self.assertNotIn(task_uplink_activity_key("task-dlq"), self.redis.values)

        dead_letter_id = next(
            entry_id
            for name, entry_id in self.redis.added_to
            if name == NODE_UPLINK_DEAD_LETTER_STREAM
        )
        dead_letter_fields = next(
            fields
            for entry_id, fields in self.redis.stream
            if entry_id == dead_letter_id
        )
        replayed_id = replay_dead_letter_entry(
            self.redis,
            entry_id=dead_letter_id,
            fields=dead_letter_fields,
        )

        self.assertNotEqual(replayed_id, dead_letter_id)
        self.assertIn(dead_letter_id, self.redis.deleted)
        self.assertIn(task_uplink_activity_key("task-dlq"), self.redis.values)

    def test_stream_constants(self):
        self.assertEqual(NODE_UPLINK_STREAM, "node:uplink:stream")
        self.assertEqual(UPLINK_INGEST_GROUP, "node-uplink-ingest")

    def test_reclaim_continues_from_persisted_scan_cursor(self):
        client = Mock()
        client.get.return_value = "42-0"
        client.xautoclaim.return_value = ["99-0", [], []]

        self.assertEqual(_claim_stale_entries(client, count=25), [])

        self.assertEqual(client.xautoclaim.call_args.kwargs["start_id"], "42-0")
        client.set.assert_called_once_with(NODE_UPLINK_RECLAIM_CURSOR, "99-0")
