from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_lifecycle import start_node_upgrade
from apps.node.services.internal.node_lifecycle_task import (
    sync_node_upgrade_operation_task,
)
from apps.task.models import Task, TaskEvent
from apps.task.selectors.interface import list_tasks
from apps.task.services.interface import create_task


class NodeUpgradeTaskProjectionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            key="upgrade-projection-org",
            name="Upgrade Projection Org",
        )
        self.node = Node.objects.create(
            organization=self.org,
            name="upgrade-agent",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            version="1.0.0",
        )

    def _operation_task(self) -> Task:
        return create_task(
            organization_id=self.org.id,
            task_type=Task.Type.NODE_LIFECYCLE,
            display_name='Upgrade Agent "upgrade-agent"',
            request_payload={"operation": "upgrade"},
            steps=[
                "dispatch_agent_upgrade",
                "install_agent_upgrade",
                "restart_agent",
                "verify_agent_upgrade",
                "finalize_agent_upgrade",
            ],
        )

    def _node_task(self, status: str, *, parent_task: Task | None = None) -> NodeTask:
        return NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="agent.upgrade",
            status=status,
            payload={"target_version": "1.2.0"},
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=1),
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            correlation_id=f"upgrade:{self.node.id}",
            parent_task=parent_task,
        )

    @patch(
        "apps.node.services.internal.node_lifecycle.agent_release_commit",
        return_value="b" * 40,
    )
    @patch(
        "apps.node.services.internal.node_lifecycle.validate_agent_upgrade",
        return_value="1.2.0",
    )
    @patch("apps.node.services.internal.task.deliver_agent_task")
    @patch(
        "apps.node.services.internal.node_lifecycle.redis_store.get_agent_session",
        return_value="",
    )
    def test_start_creates_both_tasks_and_defers_delivery_until_commit(
        self,
        get_session,
        deliver,
        _validate,
        _release_commit,
    ):
        get_session.return_value = "old-session"
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = start_node_upgrade(org=self.org, node=self.node)

        node_task = NodeTask.objects.get(pk=result["node_task_id"])
        operation_task = Task.objects.get(task_uuid=result["task_uuid"])
        self.assertEqual(
            Task.objects.filter(
                organization_id=self.org.id,
                request_payload__node_task_id=str(node_task.id),
            ).count(),
            1,
        )
        self.assertEqual(node_task.parent_task_id, operation_task.id)
        self.assertEqual(
            operation_task.request_payload["node_task_id"],
            str(node_task.id),
        )
        deliver.assert_not_called()
        for callback in callbacks:
            callback()
        deliver.assert_called_once()

    @patch(
        "apps.node.services.internal.node_lifecycle.agent_release_commit",
        return_value="b" * 40,
    )
    @patch(
        "apps.node.services.internal.node_lifecycle.validate_agent_upgrade",
        return_value="1.2.0",
    )
    @patch(
        "apps.node.services.internal.node_lifecycle_task.create_task",
        side_effect=RuntimeError("projection create failed"),
    )
    @patch(
        "apps.node.services.internal.node_lifecycle.redis_store.get_agent_session",
        return_value="",
    )
    def test_start_rolls_back_node_task_when_display_task_creation_fails(
        self,
        _get_session,
        _create_task,
        _validate,
        _release_commit,
    ):
        with self.assertRaises(RuntimeError):
            start_node_upgrade(org=self.org, node=self.node)

        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())
        self.assertFalse(Task.objects.filter(organization_id=self.org.id).exists())

    def test_projection_maps_every_status_and_is_idempotent(self):
        expected = {
            NodeTask.Status.PENDING: Task.Status.PENDING,
            NodeTask.Status.RUNNING: Task.Status.RUNNING,
            NodeTask.Status.SUCCESS: Task.Status.SUCCESS,
            NodeTask.Status.FAILED: Task.Status.FAILED,
            NodeTask.Status.TIMEOUT: Task.Status.TIMEOUT,
            NodeTask.Status.CANCELED: Task.Status.CANCELLED,
        }
        for node_status, task_status in expected.items():
            with self.subTest(status=node_status):
                operation_task = self._operation_task()
                node_task = self._node_task(node_status, parent_task=operation_task)
                sync_node_upgrade_operation_task(node_task=node_task)
                operation_task.refresh_from_db()
                self.assertEqual(operation_task.status, task_status)
                event_count = TaskEvent.objects.filter(task=operation_task).count()

                sync_node_upgrade_operation_task(node_task=node_task)

                self.assertEqual(
                    TaskEvent.objects.filter(task=operation_task).count(),
                    event_count,
                )

    def test_terminal_display_task_is_never_revived(self):
        operation_task = self._operation_task()
        node_task = self._node_task(NodeTask.Status.FAILED, parent_task=operation_task)
        sync_node_upgrade_operation_task(node_task=node_task)
        operation_task.refresh_from_db()
        self.assertEqual(operation_task.status, Task.Status.FAILED)

        NodeTask.objects.filter(pk=node_task.pk).update(status=NodeTask.Status.RUNNING)
        node_task.refresh_from_db()
        sync_node_upgrade_operation_task(node_task=node_task)

        operation_task.refresh_from_db()
        self.assertEqual(operation_task.status, Task.Status.FAILED)

    def test_projection_preserves_agent_diagnostic_error_code(self):
        operation_task = self._operation_task()
        node_task = self._node_task(NodeTask.Status.TIMEOUT, parent_task=operation_task)
        node_task.result = {"diagnostic_error_code": "AGENT_UNAVAILABLE"}
        node_task.last_error = "Agent remained unavailable during delivery"
        node_task.save(update_fields=["result", "last_error", "updated_at"])

        sync_node_upgrade_operation_task(node_task=node_task)

        operation_task.refresh_from_db()
        self.assertEqual(operation_task.error_code, "AGENT_UNAVAILABLE")

    def test_projection_repairs_parentless_upgrade_created_during_rollout(self):
        node_task = self._node_task(NodeTask.Status.RUNNING)

        sync_node_upgrade_operation_task(node_task=node_task)
        sync_node_upgrade_operation_task(node_task=node_task)

        node_task.refresh_from_db()
        self.assertIsNotNone(node_task.parent_task_id)
        self.assertEqual(
            Task.objects.filter(
                organization_id=self.org.id,
                node_tasks=node_task,
            ).count(),
            1,
        )
        self.assertEqual(node_task.parent_task.status, Task.Status.RUNNING)

    def test_search_accepts_both_task_ids_and_keeps_tenant_boundary(self):
        operation_task = self._operation_task()
        node_task = self._node_task(NodeTask.Status.PENDING, parent_task=operation_task)
        other_org = Organization.objects.create(key="other-org", name="Other Org")

        for value in (str(operation_task.task_uuid), str(node_task.id)):
            with self.subTest(value=value):
                self.assertEqual(
                    list(list_tasks(organization_id=self.org.id, search=value)),
                    [operation_task],
                )
                self.assertFalse(
                    list_tasks(organization_id=other_org.id, search=value).exists()
                )

    def test_historical_backfill_is_idempotent_and_does_not_dispatch(self):
        node_task = self._node_task(NodeTask.Status.FAILED)
        node_task.result = {"diagnostic_error_code": "AGENT_UNAVAILABLE"}
        node_task.save(update_fields=["result", "updated_at"])
        self.node.is_deleted = True
        self.node.save(update_fields=["is_deleted", "updated_at"])
        migration = import_module(
            "apps.node.migrations.0020_backfill_upgrade_operation_tasks"
        )
        schema_editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))

        with patch("apps.node.services.internal.task.deliver_agent_task") as deliver:
            migration.backfill_upgrade_operation_tasks(django_apps, schema_editor)
            migration.backfill_upgrade_operation_tasks(django_apps, schema_editor)

        node_task.refresh_from_db()
        self.assertIsNotNone(node_task.parent_task_id)
        self.assertEqual(
            Task.objects.filter(
                organization_id=self.org.id,
                node_tasks=node_task,
            ).count(),
            1,
        )
        self.assertEqual(
            list(
                list_tasks(
                    organization_id=self.org.id,
                    search=str(node_task.id),
                )
            )[0].id,
            node_task.parent_task_id,
        )
        self.assertEqual(node_task.parent_task.error_code, "AGENT_UNAVAILABLE")
        deliver.assert_not_called()
