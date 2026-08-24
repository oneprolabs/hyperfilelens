from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase

from apps.storage.periodic_tasks import register_periodic_tasks


class RepositoryUsageScheduleTests(SimpleTestCase):
    @mock.patch("apps.storage.periodic_tasks.TASK_REGISTRY.add")
    @mock.patch("apps.storage.periodic_tasks.maintenance_settings")
    def test_periodic_collection_bypasses_interactive_staleness_gate(
        self,
        maintenance_settings,
        registry_add,
    ):
        maintenance_settings.return_value = mock.Mock(
            scan_interval=timedelta(seconds=60),
            enabled=True,
        )

        register_periodic_tasks()

        usage_call = next(
            call
            for call in registry_add.call_args_list
            if call.kwargs["name"] == "storage_reconcile_repositories"
        )
        self.assertTrue(usage_call.kwargs["kwargs"]["force"])
        self.assertIsNone(usage_call.kwargs["kwargs"]["stale_after_seconds"])
