from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.node import conf as node_conf
from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.task_offline_reconcile import (
    is_node_offline_stale,
    offline_stale_threshold_seconds,
    product_task_blocks_cleanup,
    reconcile_offline_stale_node_tasks,
    task_execution_state,
)
from apps.restore.models import RestoreRecord
from apps.task.models import Task


class OfflineStaleThresholdTests(SimpleTestCase):
    def test_offline_stale_threshold_includes_reconnect_and_fail_grace(self):
        expected = (
            node_conf.NODE_RECONNECT_GRACE_SECONDS + node_conf.OFFLINE_TASK_FAIL_SECONDS
        )
        self.assertEqual(offline_stale_threshold_seconds(), expected)


class TaskExecutionStateTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="offline-org", name="Offline Org")
        self.node = Node.objects.create(
            organization=self.org,
            name="agent-offline",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now() - timedelta(seconds=300),
        )
        self.task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.BACKUP,
            display_name="Backup",
            status=Task.Status.RUNNING,
            trigger_type=Task.TriggerType.MANUAL,
            request_payload={
                "source_type": "agent",
                "source_ref_id": self.node.id,
            },
        )

    @patch(
        "apps.node.services.internal.task_offline_reconcile.redis_store.offline_task_finalization_ready",
        return_value=True,
    )
    def test_offline_stale_when_last_seen_beyond_threshold(self, _mock_ready):
        self.assertTrue(is_node_offline_stale(self.node))

    def test_reconnecting_state_within_grace(self):
        self.node.availability = Node.Availability.ONLINE
        self.node.last_seen_at = timezone.now() - timedelta(seconds=30)
        self.node.save(update_fields=["availability", "last_seen_at", "updated_at"])
        self.assertEqual(
            task_execution_state(node=self.node, task=self.task), "reconnecting"
        )
        self.assertTrue(product_task_blocks_cleanup(task=self.task))

    @patch(
        "apps.node.services.internal.task_offline_reconcile.redis_store.offline_task_finalization_ready",
        return_value=True,
    )
    def test_offline_pending_blocks_cleanup(self, _mock_ready):
        self.node.availability = Node.Availability.OFFLINE
        self.node.last_seen_at = timezone.now() - timedelta(
            seconds=node_conf.NODE_RECONNECT_GRACE_SECONDS + 10
        )
        self.node.save(update_fields=["availability", "last_seen_at", "updated_at"])
        self.assertEqual(
            task_execution_state(node=self.node, task=self.task), "offline_pending"
        )
        self.assertTrue(product_task_blocks_cleanup(task=self.task))

    @patch(
        "apps.node.services.internal.task_offline_reconcile.redis_store.offline_task_finalization_ready",
        return_value=True,
    )
    def test_offline_stale_does_not_block_cleanup(self, _mock_ready):
        self.node.last_seen_at = timezone.now() - timedelta(
            seconds=offline_stale_threshold_seconds() + 5
        )
        self.node.save(update_fields=["last_seen_at", "updated_at"])
        self.assertEqual(
            task_execution_state(node=self.node, task=self.task), "offline_stale"
        )
        self.assertFalse(product_task_blocks_cleanup(task=self.task))

    @patch(
        "apps.node.services.internal.task_offline_reconcile.redis_store.offline_task_finalization_ready",
        return_value=False,
    )
    def test_recovery_hold_keeps_stale_node_in_offline_pending(self, _mock_ready):
        self.assertFalse(is_node_offline_stale(self.node))
        self.assertEqual(
            task_execution_state(node=self.node, task=self.task), "offline_pending"
        )
        self.assertTrue(product_task_blocks_cleanup(task=self.task))

    def test_insight_restore_uses_cross_organization_gateway_execution_state(self):
        platform_org = Organization.objects.create(
            key="offline-platform-org",
            name="Offline Platform Org",
        )
        gateway = Node.objects.create(
            organization=platform_org,
            name="platform-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
        )
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.INSIGHT_WORKSPACE_RESTORE,
            display_name="Insight workspace restore",
            status=Task.Status.RUNNING,
            trigger_type=Task.TriggerType.SYSTEM,
        )
        RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=platform_org.id,
            target_execution_node_id=gateway.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="offline-cross-org-restore",
            workspace_binding_id=101,
            restore_uid="offline-cross-org-restore",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=201,
            source_snapshot_id=301,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=gateway.id,
            target_path="/var/lib/hyperfilelens/insight/workspace",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )

        self.assertEqual(task_execution_state(node=gateway, task=task), "running")
        self.assertTrue(product_task_blocks_cleanup(task=task))

    @patch(
        "apps.node.services.internal.task_offline_reconcile.agent_connection_status",
        return_value=Node.Availability.ONLINE,
    )
    def test_nas_restore_uses_proxy_execution_state(self, _mock_connection):
        proxy = Node.objects.create(
            organization=self.org,
            name="restore-proxy",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            last_seen_at=timezone.now(),
        )
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.RESTORE,
            display_name="NAS restore",
            status=Task.Status.RUNNING,
        )
        RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=proxy.id,
            purpose=RestoreRecord.Purpose.USER_DATA,
            restore_uid="proxy-execution-restore",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=201,
            source_snapshot_id=301,
            target_type=RestoreRecord.EndpointType.NAS,
            target_ref_id=401,
            target_path="/restore",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )

        self.assertEqual(task_execution_state(node=proxy, task=task), "running")
        self.assertTrue(product_task_blocks_cleanup(task=task))

    @patch(
        "apps.node.services.internal.task_offline_reconcile.fail_node_task_offline",
        return_value=True,
    )
    @patch(
        "apps.node.services.internal.task_offline_reconcile.is_node_offline_stale",
        return_value=True,
    )
    @patch(
        "apps.node.services.internal.task_offline_reconcile.redis_store.offline_task_finalization_ready",
        return_value=True,
    )
    def test_offline_reconciliation_includes_gateway_nodes(
        self,
        _mock_ready,
        _mock_stale,
        mock_fail,
    ):
        gateway = Node.objects.create(
            organization=self.org,
            name="offline-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.OFFLINE,
        )
        from apps.node.models import NodeTask

        node_task = NodeTask.objects.create(
            organization=self.org,
            requesting_organization_id=self.org.id,
            node=gateway,
            kind="restore.run",
            status=NodeTask.Status.RUNNING,
            watchdog_deadline_at=timezone.now() + timedelta(minutes=5),
            correlation_type="restore.record",
            correlation_id="offline-gateway-restore",
        )

        summary = reconcile_offline_stale_node_tasks(limit=10)

        self.assertGreaterEqual(summary["nodes_checked"], 1)
        self.assertTrue(
            any(
                call.kwargs.get("node_task").id == node_task.id
                for call in mock_fail.call_args_list
            )
        )
