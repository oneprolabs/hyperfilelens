from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase

from apps.storage.periodic_tasks import register_periodic_tasks
from apps.storage.tasks import reconcile_storage_repositories


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
        self.assertTrue(usage_call.kwargs["kwargs"]["background"])


class RepositoryUsageCapacityTests(SimpleTestCase):
    @mock.patch("apps.storage.tasks.sync_all_repositories")
    @mock.patch(
        "apps.storage.tasks.try_acquire_background_storage_capacity",
        return_value=None,
    )
    def test_periodic_sync_defers_without_background_capacity(
        self,
        _acquire_capacity,
        sync_all,
    ):
        result = reconcile_storage_repositories.run(background=True)

        self.assertEqual(result["status"], "deferred_background_capacity")
        self.assertEqual(result["repositories_scanned"], 0)
        sync_all.assert_not_called()

    @mock.patch(
        "apps.storage.tasks.try_acquire_background_storage_capacity"
    )
    @mock.patch("apps.storage.tasks.sync_all_repositories")
    def test_interactive_sync_does_not_use_background_capacity(
        self,
        sync_all,
        acquire_capacity,
    ):
        sync_all.return_value = {
            "repositories_attempted": 1,
            "repositories_synced": 1,
            "repositories_failed": 0,
        }

        result = reconcile_storage_repositories.run(background=False)

        self.assertEqual(result["repositories_synced"], 1)
        acquire_capacity.assert_not_called()
