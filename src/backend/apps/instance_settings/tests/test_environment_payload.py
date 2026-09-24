"""Runtime environment health semantics."""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.instance_settings.services.environment_payload import probe_celery


class CeleryHealthProbeTests(SimpleTestCase):
    @patch(
        "common.ops.runtime_backlog.runtime_backlog_snapshot",
        return_value={"status": "degraded", "warnings": ["legacy notification keys"]},
    )
    @patch("celery.current_app")
    def test_worker_health_is_not_degraded_by_redis_retention_warning(
        self,
        current_app: Mock,
        _backlog: Mock,
    ) -> None:
        inspector = current_app.control.inspect.return_value
        inspector.stats.return_value = {"worker-1": {}}
        inspector.active.return_value = {"worker-1": []}

        result = probe_celery()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["worker_count"], 1)
        self.assertEqual(result["active_tasks"], 0)
        self.assertEqual(result["backlog"]["status"], "degraded")
