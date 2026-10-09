"""Low-frequency, single-flight SourceLens queue metrics."""

import os
import threading
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.instance_settings.services import sourcelens_runtime as runtime


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
@patch.dict(
    os.environ,
    {
        "HFL_SL_RUNTIME_REDIS_URL": "",
        "HFL_SL_RUNTIME_AUTO_REDIS_URL": "",
        "HFL_SL_RUNTIME_QUEUES": "lens,sourcelens",
        "HFL_SL_RUNTIME_QUEUE_WARNING": "1000",
    },
)
class QueueCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def good(self):
        return {
            "health_status": "ok",
            "availability_status": "degraded",
            "queue_lengths": {"lens": 144307, "sourcelens": 1640},
            "notices": [
                runtime._notice(
                    "queue_backlog",
                    "warning",
                    queue="lens",
                    count=144307,
                    threshold=1000,
                )
            ],
        }

    @patch.object(runtime, "probe_queue_backlog")
    def test_success_is_reused_for_five_minutes(self, probe):
        probe.side_effect = lambda _: self.good()
        with patch.object(runtime.time, "time", return_value=1000) as clock:
            first = runtime.cached_queue_backlog("redis://sl/0")
            clock.return_value = 1299
            second = runtime.cached_queue_backlog("redis://sl/0")
            self.assertEqual(first, second)
            probe.assert_called_once()
            self.assertIn("checked_at", first)
            clock.return_value = 1301
            runtime.cached_queue_backlog("redis://sl/0")
            self.assertEqual(probe.call_count, 2)

    @patch.object(runtime, "probe_queue_backlog")
    def test_failure_is_cached_for_one_minute(self, probe):
        probe.side_effect = lambda _: {
            "health_status": "error",
            "availability_status": "unknown",
            "notices": [runtime._notice("queue_probe_failed", "warning")],
        }
        with patch.object(runtime.time, "time", return_value=1000) as clock:
            first = runtime.cached_queue_backlog("redis://sl/0")
            clock.return_value = 1059
            runtime.cached_queue_backlog("redis://sl/0")
            probe.assert_called_once()
            self.assertNotIn("queue_lengths", first)
            clock.return_value = 1061
            runtime.cached_queue_backlog("redis://sl/0")
            self.assertEqual(probe.call_count, 2)

    @patch.object(runtime, "probe_queue_backlog")
    @patch.object(runtime.cache, "add", return_value=False)
    def test_busy_request_never_issues_an_unlocked_query(self, _add, probe):
        result = runtime.cached_queue_backlog("redis://sl/0")
        probe.assert_not_called()
        self.assertEqual(result["health_status"], "unknown")
        self.assertNotIn("queue_lengths", result)

    @patch.object(runtime, "probe_queue_backlog")
    @patch.object(runtime, "_read_scan_checkpoint", side_effect=RuntimeError)
    def test_cache_failure_does_not_create_retry_load(self, _read, probe):
        result = runtime.cached_queue_backlog("redis://sl/0")
        probe.assert_not_called()
        self.assertEqual(result["notices"][0]["code"], "queue_probe_failed")

    @patch.object(runtime, "probe_queue_backlog")
    def test_broker_and_queue_settings_are_part_of_the_cache_key(self, probe):
        probe.side_effect = lambda _: self.good()
        runtime.cached_queue_backlog("redis://sl/0")
        runtime.cached_queue_backlog("redis://sl/1")
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_QUEUES": "lens"}):
            runtime.cached_queue_backlog("redis://sl/0")
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_QUEUE_WARNING": "2000"}):
            runtime.cached_queue_backlog("redis://sl/0")
        self.assertEqual(probe.call_count, 4)

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    @patch.object(
        runtime.sl_client,
        "ping",
        return_value={
            "configured": True,
            "reachable": True,
            "authenticated": True,
            "business_ready": True,
        },
    )
    @patch.object(
        runtime,
        "probe_index_errors",
        return_value={
            "health_status": "unknown",
            "availability_status": "unknown",
            "notices": [],
        },
    )
    @patch.object(runtime, "cached_queue_backlog")
    def test_automatic_url_is_used_but_explicit_url_wins(
        self, queues, _logs, _ping, _mode
    ):
        queues.side_effect = lambda _: self.good()
        with patch.dict(
            os.environ,
            {"HFL_SL_RUNTIME_AUTO_REDIS_URL": "redis://hfl-sourcelens-redis/0"},
        ):
            runtime._collect_health(timeout=2)
            queues.assert_called_with("redis://hfl-sourcelens-redis/0")
            with patch.dict(
                os.environ, {"HFL_SL_RUNTIME_REDIS_URL": "redis://custom/1"}
            ):
                runtime._collect_health(timeout=2)
                queues.assert_called_with("redis://custom/1")

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="external")
    @patch.object(
        runtime.sl_client,
        "ping",
        return_value={"configured": True, "reachable": True, "business_ready": True},
    )
    @patch.object(runtime, "cached_queue_backlog")
    def test_external_does_not_use_bundled_automatic_url(self, queues, _ping, _mode):
        with patch.dict(
            os.environ,
            {"HFL_SL_RUNTIME_AUTO_REDIS_URL": "redis://hfl-sourcelens-redis/0"},
        ):
            payload = runtime._collect_health(timeout=2)
        queues.assert_not_called()
        self.assertEqual(payload["runtime_monitor"]["components"], {})

    def test_concurrent_requests_issue_only_one_measurement(self):
        started = threading.Event()
        resume = threading.Event()
        results = {}

        def query(_url):
            started.set()
            if not resume.wait(5):
                raise RuntimeError("Test did not release queue query")
            return self.good()

        def owner():
            results["owner"] = runtime.cached_queue_backlog("redis://sl/0")

        with patch.object(runtime, "probe_queue_backlog", side_effect=query) as probe:
            thread = threading.Thread(target=owner)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                busy = runtime.cached_queue_backlog("redis://sl/0")
                self.assertEqual(busy["health_status"], "unknown")
                probe.assert_called_once()
            finally:
                resume.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            ready = runtime.cached_queue_backlog("redis://sl/0")
            self.assertEqual(ready, results["owner"])
            probe.assert_called_once()

    @patch.object(runtime, "_collect_health")
    def test_parent_snapshot_does_not_extend_queue_expiry(self, collect):
        collect.return_value = {
            "runtime_monitor": {"components": {"redis": {"expires_at": 1002}}}
        }
        with (
            patch.object(runtime.time, "time", return_value=1000),
            patch.object(
                runtime.cache,
                "set",
                wraps=cache.set,
            ) as save,
        ):
            runtime.sourcelens_health_payload()
        self.assertEqual(save.call_args.args[2], 2)
