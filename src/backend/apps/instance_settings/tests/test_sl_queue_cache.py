"""Low-frequency, single-flight SourceLens queue metrics."""

import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
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
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "sl-queue-monitor.json"
        patcher = patch.object(runtime, "AUTO_QUEUE_CONFIG_PATH", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_unset_monitoring_list_includes_backend_without_changing_routing(self):
        with patch.dict(os.environ):
            os.environ.pop("HFL_SL_RUNTIME_QUEUES", None)
            os.environ["CELERY_TASK_DEFAULT_QUEUE"] = "custom-routing"
            queues, threshold = runtime._queue_config()
            self.assertEqual(queues, ["backend", "lens", "sourcelens"])
            self.assertEqual(threshold, 1000)
            self.assertEqual(
                os.environ["CELERY_TASK_DEFAULT_QUEUE"], "custom-routing"
            )
            self.assertNotIn("HFL_SL_RUNTIME_QUEUES", os.environ)

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    def test_bundled_legacy_monitoring_default_includes_backend(self, _mode):
        self.assertEqual(
            runtime._queue_config()[0], ["backend", "lens", "sourcelens"]
        )
        self.assertEqual(os.environ["HFL_SL_RUNTIME_QUEUES"], "lens,sourcelens")

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="external")
    def test_external_legacy_list_is_not_expanded(self, _mode):
        self.assertEqual(runtime._queue_config()[0], ["lens", "sourcelens"])

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    def test_other_explicit_monitoring_lists_are_preserved(self, _mode):
        for raw, expected in (
            ("lens", ["lens"]),
            ("custom,lens", ["custom", "lens"]),
            ("sourcelens,lens", ["sourcelens", "lens"]),
            ("backend,lens,sourcelens", ["backend", "lens", "sourcelens"]),
        ):
            with self.subTest(raw=raw), patch.dict(
                os.environ, {"HFL_SL_RUNTIME_QUEUES": raw}
            ):
                self.assertEqual(runtime._queue_config()[0], expected)

    @patch.object(runtime, "probe_queue_backlog")
    def test_deployment_mode_cannot_reuse_a_different_legacy_queue_list(self, probe):
        probe.side_effect = lambda _: self.good()
        with patch.object(runtime.deploy, "sourcelens_mode", return_value="external"):
            runtime.cached_queue_backlog("redis://sl/0")
        with patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled"):
            runtime.cached_queue_backlog("redis://sl/0")
        self.assertEqual(probe.call_count, 2)

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    @patch("redis.from_url")
    def test_backend_backlog_is_measured_and_alerted_with_read_only_commands(
        self, connect, _mode
    ):
        pipeline = connect.return_value.pipeline.return_value.__enter__.return_value
        pipeline.execute.return_value = [True, 1234, 0, 0]
        result = runtime.probe_queue_backlog("redis://sl/0")
        self.assertEqual(
            result["queue_lengths"], {"backend": 1234, "lens": 0, "sourcelens": 0}
        )
        self.assertEqual(result["notices"][0]["params"]["queue"], "backend")
        self.assertEqual(
            [call.args for call in pipeline.llen.call_args_list],
            [("backend",), ("lens",), ("sourcelens",)],
        )
        self.assertEqual(
            {call[0] for call in pipeline.method_calls}, {"ping", "llen", "execute"}
        )

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

    def publish(self, url):
        path = self.config.with_suffix(".tmp")
        path.write_text(json.dumps({"version": 1, "url": url}))
        path.chmod(0o600)
        path.replace(self.config)

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
    @patch.object(runtime, "probe_queue_backlog")
    def test_hot_broker_change_invalidates_both_caches_without_environment_change(
        self,
        query,
        _logs,
        _ping,
        _mode,
    ):
        old = "redis://old-secret@hfl-sourcelens-redis:6379/0"
        new = "redis://new-secret@hfl-sourcelens-redis:6379/2"
        query.side_effect = lambda url: dict(
            self.good(),
            queue_lengths={"lens": 7 if url == old else 1234},
        )
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_AUTO_REDIS_URL": old}):
            self.publish(old)
            first = runtime.sourcelens_health_payload()
            self.publish(new)
            second = runtime.sourcelens_health_payload()
            runtime.sourcelens_health_payload()
            self.assertEqual(os.environ["HFL_SL_RUNTIME_AUTO_REDIS_URL"], old)
        self.assertEqual(
            first["runtime_monitor"]["components"]["redis"]["queue_lengths"]["lens"], 7
        )
        self.assertEqual(
            second["runtime_monitor"]["components"]["redis"]["queue_lengths"]["lens"],
            1234,
        )
        self.assertEqual([c.args[0] for c in query.call_args_list], [old, new])
        self.assertNotIn("secret", str(second))

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    def test_tombstone_and_invalid_hot_config_never_restore_old_environment(
        self, _mode
    ):
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_AUTO_REDIS_URL": "redis://old/0"}):
            self.publish("")
            self.assertEqual(runtime.queue_monitor_url(), "")
            self.config.write_text("{invalid-json")
            self.assertEqual(runtime.queue_monitor_url(), "")
            self.config.write_text(json.dumps({"version": 2, "url": "redis://old/0"}))
            self.assertEqual(runtime.queue_monitor_url(), "")
            self.config.unlink()
            self.assertEqual(runtime.queue_monitor_url(), "redis://old/0")

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    def test_explicit_override_still_wins_over_hot_config(self, _mode):
        self.publish("redis://hfl-sourcelens-redis:6379/2")
        with patch.dict(os.environ, {"HFL_SL_RUNTIME_REDIS_URL": "redis://custom/1"}):
            self.assertEqual(runtime.queue_monitor_url(), "redis://custom/1")

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="external")
    def test_external_ignores_hot_bundled_configuration(self, _mode):
        self.publish("redis://hfl-sourcelens-redis:6379/2")
        self.assertEqual(runtime.queue_monitor_url(), "")

    @patch.object(runtime.deploy, "sourcelens_mode", return_value="bundled")
    def test_hot_config_is_bounded_regular_and_private(self, _mode):
        self.publish("redis://hfl-sourcelens-redis/0")
        self.config.chmod(0o644)
        self.assertEqual(runtime.queue_monitor_url(), "")
        self.config.chmod(0o600)
        self.config.write_text("x" * (runtime.MAX_AUTO_CONFIG_BYTES + 1))
        self.assertEqual(runtime.queue_monitor_url(), "")
        target = self.config.with_suffix(".target")
        self.config.rename(target)
        self.config.symlink_to(target)
        self.assertEqual(runtime.queue_monitor_url(), "")
