"""Bounded, read-only SourceLens runtime monitoring contracts."""

import os
import hashlib
import pickle
import threading
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.cache import cache, caches
from django.core.cache.backends.redis import RedisCache, RedisSerializer
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from apps.instance_settings.services import sourcelens_runtime as runtime

NOW = datetime(2026, 10, 8, 6, tzinfo=timezone.utc)
API_READY = {
    "configured": True,
    "reachable": True,
    "authenticated": True,
    "business_ready": True,
    "status": "ready",
}


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class IndexLogProbeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "postgresql").mkdir()
        (self.root / "worker").mkdir()

    def write(self, directory, text, name="service.log"):
        path = self.root / directory / name
        path.write_text(text)
        return path

    def probe(self):
        return runtime.probe_index_errors(self.root, now=NOW)

    def test_recent_postgres_errors_are_deduplicated_and_redacted(self):
        self.write(
            "postgresql",
            (
                "2026-10-08 05:30:00.000 GMT ERROR: unexpected zero page at block 1046\n"
                "STATEMENT: private SQL/password is not returned\n"
                "2026-10-08 05:40:00+00:00 ERROR: right sibling's left-link doesn't match\n"
            ),
        )
        result = self.probe()
        self.assertEqual(result["health_status"], "error")
        self.assertEqual(result["availability_status"], "unknown")
        self.assertEqual(
            result["notices"],
            [
                {
                    "code": "index_corruption",
                    "level": "error",
                    "params": {"last_seen_at": "2026-10-08T05:40:00+00:00"},
                }
            ],
        )
        self.assertNotIn("private", str(result))

    def test_worker_traceback_inherits_event_timestamp(self):
        self.write(
            "worker",
            (
                "[2026-10-08 13:30:00,010+08:00: ERROR/MainProcess] Task failed\n"
                "Traceback (most recent call last):\n"
                "django.db.DatabaseError: contains unexpected zero page\n"
            ),
        )
        self.assertEqual(self.probe()["health_status"], "error")

    def test_old_error_is_history_even_when_file_was_modified_today(self):
        self.write(
            "worker",
            (
                "[2026-09-30 18:00:00,123: ERROR/MainProcess] unexpected zero page\n"
                "[2026-10-08 05:00:00,123: INFO/MainProcess] Worker started\n"
            ),
        )
        result = self.probe()
        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["notices"][0]["code"], "index_corruption_history")
        self.assertEqual(result["notices"][0]["level"], "info")

    def test_empty_or_normal_logs_do_not_prove_database_integrity(self):
        self.write("postgresql", "2026-10-08 05:00:00 UTC LOG: connection received\n")
        self.assertEqual(self.probe()["health_status"], "unknown")
        self.assertEqual(self.probe()["notices"], [])

    def test_missing_and_unreadable_logs_are_not_silent_success(self):
        result = runtime.probe_index_errors(self.root / "missing", now=NOW)
        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["notices"][0]["code"], "index_logs_unavailable")
        self.write("postgresql", "some text")
        with patch.object(runtime, "_read_chunk", side_effect=PermissionError):
            self.assertEqual(
                self.probe()["notices"][0]["code"], "index_logs_unavailable"
            )

    def test_undated_and_future_errors_do_not_claim_current_failure(self):
        for text in (
            "django.db.DatabaseError: unexpected zero page\n",
            "2027-01-01 05:00:00 UTC ERROR: unexpected zero page\n",
            "2026-99-99 05:00:00 UTC ERROR: unexpected zero page\n",
        ):
            with self.subTest(text=text):
                cache.clear()
                self.write("worker", text)
                self.assertEqual(self.probe()["health_status"], "unknown")
                self.assertEqual(
                    self.probe()["notices"][0]["code"], "index_corruption_undated"
                )

    def test_scan_limits_files_and_tail_bytes_and_skips_symlinks(self):
        for number in range(7):
            path = self.write("postgresql", "ordinary log", f"db-{number}.log")
            os.utime(path, (number, number))
        (self.root / "postgresql" / "symlink.log").symlink_to("/etc/passwd")
        self.assertEqual(len(runtime._log_files(self.root / "postgresql")[0]), 4)
        path = self.write(
            "worker",
            (
                "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n"
                + "normal line\n" * runtime.MAX_SCAN_BYTES
            ),
        )
        chunk, position, more = runtime._read_chunk(path, {})
        self.assertLessEqual(len(chunk.encode()), runtime.MAX_SCAN_BYTES)
        self.assertLessEqual(position["offset"], path.stat().st_size)
        self.assertFalse(more)
        # Bounded recent-log checks do not claim complete historical coverage.
        self.assertEqual(self.probe()["health_status"], "unknown")

    def test_recent_window_boundary(self):
        stamp = (NOW - timedelta(hours=24)).isoformat()
        self.write("worker", f"{stamp} ERROR: unexpected zero page\n")
        self.assertEqual(self.probe()["health_status"], "error")

    def test_detected_error_survives_append_rotation_and_file_removal(self):
        path = self.write(
            "postgresql",
            "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n",
        )
        self.assertEqual(self.probe()["health_status"], "error")
        with path.open("a") as stream:
            stream.write("2026-10-08 05:01:00 UTC LOG: connection received\n" * 12000)
        result = self.probe()
        self.assertEqual(result["health_status"], "error")
        self.assertNotIn(
            "index_scan_incomplete", [n["code"] for n in result["notices"]]
        )
        path.rename(path.with_suffix(".log.1"))
        path.write_text("2026-10-08 05:10:00 UTC LOG: connection received\n")
        self.assertEqual(self.probe()["health_status"], "error")
        path.unlink()
        path.with_suffix(".log.1").unlink()
        self.assertEqual(self.probe()["health_status"], "error")

    def test_retained_alert_ages_to_unconfirmed_warning_not_recovery(self):
        path = self.write(
            "worker",
            "[2026-10-08 05:00:00,000: ERROR/MainProcess] unexpected zero page\n",
        )
        self.assertEqual(self.probe()["health_status"], "error")
        path.unlink()
        result = runtime.probe_index_errors(self.root, now=NOW + timedelta(days=2))
        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["notices"][0]["code"], "index_corruption_unconfirmed")
        self.assertEqual(result["notices"][0]["level"], "warning")

    def test_initial_scan_finds_latest_error_without_historical_backfill(self):
        path = self.write(
            "postgresql",
            (
                "2026-10-08 04:00:00 UTC LOG: connection received\n" * 7000
                + "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n"
            ),
        )
        self.assertGreater(path.stat().st_size, runtime.MAX_SCAN_BYTES)
        first = self.probe()
        self.assertEqual(first["health_status"], "error")
        self.assertNotIn(
            "index_scan_incomplete",
            [n["code"] for n in first["notices"]],
        )
        second = self.probe()
        self.assertEqual(second["health_status"], "error")
        self.assertNotIn(
            "index_scan_incomplete", [n["code"] for n in second["notices"]]
        )

    def test_normal_logs_and_statement_text_do_not_trigger_corruption(self):
        cases = [
            "2026-10-08 05:00:00 UTC LOG: duration: 2001 ms statement: SELECT 'unexpected zero page';\n",
            "2026-10-08 05:00:00 UTC LOG: statement: SELECT 'ERROR: unexpected zero page';\n",
            "[2026-10-08 05:00:00,000: INFO/MainProcess] unexpected zero page\n",
            "2026-10-08 05:00:00 UTC ERROR: unrelated syntax error\n"
            "STATEMENT: SELECT 'unexpected zero page';\n",
            "[2026-10-08 05:00:00,000: ERROR/MainProcess] SQL syntax error\n"
            "  cursor.execute(\"SELECT 'unexpected zero page'\")\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                cache.clear()
                self.write("worker", text)
                self.assertEqual(self.probe()["notices"], [])

    def test_postgres_pid_prefix_and_worker_exception_across_scan_boundary(self):
        text = (
            "2026-10-08 05:00:00 UTC [1234] ERROR: unexpected zero page\n"
            "[2026-10-08 05:10:00,000: ERROR/MainProcess] Task failed\n"
        )
        exception = (
            "psycopg2.errors.IndexCorrupted: right sibling's left-link doesn't match\n"
        )
        path = self.write("worker", text)
        with patch.object(runtime, "MAX_SCAN_BYTES", len(text.encode())):
            first = self.probe()
            with path.open("a") as stream:
                stream.write(exception)
            second = self.probe()
        self.assertEqual(first["health_status"], "error")
        self.assertEqual(
            second["notices"][0]["params"]["last_seen_at"], "2026-10-08T05:10:00+00:00"
        )

    def test_truncated_file_restarts_scanning(self):
        path = self.write(
            "worker", "2026-10-08 04:00:00 UTC LOG: connection received\n" * 100
        )
        self.assertEqual(self.probe()["notices"], [])
        path.write_text("2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n")
        self.assertEqual(self.probe()["health_status"], "error")

    def test_incomplete_final_line_is_retried(self):
        path = self.write("worker", "2026-10-08 05:00:00 UTC ERROR: unexpected zero")
        self.assertEqual(self.probe()["notices"][0]["code"], "index_scan_incomplete")
        with path.open("a") as stream:
            stream.write(" page\n")
        self.assertEqual(self.probe()["health_status"], "error")

    def test_oversized_line_fragments_are_not_parsed_as_error_records(self):
        self.write(
            "worker",
            (
                "2026-10-08 05:00:00 UTC LOG: large statement "
                + "x" * 100
                + "ERROR: unexpected zero page\n"
            ),
        )
        with patch.object(runtime, "MAX_SCAN_BYTES", 64):
            for _ in range(5):
                self.assertEqual(self.probe()["health_status"], "unknown")

    def test_cache_failure_reports_incomplete_monitoring(self):
        self.write("worker", "2026-10-08 04:00:00 UTC LOG: connection received\n")
        with (
            patch.object(runtime.cache, "get", side_effect=RuntimeError),
            patch.object(
                runtime.cache,
                "set",
                side_effect=RuntimeError,
            ),
        ):
            self.assertEqual(
                self.probe()["notices"][0]["code"], "index_scan_incomplete"
            )

    def test_checkpoint_read_failure_does_not_overwrite_retained_error(self):
        from redis.exceptions import TimeoutError

        path = self.write(
            "postgresql",
            "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n",
        )
        self.assertEqual(self.probe()["health_status"], "error")
        key = (
            "runtime:sl:logs:v2:"
            + hashlib.sha256(
                str(self.root.absolute()).encode(),
            ).hexdigest()
        )
        before = cache.get(key)
        path.unlink()
        path.write_text("2026-10-08 05:10:00 UTC LOG: connection received\n")
        with (
            patch.object(
                runtime,
                "_read_scan_checkpoint",
                side_effect=TimeoutError("temporary read timeout"),
            ),
            patch.object(
                runtime,
                "_atomic_scan_update",
                wraps=runtime._atomic_scan_update,
            ) as update,
        ):
            failed = self.probe()
        self.assertEqual(failed["health_status"], "unknown")
        self.assertIn("index_scan_incomplete", [n["code"] for n in failed["notices"]])
        self.assertEqual(cache.get(key), before)
        self.assertIsNone(cache.get(key + ":scan-lock"))
        # Releasing the lease is allowed; writing a new checkpoint is not.
        self.assertEqual(update.call_count, 1)
        self.assertEqual(len(update.call_args.args), 3)
        recovered = self.probe()
        self.assertEqual(recovered["health_status"], "error")
        self.assertEqual(recovered["notices"][0]["code"], "index_corruption")
        self.assertNotIn(
            "index_scan_incomplete", [n["code"] for n in recovered["notices"]]
        )

    def test_checkpoint_read_failure_still_reports_current_log_evidence(self):
        self.write(
            "worker",
            "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n",
        )
        key = (
            "runtime:sl:logs:v2:"
            + hashlib.sha256(
                str(self.root.absolute()).encode(),
            ).hexdigest()
        )
        with patch.object(runtime, "_read_scan_checkpoint", side_effect=RuntimeError):
            failed = self.probe()
        self.assertEqual(failed["health_status"], "error")
        self.assertIn("index_scan_incomplete", [n["code"] for n in failed["notices"]])
        self.assertIsNone(cache.get(key))
        self.assertIsNone(cache.get(key + ":scan-lock"))
        # A successful read of an absent checkpoint is not treated as a failure.
        self.assertEqual(self.probe()["health_status"], "error")
        self.assertIsNotNone(cache.get(key)["latest"])

    def test_large_normal_log_does_not_trigger_a_backfill_warning(self):
        self.write(
            "postgresql",
            "2026-10-08 05:00:00 UTC LOG: connection received\n" * 12000,
        )
        result = self.probe()
        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["notices"], [])

    def test_latest_error_is_found_after_large_growth_even_with_checkpoint(self):
        path = self.write(
            "postgresql",
            "2026-10-08 04:00:00 UTC LOG: connection received\n",
        )
        self.assertEqual(self.probe()["notices"], [])
        with path.open("a") as stream:
            stream.write("2026-10-08 05:00:00 UTC LOG: connection received\n" * 12000)
            stream.write("2026-10-08 05:30:00 UTC ERROR: unexpected zero page\n")
        self.assertEqual(self.probe()["health_status"], "error")

    def test_truncated_first_record_is_not_interpreted_as_a_new_error(self):
        prefix = "2026-10-08 05:00:00 UTC LOG: statement: SELECT '"
        self.write("worker", prefix + "ERROR: unexpected zero page';\n")
        with patch.object(runtime, "MAX_SCAN_BYTES", 27):
            self.assertEqual(self.probe()["health_status"], "unknown")

    def test_aligned_window_keeps_its_first_complete_error_record(self):
        error = "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n"
        filler = "x" * (runtime.MAX_SCAN_BYTES - len(error.encode()) - 1) + "\n"
        path = self.write("postgresql", "old record\n" + error + filler)
        self.assertGreater(path.stat().st_size, runtime.MAX_SCAN_BYTES)
        chunk, _, more = runtime._read_chunk(path, {})
        self.assertTrue(chunk.startswith(error))
        self.assertLessEqual(len(chunk.encode()), runtime.MAX_SCAN_BYTES)
        self.assertFalse(more)
        self.assertEqual(self.probe()["health_status"], "error")

    def test_partial_window_discards_only_the_truncated_record(self):
        error = "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n"
        partial = "partial-record\n"
        filler = (
            "x"
            * (runtime.MAX_SCAN_BYTES - len(partial.encode()) - len(error.encode()) - 1)
            + "\n"
        )
        self.write("postgresql", "old " + partial + error + filler)
        self.assertEqual(self.probe()["health_status"], "error")

    def test_concurrent_scan_does_not_write_over_the_owner_checkpoint(self):
        self.write(
            "postgresql",
            "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n",
        )
        started = threading.Event()
        release = threading.Event()
        results = {}
        original = runtime._read_chunk

        def pause_owner(path, previous):
            value = original(path, previous)
            if threading.current_thread().name == "checkpoint-owner":
                started.set()
                if not release.wait(5):
                    raise RuntimeError("Test owner did not receive release")
            return value

        def scan_owner():
            try:
                results["owner"] = self.probe()
            except Exception as exc:
                results["exception"] = exc

        with patch.object(runtime, "_read_chunk", side_effect=pause_owner):
            thread = threading.Thread(target=scan_owner, name="checkpoint-owner")
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                with patch.object(runtime.cache, "set", wraps=cache.set) as writes:
                    busy = self.probe()
                    writes.assert_not_called()
                self.assertEqual(busy["notices"][0]["code"], "index_scan_incomplete")
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("exception", results)
        self.assertEqual(results["owner"]["health_status"], "error")
        # Rotation/removal cannot erase the evidence committed by the owner.
        for path in (self.root / "postgresql").iterdir():
            path.unlink()
        self.assertEqual(self.probe()["health_status"], "error")

    def test_busy_lock_reuses_existing_error_evidence(self):
        self.write(
            "worker",
            "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n",
        )
        self.assertEqual(self.probe()["health_status"], "error")
        with (
            patch.object(runtime.cache, "add", return_value=False),
            patch.object(
                runtime,
                "_read_chunk",
            ) as read,
        ):
            result = self.probe()
        read.assert_not_called()
        self.assertEqual(result["health_status"], "error")
        self.assertIn("index_scan_incomplete", [n["code"] for n in result["notices"]])

    def test_expired_owner_cannot_commit_or_release_new_owner_lock(self):
        self.write(
            "worker",
            "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n",
        )
        original = runtime._read_chunk
        locks = []

        def replace_lease(path, previous):
            value = original(path, previous)
            cache.set(locks[0], "new-owner", runtime.SCAN_LOCK_SECONDS)
            return value

        real_add = cache.add
        with (
            patch.object(
                runtime.cache,
                "add",
                side_effect=lambda key, owner, timeout: (
                    locks.append(key) or real_add(key, owner, timeout)
                ),
            ),
            patch.object(runtime, "_read_chunk", side_effect=replace_lease),
        ):
            result = self.probe()
        self.assertIn("index_scan_incomplete", [n["code"] for n in result["notices"]])
        self.assertEqual(cache.get(locks[0]), "new-owner")
        self.assertIsNone(cache.get(locks[0].removesuffix(":scan-lock")))

    def test_near_expiry_owner_does_not_write_checkpoint(self):
        self.write("worker", "2026-10-08 04:00:00 UTC LOG: connection received\n")
        with (
            patch.object(runtime.time, "monotonic", side_effect=[0, 55, 55]),
            patch.object(
                runtime.cache,
                "set",
                wraps=cache.set,
            ) as write,
        ):
            result = self.probe()
        write.assert_not_called()
        self.assertEqual(result["notices"][0]["code"], "index_scan_incomplete")

    def test_lease_expiry_after_guard_cannot_overwrite_new_evidence(self):
        normal = "2026-10-08 04:00:00 UTC LOG: connection received\n"
        path = self.write("postgresql", normal)
        key = (
            "runtime:sl:logs:v2:"
            + hashlib.sha256(
                str(self.root.absolute()).encode(),
            ).hexdigest()
        )
        lock_key = key + ":scan-lock"
        blocked = threading.Event()
        resume = threading.Event()
        results = {}
        original = runtime._atomic_scan_update

        def pause_before_atomic_write(key, lock_key, owner, payload=None):
            if payload is not None and threading.current_thread().name == "stale-owner":
                blocked.set()
                if not resume.wait(5):
                    raise RuntimeError("Stale owner did not receive release")
            return original(key, lock_key, owner, payload)

        def old_request():
            try:
                results["old"] = self.probe()
            except Exception as exc:
                results["exception"] = exc

        with patch.object(
            runtime, "_atomic_scan_update", side_effect=pause_before_atomic_write
        ):
            thread = threading.Thread(target=old_request, name="stale-owner")
            thread.start()
            try:
                self.assertTrue(blocked.wait(5))
                backend = caches["default"]
                # Expire the old lease while it is paused after the time guard.
                with backend._lock:
                    backend._expire_info[backend.make_key(lock_key)] = 0
                with path.open("a") as stream:
                    stream.write(
                        "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n"
                    )
                new_result = self.probe()
                self.assertEqual(new_result["health_status"], "error")
                self.assertIsNotNone(cache.get(key)["latest"])
                self.assertTrue(
                    cache.add(lock_key, "replacement-owner", runtime.SCAN_LOCK_SECONDS)
                )
            finally:
                resume.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("exception", results)
        self.assertIsNotNone(cache.get(key)["latest"])
        self.assertEqual(cache.get(lock_key), "replacement-owner")
        self.assertIn(
            "index_scan_incomplete", [n["code"] for n in results["old"]["notices"]]
        )
        cache.delete(lock_key)
        path.unlink()
        path.write_text(normal)
        self.assertEqual(self.probe()["health_status"], "error")


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "KEY_PREFIX": "scan-test",
            "VERSION": 7,
        }
    },
)
class AtomicScanUpdateTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_locmem_save_and_release_use_the_cache_namespace_and_expiry(self):
        self.assertTrue(cache.add("scan:lease", "owner", runtime.SCAN_LOCK_SECONDS))
        self.assertTrue(
            runtime._atomic_scan_update(
                "scan:state", "scan:lease", "owner", {"latest": "seen"}
            )
        )
        self.assertEqual(cache.get("scan:state"), {"latest": "seen"})
        self.assertTrue(
            runtime._atomic_scan_update("scan:state", "scan:lease", "owner")
        )
        self.assertIsNone(cache.get("scan:lease"))
        self.assertEqual(cache.get("scan:state"), {"latest": "seen"})

    def test_locmem_rejects_expired_or_wrong_owner_without_touching_state(self):
        cache.set("scan:state", {"latest": "newer"})
        cache.add("scan:lease", "new-owner", runtime.SCAN_LOCK_SECONDS)
        self.assertFalse(
            runtime._atomic_scan_update("scan:state", "scan:lease", "old-owner", {})
        )
        self.assertFalse(
            runtime._atomic_scan_update("scan:state", "scan:lease", "old-owner")
        )
        self.assertEqual(cache.get("scan:lease"), "new-owner")
        backend = caches["default"]
        with backend._lock:
            backend._expire_info[backend.make_key("scan:lease")] = 0
        self.assertFalse(
            runtime._atomic_scan_update("scan:state", "scan:lease", "new-owner", {})
        )
        self.assertEqual(cache.get("scan:state"), {"latest": "newer"})

    def test_redis_uses_one_atomic_script_on_primary_with_exact_serializer(self):
        backend = RedisCache(
            "redis://unused/1", {"KEY_PREFIX": "scan-test", "VERSION": 7}
        )
        client = Mock()
        client.eval.return_value = 1
        backend._cache.get_client = Mock(return_value=client)
        backend._cache._serializer = RedisSerializer()
        payload = {"latest": "newer", "files": {}}
        with patch.object(runtime, "caches", {"default": backend}):
            self.assertTrue(
                runtime._atomic_scan_update(
                    "scan:state", "scan:lease", "owner", payload
                )
            )
        backend._cache.get_client.assert_called_once_with(
            "scan-test:7:scan:lease", write=True
        )
        client.eval.assert_called_once_with(
            runtime.SAVE_CHECKPOINT,
            2,
            "scan-test:7:scan:lease",
            "scan-test:7:scan:state",
            backend._cache._serializer.dumps("owner"),
            backend._cache._serializer.dumps(payload),
            runtime.SCAN_STATE_SECONDS,
        )
        self.assertEqual(pickle.loads(client.eval.call_args.args[4]), "owner")
        self.assertEqual(pickle.loads(client.eval.call_args.args[5]), payload)

    def test_redis_checkpoint_read_uses_primary_and_matching_namespace(self):
        backend = RedisCache(
            ["redis://unused-primary/1", "redis://unused-replica/1"],
            {"KEY_PREFIX": "scan-test", "VERSION": 7},
        )
        client = Mock()
        client.get.return_value = backend._cache._serializer.dumps({"latest": "newer"})
        backend._cache.get_client = Mock(return_value=client)
        with patch.object(runtime, "caches", {"default": backend}):
            self.assertEqual(
                runtime._read_scan_checkpoint("scan:state"), {"latest": "newer"}
            )
            client.get.return_value = None
            self.assertEqual(runtime._read_scan_checkpoint("scan:state"), {})
        self.assertTrue(
            all(
                call.kwargs["write"]
                for call in backend._cache.get_client.call_args_list
            )
        )
        client.get.assert_called_with("scan-test:7:scan:state")

    def test_redis_stale_owner_cannot_write_or_release(self):
        backend = RedisCache("redis://unused/1", {})
        client = Mock()
        client.eval.return_value = 0
        backend._cache.get_client = Mock(return_value=client)
        with patch.object(runtime, "caches", {"default": backend}):
            self.assertFalse(
                runtime._atomic_scan_update("scan:state", "scan:lease", "stale", {})
            )
            self.assertFalse(
                runtime._atomic_scan_update("scan:state", "scan:lease", "stale")
            )
        self.assertEqual(client.eval.call_count, 2)
        release = client.eval.call_args.args
        self.assertEqual(release[:3], (runtime.RELEASE_SCAN, 1, ":1:scan:lease"))
        self.assertEqual(pickle.loads(release[3]), "stale")
        client.set.assert_not_called()
        client.delete.assert_not_called()

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
    )
    def test_unsupported_backend_never_falls_back_to_unsafe_write(self):
        with (
            patch.object(runtime.cache, "set") as write,
            patch.object(
                runtime.cache,
                "delete",
            ) as delete,
        ):
            self.assertFalse(runtime._atomic_scan_update("state", "lease", "owner", {}))
            self.assertFalse(runtime._atomic_scan_update("state", "lease", "owner"))
        write.assert_not_called()
        delete.assert_not_called()


@patch.dict(
    os.environ,
    {
        # Explicit external metrics lists remain unchanged by the bundled
        # legacy-default compatibility rule.
        "SOURCELENS_MODE": "external",
        "HFL_SL_RUNTIME_QUEUES": "lens,sourcelens",
        "HFL_SL_RUNTIME_QUEUE_WARNING": "1000",
    },
)
class QueueProbeTests(SimpleTestCase):
    @patch("redis.from_url")
    def test_unconfigured_does_not_connect(self, connect):
        result = runtime.probe_queue_backlog("")
        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["notices"][0]["code"], "queue_not_configured")
        connect.assert_not_called()

    @patch("redis.from_url")
    def test_backlog_does_not_mark_redis_connection_unhealthy(self, connect):
        client = connect.return_value
        pipeline = client.pipeline.return_value.__enter__.return_value
        pipeline.execute.return_value = [True, 144307, 1573]
        result = runtime.probe_queue_backlog("redis://monitor:secret@sl-redis:6379/0")
        self.assertEqual(result["health_status"], "ok")
        self.assertEqual(result["availability_status"], "degraded")
        self.assertEqual(result["queue_lengths"], {"lens": 144307, "sourcelens": 1573})
        self.assertEqual(len(result["notices"]), 2)
        client.pipeline.assert_called_once_with(transaction=False)
        pipeline.ping.assert_called_once_with()
        pipeline.execute.assert_called_once_with(raise_on_error=False)
        self.assertEqual(
            [call.args for call in pipeline.llen.call_args_list],
            [
                ("lens",),
                ("sourcelens",),
            ],
        )
        self.assertEqual(
            set(call[0] for call in pipeline.method_calls),
            {
                "ping",
                "llen",
                "execute",
            },
        )
        self.assertNotIn("secret", str(result))
        self.assertEqual(connect.call_args.kwargs["socket_timeout"], 2)
        self.assertEqual(connect.call_args.kwargs["retry"].get_retries(), 0)
        client.close.assert_called_once_with()

    @patch("redis.from_url")
    def test_below_threshold_does_not_claim_business_readiness(self, connect):
        connect.return_value.pipeline.return_value.__enter__.return_value.execute.return_value = [
            True,
            0,
            999,
        ]
        result = runtime.probe_queue_backlog("redis://sl-redis:6379/0")
        self.assertEqual(result["health_status"], "ok")
        self.assertEqual(result["availability_status"], "unknown")
        self.assertEqual(result["notices"], [])

    @patch("redis.from_url")
    def test_threshold_boundary_and_duplicate_queue_names(self, connect):
        pipeline = connect.return_value.pipeline.return_value.__enter__.return_value
        pipeline.execute.return_value = [True, 1000]
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_QUEUES": "lens,lens"}):
            result = runtime.probe_queue_backlog("redis://sl-redis:6379/0")
        pipeline.llen.assert_called_once_with("lens")
        self.assertEqual(result["availability_status"], "degraded")
        self.assertEqual(result["notices"][0]["params"]["count"], 1000)

    @patch("redis.from_url")
    def test_invalid_configuration_is_safe(self, connect):
        for url in ("https://sl", "redis://sl/0?socket_timeout=100", "redis://"):
            self.assertEqual(
                runtime.probe_queue_backlog(url)["notices"][0]["code"],
                "queue_config_invalid",
            )
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_QUEUE_WARNING": "invalid"}):
            self.assertEqual(
                runtime.probe_queue_backlog("redis://sl/0")["notices"][0]["code"],
                "queue_config_invalid",
            )
        for queues in ("", ",".join(f"queue-{number}" for number in range(9))):
            with patch.dict(os.environ, {"HFL_SL_RUNTIME_QUEUES": queues}):
                self.assertEqual(
                    runtime.probe_queue_backlog("redis://sl/0")["notices"][0]["code"],
                    "queue_config_invalid",
                )
        connect.assert_not_called()

    @patch("redis.from_url")
    def test_connection_failure_does_not_return_credentials_or_zero_depth(
        self, connect
    ):
        import redis

        connect.return_value.pipeline.return_value.__enter__.return_value.execute.side_effect = redis.ConnectionError(
            "secret redis://monitor:password@sl"
        )
        result = runtime.probe_queue_backlog("redis://sl/0")
        self.assertEqual(result["health_status"], "error")
        self.assertNotIn("queue_lengths", result)
        self.assertNotIn("password", str(result))
        self.assertEqual(result["notices"][0]["code"], "queue_probe_failed")

    @patch("redis.from_url")
    def test_llen_permission_or_type_error_preserves_successful_ping(self, connect):
        import redis

        pipeline = connect.return_value.pipeline.return_value.__enter__.return_value
        for message in ("NOPERM no permissions", "WRONGTYPE key is not a list"):
            with self.subTest(message=message):
                pipeline.execute.return_value = [
                    True,
                    redis.ResponseError(message),
                    1573,
                ]
                result = runtime.probe_queue_backlog("redis://monitor:secret@sl/0")
                self.assertEqual(result["health_status"], "ok")
                self.assertEqual(result["queue_lengths"], {"sourcelens": 1573})
                self.assertEqual(result["availability_status"], "degraded")
                self.assertEqual(
                    result["notices"][0]["code"], "queue_metrics_unavailable"
                )
                self.assertEqual(result["notices"][1]["code"], "queue_backlog")
                self.assertNotIn(message, str(result))

    @patch("redis.from_url")
    def test_all_metric_errors_do_not_claim_business_unavailability(self, connect):
        import redis

        connect.return_value.pipeline.return_value.__enter__.return_value.execute.return_value = [
            True,
            redis.ResponseError("NOPERM"),
            redis.ResponseError("WRONGTYPE"),
        ]
        result = runtime.probe_queue_backlog("redis://sl/0")
        self.assertEqual(result["health_status"], "ok")
        self.assertEqual(result["availability_status"], "unknown")
        self.assertEqual(result["queue_lengths"], {})
        self.assertTrue(
            all(n["code"] == "queue_metrics_unavailable" for n in result["notices"])
        )

    @patch("redis.from_url")
    def test_ping_permission_failure_is_unknown_not_unhealthy(self, connect):
        import redis

        connect.return_value.pipeline.return_value.__enter__.return_value.execute.return_value = [
            redis.ResponseError("NOPERM ping"),
            5,
            10,
        ]
        result = runtime.probe_queue_backlog("redis://sl/0")
        self.assertEqual(result["health_status"], "unknown")
        self.assertEqual(result["queue_lengths"], {"lens": 5, "sourcelens": 10})


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
@patch.dict(
    os.environ,
    {
        "HFL_SL_RUNTIME_LOG_DIR": "",
        "HFL_SL_RUNTIME_REDIS_URL": "",
        "HFL_SL_RUNTIME_QUEUES": "lens",
        "HFL_SL_RUNTIME_QUEUE_WARNING": "1000",
    },
)
class SnapshotTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    @patch.object(runtime.sl_client, "ping", return_value=API_READY)
    @patch.object(runtime, "probe_queue_backlog")
    @patch.object(runtime, "probe_index_errors")
    def test_shared_snapshot_preserves_api_and_degrades_overall(
        self, logs, queues, ping, _mode
    ):
        logs.return_value = {
            "health_status": "error",
            "availability_status": "unknown",
            "notices": [
                runtime._notice(
                    "index_corruption", "error", last_seen_at=NOW.isoformat()
                )
            ],
        }
        queues.return_value = {
            "health_status": "ok",
            "availability_status": "degraded",
            "notices": [
                runtime._notice(
                    "queue_backlog", "warning", queue="lens", count=1500, threshold=1000
                )
            ],
        }
        first = runtime.sourcelens_health_payload(timeout=2)
        second = runtime.sourcelens_health_payload(timeout=3)
        self.assertEqual(first, second)
        self.assertTrue(first["business_ready"])
        self.assertEqual(first["runtime_monitor"]["status"], "degraded")
        self.assertEqual(first["runtime_monitor"]["health_status"], "error")
        ping.assert_called_once_with(timeout=2)

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="external")
    @patch.object(runtime.sl_client, "ping", return_value=API_READY)
    @patch.object(runtime, "probe_queue_backlog")
    @patch.object(runtime, "probe_index_errors")
    def test_external_does_not_inspect_unrelated_bundled_logs(
        self, logs, queues, _ping, _mode
    ):
        result = runtime.sourcelens_health_payload()
        self.assertEqual(result["runtime_monitor"]["components"], {})
        self.assertEqual(result["runtime_monitor"]["status"], "ok")
        logs.assert_not_called()
        queues.assert_not_called()

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="external")
    @patch.object(
        runtime.sl_client, "ping", return_value={"configured": True, "reachable": False}
    )
    def test_api_unavailable_is_not_downgraded_to_a_warning(self, _ping, _mode):
        self.assertEqual(
            runtime.sourcelens_health_payload()["runtime_monitor"]["status"], "error"
        )

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="external")
    @patch.object(runtime.sl_client, "ping", return_value=API_READY)
    @patch.object(runtime.cache, "get", side_effect=RuntimeError)
    @patch.object(runtime.cache, "set", side_effect=RuntimeError)
    def test_cache_failure_does_not_hide_health(self, _set, _get, _ping, _mode):
        self.assertEqual(
            runtime.sourcelens_health_payload()["runtime_monitor"]["status"], "ok"
        )

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    @patch.object(runtime.sl_client, "ping", return_value=API_READY)
    def test_normal_log_growth_does_not_restore_overall_health(self, _ping, _mode):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "postgresql").mkdir()
            (root / "worker").mkdir()
            path = root / "postgresql" / "service.log"
            stamp = datetime.now(timezone.utc).isoformat()
            path.write_text(f"{stamp} ERROR: unexpected zero page\n")
            with patch.dict(os.environ, {"HFL_SL_RUNTIME_LOG_DIR": directory}):
                first = runtime._collect_health(timeout=2)["runtime_monitor"]
                with path.open("a") as stream:
                    stream.write(f"{stamp} LOG: connection received\n" * 12000)
                second = runtime._collect_health(timeout=2)["runtime_monitor"]
            self.assertEqual(first["status"], "degraded")
            self.assertEqual(second["status"], "degraded")
            self.assertEqual(second["health_status"], "error")

    @patch.object(runtime.deploy, "lens_gateway_base_url", return_value="")
    def test_integrations_view_returns_same_snapshot(self, _gateway):
        from apps.instance_settings.api.views.settings import (
            PlatformOpsSettingsIntegrationsView,
        )

        payload = dict(
            API_READY,
            runtime_monitor={
                "status": "degraded",
                "health_status": "error",
                "checked_at": NOW.isoformat(),
                "components": {},
            },
        )
        with patch.object(runtime, "sourcelens_health_payload", return_value=payload):
            response = PlatformOpsSettingsIntegrationsView.as_view(
                permission_classes=[]
            )(
                APIRequestFactory().get("/"),
            )
        row = response.data["integrations"][0]
        self.assertEqual(row["status"], "degraded")
        self.assertEqual(row["runtime_monitor"], payload["runtime_monitor"])
        self.assertEqual(row["checked_at"], NOW.isoformat())
        self.assertTrue(row["business_ready"])

    def test_environment_uses_runtime_summary_instead_of_api_readiness(self):
        from apps.instance_settings.api.views import settings as views
        from apps.instance_settings.services import environment_payload as environment

        payload = dict(
            API_READY,
            runtime_monitor={
                "status": "degraded",
                "health_status": "error",
                "checked_at": NOW.isoformat(),
                "components": {},
            },
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(runtime, "sourcelens_health_payload", return_value=payload)
            )
            stack.enter_context(
                patch.object(runtime.deploy, "lens_gateway_base_url", return_value="")
            )
            stack.enter_context(
                patch.object(
                    views.runtime_settings_svc,
                    "enterprise_identity_enabled",
                    return_value=False,
                )
            )
            stack.enter_context(
                patch.object(
                    views,
                    "email_connection_kwargs",
                    return_value={"host": "", "password": "", "source": "default"},
                )
            )
            for name in (
                "email_signup_enabled",
                "email_code_login_enabled",
                "password_reset_available",
                "platform_ops_enabled",
                "openai_api_key",
            ):
                stack.enter_context(patch.object(views, name, return_value=False))
            for name in ("get_source", "tenant_public_url"):
                stack.enter_context(patch.object(views, name, return_value=""))
            for name in (
                "system_health_payload",
                "probe_nginx",
                "probe_web",
                "probe_platform_data_gateway",
                "deploy_profile_staff_payload",
            ):
                stack.enter_context(patch.object(environment, name, return_value={}))
            response = views.PlatformOpsSettingsEnvironmentView().get(None)
        self.assertEqual(response.data["health"]["sourcelens"]["status"], "degraded")
        self.assertTrue(response.data["health"]["sourcelens"]["business_ready"])
        self.assertEqual(
            response.data["health"]["sourcelens"]["runtime_monitor"],
            payload["runtime_monitor"],
        )
