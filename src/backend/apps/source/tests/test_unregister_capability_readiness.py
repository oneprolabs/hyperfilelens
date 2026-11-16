"""Source deregistration must not mistake unprojected capabilities for an old Agent."""

from unittest.mock import Mock, patch

from django.test import TestCase

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.services.internal import redis_store
from apps.node.services.internal.uninstall_readiness import (
    CAPABILITIES_NOT_READY,
    REQUIRED_UNINSTALL_CAPABILITY,
)
from apps.source.services.internal.backup_source_delete import (
    BackupSourceDeleteFailed,
    delete_backup_sources,
    preflight_delete_backup_sources,
    queue_delete_backup_sources,
)
from apps.task.models import Task


class UnregisterCapabilityReadinessTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="unregister-readiness", name="Readiness")
        self.node = Node.objects.create(
            organization=self.org,
            name="ready-agent",
            role=Node.Role.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            metadata={
                "inventory_session_id": "current",
                "inventory_capabilities_session_id": "current",
                "inventory": {"capabilities": [REQUIRED_UNINSTALL_CAPABILITY]},
            },
        )
        self.source_id = f"agent:{self.node.pk}"
        client = Mock()
        client.get.return_value = redis_store._encode_agent_loc(
            ws_instance_id="api", session_id="current",
        )
        client.exists.return_value = True
        route_patch = patch(
            "apps.node.services.internal.uninstall_readiness.redis_store",
            wraps=redis_store,
        )
        helpers = route_patch.start()
        helpers.get_redis.return_value = client
        self.addCleanup(route_patch.stop)
        wake_patch = patch("apps.node.tasks.uplink_ingest.request_uplink_ingest")
        self.wake = wake_patch.start()
        self.addCleanup(wake_patch.stop)

    def preflight(self, *, force=False):
        return preflight_delete_backup_sources(
            organization_id=self.org.pk, ids=[self.source_id], force=force,
        )

    def test_current_session_supported_source_is_ready_without_wakeup(self):
        self.assertFalse(self.preflight()["delete_disabled"])
        self.wake.assert_not_called()

    def test_preflight_unready_snapshot_wakes_ingest_and_preserves_source(self):
        self.node.metadata = {}
        self.node.save(update_fields=["metadata"])
        response = self.preflight()
        self.assertTrue(response["delete_disabled"])
        self.assertEqual(response["blocking"][0]["code"], CAPABILITIES_NOT_READY)
        self.wake.assert_called_once_with()
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    def test_empty_current_snapshot_requires_upgrade_without_wakeup(self):
        self.node.metadata["inventory"]["capabilities"] = []
        self.node.save(update_fields=["metadata"])
        response = self.preflight()
        self.assertEqual(response["blocking"][0]["code"], "agent_upgrade_required")
        self.wake.assert_not_called()

    def test_resubmission_after_sync_is_ready_without_automatic_unregister(self):
        ready = self.node.metadata
        self.node.metadata = {}
        self.node.save(update_fields=["metadata"])
        self.assertTrue(self.preflight()["delete_disabled"])
        self.node.metadata = ready
        self.node.save(update_fields=["metadata"])
        self.assertFalse(self.preflight()["delete_disabled"])
        self.assertFalse(Task.objects.filter(task_type="source_unregister").exists())

    def test_direct_strict_delete_fails_before_cleanup_or_task_creation(self):
        self.node.metadata = {}
        self.node.save(update_fields=["metadata"])
        with self.assertRaises(BackupSourceDeleteFailed) as raised:
            delete_backup_sources(org=self.org, ids=[self.source_id])
        self.assertEqual(raised.exception.reasons[0].code, CAPABILITIES_NOT_READY)
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())
        self.assertFalse(Task.objects.filter(task_type="source_unregister").exists())
        self.node.refresh_from_db()
        self.assertFalse(self.node.is_deleted)

    def test_async_submission_records_unready_reason_without_dispatch(self):
        self.node.metadata = {}
        self.node.save(update_fields=["metadata"])
        response = queue_delete_backup_sources(org=self.org, ids=[self.source_id])
        task = Task.objects.get(pk=response["task_id"])
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.result_payload["reasons"][0]["code"], CAPABILITIES_NOT_READY)
        self.assertFalse(NodeTask.objects.filter(node=self.node).exists())

    def test_force_preflight_does_not_require_capabilities_or_wake_ingest(self):
        self.node.metadata = {}
        self.node.save(update_fields=["metadata"])
        self.assertFalse(self.preflight(force=True)["delete_disabled"])
        self.wake.assert_not_called()
