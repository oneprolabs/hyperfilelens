from unittest import mock

from django.test import SimpleTestCase

from apps.lens_bridge.periodic_tasks import register_periodic_tasks


class LensBridgePeriodicTaskTests(SimpleTestCase):
    @mock.patch("apps.lens_bridge.periodic_tasks.TASK_REGISTRY.add")
    def test_registers_usage_reconciliation(self, add):
        register_periodic_tasks()

        add.assert_any_call(
            name="lens_bridge_reconcile_usage_ledgers",
            task=(
                "apps.lens_bridge.tasks.usage_reconciliation."
                "reconcile_usage_ledgers_task"
            ),
            schedule=30,
            kwargs={"limit": 100},
            enabled=True,
            expire_seconds=25,
        )
