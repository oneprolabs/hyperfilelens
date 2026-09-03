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
    @mock.patch(
        "apps.storage.tasks.reconcile_storage_repositories.apply_async"
    )
    @mock.patch("apps.storage.tasks.sync_all_repositories")
    @mock.patch(
        "apps.storage.tasks.try_acquire_background_storage_capacity",
        return_value=None,
    )
    def test_periodic_sync_retries_without_changing_sample_time(
        self,
        _acquire_capacity,
        sync_all,
        apply_async,
    ):
        sample_recorded_at = "2026-09-03T13:45:01+08:00"

        result = reconcile_storage_repositories.run(
            background=True,
            sample_recorded_at=sample_recorded_at,
        )

        self.assertEqual(result["status"], "deferred_background_capacity")
        self.assertEqual(result["repositories_scanned"], 0)
        self.assertTrue(result["retry_scheduled"])
        self.assertEqual(result["capacity_retry_attempt"], 0)
        apply_async.assert_called_once()
        retry = apply_async.call_args
        self.assertEqual(retry.kwargs["countdown"], 15)
        self.assertEqual(retry.kwargs["kwargs"]["capacity_retry_attempt"], 1)
        self.assertEqual(
            retry.kwargs["kwargs"]["sample_recorded_at"],
            sample_recorded_at,
        )
        sync_all.assert_not_called()

    @mock.patch(
        "apps.storage.tasks.reconcile_storage_repositories.apply_async"
    )
    @mock.patch("apps.storage.tasks.sync_all_repositories")
    @mock.patch(
        "apps.storage.tasks.try_acquire_background_storage_capacity",
        return_value=None,
    )
    def test_periodic_sync_stops_after_three_retries(
        self,
        _acquire_capacity,
        sync_all,
        apply_async,
    ):
        result = reconcile_storage_repositories.run(
            background=True,
            capacity_retry_attempt=3,
            sample_recorded_at="2026-09-03T13:45:01+08:00",
        )

        self.assertEqual(result["status"], "deferred_background_capacity")
        self.assertFalse(result["retry_scheduled"])
        self.assertEqual(result["capacity_retry_attempt"], 3)
        apply_async.assert_not_called()
        sync_all.assert_not_called()

    @mock.patch(
        "apps.storage.tasks.reconcile_storage_repositories.apply_async"
    )
    @mock.patch("apps.storage.tasks.sync_all_repositories")
    @mock.patch(
        "apps.storage.tasks.try_acquire_background_storage_capacity"
    )
    def test_successful_retry_collects_for_original_sample_time(
        self,
        _acquire_capacity,
        sync_all,
        apply_async,
    ):
        sync_all.return_value = {
            "repositories_attempted": 1,
            "repositories_synced": 1,
            "repositories_failed": 0,
        }
        sample_recorded_at = "2026-09-03T13:45:01+08:00"

        result = reconcile_storage_repositories.run(
            background=True,
            capacity_retry_attempt=2,
            sample_recorded_at=sample_recorded_at,
        )

        self.assertEqual(result["repositories_synced"], 1)
        self.assertEqual(
            sync_all.call_args.kwargs["recorded_at"].isoformat(),
            sample_recorded_at,
        )
        apply_async.assert_not_called()

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
