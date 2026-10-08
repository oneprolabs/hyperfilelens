"""Bounded, read-only SourceLens runtime monitoring contracts."""

import os
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
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


class IndexLogProbeTests(SimpleTestCase):
    def setUp(self):
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
        with patch.object(runtime, "_tail", side_effect=PermissionError):
            self.assertEqual(
                self.probe()["notices"][0]["code"], "index_logs_unavailable"
            )

    def test_undated_and_future_errors_do_not_claim_current_failure(self):
        for text in (
            "unexpected zero page\n",
            "2027-01-01 05:00:00 UTC ERROR: unexpected zero page\n",
            "2026-99-99 05:00:00 UTC ERROR: unexpected zero page\n",
        ):
            with self.subTest(text=text):
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
        self.assertEqual(len(runtime._log_files(self.root / "postgresql")), 4)
        path = self.write(
            "worker",
            (
                "2026-10-08 05:00:00 UTC ERROR: unexpected zero page\n"
                + "normal line\n" * runtime.MAX_TAIL_BYTES
            ),
        )
        self.assertLessEqual(len(runtime._tail(path)), runtime.MAX_TAIL_BYTES)
        self.assertEqual(self.probe()["health_status"], "unknown")

    def test_recent_window_boundary(self):
        stamp = (NOW - timedelta(hours=24)).isoformat()
        self.write("worker", f"{stamp} ERROR: unexpected zero page\n")
        self.assertEqual(self.probe()["health_status"], "error")


@patch.dict(
    os.environ,
    {
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
