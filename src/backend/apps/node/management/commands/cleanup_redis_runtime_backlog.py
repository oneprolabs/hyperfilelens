"""Safely expire legacy task notifications and delete acknowledged uplink history."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from redis.exceptions import RedisError, ResponseError

from apps.node.services.internal import redis_store
from apps.node.ws.uplink_queue import NODE_UPLINK_STREAM


def _stream_id_parts(entry_id: str) -> tuple[int, int]:
    try:
        milliseconds, sequence = str(entry_id).split("-", 1)
        return int(milliseconds), int(sequence)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"Invalid Redis Stream entry ID: {entry_id}") from exc


def _minimum_id(entry_ids: list[str]) -> str | None:
    if not entry_ids:
        return None
    return min(entry_ids, key=_stream_id_parts)


class Command(BaseCommand):
    help = (
        "Expire legacy task_stream Lists and remove only Agent uplink entries "
        "that are safe for every consumer group. Dry-run is the default."
    )

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Report changes without modifying Redis (default).",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Apply one bounded cleanup batch.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Process at most this many keys or Stream entries (1-1000).",
        )
        parser.add_argument(
            "--task-stream-ttl-seconds",
            type=int,
            default=86_400,
            help="Transitional TTL assigned to legacy notification Lists.",
        )

    @staticmethod
    def _memory_used(client) -> int:
        try:
            return int((client.info("memory") or {}).get("used_memory", 0))
        except (RedisError, AttributeError, TypeError, ValueError):
            return 0

    @staticmethod
    def _legacy_task_stream_keys(client, *, batch_size: int) -> tuple[int, list[str]]:
        total_without_ttl = 0
        eligible: list[str] = []
        batch: list[str] = []

        def inspect(keys: list[str]) -> None:
            nonlocal total_without_ttl
            if not keys:
                return
            pipeline = client.pipeline(transaction=False)
            for key in keys:
                task_id = key.removeprefix("task_stream:")
                pipeline.ttl(key)
                pipeline.exists(redis_store.task_stream_waiters_key(task_id))
            results = pipeline.execute()
            for index, key in enumerate(keys):
                ttl = int(results[index * 2])
                has_waiter = bool(results[index * 2 + 1])
                if ttl != -1:
                    continue
                total_without_ttl += 1
                if not has_waiter and len(eligible) < batch_size:
                    eligible.append(key)

        for raw_key in client.scan_iter(match="task_stream:*", count=200):
            batch.append(str(raw_key))
            if len(batch) == 200:
                inspect(batch)
                batch = []
        inspect(batch)
        return total_without_ttl, eligible

    @staticmethod
    def _safe_uplink_entries(
        client,
        *,
        batch_size: int,
    ) -> tuple[int, list[str], int, int]:
        stream_length = int(client.xlen(NODE_UPLINK_STREAM))
        if stream_length == 0:
            return 0, [], 0, 0
        # Freeze this batch before inspecting groups so concurrent XADD entries
        # can never become cleanup candidates from a stale group snapshot.
        newest_rows = client.xrevrange(
            NODE_UPLINK_STREAM,
            max="+",
            min="-",
            count=1,
        )
        if not newest_rows:
            return stream_length, [], 0, 0
        cleanup_ceiling = str(newest_rows[0][0])
        try:
            groups = client.xinfo_groups(NODE_UPLINK_STREAM)
        except ResponseError as exc:
            raise CommandError(
                "Unable to inspect Agent uplink consumer groups; refusing cleanup"
            ) from exc
        if not groups:
            raise CommandError(
                "Agent uplink has retained entries but no consumer group; refusing cleanup"
            )

        unsafe_boundaries: list[str] = []
        total_pending = 0
        total_lag = 0
        for group in groups:
            group_name = str(group.get("name") or "")
            if not group_name or group.get("lag") is None:
                raise CommandError(
                    "Agent uplink consumer group information is incomplete; refusing cleanup"
                )
            pending = client.xpending(NODE_UPLINK_STREAM, group_name) or {}
            pending_count = int(pending.get("pending", 0))
            total_pending += pending_count
            if pending_count:
                earliest_pending = str(pending.get("min") or "")
                if not earliest_pending:
                    raise CommandError(
                        f"Consumer group {group_name} has pending entries without a boundary"
                    )
                unsafe_boundaries.append(earliest_pending)

            lag = int(group.get("lag") or 0)
            total_lag += lag
            if lag:
                last_delivered_id = str(group.get("last-delivered-id") or "0-0")
                unread = client.xrange(
                    NODE_UPLINK_STREAM,
                    min=f"({last_delivered_id}",
                    max="+",
                    count=1,
                )
                if not unread:
                    raise CommandError(
                        f"Consumer group {group_name} reports lag without an unread boundary"
                    )
                unsafe_boundaries.append(str(unread[0][0]))

        first_unsafe = _minimum_id(unsafe_boundaries)
        oldest_rows = client.xrange(
            NODE_UPLINK_STREAM,
            min="-",
            max=cleanup_ceiling,
            count=batch_size,
        )
        safe_ids = [
            str(entry_id)
            for entry_id, _fields in oldest_rows
            if first_unsafe is None
            or _stream_id_parts(str(entry_id)) < _stream_id_parts(first_unsafe)
        ]
        return stream_length, safe_ids, total_pending, total_lag

    def handle(self, *args, **options):
        batch_size = int(options["batch_size"])
        ttl_seconds = int(options["task_stream_ttl_seconds"])
        if not 1 <= batch_size <= 1_000:
            raise CommandError("--batch-size must be between 1 and 1000")
        if not 300 <= ttl_seconds <= 604_800:
            raise CommandError(
                "--task-stream-ttl-seconds must be between 300 and 604800"
            )
        apply = bool(options["apply"])

        client = redis_store.get_redis()
        if client is None:
            raise CommandError("Redis is unavailable")
        before_memory = self._memory_used(client)
        try:
            task_stream_without_ttl, legacy_keys = self._legacy_task_stream_keys(
                client,
                batch_size=batch_size,
            )
            (
                uplink_length,
                safe_uplink_ids,
                uplink_pending,
                uplink_lag,
            ) = self._safe_uplink_entries(client, batch_size=batch_size)
            task_stream_expired = 0
            if apply:
                pipeline = client.pipeline(transaction=True)
                for key in legacy_keys:
                    pipeline.expire(key, ttl_seconds)
                if safe_uplink_ids:
                    pipeline.xdel(NODE_UPLINK_STREAM, *safe_uplink_ids)
                results = pipeline.execute()
                task_stream_expired = sum(
                    1 for result in results[: len(legacy_keys)] if bool(result)
                )
            uplink_length_after = int(client.xlen(NODE_UPLINK_STREAM))
        except (RedisError, TypeError, ValueError) as exc:
            raise CommandError(
                f"Redis runtime cleanup failed safely: {type(exc).__name__}"
            ) from exc

        mode = "applied" if apply else "dry-run"
        after_memory = self._memory_used(client)
        task_stream_without_ttl_after = max(
            0,
            task_stream_without_ttl - task_stream_expired,
        )
        self.stdout.write(
            f"mode={mode} "
            f"task_stream_without_ttl_before={task_stream_without_ttl} "
            f"task_stream_without_ttl_after={task_stream_without_ttl_after} "
            f"task_stream_selected={len(legacy_keys)} "
            f"task_stream_expired={task_stream_expired} "
            f"uplink_length_before={uplink_length} "
            f"uplink_length_after={uplink_length_after} "
            f"uplink_pending={uplink_pending} "
            f"uplink_lag={uplink_lag} "
            f"uplink_selected={len(safe_uplink_ids)} "
            f"memory_before={before_memory} "
            f"memory_after={after_memory}"
        )
        if apply:
            self.stdout.write(
                self.style.SUCCESS("Applied one bounded Redis runtime cleanup batch.")
            )
        else:
            self.stdout.write(
                "No Redis data changed; rerun with --apply after the blue/green "
                "and rollback window has drained."
            )
