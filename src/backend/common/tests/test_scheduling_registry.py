"""Tests for code-managed periodic task delivery safety."""

import json

from django.test import TestCase
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

from common.scheduling.periodic_wakeup import PERIODIC_WAKEUP_COALESCE_HEADER
from common.scheduling.registry import TaskRegistry


class TaskRegistryTests(TestCase):
    def test_storage_upgrade_migrates_legacy_schedule_once(self):
        cron = CrontabSchedule.objects.create(minute="*/15")
        PeriodicTask.objects.create(
            name="storage_reconcile_repositories", task="apps.storage.tasks.reconcile_storage_repositories",
            crontab=cron, queue="operator-queue",
        )
        registry = TaskRegistry()
        registry.add(
            name="storage_reconcile_repositories", task="apps.storage.tasks.reconcile_storage_repositories",
            schedule=60, kwargs={"periodic": True}, managed_schedule=True, sync_existing_kwargs=True,
        )
        registry.apply()
        registry.apply()
        task = PeriodicTask.objects.get(name="storage_reconcile_repositories")
        self.assertIsNone(task.crontab_id)
        self.assertEqual(task.interval.every, 60)
        self.assertEqual(task.queue, "operator-queue")
        self.assertEqual(PeriodicTask.objects.filter(name=task.name).count(), 1)

    def test_storage_upgrade_preserves_operator_interval_and_collection_cadence(self):
        interval = IntervalSchedule.objects.create(every=45, period="minutes")
        PeriodicTask.objects.create(
            name="storage_dispatch_repository_health_checks", task="tests.health", interval=interval,
        )
        registry = TaskRegistry()
        registry.add(
            name="storage_dispatch_repository_health_checks", task="tests.health",
            schedule=60, managed_schedule=True, sync_existing_kwargs=True,
        )
        registry.apply()
        task = PeriodicTask.objects.get(name="storage_dispatch_repository_health_checks")
        self.assertEqual(task.interval_id, interval.id)
        self.assertEqual(json.loads(task.kwargs)["sampling_interval_seconds"], 2700)

    def test_expiry_is_applied_to_new_periodic_task(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_expiring_task",
            task="tests.expiring",
            schedule=2,
            queue="node.ingest",
            expire_seconds=5,
        )

        registry.apply()

        task = PeriodicTask.objects.get(name="test_expiring_task")
        self.assertEqual(task.expire_seconds, 5)
        self.assertTrue(json.loads(task.headers)[PERIODIC_WAKEUP_COALESCE_HEADER])

    def test_explicit_expiry_updates_without_overwriting_custom_queue(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_managed_expiry",
            task="tests.expiring",
            schedule=2,
            queue="node.ingest",
            expire_seconds=5,
        )
        registry.apply()
        PeriodicTask.objects.filter(name="test_managed_expiry").update(
            queue="operator-custom",
            expire_seconds=60,
        )

        registry.apply()

        task = PeriodicTask.objects.get(name="test_managed_expiry")
        self.assertEqual(task.expire_seconds, 5)
        self.assertEqual(task.queue, "operator-custom")

    def test_unspecified_expiry_preserves_existing_value(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_unmanaged_expiry",
            task="tests.unmanaged",
            schedule=10,
        )
        registry.apply()
        PeriodicTask.objects.filter(name="test_unmanaged_expiry").update(
            expire_seconds=120
        )

        registry.apply()

        task = PeriodicTask.objects.get(name="test_unmanaged_expiry")
        self.assertEqual(task.expire_seconds, 120)

    def test_delivery_contract_preserves_operator_headers(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_managed_headers",
            task="tests.managed",
            schedule=10,
        )
        registry.apply()
        PeriodicTask.objects.filter(name="test_managed_headers").update(
            headers='{"operator-header": "preserved"}'
        )

        registry.apply()

        task = PeriodicTask.objects.get(name="test_managed_headers")
        headers = json.loads(task.headers)
        self.assertEqual(headers["operator-header"], "preserved")
        self.assertTrue(headers[PERIODIC_WAKEUP_COALESCE_HEADER])

    def test_registry_can_disable_wakeup_coalescing(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_non_coalesced_task",
            task="tests.every-trigger-matters",
            schedule=10,
            coalesce_wakeup=False,
        )

        registry.apply()

        task = PeriodicTask.objects.get(name="test_non_coalesced_task")
        self.assertFalse(json.loads(task.headers)[PERIODIC_WAKEUP_COALESCE_HEADER])

    def test_sync_existing_kwargs_updates_code_managed_definition(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_sync_kwargs",
            task="tests.sync-kwargs",
            schedule=10,
            kwargs={"force": False},
            sync_existing_kwargs=True,
        )
        registry.apply()
        PeriodicTask.objects.filter(name="test_sync_kwargs").update(
            kwargs=json.dumps({"force": True}),
        )

        registry.add(
            name="test_sync_kwargs",
            task="tests.sync-kwargs",
            schedule=10,
            kwargs={"force": False, "stale_after_seconds": None},
            sync_existing_kwargs=True,
        )
        registry.apply()

        task = PeriodicTask.objects.get(name="test_sync_kwargs")
        self.assertEqual(
            json.loads(task.kwargs),
            {"force": False, "stale_after_seconds": None},
        )

    def test_existing_kwargs_are_preserved_without_explicit_sync(self) -> None:
        registry = TaskRegistry()
        registry.add(
            name="test_preserve_kwargs",
            task="tests.preserve-kwargs",
            schedule=10,
            kwargs={"force": False},
        )
        registry.apply()
        PeriodicTask.objects.filter(name="test_preserve_kwargs").update(
            kwargs=json.dumps({"operator": "custom"}),
        )

        registry.apply()

        task = PeriodicTask.objects.get(name="test_preserve_kwargs")
        self.assertEqual(json.loads(task.kwargs), {"operator": "custom"})

    def test_expiry_must_be_positive(self) -> None:
        registry = TaskRegistry()

        with self.assertRaisesRegex(ValueError, "expire_seconds must be positive"):
            registry.add(
                name="test_invalid_expiry",
                task="tests.invalid",
                schedule=1,
                expire_seconds=0,
            )
