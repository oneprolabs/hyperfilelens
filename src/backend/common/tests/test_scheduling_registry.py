"""Tests for code-managed periodic task delivery safety."""

import json

from django.test import TestCase
from django_celery_beat.models import PeriodicTask

from common.scheduling.periodic_wakeup import PERIODIC_WAKEUP_COALESCE_HEADER
from common.scheduling.registry import TaskRegistry


class TaskRegistryTests(TestCase):
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

    def test_expiry_must_be_positive(self) -> None:
        registry = TaskRegistry()

        with self.assertRaisesRegex(ValueError, "expire_seconds must be positive"):
            registry.add(
                name="test_invalid_expiry",
                task="tests.invalid",
                schedule=1,
                expire_seconds=0,
            )
