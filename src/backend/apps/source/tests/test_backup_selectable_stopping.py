from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from common.errors import AppError
from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.source.services.internal.source_operation_fence import (
    assert_no_active_backup_for_source,
    product_task_is_stopping,
)
from apps.task.models import Task, TaskResource


class ProductTaskStoppingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="runtime-stopping", name="Runtime Stopping")
        self.node = Node.objects.create(
            organization=self.org,
            name="runtime-stopping-agent",
            role=NodeRole.AGENT,
        )
        self.task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup",
            status=Task.Status.CANCELLED,
        )
        TaskResource.objects.create(
            task=self.task,
            resource_type=TaskResource.Type.BACKUP_SOURCE,
            resource_subtype="agent",
            resource_id=self.node.id,
            is_primary=True,
        )

    def create_node_task(self, *, age_seconds: int, cancel_requested: bool = True):
        return NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id=str(self.task.task_uuid),
            status=NodeTask.Status.RUNNING,
            cancel_requested_at=(
                timezone.now() - timezone.timedelta(seconds=age_seconds)
                if cancel_requested
                else None
            ),
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=2),
        )

    def test_stopping_only_during_cancel_grace(self):
        self.create_node_task(age_seconds=299)
        with patch("apps.node.conf.TASK_CANCEL_GRACE_SECONDS", 300):
            self.assertTrue(
                product_task_is_stopping(organization_id=self.org.id, task=self.task)
            )

    def test_stopped_at_or_after_cancel_grace(self):
        self.create_node_task(age_seconds=301)
        with patch("apps.node.conf.TASK_CANCEL_GRACE_SECONDS", 300):
            self.assertFalse(
                product_task_is_stopping(organization_id=self.org.id, task=self.task)
            )

    def test_active_task_without_cancel_request_is_not_stopping(self):
        self.create_node_task(age_seconds=0, cancel_requested=False)
        self.assertFalse(
            product_task_is_stopping(organization_id=self.org.id, task=self.task)
        )

    def test_source_fence_blocks_until_cancel_grace_expires(self):
        node_task = self.create_node_task(age_seconds=299)
        with patch("apps.node.conf.TASK_CANCEL_GRACE_SECONDS", 300):
            with self.assertRaises(AppError) as raised:
                assert_no_active_backup_for_source(
                    organization_id=self.org.id,
                    source_type="agent",
                    source_ref_id=self.node.id,
                )
            self.assertEqual(raised.exception.code, "BACKUP.ALREADY_RUNNING")
            self.assertEqual(raised.exception.status, 409)
            self.assertEqual(raised.exception.meta["status"], "stopping")

            node_task.cancel_requested_at = timezone.now() - timezone.timedelta(
                seconds=301
            )
            node_task.save(update_fields=["cancel_requested_at", "updated_at"])
            assert_no_active_backup_for_source(
                organization_id=self.org.id,
                source_type="agent",
                source_ref_id=self.node.id,
            )

    def test_source_fence_ignores_malformed_backup_correlation_id(self):
        NodeTask.objects.create(
            organization=self.org,
            node=self.node,
            kind="backup.run",
            correlation_type="protection.backup",
            correlation_id="not-a-task-uuid",
            status=NodeTask.Status.RUNNING,
            cancel_requested_at=timezone.now(),
            watchdog_deadline_at=timezone.now() + timezone.timedelta(hours=2),
        )

        assert_no_active_backup_for_source(
            organization_id=self.org.id,
            source_type="agent",
            source_ref_id=self.node.id,
        )
