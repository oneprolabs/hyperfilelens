"""Safety checks for explicit Redis runtime history cleanup."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.node.services.internal import redis_store


def _id_parts(entry_id: str) -> tuple[int, int]:
    milliseconds, sequence = entry_id.split("-", 1)
    return int(milliseconds), int(sequence)


class _CleanupPipeline:
    def __init__(self, client) -> None:
        self.client = client
        self.operations = []

    def ttl(self, key):
        self.operations.append(("ttl", (key,)))
        return self

    def exists(self, key):
        self.operations.append(("exists", (key,)))
        return self

    def expire(self, key, seconds):
        self.operations.append(("expire", (key, seconds)))
        return self

    def xdel(self, stream, *entry_ids):
        self.operations.append(("xdel", (stream, *entry_ids)))
        return self

    def execute(self):
        return [
            getattr(self.client, operation)(*args)
            for operation, args in self.operations
        ]


class _CleanupRedis:
    def __init__(self) -> None:
        self.key_ttls = {
            "task_stream:legacy": -1,
            "task_stream:active": -1,
        }
        self.waiters = {redis_store.task_stream_waiters_key("active")}
        self.stream = [
            ("1-0", {"payload": "one"}),
            ("2-0", {"payload": "pending"}),
            ("3-0", {"payload": "acked-after-pending"}),
            ("4-0", {"payload": "unread"}),
        ]
        self.groups = [
            {
                "name": "node-uplink-ingest",
                "lag": 1,
                "last-delivered-id": "3-0",
            }
        ]

    def pipeline(self, transaction=False):
        return _CleanupPipeline(self)

    def scan_iter(self, match, count):
        yield from list(self.key_ttls)

    def ttl(self, key):
        return self.key_ttls.get(str(key), -2)

    def exists(self, key):
        return int(str(key) in self.waiters)

    def expire(self, key, seconds):
        if str(key) not in self.key_ttls:
            return False
        self.key_ttls[str(key)] = int(seconds)
        return True

    def info(self, section):
        return {"used_memory": 4096}

    def xlen(self, stream):
        return len(self.stream)

    def xrevrange(self, stream, max="+", min="-", count=None):
        rows = list(reversed(self.stream))
        return rows[:count] if count is not None else rows

    def xinfo_groups(self, stream):
        return self.groups

    def xpending(self, stream, group):
        return {"pending": 1, "min": "2-0"}

    def xrange(self, stream, min="-", max="+", count=None):
        rows = self.stream
        if str(min).startswith("("):
            lower = _id_parts(str(min)[1:])
            rows = [row for row in rows if _id_parts(row[0]) > lower]
        elif min not in {"-", None}:
            lower = _id_parts(str(min))
            rows = [row for row in rows if _id_parts(row[0]) >= lower]
        if max not in {"+", None}:
            upper = _id_parts(str(max))
            rows = [row for row in rows if _id_parts(row[0]) <= upper]
        return rows[:count] if count is not None else rows

    def xdel(self, stream, *entry_ids):
        selected = {str(entry_id) for entry_id in entry_ids}
        before = len(self.stream)
        self.stream = [row for row in self.stream if row[0] not in selected]
        return before - len(self.stream)


class RedisRuntimeCleanupTests(SimpleTestCase):
    @patch(
        "apps.node.management.commands.cleanup_redis_runtime_backlog.redis_store.get_redis"
    )
    def test_dry_run_reports_without_mutating_redis(self, get_redis) -> None:
        client = _CleanupRedis()
        get_redis.return_value = client
        stdout = StringIO()

        call_command(
            "cleanup_redis_runtime_backlog",
            "--dry-run",
            stdout=stdout,
        )

        self.assertEqual(client.key_ttls["task_stream:legacy"], -1)
        self.assertEqual(
            [entry_id for entry_id, _fields in client.stream],
            ["1-0", "2-0", "3-0", "4-0"],
        )
        self.assertIn("task_stream_selected=1", stdout.getvalue())
        self.assertIn("uplink_selected=1", stdout.getvalue())

    @patch(
        "apps.node.management.commands.cleanup_redis_runtime_backlog.redis_store.get_redis"
    )
    def test_apply_expires_legacy_list_and_stops_before_pending(
        self, get_redis
    ) -> None:
        client = _CleanupRedis()
        get_redis.return_value = client

        call_command(
            "cleanup_redis_runtime_backlog",
            "--apply",
            "--task-stream-ttl-seconds=3600",
            stdout=StringIO(),
        )

        self.assertEqual(client.key_ttls["task_stream:legacy"], 3600)
        self.assertEqual(client.key_ttls["task_stream:active"], -1)
        self.assertEqual(
            [entry_id for entry_id, _fields in client.stream],
            ["2-0", "3-0", "4-0"],
        )

    @patch(
        "apps.node.management.commands.cleanup_redis_runtime_backlog.redis_store.get_redis"
    )
    def test_refuses_retained_stream_without_consumer_groups(self, get_redis) -> None:
        client = _CleanupRedis()
        client.groups = []
        get_redis.return_value = client

        with self.assertRaisesRegex(CommandError, "no consumer group"):
            call_command(
                "cleanup_redis_runtime_backlog",
                "--apply",
                stdout=StringIO(),
            )

    @patch(
        "apps.node.management.commands.cleanup_redis_runtime_backlog.redis_store.get_redis"
    )
    def test_apply_does_not_delete_entry_appended_after_batch_ceiling(
        self, get_redis
    ) -> None:
        client = _CleanupRedis()
        client.stream = [("1-0", {"payload": "acknowledged"})]
        client.groups = [
            {
                "name": "node-uplink-ingest",
                "lag": 0,
                "last-delivered-id": "1-0",
            }
        ]
        client.xpending = lambda _stream, _group: {"pending": 0}
        original_xrange = client.xrange
        appended = False

        def append_before_candidate_scan(stream, min="-", max="+", count=None):
            nonlocal appended
            if not appended and min == "-":
                client.stream.append(("2-0", {"payload": "new-unread"}))
                appended = True
            return original_xrange(stream, min=min, max=max, count=count)

        client.xrange = append_before_candidate_scan
        get_redis.return_value = client

        call_command(
            "cleanup_redis_runtime_backlog",
            "--apply",
            stdout=StringIO(),
        )

        self.assertEqual(
            [entry_id for entry_id, _fields in client.stream],
            ["2-0"],
        )
