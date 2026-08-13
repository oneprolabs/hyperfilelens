"""Tests for node workload guards."""

from django.test import TestCase

from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_lifecycle import enrich_node_row
from apps.node.services.internal.node_workload import node_workload_payload
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.storage.repositories.models import Repository
from apps.task.models import Task


class NodeWorkloadTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(key="workload-org", name="Workload Org")
        self.proxy = Node.objects.create(
            organization=self.org,
            name="proxy-1",
            role=NodeRole.PROXY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            version="1.0.0",
        )

    def test_proxy_workload_payload_does_not_query_missing_fields(self):
        payload = node_workload_payload(node=self.proxy)
        self.assertEqual(payload, {"blocked": False, "reasons": []})

    def test_proxy_enrich_row_succeeds(self):
        row = enrich_node_row(org=self.org, node=self.proxy)
        self.assertIn("workload", row)
        self.assertIn("lifecycle", row)

    def test_platform_gateway_is_blocked_by_tenant_insight_workspace_restore(self):
        platform_org = Organization.objects.create(
            key="workload-platform",
            name="Workload Platform",
        )
        platform_gateway = Node.objects.create(
            organization=platform_org,
            name="platform-gateway",
            role=NodeRole.GATEWAY,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
            version="1.0.0",
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
            target_execution_node_id=platform_gateway.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="workload-cross-org-restore",
            workspace_binding_id=101,
            restore_uid="workload-cross-org-restore",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=201,
            source_snapshot_id=301,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=platform_gateway.id,
            target_path="/var/lib/hyperfilelens/insight/workspace",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )

        payload = node_workload_payload(node=platform_gateway)

        self.assertTrue(payload["blocked"])
        self.assertEqual(len(payload["reasons"]), 1)
        self.assertEqual(payload["reasons"][0]["code"], "restore_running")
        self.assertEqual(payload["reasons"][0]["task_uuid"], str(task.task_uuid))
        self.assertEqual(
            payload["reasons"][0]["task_type"],
            Task.Type.INSIGHT_WORKSPACE_RESTORE,
        )

    def test_proxy_is_blocked_by_restore_execution(self):
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
            target_execution_node_id=self.proxy.id,
            purpose=RestoreRecord.Purpose.USER_DATA,
            restore_uid="proxy-restore",
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

        payload = node_workload_payload(node=self.proxy)

        self.assertTrue(payload["blocked"])
        self.assertTrue(
            any(reason["code"] == "restore_running" for reason in payload["reasons"])
        )

    def test_proxy_is_blocked_while_serving_repository_for_restore(self):
        target = Node.objects.create(
            organization=self.org,
            name="restore-target",
            role=NodeRole.AGENT,
            status=Node.Status.ACTIVE,
            availability=Node.Availability.ONLINE,
        )
        repository = Repository.objects.create(
            organization_id=self.org.id,
            name="proxy-restore-repository",
            repo_type=Repository.Type.PROXY_FS,
            status=Repository.Status.CREATED,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=self.proxy.id,
        )
        task = Task.objects.create(
            organization_id=self.org.id,
            task_type=Task.Type.INSIGHT_WORKSPACE_RESTORE,
            display_name="Insight restore through repository proxy",
            status=Task.Status.RUNNING,
            trigger_type=Task.TriggerType.SYSTEM,
        )
        record = RestoreRecord.objects.create(
            organization_id=self.org.id,
            requesting_organization_id=self.org.id,
            target_execution_organization_id=self.org.id,
            target_execution_node_id=target.id,
            purpose=RestoreRecord.Purpose.LENS_WORKSPACE,
            idempotency_key="proxy-repository-restore",
            workspace_binding_id=101,
            restore_uid="proxy-repository-restore",
            source_mode=RestoreRecord.SourceMode.MANUAL,
            task_id=task.id,
            task_uuid=task.task_uuid,
            source_type=RestoreRecord.EndpointType.AGENT,
            source_ref_id=201,
            source_snapshot_id=301,
            target_type=RestoreRecord.EndpointType.AGENT,
            target_ref_id=target.id,
            target_path="/var/lib/hyperfilelens/insight/workspace",
            scope=RestoreRecord.Scope.PATHS,
            conflict_mode=RestoreRecord.ConflictMode.OVERWRITE,
        )
        RestoreRecordItem.objects.create(
            organization_id=self.org.id,
            restore_record=record,
            source_snapshot_directory_id=401,
            backup_config_dir_id=501,
            repository_id=repository.id,
            kopia_snapshot_id="kopia-snapshot",
            source_path="/source",
            target_path=record.target_path,
            conflict_mode=RestoreRecordItem.ConflictMode.OVERWRITE,
            status=RestoreRecordItem.Status.RUNNING,
        )

        payload = node_workload_payload(node=self.proxy)

        self.assertTrue(payload["blocked"])
        self.assertEqual(len(payload["reasons"]), 1)
        self.assertEqual(payload["reasons"][0]["code"], "restore_running")
        self.assertEqual(payload["reasons"][0]["task_uuid"], str(task.task_uuid))

        task.status = Task.Status.SUCCESS
        task.save(update_fields=["status", "updated_at"])

        payload = node_workload_payload(node=self.proxy)

        self.assertFalse(payload["blocked"])
        self.assertEqual(payload["reasons"], [])
